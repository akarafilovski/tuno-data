"""Builds one TV catalog JSON per country for TUNO, from the iptv-org dataset (channels, streams, logos).

    python build_tv.py                 -> every country
    python build_tv.py HR RS SI        -> only these ISO country codes
    python build_tv.py --limit 200     -> at most 200 channels per country (default 300)

Output (served by GitHub Pages):
    tv/<CC>.json    {"stations": [...], "minSupportedVersion": 1}   same shape as TUNO's StationsResponse
    tv/index.json   countries with channel counts and the time of the last change
    state/health_tv.json   failure counters; a channel is removed only after FAIL_LIMIT failed runs in a row

Rules: adult and closed channels are skipped; for each channel the first working stream (of up to 3) is used;
a stream that answers 403/451 or times out is treated as "blocked here, unknown" (many channels are geo-blocked
for the GitHub runner) and stays in; a stream that is gone (404, bad playlist, no connection) counts as a failure.
"""
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'tv')
STATE = os.path.join(ROOT, 'state', 'health_tv.json')
BASE = 'https://iptv-org.github.io/api'
UA = 'Mozilla/5.0 (compatible; TUNO-catalog/1.0; +https://github.com/akarafilovski/tuno-data)'
FAIL_LIMIT = 3
PROBE_TIMEOUT = 8
MIN_SUPPORTED_VERSION = 1



def sort_key(name):
    base = unicodedata.normalize('NFKD', name)
    base = ''.join(ch for ch in base if not unicodedata.combining(ch)).casefold()
    base = re.sub(r'^[^0-9a-zЀ-ӿͰ-Ͽ]+', '', base)
    return (base, name)

def get_json(name):
    r = requests.get(f'{BASE}/{name}', headers={'User-Agent': UA}, timeout=120)
    r.raise_for_status()
    return r.json()


def clean_url(u):
    u = (u or '').strip()
    return None if not u or u.lower() == 'null' else u


def category(cats):
    t = ','.join(cats or []).lower()
    if 'news' in t:
        return 'news'
    if 'sport' in t:
        return 'sports'
    if 'music' in t:
        return 'music'
    return 'entertainment'


def is_hls(url):
    return url.split('?')[0].lower().endswith('.m3u8')


def probe(stream):
    """'ok', 'dead' or 'blocked' (403/451/timeout: cannot tell from here)."""
    headers = {'User-Agent': stream.get('user_agent') or UA}
    ref = stream.get('referrer') or stream.get('http_referrer')
    if ref:
        headers['Referer'] = ref
    url = stream['url']
    try:
        r = requests.get(url, headers=headers, stream=True, timeout=PROBE_TIMEOUT)
        code = r.status_code
        if code in (401, 403, 451):
            r.close()
            return 'blocked'
        if code != 200:
            r.close()
            return 'dead'
        first = next(r.iter_content(4096), b'')
        r.close()
        if not first:
            return 'dead'
        if is_hls(url) and not first.lstrip().startswith(b'#EXTM3U'):
            return 'dead'
        return 'ok'
    except (requests.Timeout, requests.exceptions.ReadTimeout):
        return 'blocked'
    except Exception:
        return 'dead'


def check_channel(streams):
    """First stream that is ok wins; otherwise the first blocked one; otherwise dead."""
    blocked = None
    for st in streams[:3]:
        res = probe(st)
        if res == 'ok':
            return st, 'ok'
        if res == 'blocked' and blocked is None:
            blocked = st
    if blocked:
        return blocked, 'blocked'
    return None, 'dead'


def load_state():
    try:
        return json.load(open(STATE, encoding='utf-8'))
    except Exception:
        return {}


