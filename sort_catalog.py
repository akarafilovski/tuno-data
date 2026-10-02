"""One-off: sort every published radio/tv JSON file alphabetically (the generators do this from now on)."""
import glob
import json
import re
import unicodedata


def sort_key(name):
    base = unicodedata.normalize('NFKD', name)
    base = ''.join(ch for ch in base if not unicodedata.combining(ch)).casefold()
    base = re.sub(r'^[^0-9a-zЀ-ӿͰ-Ͽ]+', '', base)
    return (base, name)


changed = 0
for path in glob.glob('radio/??.json') + glob.glob('tv/??.json'):
    d = json.load(open(path, encoding='utf-8'))
    d['stations'].sort(key=lambda s: sort_key(s['name']))
    text = json.dumps(d, ensure_ascii=False, separators=(',', ':'))
    if open(path, encoding='utf-8').read() != text:
        open(path, 'w', encoding='utf-8', newline='\n').write(text)
        changed += 1
print('files re-sorted:', changed)
