"""Verification planning conditions, comparison and execution preview."""
from math import cos, pi, sin

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QMessageBox, QStackedWidget,
    QSizePolicy, QTabWidget, QVBoxLayout,
)

from ...domain import advance
from ...planning import MODES, PREFERENCES, parameters, reservations, staff_records
from ..presenters import duration_text, incident_badge, operation_window_text, plan_task_cells
from ..widgets.common import (
    ActionButton, Badge, Card, EmptyState, SectionHeader, SegmentedControl, add_row, label,
    scroll_contents, table,
)
from ..widgets.dialogs import ActivityDialog, OperationDialog
from ..widgets.gantt import Gantt
from ..widgets.planning import ComparisonTable, StepIndicator, status_line
from .base import DutyMapPage

OBSERVATION_MINUTES = (1, 3, 5)
CHANGE_TONES = {'新增': 'ok', '改期': 'warn', '换人': 'info', '取消': 'danger', '不变': 'unknown'}


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.layout() is not None:
            _clear(item.layout())
            item.layout().deleteLater()
        elif item.widget() is not None:
            item.widget().hide()
            item.widget().deleteLater()


def _badge_row(layout, title, value, tone):
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(Badge(title, tone), 0, Qt.AlignTop)
    row.addWidget(label(value), 1)
    layout.addLayout(row)


