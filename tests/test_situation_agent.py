
from datetime import timedelta
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from skyguard.civic import CivicService
from skyguard.flight_check import build_flight_check
from skyguard.flight_route import destination, tracking
from skyguard.flight_rules import FlightQuery
from skyguard.infrastructure import SQLiteRepository
from skyguard.situation_agent import analyse_target, detect_formations, level_rows, score_rows
from skyguard.training_city_cases import EXTENDED_CASES
from skyguard.training_tracks import FLAGSHIPS, TrainingFrames, decode_scenario, example
from skyguard.ui.widgets.workflow_session import WorkflowSession


@pytest.fixture
def exercise(tmp_path):
    app = QApplication.instance() or QApplication([])
    civic = CivicService(SQLiteRepository(tmp_path/'civic.sqlite3'))
    context = QObject()
    context.data_root,context.civic,context.clock = tmp_path,civic,SimpleNamespace(demo=False)
    session = WorkflowSession(context)
    session.clock.stop()
    session.load_example(next(iter(FLAGSHIPS)))
    yield session
    app.processEvents()


@pytest.mark.parametrize('name, spec', (FLAGSHIPS | EXTENDED_CASES).items())
def test_flagship_replay_is_deterministic(name,spec):
    scene=decode_scenario(example(name))
    assert scene['flagship'] and len(scene['tracks'])==spec[3]
    assert max(t['points'][-1]['t'] for t in scene['tracks'])==spec[2]
    assert len(scene['posts'])==4
    engine=TrainingFrames(scene)
    end=engine.frame(95)
    assert engine.frame(43)==TrainingFrames(scene).frame(43)
    assert end==engine.frame(95)


def test_formations_have_buffered_hulls(exercise):
    frame=exercise.frames.frame(45)
    zones=exercise.frames.zones_at(45)
    groups=detect_formations(frame,zones)
    pair=next(group for group in groups if {'T04','T05'}<=set(group['members']))
    assert pair['coordinated']
    assert pair['id']=='G01' and pair['members']==['T04','T05']
    assert all('T01' not in group['members'] for group in groups)
    assert all(next(o for o in exercise.frames.frame(t) if o['id']=='T01')['attention'].level==1
               for t in range(121))
    assert pair['hull'][0]==pair['hull'][-1] and len(pair['hull'])>6
    assert groups==detect_formations(tuple(reversed(frame)),zones)


def test_event_stream_covers_recorded_transitions(exercise):
    _, events=exercise.agent_snapshot(127)
    codes={e['code'] for e in events}
    assert {'进入:core','speed_up','orbit','gap','lock','outside','穿越:road'}<=codes
    assert {'up','formation'}<={e['kind'] for e in events}
    assert all(0<=r['at']<=127 and len(r['point'])==2 for r in events)
    assert exercise.agent_snapshot(40)[1]==[e for e in events if e['at']<=40]


def test_explanation_matches_assessment_inputs(exercise):
    obj=next(o for o in exercise.frames.frame(55) if o['id']=='T04')
    rows=level_rows(obj)
    assert rows[0][2]=='A00' and any(r[2]=='A11' for r in rows)
    scores=score_rows(obj)
    assert [row['weight'] for row in scores]==[35,25,25,15]
    strength=sum(r['contribution'] for r in scores)
    assert obj['attention'].score==pytest.approx((obj['attention'].level-1)*25+.25*strength,abs=.03)


def test_cpa_and_signed_zone_boundaries(exercise):
    exercise.now=37
    exercise.seek(37)
    obj=next(o for o in exercise.objects() if o['id']=='T04')
    info=analyse_target(obj,exercise.frames.zones_at(37))
    assert info['cpa']['entering'] and 0<info['cpa']['seconds']<=180
    assert len(info['zone_distances'])==4
    assert all(z['metres']<=0 if z['inside'] else z['metres']>=0 for z in info['zone_distances'])
    assert '安全回收区' in info['recovery_zone']['name']


def test_plan_comparison_and_idempotent_adoption(exercise):
    exercise.now=exercise.cursor=44
    exercise.selected='T04'
    _,plans=exercise.agent_plans(exercise.selected_object())
    assert [p['code'] for p in plans]==list('ABC')
    assert plans[0]['eta_s']==2 and not plans[0]['staff_needed']
    assert plans[1]['assignment']['place'] and plans[2]['recovery_zone']
    assert exercise.adopt_agent_plan('C')
    assert not exercise.adopt_agent_plan('C')
    assert len(exercise.agent_log)==1
    assert {'nudge','dispatch','notify','recover','handoff'}<={r['kind'] for r in exercise.actions}


