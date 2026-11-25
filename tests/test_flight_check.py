"""Flight check verdict precedence, evidence and coverage across all dimensions."""
from dataclasses import replace
from datetime import timedelta
from itertools import product

import numpy as np
import pytest
from shapely import box

from skyguard.flight_check import (
    HEADLINES, CheckItem, ItemState, Verdict,
    build_flight_check, derive_verdict, model_notice_facts,
)
from skyguard.flight_rules import DEMO_START, FlightQuery, RuleBook, demo_city
from skyguard.official_airspace import BASE_SOURCES, HUNAN_EFFECTIVE, ORDINANCE, grid_coverage_ratio
from skyguard.official_notices import Notice, Scan


def _items(states):
    return tuple(CheckItem(str(index), '测试项', state, '') for index, state in enumerate(states))


@pytest.mark.parametrize(('states', 'verdict', 'headline'), [
    ((ItemState.PENDING, ItemState.PERMIT, ItemState.RESTRICTED), Verdict.RESTRICTED, '禁止或受限'),
    ((ItemState.UNKNOWN, ItemState.PERMIT), Verdict.PERMIT, '需申请后飞行'),
    ((ItemState.ATTENTION, ItemState.OK), Verdict.CHECK, '需核对后再飞行'),
    ((ItemState.OK, ItemState.OK), Verdict.CLEAR, '未发现限制'),
])
def test_verdict_table(states, verdict, headline):
    assert derive_verdict(_items(states)) is verdict
    assert HEADLINES[verdict] == headline


@pytest.mark.parametrize('states', [
    states for states in product(ItemState, repeat=2)
    if ItemState.PENDING in states or ItemState.UNKNOWN in states
])
def test_missing_indicators_cannot_yield_clear(states):
    assert derive_verdict(_items(states)) is not Verdict.CLEAR


def test_empty_checklist_cannot_yield_clear():
    assert derive_verdict(()) is Verdict.CHECK


@pytest.fixture
def query():
    return FlightQuery(((112.948, 28.173),), 'circle', 75, DEMO_START,
                       DEMO_START + timedelta(minutes=15), 70, '轻型', demo=False)


def _build(query, *, city='changsha', rules=(), **options):
    return build_flight_check(query, RuleBook(rules).evaluate(query), city_id=city, **options)


def _item(check, key):
    return next(item for item in check.items if item.key == key)


@pytest.mark.parametrize(('height', 'state', 'fragment'), [
    (119, ItemState.OK, '相对真高 119 m'),
    (120, ItemState.ATTENTION, '相对真高达到 120 m'),
    (121, ItemState.PERMIT, '需办理飞行活动申请'),
])
def test_altitude_thresholds(query, height, state, fragment):
    item = _item(_build(replace(query, altitude_agl=height), grid_ratio=1), 'altitude')
    assert item.title == '相对真高'
    assert item.state is state
    assert fragment in item.summary
    assert item.evidence[0].url == ORDINANCE


@pytest.mark.parametrize(('fields', 'state'), [
    ({'category': '轻型'}, ItemState.OK),
    ({'category': '中型'}, ItemState.PERMIT),
    ({'category': '大型'}, ItemState.PERMIT),
    ({'special': True}, ItemState.PERMIT),
    ({'category': '未知', 'special': True}, ItemState.UNKNOWN),
    ({'category': '大型', 'special_unknown': True}, ItemState.UNKNOWN),
])
def test_aircraft_and_operation(query, fields, state):
    check = _build(replace(query, **fields), grid_ratio=1)
    assert _item(check, 'aircraft').state is state


@pytest.mark.parametrize(('grid_state', 'ratio', 'state', 'summary'), [
    ('full', 1, ItemState.OK, '所选范围全部位于适飞网格内'),
    ('partial', 0.62, ItemState.PERMIT, '62% 位于适飞网格内，超出部分需申请'),
    ('none', 0, ItemState.PERMIT, '所选范围在适飞网格外，需申请'),
])
def test_changsha_coverage_at_effective_time(query, grid_state, ratio, state, summary):
    start = HUNAN_EFFECTIVE
    query = replace(query, start=start, end=start + timedelta(minutes=15))
    check = _build(query, grid_state=grid_state, grid_ratio=ratio)
    item = _item(check, 'base_airspace')
    assert (item.state, item.summary) == (state, summary)
    assert item.evidence[0].url == BASE_SOURCES['changsha'][1]


def test_grid_area_ratio_uses_union_and_excludes_boundary_only_contact():
    bounds = np.array([(0, 0, 1, 1), (0.5, 0, 1.25, 1)])
    assert grid_coverage_ratio(box(0, 0, 2, 1), bounds) == pytest.approx(0.625)
    assert grid_coverage_ratio(box(0, 0, 1, 1), bounds) == 1
    assert grid_coverage_ratio(box(1.25, 0, 2, 1), bounds) == 0


def test_changsha_before_effective_time_and_inapplicable_aircraft(query):
    earlier = HUNAN_EFFECTIVE - timedelta(minutes=1)
    early_query = replace(query, start=earlier, end=earlier + timedelta(minutes=15))
    assert _item(_build(early_query, grid_ratio=1), 'base_airspace').state is ItemState.UNKNOWN
    large = _build(replace(query, category='大型'), grid_ratio=1)
    assert _item(large, 'base_airspace').state is ItemState.PERMIT


