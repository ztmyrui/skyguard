"""Duty task queue, inline execution controls and task history."""
from collections import defaultdict

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QPushButton, QSizePolicy, QTextEdit, QVBoxLayout, QWidget

from ...task_flow import ACTION_LABELS, ENDED, NOTE_PROMPTS, OUTCOMES, dependencies, is_overdue
from ..presenters import STATUS_TONES, audit_time, task_window
from ..widgets.common import ActionButton, Badge, Card, EmptyState, SectionHeader, SegmentedControl, label, scroll_contents
from .base import DutyPage

FILTERS = ('待接单', '进行中', '待复核', '逾期', '已完成', '已结束', '全部')
TONES = {'待接单': 'warn', '进行中': 'info', '待复核': 'demo', '已完成': 'ok',
         '无法完成': 'danger', '已替代': 'unknown', '已取消': 'unknown'}
OPERATIONS = {'待接单': ('accept', 'unable'), '进行中': ('submit', 'unable'), '待复核': ('approve', 'reject')}


def _clear(layout):
    while layout.count():
        widget = layout.takeAt(0).widget()
        if widget:
            widget.hide()
            widget.deleteLater()


def _matches(task, scope, clock):
    if scope == '逾期':
        return is_overdue(task, clock)
    if scope == '已结束':
        return task['status'] in ENDED
    return scope in ('全部', task['status'])


def _predecessors(task, tasks):
    return '、'.join(
        other['name'] if other and other['status'] == '已完成' else
        '等待：' + (other['name'] if other else key)
        for key, other in dependencies(task, tasks)
    ) or '无'


def _task_heading(layout, task, clock):
    row = QHBoxLayout()
    row.addWidget(label(task['name'], 'section'), 1)
    row.addWidget(Badge(task['status'], TONES[task['status']]))
    if is_overdue(task, clock):
        row.addWidget(Badge('逾期', 'danger'))
    layout.addLayout(row)


class TaskCard(QPushButton):
    """Select a task with a keyboard-accessible card."""
    def __init__(self, task, scene, selected, callback):
        super().__init__()
        self.setObjectName('taskCard')
        self.setCheckable(True)
        self.setChecked(selected)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.setAccessibleName(f'{task["name"]} · {task["status"]}')
        body = QVBoxLayout(self)
        body.setContentsMargins(12, 8, 12, 8)
        body.setSpacing(4)
        _task_heading(body, task, scene.clock)
        body.addWidget(label(task['purpose'], 'bodyText'))
        body.addWidget(label(f'{task["person"]} · {task.get("post") or task.get("role") or "未指定岗位"}'))
        body.addWidget(label(task_window(task, scene.clock_text)))
        body.addWidget(label(f'前置  {_predecessors(task, scene.tasks)}'))
        for child in self.findChildren(QWidget):
            child.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.clicked.connect(lambda: callback(task['id']))

    def sizeHint(self):
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize()

    def heightForWidth(self, width):
        return self.layout().heightForWidth(width)


