"""One-off: add a popularity rank (1 = most listened) to the published radio files, from radio-browser's click counts.
No stream probing, so it is fast. build_radio.py writes the same field from now on."""
import glob
import json
import os
import requests

SERVERS = ['de1', 'de2', 'fi1', 'at1', 'nl1']
UA = {'User-Agent': 'TUNO-catalog/1.0 (+https://github.com/akarafilovski/tuno-data)'}


def api(path):
    for s in SERVERS:
        try:
            r = requests.get(f'https://{s}.api.radio-browser.info{path}', headers=UA, timeout=40)
            r.raise_for_status()
            return r.json()
        except Exception:
            pass
    return None


changed = 0
for path in sorted(glob.glob('radio/??.json')):
    cc = os.path.basename(path)[:2]
    d = json.load(open(path, encoding='utf-8'))
    raw = api(f'/json/stations/bycountrycodeexact/{cc}?hidebroken=true&order=clickcount&reverse=true&limit=1000')
    if raw is None:
        print(cc, 'skipped (directory unreachable)')
        continue
    order = {f"radiobrowser:{s['stationuuid']}": i + 1 for i, s in enumerate(raw)}
    for st in d['stations']:
        st['rank'] = order.get(st['id'], 9999)
    text = json.dumps(d, ensure_ascii=False, separators=(',', ':'))
    open(path, 'w', encoding='utf-8', newline='\n').write(text)
    changed += 1
print('files with rank:', changed)
