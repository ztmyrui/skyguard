"""Constraint, ranking and snapshot coverage for the unified task model."""
from copy import deepcopy
from itertools import combinations

import pytest

from skyguard.application import Service
from skyguard.civic import default_staff
from skyguard.domain import Person, demo, snapshot
from skyguard.flight_rules import demo_city
from skyguard.infrastructure import SQLiteRepository
from skyguard.planning import (
    MODES, PREFERENCES, build_plans, parameters, plan_staleness, planning_snapshot,
    rank_candidates, task_changes,
)


@pytest.fixture
def exercise():
    scene = demo()
    city = demo_city()
    context = {key: deepcopy(city[key]) for key in ('posts', 'walk')}
    context['staff'] = default_staff()
    return scene, context


def _view(scene):
    return dict(snapshot(scene, scene.observations[0], radar=True), status='待核验')


@pytest.mark.parametrize('mode', MODES)
def test_hard_constraints_for_every_candidate(exercise, mode):
    scene, context = exercise
    scene.clock = 1.2
    context['staff'][0].update(post_id='north', start_s=2, end_s=1100)
    context['staff'][1].update(start_s=5, end_s=1000)
    context['walk'][1]['seconds'] = 5
    context['posts'][0].update(open_s=9, close_s=220)
    context['posts'][1]['enabled'] = False
    scene.people.append(Person('excluded', '暂不可用', '联络核实', False))
    scene.tasks.append(dict(id='occupied', person_id='p3', event_id='other', status='待复核'))
    view = _view(scene)
    if mode == MODES[0]:
        post = context['posts'][0]
        view['prediction'].append(dict(seconds=30, lat=post['lat'], lon=post['lon'], radius_m=10))
    plans = build_plans(scene, view, context, parameters(mode))['plans']
    assert plans
    people = {p['id']: p for p in context['staff']}
    posts = {p['id']: p for p in context['posts']}
    for plan in plans:
        if mode == '现场观察':
            assert [t['key'] for t in plan['tasks']] == ['travel', 'observe', 'report']
        by_key = {t['key']: t for t in plan['tasks']}
        for task in plan['tasks']:
            start, end = task['start_s'], task['end_s']
            person = people[task['person_id']]
            assert 'start' not in task and 'end' not in task
            assert task['person_id'] in {'p1', 'p2'}
            assert scene.clock <= start < end
            assert person['start_s'] <= start < end <= person['end_s']
            assert all(by_key[key]['end_s'] <= start for key in task['depends_on'])
            if mode != MODES[0] or task['key'] == 'observe':
                assert start % 30 == 0
            if task['key'] == 'observe' and mode == '现场观察':
                post = posts[task['post_id']]
                assert post['open_s'] <= start < end <= post['close_s']
            if task['key'] == 'review':
                assert set(task['depends_on']) == set(by_key) - {'review'}
        for first, second in combinations(plan['tasks'], 2):
            if first['person_id'] == second['person_id']:
                assert first['end_s'] <= second['start_s'] or second['end_s'] <= first['start_s']


@pytest.mark.parametrize('minutes', (1, 3, 5))
def test_observation_duration_uses_only_existing_predictions(exercise, minutes):
    scene, context = exercise
    view = _view(scene)
    before = deepcopy(view['prediction'])
    plan = build_plans(scene, view, context, parameters(MODES[0], minutes))['plans'][0]
    observe, report = plan['tasks']
    assert observe['end_s'] - observe['start_s'] == minutes * 60
    assert report['start_s'] == observe['end_s']
    assert report['end_s'] - report['start_s'] == 120
    assert view['prediction'] == before
    assert plan['metrics']['coverage'] == 1
    horizon = int(before[-1]['seconds'])
    assert f'预测仅覆盖前 {horizon} 秒，其余时段视野需现场确认' in plan['unresolved']


def test_walk_past_forecast_allows_observation_without_extrapolation(exercise):
    scene, context = exercise
    view = _view(scene)
    before = deepcopy(view['prediction'])
    context['posts'][1]['enabled'] = False
    params = parameters(MODES[0], selected_people=['p2', 'p3'])
    plan, = build_plans(scene, view, context, params)['plans']
    travel, observe, report = plan['tasks']
    assert travel['end_s'] - travel['start_s'] == 55
    assert observe['start_s'] == travel['end_s'] == 60 > before[-1]['seconds']
    assert observe['end_s'] == report['start_s'] == 240
    assert plan['metrics']['coverage'] is None
    assert '观察窗口超出预测时域（预测至 15:20:20），视野需现场确认' in plan['unresolved']
    assert view['prediction'] == before


