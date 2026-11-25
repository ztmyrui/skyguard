"""Civilian route timing, recommendation, and filing compatibility tests."""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from skyguard.aerial import Forecast, WeatherHour, touched_hours
from skyguard.aerial_plan import direction_score, hard_filter, local_topics, model_topics, recommend, Scene
from skyguard.civic import CivicService
from skyguard.filing import create_filing, decode_package, encode_package, match_filings, match_route
from skyguard.flight_check import CHINA, Verdict
from skyguard.flight_route import (
    FlightRoute, Waypoint, destination, orbit, planned_position, rise, route_duration, tracking,
)
from skyguard.flight_rules import DEMO_START, FlightQuery, RuleBook, demo_city
from skyguard.infrastructure import SQLiteRepository


POINT = (112.949, 28.181)
OPTIONS = dict(start=DEMO_START, category='轻型', speed=5)


def query_for(route, **options):
    return FlightQuery((), 'route', 20, route.start, route.end, 0, route.category, **options, route=route)


def test_hover_travel_climb_and_half_open_window():
    end = destination(POINT, 90, 100)
    route = FlightRoute((Waypoint(*POINT, 30, 20), Waypoint(*end, 30, 10), Waypoint(*end, 80)),
                        5, POINT, DEMO_START, '轻型')
    assert route_duration(route) == pytest.approx(60, abs=.000001)
    assert planned_position(route, route.start - timedelta(seconds=1)) is None
    assert planned_position(route, route.start + timedelta(seconds=19)).xy == POINT
    assert planned_position(route, route.start + timedelta(seconds=30)).xy == pytest.approx(
        destination(POINT, 90, 50), abs=.00000001)
    assert planned_position(route, route.start + timedelta(seconds=55)).altitude_m == pytest.approx(55, abs=.00001)
    assert planned_position(route, route.end) is None
    assert FlightRoute.parse(route.record()) == route


def test_templates_and_route_query_store_geometry():
    around = orbit(POINT, 125, 80, 2, **OPTIONS)
    assert len(around.waypoints) == 25
    assert around.waypoints[0].xy == around.waypoints[-1].xy
    assert 1500 < route_duration(around)*5 < 1600
    straight = tracking(POINT, 90, 300, 70, **OPTIONS)
    assert route_duration(straight) == pytest.approx(60)
    upward = rise(POINT, 25, 75, **OPTIONS)
    assert route_duration(upward) == 10
    query = query_for(upward, demo=False)
    assert query.altitude_agl == 75 and query.geometry().area > 0
    assert FlightQuery(**query.record()).record() == query.record()
    assert replace(query, start=query.start + timedelta(hours=1)).route.start > upward.start
    with pytest.raises(ValueError, match='速度'):
        replace(upward, speed_mps=13)
    with pytest.raises(ValueError, match='60'):
        replace(around, waypoints=around.waypoints*3)


def test_rule_uses_space_time_and_altitude_together():
    book = RuleBook(demo_city()['rules'])
    in_box = (112.954, 28.18)
    high = FlightRoute((Waypoint(*in_box, 130, 10),), 5, in_box, DEMO_START, '轻型')
    low = replace(high, waypoints=(Waypoint(*in_box, 70, 10),))
    assert not book.evaluate(query_for(high))['restrictions']
    assert book.evaluate(query_for(low))['restrictions']
    delayed = replace(low, start=DEMO_START + timedelta(minutes=20))
    assert not book.evaluate(query_for(delayed))['restrictions']


def test_filing_deviation_and_unknown_height_for_a_measured_civilian_flight():
    route = tracking(POINT, 90, 300, 70, **OPTIONS)
    plan = dict(id='p9', city='changsha', title='滨江航拍', query=query_for(route).record(), result='待核对')
    record = create_filing(plan, route.start)
    assert decode_package(encode_package(record)) == record
    target = destination(destination(POINT, 90, 100), 0, 12)
    observation = dict(lon=target[0], lat=target[1], altitude_agl=85, accuracy_m=2)
    actual_time = route.start + timedelta(seconds=27)
    match = match_route(observation, (record,), actual_time)
    detail, = match.deviations
    assert (detail.horizontal_m, detail.altitude_m, detail.delay_s) == pytest.approx((12, 15, 7), abs=.1)
    assert match.title == '疑似对应'
    assert match == match_route(observation, (record,), actual_time)
    assert match_route(dict(observation, altitude_agl=None), (record,), actual_time).title == '无法判断'
    assert match_route(dict(observation, altitude_agl=120), (record,), actual_time).title == '偏离'
    assert match_route(observation, (record,), actual_time+timedelta(hours=1)).title == '无对应报备'


def test_legacy_ranges_are_removed_and_logs_are_detached(tmp_path):
    repo = SQLiteRepository(tmp_path/'civic.sqlite3')
    service = CivicService(repo)
    old_query = FlightQuery((POINT,), 'circle', 100, DEMO_START,
                            DEMO_START+timedelta(minutes=5), 70, '轻型')
    old = dict(id='p8', city='changsha', title='旧版圆形', query=old_query.record(),
               result='待核对', cancelled=False)
    record = create_filing(old, DEMO_START)
    service.data['plans'].append(old)
    service.data['filings'].append(record)
    repo.save(service.data)
    service.data['flight_logs'].append({'id':'existing-log','plan_id':old['id']})
    repo.save(service.data)
    current = CivicService(repo)
    assert current.data['plans'] == current.data['filings'] == []
    assert current.data['flight_logs'][0]['plan_id'] is None
    assert CivicService(repo).data == current.data
    with pytest.raises(ValueError, match='航线'):
        current.import_filing(encode_package(record))


