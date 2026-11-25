"""Exercise the pure task state machine and its derived state."""
from itertools import product

import pytest

from skyguard.task_flow import OUTCOMES, is_overdue, replace, tasks_resolved, transition


AT = '2026-11-20T00:00:00+00:00'
LEGAL = {
    ('待接单', 'accept'): '进行中', ('进行中', 'submit'): '待复核',
    ('待复核', 'approve'): '已完成', ('待复核', 'reject'): '进行中',
    ('待接单', 'unable'): '无法完成', ('进行中', 'unable'): '无法完成',
}
STATUSES = ('待接单', '进行中', '待复核', '已完成', '已替代', '已取消', '无法完成')
ACTIONS = ('accept', 'submit', 'approve', 'reject', 'unable')


def step(status='待接单', **fields):
    return dict({'key': 'observe', 'plan_thread': 'plan-a', 'depends_on': [], 'end_s': 120,
                 'mode': '现场观察', 'status': status}, **fields)


@pytest.mark.parametrize(('status', 'action'), list(product(STATUSES, ACTIONS)))
def test_transition_matrix_and_legacy_history(status, action):
    original = step(status)
    target = LEGAL.get((status, action))
    if target is None:
        with pytest.raises(ValueError, match='状态下不能'):
            transition(original, action, note=' 说明 ', at=AT)
        return
    result = transition(original, action, note=' 说明 ', at=AT)
    assert result['status'] == target
    assert result['history'] == [
        {'at': AT, 'action': action, 'from': status, 'to': target, 'note': '说明', 'outcome': None},
    ]
    assert original == step(status)


@pytest.mark.parametrize(('status', 'action'), [
    ('进行中', 'submit'), ('待复核', 'reject'), ('待接单', 'unable'), ('进行中', 'unable'),
])
@pytest.mark.parametrize('note', ['', ' \n ', None])
def test_explanation_required(status, action, note):
    with pytest.raises(ValueError, match='填写|文本'):
        transition(step(status), action, note=note)


@pytest.mark.parametrize('outcome', (None, *OUTCOMES))
def test_submission_and_review_preserve_history(outcome):
    opened = transition(step(), 'accept', at=AT)
    submitted = transition(opened, 'submit', note=' 现场反馈 ', outcome=outcome, at=AT)
    returned = transition(submitted, 'reject', note='补充说明', at=AT)
    resubmitted = transition(returned, 'submit', note='已补齐', at=AT)
    approved = transition(resubmitted, 'approve', at=AT)
    actions = approved['history']
    assert [entry['action'] for entry in actions] == ['accept', 'submit', 'reject', 'submit', 'approve']
    assert actions[1]['outcome'] == outcome and actions[1]['note'] == '现场反馈'
    assert actions[-1]['note'] == '' and actions[-1]['to'] == '已完成'
    assert len(submitted['history']) == 2


def test_invalid_action_and_outcome():
    with pytest.raises(ValueError, match='未知任务动作'):
        transition(step(), 'invalid')
    with pytest.raises(ValueError, match='观察结果'):
        transition(step('进行中'), 'submit', note='反馈', outcome='错误')
    with pytest.raises(ValueError, match='观察结果'):
        transition(step(), 'accept', outcome='观察到')


def test_dependencies_require_all_predecessors_completed_in_same_plan():
    task = step(depends_on=['travel', 'prep'])
    travel = step('已完成', key='travel')
    prep = step('已完成', key='prep')
    for records in ([travel], [travel, dict(prep, plan_thread='other')],
                    [travel, dict(prep, status='已替代')]):
        with pytest.raises(ValueError, match='前置'):
            transition(task, 'accept', tasks=records)
    assert transition(task, 'accept', tasks=[travel, prep])['status'] == '进行中'


@pytest.mark.parametrize('clock', (120, 121))
def test_observation_window_ends_at_inclusive_accept_cutoff(clock):
    result = transition(step(), 'accept', clock=clock, at=AT)
    assert result['status'] == '已替代'
    assert result['history'][-1]['note'] == '任务窗口已结束，请重新筹划。'
    assert transition(step(mode='事件协同'), 'accept', clock=clock)['status'] == '进行中'


@pytest.mark.parametrize('status', STATUSES)
def test_overdue_is_derived_only_for_waiting_and_in_progress(status):
    task = step(status)
    assert not is_overdue(task, 119) and not is_overdue(task, 120)
    assert is_overdue(task, 121) == (status in ('待接单', '进行中'))
    assert 'overdue' not in task


def test_replacement_and_completed_group():
    accepted = step()
    result = replace(accepted, at=AT)
    assert result['history'][-1]['action'] == 'replace' and result['status'] == '已替代'
    assert tasks_resolved([step('已完成'), result])
    assert not tasks_resolved([])
    assert not tasks_resolved([step('无法完成'), result])
    with pytest.raises(ValueError, match='只能替代'):
        replace(result, at=AT)
