"""Builds one news JSON per country for TUNO and the Radio apps, from public RSS feeds.

    python build_news.py                 -> every country that has a source
    python build_news.py HR RS MK        -> only these ISO country codes

Output (served by GitHub Pages):
    news/<CC>.json    {"headlines": [...]}   same shape as the TUNO server's NewsResponse
    news/index.json   countries with headline counts and the time of the last change

Sources, in order of preference (ported from the TUNO server's news scrapers):
  1. news_feeds.json   hand-picked RSS feeds per country (countries Google/Yahoo do not cover, e.g. HR, BA, ME)
  2. Yahoo News edition (news_editions.json "yahoo"): real images, direct links
  3. Google News edition (news_editions.json "google"): no images, Google redirect links
  4. time.mk homepage for MK (no RSS exists)
A country whose sources all fail in a run keeps its previous file.
"""
import hashlib
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, 'news')
UA = {'User-Agent': 'Mozilla/5.0 (compatible; TUNO-catalog/1.0; +https://github.com/akarafilovski/tuno-data)'}
MAX_PER_COUNTRY = 30
PER_FEED = 8
NS = {'media': 'http://search.yahoo.com/mrss/', 'content': 'http://purl.org/rss/1.0/modules/content/'}
TIMEMK_PATTERN = re.compile(
    r'<h1><a href="(r/[^"]+)"[^>]*>([^<]+)</a></h1>'
    r'(?:<div class="article_image">.*?background-image:url\(\'([^\']+)\'\).*?</div>)?'
    r'.*?<h2><a href="s/[^"]+" class="source">([^<]+)</a>\s*-\s*<span class="when">([^<]*)</span>',
    re.S,
)
TIMEMK_UNITS = {'час': 3600, 'мин': 60, 'ден': 86400}


def get(url, **kw):
    return requests.get(url, headers=kw.pop('headers', UA), timeout=25, **kw)


def hid(prefix, cc, link):
    return f'{prefix}:{cc.lower()}:' + hashlib.md5(link.encode('utf-8')).hexdigest()[:12]


def millis(raw):
    try:
        return int(parsedate_to_datetime(raw.strip()).timestamp() * 1000)
    except Exception:
        return None


def clean(text):
    return html.unescape(re.sub(r'<[^>]+>', '', text or '')).strip()


def thumb(item):
    for path in ('media:content', 'media:thumbnail'):
        el = item.find(path, NS)
        if el is not None and (el.get('url') or '').startswith('http'):
            return el.get('url')
    enc = item.find('enclosure')
    if enc is not None and (enc.get('type') or '').startswith('image') and (enc.get('url') or '').startswith('http'):
        return enc.get('url')
    body = (item.findtext('content:encoded', namespaces=NS) or '') + (item.findtext('description') or '')
    m = re.search(r'<img[^>]+src=["\'](https?://[^"\']+)', body)
    return m.group(1) if m else None


def parse_rss(data, cc, prefix, source=None, limit=PER_FEED, strip_source_suffix=False):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter('item'):
        title = clean(item.findtext('title'))
        link = (item.findtext('link') or '').strip()
        if not title or not link.startswith('http'):
            continue
        src = source or clean(item.findtext('source')) or prefix
        suffix = ' - ' + src
        if strip_source_suffix and title.endswith(suffix):
            title = title[: -len(suffix)].strip()
        out.append({
            'id': hid(prefix, cc, link), 'title': title, 'source': src, 'url': link, 'country': cc,
            'publishedAtEpochMillis': millis(item.findtext('pubDate') or ''), 'thumbnailUrl': thumb(item),
        })
        if len(out) >= limit:
            break
    return out


STOP = {'the', 'and', 'for', 'sa', 'se', 'je', 'su', 'na', 'u', 'i', 'za', 'od', 'da', 'ne', 'koji', 'koja', 'kako', 'sta', 'sto', 'ali', 'iz', 'po', 'do', 'nakon', 'nije'}
RECENT_HOURS = 30


def words(title):
    return {w for w in re.findall(r'\w{4,}', title.lower()) if w not in STOP}


def rank_by_coverage(items):
    """Popularity proxy for plain RSS feeds: stories several outlets run at once come first, then newest."""
    now = time.time() * 1000
    fresh = [x for x in items if not x['publishedAtEpochMillis'] or now - x['publishedAtEpochMillis'] < RECENT_HOURS * 3600 * 1000] or items
    clusters = []
    for x in sorted(fresh, key=lambda x: x['publishedAtEpochMillis'] or 0, reverse=True):
        w = words(x['title'])
        for c in clusters:
            if w and c['words'] and len(w & c['words']) / len(w | c['words']) >= 0.4:
                c['items'].append(x)
                c['words'] |= w
                break
        else:
            clusters.append({'words': w, 'items': [x]})
    for c in clusters:
        c['score'] = len({i['source'] for i in c['items']})
        c['best'] = next((i for i in c['items'] if i['thumbnailUrl']), c['items'][0])
        c['time'] = max(i['publishedAtEpochMillis'] or 0 for i in c['items'])
    clusters.sort(key=lambda c: (c['score'], c['time']), reverse=True)
    return [c['best'] for c in clusters]


