"""Tests for framework-free monitoring and time display models."""
import pytest

from skyguard.domain import Observation, demo, snapshot
from skyguard.incident import IncidentStatus
from skyguard.ui.presenters import audit_rows, clock_text, event_sections, task_window


def test_clock_text_formats_exercise_time():
    assert clock_text(0) == '15:20:00'
    assert clock_text(65.9) == '15:21:05'
    assert clock_text(1800) == '15:50:00'


def test_task_window_formats_absolute_seconds():
    assert task_window({'start_s': 65, 'end_s': 75}) == '15:21:05–15:21:15'


def test_order_unknown_position_and_pending_impact():
    scene = demo()
    observation = Observation('report', '模拟目击', None, None, description='<原始陈述>')
    view = {**snapshot(scene, observation), 'status': IncidentStatus.NEW}
    sections = event_sections(view, scene.clock)
    assert [section.title for section in sections] == ['事实', '推测', '未知', '可信度诊断', '下一步建议']
    assert ('地面影响', '待核实', 'unknown') in sections[1].rows
    assert '无影响' not in str(sections[1].rows)
    location = [(section.key, row) for section in sections for row in section.rows if '位置' in row[0]]
    assert location == [('unknowns', ('位置', '未知', 'unknown'))]
    assert ('原始陈述', '「<原始陈述>」', 'info') in sections[0].rows


def test_simulated_event_is_always_described_as_simulated():
    scene = demo()
    view = {**snapshot(scene, scene.observations[0], radar=True), 'status': IncidentStatus.REVIEW}
    sections = event_sections(view, scene.clock)
    assert ('数据来源', '模拟事件', 'demo') in sections[0].rows
    assert ('目标身份', '合作记录待核实', 'unknown') in sections[2].rows
    text = str(sections)
    assert '实际' not in text and '已确认' not in text
    assert '虚线与范围为估计' in text and '雷达估计' in text


@pytest.mark.parametrize(('age', 'accuracy', 'stale_tone', 'accuracy_tone'), [
    (45, 100, 'ok', 'ok'),
    (46, 101, 'warn', 'warn'),
    (46, 100, 'warn', 'ok'),
])
def test_diagnostics_keep_the_three_tones_separate(age, accuracy, stale_tone, accuracy_tone):
    scene = demo()
    data = snapshot(scene, scene.observations[0], radar=True)
    view = dict(data, status=IncidentStatus.REVIEW, age=age, observed_s=0, accuracy_m=accuracy, conflict=True)
    diagnostics, steps = event_sections(view, age)[3:]
    assert [(row[0], row[2]) for row in diagnostics.rows] == [
        ('时效', stale_tone), ('定位精度', accuracy_tone), ('一致性', 'danger'),
    ]
    assert 1 <= len(steps.rows) <= 3
    assert any('联络报告人' in row[1] for row in steps.rows)
    assert any('复核结论' in row[1] for row in steps.rows)


def test_audit_rows_format_status_plans_associations_and_unknown_actions():
    instant = '2026-11-20T03:12:00+00:00'
    records = [
        {'at': instant, 'action': '事件状态', 'detail': {
            'id': 'obs-b', 'action': 'start', 'from': '待核验', 'to': '核验中', 'note': '交班后开始',
        }},
        {'at': instant, 'action': '人工确认方案', 'detail': {'index': 0, 'event': 'obs-a'}},
        {'at': instant, 'action': '人工关联目击与轨迹', 'detail': {'report': 'obs-b', 'track': 'obs-a'}},
        {'at': '旧时间', 'action': '维护操作', 'detail': {'reason': '补齐记录', 'nested': {'value': 8}}},
    ]
    assert audit_rows(records) == [
        ('11-20 11:12:00', '事件状态 · 待核验 → 核验中', '开始核验 · 交班后开始'),
        ('11-20 11:12:00', '人工确认方案 · 方案 1', 'obs-a'),
        ('11-20 11:12:00', '人工关联目击与轨迹', 'obs-b → obs-a'),
        ('旧时间', '维护操作', 'reason 补齐记录'),
    ]
    assert audit_rows(records, 'obs-b') == [audit_rows(records)[index] for index in (0, 2)]

