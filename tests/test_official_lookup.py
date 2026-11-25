from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import numpy as np
from shapely import box

from skyguard.flight_rules import FlightQuery
from skyguard.official_airspace import grid_coverage, published_evidence
from skyguard.official_notices import CHINA, OfficialNoticeLookup, interpret_periods


def test_official_grid_uses_full_footprint_and_reports_height_independently():
    start = datetime(2026, 11, 19, 16, 32, tzinfo=CHINA)
    query = FlightQuery(((112.939867574218, 28.180547690102994),), 'circle', 200,
                        start, start + timedelta(minutes=15), 150, '轻型', demo=False)
    facts = published_evidence('changsha', query, query.geometry())
    assert '150 米' in facts[0].text
    assert '全部落入' in facts[1].text
    assert '未接入' in published_evidence('shenzhen', query, query.geometry())[1].text

    cells = np.array([[1., 1., 2., 2.], [2., 1., 3., 2.]])
    assert grid_coverage(box(1.5, 1.5, 2.5, 1.75), cells) == 'full'
    assert grid_coverage(box(0.5, 1.5, 1.5, 1.75), cells) == 'partial'
    assert grid_coverage(box(0.5, 0.5, 0.75, 0.75), cells) == 'none'
    older = replace(query, start=start.replace(month=4), end=start.replace(month=4)+timedelta(minutes=15))
    assert '更早' in published_evidence('changsha', older, older.geometry())[1].text


def test_notice_intervals_use_effective_dates_and_roll_24_hours_forward():
    text = '2026年11月7日0时至11月10日24时分区管制；11月11日0时至11月21日24时全市管制。'
    periods = interpret_periods(text)
    assert len(periods)==2
    assert periods[-1][0] == datetime(2026, 11, 11, tzinfo=CHINA)
    assert periods[-1][1] == datetime(2026, 11, 22, tzinfo=CHINA)
    assert not interpret_periods('11月11日至11月21日期间')


def test_live_lookup_reads_from_official_indexes_and_reuses_city_cache(monkeypatch):
    from skyguard import official_notices
    source = 'https://gov.example/index.html'
    monkeypatch.setitem(official_notices.CITY_SOURCES, 'test', (source,))
    requests = []

    def reply(request):
        requests.append(request.url.path)
        body = {
            '/index.html': '<a href="/notice.html">无人驾驶航空器临时管制通告</a>'
                           '<a href="https://other.example/fake.html">无人机临时管制通告</a>',
            '/notice.html': '<p>2026年11月19日16时至11月19日18时对活动区域实行管制。</p>',
        }[request.url.path]
        return httpx.Response(200, headers={'content-type': 'text/html; charset=utf-8'}, text=body)

    lookup = OfficialNoticeLookup(httpx.MockTransport(reply))
    result = lookup.search('test')
    assert result.checked == (source,)
    assert len(result.notices)==1 and result.notices[0].periods
    assert lookup.search('test') is result
    assert requests == ['/index.html', '/notice.html']