def test_observation_start_at_last_forecast_counts_that_point(exercise):
    scene, context = exercise
    post = context['posts'][0]
    post['open_s'] = 30
    context['posts'][1]['enabled'] = False
    last = dict(seconds=30, lat=post['lat'], lon=post['lon'], radius_m=10)
    view = dict(_view(scene), prediction=[last])
    plan, = build_plans(scene, view, context, parameters(MODES[0], selected_people=['p1', 'p2']))['plans']
    assert plan['tasks'][0]['start_s'] == last['seconds']
    assert plan['metrics']['coverage'] == 1


@pytest.mark.parametrize('source', ('forecast', 'sighting', 'interrupted_track'))
def test_last_estimate_outside_view_excludes_post(exercise, source):
    scene, context = exercise
    view = _view(scene)
    post = context['posts'][0]
    point = dict(lat=post['lat'], lon=post['lon'], radius_m=10)
    outside = dict(point, lat=post['lat'] + 0.01)
    if source == 'forecast':
        view['prediction'] = [dict(point, seconds=0), dict(outside, seconds=20)]
    else:
        state = '仅目击待核实' if source == 'sighting' else '跟踪中断'
        view.update(lat=outside['lat'], lon=outside['lon'], prediction=[], track_state=state, accuracy_m=10)
    context['posts'][1]['enabled'] = False
    result = build_plans(scene, view, context, parameters(MODES[0]))
    assert result == {'plans': [], 'gaps': ['目标估计区域不在 东门观察岗视野内']}


def test_interrupted_track_uses_current_estimate_when_no_forecast(exercise):
    scene, context = exercise
    post = context['posts'][0]
    context['posts'][1]['enabled'] = False
    view = dict(_view(scene), prediction=[], track_state='跟踪中断',
                lat=post['lat'], lon=post['lon'])
    plan, = build_plans(scene, view, context, parameters(MODES[0], selected_people=['p1', 'p2']))['plans']
    assert plan['metrics']['coverage'] == 1


@pytest.mark.parametrize('preference', PREFERENCES)
def test_positive_unknown_and_zero_coverage_break_finish_ties(exercise, preference):
    scene, context = exercise
    plans = build_plans(scene, _view(scene), context, parameters(MODES[0]))['plans']
    positive = next(p for p in plans if p['metrics']['coverage'] == 1)
    zero = next(p for p in plans if p['metrics']['coverage'] == 0)
    unknown = next(p for p in plans if p['metrics']['coverage'] is None)
    unknown['metrics'].update(zero['metrics'], coverage=None)
    positive['metrics']['skill_mismatch'] = 1
    ranked = rank_candidates([zero, unknown, positive], preference)
    assert [p['metrics']['coverage'] for p in ranked] == [1, None, 0]
    assert ranked[0]['preference_fit'] == list(PREFERENCES)
    zero['metrics']['finish_s'] -= 30
    ranked = rank_candidates([unknown, positive, zero], preference)
    assert ranked[0]['metrics']['coverage'] == 0


def test_static_sighting_and_observer_report_fallback(exercise):
    scene, context = exercise
    view = dict(_view(scene), track_state='仅目击待核实', prediction=[], age=20)
    plan = build_plans(scene, view, context,
                       parameters(MODES[0], selected_people=['p1']))['plans'][0]
    observe, report = plan['tasks']
    assert observe['person_id'] == report['person_id'] == 'p1'
    assert plan['metrics']['skill_mismatch'] == 1
    assert any('观察人员本人负责' in reason for reason in plan['unresolved'])
    assert all('预测仅覆盖' not in reason for reason in plan['unresolved'])
    assert build_plans(scene, dict(view, age=91), context, parameters(MODES[0]))['gaps']


def test_ranking_deduplicates_winners_and_sorts_by_selected_preference(exercise):
    scene, context = exercise
    quick = build_plans(scene, _view(scene), context, parameters(MODES[1]))['plans']
    fewer = build_plans(scene, _view(scene), context, parameters(MODES[1], preference=PREFERENCES[1]))['plans']
    assert quick[0]['metrics']['finish_s'] < fewer[0]['metrics']['finish_s']
    assert fewer[0]['metrics']['people'] < quick[0]['metrics']['people']
    assert quick[0]['preference_fit'] == [PREFERENCES[0], PREFERENCES[2]]
    assert fewer[0]['preference_fit'] == [PREFERENCES[1]]
    assert [plan['title'] for plan in quick] == ['方案 A', '方案 B', '方案 C']
    combined = rank_candidates([quick[0], deepcopy(quick[0])], PREFERENCES[0])
    assert len(combined) == 1
    assert combined[0]['preference_fit'] == list(PREFERENCES)
    for task in quick[1]['tasks']:
        scene.tasks.append(dict(task, mode=MODES[1], event_id='obs-a', status='待接单'))
    keep = build_plans(scene, _view(scene), context, parameters(MODES[1], preference=PREFERENCES[2]))
    assert keep['plans'][0]['metrics']['changes'] == 0
    assert all(type(plan['metrics']['changes']) is int for plan in keep['plans'])


