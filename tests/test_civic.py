"""Regressions spanning rule scopes, replay and the actual task workflow."""
from dataclasses import replace
from datetime import timedelta
import json
from math import cos, isfinite, radians
from warnings import catch_warnings, filterwarnings

import pytest
from shapely import get_coordinates
from shapely.geometry import LineString, Point
from shapely.ops import transform as legacy_transform

from skyguard.application import Service
from skyguard.civic import CivicService
from skyguard.flight_rules import DEMO_START, FlightQuery, RuleBook, demo_city
from skyguard.infrastructure import SQLiteRepository
from skyguard.flight_route import FlightRoute, Waypoint
from skyguard.radar import track_view
from skyguard.workflow import Workflow


def query(**fields):
    base = FlightQuery(((112.950,28.180),(112.961,28.180)), 'route', 25,
                       DEMO_START, DEMO_START+timedelta(minutes=15),70,'轻型')
    return replace(base, **fields)


def route_query():
    base = query()
    route = FlightRoute(tuple(Waypoint(*point, 70) for point in base.points), 5,
                        base.points[0], base.start, base.category)
    return replace(base, route=route)


@pytest.mark.parametrize('form, points, radius', [
    ('circle', ((112.953, 28.1792),), 75),
    ('route', ((112.950, 28.180), (112.961, 28.180), (112.963, 28.186)), 25),
])
def test_buffer_coordinates_match_the_shapely_ops_transform(form, points, radius):
    x0, y0 = points[0]
    east = 111195 * cos(radians(y0))
    meters = [((x - x0) * east, (y - y0) * 111195) for x, y in points]
    target = Point(meters[0]) if form == 'circle' else LineString(meters)
    buffer = target.buffer(radius, quad_segs=12)
    with catch_warnings():
        filterwarnings('ignore', message=r"The 'shapely\.ops\.transform\(\)' function is deprecated",
                       category=DeprecationWarning)
        legacy = legacy_transform(lambda x, y: (x0 + x / east, y0 + y / 111195), buffer)

    actual = query(form=form, points=points, radius_m=radius).geometry()
    assert actual.geom_type == legacy.geom_type
    assert get_coordinates(actual) == pytest.approx(get_coordinates(legacy), rel=0, abs=1e-9)


def test_route_crosses_restriction_and_back_to_back_boundary_is_free():
    rules=RuleBook(demo_city()['rules'])
    result=rules.evaluate(query())
    assert [r.id for r in result['restrictions']]==['demo-restriction']
    start,end=result['alternatives'][0]
    assert start==DEMO_START+timedelta(minutes=20)
    assert not rules.evaluate(query(start=start,end=end))['restrictions']


def test_uncovered_real_query_and_unknown_aircraft_do_not_get_a_clear_result():
    rules=RuleBook(demo_city()['rules'])
    assert rules.evaluate(query(demo=False))['status']=='官方空域待核对'
    point=((112.948,28.173),)
    assert rules.evaluate(query(points=point,form='circle',category='未知'))['status']=='演练资料不足'
    assert rules.evaluate(query(points=point,form='circle',category='大型'))['status']=='涉及限制或需核对申请'


def test_partial_spatial_and_time_coverage_are_not_treated_as_clear():
    rules=RuleBook(demo_city()['rules'])
    assert rules.evaluate(query(points=((112.977,28.192),),form='circle'))['status']=='演练资料不足'
    late=DEMO_START+timedelta(minutes=85)
    assert rules.evaluate(query(start=late,end=late+timedelta(minutes=10)))['status']=='演练资料不足'


def test_second_city_rule_pack_is_independent_and_plans_persist(tmp_path):
    service=CivicService(SQLiteRepository(tmp_path/'civic.db'))
    first=service.book('changsha')
    service.import_city(json.dumps({'id':'test-city','name':'城市隔离测试','center':[120,30],
                                    'sources':[],'rules':[],'posts':[],'walk':[]}))
    assert service.book('test-city').evaluate(query())['status']=='演练资料不足'
    assert first is service.book('changsha')
    identifier=service.save_plan('changsha','测试航拍',route_query(),'涉及限制')
    recovered=CivicService(SQLiteRepository(tmp_path/'civic.db'))
    assert recovered.data['plans'][0]['id']==identifier
    recovered.cancel_plan(identifier)
    with pytest.raises(ValueError):
        recovered.save_plan('changsha','已撤销计划',route_query(),'涉及限制',identifier)


