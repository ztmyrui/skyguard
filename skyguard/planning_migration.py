"""Upgrade persisted pre-P4 task records at the application boundary."""
from collections import defaultdict

from .planning import MODES, SPECS, parameters, planning_snapshot


def _known_step(task, mode):
    modes = (mode,) if mode in SPECS else MODES
    for candidate in modes:
        for key, name, purpose, role, _ in SPECS[candidate]:
            if task['name'] == name or key == 'observe' and task['name'] == '固定岗位观察':
                return candidate, key, purpose, role
    return mode, None, task['name'], ''


def _upgrade_tasks(tasks, clocks, *, mode=None, clock=0):
    updated = 0
    groups = defaultdict(list)
    for index, task in enumerate(tasks):
        thread = task.get('plan_thread')
        groups[thread].append(task)
        if not (('start' in task or 'end' in task) or 'key' not in task):
            continue
        updated += 1
        generated = task.pop('clock', clocks.get(thread, clock))
        for key in ('start', 'end'):
            relative = task.pop(key, None)
            if key + '_s' not in task and relative is not None:
                task[key + '_s'] = round(generated + relative * 60, 6)
        inferred, key, purpose, role = _known_step(task, mode or task.get('mode'))
        task.setdefault('key', key or task.get('id', f'legacy-{index}'))
        task.setdefault('mode', inferred)
        task.setdefault('purpose', purpose)
        task.setdefault('role', role)
        task.setdefault('post_id', None)
        task.setdefault('post', None)
        task.setdefault('criterion', '记录执行结果，提交复核。')
        task.pop('arrival_s', None)
    for group in groups.values():
        for task in group:
            if 'depends_on' in task:
                continue
            if task['key'] == 'review':
                task['depends_on'] = [item['key'] for item in group
                                      if item['key'] in ('contact', 'observe', 'request', 'site')]
            else:
                task['depends_on'] = []
    return updated


def migrate_planning(scene, pending, audit):
    clocks = {entry['detail']['thread']: entry['detail']['clock'] for entry in audit
              if entry['action'] == '生成候选方案' and 'clock' in entry['detail'] and 'thread' in entry['detail']}
    legacy = pending is not None and 'snapshot' not in pending and 'gaps' not in pending
    if legacy:
        clocks.setdefault(pending['thread'], pending.get('clock', 0))
    count = _upgrade_tasks(scene.tasks, clocks)
    if legacy:
        for index, plan in enumerate(pending['plans']):
            count += _upgrade_legacy_plan(scene, plan, index, pending)
        pending.setdefault('gaps', [])
    return {'tasks': count, 'legacy_draft': legacy} if count or legacy else None


def _upgrade_legacy_plan(scene, plan, index, pending):
    clock = plan.pop('clock', pending.get('clock', 0))
    mode = plan['mode']
    tasks = plan['tasks']
    count = _upgrade_tasks(tasks, {}, mode=mode, clock=clock)
    window = plan.get('operation_window')
    if window:
        window['start_s'], window['end_s'] = window.pop('start'), window.pop('end')
    finish = window['end_s'] if window else max(t['end_s'] for t in tasks)
    inputs = planning_snapshot(scene, {'id': pending['event_id']}, {}, parameters(mode))
    inputs.update(clock=clock, revision=plan.pop('revision', pending.get('revision', scene.revision)))
    changes = plan.pop('changes', 0)
    plan.update(title=f'方案 {"ABC"[index]}', preference_fit=[], changes=[], snapshot=inputs,
                operation_window=window,
                metrics=dict(finish_s=finish, duration_s=finish - clock,
                             people=plan.pop('people_count', len({t['person_id'] for t in tasks})),
                             walk_s=None, coverage=None, skill_mismatch=plan.pop('skill_mismatch', 0),
                             activity_overlap_s=None, changes=changes))
    plan.pop('minutes', None)
    return count