def test_observation_only_ranks_distinct_post_and_observer_pairs(exercise):
    scene, context = exercise
    scene.clock = 90
    plans = build_plans(scene, _view(scene), context, parameters(MODES[0]))['plans']
    slots = [next(task for task in plan['tasks'] if task['key'] == 'observe') for plan in plans]
    assert len(slots) == len({(t['post_id'], t['person_id']) for t in slots}) == 3
    assert [(t['post_id'], t['person_id'], t['start_s']) for t in slots[:2]] == [
        ('east', 'p1', 90), ('north', 'p3', 90),
    ]
    assert slots[2]['start_s'] == 150


@pytest.mark.parametrize('mode', MODES[1:])
def test_coordination_only_ranks_distinct_step_assignments(exercise, mode):
    scene, context = exercise
    plans = build_plans(scene, _view(scene), context, parameters(mode))['plans']
    for task in plans[0]['tasks']:
        scene.tasks.append(dict(task, start_s=task['start_s'] + 60, end_s=task['end_s'] + 60,
                                mode=mode, event_id='obs-a', status='待接单'))
    if mode == MODES[2]:
        scene.operation = dict(earliest=1200, latest=1800, duration=300)
    plans = build_plans(scene, _view(scene), context, parameters(mode))['plans']
    assignments = {tuple((t['key'], t['person_id']) for t in plan['tasks']) for plan in plans}
    assert len(plans) == len(assignments) == 3


@pytest.mark.parametrize(('mode', 'basis'), (
    (MODES[0], '仅有 1 种可行的岗位与人员组合'),
    (MODES[1], '仅有 1 种不同的人员分配'),
    (MODES[2], '仅有 1 种不同的人员分配'),
))
def test_one_feasible_candidate_and_first_plan_changes(exercise, mode, basis):
    scene, context = exercise
    context['posts'][1]['enabled'] = False
    view = _view(scene)
    params = parameters(mode, selected_people=['p1'])
    plan, = build_plans(scene, view, context, params)['plans']
    assert basis in plan['basis']
    assert plan['metrics']['changes'] is None
    assert {entry['kind'] for entry in plan['changes']} == {'新增'}
    scene.tasks.extend(dict(task, mode=mode, event_id='obs-a', status='待接单') for task in plan['tasks'])
    renewed, = build_plans(scene, view, context, params)['plans']
    assert type(renewed['metrics']['changes']) is int
    assert renewed['metrics']['changes'] == 0


def test_aerial_work_windows_distinguish_same_assignment(exercise):
    scene, context = exercise
    for area in scene.areas:
        area.active = False
    view = _view(scene)
    params = parameters(MODES[2], selected_people=['p2'])
    initial, = build_plans(scene, view, context, params)['plans']
    prior = initial['tasks'][-1]
    scene.tasks.append(dict(prior, start_s=prior['start_s'] + 60, end_s=prior['end_s'] + 60,
                            mode=MODES[2], event_id='obs-a', status='待接单'))
    plans = build_plans(scene, view, context, params)['plans']
    assert len(plans) == 2
    assert len({p['id'] for p in plans}) == 2
    assert {p['operation_window']['start_s'] for p in plans} == {720, 780}
    assert all('仅有 1 种不同的人员分配' in p['basis'] for p in plans)


def test_observation_tries_next_boundary_and_keeps_earliest_feasible(exercise):
    scene, context = exercise
    post = context['posts'][0]
    post.update(range_m=100, open_s=5, close_s=180)
    view = dict(_view(scene), prediction=[
        dict(seconds=30, lat=post['lat'] + 0.002, lon=post['lon'], radius_m=0),
        dict(seconds=60, lat=post['lat'] + 0.001, lon=post['lon'], radius_m=0),
        dict(seconds=120, lat=post['lat'], lon=post['lon'], radius_m=0),
    ])
    params = parameters(MODES[0], 1, PREFERENCES[2], ['p1', 'p2'])
    post['open_s'] = 65
    prior, = build_plans(scene, view, context, params)['plans']
    assert prior['tasks'][0]['start_s'] == 90
    scene.tasks.extend(dict(task, mode=MODES[0], event_id='obs-a', status='待接单')
                       for task in prior['tasks'])
    post['open_s'] = 5
    plan, = build_plans(scene, view, context, params)['plans']
    assert plan['tasks'][0]['start_s'] == 60
    assert plan['metrics']['coverage'] == 0.5
    post['close_s'] = 119
    assert not build_plans(scene, view, context, params)['plans']


