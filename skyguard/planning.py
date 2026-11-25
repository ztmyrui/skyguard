"""Civilian verification planning and input snapshots."""
from dataclasses import asdict
from hashlib import sha256
from itertools import product
from math import ceil

from .domain import clock_text, distance, snapshot
from .task_flow import ACTIVE

MODES = ('现场观察', '事件协同', '航拍辅助')
PREFERENCES = ('更快核验', '更少人员', '尽量保留原计划')
STARTED = {'进行中', '待复核'}
START_STEP_S = 30
OBSERVE_SPECS = (
    ('travel', '前往岗位', '按已配置的步行安排前往观察岗。', '现场核验', 0),
    ('observe', '岗位观察', '在观察岗确认目标方向与外观。', '现场核验', 0),
    ('report', '回传与复核', '汇总岗位观察，并与值班记录核对。', '联络核实', 120),
)
SPECS = {
    MODES[0]: OBSERVE_SPECS,
    MODES[1]: (
        ('contact', '联系负责人核实', '向负责人核对事件时间、地点和已有记录。', '联络核实', 180),
        ('observe', '补充独立观察', '补充一份独立观察记录，标明来源与不确定项。', '现场核验', 300),
        ('review', '复核证据与活动安排', '对照联络记录、观察证据和地面活动。', '联络核实', 180),
    ),
    MODES[2]: (
        ('request', '核对拍摄需求与场地时间', '核对拍摄用途和场地的可用时间。', '联络核实', 180),
        ('site', '确认现场联络与活动范围', '核实联络渠道和地面活动安排。', '现场核验', 300),
        ('review', '确认作业调整与交付清单', '复核建议时段、协调事项与交付内容。', '联络核实', 240),
    ),
}


TEMPLATE_SPECS = {key if key != 'observe' else 'second': spec
                  for spec in SPECS[MODES[1]] for key in (spec[0],)}


def schedule_templates(scene, context, chosen):
    selected = tuple(dict.fromkeys(chosen))
    if not selected or any(key not in TEMPLATE_SPECS for key in selected):
        raise ValueError('请选择要建立的协同任务')
    roster = staff_records(scene, context)
    busy = [task for task in scene.tasks if task['status'] in ACTIVE]
    tasks = []
    for key in selected:
        spec = TEMPLATE_SPECS[key]
        duration = spec[-1]
        ready = max([scene.clock, *(task['end_s'] for task in tasks)] if key == 'review' else [scene.clock])
        candidates = []
        for person in roster:
            if not person['available'] or person['role'] != spec[3]:
                continue
            start = max(ceil(ready), person['start_s'])
            for task in sorted(busy + tasks, key=lambda item:item['start_s']):
                if task['person_id'] == person['id'] and start < task['end_s'] and task['start_s'] < start+duration:
                    start = task['end_s']
            if start + duration <= min(person['end_s'],scene.duration_s):
                candidates.append((start, person['id'], person))
        if not candidates:
            raise ValueError(f'没有可安排的值班人员：{spec[1]}')
        start, _, person = min(candidates)
        tasks.append(dict(_task(spec, person, start, duration, [row['key'] for row in tasks] if key=='review' else ()),
                          key=key))
    return tasks


def parameters(mode, observation_minutes=3, preference=PREFERENCES[0], selected_people=None):
    if mode not in MODES or observation_minutes not in (1, 3, 5) or preference not in PREFERENCES:
        raise ValueError('请核对筹划类型、观察时长和偏好。')
    return dict(mode=mode, observation_minutes=observation_minutes if mode == MODES[0] else None,
                preference=preference,
                selected_people=sorted(set(selected_people)) if selected_people is not None else None)


def staff_records(scene, context):
    shifts = {row['id']: row for row in context.get('staff', [])}
    roster = []
    for person in scene.people:
        shift = shifts.get(person.id, {})
        record = dict(start_s=0, end_s=scene.duration_s, post_id=None) | shift | asdict(person)
        record['available'] = person.available and shift.get('available', True)
        roster.append(record)
    return roster

def reservations(scene, event_id):
    """Same-event drafts may be replaced. Accepted work keeps its owner occupied."""
    return [task for task in scene.tasks if task['status'] in ACTIVE
            and (task['event_id'] != event_id or task['status'] in STARTED)]


