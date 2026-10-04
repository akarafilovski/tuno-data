"""One-off: remove single-reciter Quran recitation streams from the published radio files and fix the counts in radio/index.json.
They were copied into the list of (almost) every country and are not stations of any of them.
build_radio.py skips the same names from now on (BLOCKED_NAME)."""
import glob
import json
import os
import re

from build_radio import BLOCKED_NAME

removed = {}
for path in sorted(glob.glob('radio/??.json')):
    cc = os.path.basename(path)[:2]
    d = json.load(open(path, encoding='utf-8'))
    keep = [s for s in d['stations'] if not BLOCKED_NAME.search(s['name'])]
    if len(keep) != len(d['stations']):
        removed[cc] = len(d['stations']) - len(keep)
        d['stations'] = keep
        open(path, 'w', encoding='utf-8', newline='\n').write(json.dumps(d, ensure_ascii=False, separators=(',', ':')))

index_path = 'radio/index.json'
index = json.load(open(index_path, encoding='utf-8'))
for c in index['countries']:
    if c['code'] in removed:
        c['stations'] -= removed[c['code']]
open(index_path, 'w', encoding='utf-8', newline='\n').write(json.dumps(index, ensure_ascii=False, indent=1))
print('removed', sum(removed.values()), 'stations from', len(removed), 'countries')