def from_feeds(cc, feeds):
    items = []
    for f in feeds:
        try:
            r = get(f['url'])
            if r.status_code == 200:
                items += parse_rss(r.content, cc, 'rss', f['source'], limit=25)
        except Exception:
            pass
    return rank_by_coverage(items)[:MAX_PER_COUNTRY]


def from_yahoo(cc, host):
    r = get(f'https://{host}/rss')
    return parse_rss(r.content, cc, 'yahoo', limit=20) if r.status_code == 200 else []


def from_google(cc, hl, ceid):
    r = get(f'https://news.google.com/rss?hl={hl}&gl={cc}&ceid={ceid}', allow_redirects=False)
    return parse_rss(r.content, cc, 'googlenews', limit=20, strip_source_suffix=True) if r.status_code == 200 else []


def from_timemk():
    base = 'https://time.mk/'
    page = ''
    for _ in range(8):
        try:
            r = get(base)
            r.encoding = 'utf-8'
            page = r.text
        except Exception:
            continue
        if '<h1><a href="r/' in page:
            break
    now = time.time()
    out = []
    for m in TIMEMK_PATTERN.finditer(page):
        path, title, th, src, when = m.groups()
        try:
            loc = requests.get(base + path, headers={**UA, 'Referer': base}, allow_redirects=False, timeout=15).headers.get('Location')
        except Exception:
            continue
        if not loc:
            continue
        if not loc.startswith('http'):
            loc = base + loc.lstrip('/')
        age = 0
        mm = re.search(r'(\d+)\s*(час|мин|ден)', when)
        if mm:
            age = int(mm.group(1)) * TIMEMK_UNITS[mm.group(2)]
        out.append({
            'id': 'timemk:' + path.removeprefix('r/').strip('/'), 'title': html.unescape(title).strip(),
            'source': html.unescape(src).strip(), 'url': loc, 'country': 'MK',
            'publishedAtEpochMillis': int((now - age) * 1000), 'thumbnailUrl': (base + th) if th else None,
        })
        if len(out) >= MAX_PER_COUNTRY:
            break
    return out


def build(cc, feeds, editions):
    if cc in feeds:
        items = from_feeds(cc, feeds[cc])
        if items:
            return items
    if cc in editions['yahoo']:
        items = from_yahoo(cc, editions['yahoo'][cc])
        if items:
            return items
    if cc in editions['google']:
        items = from_google(cc, *editions['google'][cc])
        if items:
            return items
    if cc == 'MK':
        return from_timemk()
    return []


def main():
    feeds = json.load(open(os.path.join(ROOT, 'news_feeds.json'), encoding='utf-8'))
    editions = json.load(open(os.path.join(ROOT, 'news_editions.json'), encoding='utf-8'))
    wanted = [a.upper() for a in sys.argv[1:]] or sorted(set(feeds) | set(editions['yahoo']) | set(editions['google']) | {'MK'})
    os.makedirs(OUT, exist_ok=True)
    index_path = os.path.join(OUT, 'index.json')
    try:
        old = json.load(open(index_path, encoding='utf-8'))
    except Exception:
        old = {'countries': []}
    counts = {c['code']: c for c in old.get('countries', [])}
    changed = False
    for cc in wanted:
        items = build(cc, feeds, editions)
        if not items:
            print(cc, 'no headlines this run, keeping the previous file')
            continue
        text = json.dumps({'headlines': items}, ensure_ascii=False, separators=(',', ':'))
        path = os.path.join(OUT, f'{cc}.json')
        try:
            same = open(path, encoding='utf-8').read() == text
        except Exception:
            same = False
        if not same:
            open(path, 'w', encoding='utf-8', newline='\n').write(text)
            changed = True
        counts[cc] = {'code': cc, 'headlines': len(items)}
        print(cc, len(items), 'headlines' + (' [unchanged]' if same else ''))
    index = {'generatedAt': old.get('generatedAt'), 'countries': sorted(counts.values(), key=lambda c: c['code'])}
    if changed or not index['generatedAt']:
        index['generatedAt'] = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    open(index_path, 'w', encoding='utf-8', newline='\n').write(json.dumps(index, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