def planning_snapshot(scene, view, context, params):
    staff = staff_records(scene, context)
    busy = reservations(scene, view['id'])
    return {
        'clock': scene.clock, 'start_of_day_s': scene.start_of_day_s, 'revision': scene.revision,
        'event': {key: view.get(key) for key in ('status', 'quality', 'priority', 'conflict')} | {
            key: round(view[key], 4) if view.get(key) is not None else None for key in ('lat', 'lon')
        },
        'available_people': sorted(p['id'] for p in staff if p['available']),
        'people': {p['id']: {key: p[key] for key in ('name', 'role', 'start_s', 'end_s', 'post_id')}
                   for p in staff},
        'ongoing_tasks': {
            p['id']: sorted(task['id'] for task in busy if task['person_id'] == p['id'])
            for p in staff if params['selected_people'] is None or p['id'] in params['selected_people']
        },
        'activities': {a.id: {key: getattr(a, key) for key in ('name', 'active', 'start_s', 'end_s')}
                       for a in scene.areas},
        'params': params,
        'operation': dict(scene.operation) if params['mode'] == MODES[2] else None,
        'posts': {p['id']: dict(p) for p in context.get('posts', [])} if params['mode'] == MODES[0] else {},
        'walk': sorted([e['from'], e['to'], e['seconds']] for e in context.get('walk', []))
        if params['mode'] == MODES[0] else [],
    }


def _clock(input_snapshot, seconds):
    return clock_text(seconds, input_snapshot['start_of_day_s'])


def _event_changes(before, after):
    names = dict(status='状态', quality='可信度', priority='优先级', conflict='冲突标记')
    changes = []
    for key, label in names.items():
        if before.get(key) != after.get(key):
            display = lambda v: ('有' if v else '无') if isinstance(v, bool) else v or '未知'
            changes.append(f'{label} {display(before.get(key))} → {display(after.get(key))}')
    if any(before.get(key) != after.get(key) for key in ('lat', 'lon')):
        changes.append('位置变化')
    return ['事件信息已更新：' + '、'.join(changes)] if changes else []


def _people_changes(before, after):
    reasons = []
    old, new = set(before['available_people']), set(after['available_people'])
    staff, current = before['people'], after['people']
    for pid in sorted(staff.keys() | current.keys()):
        name = (current.get(pid) or staff[pid])['name']
        if (pid in old) != (pid in new):
            reasons.append(f'人员状态变化：{name} {"恢复可用" if pid in new else "不可用"}')
        if staff.get(pid) != current.get(pid):
            reasons.append(f'人员资料已调整：{name}')
        if before['ongoing_tasks'].get(pid, []) != after['ongoing_tasks'].get(pid, []):
            reasons.append(f'人员占用变化：{name}')
    return reasons


def plan_staleness(pending, current_snapshot, clock):
    """Explain all changes affecting the selected draft."""
    if pending.get('confirmed'):
        return []
    before = pending.get('snapshot')
    if not before:
        return ['旧版草案缺少输入快照，请重新生成']
    after = current_snapshot
    reasons = _event_changes(before['event'], after['event']) + _people_changes(before, after)
    old, new = before['activities'], after['activities']
    for aid in sorted(old.keys() | new.keys()):
        if old.get(aid) != new.get(aid):
            reasons.append(f'地面活动已调整：{(new.get(aid) or old[aid])["name"]}')
    for key, label in zip(('mode', 'observation_minutes', 'preference', 'selected_people'),
                          ('筹划类型', '观察时长', '偏好', '勾选人员')):
        if before['params'][key] != after['params'][key]:
            reasons.append(f'筹划条件已调整：{label}')
    if before['params']['mode'] == after['params']['mode']:
        if before['operation'] != after['operation']:
            reasons.append('航拍需求已调整')
        if before['posts'] != after['posts'] or before['walk'] != after['walk']:
            reasons.append('观察岗位或步行用时已调整')
    tasks = pending.get('tasks')
    if tasks is None:
        plans = pending.get('plans', [])
        tasks = plans[pending.get('selected', 0)]['tasks'] if plans else []
    if before['params']['mode'] == MODES[0]:
        for task in tasks:
            if task['key'] == 'observe' and task['start_s'] < clock:
                reasons.append(f'观察窗口已开始（{_clock(before, task["start_s"])}）')
    elif clock - before['clock'] > 120:
        reasons.append('时间安排已过期')
    return reasons


