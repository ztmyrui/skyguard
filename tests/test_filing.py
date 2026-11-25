from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
import json

import pytest
from shapely.geometry import Point

from skyguard.aerial import local_metres
from skyguard.civic import CivicService
from skyguard.filing import (apply_receipt, change_state, create_filing, decode_package,
                             encode_package, match_filings)
from skyguard.flight_rules import DEMO_START, FlightQuery
from skyguard.infrastructure import SQLiteRepository
from skyguard.flight_route import FlightRoute, Waypoint

CENTER = (112.956, 28.181)
START = DEMO_START
QUERY = FlightQuery((CENTER,), 'circle', 100, START, START+timedelta(minutes=15), 70, '轻型')
PLAN = dict(id='plan', title='滨江航拍', city='changsha', query=QUERY.record(), result='待核对')


def event(distance=0, accuracy=20, seconds=120):
    _, unproject = local_metres(CENTER)
    lon,lat = unproject(distance, 0)
    return dict(id='field',lon=lon,lat=lat,accuracy_m=accuracy,observed_s=seconds)


def test_snapshot_identity_checksum_and_state_transitions():
    raw = deepcopy(PLAN)
    record = create_filing(raw,START,contact='填写的联系方式')
    assert create_filing(raw, START+timedelta(minutes=1))['id'] == record['id']
    raw['query']['points'] = ((113.0,28.0),)
    assert record['snapshot']['query']['points'] == [list(CENTER)]
    assert create_filing(raw, START)['id'] != record['id']
    assert decode_package(encode_package(record)) == record
    packet = json.loads(encode_package(record))
    packet['version'] = 3
    with pytest.raises(ValueError):
        decode_package(json.dumps(packet))
    with pytest.raises(ValueError):
        decode_package(encode_package(record).replace('滨江','滨河'))
    with pytest.raises(ValueError):
        change_state(record,'review',outcome='联系不上',at=START)
    matched = change_state(record, 'match', event_id='obs')
    assert matched['state'] == '已对应' and record['state'] == '已报备'
    assert change_state(matched,'undo') == record
    assert change_state(matched,'cancel')['state'] == '已取消'
    reviewed = change_state(matched,'review',outcome='已联系并确认',at=START)
    receipt = decode_package(encode_package(reviewed,receipt=True),receipt=True)
    assert apply_receipt(record,receipt)['state'] == '已核对'
    assert 'contact' not in receipt


def test_location_and_time_boundaries_without_automatic_changes():
    first = create_filing(PLAN, START)
    second = create_filing(dict(PLAN,id='second'),START)
    assert match_filings(event(),[first,second],0).title == '疑似对应'
    assert len(match_filings(event(110,20,0),[first,second],0).candidates) == 2
    assert match_filings(event(120,10),[first],0).title == '无对应报备'
    assert match_filings(event(219,20),[first],0).title == '无对应报备'
    assert match_filings(event(221,20),[first],0).title == '计划外越界'
    assert match_filings(event(seconds=900),[first],0).title == '无对应报备'
    assert match_filings(dict(event(),lat=None),[first],0).title == '无法判断'
    assert match_filings(event(),[change_state(first,'cancel')],0).title == '无对应报备'
    assert first['state'] == second['state'] == '已报备'


def test_shared_store_round_trip_audit(tmp_path):
    citizen = CivicService(SQLiteRepository(tmp_path/'citizen.sqlite3'))
    duty = CivicService(SQLiteRepository(tmp_path/'duty.sqlite3'))
    origin = (112.949, 28.181)
    route = FlightRoute((Waypoint(*origin, 70, 180),), 5, origin, START, '轻型')
    query = replace(QUERY, route=route, radius_m=25)
    observed = dict(event(), lon=origin[0], lat=origin[1], altitude_agl=70)
    plan = citizen.save_plan('changsha', PLAN['title'], query, '待核对')
    record = citizen.public_filing(plan,contact='测试联系')
    assert citizen.public_filing(plan)['id'] == record['id']
    path = tmp_path/'filing.json'
    citizen.filing_export(record['id'],path)
    identifier = duty.import_filing(path.read_text(encoding='utf8'))
    assert duty.import_filing(path.read_text(encoding='utf8')) == identifier
    assert len(duty.data['filings']) == 1
    duty.change_filing(identifier,'match',event_view=observed,clock=120)
    duty.change_filing(identifier,'undo')
    duty.change_filing(identifier,'match',event_view=observed,clock=120)
    duty.change_filing(identifier,'review',outcome='飞手说明',note='回访说明',at=START)
    receipt = tmp_path/'review.json'
    duty.filing_export(identifier,receipt,receipt=True)
    assert citizen.save_receipt(receipt.read_text(encoding='utf8')) == identifier
    assert citizen.plan_reviewed(plan)
    assert {'确认对应报备','撤销对应报备','回访飞手','导出核对回执'} <= {row['action'] for row in duty.repo.history()}
    assert all('测试联系' not in str(row) for row in duty.repo.history())
