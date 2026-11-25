"""Flight conditions derived from evaluated rules and public evidence."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from shapely import union_all

from .flight_rules import polygon
from .official_airspace import (
    BASE_SOURCES, HUNAN_EFFECTIVE, ORDINANCE, grid_coverage_ratio, hunan_grid,
)

CHINA = timezone(timedelta(hours=8))
ALTITUDE_LIMIT = 120
LARGE_AIRCRAFT = ('中型', '大型')


class ItemState(str, Enum):
    OK = 'ok'
    ATTENTION = 'attention'
    PERMIT = 'permit'
    RESTRICTED = 'restricted'
    UNKNOWN = 'unknown'
    PENDING = 'pending'


@dataclass(frozen=True)
class Evidence:
    title: str
    url: str = ''
    note: str = ''


@dataclass(frozen=True)
class CheckItem:
    key: str
    title: str
    state: ItemState
    summary: str
    details: tuple[str, ...] = ()
    evidence: tuple[Evidence, ...] = ()


class Verdict(str, Enum):
    CLEAR = 'clear'
    CHECK = 'check'
    PERMIT = 'permit'
    RESTRICTED = 'restricted'


@dataclass(frozen=True)
class FlightCheck:
    items: tuple[CheckItem, ...]
    verdict: Verdict
    provisional: bool
    headline: str
    alternatives: tuple[tuple[datetime, datetime], ...]
    demo: bool


HEADLINES = {
    Verdict.CLEAR: '未发现限制',
    Verdict.CHECK: '需核对后再飞行',
    Verdict.PERMIT: '需申请后飞行',
    Verdict.RESTRICTED: '禁止或受限',
}
VERDICT_PRIORITY = (
    ({ItemState.RESTRICTED}, Verdict.RESTRICTED),
    ({ItemState.PERMIT}, Verdict.PERMIT),
    ({ItemState.UNKNOWN, ItemState.ATTENTION, ItemState.PENDING}, Verdict.CHECK),
)


def derive_verdict(items: tuple[CheckItem, ...]) -> Verdict:
    states = {item.state for item in items}
    for decisive_states, verdict in VERDICT_PRIORITY:
        if states & decisive_states:
            return verdict
    return Verdict.CLEAR if states == {ItemState.OK} else Verdict.CHECK


def altitude_requires_permit(height):
    return height > ALTITUDE_LIMIT


def _altitude(height):
    if altitude_requires_permit(height):
        state, summary = ItemState.PERMIT, f'相对真高 {height:g} m，超过真高 120 m 适飞上限，需办理飞行活动申请'
    elif height == ALTITUDE_LIMIT:
        state, summary = ItemState.ATTENTION, '相对真高达到 120 m，须核对该空域上限'
    else:
        state, summary = ItemState.OK, f'相对真高 {height:g} m，低于真高 120 m 适飞上限'
    source = Evidence('国务院《无人驾驶航空器飞行管理暂行条例》', ORDINANCE)
    return CheckItem('altitude', '相对真高', state, summary, evidence=(source,))


def _aircraft(query):
    if query.category == '未知' or query.special_unknown:
        state, summary = ItemState.UNKNOWN, '请补充机型或核对特殊情形'
    elif query.category in LARGE_AIRCRAFT or query.special:
        state, summary = ItemState.PERMIT, '所选机型或特殊情形需办理申请'
    else:
        state, summary = ItemState.OK, f'{query.category} · 常规飞行'
    return CheckItem('aircraft', '机型与情形', state, summary)


def _rule_details(rules):
    return tuple(
        f'{rule.name}\n{rule.start.astimezone(CHINA):%m-%d %H:%M} — '
        f'{rule.end.astimezone(CHINA):%m-%d %H:%M}\n{rule.note}'.strip()
        for rule in rules
    )


def _rule_sources(rules):
    return tuple(
        Evidence(f'{rule.name} · {rule.issuer}原文' if rule.issuer else f'{rule.name} · 原文', rule.source)
        for rule in rules if rule.source
    )


def _demo_coverage(result):
    proofs = result['proofs']
    geometry = result['geometry']
    covered = bool(proofs and union_all([polygon(rule.rings) for rule in proofs]).covers(geometry))
    state = ItemState.OK if covered else ItemState.UNKNOWN
    summary = '所选范围由演示规则完整覆盖' if covered else '演示资料未覆盖所选范围'
    return CheckItem('base_airspace', '基础空域', state, summary,
                     _rule_details(proofs), _rule_sources(proofs))


def _grid_condition(geometry, ratio, state):
    try:
        if ratio is None and state not in ('full', 'none'):
            ratio = grid_coverage_ratio(geometry, hunan_grid())
    except (OSError, ValueError):
        return ItemState.UNKNOWN, '长沙适飞网格暂未读取，需到 UOM 核对'
    if state == 'full' or (state is None and ratio == 1):
        return ItemState.OK, '所选范围全部位于适飞网格内'
    if state == 'none' or ratio == 0:
        return ItemState.PERMIT, '所选范围在适飞网格外，需申请'
    if ratio is not None and 0 < ratio < 1:
        percent = '<1%' if ratio < 0.01 else '>99%' if ratio > 0.99 else f'{ratio:.0%}'
        return ItemState.PERMIT, f'{percent} 位于适飞网格内，超出部分需申请'
    return ItemState.UNKNOWN, '长沙适飞网格覆盖范围待核对'


def _base_airspace(query, result, city_id, ratio, grid_state):
    if query.demo:
        return _demo_coverage(result)
    source = BASE_SOURCES.get(city_id)
    evidence = (Evidence(*source),) if source else ()
    if city_id != 'changsha':
        state, summary = ItemState.UNKNOWN, '尚未接入可计算边界'
    elif query.start < HUNAN_EFFECTIVE:
        state, summary = ItemState.UNKNOWN, '所选时间早于长沙适飞网格生效日（2026-05-14）'
    elif query.category in LARGE_AIRCRAFT:
        state, summary = ItemState.PERMIT, '适飞网格适用于微型、轻型、小型，所选机型需申请'
    else:
        state, summary = _grid_condition(result['geometry'], ratio, grid_state)
    return CheckItem('base_airspace', '基础空域', state, summary, evidence=evidence)


def _local_rules(result):
    restrictions, unresolved = result['restrictions'], result['unresolved']
    if restrictions:
        state, summary = ItemState.RESTRICTED, f'命中 {len(restrictions)} 条已核实限制'
    elif unresolved:
        state, summary = ItemState.UNKNOWN, f'{len(unresolved)} 条本地公告待核实'
    else:
        state, summary = ItemState.OK, '本地已核实规则中未发现'
    details = _rule_details(restrictions) + tuple(f'待核实 · {doc}' for doc in _rule_details(unresolved))
    return CheckItem('local_rules', '临时限制', state, summary, details,
                     _rule_sources((*restrictions, *unresolved)))


def _venue(rules):
    state = ItemState.ATTENTION if rules else ItemState.OK
    summary = f'涉及 {len(rules)} 项场地活动，请先协调' if rules else '本地资料中未发现需协调的场地'
    return CheckItem('venue', '场地协调', state, summary, _rule_details(rules), _rule_sources(rules))


def _overlaps(notice, query):
    return any(start < query.end and query.start < end for start, end in notice.periods)


def _relevant_notices(query, scan):
    return tuple(notice for notice in scan.notices if not notice.periods or _overlaps(notice, query))


def model_notice_facts(query, scan):
    """Format the relevant notice inputs for the advisory model."""
    return [
        {'标题': notice.title, '原文链接': notice.url, '原文摘录': notice.excerpt,
         '时段对照': notice.time_status(query)}
        for notice in _relevant_notices(query, scan)[:3]
    ]


def _notices(query, scan):
    if scan is None:
        return CheckItem('notices', '临时公告', ItemState.PENDING, '正在检索官方栏目…')
    notices = _relevant_notices(query, scan)
    overlapping = sum(bool(notice.periods) for notice in notices)
    gaps = []
    if any(not notice.periods for notice in notices):
        gaps.append('部分相关公告未识别出有效时段')
    if scan.stale:
        gaps.append('公告更新失败，显示上次读取结果')
    if not scan.checked:
        gaps.append('未读取到官方栏目')
    elif scan.inaccessible:
        gaps.append('部分官方栏目或公告正文未能读取')
    if overlapping:
        state = ItemState.ATTENTION
        summary = f'{overlapping} 条公告时段重叠，空间范围待核对'
    elif gaps:
        state, summary = ItemState.UNKNOWN, '，'.join(gaps)
    else:
        state = ItemState.OK
        summary = f'已读 {len(scan.checked)} 个栏目，未发现时段匹配的公告'
    evidence = tuple(Evidence(notice.title, notice.url, notice.time_status(query)) for notice in notices)
    # For notices, the first detail per evidence entry is its excerpt.
    details = tuple(notice.excerpt for notice in notices)
    if scan.at:
        checked = scan.at.astimezone(CHINA)
        details += (f'官方栏目 · {"上次读取" if scan.stale else "读取时间"} {checked:%Y-%m-%d %H:%M}',)
    if overlapping:
        details += tuple(gaps)
    return CheckItem('notices', '临时公告', state, summary, details, evidence)


def build_flight_check(query, rule_result, *, city_id, scan=None, grid_ratio=None, grid_state=None):
    """Build a deterministic result from the inputs available at this query stage."""
    items = (
        _altitude(query.altitude_agl),
        _aircraft(query),
        _base_airspace(query, rule_result, city_id, grid_ratio, grid_state),
        _local_rules(rule_result),
        _venue(rule_result['venue']),
    )
    if not query.demo:
        items += (_notices(query, scan),)
    verdict = derive_verdict(items)
    if exercise := rule_result.get('exercise'):
        items += (CheckItem('exercise', '态势联动演练', ItemState.ATTENTION,
                            '受临时活动区影响 · 建议避开演练时段',
                            tuple(f'{rule.name} · 建议改至 {rule.end:%m-%d %H:%M} 后复查'
                                  for rule in exercise)),)
    return FlightCheck(
        items, verdict, any(item.state == ItemState.PENDING for item in items),
        HEADLINES[verdict],
        tuple(rule_result['alternatives']), query.demo,
    )