def _task(spec, person, start, duration, dependencies=(), post=None, criterion=None):
    key, name, purpose, role, _ = spec
    return dict(key=key, name=name, purpose=purpose, person_id=person['id'], person=person['name'],
                role=role, post_id=post['id'] if post else None, post=post['name'] if post else None,
                start_s=start, end_s=start + duration, depends_on=list(dependencies),
                criterion=criterion or '提交可追溯核实结果，由值班人员复核。', status='草案')


def task_changes(tasks, previous, input_snapshot):
    mode = input_snapshot['params']['mode']
    prior = {(task.get('mode') or mode, task['key']): task for task in previous}
    changes = []
    for task in tasks:
        key, name = task['key'], task['name']
        old = prior.pop((mode, key), None)
        if old is None:
            kind, detail = '新增', f'{task["person"]} · {_clock(input_snapshot, task["start_s"])}'
        else:
            details = []
            if old['person_id'] != task['person_id']:
                details.append(f'{old["person"]} → {task["person"]}')
            if any(old[k] != task[k] for k in ('start_s', 'end_s')):
                bounds = lambda t: '–'.join(_clock(input_snapshot, t[k]) for k in ('start_s', 'end_s'))
                details.append(f'{bounds(old)} → {bounds(task)}')
            if old.get('post_id') != task.get('post_id'):
                details.append(f'{old.get("post") or "—"} → {task.get("post") or "—"}')
            kind = '换人' if old['person_id'] != task['person_id'] else '改期' if details else '不变'
            detail = '；'.join(details) if details else '负责人、岗位与时间一致'
        changes.append(dict(key=key, name=name, kind=kind, detail=detail))
    changes.extend(dict(key=t['key'], name=t['name'], kind='取消', detail=f'原负责人 {t["person"]}')
                   for t in prior.values())
    return changes


def _signature(tasks):
    return tuple(sorted((t['key'], t['person_id'], t['post_id'], t['start_s'], t['end_s']) for t in tasks))


def _candidate_key(plan):
    if plan['mode'] == MODES[0]:
        observe = next(t for t in plan['tasks'] if t['key'] == 'observe')
        return observe['post_id'], observe['person_id']
    assignment = tuple(sorted((t['key'], t['person_id']) for t in plan['tasks']))
    window = plan['operation_window']
    return assignment, (window['start_s'], window['end_s']) if window else None


def _plan(event_id, inputs, tasks, people, previous, basis, unresolved, *,
          walk=None, coverage=None, window=None, overlap=None):
    changes = task_changes(tasks, previous, inputs)
    finish = window['end_s'] if window else max(t['end_s'] for t in tasks)
    signature = _signature(tasks)
    if window:
        signature += ((window['start_s'], window['end_s']),)
    return dict(id=sha256(repr(signature).encode()).hexdigest()[:12],
                title='', mode=inputs['params']['mode'], event_id=event_id,
                preference_fit=[], snapshot=inputs, tasks=tasks, changes=changes, basis=basis,
                unresolved=unresolved, operation_window=window,
                metrics=dict(finish_s=finish, duration_s=finish - inputs['clock'],
                             people=len({t['person_id'] for t in tasks}), walk_s=walk,
                             coverage=coverage, skill_mismatch=sum(
                                 t['role'] != people[t['person_id']]['role'] for t in tasks),
                             activity_overlap_s=overlap,
                             changes=sum(c['kind'] != '不变' for c in changes) if previous else None))


