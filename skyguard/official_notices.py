"""Discover and read published notices from configured government indexes."""

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from .system_clock import now as system_now
from html.parser import HTMLParser
import re
import ssl
from threading import Lock
from time import monotonic
from urllib.parse import urljoin, urlsplit

import httpx


CHINA = timezone(timedelta(hours=8))
CITY_SOURCES = {
    'changsha': (
        'https://gat.hunan.gov.cn/gat/jwgk/jwzx/jqfb/index.html',
        'https://gat.hunan.gov.cn/gat/jwgk/zfxxgk/xxgkml/tzgg/index.html',
    ),
    'guangzhou': (
        'https://gaj.gz.gov.cn/zwdt/tzgg/index.html',
        'https://gaj.gz.gov.cn/zzzq/tzgg/index.html',
    ),
    'shanghai': ('https://www.shanghai.gov.cn/nw42944/',),
    'shenzhen': (
        'https://www.sz.gov.cn/cn/xxgk/zfxxgj/tzgg/index.html',
        'https://www.sz.gov.cn/cn/xxgk/zfxxgj/tzgg/index_2.html',
    ),
    'hangzhou': ('https://zfgb.hangzhou.gov.cn/bulletin.shtml',),
}
# Official articles linked outside the recurring bulletin lists. The content is
# still fetched and compared with the requested date, like a discovered item.
ADDITIONAL_LINKS = {
    'guangzhou': (
        ('https://gaj.gz.gov.cn/zwdt/tzgg/content/post_11032190.html',
         '广州市公安局对相关区域低空飞行活动实施从严管控的通告'),
    ),
    'shenzhen': (
        ('https://www.sz.gov.cn/cn/xxgk/zfxxgj/zwdt/content/post_13053144.html',
         '深圳市政府门户 · APEC 会议期间低空飞行管控通报'),
    ),
}
DYNAMIC_SUBJECT = re.compile(r'无人机|无人驾驶航空器|低慢小|低空飞行|空飘物')
DYNAMIC_ACTION = re.compile(r'管控|管制|禁飞|限制|安全管理')
TIME_RANGE = re.compile(
    r'(?:(?P<y>20\d{2})\s*年\s*)?(?P<m>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日'
    r'(?:\s*(?P<h>\d{1,2})\s*时(?:\s*(?P<i>\d{1,2})\s*分)?)?\s*(?:起\s*)?'
    r'(?:至|到|[—－~～])\s*(?:(?P<ey>20\d{2})\s*年\s*)?'
    r'(?:(?P<em>\d{1,2})\s*月\s*)?(?P<ed>\d{1,2})\s*日'
    r'(?:\s*(?P<eh>\d{1,2})\s*时(?:\s*(?P<ei>\d{1,2})\s*分)?)?'
)


def compact(text):
    return ' '.join(text.split())


def same_origin(url, source):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.netloc == urlsplit(source).netloc
            and parts.path.lower().endswith(('.html', '.shtml', '.htm', '.aspx')))


class BulletinPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.paragraphs = []
        self.anchor = None
        self.block = None
        self.hidden = None

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden = tag
        if self.hidden:
            return
        attrs = dict(attrs)
        if tag == 'a':
            self.anchor = (attrs.get('href') or '', attrs.get('title') or '', [])
        if tag in ('p', 'li', 'h1', 'h2'):
            self.block = []

    def handle_data(self, text):
        if self.hidden:
            return
        if self.anchor is not None:
            self.anchor[2].append(text)
        if self.block is not None:
            self.block.append(text)

    def handle_endtag(self, tag):
        if tag == self.hidden:
            self.hidden = None
            return
        if self.hidden:
            return
        if tag == 'a' and self.anchor is not None:
            url, title, text = self.anchor
            label = max((compact(title), compact(''.join(text))), key=len)
            self.links.append((url, label))
            self.anchor = None
        if tag in ('p', 'li', 'h1', 'h2') and self.block is not None:
            paragraph = compact(''.join(self.block))
            if paragraph:
                self.paragraphs.append(paragraph)
            self.block = None


def interpret_periods(text):
    periods = []
    year = None
    for match in TIME_RANGE.finditer(text):
        data = match.groupdict()
        year = int(data['y']) if data['y'] else year
        if year is None:
            continue
        month = int(data['m'])
        end_year, end_month = int(data['ey'] or year), int(data['em'] or month)
        try:
            start = datetime(year, month, int(data['d']), tzinfo=CHINA)
            start += timedelta(hours=int(data['h'] or 0), minutes=int(data['i'] or 0))
            end = datetime(end_year, end_month, int(data['ed']), tzinfo=CHINA)
            end += timedelta(hours=int(data['eh'] or 24), minutes=int(data['ei'] or 0))
        except ValueError:
            continue
        if end > start:
            periods.append((start, end))
    return tuple(dict.fromkeys(periods))


