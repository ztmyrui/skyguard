from dataclasses import replace
from datetime import datetime, timedelta

from skyguard.aerial import CHINA, Forecast
from skyguard.aerial_plan import LIGHT, SUBJECTS, recommend
from skyguard.flight_check import Verdict
from skyguard.flight_route import FlightRoute, Waypoint
from skyguard.photo_media import media_metadata
from skyguard.photo_mission import TEMPLATES, match_shot, mission_for, plan_frame

NOW = datetime(2026, 11, 22, 18, tzinfo=CHINA)
POS = (112.94, 28.17)
ROUTE = FlightRoute((Waypoint(112.942,28.17,32,10,350), Waypoint(*POS,30,4,10)),
                    3, POS, NOW, '轻型')


def test_each_city_submits_a_real_reference_for_every_preference():
    for city in ['changsha','guangzhou','shanghai','shenzhen','hangzhou']:
        for preference in (*SUBJECTS, *LIGHT):
            plans = recommend(city,(preference,),NOW,NOW+timedelta(days=2),'轻型',None,
                              lambda _: Forecast((),NOW),lambda _:Verdict.CHECK,8,earliest=True)
            assert any(plan.scene.reference_path and any(w.query.route for w in plan.windows)
                       for plan in plans), (city, preference)
            if preference == '日出':
                assert plans[0].windows[0].query.start.date() > NOW.date()


def test_template_shots_and_time_based_matching(tmp_path):
    result = {kind:mission_for(ROUTE,kind,'','') for kind in TEMPLATES}
    assert len({tuple(shot['title'] for shot in record['shots']) for record in result.values()}) == 4
    assert plan_frame(ROUTE,0)['point'] == POS
    assert plan_frame(ROUTE,1000)['point'] == POS
    assert plan_frame(ROUTE,0)['heading'] == 350
    filename = tmp_path/'DJI_20261122_180000.mp4'
    record = media_metadata(filename)
    assert record['time'] == NOW.isoformat()
    assert match_shot(record,ROUTE,result['travel']['shots']) == 'travel-1'
    assert match_shot({'point':[1,1]},ROUTE,result['travel']['shots']) == ''
    old = dict(result['travel'], materials=[{'shot':'travel-1','id':'record1'}], completed=['travel-1'])
    assert mission_for(ROUTE,'promo','','',old)['materials'][0]['shot'] == ''
    assert mission_for(ROUTE,'travel','','',old)['completed'] == ['travel-1']