def main():
    args = sys.argv[1:]
    limit = 300
    if '--limit' in args:
        i = args.index('--limit')
        limit = int(args[i + 1])
        del args[i:i + 2]
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    channels = get_json('channels.json')
    streams = get_json('streams.json')
    logos = get_json('logos.json')
    names = {c['code']: c['name'] for c in get_json('countries.json')}
    by_channel = {}
    for s in streams:
        if s.get('channel') and (s.get('url') or '').startswith(('http://', 'https://')):
            by_channel.setdefault(s['channel'], []).append(s)
    logo_of = {}
    for lg in logos:
        if lg.get('channel') and lg.get('url') and lg['channel'] not in logo_of:
            logo_of[lg['channel']] = lg['url']
    by_country = {}
    for c in channels:
        if c.get('is_nsfw') or c.get('closed') or not c.get('country') or c['id'] not in by_channel:
            continue
        by_country.setdefault(c['country'].upper(), []).append(c)
    wanted = [a.upper() for a in args] or sorted(by_country)
    state = load_state()
    index_path = os.path.join(OUT, 'index.json')
    try:
        old_index = json.load(open(index_path, encoding='utf-8'))
    except Exception:
        old_index = {'countries': []}
    counts = {c['code']: c for c in old_index.get('countries', [])}
    changed = False
    for cc in wanted:
        chans = by_country.get(cc, [])
        if not chans:
            continue
        t = time.time()
        seen = set()
        uniq = []
        for c in sorted(chans, key=lambda c: c['name'].lower()):
            key = re.sub(r'[^a-z0-9]+', '', c['name'].lower())
            if key and key not in seen:
                seen.add(key)
                uniq.append(c)
        uniq = uniq[:limit]
        with cf.ThreadPoolExecutor(24) as ex:
            results = list(ex.map(lambda c: check_channel(by_channel[c['id']]), uniq))
        out, dropped = [], 0
        for c, (st, res) in zip(uniq, results):
            sid = f"iptvorg:{c['id']}"
            h = state.get(sid, {'fails': 0, 'published': False})
            if res == 'dead':
                h['fails'] = h.get('fails', 0) + 1
            else:
                h = {'fails': 0, 'published': True}
            state[sid] = h
            if res == 'dead' and not (h['published'] and h['fails'] < FAIL_LIMIT):
                dropped += 1
                continue
            if st is None:
                st = by_channel[c['id']][0]
            item = {
                'id': sid,
                'name': c['name'].strip(),
                'type': 'tv',
                'country': cc,
                'category': category(c.get('categories')),
                'stream': {'url': st['url'], 'format': 'hls' if is_hls(st['url']) else 'progressive'},
            }
            ref = st.get('referrer') or st.get('http_referrer')
            if ref:
                item['stream']['referer'] = ref
            if logo_of.get(c['id']):
                item['logoUrl'] = logo_of[c['id']]
            if clean_url(c.get('website')):
                item['website'] = clean_url(c['website'])
            out.append(item)
        if not out:
            continue
        out.sort(key=lambda st: sort_key(st['name']))
        text = json.dumps({'stations': out, 'minSupportedVersion': MIN_SUPPORTED_VERSION}, ensure_ascii=False, separators=(',', ':'))
        path = os.path.join(OUT, f'{cc}.json')
        try:
            same = open(path, encoding='utf-8').read() == text
        except Exception:
            same = False
        if not same:
            open(path, 'w', encoding='utf-8', newline='\n').write(text)
            changed = True
        counts[cc] = {'code': cc, 'name': names.get(cc, cc), 'stations': len(out)}
        print(f'{cc}: {len(out)} channels ({dropped} dropped) in {time.time() - t:.0f}s{" [unchanged]" if same else ""}')
    index = {'generatedAt': old_index.get('generatedAt'), 'countries': sorted(counts.values(), key=lambda c: c['code'])}
    if changed or not index['generatedAt']:
        index['generatedAt'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    open(index_path, 'w', encoding='utf-8', newline='\n').write(json.dumps(index, ensure_ascii=False, indent=1))
    open(STATE, 'w', encoding='utf-8', newline='\n').write(json.dumps(state, sort_keys=True, separators=(',', ':')))


if __name__ == '__main__':
    main()
