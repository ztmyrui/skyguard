"""Versioned local sensitivity types. A sensitivity rating is an advisory."""
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
import json
from pathlib import Path

from shapely.geometry import Point, shape

from .flight_check import CHINA
from .flight_route import corridor
from .flight_rules import instant


ASSETS = Path(__file__).with_name('assets')


@lru_cache(maxsize=None)
def read_table(name):
    return json.loads((ASSETS/name).read_text(encoding='utf8'))


@dataclass(frozen=True)
class Zone:
    id: str
    name: str
    kind: str
    geometry: object
    start: datetime | None = None
    end: datetime | None = None
    source: str = '演练区域'
    confirmed: bool = False
    restricted: bool = False
    holiday: bool = False
    helipad: bool = False
    override: str = ''
    confidence: str = ''

    def rating(self, when, types=None):
        when = when.astimezone(CHINA)
        if self.start and when < self.start or self.end and when >= self.end:
            return None
        values = (types or read_table('zone-types.json')['types'])[self.kind]
        for days, begin, end in values.get('peak', ()):
            if when.weekday() in days and begin <= when.strftime('%H:%M') < end:
                return self.override or 'S1'
        if self.holiday and self.kind == 'school':
            return self.override or 'S3'
        if self.kind in ('event','venue') and self.start and self.end or self.kind == 'hospital' and self.helipad:
            return self.override or 'S1'
        if self.kind == 'park' and self.holiday:
            return self.override or 'S1'
        return self.override or values['default']

    def description(self, when, types=None):
        if self.rating(when, types) != 'S1':
            return self.source
        if self.override:
            return '值班员设为 S1'
        if self.kind in ('event','venue','notice'):
            return '处于当前配置的活动时段'
        if self.kind=='airport' or self.kind=='hospital' and self.helipad:
            return '机场或停机坪区域'
        return '命中当前高峰时段'

    def contains(self, point, when, types=None):
        return self.rating(when, types) is not None and self.geometry.covers(Point(*point))


def city_profile(city):
    return read_table('city-profiles.json')['cities'].get(city, {'grade':'C','coverage':'底图'})


def zone_from_record(row, types=None):
    types = types or read_table('zone-types.json')['types']
    geometry = shape(row['geometry']) if row.get('geometry') else corridor(
        (tuple(row['point']),), row.get('radius', types[row['kind']]['buffer']))
    if not geometry.is_valid or geometry.is_empty or geometry.geom_type not in ('Polygon','MultiPolygon'):
        raise ValueError('请核对区域边界')
    if row.get('start') and row.get('end') and instant(row['start']) >= instant(row['end']):
        raise ValueError('区域开始时间应早于结束时间')
    return Zone(row['id'],row['name'],row['kind'],geometry,
                instant(row['start']) if row.get('start') else None,
                instant(row['end']) if row.get('end') else None,
                row.get('source','演练区域'), bool(row.get('confirmed',False)),
                bool(row.get('restricted',False) and row.get('confirmed',False)),
                bool(row.get('holiday',False)), bool(row.get('helipad',False)), row.get('override',''),
                row.get('region_confidence', '已确认' if row.get('confirmed') else '边界待核对'))


def zone_hits(point, when, zones):
    return tuple(zone for zone in zones if zone.contains(point, when))
