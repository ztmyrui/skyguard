from dataclasses import replace
from datetime import datetime, timedelta

import httpx
import pytest

from skyguard.aerial import CHINA, WeatherHour
from skyguard.airspace_display import MAX_RECTS, visible_grid
from skyguard.civic import CivicService, default_staff
from skyguard.filing_material import DECLARATION, facts_for, template, verified_material
from skyguard.flight_check import Verdict
from skyguard.flight_log import statistics, validate_record
from skyguard.flight_model import FlightModel, FlightModelConfig
from skyguard.flight_rules import FlightQuery, demo_city
from skyguard.flight_window import hourly_window, recommend, score_hour
from skyguard.infrastructure import SQLiteRepository
from skyguard.official_notices import Notice, Scan
from skyguard.plan_watch import due_cities, update_notice_watch
from skyguard.preflight import UOM, ORDINANCE, checklist, progress

NOW = datetime(2026, 11, 21, 9, tzinfo=CHINA)
QUERY = FlightQuery(((112.956, 28.18),), 'circle', 100, NOW, NOW+timedelta(hours=2), 70, '轻型', demo=False)
SUN = {'rise': (NOW.replace(hour=7), 110), 'set': (NOW.replace(hour=18), 245)}
GOOD = WeatherHour(NOW, wind=2, gust=8, rain_probability=.45, rain_mm=.3, visibility_km=3)


@pytest.mark.parametrize(('changes','expected'), [
    ({}, 'warn'), ({'gust':8.01},'danger'), ({'rain_probability':.451},'danger'),
    ({'rain_mm':.31},'danger'), ({'visibility_km':2.99},'danger'), ({'visibility_km':None},'unknown'),
])
def test_weather_boundaries(changes, expected):
    assert score_hour(NOW, replace(GOOD, **changes), SUN).tone == expected


def test_weather_windows_gold_night_override_and_failure():
    assert score_hour(NOW, replace(GOOD, gust=8.1), SUN, 9).score
    assert score_hour(NOW.replace(hour=6), GOOD, SUN).score == 0
    assert score_hour(NOW.replace(hour=7), GOOD, SUN).golden == '日出金光'
    assert score_hour(NOW.replace(hour=17), GOOD, SUN).golden == '日落金光'
    assert score_hour(NOW, None, SUN).score is None
    inputs = tuple(replace(GOOD, time=NOW+timedelta(hours=i)) for i in (0,1,3))
    rows = hourly_window(inputs, NOW, lambda _: SUN, 9)
    best, = recommend(rows, timedelta(minutes=90), NOW)
    assert best.start == NOW and best.end == NOW+timedelta(hours=2)


def test_grid_viewport_bound_and_cache():
    bounds = (112.85,28.02,113.2,28.42)
    result = visible_grid(bounds, 14)
    assert 0 < len(result) <= MAX_RECTS and visible_grid(bounds, 14) is result
    assert all(w < bounds[2] and e > bounds[0] and s < bounds[3] and n > bounds[1] for w,s,e,n in result)
    assert visible_grid((123.,30.,124.,31.), 14) == ()


def test_preparation_diff_and_known_sources():
    items = checklist('小型', '', 150, True, True, Verdict.PERMIT)
    assert items[0].id == 'application'
    assert {'lights','insurance','license','special'} <= {item.id for item in items if item.required}
    assert {item.url for item in items} <= {'', UOM, ORDINANCE}
    assert progress(items, {'application'})[0] == 1
    assert checklist('轻型','已选',70,False,False,Verdict.CHECK)[0].id == 'uom'


def test_notice_changes_dedup_timeout_and_expiry():
    plan = dict(city='changsha', query=QUERY.record())
    note = Notice('临时公告','https://example.test/notice','原文',((NOW, QUERY.end),))
    valid = Scan(('https://example.test/',),(),(note,),NOW)
    state, alerts = update_notice_watch(plan, valid, NOW)
    assert len(alerts) == 1 and due_cities((plan,), NOW) == {'changsha'}
    plan['watch'] = state
    assert update_notice_watch(plan, valid, NOW)[1] == ()
    assert update_notice_watch(plan, Scan(), NOW) == (state, ())
    assert update_notice_watch(plan, valid, QUERY.end) == (state, ())
    earlier = replace(note, periods=((NOW-timedelta(hours=2),NOW),))
    old_state,_ = update_notice_watch(dict(city='changsha',query=QUERY.record()), replace(valid,notices=(earlier,)), NOW)
    assert update_notice_watch(dict(plan,watch=old_state),valid,NOW)[1]


