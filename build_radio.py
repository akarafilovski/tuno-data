"""Builds one radio catalog JSON per country for TUNO, from the radio-browser.info directory.

    python build_radio.py                 -> every country the directory lists
    python build_radio.py HR RS SI        -> only these ISO country codes
    python build_radio.py --limit 120     -> keep at most 120 stations per country (default 300)

Output (served by GitHub Pages):
    radio/<CC>.json   {"stations": [...], "minSupportedVersion": 1}   same shape as TUNO's StationsResponse
    radio/index.json  list of countries with station counts + generatedAt
    state/health.json per-station failure counter, so a station is only removed after FAIL_LIMIT failed runs in a row

Order: most listened first (the app can also sort alphabetically on the device).
Rules: only stations the directory marks as working; names cleaned of HTML entities; duplicates removed by name and
by stream URL; every stream is probed here; a station that was published before stays for FAIL_LIMIT-1 bad runs.
"""
import concurrent.futures as cf
import hashlib
import html
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

import requests

import curated
import logos

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'radio')
STATE = os.path.join(ROOT, 'state', 'health.json')
SERVERS = ['de1', 'de2', 'fi1', 'at1', 'nl1']
UA = {'User-Agent': 'TUNO-catalog/1.0 (+https://github.com/akarafilovski/tuno-data)'}
FAIL_LIMIT = 3
PROBE_TIMEOUT = 8
MIN_SUPPORTED_VERSION = 1
# logos are checked (and replaced from the station's own site) only for the countries that have a Radio app
LOGO_COUNTRIES = {'HR', 'SI', 'BG', 'RS', 'RO', 'MK', 'NL', 'GR', 'ME', 'BA', 'TR', 'HU'}



# Single-reciter Quran recitation streams (for example "Abdulbasit Abdulsamad") are listed under almost every country in the directory
# but belong to none of them: they are never published. Named Quran radio stations are kept.
BLOCKED_NAME = re.compile(r"abdul\s*-?bas[iu]t|abdul\s*-?samad|hus[ae]ri|minshawi|alafasy|al-afasy|sudais|shuraim|beautiful recitation", re.I)


def sort_key(name):
    base = unicodedata.normalize('NFKD', name)
    base = ''.join(ch for ch in base if not unicodedata.combining(ch)).casefold()
    base = re.sub(r'^[^0-9a-zЀ-ӿͰ-Ͽ]+', '', base)
    return (base, name)

def api(path):
    last = None
    for s in SERVERS:
        try:
            r = requests.get(f'https://{s}.api.radio-browser.info{path}', headers=UA, timeout=40)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # try the next mirror
            last = e
    raise RuntimeError(f'radio-browser unreachable: {last}')


def clean_url(u):
    u = (u or '').strip()
    return None if not u or u.lower() == 'null' else u


def norm_name(n):
    return re.sub(r'[^a-z0-9]+', '', html.unescape(n).lower())


def norm_stream(u):
    return re.sub(r'^https?://', '', u.strip().lower()).rstrip('/')


def category(tags):
    t = (tags or '').lower()
    if 'news' in t:
        return 'news'
    if 'sport' in t:
        return 'sports'
    if 'talk' in t:
        return 'talk'
    return 'music'


def stream_format(url):
    return 'hls' if re.search(r'\.m3u8?(\?|$)', url.lower()) else 'icecast'


def probe(url):
    """True if the stream answers like audio. Shoutcast servers answer 'ICY 200 OK', which requests reports as an error."""
    try:
        r = requests.get(url, headers=UA, stream=True, timeout=PROBE_TIMEOUT)
        ctype = r.headers.get('content-type', '').lower()
        ok = r.status_code == 200 and 'text/html' not in ctype
        if ok:
            first = next(r.iter_content(2048), b'')
            ok = len(first) > 0
            if ok and url.lower().split('?')[0].endswith('.m3u8'):
                ok = first.lstrip().startswith(b'#EXTM3U')
        r.close()
        return ok
    except Exception as e:
        return 'ICY 200' in str(e)


def load_state():
    try:
        return json.load(open(STATE, encoding='utf-8'))
    except Exception:
        return {}