class TasksPage(DutyPage):
    """Filter work, select a step and execute it in place."""
    title = '任务中心'
    situation_requested = Signal(str)
    planning_requested = Signal(str)

    def __init__(self, context, compact=False):
        super().__init__(context)
        self.compact = compact
        self.event_id = None
        self.selected_id = None
        self.cards = {}
        self.completion_buttons = {}
        self._list_key = None
        self._detail_key = None
        self._event_options = None
        columns = QVBoxLayout() if compact else QHBoxLayout()
        columns.setSpacing(8)
        queue = self._queue()
        filters = self._filters()
        queue.body.insertWidget(1, filters)
        filters.setVisible(not compact)
        columns.addWidget(queue, 1)
        self.detail = Card()
        self.detail.setMinimumWidth(235 if compact else 410)
        if not compact:
            self.detail.setMaximumWidth(470)
        self.detail_scroll, self.details = scroll_contents()
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.detail.body.addWidget(self.detail_scroll, 1)
        self.error = label('', 'inlineError')
        self.error.hide()
        self.detail.body.addWidget(self.error)
        self.form = QWidget()
        self.form_body = QVBoxLayout(self.form)
        self.form_body.setContentsMargins(0, 0, 0, 0)
        self.form_body.setSpacing(4)
        self.detail.body.addWidget(self.form)
        self.form.hide()
        self.operations = QHBoxLayout()
        self.detail.body.addLayout(self.operations)
        columns.addWidget(self.detail, 1 if self.compact else 0)
        self.body.addLayout(columns, 1)

    def actions(self):
        return ()

    def _filters(self):
        card = Card()
        row = QHBoxLayout()
        if self.compact:
            card.setVisible(False)
        self.filter_buttons = {}
        for scope in FILTERS:
            button = ActionButton(scope, lambda scope=scope: self._select_scope(scope), variant='quiet')
            button.setProperty('taskFilter', True)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            self.filter_buttons[scope] = button
            row.addWidget(button)
        self.scope = '全部'
        self.filter_buttons[self.scope].setChecked(True)
        card.body.addLayout(row)
        self.event_filter = QComboBox()
        self.event_filter.setAccessibleName('关联事件筛选')
        self.event_filter.currentIndexChanged.connect(lambda _: self.refresh())
        card.body.addWidget(self.event_filter)
        return card

    def _queue(self):
        card = Card()
        card.setMinimumWidth(235 if self.compact else 420)
        card.body.addWidget(SectionHeader('事件任务'))
        self.queue, self.rows = scroll_contents()
        self.queue.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.empty = EmptyState('暂无任务', description='在事件详情中派人核验。', glyph='tasks')
        self.no_matches = EmptyState('没有匹配的任务', description='可调整状态或事件筛选。', glyph='tasks')
        space = QWidget()
        available = QVBoxLayout(space)
        available.setContentsMargins(0,0,0,0)
        for widget in (self.queue, self.empty, self.no_matches):
            available.addWidget(widget, 1)
        card.body.addWidget(space, 1)
        return card

    def _select_scope(self, scope):
        self.scope = scope
        self.filter_buttons[scope].setChecked(True)
        self.refresh()

    def _sync_events(self, scene):
        names = {event.id: event.name for event in scene.observations}
        options = [(eid, names.get(eid, eid)) for eid in dict.fromkeys(t['event_id'] for t in scene.tasks)]
        if options != self._event_options:
            selected = self.event_filter.currentData()
            with QSignalBlocker(self.event_filter):
                self.event_filter.clear()
                self.event_filter.addItem('全部事件', None)
                for event_id, name in options:
                    self.event_filter.addItem(name, event_id)
                self.event_filter.setCurrentIndex(max(0, self.event_filter.findData(selected)))
            self._event_options = options
        return names

    def select_task(self, task_id):
        self.selected_id = task_id
        self.refresh()

    def refresh(self):
        service = self.context.service
        scene = service.scene
        names = self._sync_events(scene)
        event_id = self.event_id if self.compact else self.event_filter.currentData()
        in_scope = [t for t in scene.tasks if event_id is None or t['event_id'] == event_id]
        for scope, button in self.filter_buttons.items():
            count = sum(_matches(task, scope, scene.clock) for task in in_scope)
            button.setText(f'{scope}   {count}')
        visible = [t for t in in_scope if _matches(t, self.scope, scene.clock)]
        chosen = next((task for task in visible if task['id'] == self.selected_id), None)
        if chosen is None:
            chosen = visible[0] if visible else None
        self.selected_id = chosen['id'] if chosen else None
        key = (id(scene), scene.revision, tuple((t['id'], is_overdue(t, scene.clock)) for t in visible))
        if key != self._list_key:
            self._list_key = key
            self._render_queue(visible, names, scene)
        for task_id, card in self.cards.items():
            card.setChecked(task_id == self.selected_id)
        self.queue.setVisible(bool(visible))
        self.empty.setVisible(not in_scope)
        self.no_matches.setVisible(bool(in_scope) and not visible)
        self.detail.setVisible(bool(visible))
        self._show_detail(chosen, names, scene)

    def _render_queue(self, visible, names, scene):
        _clear(self.rows)
        self.cards.clear()
        self.completion_buttons.clear()
        groups = defaultdict(list)
        for task in visible:
            groups[task['event_id']].append(task)
        service = self.context.service
        for eid, tasks in groups.items():
            if service.event_tasks_resolved(eid):
                button = ActionButton('任务已全部完成 · 前往态势监控记录结论',
                                      lambda eid=eid: self.situation_requested.emit(eid), 'check', 'quiet')
                button.setProperty('taskResolved', True)
                self.rows.addWidget(button)
                self.completion_buttons[eid] = button
            for task in tasks:
                card = TaskCard(task, scene, task['id'] == self.selected_id, self.select_task)
                self.cards[task['id']] = card
                self.rows.addWidget(card)
        self.rows.addStretch()

    def _show_detail(self, task, names, scene):
        key = (id(scene), scene.revision, self.selected_id, bool(task and is_overdue(task, scene.clock)))
        if key == self._detail_key:
            return
        previous_task = self._detail_key[2] if self._detail_key else None
        self._detail_key = key
        _clear(self.details)
        if previous_task != self.selected_id:
            self._close_form()
            self.detail_scroll.verticalScrollBar().setValue(0)
        if task is None:
            self.details.addWidget(EmptyState('选择任务查看详情', glyph='tasks'), 1)
        else:
            self._render_detail(task, names, scene)
        self._render_operations(task)

    def _render_detail(self, task, names, scene):
        heading = QWidget()
        box = QVBoxLayout(heading)
        box.setContentsMargins(0, 0, 0, 0)
        _task_heading(box, task, scene.clock)
        self.details.addWidget(heading)
        for title, text in (
            ('做什么', task['purpose']), ('为什么', f'关联事件 · {names[task["event_id"]]}'),
            ('完成标准', task['criterion']), ('前置步骤', _predecessors(task, scene.tasks)),
        ):
            self.details.addWidget(SectionHeader(title))
            self.details.addWidget(label(text, 'bodyText'))
        self._render_records(task)
        self.details.addStretch()

    def _render_records(self, task):
        history = task.get('history', [])
        for title, actions in (('执行反馈', ('submit', 'unable')), ('复核记录', ('approve', 'reject'))):
            entries = [entry for entry in history if entry['action'] in actions]
            entry = entries[-1] if entries else None
            text = (' · '.join(part for part in (ACTION_LABELS[entry['action']], entry['outcome'], entry['note'])
                               if part) if entry else '暂无记录')
            if not entry and title == '执行反馈' and not history and task.get('feedback'):
                text = task['feedback']
            self.details.addWidget(SectionHeader(title))
            self.details.addWidget(label(text))
        self.details.addWidget(SectionHeader('任务时间线 · 北京时间'))
        if not history:
            self.details.addWidget(label('暂无任务动作'))
        for entry in reversed(history):
            title = ACTION_LABELS.get(entry['action'], entry['action'])
            self.details.addWidget(label(
                f'{audit_time(entry["at"])}  ·  {title}  ·  {entry["from"]} → {entry["to"]}',
            ))
            description = ' · '.join(part for part in (entry['outcome'], entry['note']) if part)
            if description:
                self.details.addWidget(label(description, 'bodyText'))

    def _render_operations(self, task):
        _clear(self.operations)
        self.action_buttons = {}
        self.operations.addStretch()
        if task is None:
            return
        tid, status = task['id'], task['status']
        if status == '待接单':
            self.operations.addWidget(ActionButton(
                '换人重新筹划', lambda: self.planning_requested.emit(task['event_id']), variant='quiet',
            ))
        for index, action in enumerate(OPERATIONS.get(status, ())):
            button = ActionButton(ACTION_LABELS[action], lambda a=action: self._choose_action(tid, a),
                                  variant='primary' if index == 0 else 'secondary')
            self.operations.addWidget(button)
            self.action_buttons[action] = button
        for widget in self.action_buttons.values():
            widget.setVisible(self.form.isHidden())

    def _choose_action(self, tid, action):
        if action == 'accept':
            self._commit(tid, action)
            return
        self._close_form()
        title = NOTE_PROMPTS.get(action, '复核意见（可选）')
        self.form_body.addWidget(SectionHeader(ACTION_LABELS[action]))
        self.note = QTextEdit()
        self.note.setObjectName('inlineFeedback')
        self.note.setAccessibleName(title)
        self.note.setPlaceholderText(title)
        self.note.setFixedHeight(72)
        self.note.textChanged.connect(self.error.hide)
        self.form_body.addWidget(self.note)
        self.outcome = None
        if action == 'submit':
            self.outcome = SegmentedControl(('未选择', *OUTCOMES))
            self.outcome.setObjectName('taskOutcome')
            self.outcome.setAccessibleName('观察结果（可选）')
            self.form_body.addWidget(self.outcome)
        controls = QWidget()
        row = QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch()
        row.addWidget(ActionButton('取消', self._cancel_form, variant='quiet'))
        self.form_submit = ActionButton(ACTION_LABELS[action], lambda: self._submit(tid, action), variant='primary')
        row.addWidget(self.form_submit)
        self.form_body.addWidget(controls)
        self.form.show()
        for widget in self.action_buttons.values():
            widget.hide()
        self.note.setFocus()

    def _submit(self, tid, action):
        outcome = self.outcome.currentIndex() if self.outcome else 0
        self._commit(tid, action, self.note.toPlainText(), OUTCOMES[outcome - 1] if outcome else None)

    def _close_form(self):
        self.form.hide()
        self.error.hide()
        _clear(self.form_body)

    def _cancel_form(self):
        self._close_form()
        for widget in self.action_buttons.values():
            widget.show()

    def _commit(self, tid, action, note='', outcome=None):
        if self.context.busy:
            return
        try:
            task = self.context.service.task_action(tid, action, note, outcome)
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc))
            self.error.show()
            return
        self._close_form()
        if not _matches(task, self.scope, self.context.service.scene.clock):
            self._select_scope('全部')
        self.refresh()
        self.context.status.emit(f'{ACTION_LABELS[action]} · {task["status"]}', 6000)
