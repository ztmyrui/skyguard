from datetime import timedelta
from copy import deepcopy

from skyguard.aerial import Forecast, WeatherHour
from skyguard.disposal import apply, feedback, fork_at, movement, observe_point, recommendation, simulated_landing, summarize, walking
from skyguard.flight_route import destination, tracking
from skyguard.flight_rules import FlightQuery
from skyguard.outlook import review
from skyguard.training_tracks import BEGIN, HOME, decode_scenario, example
from skyguard.zone_catalog import zone_from_record


def filing(identifier,start,point,city='changsha'):
    route=tracking(point,90,220,70,start=start,category='轻型',speed=3)
    query=FlightQuery((),'route',20,start,route.end,70,'轻型',demo=False,route=route)
    return dict(id=identifier,state='已报备',snapshot=dict(query=query.record(),title=identifier,city=city))


def test_outlook_separation_hotspot_weather_and_roster():
    now=BEGIN
    start=now+timedelta(hours=1)
    rows=[filing(f'F{i}',start,destination(HOME,0,i)) for i in range(5)]
    zone=zone_from_record(dict(id='z',name='演练商圈',kind='mall',point=list(HOME),radius=155))
    data=Forecast((WeatherHour(now.replace(minute=0), '少云'),),now)
    hints=review(now,rows,[zone],lambda *_:data)
    kinds={h.kind for h in hints}
    assert {'报备','冲突','热点','排班'}<=kinds
    assert any('超出预报范围' in h.reason for h in hints)
    assert next(h for h in hints if h.kind=='排班').reason=='未设置排班'
    assert max(h.level for h in hints if h.level)>=2
    distant=filing('near-by-city',start,HOME,'shanghai')
    assert not any(h.kind=='冲突' for h in review(now,[rows[0],distant],[],lambda *_:None))


def test_disposition_mappings_and_issue_policy():
    assert recommendation(1,False)[0]=='观察'
    assert recommendation(2,True)[0]=='提醒'
    assert recommendation(2,False)[0]=='现场核验'
    assert recommendation(3,True)[0]=='现场核验'
    assert recommendation(4,True)[0]=='紧急协调'
    assert ('dispatch','安排观察岗核验','confirm') in recommendation(3,True)[1]
    assert ('nudge','发本机提醒','auto') in recommendation(2,True)[1]
    rows,first=apply([],'A','watch',0)
    dup,same=apply(rows,'A','watch',2)
    assert (dup,same)==(rows,first)
    rows,_=apply(rows,'A','call',10,result='联系不上')
    assert feedback(rows)['unreachable']


def test_fixed_post_route_and_simulated_landing():
    point,label=observe_point([dict(id='public',lon=HOME[0],lat=HOME[1],name='观察岗')],HOME)
    assert point==HOME and label=='观察岗'
    path=walking(HOME,destination(HOME,90,100))
    assert path['seconds'] in (80,81)
    xy=movement(dict(walk=path,at=10,state='进行中'),50)
    assert HOME[0]<xy[0]<path['finish'][0]
    landing=simulated_landing(dict(lon=HOME[0],lat=HOME[1],height=15,battery=20),(90,2))
    assert landing['seconds']==30 and landing['point'][0]>HOME[0] and landing['radius_m']>=50


def test_replay_branch_preserves_original_and_keeps_prior_actions():
    source=decode_scenario(example('风漂后修正'))
    rows,step=apply([],'T01','dispatch',12)
    step.update(state='已完成',finished_at=18,result='完成')
    rows,_=apply(rows,'T01','close',25,result='合规飞行')
    before=deepcopy(rows)
    branch,forked=fork_at(source,rows,15)
    assert len(forked)==1 and forked[0]['state']=='进行中'
    assert 'finished_at' not in forked[0]
    assert rows==before and summarize(rows)['average']==13
    assert branch['name']!=source['name'] and branch['tracks']==source['tracks']