def test_local_migration_favorites_checklist_and_flight_logs(tmp_path):
    repo = SQLiteRepository(tmp_path/'civic.sqlite3')
    repo.save({'cities':{'changsha':demo_city()},'staff':default_staff(),'announcements':[],'plans':[
        dict(id='p',city='changsha',query=QUERY.record(),title='飞行',cancelled=False)]})
    service = CivicService(repo)
    assert not service.data['favorite_places'] and not service.data['flight_logs']
    assert not service.data['filings'] and not service.data['plans']
    favorite = service.save_favorite('changsha','位置',QUERY.points[0])
    service.save_favorite('changsha','公园',QUERY.points[0],favorite)
    service.update_favorite_checks('changsha',QUERY,'check',NOW)
    from skyguard.flight_route import tracking
    route = tracking(QUERY.points[0], 90, 100, 70, start=NOW, category='轻型')
    pid = service.save_plan('changsha', '飞行', replace(QUERY, route=route), '待核对')
    service.update_plan_details(pid,preflight=['register'])
    fields = dict(start=NOW,end=QUERY.end,altitude=70,place='公园',model='轻型',plan_id=pid,issue='天气')
    identifier = service.save_log(fields)
    service.save_log(dict(fields,note='记录'),identifier)
    reopened = CivicService(repo)
    assert reopened.data['favorite_places'][0]['name'] == '公园'
    assert reopened.data['favorite_places'][0]['last_check']['verdict'] == 'check'
    assert reopened.data['plans'][0]['preflight'] == ['register']
    assert reopened.export_records()['flight_logs'][0]['note'] == '记录'
    assert statistics(reopened.data['flight_logs'], NOW) == dict(month_count=1,seconds=7200,places=[('公园',1)],issue_share=1)
    with pytest.raises(ValueError):
        validate_record(dict(fields,end=NOW))
    with pytest.raises(ValueError):
        validate_record(dict(fields,altitude=0))
    reopened.delete_log(identifier)
    reopened.delete_favorite(favorite)
    final = CivicService(repo)
    assert not final.data['flight_logs'] and not final.data['favorite_places']


def test_material_uses_minimized_facts_mock_transport_and_safe_template():
    facts = facts_for('长沙',QUERY,'需核对','航拍')
    assert '112.956' not in str(facts)
    local = template(facts)
    assert not local.ai and local.text.endswith(DECLARATION)
    response = {'申报要点':'https://invented.test', '风险与应对':'核对现场范围','请求事项':'批复编号 ABC123'}
    client = FlightModel(FlightModelConfig('https://example.test/v1','test','fake'),
                         httpx.MockTransport(lambda r: httpx.Response(200,json={'choices':[
                             {'message':{'content':__import__('json').dumps(response)}}]})))
    report = client.material(facts)
    assert report.ai and report.sections == (local.sections[0], '核对现场范围', local.sections[2])
    assert report.text.endswith(DECLARATION)
    with pytest.raises(ValueError):
        verified_material({'other': 5}, facts)


def test_log_uses_beijing_month_boundary_and_ranks_places():
    from datetime import timezone
    before = datetime(2026,10,31,15,59,tzinfo=timezone.utc)
    boundary = before+timedelta(minutes=1)
    def log(start,place,issue='无'):
        return validate_record(dict(start=start,end=start+timedelta(minutes=10),
                                    altitude=12,place=place,model='微型',issue=issue))
    records = [log(before,'橘子洲'),log(boundary,'梅溪湖','天气'),log(boundary,'梅溪湖'),
               log(boundary,'月湖'),log(boundary,'月湖')]
    result = statistics(records,NOW)
    assert result['month_count']==4 and result['seconds']==3000
    assert [n for _,n in result['places']]==[2,2,1]
    assert result['issue_share']==.2
