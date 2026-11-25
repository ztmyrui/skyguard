"""Local venue filings, portable records, and observational correspondence."""
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from hmac import compare_digest
import json
from math import isfinite
from uuid import UUID

from shapely import transform
from shapely.geometry import Point

from .aerial import local_metres
from .flight_rules import DEMO_START, FlightQuery, instant, lonlat

VERSION = 1
MAX_BYTES = 131_072
# Engineering default for uncertain map/observation alignment, in metres.
EDGE_BUFFER_M = 100
CALL_OUTCOMES = ('已联系并确认', '联系不上', '飞手说明')
REVIEW_OUTCOMES = ('合规飞行', '轻微偏离已纠正', '违规已劝离', '失联已找回', '已移交')
STATES = {'已报备', '已对应', '已核对', '已取消'}


def _json(body):
    return json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(body):
    return sha256(_json(body).encode()).hexdigest()


def _text(value, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError('请核对报备字段和长度')
    return value.strip()


def _query(data):
    fields = ('points', 'form', 'radius_m', 'start', 'end', 'altitude_agl', 'category')
    if not isinstance(data, dict) or not all(key in data for key in fields):
        raise ValueError('报备范围信息不完整')
    if (type(data['radius_m']) is not int or type(data['altitude_agl']) is not int
            or any(type(data.get(key, False)) is not bool for key in ('special','demo','special_unknown'))):
        raise ValueError('报备范围参数无效')
    points = tuple(lonlat(point) for point in data['points'])
    if data['form'] == 'circle' and len(points) != 1:
        raise ValueError('圆形报备需要一个中心点')
    query = FlightQuery(points, data['form'], data['radius_m'], instant(data['start']),
                        instant(data['end']), data['altitude_agl'], data['category'],
                        data.get('special', False), data.get('demo', False), data.get('special_unknown', False),
                        data.get('route'))
    query.validate()
    return query


def snapshot(plan, purpose=None):
    query = _query(plan['query'])
    return {'title': _text(plan['title'], 120, True), 'city': _text(plan['city'], 48, True),
            'query': json.loads(_json(query.record())),
            'purpose': _text(plan.get('purpose', '') if purpose is None else purpose, 200),
            'result': _text(plan.get('result', ''), 160)}


def create_filing(plan, created_at, *, contact='', purpose=None, material='', attempt=0):
    saved = snapshot(plan, purpose)
    plan_id = _text(plan['id'], 128, True)
    identity = [plan_id, saved]
    if attempt:
        identity.append(attempt)
    record = {'id': _hash(identity)[:32], 'plan_id': plan_id, 'snapshot': saved,
              'created_at': instant(created_at).isoformat(), 'contact': _text(contact, 300),
              'material': _text(material, 5000), 'state': '已报备', 'event_id': None, 'review': None}
    if attempt:
        record['attempt'] = attempt
    return record


def _known_id(value):
    _text(value, 32, True)
    if len(value) != 32 or UUID(hex=value).hex != value:
        raise ValueError('报备编号无效')
    return value


def change_state(record, action, *, event_id=None, outcome=None, note='', at=None, comparison=None):
    new = deepcopy(record)
    state = record['state']
    if action == 'match' and state == '已报备':
        new.update(state='已对应', event_id=_text(event_id, 128, True))
    elif action == 'undo' and state == '已对应':
        new.update(state='已报备', event_id=None)
    elif action == 'cancel' and state in ('已报备', '已对应'):
        new.update(state='已取消', event_id=None)
    elif action == 'review' and state == '已对应' and outcome in CALL_OUTCOMES + REVIEW_OUTCOMES:
        new.update(state='已核对', review={'outcome': outcome, 'note': _text(note, 1200),
                                          'at': instant(at).isoformat()})
        if comparison is not None:
            new['review']['comparison'] = comparison
    else:
        raise ValueError('该报备当前状态不支持这项操作')
    return new


def encode_package(record, *, receipt=False):
    if receipt:
        if record['state'] != '已核对':
            raise ValueError('完成回访后可导出核对回执')
        payload = {key: record[key] for key in ('id', 'plan_id', 'review')}
        payload['snapshot_digest'] = _hash(record['snapshot'])
    else:
        payload = {key: record[key] for key in ('id', 'plan_id', 'snapshot', 'created_at', 'contact', 'material')}
        if record.get('attempt'):
            payload['attempt'] = record['attempt']
    if record.get('dataset') == '演示':
        payload['dataset'] = '演示'
    body = dict(version=VERSION, kind='receipt' if receipt else 'filing', payload=payload)
    return _json(dict(body, checksum=_hash(body)))


def decode_package(text, *, receipt=False):
    invalid = '报备文件不完整或校验失败'
    try:
        if len(text.encode()) > MAX_BYTES:
            raise ValueError(invalid)
        packet = json.loads(text)
        kind = 'receipt' if receipt else 'filing'
        if (not isinstance(packet, dict) or set(packet) != {'version','kind','payload','checksum'}
                or type(packet['version']) is not int or packet['version'] != VERSION or packet['kind'] != kind):
            raise ValueError('请选择同版本的' + ('核对回执' if receipt else '报备包'))
        body = {key: packet[key] for key in ('version','kind','payload')}
        if not compare_digest(_hash(body), packet['checksum']):
            raise ValueError(invalid)
        data = packet['payload']
        marker = data.pop('dataset', None)
        if marker not in (None, '演示'):
            raise ValueError(invalid)
        _known_id(data['id'])
        _text(data['plan_id'], 128, True)
        if receipt:
            review, digest = data['review'], data['snapshot_digest']
            if (set(data) != {'id','plan_id','review','snapshot_digest'} or set(review) - {'comparison'} != {'outcome','note','at'}
                    or review['outcome'] not in CALL_OUTCOMES + REVIEW_OUTCOMES or len(digest) != 64
                    or any(char not in '0123456789abcdef' for char in digest)):
                raise ValueError(invalid)
            instant(review['at'])
            _text(review['note'], 1200)
            if 'comparison' in review:
                comparison = review['comparison']
                fields = {'horizontal_m','height_m','duration_s','count'}
                if not isinstance(comparison, dict) or set(comparison) != fields or any(
                        type(value) not in (int,float) or not isfinite(value) or value < 0
                        for value in comparison.values()):
                    raise ValueError(invalid)
            if marker:
                data['dataset'] = marker
            return data
        if set(data) - {'attempt'} != {'id','plan_id','snapshot','created_at','contact','material'}:
            raise ValueError(invalid)
        plan = dict(data['snapshot'], id=data['plan_id'])
        attempt = data.get('attempt', 0)
        if type(attempt) is not int or not 0 <= attempt <= MAX_BYTES:
            raise ValueError(invalid)
        normalized = create_filing(plan, data['created_at'], contact=data['contact'],
                                   material=data['material'], attempt=attempt)
        if data['id'] != normalized['id'] or data['snapshot'] != normalized['snapshot']:
            raise ValueError(invalid)
        if marker:
            normalized['dataset'] = marker
        return normalized
    except (KeyError, TypeError, OverflowError, AttributeError, RecursionError) as exc:
        raise ValueError(invalid) from exc


def apply_receipt(record, receipt):
    if (record['id'] != receipt['id'] or record['plan_id'] != receipt['plan_id']
            or _hash(record['snapshot']) != receipt['snapshot_digest'] or record['state'] == '已取消'):
        raise ValueError('回执和本机报备不匹配')
    if record['state'] == '已核对':
        if record['review'] != receipt['review']:
            raise ValueError('本机已有不同的核对记录')
        return record
    return dict(record, state='已核对', review=deepcopy(receipt['review']))


@dataclass(frozen=True)
class Correspondence:
    title: str
    candidates: tuple = ()
    deviations: tuple = ()


def match_filings(event_view, filings, clock, buffer_m=EDGE_BUFFER_M):
    lon, lat = event_view.get('lon'), event_view.get('lat')
    accuracy = event_view.get('accuracy_m')
    if (lon is None or lat is None or accuracy is None
            or not all(isfinite(value) for value in (lat, lon, accuracy)) or accuracy < 0):
        return Correspondence('无法判断')
    if isinstance(clock, datetime):
        when = instant(event_view.get('observed_at') or clock)
    else:
        from datetime import timedelta
        when = DEMO_START + timedelta(seconds=event_view.get('observed_s', clock))
    project, _ = local_metres((lon, lat))
    measured = Point(0, 0)
    surveyed = measured.buffer(accuracy) if accuracy else measured
    matches, outside = [], []
    for record in filings:
        saved = record['snapshot']
        if record['state'] == '已取消' or saved['city'] != event_view.get('city', 'changsha'):
            continue
        query = _query(saved['query'])
        if not query.start <= when < query.end:
            continue
        area = transform(query.geometry(), project, interleaved=False)
        distance = area.distance(measured)
        if distance <= accuracy:
            ratio = area.intersection(surveyed).area / surveyed.area if accuracy else float(area.covers(measured))
            matches.append((record['id'], min(1.0, ratio), distance))
        elif distance > accuracy + buffer_m:
            outside.append((record['id'], 0.0, distance))
    if matches:
        return Correspondence('疑似对应', tuple(sorted(matches, key=lambda c: (-c[1], c[2], c[0]))))
    if outside:
        return Correspondence('计划外越界', tuple(sorted(outside, key=lambda c: (c[2], c[0]))))
    return Correspondence('无对应报备')

def route_filing(record):
    return bool(record['snapshot']['query'].get('route'))


@dataclass(frozen=True)
class RouteMatch:
    identifier: str
    horizontal_m: float
    altitude_m: float | None
    delay_s: float
    conclusion: str


def match_route(observation, filings, when):
    """Match civilian position observations to saved aerial filming plans."""
    from datetime import timedelta
    from .flight_route import ROUTE_LIMITS, deviation

    when = instant(when)
    lon, lat = observation.get('lon'), observation.get('lat')
    accuracy = observation.get('accuracy_m')
    if any(v is None or not isfinite(v) for v in (lon, lat, accuracy)) or accuracy < 0:
        return Correspondence('无法判断')
    results = []
    for record in filings:
        saved = record['snapshot']
        if not route_filing(record) or record['state'] == '已取消' or saved['city'] != observation.get('city', 'changsha'):
            continue
        route = _query(saved['query']).route
        time_tolerance = 60
        if not route.start - timedelta(seconds=time_tolerance) <= when < route.end + timedelta(seconds=time_tolerance):
            continue
        sample = min(max(when, route.start), route.end - timedelta(microseconds=1))
        cross, height, delay = deviation(route, sample, lon, lat, observation.get('altitude_agl'))
        delay += (when - sample).total_seconds()
        _, _, horizontal_limit, altitude_limit = ROUTE_LIMITS[route.category]
        if (cross - accuracy > horizontal_limit or abs(delay) > time_tolerance
                or (height is not None and abs(height) > altitude_limit)):
            label = '偏离'
        elif height is None or cross + accuracy > horizontal_limit:
            label = '无法判断'
        else:
            label = '疑似对应'
        results.append(RouteMatch(record['id'], cross, height, delay, label))
    order = {'疑似对应': 0, '无法判断': 1, '偏离': 2}
    results.sort(key=lambda row: (order[row.conclusion], row.horizontal_m, abs(row.delay_s), row.identifier))
    if not results:
        return Correspondence('无对应报备')
    return Correspondence(results[0].conclusion,
                          tuple((r.identifier, 1.0 if r.conclusion == '疑似对应' else 0.0, r.horizontal_m)
                                for r in results), tuple(results))


def correspondence(view, filings, clock):
    from datetime import timedelta
    if isinstance(clock, datetime):
        when = instant(view.get('observed_at') or clock)
    else:
        seconds = view.get('observed_s')
        if seconds is None and clock is None:
            return Correspondence('无法判断')
        when = DEMO_START + timedelta(seconds=clock if seconds is None else seconds)
    legacy = match_filings(view, (r for r in filings if not route_filing(r)), clock)
    current = match_route(view, filings, when)
    candidates = current.candidates + legacy.candidates
    if not current.candidates:
        return legacy
    if legacy.title == '疑似对应' and current.title != '疑似对应':
        return Correspondence(legacy.title, legacy.candidates, current.deviations)
    if current.title == '疑似对应':
        candidates = tuple(r for r in current.candidates if r[1]) + (
            legacy.candidates if legacy.title == '疑似对应' else ())
    return Correspondence(current.title, candidates, current.deviations)