def test_coordination_does_not_reuse_off_grid_prior_starts(exercise):
    scene, context = exercise
    scene.clock = 11
    view = _view(scene)
    params = parameters(MODES[1], preference=PREFERENCES[2], selected_people=['p1'])
    plan, = build_plans(scene, view, context, params)['plans']
    scene.tasks.extend(dict(task, start_s=task['start_s'] + 5, end_s=task['end_s'] + 5,
                            mode=MODES[1], event_id='obs-a', status='待接单') for task in plan['tasks'])
    plan, = build_plans(scene, view, context, params)['plans']
    assert [task['start_s'] for task in plan['tasks']] == [30, 210, 510]
    assert plan['metrics']['changes'] == 3


@pytest.mark.parametrize('mode', MODES)
def test_no_staff_explains_unavailable_and_occupied(exercise, mode):
    scene, context = exercise
    scene.people[1].available = scene.people[2].available = False
    scene.tasks.append(dict(id='busy', person_id='p1', event_id='obs-b', status='待接单'))
    result = build_plans(scene, _view(scene), context, parameters(mode))
    assert not result['plans']
    assert '现场人员甲：已被事件 obs-b 占用' in result['gaps'][0]
    assert '值班人员乙：不可用' in result['gaps'][0]


def test_post_closure_and_visibility_gaps(exercise):
    scene, context = exercise
    for post in context['posts']:
        post['close_s'] = 60
    result = build_plans(scene, _view(scene), context, parameters(MODES[0]))
    assert result['plans'] == []
    assert '东门观察岗 15:21 关闭，无法容纳观察窗口' in result['gaps']
    for post in context['posts']:
        post.update(close_s=900, lat=29.0)
    result = build_plans(scene, _view(scene), context, parameters(MODES[0]))
    assert result['plans'] == []
    assert '目标估计区域不在 东门观察岗视野内' in result['gaps']


def test_task_change_list_reports_addition_schedule_assignment_removal_and_unchanged(exercise):
    scene, context = exercise
    inputs = planning_snapshot(scene, _view(scene), context, parameters(MODES[1]))
    base = dict(name='原步骤', person='现场人员甲', person_id='p1', start_s=10, end_s=20)
    old = [dict(base, key=key) for key in ('late', 'swap', 'keep', 'drop')]
    tasks = [dict(base, key='new'), dict(base, key='late', start_s=11),
             dict(base, key='swap', person_id='p2', person='值班人员乙'), dict(base, key='keep')]
    changes = {entry['key']: entry for entry in task_changes(tasks, old, inputs)}
    assert {key: value['kind'] for key, value in changes.items()} == dict(
        new='新增', late='改期', swap='换人', keep='不变', drop='取消')
    assert '15:20:11' in changes['late']['detail']
    assert '现场人员甲 → 值班人员乙' in changes['swap']['detail']


def test_aerial_window_overlap_counts_union_of_active_nearby_intervals(exercise):
    scene, context = exercise
    scene.operation = dict(purpose='活动纪实', earliest=700, latest=820, duration=120)
    for area in scene.areas:
        area.lat, area.lon = 28.1792, 112.953
    scene.areas[0].start_s, scene.areas[0].end_s = 700, 760
    scene.areas[1].start_s, scene.areas[1].end_s = 730, 780
    scene.areas[2].active = False
    plan = build_plans(scene, _view(scene), context, parameters(MODES[2]))['plans'][0]
    assert plan['operation_window']['start_s'] == 700
    assert plan['operation_window']['end_s'] == 820
    assert plan['metrics']['activity_overlap_s'] == 80


