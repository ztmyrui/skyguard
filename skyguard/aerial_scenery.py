"""Public tourism evidence for verified scenery near flight routes."""
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path

import httpx

from .aerial import ScenicPlace


SEARCH_HEADERS = {'User-Agent': 'Skyguard desktop photography planner/0.9'}


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []

    def handle_data(self, data):
        self.text.append(data)


class ScenicLookup:
    def __init__(self, transport=None):
        catalog = Path(__file__).parent / 'assets' / 'scenic-catalog.json'
        self.catalog = json.loads(catalog.read_text(encoding='utf-8'))
        self.transport = transport

    def search(self, city):
        guide = self.catalog.get(city)
        if guide is None:
            return None
        return tuple(ScenicPlace(f'{city}-{index + 1}', name, (lon, lat), genre,
                                (guide['sources'][source],), 100)
                     for index, (name, lon, lat, genre, _alias, source, *_planning) in enumerate(guide['places'][:20]))

    def _locate(self, names, links):
        found = {}
        with httpx.Client(transport=self.transport, timeout=httpx.Timeout(3, connect=2.0)) as client:
            for link in sorted(url for url in links if not url.endswith('.pdf') and 'download/' not in url)[:2]:
                try:
                    with client.stream('GET', link, headers=SEARCH_HEADERS) as response:
                        response.raise_for_status()
                        chunks = []
                        size = 0
                        for chunk in response.iter_bytes(16384):
                            chunks.append(chunk)
                            size += len(chunk)
                            if size >= 300_000:
                                break
                        parser = _VisibleText()
                        parser.feed(b''.join(chunks).decode(response.encoding or 'utf-8', errors='replace'))
                    text = ' '.join(' '.join(parser.text).split())
                    for key, title in names.items():
                        start = text.find(title)
                        if start >= 0:
                            found[key] = link
                except (httpx.HTTPError, ValueError, LookupError):
                    continue
        return found

    def discover(self, city, candidates):
        places = self.search(city)
        guide = self.catalog.get(city)
        if places is None or not candidates:
            return ()
        base = {place.name for place in places}
        names = {index: name for index, (name, _) in enumerate(candidates)
                 if len(name) >= 3 and not any(name in known or known in name for known in base)}
        if not names:
            return ()
        found = self._locate(names, guide['sources'])
        return tuple(ScenicPlace(f'{city}-{sha256(name.encode()).hexdigest()[:12]}', name, point,
                                 '公开景点', (found[index],), 100)
                     for index, (name, point) in enumerate(candidates) if index in found)[:20 - len(places)]
