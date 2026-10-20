"""Hand-picked stations that replace the directory's messy duplicates of the same channel.

curated/<CC>.json holds stations in the published format. Optional "replaceNames": [..] switches to name-based curation (directory stations whose name contains one of those words are dropped, the curated ones are inserted at position 3). A channel is identified by the stream host's channel key
(naxidigital-<key>128...); every directory station with the same key is replaced by the curated one, at the position
of the first duplicate. Curated stations the directory does not list yet go right after the last Naxi Digital entry.

    python curated.py RS      -> patch radio/RS.json in place (build_radio.py calls apply() on every run)
"""
import json
import os
import re
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
KEY = re.compile(r'naxidigital-(.+?)(?:128|48)', re.I)


def key_of(station):
    m = KEY.search(station['stream']['url'])
    return m.group(1).lower() if m else None


def stream_key(url):
    p = urlparse(url or '')
    return (p.hostname or '').lower() + p.path.rstrip('/') + (';' if p.path.endswith(';') else '')


def name_key(name):
    import unicodedata
    base = unicodedata.normalize('NFKD', name or '').lower()
    # "(Ex-YU Retro)" names a channel and stays in the key; "(MP3 128 kbps)", the city and the frequency do not
    base = re.sub(r'\((?:mp3|aac|hd|hq)[^)]*\)|\bradio\b|\bfm\b|радио|\d+[.,]\d+|skopje|macedonia', ' ', base)
    return re.sub(r'[^a-z0-9а-яѐ-џ]', '', base)


def apply_top(cc, stations):
    """curated/top/<CC>.json: the researched top stations (rank, official stream, fallbacks). Each one replaces the directory entries
    that are the same station: the ones whose stream address it lists under "replaces", and the ones with the same name. What those
    entries played stays available as a fallback stream, so nothing shows twice and nothing that worked is lost."""
    path = os.path.join(ROOT, 'curated', 'top', f'{cc}.json')
    if not os.path.exists(path):
        return stations
    top = json.load(open(path, encoding='utf-8'))['stations']
    ids = {t['id'] for t in top}
    stations = [s for s in stations if s['id'] not in ids]
    out_top = []
    removed_ids = set()
    for t in top:
        gone = {stream_key(u) for u in t.get('replaces', [])} | {stream_key(t['stream']['url'])}
        tk = name_key(t['name'])
        extra = []
        for s in stations:
            if s['id'] in removed_ids:
                continue
            if stream_key(s['stream']['url']) in gone or (tk and name_key(s['name']) == tk):
                removed_ids.add(s['id'])
                extra += [s['stream']] + s.get('fallbackStreams', [])
        item = {k: v for k, v in t.items() if k != 'replaces'}
        have = {stream_key(item['stream']['url'])} | {stream_key(f['url']) for f in item.get('fallbackStreams', [])}
        fall = list(item.get('fallbackStreams', []))
        for f in extra:
            if stream_key(f['url']) not in have and len(fall) < 3:
                fall.append(f)
                have.add(stream_key(f['url']))
        if fall:
            item['fallbackStreams'] = fall
        out_top.append(item)
    return [s for s in stations if s['id'] not in removed_ids] + out_top


def apply(cc, stations):
    return apply_top(cc, _apply_curated(cc, stations))


def _apply_curated(cc, stations):
    path = os.path.join(ROOT, 'curated', f'{cc}.json')
    if not os.path.exists(path):
        return stations
    spec = json.load(open(path, encoding='utf-8'))
    curated = spec['stations']
    names = [n.lower() for n in spec.get('replaceNames', [])]
    if names:
        # name-based curation: drop the directory's own entries for that brand and put ours near the top
        keep = [s for s in stations if not any(n in s['name'].lower() for n in names)]
        at = min(3, len(keep))
        keep[at:at] = curated
        return keep
    by_key = {key_of(c): c for c in curated if key_of(c)}
    placed, out = set(), []
    last = -1
    for st in stations:
        k = key_of(st)
        if k is None:
            out.append(st)
            continue
        if k in by_key:
            if k not in placed:
                placed.add(k)
                out.append(by_key[k])
                last = len(out) - 1
            continue
        if k in placed:
            continue
        placed.add(k)
        out.append(st)
        last = len(out) - 1
    extra = [c for k, c in by_key.items() if k not in placed]  # (uncurated keys are in `placed` too, see above)
    if extra:
        at = last + 1 if last >= 0 else len(out)
        out[at:at] = extra
    return out


if __name__ == '__main__':
    # python curated.py MK RS        -> the full curation (for a file straight from the directory)
    # python curated.py --top MK RS  -> only the researched top stations (safe to repeat on a file that is already curated)
    args = sys.argv[1:]
    top_only = bool(args) and args[0] == '--top'
    for cc in (args[1:] if top_only else args):
        p = os.path.join(ROOT, 'radio', f'{cc}.json')
        d = json.load(open(p, encoding='utf-8'))
        before = len(d['stations'])
        d['stations'] = apply_top(cc, d['stations']) if top_only else apply(cc, d['stations'])
        open(p, 'w', encoding='utf-8', newline='\n').write(json.dumps(d, ensure_ascii=False, separators=(',', ':')))
        print(cc, before, '->', len(d['stations']))
