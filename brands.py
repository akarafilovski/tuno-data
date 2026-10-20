"""Marks the channels of a radio brand (Naxi, Super Radio, Star Radio) so the app can offer a Channels filter beside Live radios.

curated/brands.json: {"MK": [{"brand": "Super Radio", "prefix": ["super radio"], "main": ["Super Radio Live"]}], ...}

A station whose name starts with one of the prefixes and is not one of the brand's main streams gets "channelOf": "<brand>";
the main stream (and every station of no brand) stays a live radio. A brand with fewer than 3 matching stations is left alone.
    python brands.py MK RS      -> patch radio/<CC>.json in place (build_radio.py calls apply() on every run)
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def _rules(cc):
    path = os.path.join(ROOT, 'curated', 'brands.json')
    if not os.path.exists(path):
        return []
    return json.load(open(path, encoding='utf-8')).get(cc, [])


def apply(cc, stations):
    for rule in _rules(cc):
        prefixes = [p.casefold() for p in rule['prefix']]
        main = {m.casefold() for m in rule.get('main', [])}
        found = [s for s in stations if any(s['name'].casefold().startswith(p) for p in prefixes)]
        if len(found) < 3:
            continue
        for s in found:
            if s['name'].casefold() in main:
                s.pop('channelOf', None)
            else:
                s['channelOf'] = rule['brand']
    return stations


if __name__ == '__main__':
    for code in sys.argv[1:]:
        path = os.path.join(ROOT, 'radio', f'{code.upper()}.json')
        data = json.load(open(path, encoding='utf-8'))
        before = sum(1 for s in data['stations'] if s.get('channelOf'))
        data['stations'] = apply(code.upper(), data['stations'])
        after = sum(1 for s in data['stations'] if s.get('channelOf'))
        open(path, 'w', encoding='utf-8', newline='\n').write(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
        print(f'{code.upper()}: channels {before} -> {after}')
