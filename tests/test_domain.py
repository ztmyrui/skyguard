import math
import pytest
from skyguard.domain import demo, snapshot, Observation, build_plans, impact_grid, distance


def test_unknown_does_not_become_low_risk():
    s=demo();o=Observation('unknown','未知位置',None,None)
    view=snapshot(s,o)
    assert view['impact'] is None
    assert view['priority']=='优先核验'
    assert view['prediction']==[]


def test_uncertainty_grows_with_age_and_conflict_does_not_discount_impact():
    s=demo();o=s.observations[1]
    first=snapshot(s,o);s.clock=90;second=snapshot(s,o)
    assert second['accuracy_m']>first['accuracy_m']
    assert second['quality']=='信息陈旧'
    o.conflict=True;third=snapshot(s,o)
    assert third['impact']==second['impact']
    assert third['quality']=='存在冲突'


def test_activity_time_changes_relative_impact():
    s=demo();a=s.areas[0]
    active=snapshot(s,s.observations[0])['impact']
    a.active=False
    quiet=snapshot(s,s.observations[0])['impact']
    assert active>quiet
    assert len(impact_grid(s))>0


def test_plans_do_not_overlap_person_and_review_is_last():
    s=demo()
    for p in build_plans(s,'obs-a'):
        assert p['tasks'][-1]['start_s']>=max(t['end_s'] for t in p['tasks'][:-1])
        for i,t in enumerate(p['tasks']):
            for u in p['tasks'][i+1:]:
                if t['person_id']==u['person_id']:
                    assert t['end_s']<=u['start_s'] or u['end_s']<=t['start_s']
    for person in s.people:person.available=False
    assert build_plans(s,'obs-a')==[]


@pytest.mark.parametrize('delta',[{'lat':float('nan')},{'accuracy_m':0},{'vx':101},{'lon':None},{'source':'invented'}])
def test_invalid_observation(delta):
    data={'id':'x','name':'x','lat':28.18,'lon':112.95,**delta}
    with pytest.raises(ValueError):Observation(**data)


def test_fresh_track_advances_without_marking_plan_as_measured():
    s=demo();s.clock=30;v=snapshot(s,s.observations[0])
    assert v['age']==0
    assert distance((v['lat'],v['lon']),(28.1792,112.953))>100
    assert v['source']=='synthetic'