class PlanningPage(DutyMapPage):
    """Compare verification arrangements and confirm one local task batch."""

    title = '核验筹划'
    shows_posts = True
    tasks_requested = Signal()
    pause_requested = Signal()

    def __init__(self, context):
        super().__init__(context)
        self._event_items = None
        self._staff_key = None
        self._activity_key = None
        self._rendered_pending = None
        self._rendered_detail = None
        self._rendered_status = None
        self._chosen_people = {}
        self.index = 0
        self.steps = StepIndicator()
        self.body.addWidget(self.steps)
        self._build_tabs()
        self.columns = self.build_columns(self._conditions_panel(), self._plan_panel(), self.tabs,
                                          tabs_height=220, tabs_minimum=220)
        self._restore_conditions()
        self.context.busy_changed.connect(lambda _: self.refresh_plans())

    def _restore_conditions(self):
        pending = self.service.pending
        if not pending or not pending.get('snapshot'):
            return
        params = pending['snapshot']['params']
        roster = params['selected_people']
        if roster is not None:
            self._chosen_people = {p.id: p.id in roster for p in self.scene.people}
        with QSignalBlocker(self.plan_mode), QSignalBlocker(self.duration), QSignalBlocker(self.preference):
            self.plan_mode.setCurrentIndex(MODES.index(params['mode']))
            self.duration.setCurrentIndex(OBSERVATION_MINUTES.index(params['observation_minutes'] or 3))
            self.preference.setCurrentIndex(PREFERENCES.index(params['preference']))

    @property
    def service(self):
        return self.context.service

    @property
    def scene(self):
        return self.service.scene

    def _build_tabs(self):
        self.tabs = QTabWidget()
        self.gantt = Gantt()
        self.schedule = QStackedWidget()
        self.schedule.addWidget(EmptyState('暂无时间安排', description='生成候选方案后查看人员时间线。'))
        self.schedule.addWidget(self.gantt)
        self.plan_table = table(['步骤', '负责人', '岗位', '开始', '结束', '前置', '完成标准'])
        self.matrix = table(['步骤'])
        trace_scroll, self.trace = scroll_contents()
        self.trace.setContentsMargins(12, 12, 12, 12)
        for widget, title in ((self.schedule, '时间安排'), (self.plan_table, '执行序列'),
                              (self.matrix, '人员分配'), (trace_scroll, '流程记录')):
            self.tabs.addTab(widget, title)

    def _conditions_panel(self):
        panel = Card()
        panel.body.addWidget(SectionHeader('筹划条件'))
        self.conditions, layout = scroll_contents()
        self.conditions.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.conditions.widget().setObjectName('planningConditions')
        panel.body.addWidget(self.conditions, 1)
        self._event_controls(layout)
        self._mode_controls(layout)
        layout.addWidget(label('可用人员', 'section'))
        self.people_layout = self._group(layout)
        self.people_checks = {}
        layout.addWidget(label('地面活动', 'section'))
        self.activities = self._group(layout)
        layout.addWidget(label('偏好', 'section'))
        self.preference = SegmentedControl(PREFERENCES)
        self.preference.setAccessibleName('方案偏好')
        self.preference.currentIndexChanged.connect(self._adjust_conditions)
        layout.addWidget(self.preference)
        layout.addStretch()
        self.generate_btn = ActionButton('生成候选方案', self.generate, 'spark', 'primary')
        panel.body.addWidget(self.generate_btn)
        return panel

    @staticmethod
    def _group(layout):
        group = QVBoxLayout()
        group.setContentsMargins(0, 0, 0, 0)
        group.setSpacing(4)
        layout.addLayout(group)
        return group

    def _event_controls(self, layout):
        layout.addWidget(label('事件', 'section'))
        self.events = QComboBox()
        self.events.setAccessibleName('选择处理中的事件')
        self.events.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.events.setMinimumContentsLength(9)
        self.events.currentIndexChanged.connect(self._event_chosen)
        self.incident_status = Badge('')
        row = QHBoxLayout()
        row.setSpacing(4)
        row.addWidget(self.events, 1)
        row.addWidget(self.incident_status)
        layout.addLayout(row)

    def _mode_controls(self, layout):
        layout.addWidget(label('筹划类型', 'section'))
        self.plan_mode = SegmentedControl(MODES)
        self.plan_mode.setAccessibleName('筹划类型')
        self.plan_mode.currentIndexChanged.connect(self._adjust_conditions)
        layout.addWidget(self.plan_mode)
        self.operation_btn = ActionButton('填写航拍需求', self.operation_dialog, 'plan')
        layout.addWidget(self.operation_btn)
        self.operation_summary = label('')
        layout.addWidget(self.operation_summary)
        self.observe_label = label('观察时长', 'section')
        layout.addWidget(self.observe_label)
        self.duration = SegmentedControl(tuple(f'{n} 分钟' for n in OBSERVATION_MINUTES))
        self.duration.setCurrentIndex(1)
        self.duration.setAccessibleName('观察时长')
        self.duration.currentIndexChanged.connect(self._adjust_conditions)
        layout.addWidget(self.duration)

    def _plan_panel(self):
        panel = Card()
        panel.body.addWidget(SectionHeader('方案比较'))
        self.result_scroll, body = scroll_contents()
        body.setSpacing(6)
        panel.body.addWidget(self.result_scroll, 1)
        self.status_box = QFrame()
        self.status_box.setObjectName('planningStatus')
        self.status_content = self._group_on(self.status_box, (8, 6, 8, 6))
        body.addWidget(self.status_box)
        self.input_info = label('')
        body.addWidget(self.input_info)
        self.comparison = ComparisonTable()
        self.comparison.selected.connect(self.select_plan)
        body.addWidget(self.comparison)
        self.detail = self._group(body)
        body.addStretch()
        self.confirm_btn = ActionButton('确认方案并建立任务', self.confirm, 'check', 'primary')
        panel.body.addWidget(self.confirm_btn)
        return panel

    @staticmethod
    def _group_on(widget, margins):
        group = QVBoxLayout(widget)
        group.setContentsMargins(*margins)
        group.setSpacing(4)
        return group

    def _event_chosen(self, index):
        event_id = self.events.itemData(index) or ''
        if event_id != self.context.selected:
            self.context.select(event_id)

    def on_enter(self):
        self.tabs.setCurrentIndex(0)

    def refresh(self):
        self.refresh_selection()

    def refresh_selection(self):
        views = self.service.views()
        choices = tuple((view['id'], view['name'], incident_badge(view)[0]) for view in views)
        with QSignalBlocker(self.events):
            if choices != self._event_items:
                self._event_items = choices
                self.events.clear()
                self.events.addItem('选择处理中的事件', '')
                for event_id, name, status in choices:
                    self.events.addItem(name, event_id)
                    row = self.events.count() - 1
                    self.events.setItemData(row, f'{name} · {status}', Qt.ToolTipRole)
                    self.events.setItemData(row, f'{name} · {status}', Qt.AccessibleTextRole)
            self.events.setCurrentIndex(max(0, self.events.findData(self.context.selected)))
        view = next((v for v in views if v['id'] == self.context.selected), None)
        self.incident_status.setVisible(view is not None)
        if view:
            self.incident_status.set_value(*incident_badge(view))
        self.rebuild_people(views)
        self._refresh_activities()
        self._adjust_conditions()

    def rebuild_people(self, views=None):
        roster = staff_records(self.scene, self.service.planning_context())
        busy = {t['person_id']: t['event_id'] for t in reservations(self.scene, self.context.selected)}
        names = {v['id']: v['name'] for v in views if v} if views is not None else {
            o.id: o.name for o in self.scene.observations}
        key = (roster, busy, names, self.scene.start_of_day_s)
        if key == self._staff_key:
            return
        self._staff_key = key
        _clear(self.people_layout)
        self.people_checks = {}
        for person in roster:
            self._staff_row(person, busy, names)

    def _staff_row(self, person, busy, names):
        pid = person['id']
        enabled = person['available'] and pid not in busy
        row = QHBoxLayout()
        row.setSpacing(4)
        check = QCheckBox(person['name'])
        check.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        check.setToolTip(person['name'])
        check.setEnabled(enabled)
        check.setChecked(enabled and self._chosen_people.get(pid, True))
        check.toggled.connect(lambda value, key=pid: self._choose_person(key, value))
        row.addWidget(check, 1)
        row.addWidget(Badge(person['role'], 'info'))
        clock = self.scene.clock_text
        row.addWidget(label(f'{clock(person["start_s"])[:5]}–{clock(person["end_s"])[:5]}'))
        self.people_layout.addLayout(row)
        if pid in busy:
            self.people_layout.addWidget(label(f'占用中 · {names.get(busy[pid], busy[pid])}'))
        elif not person['available']:
            self.people_layout.addWidget(label('当前不可用'))
        self.people_checks[pid] = check

    def _choose_person(self, person_id, chosen):
        self._chosen_people[person_id] = chosen
        self.refresh_plans()

    def _refresh_activities(self):
        clock = self.scene.clock
        stages = ('停用', '待开始', '进行中', '已结束')
        key = [
            (area.id, area.name, area.start_s, area.end_s,
             stages[0] if not area.active else stages[1] if clock < area.start_s else
             stages[2] if clock < area.end_s else stages[3])
            for area in self.scene.areas
        ]
        if key == self._activity_key:
            return
        self._activity_key = key
        _clear(self.activities)
        for aid, name, start, end, state in key:
            row = QHBoxLayout()
            row.setSpacing(4)
            activity = label(name)
            activity.setToolTip(f'{self.scene.clock_text(start)}–{self.scene.clock_text(end)}')
            row.addWidget(activity, 1)
            row.addWidget(Badge(state, 'ok' if state == '进行中' else 'unknown'))
            button = ActionButton('调整', lambda key=aid: self.activity_dialog(key), variant='quiet')
            button.setAccessibleName(f'调整{name}')
            row.addWidget(button)
            self.activities.addLayout(row)

    def _adjust_conditions(self, *_):
        mode = MODES[self.plan_mode.currentIndex()]
        self.observe_label.setVisible(mode == MODES[0])
        self.duration.setVisible(mode == MODES[0])
        self.operation_btn.setVisible(mode == MODES[2])
        request = self.scene.operation
        self.operation_summary.setVisible(mode == MODES[2])
        self.operation_summary.setText(
            f'{request["purpose"]}\n{self.scene.clock_text(request["earliest"])[:5]}–'
            f'{self.scene.clock_text(request["latest"])[:5]} · {duration_text(request["duration"])}'
            if request else '填写拍摄目的、可用时段与预计作业时长。')
        self.refresh_plans()

    def activity_dialog(self, aid):
        if self.context.busy:
            return
        self.pause_requested.emit()
        area = next(a for a in self.scene.areas if a.id == aid)
        dialog = ActivityDialog(self, area, self.scene)
        if dialog.exec() == QDialog.Accepted:
            self.context.safe(lambda: self.service.set_activity(aid, *dialog.values()))
            self.refresh()
            self.map_changed.emit()

    def operation_dialog(self):
        if self.context.busy:
            return
        self.pause_requested.emit()
        dialog = OperationDialog(self, self.scene.operation, self.scene)
        if dialog.exec() == QDialog.Accepted:
            self.context.safe(lambda: self.service.set_operation(*dialog.values()))
            self.refresh()

    def _params(self):
        return parameters(MODES[self.plan_mode.currentIndex()],
                          OBSERVATION_MINUTES[self.duration.currentIndex()],
                          PREFERENCES[self.preference.currentIndex()],
                          [pid for pid, check in self.people_checks.items() if check.isChecked()])

    def _pending(self):
        pending = self.service.pending
        return pending if pending and pending['event_id'] == self.context.selected else None

    def _plan(self):
        pending = self._pending()
        return pending['plans'][self.index] if pending and 0 <= self.index < len(pending['plans']) else None

    def generate(self):
        if self.context.busy or not self.events.currentData():
            return
        event_id, inputs = self.context.selected, self._params()
        minutes = inputs['observation_minutes'] or 3
        self.context.run_worker(
            lambda: self.service.generate(event_id, inputs['mode'], minutes,
                                          inputs['preference'], inputs['selected_people']),
            lambda _: self.refresh(),
        )

    def select_plan(self, index):
        self.index = index
        self.refresh_plans()
        self.map_changed.emit()

    def refresh_plans(self):
        pending = self._pending()
        plans = pending['plans'] if pending else []
        confirmed = pending is not None and pending.get('confirmed', False)
        if pending is not self._rendered_pending:
            self.index = pending.get('selected', 0) if pending else 0
            self.comparison.set_plans(plans, self.scene.clock_text)
            self._rendered_pending = pending
        plan = self._plan()
        reasons = self.service.staleness(pending, self.index, self._params()) if plans else []
        self.comparison.select(self.index)
        self.steps.set_step(4 if confirmed else 2 if plans else 1 if self.events.currentData() else 0)
        self._update_status(pending, reasons)
        self._update_details(pending, plan)
        self._update_actions(plan, confirmed, reasons)

    def _update_actions(self, plan, confirmed, reasons):
        busy = self.context.busy
        available = bool(self.events.currentData())
        self.conditions.setEnabled(not busy)
        self.generate_btn.setEnabled(available and not busy)
        self.confirm_btn.setEnabled(bool(plan and not confirmed and not reasons and not busy))
        self.confirm_btn.setToolTip(
            '本批候选方案已确认' if confirmed else '方案已过期，请重新生成' if reasons else
            '请先生成候选方案' if not plan else '已在后台生成候选方案' if busy else '')

    def _update_status(self, pending, reasons):
        busy = self.context.busy
        key = (id(pending), pending.get('confirmed') if pending else False, tuple(reasons), busy)
        if self._rendered_status == key:
            return
        self._rendered_status = key
        _clear(self.status_content)
        self.input_info.clear()
        if pending and pending.get('snapshot'):
            inputs = pending['snapshot']
            self.input_info.setText(
                f'输入快照 v{inputs["revision"]} · {self.scene.clock_text(inputs["clock"])} 生成')
        self.input_info.setVisible(bool(self.input_info.text()))
        if busy:
            self._notice('正在生成候选方案', 'info')
        elif pending and pending.get('confirmed'):
            adopted = pending['plans'][pending['selected']]
            self._notice(f'已采用{adopted["title"]} · 已建立 {len(adopted["tasks"])} 个任务', 'ok',
                         '前往任务中心', self.tasks_requested.emit)
        elif reasons:
            self._notice('方案已过期', 'warn')
            for reason in reasons:
                status_line(self.status_content, reason, glyph='dot')
            self.status_content.addWidget(ActionButton('重新生成', self.generate, 'spark'), 0, Qt.AlignLeft)
        elif pending and not pending['plans']:
            self._notice('无可行方案', 'warn')
            for gap in pending['gaps']:
                status_line(self.status_content, gap, glyph='dot')
            self.status_content.addWidget(label('可调整勾选人员、筹划时刻或观察时长后重新生成。'))
        elif not pending:
            self._notice('等待生成候选方案', 'info')
            self.status_content.addWidget(label('选择事件并设定条件，即可比较可行安排。'))
        else:
            self.status_box.hide()

    def _notice(self, title, tone, action=None, callback=None):
        self.status_box.setProperty('tone', tone)
        self.status_box.style().unpolish(self.status_box)
        self.status_box.style().polish(self.status_box)
        self.status_box.show()
        self.status_content.addWidget(label(title, 'section'))
        if action:
            self.status_content.addWidget(ActionButton(action, callback, variant='quiet'), 0, Qt.AlignLeft)

    def _update_details(self, pending, plan):
        adopted = bool(plan and pending.get('confirmed') and pending['selected'] == self.index)
        tasks = plan['tasks'] if plan else []
        if adopted:
            tasks = [t for t in self.scene.tasks if t['plan_thread'] == pending['thread']]
        key = (id(pending), self.index, adopted, tuple((t['key'], t['status']) for t in tasks))
        self.gantt.set_clock(self.scene.clock)
        if key == self._rendered_detail:
            return
        self._rendered_detail = key
        _clear(self.detail)
        if plan:
            self._detail_sections(plan, adopted, bool(pending.get('confirmed')))
        self._populate_execution(tasks, adopted)
        self._populate_trace(pending)

    def _detail_sections(self, plan, adopted, confirmed):
        title = f'{plan["title"]} · 已采用' if adopted else (
            f'{plan["title"]} · 备选草案' if confirmed else f'{plan["title"]} · 安排详情')
        self.detail.addWidget(SectionHeader(title))
        if plan['operation_window']:
            self.detail.addWidget(label(operation_window_text(plan['operation_window'], self.scene.clock_text)))
        self.detail.addWidget(label('变化清单', 'section'))
        for change in plan['changes']:
            _badge_row(self.detail, change['kind'], f'{change["name"]}\n{change["detail"]}',
                       CHANGE_TONES[change['kind']])
        if not plan['changes']:
            self.detail.addWidget(label('暂无变化记录'))
        for title, key, tone in (('依据', 'basis', 'info'), ('待核对', 'unresolved', 'warn')):
            self.detail.addWidget(label(title, 'section'))
            for line in plan[key]:
                status_line(self.detail, line, tone, glyph='dot')

    def _populate_execution(self, tasks, adopted):
        names = {task['key']: task['name'] for task in tasks}
        people = {t['person_id']: t['person'] for t in tasks}
        self.plan_table.setRowCount(0)
        self.matrix.setRowCount(0)
        self.matrix.setColumnCount(len(people) + 1)
        self.matrix.setHorizontalHeaderLabels(['步骤', *people.values()])
        for task in tasks:
            add_row(self.plan_table, plan_task_cells(task, names, self.scene.clock_text))
            add_row(self.matrix, [task['name'], *('负责' if pid == task['person_id'] else '—' for pid in people)])
        self.schedule.setCurrentIndex(int(bool(tasks)))
        self.gantt.set_plan(tasks, self.scene.clock, self.scene.clock_text, adopted)

    def _populate_trace(self, pending):
        _clear(self.trace)
        trace = pending['trace'] if pending else []
        if trace:
            for entry in trace:
                clock = self.scene.clock_text(entry.get('clock', pending.get('clock', 0)))
                _badge_row(self.trace, clock, f'{entry["role"]}  ·  {entry["result"]}', 'info')
            if pending['plans'] and not pending.get('confirmed'):
                _badge_row(self.trace, '待确认', '复核归档  ·  等待人工复核候选方案', 'unknown')
        else:
            self.trace.addWidget(label('生成候选方案后显示信息整理、研判、筹划和复核记录。'))
        self.trace.addStretch()

    def confirm(self):
        plan = self._plan()
        if not plan or not self.confirm_btn.isEnabled():
            return
        self.pause_requested.emit()
        if QMessageBox.question(
            self, '确认方案', f'采用{plan["title"]}，并在本机建立 {len(plan["tasks"])} 个待接单任务？',
        ) != QMessageBox.Yes:
            return
        self.context.safe(lambda: self.service.confirm(self.index, params=self._params()))
        self.refresh()
        self.map_changed.emit()

    def map_payload(self):
        payload = super().map_payload()
        plan = self._plan()
        if not plan or plan['mode'] != MODES[0]:
            return payload
        task = next(t for t in plan['tasks'] if t['key'] == 'observe')
        post_id = task['post_id']
        source_id = plan['snapshot']['people'].get(task['person_id'], {}).get('post_id')
        involves_travel = any(t['key'] == 'travel' for t in plan['tasks'])
        ids = {post_id, source_id} if involves_travel else {post_id}
        posts = {post['id']: post for post in payload['pins']}
        payload['pins'] = [p for p in posts.values() if p['id'] in ids]
        if post_id in posts:
            payload['zones'].append(_observation_range(posts[post_id]))
            if involves_travel and source_id in posts:
                payload['zones'].append(_walking_connection(posts[source_id], posts[post_id]))
        return payload


def _observation_range(post):
    points = []
    for index in range(49):
        radians = 2 * pi * index / 48
        lat, lon = advance(post['lat'], post['lon'], sin(radians), cos(radians), post['range_m'])
        points.append([lon, lat])
    return dict(kind='plan', highlight=True, rings=[points])


def _walking_connection(source, target):
    # The configured edge gives a walking duration, with a straight map connector as a schematic.
    points = []
    for post, east in ((source, -4), (target, -4), (target, 4), (source, 4), (source, -4)):
        lat, lon = advance(post['lat'], post['lon'], east, 0, 1)
        points.append([lon, lat])
    return dict(kind='plan', highlight=False, rings=[points])
