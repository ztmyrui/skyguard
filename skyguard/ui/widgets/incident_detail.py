"""Native incident evidence, review forms and lifecycle actions."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QMenu, QPlainTextEdit, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from ...incident import ACTION_LABELS, NOTE_PROMPTS, Conclusion, IncidentStatus
from ...theme import set_themed_icon
from ..presenters import event_sections, incident_badge, priority_badge
from .flight_result import Disclosure
from .common import ActionButton, Badge, Card, EmptyState, SectionHeader, SegmentedControl, label, scroll_contents

_ACTIONS = {
    IncidentStatus.NEW: ('accept', 'start', 'plan'),
    IncidentStatus.PENDING: ('plan', 'start', 'conclude'),
    IncidentStatus.VERIFYING: ('conclude', 'plan'),
    IncidentStatus.REVIEW: ('approve', 'return'),
    IncidentStatus.CLOSED: ('reopen',),
}
_DIRECT_CLOSE = {IncidentStatus.NEW, IncidentStatus.PENDING, IncidentStatus.VERIFYING}
_DISPLAY_ACTION = {**ACTION_LABELS, 'plan': '派人核验', 'approve': '复核通过并关闭'}
_DEMO_ACTIONS = (
    ('模拟位置中断', 'stale'), ('切换矛盾报告', 'conflict'), ('追加核实更新', 'refresh'),
)
_SECTION_HEADINGS = {
    'facts': ('观察记录', '事实', 'ok'),
    'estimates': ('推测', '估计', 'info'),
    'unknowns': ('待核验信息', '未知', 'unknown'),
}
_ROW_ICONS = {
    'ok': 'condition-ok', 'warn': 'condition-attention', 'danger': 'condition-attention',
    'unknown': 'help', 'demo': 'layers', 'info': 'dot',
}


def clear_widgets(layout):
    while layout.count():
        widget = layout.takeAt(0).widget()
        widget.hide()
        widget.deleteLater()


class AssessmentRow(QWidget):
    """Keep evidence labels legible and information tones explicit."""

    def __init__(self, title):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        if title:
            heading = label(title)
            heading.setFixedWidth(76)
            row.addWidget(heading, 0, Qt.AlignTop)
        self.symbol = QLabel()
        self.symbol.setFixedSize(14, 18)
        row.addWidget(self.symbol, 0, Qt.AlignTop)
        self.value = label('', 'assessmentValue')
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.value.setMinimumWidth(0)
        row.addWidget(self.value, 1)
        self.tone = None

    def update_value(self, text, tone):
        self.value.setText(text)
        if self.tone != tone:
            set_themed_icon(self.symbol, _ROW_ICONS[tone], tone, 13)
            self.value.setProperty('tone', tone)
            style = self.value.style()
            style.unpolish(self.value)
            style.polish(self.value)
            self.tone = tone


class AssessmentSection(Card):
    """Display a section with a persistent, keyboard-accessible disclosure."""

    def __init__(self, section):
        super().__init__()
        self.setObjectName('assessmentSection')
        self.body.setContentsMargins(8, 6, 8, 8)
        self.body.setSpacing(4)
        header = QHBoxLayout()
        header.setSpacing(8)
        caption, category, tone = _SECTION_HEADINGS.get(section.key, (section.title, '', 'info'))
        if section.key == 'next_steps':
            header.addWidget(SectionHeader(caption), 0)
        else:
            self.toggle = QToolButton()
            self.toggle.setObjectName('assessmentToggle')
            self.toggle.setText(caption)
            self.toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            self.toggle.setCheckable(True)
            self.toggle.setChecked(True)
            self.toggle.setCursor(Qt.PointingHandCursor)
            self.toggle.toggled.connect(self._expanded)
            header.addWidget(self.toggle, 0)
        if category:
            header.addWidget(Badge(category, tone), 0)
        header.addStretch()
        self.body.addLayout(header)
        self.content = QWidget()
        self.rows = QVBoxLayout(self.content)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(4)
        self.body.addWidget(self.content)
        self.fields = []
        self.labels = None
        if section.key != 'next_steps':
            self._expanded(True)
        self.update_rows(section.rows)

    def _expanded(self, expanded):
        set_themed_icon(self.toggle, 'chevron-down' if expanded else 'chevron-right')
        self.content.setVisible(expanded)

    def update_rows(self, rows):
        titles = [title for title, _, _ in rows]
        if titles != self.labels:
            clear_widgets(self.rows)
            self.fields = [AssessmentRow(title) for title in titles]
            for field in self.fields:
                self.rows.addWidget(field)
            self.labels = titles
        for field, (_, text, tone) in zip(self.fields, rows):
            field.update_value(text, tone)


class EvidencePanel(Card):
    """Show local attachments and confirm a candidate association in its row."""

    association_requested = Signal(str)
    attachment_requested = Signal()

    def __init__(self):
        super().__init__()
        self.body.addWidget(SectionHeader('证据与关联'))
        self.linked = label('')
        self.body.addWidget(self.linked)
        self.attachment = ActionButton('打开附件', self.attachment_requested.emit, 'link', 'quiet')
        self.attachment.setObjectName('attachmentLink')
        self.body.addWidget(self.attachment, 0, Qt.AlignLeft)
        self.candidates = QVBoxLayout()
        self.candidates.setSpacing(8)
        self.body.addLayout(self.candidates)
        self.error = label('', 'incidentError')
        self.body.addWidget(self.error)
        self.error.hide()
        self.signature = None
        self.rows = {}

    def render(self, view):
        candidates = tuple((row['id'], row['distance_m']) for row in view.get('evidence_candidates', ()))
        linked = tuple(view.get('linked_reports', ())) or ((view['linked_track'],) if view.get('linked_track') else ())
        attachment = view.get('attachment', '')
        signature = (view['id'], candidates, linked, attachment, view['status'])
        if signature == self.signature:
            return
        confirmed_track = view.get('linked_track')
        self.signature = signature
        self.setVisible(bool(candidates or linked or attachment))
        self.linked.setText('人工关联 · ' + '、'.join(linked))
        self.linked.setVisible(bool(linked))
        self.attachment.setVisible(bool(attachment))
        self.error.hide()
        clear_widgets(self.candidates)
        self.rows.clear()
        for track_id, distance in candidates:
            actionable = view['status'] != IncidentStatus.CLOSED and track_id != confirmed_track
            self._candidate(track_id, distance, actionable)

    def _candidate(self, track_id, distance, actionable):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        info = label(f'{track_id}\n距目击中心约 {distance} 米')
        row.addWidget(info, 1)
        if actionable:
            ask = ActionButton('确认关联', lambda: self._confirming(track_id, True), variant='quiet')
            yes = ActionButton('确认', lambda: self.association_requested.emit(track_id), variant='primary')
            no = ActionButton('取消', lambda: self._confirming(track_id, False), variant='quiet')
            for button in (ask, yes, no):
                row.addWidget(button)
            yes.hide()
            no.hide()
            self.rows[track_id] = (ask, yes, no)
        self.candidates.addWidget(widget)

    def _confirming(self, track_id, active):
        for candidate, (ask, yes, no) in self.rows.items():
            pending = active and candidate == track_id
            ask.setVisible(not pending)
            yes.setVisible(pending)
            no.setVisible(pending)
            if candidate == track_id:
                (yes if pending else ask).setFocus(Qt.OtherFocusReason)
        self.error.hide()


class IncidentDetail(Card):
    """Render one incident and emit requests for explicit user actions."""

    action_requested = Signal(str, str, str, object)
    planning_requested = Signal()
    demo_requested = Signal(str)
    association_requested = Signal(str)
    attachment_requested = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName('incidentDetail')
        self.current = None
        self.sections = {}
        self.form_action = ''
        self.empty = EmptyState('请选择事件', description='选择列表或地图中的事件查看研判。', glyph='map')
        self.body.addWidget(self.empty, 1)
        self.content = QWidget()
        detail = QVBoxLayout(self.content)
        detail.setContentsMargins(0, 0, 0, 0)
        detail.setSpacing(8)
        self._build_header(detail)
        self.scroll, overview = scroll_contents()
        self.quick = Card()
        overview.addWidget(self.quick)
        self.basis_toggle = Disclosure('更多依据')
        self.basis_toggle.row.addWidget(label('更多依据', 'checkTitle'), 1)
        self.basis_toggle.add_arrow()
        overview.addWidget(self.basis_toggle)
        more_body = QWidget()
        self.assessment = QVBoxLayout(more_body)
        self.assessment.setContentsMargins(0, 0, 0, 0)
        overview.addWidget(more_body)
        overview.addStretch()
        more_body.hide()
        self.basis_toggle.toggled.connect(more_body.setVisible)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        detail.addWidget(self.scroll, 1)
        self.evidence = EvidencePanel()
        self.evidence.association_requested.connect(self.association_requested.emit)
        self.evidence.attachment_requested.connect(self.attachment_requested.emit)
        self.assessment.addWidget(self.evidence)
        self.assessment.addStretch()
        self._build_footer(detail)
        self.body.addWidget(self.content, 1)
        self.content.hide()

    def _build_header(self, layout):
        self.name = label('', 'incidentTitle')
        layout.addWidget(self.name)
        header = QHBoxLayout()
        header.setSpacing(4)
        self.status = Badge('')
        self.priority = Badge('')
        self.demo = Badge('演练', 'demo')
        for badge in (self.status, self.priority, self.demo):
            header.addWidget(badge)
        header.addStretch()
        layout.addLayout(header)

    def _build_footer(self, layout):
        self.footer = QWidget()
        body = QVBoxLayout(self.footer)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        self._build_form(body)
        self.error = label('', 'incidentError')
        self.error.hide()
        body.addWidget(self.error)
        self.primary_row = QHBoxLayout()
        self.primary_row.setSpacing(4)
        self.secondary_row = QHBoxLayout()
        self.secondary_row.setSpacing(4)
        body.addLayout(self.primary_row)
        body.addLayout(self.secondary_row)
        layout.addWidget(self.footer)

    def _build_form(self, layout):
        self.form = Card()
        self.form.setObjectName('incidentForm')
        self.form.body.setContentsMargins(8, 8, 8, 8)
        self.form.body.setSpacing(4)
        self.form_title = SectionHeader('')
        self.form.body.addWidget(self.form_title)
        self.conclusion = SegmentedControl([item.value for item in Conclusion])
        self.conclusion.setAccessibleName('结论类型')
        self.form.body.addWidget(self.conclusion)
        self.note = QPlainTextEdit()
        self.note.setFixedHeight(60)
        self.note.setAccessibleName('事件操作说明')
        self.form.body.addWidget(self.note)
        self.form_error = label('', 'incidentError')
        self.form.body.addWidget(self.form_error)
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addStretch()
        self.cancel = ActionButton('取消', self.hide_form, variant='quiet')
        self.submit = ActionButton('确认', self._submit_form, variant='primary')
        row.addWidget(self.cancel)
        row.addWidget(self.submit)
        self.form.body.addLayout(row)
        layout.addWidget(self.form)
        self.form.hide()

    def render(self, view, clock, time_text):
        self.empty.setVisible(view is None)
        self.content.setVisible(view is not None)
        identity = (view['id'], view['status']) if view else None
        changed = self.current != identity
        self.current = identity
        if changed:
            self.hide_form()
            self.error.hide()
            self.scroll.verticalScrollBar().setValue(0)
        if not view:
            return
        self.name.setText(view['name'])
        self.status.set_value(*incident_badge(view))
        self.priority.set_value(*priority_badge(view))
        self.demo.setVisible(view['source'] == 'synthetic')
        sections = event_sections(view, clock, time_text)
        for section in sections:
            section.rows[:] = [row for row in section.rows if row[0] not in ('地面影响','涉及场地')]
        self._render_sections(sections)
        self.evidence.render(view)
        if changed:
            self._render_actions(view)

    def _render_sections(self, sections):
        for index, section in enumerate(sections):
            if section.key not in self.sections:
                card = AssessmentSection(section)
                self.sections[section.key] = card
                self.assessment.insertWidget(index, card)
            else:
                self.sections[section.key].update_rows(section.rows)

    def _render_actions(self, view):
        clear_widgets(self.primary_row)
        clear_widgets(self.secondary_row)
        status = view['status']
        primary, *secondary = _ACTIONS[status]
        self.buttons = {key: self._action_button(key, 'secondary' if key != primary else 'primary')
                        for key in (primary, *secondary)}
        self.primary_row.addWidget(self.buttons[primary], 1)
        for key in secondary:
            self.secondary_row.addWidget(self.buttons[key], 1)
        self.more = self._more_button(view)
        if self.more:
            self.primary_row.addWidget(self.more)

    def _action_button(self, action, variant):
        return ActionButton(_DISPLAY_ACTION[action], lambda: self._dispatch(action), variant=variant)

    def _more_button(self, view):
        simulated = view['source'] == 'synthetic' and view['status'] != IncidentStatus.CLOSED
        direct_close = view['status'] in _DIRECT_CLOSE
        if not simulated and not direct_close:
            return None
        button = ActionButton('更多', None, 'more', 'quiet')
        menu = QMenu(button)
        if simulated:
            for title, kind in _DEMO_ACTIONS:
                menu.addAction(title, lambda checked=False, value=kind: self.demo_requested.emit(value))
        if direct_close:
            if simulated:
                menu.addSeparator()
            menu.addAction('直接关闭', lambda: self._dispatch('force_close'))
        button.setMenu(menu)
        return button

    def _dispatch(self, action):
        if action == 'plan':
            self.planning_requested.emit()
        elif action in NOTE_PROMPTS:
            self.show_form(action)
        else:
            self.hide_form()
            self.action_requested.emit(self.current[0], action, '', None)

    def show_form(self, action):
        if action == self.form_action:
            self.note.setFocus(Qt.OtherFocusReason)
            return
        self.form_action = action
        self.form_title.setText(ACTION_LABELS[action])
        self.conclusion.setVisible(action == 'conclude')
        self.conclusion.setCurrentIndex(0)
        self.note.clear()
        self.note.setPlaceholderText(NOTE_PROMPTS[action])
        self.form_error.hide()
        self.error.hide()
        self.form.show()
        self.note.setFocus(Qt.OtherFocusReason)

    def hide_form(self):
        self.form_action = ''
        self.form.hide()

    def _submit_form(self):
        action = self.form_action
        note = self.note.toPlainText()
        if not note.strip():
            self.show_error(NOTE_PROMPTS[action])
            self.note.setFocus(Qt.OtherFocusReason)
            return
        conclusion = list(Conclusion)[self.conclusion.currentIndex()] if action == 'conclude' else None
        self.action_requested.emit(self.current[0], action, note, conclusion)

    def show_error(self, message):
        error = self.form_error if self.form_action else self.error
        error.setText(message)
        error.show()