def test_plan_verdict_and_headline_persist_on_update(tmp_path):
    path = tmp_path / 'civic.db'
    service = CivicService(SQLiteRepository(path))
    identifier = service.save_plan('changsha', '新计划', route_query(), '禁止或受限', verdict='restricted')
    service.save_plan('changsha', '调整后的计划', route_query(), '需核对后再飞行', identifier, verdict='check')

    recovered = CivicService(SQLiteRepository(path))
    plan, = recovered.data['plans']
    assert (plan['id'], plan['title']) == (identifier, '调整后的计划')
    assert (plan['verdict'], plan['result']) == ('check', '需核对后再飞行')
    assert plan['saved_at']


def test_legacy_plan_without_verdict_or_saved_time_can_be_loaded(tmp_path):
    repo = SQLiteRepository(tmp_path / 'civic.db')
    service = CivicService(repo)
    legacy = dict(id='legacy', title='旧计划', city='changsha',
                  query=query().record(), result='旧状态', cancelled=False)
    service.data['plans'].append(legacy)
    repo.save(service.data)

    recovered = CivicService(repo)
    plan, = recovered.data['plans']
    assert plan['query']['form'] == 'route' and plan['result'] == '旧状态'
    assert 'verdict' not in plan and 'saved_at' not in plan


def test_city_defaults_merge_with_an_existing_catalog(tmp_path):
    repo=SQLiteRepository(tmp_path/'civic.db')
    service=CivicService(repo)
    imported={'id':'shenzhen','name':'深圳','center':[114.07,22.55],
              'rules':[],'sources':[],'posts':[],'walk':[]}
    repo.save(dict(service.data,cities={'changsha':demo_city(),'shenzhen':imported}))
    restored=CivicService(repo)
    assert set(restored.cities())=={'changsha','shenzhen','hangzhou','shanghai','guangzhou'}
    assert restored.cities()['shenzhen']==imported
    assert restored.book('guangzhou').evaluate(query(demo=False))['status']=='官方空域待核对'


def test_city_malformed_rule_and_staff_csv_leave_old_snapshot_intact(tmp_path):
    service=CivicService(SQLiteRepository(tmp_path/'civic.db'))
    previous=service.data
    invalid=demo_city()
    invalid['rules'][0]['start']='2026-11-19T15:20:00'
    with pytest.raises(ValueError):
        service.import_city(json.dumps(invalid))
    with pytest.raises(ValueError):
        service.import_staff('id,name,role,start_s,end_s,post_id,available\nA,演练人,现场核验,20,10,east,true')
    assert service.data==previous


def test_replay_handles_radar_silence_and_reacquisition():
    first=track_view('obs-a',119)
    assert first['prediction'] and all(isfinite(p['radius_m']) for p in first['prediction'])
    assert track_view('obs-a',135)['track_state']=='跟踪中断'
    assert not track_view('obs-a',135)['prediction']
    assert not track_view('obs-a',156)['prediction']
    assert track_view('obs-a',161)['prediction']
    assert track_view('obs-a',119)==first


def test_public_report_requires_review_and_retains_both_evidence_records(tmp_path):
    civic=CivicService(SQLiteRepository(tmp_path/'civic.db'))
    svc=Service(SQLiteRepository(tmp_path/'business.db'),Workflow(tmp_path/'workflow.db'),civic)
    track=track_view('obs-a',0)
    report=svc.add_observation({'name':'测试目击','lat':track['lat'],'lon':track['lon'],'accuracy_m':25})
    view=next(v for v in svc.views() if v['id']==report)
    assert view['track_state']=='仅目击待核实' and view['prediction']==[] and view['speed'] is None
    assert view['evidence_candidates'][0]['id']=='obs-a'
    svc.associate(report,'obs-a')
    assert report in next(v for v in svc.views() if v['id']=='obs-a')['linked_reports']
    assert any(o.id==report for o in svc.scene.observations)


def test_field_plan_checks_shift_and_observation_deadline_at_confirmation(tmp_path):
    civic=CivicService(SQLiteRepository(tmp_path/'civic.db'))
    svc=Service(SQLiteRepository(tmp_path/'business.db'),Workflow(tmp_path/'workflow.db'),civic)
    plan=svc.generate('obs-a','现场观察')['plans'][0]
    task = next(t for t in plan['tasks'] if t['key'] == 'observe')
    assert task['post_id'] == 'east'
    assert task['end_s'] - task['start_s'] == 180
    assert any('预测仅覆盖' in reason for reason in plan['unresolved'])
    svc.tick(task['start_s']+1)
    with pytest.raises(ValueError,match='观察窗口'):
        svc.confirm(0)
    assert not svc.scene.tasks
    civic.save_staff([dict(p,available=False) for p in civic.data['staff']])
    svc.sync_staff()
    assert not svc.generate('obs-a','现场观察')['plans']