def test_snapshot_reasons_and_no_changes(exercise):
    scene, context = exercise
    params = parameters(MODES[0], selected_people=['p1', 'p2', 'p3'])
    view = _view(scene)
    before = planning_snapshot(scene, view, context, params)
    draft = dict(build_plans(scene, view, context, params, before), snapshot=before)
    assert plan_staleness(draft, deepcopy(before), 0) == []
    adjusted = deepcopy(before)
    adjusted['event'].update(quality='存在冲突', status='核验中', priority='持续关注',
                             conflict=True, lat=27.1)
    adjusted['available_people'].remove('p1')
    adjusted['ongoing_tasks']['p2'] = ['new-task']
    adjusted['people']['p3']['end_s'] -= 1
    adjusted['activities']['area-a']['active'] = False
    adjusted['params'].update(observation_minutes=5, preference=PREFERENCES[1], selected_people=['p1'])
    adjusted['posts']['east']['close_s'] -= 1
    reasons = plan_staleness(draft, adjusted, 1)
    assert any('可信度 较充分 → 存在冲突' in r and '位置变化' in r and '状态' in r for r in reasons)
    assert '人员状态变化：现场人员甲 不可用' in reasons
    assert '人员占用变化：值班人员乙' in reasons
    assert '人员资料已调整：协作人员丙' in reasons
    assert '地面活动已调整：湖南大学周边活动' in reasons
    assert all(f'筹划条件已调整：{label}' in reasons for label in ('观察时长', '偏好', '勾选人员'))
    assert '观察岗位或步行用时已调整' in reasons
    assert '观察窗口已开始（15:20:00）' in reasons
    adjusted['params']['mode'] = MODES[1]
    assert '筹划条件已调整：筹划类型' in plan_staleness(draft, adjusted, 0)
    assert not plan_staleness(dict(draft, confirmed=True), adjusted, 1)


@pytest.mark.parametrize('mode', MODES[1:])
def test_coordination_time_limit_and_aerial_request(exercise, mode):
    scene, context = exercise
    inputs = planning_snapshot(scene, _view(scene), context, parameters(mode))
    pending = {'snapshot': inputs, 'plans': []}
    assert plan_staleness(pending, inputs, 120) == []
    assert plan_staleness(pending, inputs, 121) == ['时间安排已过期']
    if mode == MODES[2]:
        assert plan_staleness(pending, dict(inputs, operation={'purpose': '调整'}), 0) == ['航拍需求已调整']


def test_legacy_minutes_migrate_using_generation_clock_and_audit_once(tmp_path):
    repo = SQLiteRepository(tmp_path / 'legacy.sqlite')
    scene = demo()
    base = dict(name='联系负责人核实', person_id='p2', person='值班人员乙',
                event_id='obs-a', feedback='', start=1.5, end=3, status='待接单')
    scene.tasks = [
        dict(base, id='current-task', plan_thread='current'),
        dict(base, id='audited-task', plan_thread='audited'),
        dict(base, id='unknown-task', plan_thread='unknown'),
        dict(base, id='field-task', plan_thread='field', start_s=66, end_s=77, arrival_s=65),
    ]
    repo.commit(dict(schema=3, scene=scene.to_dict(), pending=dict(
        event_id='obs-a', thread='current', clock=37, plans=[], trace=[], confirmed=False)),
        '生成候选方案', {'thread': 'audited', 'clock': 20})
    service = Service(repo, None)
    windows = [(task['start_s'], task['end_s']) for task in service.scene.tasks]
    assert windows == [(127, 217), (110, 200), (90, 180), (66, 77)]
    assert all(task['key'] and task['purpose'] and task['depends_on'] == [] for task in service.scene.tasks)
    assert all(task['history'] == [] for task in service.scene.tasks)
    assert all(not {'start', 'end', 'arrival_s'}.intersection(t) for t in service.scene.tasks)
    assert Service(repo, None).scene.tasks == service.scene.tasks
    assert len([log for log in repo.history() if log['action'] == '迁移筹划任务']) == 1


def test_coverage_only_counts_points_inside_the_selected_window(exercise):
    scene, context = exercise
    station = context['posts'][0]
    station.update(range_m=100, open_s=5, close_s=90)
    context['posts'][1]['enabled'] = False
    points = [
        {'seconds': at, 'lat': station['lat'] + offset, 'lon': station['lon'], 'radius_m': 40}
        for at, offset in ((0, 0.002), (5, 0.002), (30, 0), (60, 0.001), (91, 0))
    ]
    view = dict(_view(scene), prediction=points)
    plan = build_plans(scene, view, context, parameters(MODES[0], 1))['plans'][0]
    assert plan['metrics']['coverage'] == 0.5
    assert all('预测仅覆盖' not in reason for reason in plan['unresolved'])


def test_snapshot_rounds_coordinates_and_uses_configured_exercise_start(exercise):
    scene, context = exercise
    scene.start_of_day_s = 36000
    params = parameters(MODES[1])
    view = dict(_view(scene), lat=28.17761, lon=112.95352)
    before = planning_snapshot(scene, view, context, params)
    near = planning_snapshot(scene, dict(view, lat=28.17762), context, params)
    assert before['event'] == near['event']
    assert before['event']['lat'] == 28.1776
    assert before['event']['lon'] == 112.9535
    assert scene.clock_text(90) == '10:01:30'
