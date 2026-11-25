"""Exhaustive coverage of incident status and action combinations."""
from itertools import product

import pytest

from skyguard.incident import Conclusion, IncidentStatus as Status, incident_record, transition

AT = '2026-11-20T00:00:00+00:00'
LEGAL = {
    (Status.NEW, 'accept'): Status.PENDING,
    (Status.NEW, 'start'): Status.VERIFYING,
    (Status.PENDING, 'start'): Status.VERIFYING,
    (Status.PENDING, 'conclude'): Status.REVIEW,
    (Status.VERIFYING, 'conclude'): Status.REVIEW,
    (Status.REVIEW, 'approve'): Status.CLOSED,
    (Status.REVIEW, 'return'): Status.VERIFYING,
    (Status.CLOSED, 'reopen'): Status.PENDING,
    (Status.NEW, 'force_close'): Status.CLOSED,
    (Status.PENDING, 'force_close'): Status.CLOSED,
    (Status.VERIFYING, 'force_close'): Status.CLOSED,
    (Status.VERIFYING, 'block'): Status.PENDING,
}
ACTIONS = ('accept', 'start', 'conclude', 'approve', 'return', 'reopen', 'force_close', 'block')


@pytest.mark.parametrize(('start', 'action'), list(product(Status, ACTIONS)))
def test_transition_matrix_and_history(start, action):
    record = incident_record(start)
    target = LEGAL.get((start, action))
    arguments = dict(note=' 核验记录 ', conclusion=Conclusion.CLARIFIED, at=AT)
    if target is None:
        with pytest.raises(ValueError, match=f'{start}状态下不能'):
            transition(record, action, **arguments)
        assert not record['history']
        return

    result = transition(record, action, **arguments)
    assert result['status'] == target
    assert result['history'] == [
        {'at': AT, 'action': action, 'from': start.value, 'to': target.value, 'note': '核验记录'},
    ]
    assert result['conclusion'] == (Conclusion.CLARIFIED if action == 'conclude' else None)
    assert record == incident_record(start)


@pytest.mark.parametrize(('status', 'action', 'error'), [
    (Status.PENDING, 'conclude', '请填写核验结论'),
    (Status.REVIEW, 'return', '请填写退回原因'),
    (Status.CLOSED, 'reopen', '请填写新证据或重新打开的原因'),
    (Status.VERIFYING, 'force_close', '请填写关闭说明'),
    (Status.VERIFYING, 'block', '请填写核验受阻原因'),
])
@pytest.mark.parametrize('note', ['', ' \n\t '])
def test_actions_requiring_an_explanation(status, action, error, note):
    with pytest.raises(ValueError, match=error):
        transition(incident_record(status), action, note=note, conclusion=Conclusion.CLARIFIED, at=AT)


@pytest.mark.parametrize(('status', 'action', 'note'), [
    (Status.REVIEW, 'approve', ''),
    (Status.NEW, 'force_close', '重复报告'),
    (Status.PENDING, 'force_close', '重复报告'),
    (Status.VERIFYING, 'force_close', '重复报告'),
])
def test_active_tasks_prevent_closing(status, action, note):
    with pytest.raises(ValueError, match='仍有未完成任务'):
        transition(incident_record(status), action, note=note, open_tasks=2, at=AT)


@pytest.mark.parametrize('value', [None, '', '待处理'])
def test_conclusion_must_have_a_valid_type(value):
    with pytest.raises(ValueError, match='请选择结论类型'):
        transition(incident_record(Status.VERIFYING), 'conclude', note='已核实', conclusion=value, at=AT)


def test_conclusion_lives_through_approval_and_is_cleared_on_return_or_reopen():
    record = incident_record(Status.PENDING)
    reviewed = transition(record, 'conclude', note='完成联络', conclusion=Conclusion.COORDINATED, at=AT)
    assert reviewed['conclusion'] == Conclusion.COORDINATED
    closed = transition(reviewed, 'approve', note='', at=AT)
    returned = transition(reviewed, 'return', note='补齐记录', at=AT)
    reopened = transition(closed, 'reopen', note='新增证据', at=AT)
    assert closed['conclusion'] == Conclusion.COORDINATED
    assert returned['conclusion'] is reopened['conclusion'] is None
    assert [item['action'] for item in reopened['history']] == ['conclude', 'approve', 'reopen']
    assert [item['action'] for item in reviewed['history']] == ['conclude']


def test_unknown_action_has_a_chinese_error():
    with pytest.raises(ValueError, match='未知事件动作'):
        transition(incident_record(), 'invalid', note='', at=AT)

