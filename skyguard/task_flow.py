"""Execution rules for civilian exercise tasks and their audit history."""

ACTIVE = frozenset({'待接单', '进行中', '待复核'})
ENDED = frozenset({'无法完成', '已替代', '已取消'})
RESOLVED = frozenset({'已完成', '已替代'})
OUTCOMES = ('观察到', '未观察到', '部分观察到', '不适用')
ACTION_LABELS = {
    'accept': '接单', 'submit': '提交反馈', 'approve': '复核通过',
    'reject': '退回', 'unable': '无法完成', 'replace': '方案替代',
}
NOTE_PROMPTS = {'submit': '请填写执行结果', 'reject': '请填写退回原因', 'unable': '请填写无法完成的原因'}
_TRANSITIONS = {
    'accept': ({'待接单'}, '进行中'), 'submit': ({'进行中'}, '待复核'),
    'approve': ({'待复核'}, '已完成'), 'reject': ({'待复核'}, '进行中'),
    'unable': ({'待接单', '进行中'}, '无法完成'),
}


def is_overdue(task, clock):
    """An unfinished execution is overdue strictly after its scheduled end."""
    return task['status'] in ('待接单', '进行中') and clock > task['end_s']


def dependencies(task, tasks):
    """Resolve predecessor keys within the task's own confirmed plan."""
    by_key = {other['key']: other for other in tasks if other['plan_thread'] == task['plan_thread']}
    return [(key, by_key.get(key)) for key in task['depends_on']]


def tasks_resolved(tasks):
    return bool(tasks) and all(task['status'] in RESOLVED for task in tasks)


def _record(task, action, target, note, outcome, at):
    entry = {'at': at, 'action': action, 'from': task['status'], 'to': target, 'note': note, 'outcome': outcome}
    return dict(task, status=target, feedback=note or task.get('feedback', ''),
                history=[*task.get('history', []), entry])


def transition(task, action, *, tasks=(), clock=0, note='', outcome=None, at=None):
    """Return a new task record after applying one operator action."""
    if action not in _TRANSITIONS:
        raise ValueError('未知任务动作')
    allowed, target = _TRANSITIONS[action]
    if task['status'] not in allowed:
        raise ValueError(f'{task["status"]}状态下不能{ACTION_LABELS[action]}')
    if not isinstance(note, str):
        raise ValueError('说明应为文本')
    note = note.strip()
    if action in NOTE_PROMPTS and not note:
        raise ValueError(NOTE_PROMPTS[action])
    if outcome is not None and (action != 'submit' or outcome not in OUTCOMES):
        raise ValueError('请选择有效的观察结果')
    if action == 'accept':
        if any(not other or other['status'] != '已完成' for _, other in dependencies(task, tasks)):
            raise ValueError('请先完成并复核前置任务')
        if task.get('mode') == '现场观察' and clock >= task['end_s']:
            target, note = '已替代', '任务窗口已结束，请重新筹划。'
    return _record(task, action, target, note, outcome, at)


def replace(task, *, at):
    """Record the automatic replacement made by confirming a newer plan."""
    if task['status'] != '待接单':
        raise ValueError('只能替代待接单的任务')
    return _record(task, 'replace', '已替代', '已确认新方案。', None, at)