def test_daily_plan_progress_and_current_snapshot(tmp_path):
    client = CivicService(SQLiteRepository(tmp_path/'civic.sqlite3'))
    start = DEMO_START + timedelta(hours=1)
    plan_route = tracking(POINT, 270, 40, 70, **dict(OPTIONS, start=start))
    query = query_for(plan_route)
    identifier = client.save_plan('changsha', '航线计划', query, '待核对')
    def stage(now):
        return client.plan_stage(client.data['plans'][0], now)
    assert stage(start) == '计划中'
    record = client.public_filing(identifier)
    assert stage(start - timedelta(days=1)) == '已报备'
    assert stage(start) == '当天'
    client.transition_plan(identifier, 'start', start)
    assert stage(start) == '飞行中'
    client.transition_plan(identifier, 'end', start + timedelta(seconds=2))
    assert stage(start) == '已结束'
    observation = dict(id='civil-observation', lon=POINT[0], lat=POINT[1], altitude_agl=70, accuracy_m=3)
    client.change_filing(record['id'], 'match', event_view=observation, clock=start)
    client.change_filing(record['id'], 'review', outcome='飞手说明', at=start)
    assert stage(start) == '已核对'


def test_recommendation_filters_restrictions_and_overflight_and_is_deterministic():
    start = datetime(2026, 11, 21, 10, tzinfo=CHINA)
    stop = start + timedelta(days=1)
    forecast = Forecast(tuple(WeatherHour(t,'晴',2,3,.05,0,14,.15,False,((10,2),(80,3),(120,3)),3)
                              for t in touched_hours(start,stop)), start)
    topics = local_topics('湘江日落')
    assert topics == ('江河','日出日落')
    assert model_topics({'tags':['江河','任意地点', {'lon':1}]}) == ('江河',)
    fast = tracking(POINT, 90, 700, 70, **OPTIONS)
    assert not hard_filter(fast, ())
    scene = Scene('场馆测试', destination(POINT, 90, 95), ('公园绿地',), 0, (), 150, 20)
    assert not hard_filter(tracking(POINT,90,150,70,**OPTIONS), (scene,))
    assert not hard_filter(tracking(POINT,90,150,121,**OPTIONS), ())
    def blocked(_):
        return Verdict.RESTRICTED
    filtered = recommend('changsha',topics,start,stop,'轻型',POINT,lambda _:forecast,blocked,8)
    assert all(not w.query.route for p in filtered for w in p.windows)
    args = ('changsha',topics,start,stop,'轻型',POINT,lambda _:forecast,lambda _:Verdict.CHECK,8)
    proposals = recommend(*args)
    assert 3 <= len(proposals) <= 5
    assert len({p.scene.name for p in proposals}) == len(proposals)
    assert any(w.query.route for p in proposals for w in p.windows)
    assert all(0 <= w.score <= 100 if w.query.route else w.score is None for p in proposals for w in p.windows)
    assert proposals == recommend(*args)
    evening = start.replace(hour=17)
    assert direction_score(POINT,350,evening,True) > direction_score(POINT,350,evening,False)


def test_recommendation_keeps_distinct_light_options_and_names_weather_hazards():
    from skyguard.aerial_plan import capture_preferences, load_scenes, time_windows
    start = datetime(2026, 11, 21, 10, tzinfo=CHINA)
    stop = start + timedelta(hours=12)
    scene = next(s for s in load_scenes('changsha') if s.name == '橘子洲')
    route = tracking(scene.point, 90, 100, 70, start=start, category='轻型')
    assert capture_preferences('不要日出，只想拍湘江日落和夜景') == (('江河',), ('日落','夜景'))
    bad = tuple(WeatherHour(t,'阵雨',2,3,.35,1,12,.1,True,((10,2),(80,3)),3)
                for t in touched_hours(start, stop))
    forecast = Forecast(bad, start)
    windows = time_windows(route, scene, ('江河','日落','夜景'), start, stop,
                           forecast, lambda _: Verdict.CHECK, None)
    assert {w.light for w in windows} == {'日落','夜景'}
    assert all(w.unsuitable and w.score is None and '雷暴' in w.status for w in windows)
    assert all('阵风' in w.weather_summary for w in windows)


def test_cancelled_filing_can_be_replaced_and_its_package_round_trips(tmp_path):
    service = CivicService(SQLiteRepository(tmp_path/'filings.sqlite3'))
    route = tracking(POINT, 270, 70, 70, **OPTIONS)
    plan_id = service.save_plan('changsha', '南岸航线', query_for(route), '待核对')
    first = service.public_filing(plan_id)
    service.change_filing(first['id'], 'cancel')
    second = service.public_filing(plan_id)
    assert second['id'] != first['id'] and second['state'] == '已报备'
    assert service.filing(first['id'])['state'] == '已取消'
    assert service.public_filing(plan_id)['id'] == second['id']
    assert decode_package(encode_package(second)) == second


def test_expired_formal_route_cannot_start(tmp_path):
    service = CivicService(SQLiteRepository(tmp_path/'expired.sqlite3'))
    route = tracking(POINT, 90, 120, 70, **OPTIONS)
    identifier = service.save_plan('guangzhou', '旧时间计划', query_for(route, demo=False), '待核对')
    service.public_filing(identifier)
    plan, = service.data['plans']
    assert service.plan_stage(plan, route.end) == '已过期'
    with pytest.raises(ValueError):
        service.transition_plan(identifier, 'start', route.end)
