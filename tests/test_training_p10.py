from datetime import timedelta
import json

import pytest

from skyguard.aerial import CHINA
from skyguard.attention_level import assess
from skyguard.flight_route import destination, tracking, WGS84
from skyguard.sim_flight import advance, new_flight
from skyguard.track_predict import predict
from skyguard.training_tracks import BEGIN, EXAMPLES, HOME, decode_scenario, example, TrainingFrames
from skyguard.zone_catalog import zone_from_record


def metres(a,b):
    return WGS84.Inverse(a[1],a[0],b[1],b[0])['s12']


def test_follow_limits_and_returns_to_takeoff():
    route=tracking(HOME,90,300,70,start=BEGIN,category='轻型')
    state=new_flight(route)
    far=destination(HOME,90,1200)
    for _ in range(22):
        nxt=advance(state,route,target=(*far,40))
        assert metres(state.position,nxt.position)<=12.01
        state=nxt
    for _ in range(100):
        state=advance(state,route,mode='返航')
        if state.mode=='已降落':
            break
    assert state.height==0 and state.position==HOME
    assert state.mode=='已降落'


def test_lost_flight_drifts_and_descends_deterministically():
    from dataclasses import replace
    route=tracking(HOME,90,200,50,start=BEGIN,category='轻型')
    start=replace(new_flight(route,endurance=2),height=4)
    frame=advance(start,route,mode='失联',wind=(90,3))
    assert frame.lon>start.lon and frame.heading==90 and frame.height==4
    assert advance(frame,route,mode='手动',wind=(90,3)).height==2.5
    assert advance(start,route,mode='失联',wind=(90,3))==frame


def test_five_mps_first_intersection_and_scheduled_mall():
    from skyguard.aerial import local_metres
    project,unproject=local_metres(HOME)
    history=[dict(t=i,lon=unproject(5*(i-15),0)[0],lat=HOME[1]) for i in range(16)]
    x, y=unproject(1000,0)
    a,b=unproject(1100,50)
    _,low=unproject(1100,-50)
    zone=zone_from_record(dict(id='square',name='演练商圈',kind='mall',
        geometry={'type':'Polygon','coordinates':[[[x,low],[a,low],[a,b],[x,b],[x,low]]]}))
    points, entries=predict(history,BEGIN,[zone])
    assert len(points)==300 and entries[0].seconds in (200,201)
    assert zone.rating(BEGIN)=='S1'
    assert zone.rating(BEGIN.replace(hour=22))=='S2'


def test_attention_priority_move_hold_and_explanations():
    low=assess({'rating':'S2'},0)
    raised=assess({'rating':'S2','unfiled':True,'high':True},1,low)
    assert (raised.level, raised.trend)==(3,'↑')
    assert len([d for d in raised.detail if d[0] in ('A11','A13')])==1
    began=assess({},2,raised)
    assert assess({},31,began).level==3
    assert assess({},32,began).level==2
    assert assess({'restricted':True},33).level==4
    assert assess({'verified_on_site':True},33,raised).level==1
    assert assess({},34,raised,override=(4,'人工核验')).detail[-1][0]=='A99'
    for condition in ('entry','high','outside','unfiled'):
        assert assess({'rating':'S2',condition: True},5).level >= low.level


def test_all_scenarios_round_trip_and_repeatable_time_cursor():
    for name in EXAMPLES:
        scenario=decode_scenario(example(name))
        assert scenario['source']=='演练' and scenario['tracks']
        timeline=TrainingFrames(scenario)
        assert timeline.frame(45)==timeline.frame(45)==TrainingFrames(scenario).frame(45)
        if name!='商圈夜景' and name!='合规飞行':
            assert timeline.planned(50)