def test_autopilot_advances_each_second_at_eight_times_speed(exercise):
    assert exercise.autopilot_mode=='auto'
    exercise.clock_running=True
    exercise.play_speed=8
    for _ in range(8):
        exercise.tick()
    assert exercise.now==64 and exercise.agent_log
    assert {'nudge','dispatch'}<={r['kind'] for r in exercise.actions}
    walks=[r for r in exercise.actions if r['kind']=='dispatch' and r['target']=='T04']
    assert walks and walks[0]['walk']['seconds']>=10 and walks[0]['walk']['metres']>100
    walk=walks[0]
    assert walk['finished_at']==walk['at']+walk['walk']['seconds']
    assert any(r['title']=='观察岗核验完成' and r['target']=='T04' and r['at']==walk['finished_at']
               for r in exercise.agent_log)
    assert any(r['at']%8 for r in exercise.agent_log)
    exercise.seek(47)
    assert exercise.map_layers()['agent_links'], [(r['target'],r['at'],r['kind'],r['state'],r.get('finished_at'))
                                                   for r in exercise.actions]


def test_assist_and_observe_do_not_dispatch(exercise):
    for mode in ('observe','assist'):
        exercise.autopilot_mode=mode
        exercise.now=exercise.cursor=50
        exercise.agent_step()
        assert not any(r['kind']=='dispatch' for r in exercise.actions)


def test_shanghai_reassigns_the_only_busy_observer(exercise):
    exercise.load_example(next(name for name in FLAGSHIPS if name.startswith('上海')))
    assert exercise.scenario['staff_limit']==1
    exercise.clock_running=True
    exercise.play_speed=8
    for _ in range(9):
        exercise.tick()
    reassigned=[row for row in exercise.actions if row['kind']=='dispatch' and row['state']=='已取消']
    assert reassigned, [(row['target'],row['at'],row['kind']) for row in exercise.actions
                        if row['kind'] in ('dispatch','notify')]
    assert any('改派' in row['result'] for row in exercise.agent_log)
    from skyguard.disposal import movement
    original=next(r for r in reassigned if r['target']=='T03')
    reassignment=next(r for r in exercise.actions if r['target']=='T04' and r['kind']=='dispatch')
    assert (original['at'],original['finished_at'],reassignment['at'])==(18,26,26)
    original_in_progress=dict(original,state='进行中')
    assert reassignment['walk']['start']==list(movement(original_in_progress,26))
    rows=[next(o for o in exercise.frames.frame(t) if o['id']=='T02') for t in range(27,51)]
    assert [r['age'] for r in rows]==[*range(23),0]
    assert {r['attention'].level for r in rows}=={3}
    events=exercise.agent_snapshot(50)[1]
    assert any(r['at']==28 and r['code']=='gap' and r['target']=='T02' for r in events)
    assert any(r['at']==50 and r['code']=='lock' and '22 秒' in r['detail'] for r in events)


def test_offline_queries_use_current_frame(exercise):
    exercise.now=37
    exercise.seek(37)
    assert 'T04' in exercise.ask_agent('T04 预计何时越界？')
    assert '预计 +' in exercise.ask_agent('T04 预计何时越界？')
    assert '协同' in exercise.ask_agent('编队与异常？')
    assert '空闲观察员' in exercise.ask_agent('区域分布统计？')
    assert '未观测' in exercise.ask_agent('T99 在哪？')


def test_guangzhou_clock_school_height_and_recovery(exercise):
    exercise.load_example(next(name for name in FLAGSHIPS if name.startswith('广州')))
    frames=exercise.frames
    assert frames.origin.strftime('%H:%M:%S')=='18:59:50'
    assert 'core' not in {z.id for z,_ in frames.zones_at(9)}
    assert 'core' in {z.id for z,g in frames.zones_at(10) if g=='S1'}
    for second in range(121):
        exercise.now=exercise.cursor=second
        exercise.agent_step()
    _,events=exercise.agent_snapshot(120)
    diversion=[next(o for o in frames.frame(t) if o['id']=='T02')['attention'].level for t in range(121)]
    assert diversion[9:11]==[1,3] and max(diversion)==3 and diversion[45]==1
    codes={(r['target'],r['code']) for r in events}
    assert {('T02','进入:core'),('T03','high'),('T04','进入:school'),('T04','orbit')}<=codes
    assert any(r['target']=='T05' and r['kind']=='recover' and r['at']<87 for r in exercise.actions)
    assert any(r['target']=='T05' and r['title']=='模拟目标已降落' for r in exercise.agent_log)


