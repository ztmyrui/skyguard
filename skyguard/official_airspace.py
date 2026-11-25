"""Versioned public airspace snapshots and verified nationwide altitude guidance."""

from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from math import nextafter
from pathlib import Path

import numpy as np
from shapely import box, union_all


ORDINANCE = 'https://app.www.gov.cn/govdata/gov/202306/28/504796/article.html'
HUNAN_PUBLICATION = 'https://fgw.hunan.gov.cn/fgw/xxgk_70899/gzdtf/gzdt/202605/t20260513_33975865.html'
HUNAN_EFFECTIVE = datetime.fromisoformat('2026-05-14T00:00:00+08:00')
SHANGHAI_PUBLICATION = 'https://www.shanghai.gov.cn/nw12344/20260807/30c537712f1448ed9181320820ffa0cb.html'
BASE_SOURCES = {
    'changsha': ('湖南省发改委 · 2026 年 5 月版', HUNAN_PUBLICATION),
    'shanghai': ('上海市政府 · 2026 版适飞空域公告', SHANGHAI_PUBLICATION),
}


@dataclass(frozen=True)
class AirspaceEvidence:
    title: str
    text: str
    source: str = ''
    url: str = ''


@lru_cache(maxsize=1)
def hunan_grid():
    with np.load(Path(__file__).with_name('assets') / 'hunan-airspace-202605.npz', allow_pickle=False) as data:
        return data['bounds']


def grid_coverage(geometry, bounds):
    x0, y0, x1, y1 = geometry.bounds
    candidates = bounds[(bounds[:, 0] <= x1) & (bounds[:, 1] <= y1)
                        & (bounds[:, 2] >= x0) & (bounds[:, 3] >= y0)]
    if not len(candidates):
        return 'none'
    merged = union_all(box(*candidates.T))
    if merged.covers(geometry):
        return 'full'
    return 'partial' if merged.intersects(geometry) else 'none'


def grid_coverage_ratio(geometry, bounds) -> float:
    """Return the covered area fraction in the same local basis as flight_rules."""
    if geometry.is_empty or not geometry.area:
        return 0.0
    x0, y0, x1, y1 = geometry.bounds
    candidates = bounds[(bounds[:, 0] <= x1) & (bounds[:, 1] <= y1)
                        & (bounds[:, 2] >= x0) & (bounds[:, 3] >= y0)]
    if not len(candidates):
        return 0.0
    merged = union_all(box(*candidates.T))
    if merged.covers(geometry):
        return 1.0
    # Local east/north scale factors are constant and cancel in the area ratio.
    return min(nextafter(1.0, 0.0), merged.intersection(geometry).area / geometry.area)


def published_evidence(city, query, geometry):
    facts = []
    if query.altitude_agl > 120:
        facts.append(AirspaceEvidence(
            '飞行申请要求',
            f'计划最高离地 {query.altitude_agl} 米，超过 120 米。普通飞行应核对并办理相应飞行活动申请。',
            '国务院《无人驾驶航空器飞行管理暂行条例》第十九、二十六条', ORDINANCE))
    elif query.altitude_agl == 120:
        facts.append(AirspaceEvidence(
            '所选高度处于 120 米界线',
            '请核对官方空域上限与拟飞真高。', '国务院条例第十九条', ORDINANCE))

    source, url = BASE_SOURCES.get(city, ('', ''))
    detail = '当前未接入可计算的官方边界。本地已核实规则与公开公告仍可在下方查看。'
    if city == 'changsha' and query.start >= HUNAN_EFFECTIVE:
        coverage = grid_coverage(geometry, hunan_grid())
        detail = {
            'full': '所选范围的水平投影全部落入这版微型、轻型、小型适飞网格。高度、机型和临时管制仍需单独核对。',
            'partial': '所选范围部分超出这版微型、轻型、小型适飞网格，请核对超出部分的空域属性。',
            'none': '这版微型、轻型、小型适飞网格未覆盖所选范围，请核对该范围的空域属性。',
        }[coverage]
    elif city == 'changsha':
        detail = '湖南现有地图自 2026 年 5 月 14 日启用，无法用于核对更早的时段。'
    facts.append(AirspaceEvidence('官方基础空域', detail, source, url))
    return tuple(facts)