@pytest.mark.parametrize('city', ['shanghai', 'guangzhou', 'shenzhen', 'hangzhou'])
def test_other_cities_without_computable_boundaries(query, city):
    item = _item(_build(query, city=city, grid_ratio=1), 'base_airspace')
    assert (item.state, item.summary) == (ItemState.UNKNOWN, '尚未接入可计算边界')
    assert bool(item.evidence) == (city in BASE_SOURCES)


@pytest.mark.parametrize(('point', 'coverage', 'local', 'venue', 'verdict'), [
    ((112.948, 28.173), ItemState.OK, ItemState.OK, ItemState.OK, Verdict.CLEAR),
    ((112.953, 28.1792), ItemState.OK, ItemState.RESTRICTED, ItemState.ATTENTION, Verdict.RESTRICTED),
    ((112.951, 28.1792), ItemState.OK, ItemState.OK, ItemState.ATTENTION, Verdict.CHECK),
    ((112.977, 28.192), ItemState.UNKNOWN, ItemState.OK, ItemState.OK, Verdict.CHECK),
])
def test_demo_coverage_and_rule_hits(query, point, coverage, local, venue, verdict):
    rules = demo_city()['rules']
    check = _build(replace(query, demo=True, points=(point,)), rules=rules)
    assert _item(check, 'base_airspace').state is coverage
    assert _item(check, 'local_rules').state is local
    assert _item(check, 'venue').state is venue
    assert [item.key for item in check.items] == [
        'altitude', 'aircraft', 'base_airspace', 'local_rules', 'venue',
    ]
    assert check.demo and not check.provisional and check.verdict is verdict
    assert bool(check.alternatives) == (local is ItemState.RESTRICTED or venue is ItemState.ATTENTION)


@pytest.mark.parametrize(('verified', 'state'), [(True, ItemState.RESTRICTED), (False, ItemState.UNKNOWN)])
def test_local_restriction_shows_rule_details_and_original_source(query, verified, state):
    rules = demo_city()['rules']
    rule = next(row for row in rules if row['kind'] == 'restriction')
    source = 'https://example.test/local-rule'
    rule.update(verified=verified, source=source)
    check = _build(replace(query, demo=True, points=((112.953, 28.1792),)), rules=rules)
    item = _item(check, 'local_rules')
    assert item.state is state
    assert all(text in item.details[0] for text in (rule['name'], rule['note'], '15:20', '15:40'))
    assert item.evidence[0].url == source


def test_notice_is_pending_in_provisional_result(query):
    check = _build(query, grid_ratio=1)
    assert _item(check, 'notices').state is ItemState.PENDING
    assert check.provisional and check.verdict is Verdict.CHECK


def test_notice_overlap_has_evidence_and_excludes_touching_period(query):
    matched = Notice('重叠公告', 'https://example.test/overlap', '原文的测试摘录', ((query.start, query.end),))
    touching = Notice('不重叠公告', 'https://example.test/touching', '', ((query.end, query.end + timedelta(hours=1)),))
    unclear = Notice('时段待确认', 'https://example.test/unclear', '未识别出时段')
    scan = Scan(('栏目',), ('未读栏目',), (touching, matched, unclear))
    check = _build(query, grid_ratio=1, scan=scan)
    item = _item(check, 'notices')
    assert item.state is ItemState.ATTENTION
    assert item.summary == '1 条公告时段重叠，空间范围待核对'
    assert [evidence.title for evidence in item.evidence] == [matched.title, unclear.title]
    assert item.evidence[0].url == matched.url
    assert item.evidence[0].note == matched.time_status(query)
    assert item.details[0] == matched.excerpt
    assert [fact['标题'] for fact in model_notice_facts(query, scan)] == [matched.title, unclear.title]
    assert not check.provisional and check.verdict is Verdict.CHECK


@pytest.mark.parametrize(('scan', 'message'), [
    (Scan(('栏目',), (), (Notice('未识别时段', 'https://example.test/unknown', ''),)), '未识别'),
    (Scan(('栏目',), ('不可达栏目',)), '未能读取'),
    (Scan(), '未读取到官方栏目'),
])
def test_incomplete_notice_scan_is_unknown(query, scan, message):
    check = _build(query, grid_ratio=1, scan=scan)
    item = _item(check, 'notices')
    assert item.state is ItemState.UNKNOWN and message in item.summary
    assert not check.provisional and check.verdict is Verdict.CHECK


def test_successful_notice_scan_without_matches_is_ok(query):
    later = Notice('另一时段', 'https://example.test/later', '',
                   ((query.end, query.end + timedelta(hours=1)),))
    check = _build(query, grid_ratio=1, scan=Scan(('栏目一', '栏目二'), (), (later,)))
    item = _item(check, 'notices')
    assert item.state is ItemState.OK
    assert item.summary == '已读 2 个栏目，未发现时段匹配的公告'
    assert not item.evidence and check.verdict is Verdict.CLEAR
