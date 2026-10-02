"""Hand-picked stations that replace the directory's messy duplicates of the same channel.

curated/<CC>.json holds stations in the published format. A channel is identified by the stream host's channel key
(naxidigital-<key>128...); every directory station with the same key is replaced by the curated one, at the position
of the first duplicate. Curated stations the directory does not list yet go right after the last Naxi Digital entry.

    python curated.py RS      -> patch radio/RS.json in place (build_radio.py calls apply() on every run)
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
KEY = re.compile(r'naxidigital-(.+?)(?:128|48)', re.I)


def key_of(station):
    m = KEY.search(station['stream']['url'])
    return m.group(1).lower() if m else None


def apply(cc, stations):
    path = os.path.join(ROOT, 'curated', f'{cc}.json')
    if not os.path.exists(path):
        return stations
    curated = json.load(open(path, encoding='utf-8'))['stations']
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
    for cc in sys.argv[1:]:
        p = os.path.join(ROOT, 'radio', f'{cc}.json')
        d = json.load(open(p, encoding='utf-8'))
        before = len(d['stations'])
        d['stations'] = apply(cc, d['stations'])
        open(p, 'w', encoding='utf-8', newline='\n').write(json.dumps(d, ensure_ascii=False, separators=(',', ':')))
        print(cc, before, '->', len(d['stations']))
