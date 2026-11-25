import ast
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from skyguard.actual_flight import route_for, track_review
from skyguard.civic import CivicService
from skyguard.demo_dataset import create_demo, load_demo, save_demo
from skyguard.filing import decode_package, encode_package
from skyguard.flight_route import FlightRoute, destination, tracking
from skyguard.flight_rules import FlightQuery
from skyguard.infrastructure import SQLiteRepository
from skyguard.system_clock import CHINA, SystemClock

NOW = datetime(2026,11,28,17,25,tzinfo=CHINA)
HOME = (112.946,28.174)


def test_clock_forward_pause_speed_and_next():
    seconds = [50]
    clock = SystemClock(real=lambda _: NOW, counter=lambda: seconds[0])
    with pytest.raises(ValueError):
        clock.advance(1)
    clock.restore(NOW)
    clock.set_speed(60)
    clock.play(True)
    seconds[0] += 3
    assert clock.now() == NOW + timedelta(minutes=3)
    clock.play(False)
    seconds[0] += 12
    assert clock.now() == NOW + timedelta(minutes=3)
    next_event = NOW + timedelta(minutes=5)
    assert clock.next_event([NOW + timedelta(hours=1), NOW, next_event]) == next_event
    clock.jump_to(next_event)
    assert clock.jump_to(next_event) == next_event
    with pytest.raises(ValueError):
        clock.jump_to(NOW)


def test_runtime_uses_system_clock():
    denied = {'datetime.now','datetime.utcnow','time.time','QDateTime.currentDateTime'}
    offenders = []
    for path in Path('skyguard').rglob('*.py'):
        if path.name == 'system_clock.py':
            continue
        module = ast.parse(path.read_text(encoding='utf-8-sig'))
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in denied:
                offenders.append((str(path), node.lineno))
    assert not offenders


def test_demo_archive_data_and_clock_without_touching_real(tmp_path):
    real = SQLiteRepository(tmp_path/'civic.sqlite3')
    real.save(dict(label='真实'))
    before = sha256(real.path.read_bytes()).digest()
    demo = create_demo(tmp_path)
    repo = SQLiteRepository(demo/'civic.sqlite3')
    assert repo.load() == real.load()
    archive = tmp_path/'演示.zip'
    repo.save(dict(label='演示1'))
    save_demo(demo, archive, NOW)
    repo.save(dict(label='演示2'))
    restored, when = load_demo(tmp_path, archive)
    assert restored == demo and when == NOW
    assert repo.load() == dict(label='演示1')
    assert sha256(real.path.read_bytes()).digest() == before


def test_filing_window_and_legacy_route():
    old = tracking(HOME,90,200,60,start=NOW,category='轻型')
    assert FlightRoute.parse(old.record()).filing_window == (old.start,old.end)
    data = old.record() | {'window_start': NOW.isoformat()}
    route = FlightRoute.parse(data)
    assert route.filing_window == (NOW, NOW + timedelta(minutes=30))
    with pytest.raises(ValueError):
        FlightRoute.parse(data | {'window_end': (NOW + timedelta(minutes=29)).isoformat()})
    assert route.shifted(NOW + timedelta(minutes=17)).start > route.start


def test_recording_duty_link_review_receipt_and_log(tmp_path):
    route = FlightRoute.parse(tracking(HOME,90,300,60,start=NOW,category='轻型').record()
                              | {'window_start':NOW.isoformat()})
    query = FlightQuery((),'route',25,NOW,route.end,60,'轻型',demo=False,route=route)
    civic = CivicService(SQLiteRepository(create_demo(tmp_path)/'civic.sqlite3'))
    name = civic.save_plan('changsha','本机验收',query,'需核对',verdict='check')
    filing = civic.public_filing(name)
    actual = NOW + timedelta(minutes=10)
    shifted = route.shifted(actual)
    from skyguard.flight_route import planned_position
    def sample(i, cross=0):
        at = actual + timedelta(seconds=i)
        target = planned_position(shifted, at)
        position = destination(target.xy,0,cross)
        return dict(t=i,at=at.isoformat(),lon=position[0],lat=position[1],height=target.altitude_m,
                    heading=90,speed=5,battery=900-i,mode='手动')
    civic.start_recorded_flight(name,actual,sample(0))
    landed = actual + timedelta(seconds=14)
    civic.append_flight_samples(name,[sample(i, 80 if i<13 else 0) for i in range(1,15)],ended=landed)
    plan = civic.data['plans'][0]
    assert route_for(plan).start == actual
    assert civic.filing(filing['id'])['event_id'] == 'recorded:'+name
    summary = track_review(plan,width=25)
    assert 79 < summary['horizontal_m'] < 81
    assert summary['duration_s'] == 12 and len(summary['intervals']) == 1
    comparison = {key:summary[key] for key in ('horizontal_m','height_m','duration_s')} | {'count':1}
    record = civic.review_recorded_flight(name,'轻微偏离已纠正','实测记录',landed,comparison)
    receipt = decode_package(encode_package(record,receipt=True),receipt=True)
    assert receipt['dataset']=='演示' and receipt['review']['comparison'] == comparison
    assert civic.plan_stage(plan,landed)=='已核对'
    civic.save_log(dict(start=actual.isoformat(),end=landed.isoformat(),place='长沙',model='轻型',
                        altitude=60,issue='无',note='',photo_note='',plan_id=name))
    assert civic.data['flight_logs'][0]['track_plan_id']==name
    assert civic.data['plans'][0].get('actual_track')