def rank_candidates(candidates, preference):
    def ranking(plan, label):
        metrics = plan['metrics']
        primary = {
            PREFERENCES[0]: (metrics['finish_s'],),
            PREFERENCES[1]: (metrics['people'], metrics['finish_s']),
            PREFERENCES[2]: (metrics['changes'] or 0, metrics['finish_s']),
        }[label]
        coverage = metrics['coverage']
        coverage_order = 1 if coverage is None else 0 if coverage > 0 else 2
        return primary + (coverage_order, metrics['skill_mismatch'], -(coverage or 0),
                          metrics['activity_overlap_s'] or 0, metrics['walk_s'] or 0,
                          _signature(plan['tasks']))

    unique = {}
    for plan in candidates:
        key = _candidate_key(plan)
        if key not in unique or ranking(plan, preference) < ranking(unique[key], preference):
            unique[key] = dict(plan, preference_fit=[])
    if not unique:
        return []

    winners = {}
    for label in PREFERENCES:
        key = min(unique, key=lambda key: ranking(unique[key], label))
        winner = unique[key]
        winner['preference_fit'].append(label)
        winners[key] = winner
    ordered = sorted(unique, key=lambda key: ranking(unique[key], preference))
    shortlist = list(winners.values())
    shortlist.extend([unique[key] for key in ordered if key not in winners][:3 - len(shortlist)])
    shortlist.sort(key=lambda plan: ranking(plan, preference))
    count = len(unique)
    mode = shortlist[0]['mode']
    basis = []
    if count < 3:
        if mode == MODES[0]:
            basis.append(f'仅有 {count} 种可行的岗位与人员组合')
        else:
            assignments = len({assignment for assignment, _ in unique})
            basis.append(f'仅有 {assignments} 种不同的人员分配')
            if assignments < count:
                basis.append(f'按人员分配和航拍作业窗口区分，共有 {count} 种方案')
    for index, plan in enumerate(shortlist):
        plan['title'] = f'方案 {"ABC"[index]}'
        plan['basis'] = [*plan['basis'], *basis]
    return shortlist


def _available(scene, view, context, params):
    busy = {task['person_id']: task['event_id'] for task in reservations(scene, view['id'])}
    selected = params['selected_people']
    people, exclusions = {}, []
    for person in staff_records(scene, context):
        pid, name = person['id'], person['name']
        if not person['available']:
            exclusions.append(f'{name}：不可用')
        elif pid in busy:
            exclusions.append(f'{name}：已被事件 {busy[pid]} 占用')
        elif selected is not None and pid not in selected:
            exclusions.append(f'{name}：未勾选')
        elif person['end_s'] <= scene.clock:
            exclusions.append(f'{name}：班次已结束')
        else:
            people[pid] = person
    return people, exclusions


def _round_start(seconds):
    return ceil(seconds / START_STEP_S) * START_STEP_S


def _observation_slots(points, first, last, duration, inputs):
    horizon = points[-1][0]
    for start in range(first, last + 1, START_STEP_S):
        if start > horizon:
            yield start, None, f'观察窗口超出预测时域（预测至 {_clock(inputs, horizon)}），视野需现场确认'
            continue
        segment = [(margin, radius) for t, margin, radius in points if start <= t <= start + duration]
        if not any(margin + radius >= 0 for margin, radius in segment):
            continue
        coverage = sum(margin >= 0 for margin, _ in segment) / len(segment)
        note = None
        if horizon < start + duration:
            note = f'预测仅覆盖前 {int(horizon - start)} 秒，其余时段视野需现场确认'
        yield start, coverage, note


def _reports(people, observer, end, bound, previous):
    options = []
    for person in people.values():
        if person['role'] != '联络核实':
            continue
        first = max(end, person['start_s'])
        last = min(bound, person['end_s']) - 120
        for start in _start_options(first, last, previous):
            options.append((person, start))
    if not options:
        last = min(bound, observer['end_s']) - 120
        options.extend((observer, start) for start in _start_options(end, last, previous))
    return options


def _start_options(first, last, previous):
    if first <= last:
        yield first
        if previous and first < previous['start_s'] <= last:
            yield previous['start_s']


