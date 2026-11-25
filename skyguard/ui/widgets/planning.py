"""Step progress and side-by-side metrics for verification planning."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QWidget

from ...planning import MODES
from ..presenters import duration_text
from .common import ActionButton, Badge, icon_label

STEPS = ('选择事件', '设定条件', '比较方案', '确认下发')
METRICS = (
    ('完成时刻', 'finish_s', min), ('总时长', 'duration_s', min), ('人数', 'people', min),
    ('步行时间', 'walk_s', min), ('视野覆盖', 'coverage', max),
    ('能力匹配', 'skill_mismatch', min), ('活动重叠', 'activity_overlap_s', min),
    ('相对原计划改动', 'changes', min), ('待核对项数', 'unresolved', min),
)


class StepIndicator(QFrame):
    """Display the current planning stage and completed stages."""

    def __init__(self):
        super().__init__()
        self.setObjectName('planningSteps')
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 2, 12, 2)
        row.setSpacing(8)
        self.steps = []
        for index, title in enumerate(STEPS):
            if index:
                line = QFrame()
                line.setObjectName('planningStepDivider')
                line.setFixedHeight(1)
                row.addWidget(line, 1)
            item = Badge(f'{index + 1}  {title}', 'unknown')
            row.addWidget(item)
            self.steps.append(item)
        self.set_step(0)

    def set_step(self, current):
        for index, (title, badge) in enumerate(zip(STEPS, self.steps)):
            complete = index < current
            tone = 'ok' if complete else 'info' if index == current else 'unknown'
            badge.set_value(f'{"✓" if complete else index + 1}  {title}', tone)
            badge.setAccessibleName(f'{title} · {"已完成" if complete else "当前" if index == current else "待处理"}')


def _metric_text(key, value, clock):
    if value is None:
        return '—（首次筹划）' if key == 'changes' else '—'
    if key == 'finish_s':
        return clock(value)
    if key in ('duration_s', 'walk_s', 'activity_overlap_s'):
        return duration_text(value)
    if key == 'coverage':
        return f'{value:.0%}'
    if key == 'skill_mismatch':
        return f'{value} 项不匹配' if value else '全部匹配'
    return str(value)


class ComparisonTable(QWidget):
    """Compare up to three candidates with keyboard-accessible column headers."""

    selected = Signal(int)

    def __init__(self):
        super().__init__()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(2)
        self.headers = []
        self.columns = []

    def set_plans(self, plans, clock):
        while self.grid.count():
            widget = self.grid.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()
        self.headers.clear()
        self.columns.clear()
        for column in range(4):
            self.grid.setColumnStretch(column, int(1 <= column <= len(plans)))
        if not plans:
            return
        self._headers(plans)
        for row, (title, key, best) in enumerate(METRICS, start=2):
            self._metric_row(row, title, key, best, plans, clock)

    def _headers(self, plans):
        for column, plan in enumerate(plans, start=1):
            button = ActionButton(plan['title'], None)
            button.setObjectName('candidateHeader')
            button.setCheckable(True)
            button.setMinimumHeight(28)
            button.clicked.connect(lambda _, selected=column - 1: self.selected.emit(selected))
            self.grid.addWidget(button, 0, column)
            self.headers.append(button)
            self.columns.append([])
            if plan['snapshot']['params']['preference'] in plan['preference_fit']:
                badge = Badge('符合所选偏好', 'ok')
                badge.setObjectName('preferredCandidate')
                self.grid.addWidget(badge, 1, column)

    def _metric_row(self, row, title, key, best, plans, clock):
        name = QLabel(title)
        name.setObjectName('comparisonMetric')
        self.grid.addWidget(name, row, 0)
        values = [len(p['unresolved']) if key == 'unresolved' else p['metrics'][key] for p in plans]
        applicable = [v for v in values if v is not None]
        optimum = best(applicable) if len(set(applicable)) > 1 else None
        for column, value in enumerate(values, start=1):
            plan = plans[column - 1]
            unknown = key == 'coverage' and value is None and plan['mode'] == MODES[0]
            text = '未知（超出预测）' if unknown else _metric_text(key, value, clock)
            cell = QLabel(text)
            cell.setObjectName('comparisonValue')
            cell.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            cell.setAlignment(Qt.AlignCenter)
            first_plan = key == 'changes' and value is None
            cell.setWordWrap(unknown or first_plan)
            if unknown:
                cell.setProperty('tone', 'unknown')
            cell.setProperty('best', value is not None and value == optimum)
            cell.setToolTip(f'{plan["title"]} · {title} · {text}')
            self.grid.addWidget(cell, row, column)
            self.columns[column - 1].append(cell)

    def select(self, index):
        for column, (header, cells) in enumerate(zip(self.headers, self.columns)):
            chosen = index == column
            header.setChecked(chosen)
            for cell in cells:
                if bool(cell.property('chosen')) != chosen:
                    cell.setProperty('chosen', chosen)
                    cell.style().unpolish(cell)
                    cell.style().polish(cell)


def status_line(layout, text, tone='warn', glyph='condition-attention'):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    row.addWidget(icon_label(glyph, tone, 14), 0, Qt.AlignTop)
    detail = QLabel(text)
    detail.setObjectName('caption')
    detail.setTextFormat(Qt.PlainText)
    detail.setWordWrap(True)
    row.addWidget(detail, 1)
    layout.addLayout(row)
