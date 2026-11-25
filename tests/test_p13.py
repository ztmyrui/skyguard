from datetime import datetime, timedelta

import httpx
import pytest

from skyguard.aerial import CHINA, Forecast, WeatherHour, touched_hours
from skyguard.aerial_plan import recommend
from skyguard.aoi_lookup import lookup_area
from skyguard.flight_check import Verdict
from skyguard.training_tracks import TrainingFrames, decode_scenario, example


def test_fixed_city_cases_capture_ranking_activity_and_observation_gap():
    def load(name):
        return TrainingFrames(decode_scenario(example(name)))
    changsha=load('长沙 · 湘江风光带')
    assert [row['id'] for row in changsha.frame(0)]==['T01','T02']
    far=changsha.frame(88)
    changsha.frame(16)
    assert changsha.frame(88)==far
    assert [(row['id'],row['attention'].level) for row in far]==[('T02',3),('T01',2)]
    before=load('广州 · 海心沙（平时）').frame(50)[0]
    active=load('广州 · 海心沙（活动）').frame(50)[0]
    assert (before['lon'],before['lat'])==(active['lon'],active['lat'])
    assert (before['region_weight'],active['region_weight'])==(2,3)
    assert (before['attention'].level,active['attention'].level)==(3,4)
    shanghai=load('上海 · 徐汇滨江')
    missing=next(row for row in shanghai.frame(59) if row['id']=='T01')
    resumed=next(row for row in shanghai.frame(60) if row['id']=='T01')
    assert missing['quality']=='观测中断' and not missing['prediction']
    assert resumed['quality']=='正常' and resumed['age']==0


def test_aoi_classification_and_failure_does_not_expose_credential():
    def success(request):
        assert request.url.path=='/v5/aoi/polyline' and request.url.params['id']=='B012345'
        return httpx.Response(200,json=dict(status='0',aois=[dict(name='样例小区',
            type='商务住宅;住宅小区',typecode='120302',polyline='112.95,28.18;112.96,28.18;112.95,28.19')]))
    row=lookup_area('B012345','fake-key',transport=httpx.MockTransport(success))
    assert row.kind=='residential' and len(row.rings[0])==3
    denied=httpx.MockTransport(lambda _:httpx.Response(403))
    with pytest.raises(ValueError,match='高德区域查询暂不可用'):
        lookup_area('B012345','fake-key',transport=denied)


def test_preference_changes_subject_or_time_and_shanghai_reference_exists():
    start=datetime(2026,11,21,12,tzinfo=CHINA)
    stop=start+timedelta(hours=12)
    conditions=Forecast(tuple(WeatherHour(hour,'晴',2,3,.05,0,12,.1,False,((10,2),(80,3)),3)
                              for hour in touched_hours(start,stop)),start)
    def run(city, preferences):
        return recommend(city,preferences,start,stop,'轻型',None,lambda _:conditions,lambda _:Verdict.CHECK,8)
    sunset=run('changsha',('江河','日落'))
    night=run('changsha',('夜景',))
    assert sunset and night
    assert sunset[0].windows[0].light=='日落' and night[0].windows[0].light=='夜景'
    route=sunset[0].windows[0].query.route
    assert route and all(w.camera_deg is not None for w in route.waypoints)
    assert type(route).parse(route.record())==route
    assert any(window.query.route is not None for p in run('shanghai',('夜景',)) for window in p.windows)
