"""Logo resolver for the published station lists.

A station's logo comes from the radio directory's favicon field, which is often empty or dead. This module
  1. checks that the logo URL really answers with a PNG/JPEG/WebP/GIF image,
  2. when it does not, looks on the station's own website for an apple-touch-icon / og:image / icon,
  3. removes the logoUrl when nothing works (the apps then show the station's initials).

Results are cached in state/logos.json (per station id) so a daily run only re-checks what is old.
A logo that worked before is only dropped after two failed checks in a row (a flaky server must not erase it).

    python logos.py HR RS      -> patch radio/HR.json and radio/RS.json in place
    python logos.py            -> every radio/*.json
build_radio.py calls apply() for every country it builds.
"""
import concurrent.futures as cf
import glob
import json
import os
import re
import sys
import time
from urllib.parse import urljoin

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_PATH = os.path.join(ROOT, 'state', 'logos.json')
UA = {'User-Agent': 'Mozilla/5.0 (compatible; TUNO-catalog/1.0; +https://github.com/akarafilovski/tuno-data)'}
OK_DAYS = 14        # re-check a working logo after this long
BAD_DAYS = 7        # re-check a station without a logo after this long
TIMEOUT = (3, 4)   # connect, read
PAGE_SECONDS = 8   # wall-clock limit for reading one home page
RUN_SECONDS = 420  # wall-clock limit for one country
MAX_BYTES = 2_000_000
MAGIC = (b'\x89PNG', b'\xff\xd8\xff', b'GIF8', b'RIFF')  # PNG, JPEG, GIF, WebP (RIFF....WEBP)


def load_cache():
    try:
        return json.load(open(CACHE_PATH, encoding='utf-8'))
    except Exception:
        return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    open(CACHE_PATH, 'w', encoding='utf-8', newline='\n').write(json.dumps(cache, sort_keys=True, separators=(',', ':')))


def is_image(url):
    """True when [url] answers 200 with real raster image bytes."""
    try:
        r = requests.get(url, headers=UA, timeout=TIMEOUT, stream=True)
        if r.status_code != 200:
            r.close()
            return False
        head = next(r.iter_content(64), b'')
        size = int(r.headers.get('content-length') or 0)
        r.close()
        if size > MAX_BYTES or not head.startswith(MAGIC):
            return False
        if head.startswith(b'RIFF') and head[8:12] != b'WEBP':
            return False
        return True
    except Exception:
        return False


def candidates_from_site(site):
    """Icon URLs advertised by the station's home page, best first."""
    try:
        r = requests.get(site, headers=UA, timeout=TIMEOUT, stream=True)
        if r.status_code != 200:
            r.close()
            return []
        chunks, got, t0 = [], 0, time.time()
        for chunk in r.iter_content(16384):
            chunks.append(chunk)
            got += len(chunk)
            if got > 300_000 or time.time() - t0 > PAGE_SECONDS:
                break
        r.close()
        page = b''.join(chunks).decode(r.encoding or 'utf-8', 'replace')
    except Exception:
        return []
    found = []

    def add(tag_pattern, attr='href'):
        for m in re.finditer(tag_pattern, page, re.I):
            tag = m.group(0)
            u = re.search(attr + r'\s*=\s*["\']([^"\']+)["\']', tag, re.I)
            if u:
                found.append(urljoin(r.url, u.group(1).strip()))

    add(r'<link[^>]+rel=["\'][^"\']*apple-touch-icon[^"\']*["\'][^>]*>')
    add(r'<meta[^>]+property=["\']og:image["\'][^>]*>', 'content')
    add(r'<meta[^>]+name=["\']twitter:image["\'][^>]*>', 'content')
    add(r'<link[^>]+rel=["\'](?:shortcut )?icon["\'][^>]*>')
    seen, out = set(), []
    for u in found:
        if u.startswith(('http://', 'https://')) and u not in seen and not u.lower().split('?')[0].endswith(('.svg', '.ico')):
            seen.add(u)
            out.append(u)
    return out[:3]


def resolve_one(st, entry):
    """Returns the cache entry for one station: {'url': logo or None, 'fails': n, 'checked': epoch}."""
    previous = entry.get('url') if entry else None
    fails = entry.get('fails', 0) if entry else 0
    tries = []
    if st.get('logoUrl'):
        tries.append(st['logoUrl'])
        if st['logoUrl'].startswith('http://'):
            tries.insert(0, 'https://' + st['logoUrl'][len('http://'):])
    for u in tries:
        if is_image(u):
            return {'url': u, 'fails': 0, 'checked': time.time()}
    if st.get('website'):
        for u in candidates_from_site(st['website']):
            if is_image(u):
                return {'url': u, 'fails': 0, 'checked': time.time()}
    # nothing worked: keep a logo that worked before for one more round
    if previous and fails < 1:
        return {'url': previous, 'fails': fails + 1, 'checked': time.time()}
    return {'url': None, 'fails': fails + 1, 'checked': time.time()}


def apply(cc, stations, cache=None):
    """Sets or removes logoUrl on [stations] in place. Returns (with_logo, total)."""
    own = cache is None
    cache = load_cache() if own else cache
    now = time.time()
    todo = []
    for st in stations:
        e = cache.get(st['id'])
        if e and e.get('url') is not None and now - e['checked'] < OK_DAYS * 86400 and e['url'] == st.get('logoUrl', e['url']):
            continue
        if e and e.get('url') is None and now - e['checked'] < BAD_DAYS * 86400:
            continue
        todo.append(st)
    if todo:
        ex = cf.ThreadPoolExecutor(24)
        futures = {ex.submit(resolve_one, s, cache.get(s['id'])): s for s in todo}
        try:
            for f in cf.as_completed(futures, timeout=RUN_SECONDS):
                cache[futures[f]['id']] = f.result()
        except cf.TimeoutError:
            print(f'{cc}: time limit reached, {sum(1 for f in futures if not f.done())} stations left for the next run')
        ex.shutdown(wait=False, cancel_futures=True)
    have = 0
    for st in stations:
        e = cache.get(st['id'])
        url = e.get('url') if e else st.get('logoUrl')
        if url:
            st['logoUrl'] = url
            have += 1
        else:
            st.pop('logoUrl', None)
    if own:
        save_cache(cache)
    return have, len(stations)


if __name__ == '__main__':
    codes = [a.upper() for a in sys.argv[1:]]
    paths = [os.path.join(ROOT, 'radio', f'{c}.json') for c in codes] if codes else sorted(glob.glob(os.path.join(ROOT, 'radio', '??.json')))
    cache = load_cache()
    for path in paths:
        d = json.load(open(path, encoding='utf-8'))
        before = sum(1 for s in d['stations'] if s.get('logoUrl'))
        t = time.time()
        have, total = apply(os.path.basename(path)[:2], d['stations'], cache)
        open(path, 'w', encoding='utf-8', newline='\n').write(json.dumps(d, ensure_ascii=False, separators=(',', ':')))
        save_cache(cache)
        print(f'{os.path.basename(path)[:2]}: logos {before} -> {have} of {total} stations in {time.time() - t:.0f}s')
