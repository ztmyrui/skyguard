"""Pure Python formatters for the existing desktop presentation."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..domain import clock_text
from ..incident import ACTION_LABELS, IncidentStatus
from ..task_flow import ACTION_LABELS as TASK_ACTION_LABELS

CHINA = timezone(timedelta(hours=8))


def flight_window(start, end, *, show_year=False):
    """Format the plan window in Beijing time, including an end date if needed."""
    start, end = start.astimezone(CHINA), end.astimezone(CHINA)
    date_format = '%Y-%m-%d' if show_year else '%m-%d'
    end_format = '%H:%M' if start.date() == end.date() else date_format + ' %H:%M'
    return f'{start.strftime(date_format)} {start:%H:%M}–{end.strftime(end_format)}'


def task_window(task, clock=clock_text):
    """Format the task window in absolute exercise time."""
    return f'{clock(task["start_s"])}–{clock(task["end_s"])}'


def duration_text(seconds):
    minutes, seconds = divmod(round(seconds), 60)
    return f'{minutes} 分 {seconds:02d} 秒' if minutes and seconds else (
        f'{minutes} 分钟' if minutes else f'{seconds} 秒')


SOURCE_LABELS = {'synthetic': '模拟事件', 'field_observation': '现场观察', 'authorized_log': '授权日志'}
QUALITY_TONES = {'较充分': 'ok', '信息有限': 'unknown', '信息陈旧': 'warn', '存在冲突': 'danger'}
STATUS_TONES = {
    IncidentStatus.NEW: 'info', IncidentStatus.PENDING: 'warn', IncidentStatus.VERIFYING: 'info',
    IncidentStatus.REVIEW: 'warn', IncidentStatus.CLOSED: 'ok',
}
STALE_SECONDS = 45


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    rows: list[tuple[str, str, str]]


def incident_badge(view):
    status = view['status']
    text = f'{status} · {view["conclusion"]}' if view.get('conclusion') else status
    return text, STATUS_TONES[status]


def priority_badge(view):
    priority = view['priority']
    return priority, 'danger' if priority == '优先核验' else 'info'


def _fact_rows(view, observed, age, time_text):
    source = view['source']
    rows = [
        ('数据来源', SOURCE_LABELS[source], 'demo' if source == 'synthetic' else 'info'),
        ('最后观测时刻', f'{time_text(observed)} · {age} 秒前', 'info'),
    ]
    if view['lat'] is not None:
        accuracy = view.get('observed_accuracy_m', view['accuracy_m'])
        rows.extend((
            ('最后已知位置', f'{view["lat"]:.6f}, {view["lon"]:.6f}', 'info'),
            ('位置误差', f'约 {accuracy:g} 米', 'info'),
        ))
    description = view['description']
    rows.append(('原始陈述', f'「{description}」' if description.strip() else '未填写',
                 'info' if description.strip() else 'unknown'))
    return rows


def _estimate_rows(view):
    prediction, speed, heading = view['prediction'], view['speed'], view.get('heading')
    impact = view['impact']
    rows = [
        ('短时预测', f'未来 {prediction[-1]["seconds"]} 秒 · 虚线与范围为估计', 'info')
        if prediction else ('短时预测', '暂无可靠估计', 'unknown'),
        ('速度与航向', f'雷达估计 {speed} m/s · {heading}°', 'info')
        if heading is not None and speed is not None else ('速度与航向', '暂无雷达估计', 'unknown'),
        ('地面影响', f'{impact:.1f} / 100（估计相对指数）', 'info')
        if impact is not None else ('地面影响', '待核实', 'unknown'),
    ]
    if impact is not None:
        sites = '、'.join(f'{item["name"]}（{item["index"]}）' for item in view['effects'])
        rows.append(('涉及场地', sites or '估计范围未与关注场地相交', 'info'))
    candidates = [row['id'] for row in view.get('evidence_candidates', ())]
    if candidates and not view.get('linked_track'):
        rows.append(('轨迹关联候选', f'{"、".join(candidates)} · 估计匹配，待人工核对', 'unknown'))
    for key, title in [('rule_hits', '规则提示'), ('plan_candidates', '候选计划')]:
        if view.get(key):
            rows.append((title, '估计匹配 · ' + '、'.join(view[key]), 'info'))
    return rows


def _next_steps(status, *, located, stale, accurate, conflict, tasks_resolved=False):
    lifecycle = {
        IncidentStatus.NEW: ('受理报告', 'info'), IncidentStatus.PENDING: ('安排或开始核验', 'info'),
        IncidentStatus.VERIFYING: ('核验完成后记录结论', 'info'),
        IncidentStatus.REVIEW: ('待复核：复核结论并关闭', 'warn'),
        IncidentStatus.CLOSED: ('收到新证据后可重新打开事件', 'info'),
    }
    first = ('该事件的任务已全部完成，可记录结论', 'ok') if tasks_resolved else lifecycle[status]
    rows = [('', *first)]
    if status == IncidentStatus.CLOSED:
        return rows
    if not located:
        rows.append(('', '位置未知：补充观察区域', 'unknown'))
    if conflict:
        rows.append(('', '存在矛盾报告：联络报告人核实', 'danger'))
    if stale:
        rows.append(('', '信息陈旧：补充近期观察', 'warn'))
    if located and not accurate:
        rows.append(('', '定位精度有限：核对位置及误差范围', 'warn'))
    return rows[:3]


def event_is_stale(view, clock):
    return clock - view.get('observed_s', clock - view['age']) > STALE_SECONDS


def event_sections(view, clock, time_text=clock_text):
    """Separate recorded evidence, estimates, unknowns and independent diagnostics."""
    observed = view.get('observed_s', clock - view['age'])
    age = max(0, clock - observed)
    status = view['status']
    located = view['lat'] is not None
    stale = event_is_stale(view, clock)
    accurate = located and view['accuracy_m'] <= 100
    conflict = view['conflict']
    unknown = [
        ('目标身份', '合作记录待核实' if view.get('cooperation') else '未知', 'unknown'),
        ('飞行授权', '未知', 'unknown'), ('高度', '未知', 'unknown'),
    ]
    if not located:
        unknown.append(('位置', '未知', 'unknown'))
    diagnostics = [
        ('时效', '陈旧' if stale else '较新', 'warn' if stale else 'ok'),
        ('定位精度', '较好' if accurate else '有限', 'ok' if accurate else 'warn' if located else 'unknown'),
        ('一致性', '存在矛盾报告' if conflict else '当前未见矛盾报告', 'danger' if conflict else 'ok'),
    ]
    return [
        Section('facts', '事实', _fact_rows(view, observed, round(age), time_text)),
        Section('estimates', '推测', _estimate_rows(view)),
        Section('unknowns', '未知', unknown),
        Section('diagnostics', '可信度诊断', diagnostics),
        Section('next_steps', '下一步建议', _next_steps(
            status, located=located, stale=stale, accurate=accurate, conflict=conflict,
            tasks_resolved=view.get('tasks_resolved', False))),
    ]


def audit_time(value):
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return str(value or '—')
    if moment.tzinfo:
        moment = moment.astimezone(CHINA)
    return moment.strftime('%m-%d %H:%M:%S')


def _audit_text(action, detail):
    if action == '事件状态' and {'from', 'to'} <= detail.keys():
        title = f'事件状态 · {detail["from"]} → {detail["to"]}'
        verb = ACTION_LABELS.get(detail.get('action'), '')
        return title, ' · '.join(part for part in (verb, detail.get('conclusion'), detail.get('note')) if part)
    if action == '人工确认方案' and 'index' in detail:
        return f'人工确认方案 · 方案 {detail["index"] + 1}', detail.get('event', '')
    if action in ('任务状态更新', '观察任务超时') and {'from', 'to'} <= detail.keys():
        verb = TASK_ACTION_LABELS.get(detail.get('action'), action)
        title = f'{verb} · {detail["from"]} → {detail["to"]}'
        return title, ' · '.join(part for part in (detail.get('outcome'), detail.get('note')) if part)
    if action == '人工关联目击与轨迹':
        return action, f'{detail.get("report", "")} → {detail.get("track", "")}'
    if action in ('生成候选方案', '导入报告') and 'count' in detail:
        unit = '个方案' if action == '生成候选方案' else '条报告'
        return action, f'{detail["count"]} {unit}'
    if action == '新增报告':
        return action, SOURCE_LABELS.get(detail.get('source'), '')
    if action == '演练信息变化':
        changes = {'stale': '模拟位置中断', 'conflict': '切换矛盾报告', 'refresh': '追加核实更新'}
        return action, changes.get(detail.get('kind'), '')
    if action in ('任务状态更新', '关闭事件'):
        return action, ' · '.join(detail[key] for key in ('status', 'note') if detail.get(key))
    brief = ' · '.join(f'{key} {value}' for key, value in detail.items() if isinstance(value, (str, int, float)))
    return action, brief[:160]


def audit_rows(records, event_id=None):
    """Format an audit timeline in repository order, optionally scoped to an event."""
    records = list(records)
    threads = {
        detail['thread']: detail['event'] for record in records
        if (detail := record['detail']).get('thread') and detail.get('event')
    }
    rows = []
    for record in records:
        detail = record['detail']
        related = {detail.get(key) for key in ('id', 'event', 'report', 'track')}
        thread = detail.get('thread') or str(detail.get('id', '')).rpartition('-')[0]
        related.update((threads.get(thread), *detail.get('events', ())))
        if event_id is None or event_id in related:
            title, text = _audit_text(record['action'], detail)
            rows.append((audit_time(record['at']), title, text or '—'))
    return rows


def operation_window_text(window, clock=clock_text):
    """Format the suggested aerial photography window."""
    return (f'建议作业时段 {task_window(window, clock)}\n'
            f'{window["purpose"]}\n{window["note"]}')


def dependency_names(task, names):
    return '、'.join(names.get(key, key) for key in task['depends_on']) or '—'


def plan_task_cells(task, names, clock=clock_text):
    return (task['name'], task['person'], task['post'] or '—', clock(task['start_s']),
            clock(task['end_s']), dependency_names(task, names), task['criterion'])