def build_country(cc, limit, state):
    raw = api(f'/json/stations/bycountrycodeexact/{cc}?hidebroken=true&order=clickcount&reverse=true&limit=600')
    seen_names, seen_urls, cands = set(), set(), []
    for s in raw:
        if s.get('lastcheckok') != 1:
            continue
        name = html.unescape(s.get('name', '')).strip()
        url = clean_url(s.get('url_resolved')) or clean_url(s.get('url'))
        if not name or not url or not url.lower().startswith(('http://', 'https://')):
            continue
        if BLOCKED_NAME.search(name):
            continue
        nn, nu = norm_name(name), norm_stream(url)
        if not nn or nn in seen_names or nu in seen_urls:
            continue
        seen_names.add(nn)
        seen_urls.add(nu)
        cands.append((s, name, url))
    cands = cands[:limit]
    with cf.ThreadPoolExecutor(24) as ex:
        results = list(ex.map(lambda c: probe(c[2]), cands))
    stations, dropped = [], 0
    for (s, name, url), ok in zip(cands, results):
        sid = f"radiobrowser:{s['stationuuid']}"
        h = state.get(sid, {'fails': 0, 'published': False})
        if ok:
            h = {'fails': 0, 'published': True}
        else:
            h['fails'] = h.get('fails', 0) + 1
        state[sid] = h
        if not (ok or (h['published'] and h['fails'] < FAIL_LIMIT)):
            dropped += 1
            continue
        st = {
            'id': sid,
            'name': name,
            'type': 'radio',
            'country': cc,
            'category': category(s.get('tags')),
            'stream': {'url': url, 'format': stream_format(url)},
        }
        if clean_url(s.get('favicon')):
            st['logoUrl'] = clean_url(s['favicon'])
        if (s.get('state') or '').strip():
            st['region'] = s['state'].strip()
        if clean_url(s.get('homepage')):
            st['website'] = clean_url(s['homepage'])
        stations.append(st)
    return stations, dropped


def main():
    args = sys.argv[1:]
    limit = 300
    if '--limit' in args:
        i = args.index('--limit')
        limit = int(args[i + 1])
        del args[i:i + 2]
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    directory = {c['iso_3166_1'].upper(): c for c in api('/json/countries') if c.get('iso_3166_1')}
    wanted = [a.upper() for a in args] or sorted(directory)
    state = load_state()
    logo_cache = logos.load_cache()
    index_path = os.path.join(OUT, 'index.json')
    try:
        old_index = json.load(open(index_path, encoding='utf-8'))
    except Exception:
        old_index = {'countries': []}
    counts = {c['code']: c for c in old_index.get('countries', [])}
    changed = False
    for cc in wanted:
        t = time.time()
        try:
            stations, dropped = build_country(cc, limit, state)
        except Exception as e:
            print(f'{cc}: skipped ({e})')
            continue
        stations = curated.apply(cc, stations)
        if cc in LOGO_COUNTRIES:
            have, total = logos.apply(cc, stations, logo_cache)
            print(f'{cc}: {have}/{total} stations have a working logo')
        path = os.path.join(OUT, f'{cc}.json')
        if not stations:
            if os.path.exists(path):
                print(f'{cc}: no stations this run, keeping the previous file')
            continue
        payload = {'stations': stations, 'minSupportedVersion': MIN_SUPPORTED_VERSION}
        text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
        try:
            same = open(path, encoding='utf-8').read() == text
        except Exception:
            same = False
        if not same:
            open(path, 'w', encoding='utf-8', newline='\n').write(text)
            changed = True
        counts[cc] = {'code': cc, 'name': directory.get(cc, {}).get('name', cc), 'stations': len(stations)}
        print(f'{cc}: {len(stations)} stations ({dropped} dropped) in {time.time() - t:.0f}s{"" if not same else " [unchanged]"}')
    index = {'generatedAt': old_index.get('generatedAt'), 'countries': sorted(counts.values(), key=lambda c: c['code'])}
    if changed or not index['generatedAt']:
        index['generatedAt'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    open(index_path, 'w', encoding='utf-8', newline='\n').write(json.dumps(index, ensure_ascii=False, indent=1))
    open(STATE, 'w', encoding='utf-8', newline='\n').write(json.dumps(state, sort_keys=True, separators=(',', ':')))
    logos.save_cache(logo_cache)


if __name__ == '__main__':
    main()
