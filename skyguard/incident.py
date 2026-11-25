"""Incident lifecycle rules and JSON-compatible history records."""
from enum import StrEnum


class IncidentStatus(StrEnum):
    NEW = '新报告'
    PENDING = '待核验'
    VERIFYING = '核验中'
    REVIEW = '待复核'
    CLOSED = '已关闭'


class Conclusion(StrEnum):
    CLARIFIED = '已澄清'
    COORDINATED = '已协同'


ACTION_LABELS = {
    'accept': '受理', 'start': '开始核验', 'conclude': '记录结论',
    'approve': '复核通过', 'return': '退回', 'reopen': '重新打开',
    'force_close': '直接关闭', 'block': '核验受阻',
}

_S = IncidentStatus
_TRANSITIONS = {
    'accept': ({_S.NEW}, _S.PENDING),
    'start': ({_S.NEW, _S.PENDING}, _S.VERIFYING),
    'conclude': ({_S.PENDING, _S.VERIFYING}, _S.REVIEW),
    'approve': ({_S.REVIEW}, _S.CLOSED),
    'return': ({_S.REVIEW}, _S.VERIFYING),
    'reopen': ({_S.CLOSED}, _S.PENDING),
    'force_close': ({_S.NEW, _S.PENDING, _S.VERIFYING}, _S.CLOSED),
    'block': ({_S.VERIFYING}, _S.PENDING),
}
NOTE_PROMPTS = {
    'conclude': '请填写核验结论', 'return': '请填写退回原因',
    'reopen': '请填写新证据或重新打开的原因', 'force_close': '请填写关闭说明',
    'block': '请填写核验受阻原因',
}


def incident_record(status=IncidentStatus.NEW):
    """Create a record with no conclusion or transitions yet."""
    return {'status': str(status), 'conclusion': None, 'history': []}


def transition(record, action, *, note, conclusion=None, open_tasks=0, at=None):
    """Return the resulting record using a timestamp supplied by the caller."""
    if action not in _TRANSITIONS:
        raise ValueError('未知事件动作')
    status = record['status']
    allowed, target = _TRANSITIONS[action]
    if status not in allowed:
        raise ValueError(f'{status}状态下不能{ACTION_LABELS[action]}')
    if not isinstance(note, str):
        raise ValueError('说明应为文本')
    note = note.strip()
    if action in NOTE_PROMPTS and not note:
        raise ValueError(NOTE_PROMPTS[action])
    if action in ('approve', 'force_close') and open_tasks != 0:
        raise ValueError('仍有未完成任务')

    result = record['conclusion']
    if action == 'conclude':
        if conclusion not in (Conclusion.CLARIFIED, Conclusion.COORDINATED):
            raise ValueError('请选择结论类型：已澄清或已协同')
        result = str(conclusion)
    elif action in ('return', 'reopen'):
        result = None
    entry = {'at': at, 'action': action, 'from': status, 'to': str(target), 'note': note}
    return {'status': str(target), 'conclusion': result, 'history': [*record['history'], entry]}