def test_changsha_correction_and_recovery_complete(exercise):
    for second in range(121):
        exercise.now=exercise.cursor=second
        exercise.agent_step()
    by_target={(r['target'],r['title']):r['at'] for r in exercise.agent_log}
    assert by_target['T02','飞手已回到航线'] in range(25,31)
    assert by_target['T04','观察岗核验完成'] < by_target['T04','驶离敏感区']
    assert by_target['T06','模拟目标已降落']==105
    assert any(r['target']=='T06' and r['kind']=='recover' and r['at']<72 for r in exercise.actions)


def test_city_copilot_buttons_approve_dispatch_and_redirect(exercise, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QPushButton
    from skyguard.ui.shell import MainWindow

    window = MainWindow(tmp_path / 'desktop')
    try:
        window.open_workspace('situation')
        panel = window.situation.training
        panel.scenes.setCurrentIndex(2)
        panel.modes.buttons()[1].click()
        session = panel.session
        session.seek_civic(18)

        def click(text):
            next(b for b in panel.focus_cards.findChildren(QPushButton)
                 if not b.isHidden() and b.text() == text).click()

        assert session.civic_replay.frame(18)['pending'] == {'T03': False}
        click('同意执行')
        session.seek_civic(26)
        assert session.civic_replay.frame(26)['pending'] == {'T04': True}
        before = session.map_layers()['staff_markers'][0]
        click('同意改派')
        after = session.map_layers()['staff_markers'][0]
        assert after['status'] == '已改派'
        assert (after['lon'], after['lat']) == (before['lon'], before['lat'])
        assert panel.session.civic_replay.frame(26)['pending'] == {}
        assert any(panel.stream.item(i).data(Qt.UserRole)['tag'] == '动态改派' for i in range(panel.stream.count()))
        panel.scenes.setCurrentIndex(1)
        assert session.solar_time().strftime('%H:%M:%S') == '18:59:50'
        assert not session.map_layers()['active_highlights']
    finally:
        window.close()


def test_civic_places_and_closed_loops(exercise):
    from shapely.geometry import LineString, Point, box

    from skyguard.civic_replay import AIR, metres
    from skyguard.training_city_cases import CIVIC_CASES, CIVIC_SITES

    # Georeferenced windows around the Orange Isle grounds, Ersha/Hai Xin Sha,
    # and the Puxi bank. Exact terrain evidence is checked in the imagery review.
    districts = ((112.95, 28.168, 112.967, 28.19), (113.311, 23.106, 113.336, 23.121),
                 (121.456, 31.165, 121.464, 31.171))
    # Interior samples digitized on the WGS84 imagery review. These are narrow
    # acceptance windows for the selected segments, independent of map bounds.
    lawns = ((112.95578, 28.18702, 112.95602, 28.18722),
             (113.31336, 23.11270, 113.31370, 23.11304),
             (121.46031, 31.16648, 121.46055, 31.16670))
    paths = ((112.95475, 28.17008, 112.95486, 28.17040),
             (113.33294, 23.11754, 113.33303, 23.11788),
             (121.45820, 31.16733, 121.45829, 31.16790))
    for (name, city), (west, south, east, north), lawn_area, path_area in zip(
            CIVIC_CASES.items(), districts, lawns, paths):
        exercise.autopilot_mode = 'auto'
        exercise.load_example(name)
        config = CIVIC_SITES[city]
        assert box(*lawn_area).contains(Point(config['sites']['lawn'][1]))
        dispatched = {i.post for i in exercise.civic_replay.incidents if i.post}
        assert all(box(*path_area).contains(LineString(config['walk'][post])) for post in dispatched)
        assert all(west < lon < east and south < lat < north for lon, lat in
                   [exercise.scenario['station'], *AIR[city], *(p for path in config['walk'].values() for p in path),
                    *(point for _, point, _ in config['sites'].values())])
        assert exercise.playback_end == 120
        assert not exercise.map_layers()['zones'] and not exercise.map_layers()['active_highlights']
        assert exercise.scenario['staff_limit'] == 1
        replay = exercise.civic_replay
        lawn = next(i for i in replay.incidents if i.site == 'lawn')
        walk_event = next(i for i in replay.incidents if i.post)
        route_at = replay.frame(walk_event.start)['walk']
        assert route_at.seconds >= 12 and route_at.points == config['walk'][walk_event.post]
        later = replay.layers(walk_event.start + 3)['staff_markers'][0]
        assert 0 < metres(config['walk'][walk_event.post][0], (later['lon'], later['lat'])) < 6
        assert tuple(later['route'][-1]) == config['walk'][walk_event.post][-1]
        assert any(a['recovery'] for a in replay.layers(lawn.start)['active_highlights'])
        assert not any(a['recovery'] for a in replay.layers(lawn.end)['active_highlights'])
        landed = next(r for r in replay.layers(lawn.end)['aircraft'] if r['id'] == lawn.target)
        assert landed['state'] == '已降落'
        assert (landed['lon'], landed['lat']) == config['sites']['lawn'][1]


def test_civic_market_turn_shadow_and_reminder_windows(exercise):
    from skyguard.training_city_cases import CIVIC_CASES

    exercise.autopilot_mode = 'auto'
    exercise.load_example(tuple(CIVIC_CASES)[2])
    replay = exercise.civic_replay
    south = replay.frame(25)['walk']
    north = replay.frame(26)['walk']
    assert north.points[0] == south.position(26)[0]
    assert north.points[-1][1] > replay.station[1] > south.points[-1][1]
    assert replay.layers(33)['staff_markers'][0]['lat'] > replay.layers(26)['staff_markers'][0]['lat']
    assert not any(a['name'] == '展馆市集区' for a in replay.layers(85)['active_highlights'])
    assert any(a['name'] == '展馆市集区' for a in replay.layers(26)['active_highlights'])
    assert any(link['label'] == '已发提醒' for link in replay.layers(29)['civic_links'])
    assert not any(link['label'] == '已发提醒' for link in replay.layers(30)['civic_links'])
    shadow = [next(row for row in replay.layers(t)['aircraft'] if row['id'] == 'T02') for t in range(27, 51)]
    assert [row['age'] for row in shadow] == [0, *range(1, 23), 0]
    assert len({(row['lon'], row['lat']) for row in shadow[:-1]}) == 1


def save_public(exercise, anchor, seconds=0):
    start=exercise.frames.origin+timedelta(seconds=seconds)
    route=tracking(anchor,90,350,60,start=start,category='轻型',speed=5)
    query=FlightQuery((),'route',20,start,route.end,60,'轻型',demo=False,route=route)
    identifier=exercise.context.civic.save_plan('changsha','测试公众航拍',query,'待核对')
    return identifier,query


def test_public_deviation_reminder_and_return_are_ephemeral(exercise):
    station=exercise.scenario['station']
    identifier,_=save_public(exercise,destination(station,180,360))
    exercise.set_public_overlay(True)
    assert next(o for o in exercise.objects() if o['id']=='PUB-01')['attention'].level==1
    exercise.now=exercise.cursor=10
    exercise.inject_public_deviation()
    exercise.clock_running=True
    exercise.play_speed=8
    for _ in range(10):
        exercise.tick()
    _,events=exercise.agent_snapshot()
    assert any(e['target']=='PUB-01' and e['code']=='outside' for e in events)
    assert any(e['target']=='PUB-01' and e['kind']=='up' for e in events)
    assert exercise.pilot_messages(identifier)
    assert any(r['target']=='PUB-01' and r['title']=='飞手已回到航线' for r in exercise.agent_log)
    exercise.set_public_overlay(False)
    assert not exercise.pilot_messages(identifier)
    assert 'PUB-01' not in exercise.frames.tracks


def test_synced_activity_is_separate_and_can_be_removed(exercise,monkeypatch):
    center=next(tuple(z.geometry.centroid.coords[0]) for z in exercise.frames.zones if z.id=='core')
    _,query=save_public(exercise,center,30)
    civic=exercise.context.civic
    assert not civic.book('changsha').evaluate(query)['exercise']
    exercise.now=exercise.cursor=35
    monkeypatch.setattr('skyguard.ui.widgets.workflow_session.system_now',
                        lambda *_:exercise.frames.origin+timedelta(seconds=35))
    exercise.set_public_sync(True)
    rule_result=civic.book('changsha').evaluate(query)
    assert rule_result['exercise'] and not rule_result['restrictions']
    assert any(i.key=='exercise' for i in build_flight_check(query,rule_result,city_id='changsha').items)
    exercise.set_public_sync(False)
    assert not civic.book('changsha').evaluate(query)['exercise']