def _post_candidates(scene, view, post, people, travel, inputs, previous, gaps, prediction):
    now, duration = ceil(scene.clock), inputs['params']['observation_minutes'] * 60
    station = post['lat'], post['lon']
    estimate = prediction[-1] if prediction else dict(lat=view['lat'], lon=view['lon'], radius_m=view['accuracy_m'])
    margin = post['range_m'] - distance(station, (estimate['lat'], estimate['lon']))
    if margin + estimate['radius_m'] < 0:
        gaps.add(f'目标估计区域不在 {post["name"]}视野内')
        return
    points = [(scene.clock + p['seconds'],
               post['range_m'] - distance(station, (p['lat'], p['lon'])), p['radius_m'])
              for p in prediction]
    visibility_basis = ('末个预测点的估计区域与岗位视野相交，视野覆盖按窗口内已有点位计算'
                        if points else '按当前静态位置与误差范围估计岗位视野')
    old_report = next((t for t in reversed(previous) if t['key'] == 'report'), None)
    for observer in people.values():
        if observer['role'] != '现场核验':
            continue
        walk = 0 if observer['post_id'] == post['id'] else travel.get((observer['post_id'], post['id']))
        if walk is None:
            gaps.add(f'{observer["name"]}无法按已配置的步行时间到达{post["name"]}')
            continue
        first = max(now, observer['start_s']) + walk
        start_bound = _round_start(max(first, post['open_s']))
        close, shift_end = post['close_s'], min(observer['end_s'], scene.duration_s)
        last = min(close, shift_end) - duration
        if start_bound > last:
            if start_bound + duration > close:
                gaps.add(f'{post["name"]} {_clock(inputs, close)[:5]} 关闭，无法容纳观察窗口')
            if start_bound + duration > shift_end:
                gaps.add(f'{observer["name"]}的班次无法容纳到岗及观察窗口')
            continue
        found_slot = False
        slots = (_observation_slots(points, start_bound, last, duration, inputs) if points
                 else ((start_bound, float(margin >= 0), None),))
        for start, coverage, visibility_note in slots:
            found_slot = True
            end = start + duration
            reports = _reports(people, observer, end, scene.duration_s, old_report)
            if not reports:
                gaps.add(f'{post["name"]}观察结束后没有可安排 2 分钟回传与复核的人员')
                continue
            for reporter, report_start in reports:
                tasks, unresolved = _observation_tasks(post, observer, reporter, start, duration,
                                                      walk, report_start, visibility_note)
                yield _plan(view['id'], inputs, tasks, people, previous,
                            ['已核对人员班次、岗位开放时段与步行用时', visibility_basis],
                            unresolved, walk=walk, coverage=coverage)
            break
        if not found_slot:
            gaps.add(f'{post["name"]}在可用观察窗口内缺少可覆盖的预测位置')


def _observation_tasks(post, observer, reporter, start, duration, walk, report_start, visibility_note):
    travel, observe, report = OBSERVE_SPECS
    tasks = []
    if walk:
        tasks.append(_task(travel, observer, start - walk, walk, post=post, criterion='到岗后记录到达时间。'))
    task = _task(observe, observer, start, duration, ['travel'] if walk else [], post,
                 '记录发现时间、方向及观察结果，条件允许时拍照。')
    task['purpose'] = f'在{post["name"]}确认目标方向与外观。'
    tasks.extend((task, _task(report, reporter, report_start, 120, ['observe'], criterion='回传观察结果并标明待核对事项。')))
    unresolved = ['岗位视野为配置估计，现场可反馈遮挡或未观察到。']
    if visibility_note:
        unresolved.append(visibility_note)
    if reporter['id'] == observer['id']:
        unresolved.append('回传时段没有可用的联络核实人员，由观察人员本人负责。')
    return tasks, unresolved


def _field_plans(scene, view, context, people, inputs, previous, gaps):
    if not any(p['role'] == '现场核验' for p in people.values()):
        return []
    posts = [post for post in context.get('posts', []) if post['enabled']]
    if not posts:
        gaps.add('没有启用的观察岗位，请在资料资源中核对岗位配置。')
    sighting = view.get('track_state') == '仅目击待核实'
    prediction = [] if sighting else view['prediction']
    if not prediction and (view['lat'] is None or view['lon'] is None or sighting and view['age'] > 90):
        gaps.add('当前缺少可靠预测位置或近期目击位置，请补充观察。')
        return []
    travel = {(r['from'], r['to']): r['seconds'] for r in context.get('walk', [])}
    return [plan for post in posts
            for plan in _post_candidates(scene, view, post, people, travel, inputs, previous, gaps, prediction)]


def _activity_overlap(start, end, areas):
    intervals = sorted((max(start, a.start_s), min(end, a.end_s))
                       for a in areas if a.start_s < end and a.end_s > start)
    total, cutoff = 0, start
    for first, last in intervals:
        total += max(0, last - max(first, cutoff))
        cutoff = max(cutoff, last)
    return total