@dataclass(frozen=True)
class Notice:
    title: str
    url: str
    excerpt: str
    periods: tuple = ()

    def time_status(self, query):
        if not self.periods:
            return '有效时间待核对'
        if any(start < query.end and query.start < end for start, end in self.periods):
            return '公告时段与计划重叠 · 空间范围待核对'
        return '识别到的时段与计划不重叠'


@dataclass(frozen=True)
class Scan:
    checked: tuple = ()
    inaccessible: tuple = ()
    notices: tuple = ()
    at: datetime | None = None
    stale: bool = False


class OfficialNoticeLookup:
    def __init__(self, transport=None):
        self.transport = transport
        self._cache = {}
        self._locks = {}
        self._lock = Lock()

    def search(self, city):
        with self._lock:
            lock = self._locks.setdefault(city, Lock())
        with lock:
            previous = self._cache.get(city)
            if previous and monotonic() - previous[0] < 300:
                return previous[1]
            result = asyncio.run(self._search(CITY_SOURCES.get(city, ()), ADDITIONAL_LINKS.get(city, ())))
            if result.checked:
                self._cache[city] = (monotonic(), result)
            elif previous:
                return replace(previous[1], stale=True)
            return result

    async def _page(self, client, url):
        try:
            async with asyncio.timeout(3.5):
                async with client.stream('GET', url) as reply:
                    reply.raise_for_status()
                    kind = reply.headers.get('content-type', '').lower()
                    if 'html' not in kind:
                        return None
                    raw = bytearray()
                    async for chunk in reply.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1_500_000:
                            return None
                    encoding = reply.encoding or 'utf-8'
                text = raw.decode(encoding, errors='replace')
                if '\ufffd' in text and encoding.lower() == 'utf-8':
                    text = raw.decode('gb18030', errors='replace')
                page = BulletinPage()
                page.feed(text)
                return page if page.links or page.paragraphs else None
        except (httpx.HTTPError, TimeoutError, LookupError, ValueError):
            return None

    async def _search(self, sources, links):
        if not sources:
            return Scan()
        tls = ssl.create_default_context()
        tls.set_ecdh_curve('prime256v1')
        async with httpx.AsyncClient(transport=self.transport, follow_redirects=False, verify=tls,
                                     headers={'User-Agent': 'SkyguardDesktop/0.7'}) as client:
            indexes = await asyncio.gather(*(self._page(client, url) for url in sources))
            checked, inaccessible, candidates = [], [], dict(links)
            for url, page in zip(sources, indexes):
                if page is None:
                    inaccessible.append(url)
                    continue
                checked.append(url)
                for href, title in page.links:
                    article = urljoin(url, href)
                    relevant = DYNAMIC_SUBJECT.search(title) and DYNAMIC_ACTION.search(title)
                    if relevant and not re.search(r'草案|征求意见', title) and same_origin(article, url):
                        candidates.setdefault(article, title)
            selected = list(candidates.items())[:6]
            details = await asyncio.gather(*(self._page(client, url) for url, _ in selected))
        notices = []
        for (url, title), page in zip(selected, details):
            if page is None:
                inaccessible.append(url)
                notices.append(Notice(title, url, '已找到公告链接，正文暂未读取。'))
                continue
            text = [p for p in page.paragraphs if DYNAMIC_SUBJECT.search(p) or re.search(r'空域|低空|升空物', p)]
            explicit = [(paragraph, interpret_periods(paragraph)) for paragraph in (text or page.paragraphs)]
            periods = tuple(dict.fromkeys(period for _, windows in explicit for period in windows))
            relevant = [paragraph for paragraph, windows in explicit if windows or
                        ('时段' in paragraph and DYNAMIC_ACTION.search(paragraph))]
            if not relevant:
                relevant = [p for p in page.paragraphs if len(p) > 25 and DYNAMIC_ACTION.search(p)]
            excerpt = ' '.join(dict.fromkeys(relevant))[:420]
            notices.append(Notice(title, url, excerpt or '请打开原文核对具体范围和时间。', periods))
        return Scan(tuple(checked), tuple(inaccessible), tuple(notices), system_now(CHINA))