def _operation_window(scene, view, ready_s):
    request = scene.operation
    first = max(ready_s, request.get('earliest', 0))
    duration = request.get('duration', 300)
    last = min(scene.duration_s, request.get('latest', scene.duration_s)) - duration
    if first > last:
        return None, None
    areas = [a for a in scene.areas if a.active and (view['lat'] is None or
             distance((view['lat'], view['lon']), (a.lat, a.lon)) <= a.radius_m + view['accuracy_m'])]
    starts = {first, last}
    for area in areas:
        for boundary in (area.start_s, area.end_s):
            starts.update(t for t in (boundary, boundary - duration) if first <= t <= last)
    overlap, start = min((_activity_overlap(t, t + duration, areas), t) for t in starts)
    return dict(start_s=start, end_s=start + duration, purpose=request.get('purpose', '摄影协作'),
                note='建议时段须经合作团队与相关责任人确认，飞行许可须另行核对。'), overlap


def _schedules(specs, assignment, now, bound, previous, tasks=()):
    if not specs:
        yield list(tasks)
        return
    spec, person = specs[0], assignment[0]
    key, _, _, _, duration = spec
    dependencies = [t['key'] for t in tasks] if key == 'review' else []
    first = _round_start(max(now, person['start_s'], *(t['end_s'] for t in tasks
                         if t['key'] in dependencies or t['person_id'] == person['id'])))
    last = min(bound, person['end_s']) - duration
    old = previous.get(key)
    if old and old['start_s'] % START_STEP_S:
        old = None
    for start in _start_options(first, last, old):
        task = _task(spec, person, start, duration, dependencies)
        yield from _schedules(specs[1:], assignment[1:], now, bound, previous, (*tasks, task))


def _coordination_plans(scene, view, people, inputs, previous, completed, gaps):
    mode, now = inputs['params']['mode'], scene.clock
    specs = [spec for spec in SPECS[mode] if spec[0] not in completed]
    if not specs:
        gaps.add('当前类型的步骤均已完成。')
        return []
    plans, viable_schedule = [], False
    old = {t['key']: t for t in previous}
    for assignment in product(people.values(), repeat=len(specs)):
        for tasks in _schedules(specs, assignment, now, scene.duration_s, old):
            viable_schedule = True
            window, overlap = None, None
            if mode == MODES[2]:
                window, overlap = _operation_window(scene, view, max(t['end_s'] for t in tasks))
                if window is None:
                    continue
            plans.append(_plan(view['id'], inputs, tasks, people, previous,
                               ['只分配空闲且可用的人员；同人任务按顺序进行',
                                '复核在联络和观察之后进行'],
                               ['身份与飞行授权仍需人工核实'], window=window, overlap=overlap))
    if not plans:
        gaps.add('完成准备任务后，航拍需求窗口无法容纳作业时长。' if viable_schedule else
                 '可用人员的剩余班次无法容纳所有步骤，请调整人员或筹划时刻。')
    return plans


def build_plans(scene, view, context=None, params=None, inputs=None):
    """Generate ranked, distinct candidates or concrete resource gaps."""
    context = context or {}
    params = params or parameters(MODES[1])
    inputs = planning_snapshot(scene, view, context, params) if inputs is None else inputs
    mode = params['mode']
    people, exclusions = _available(scene, view, context, params)
    current = [task for task in scene.tasks if task['event_id'] == view['id']]
    if any(task['status'] in STARTED for task in current):
        return {'plans': [], 'gaps': ['本事件仍有进行中或待复核的步骤，请先完成复核再重新筹划。']}
    if not people or mode == MODES[0] and not any(p['role'] == '现场核验' for p in people.values()):
        role = '现场核验' if mode == MODES[0] else '协同'
        details = f'（{"；".join(exclusions)}）' if exclusions else ''
        return {'plans': [], 'gaps': [f'没有可用的{role}人员{details}']}
    previous = [task for task in current if task['status'] == '待接单']
    completed = {task['key'] for task in current
                 if task['status'] == '已完成' and task.get('mode') == mode}
    gaps = set()
    if mode == MODES[0]:
        plans = _field_plans(scene, view, context, people, inputs, previous, gaps)
    else:
        plans = _coordination_plans(scene, view, people, inputs, previous, completed, gaps)
    return {'plans': rank_candidates(plans, params['preference']), 'gaps': sorted(gaps) if not plans else []}


def plan_for_event(scene, event_id, mode=MODES[1]):
    """Compatibility entry point for the original framework-free planner."""
    view = snapshot(scene, next(o for o in scene.observations if o.id == event_id))
    return build_plans(scene, view, params=parameters(mode))['plans']

