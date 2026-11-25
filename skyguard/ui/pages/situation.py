"""Duty situation monitoring, event triage and audit history."""
from ...system_clock import now as system_now
from pathlib import Path
from datetime import datetime, timedelta

from PySide6.QtCore import QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHeaderView, QStackedWidget, QTabWidget, QVBoxLayout, QWidget

from ...aerial import CHINA
from ...filing import correspondence, route_filing
from ...flight_route import FlightRoute, display_route
from ...flight_rules import DEMO_START, FlightQuery, instant
from ...flight_check import build_flight_check
from ...official_notices import Scan
from ...incident import IncidentStatus
from ..presenters import flight_window
from ..widgets.filing import FILING_TONES, MATCH_TONES, FilingDetail, load_json_file, overlay, today_filings
from ..widgets.flight_journey import clear
from ..widgets.flight_result import CheckRow, Disclosure, VerdictCard
from ..widgets.inline_planning import InlinePlanning
from ...task_flow import ACTIVE
from ..presenters import audit_rows, event_is_stale
from ..widgets.common import ActionButton, Badge, label, Card, EmptyState, SectionHeader, SegmentedControl, add_row, scroll_contents, table
from ..widgets.event_card import EventCard
from ..widgets.incident_detail import IncidentDetail
from ..widgets.kpi_strip import KpiStrip
from ..widgets.training_panel import TrainingPanel
from .base import DutyMapPage
from .tasks import TasksPage

class SituationPage(DutyMapPage):
    """Show the duty map alongside incident and audit controls."""

    title = '态势演练'
    planning_requested = Signal()
    tasks_requested = Signal()
    flight_point_requested = Signal(float, float)
    observation_mode_requested = Signal(float, float)

    def __init__(self, context):
        super().__init__(context)
        self._view_key = None
        self._views = {}
        self._list_order = ()
        self._audit_key = None
        self._quick_key = None
        self.observing = False
        self.cards = {}
        self.selected_filing = ''
        self._filing_list_key = None
        self.filing_detail = FilingDetail(context)
        self.filing_detail.updated.connect(self.refresh)
        self.filing_detail.updated.connect(self.map_changed.emit)
        self.kpis = KpiStrip()
        self.kpis.filter_changed.connect(self._filter_changed)
        self.body.addWidget(self.kpis)
        self.detail = IncidentDetail()
        self.detail.quick.body.addWidget(self.filing_detail)
        self.quick_summary = QVBoxLayout()
        self.detail.quick.body.insertLayout(0, self.quick_summary)
        self.detail.action_requested.connect(self._apply_action)
        self.detail.planning_requested.connect(self.open_planning)
        self.detail.demo_requested.connect(self.change)
        self.detail.association_requested.connect(self._associate)
        self.detail.attachment_requested.connect(self._open_attachment)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.detail,'概况')
        self.tabs.addTab(self._audit_panel(),'记录')
        self.event_tasks = TasksPage(context, compact=True)
        self.event_tasks.situation_requested.connect(lambda _: self.tabs.setCurrentIndex(0))
        self.tabs.addTab(self.event_tasks,'任务')
        self.tabs.currentChanged.connect(lambda _: self.refresh())
        self.planner = InlinePlanning(context)
        self.planner.returned.connect(self.return_event)
        self.planner.updated.connect(self.refresh)
        self.filing_summary = Card()
        self.filing_summary.body.addWidget(ActionButton('返回事件',self.return_event,variant='quiet'))
        filing_scroll, filing_body = scroll_contents()
        self.selected_filing_detail = FilingDetail(context)
        self.selected_filing_detail.updated.connect(self.refresh)
        filing_body.addWidget(self.selected_filing_detail)
        filing_body.addStretch()
        self.filing_summary.body.addWidget(filing_scroll,1)
        self.point_check = Card()
        self.point_check.setMinimumWidth(280)
        self.right = QStackedWidget()
        for panel in (self.tabs, self.planner, self.point_check, self.filing_summary):
            self.right.addWidget(panel)
        from ..widgets.recorded_duty import RecordedDuty
        self.recorded = RecordedDuty(self)
        self.right.addWidget(self.recorded.right)
        self.left_stack = QStackedWidget()
        self.left_stack.addWidget(self._events_panel())
        self.training = TrainingPanel(self)
        self.right.addWidget(self.training.right)
        self.columns = self.build_columns(self.left_stack, self.right, toolbar=self.training.toolbar)
        self.body.addWidget(self.recorded.timeline)
        self.body.addWidget(self.training.timeline)
        context.recorded.changed.connect(self.recorded_updated)
        context.recorded.ticked.connect(self.recorded_updated)
        context.clock_changed.connect(self.recorded_updated)
        self.training.timeline.hide()
        self.training.toolbar.hide()
        self.context.busy_changed.connect(self._busy_changed)

    def context_tags(self):
        if self.training.enabled and self.context.training.scenario:
            city=self.context.training.scenario['city']
            name=self.context.civic.cities().get(city,{}).get('name',city)
            return ((name, 'info'),)
        return super().context_tags()

    def on_enter(self):
        """Start keyboard navigation at the event scope."""
        if not self.training.enabled:
            self.event_scope.group.checkedButton().setFocus(Qt.OtherFocusReason)

    def _events_panel(self):
        panel = Card()
        panel.body.addWidget(self.recorded.panel)
        filings = Disclosure('今日报备')
        filings.row.addWidget(label('今日报备', 'checkTitle'), 1)
        self.filing_count = Badge('0', 'info')
        filings.row.addWidget(self.filing_count)
        filings.add_arrow()
        filings.setChecked(True)
        panel.body.addWidget(filings)
        self.filing_list = Card()
        filings.toggled.connect(self.filing_list.setVisible)
        panel.body.addWidget(self.filing_list)
        panel.body.addWidget(SectionHeader('观察事件'))
        self.event_scope = SegmentedControl(('处理中', '已关闭', '全部'))
        self.event_scope.setAccessibleName('事件范围')
        self.event_scope.currentIndexChanged.connect(self._filter_changed)
        panel.body.addWidget(self.event_scope)
        self.event_scroll, self.event_rows = scroll_contents()
        self.event_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.event_rows.setSpacing(8)
        self.event_rows.addStretch()
        self.events_empty = EmptyState('此范围暂无事件', description='可切换范围或清除指标筛选。', glyph='map')
        panel.body.addWidget(self.event_scroll, 1)
        panel.body.addWidget(self.events_empty, 1)
        return panel

    def _audit_panel(self):
        panel = QWidget()
        body = QVBoxLayout(panel)
        body.setContentsMargins(8, 4, 8, 8)
        body.setSpacing(4)
        self.audit_table = table(['时间（北京时间）', '标题', '说明'])
        self.audit_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.audit_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.audit_table.verticalHeader().setDefaultSectionSize(34)
        self.audit_table.setWordWrap(False)
        self.audit_empty = EmptyState('暂无审计记录')
        body.addWidget(self.audit_table, 1)
        body.addWidget(self.audit_empty, 1)
        return panel

    @staticmethod
    def _is_active(view):
        return view['status'] != IncidentStatus.CLOSED

    def _matches(self, view, metric):
        return (
            not metric or (metric == 'active' and self._is_active(view))
            or (metric == 'priority' and view['priority'] == '优先核验')
            or (metric == 'tasks' and any(task['event_id'] == view['id'] and task['status']=='进行中'
                                          for task in self.context.service.scene.tasks))
        )

    def _in_scope(self, view):
        scope = self.event_scope.currentIndex()
        return scope == 2 or self._is_active(view) == (scope == 0)

    def _filtered(self):
        return [view for view in self._views.values()
                if self._in_scope(view) and self._matches(view, self.kpis.active_filter)]

    def _update_views(self):
        service = self.context.service
        scene = service.scene
        key = (id(scene), scene.revision, scene.clock, self.context.public_city)
        if key == self._view_key:
            return
        self._view_key = key
        views = sorted(service.views(include_closed=True, measure_impact=False) if self.context.public_city=='changsha' else (), key=lambda view: (
            view['priority'] != '优先核验', view['age'], view['id'],
        ))
        self._views = {view['id']: view for view in views}
        active = [view for view in views if self._is_active(view)]
        counts = {metric: sum(self._matches(view, metric) for view in active)
                  for metric in ('active', 'priority')}
        counts['tasks'] = sum(task['status'] == '进行中' for task in scene.tasks) if views else 0
        self.kpis.set_counts(counts)

    def _render_list(self, views):
        selected = self.context.selected
        order = tuple(view['id'] for view in views)
        if order != self._list_order:
            self._place_cards(order)
            self._list_order = order
        for view in views:
            result = correspondence(view, self.context.civic.data['filings'], self.context.service.scene.clock)
            self.cards[view['id']].update_view(dict(view, filing_label=result.title, filing_tone=MATCH_TONES[result.title]),
                                               view['id'] == selected)
        self.event_scroll.setVisible(bool(views))
        self.events_empty.setVisible(not views)

    def _place_cards(self, order):
        current = set(order)
        for event_id in self.cards.keys() - current:
            card = self.cards.pop(event_id)
            self.event_rows.removeWidget(card)
            card.hide()
            card.deleteLater()
        for position, event_id in enumerate(order):
            if event_id not in self.cards:
                self.cards[event_id] = EventCard(event_id, self.context.select)
            card = self.cards[event_id]
            if self.event_rows.indexOf(card) != position:
                self.event_rows.insertWidget(position, card)

    def _refresh_audit(self, *_):
        service = self.context.service
        event_id = self.context.selected
        scene = service.scene
        civic_rows = self.context.civic.repo.history()
        key = (id(scene), scene.revision, event_id, civic_rows[0]['at'] if civic_rows else '')
        if key == self._audit_key:
            return
        self._audit_key = key
        records = sorted(service.repo.history() + civic_rows, key=lambda row: row['at'], reverse=True)
        rows = audit_rows(records, event_id) if event_id else []
        self.audit_table.setRowCount(0)
        for row in rows:
            add_row(self.audit_table, row)
        self.audit_table.setVisible(bool(rows))
        self.audit_empty.setVisible(not rows)

    def _filter_changed(self, *_):
        self._update_views()
        views = self._filtered()
        if self.context.selected not in {view['id'] for view in views}:
            selection = views[0]['id'] if views else ''
            if selection != self.context.selected:
                self.context.select(selection)
                return
        self.refresh()

    def _reveal(self, view):
        if not self._in_scope(view):
            scope = 0 if self._is_active(view) else 1
            with QSignalBlocker(self.event_scope):
                self.event_scope.setCurrentIndex(scope)
        if not self._matches(view, self.kpis.active_filter):
            self.kpis.set_filter('')

    def refresh_selection(self):
        """Reveal an event explicitly selected by the list, map or report form."""
        self.return_event()
        self._update_views()
        view = self._views.get(self.context.selected)
        if view:
            self._reveal(view)
        self.refresh()
        card = self.cards.get(self.context.selected)
        if card and not card.hasFocus():
            self.event_scroll.ensureWidgetVisible(card)

    def recorded_updated(self):
        if self.isVisible() and not self.training.enabled:
            self.recorded.refresh()
            if self.recorded.visible_now():
                self.map_changed.emit()

    def refresh(self):
        if self.training.enabled:
            self.training.render()
            return
        self.recorded.refresh()
        self._update_views()
        self._render_list(self._filtered())
        scene = self.context.service.scene
        view = self._views.get(self.context.selected)
        self.detail.render(view, scene.clock, scene.clock_text)
        self.filing_detail.render(view, scene.clock)
        if self.right.currentWidget() is self.filing_summary:
            self.selected_filing_detail.render(None,scene.clock,self.selected_filing)
        self._render_quick(view, scene)
        self._refresh_filings()
        if self.tabs.currentIndex() == 1:
            self._refresh_audit()
        if self.tabs.currentIndex() == 2:
            self.event_tasks.event_id = self.context.selected or None
            self.event_tasks.refresh()
        if self.right.currentWidget() is self.planner:
            self.planner.refresh()

    def _render_quick(self, view, scene):
        if not view:
            return
        match = correspondence(view, self.context.civic.data['filings'], scene.clock)
        names = tuple(self.context.civic.filing(fid)['snapshot']['title'] for fid, _, _ in match.candidates)
        areas = tuple(f'{area.name}（{scene.clock_text(area.start_s)}–{scene.clock_text(area.end_s)}）'
                      for area in scene.areas if any(row['id']==area.id for row in view['effects']))
        linkage = tuple((record['id'],record['state']) for record in self.context.civic.data['filings']
                        if record['event_id'] == view['id'])
        key = (view['id'],view['lat'],view['lon'],view['quality'],view['status'],match.title,names,areas,linkage)
        if self._quick_key == key:
            return
        self._quick_key = key
        clear(self.quick_summary)
        if view['lat'] is None:
            headline, unknown, next_step = '位置未知，先补充观察区域', '目标位置、方向', '请记录大致方位与时间'
            known = f'已收到{("演练" if view["source"]=="synthetic" else "现场")}观察'
        else:
            headlines = {'疑似对应':f'疑似对应{"、".join(names)}，请核对',
                         '无对应报备':'暂无对应报备，可发起现场核验',
                         '计划外越界':'观测位置超出同期报备范围',
                         '偏离':'观测位置与计划航线偏离',
                         '无法判断':'观测资料待补充'}
            headline = headlines[match.title]
            known = f'{view["lat"]:.5f}, {view["lon"]:.5f} · 约 {view["accuracy_m"]} 米误差'
            unknown, next_step = '目标身份、事项进展', '核对现场观察和报备记录'
        assigned = next((record for record in self.context.civic.data['filings']
                         if record['event_id'] == view['id'] and record['state'] in ('已对应','已核对')), None)
        if assigned:
            headline = f'{assigned["state"]}「{assigned["snapshot"]["title"]}」'
            next_step = '记录回访结果' if assigned['state']=='已对应' else '整理核验记录'
        self.quick_summary.addWidget(SectionHeader(headline))
        for title, text in (('已知', known + ('\n涉及 ' + '、'.join(areas) if areas else '')),
                            ('不知道', unknown), ('下一步', next_step)):
            self.quick_summary.addWidget(label(title,'checkTitle'))
            self.quick_summary.addWidget(label(text,'bodyText'))

    def open_planning(self):
        self.right.setCurrentWidget(self.planner)
        self.planner.refresh()

    def return_event(self):
        self.right.setCurrentWidget(self.tabs)
        self.recorded.timeline.hide()

    def on_leave(self):
        self.observing = False
        if self.training.enabled:
            self.context.training.clock_running=False

    def begin_observation(self):
        self.observing = True
        self.context.status.emit('在地图上选择观察位置', 8000)

    def map_clicked(self, lat, lon):
        if self.training.enabled:
            if self.context.training.geometry_edit:
                self.context.training.geometry_edit[1].append((lon,lat))
                self.map_changed.emit()
                return
            if self.context.training.state and self.context.training.mode=='实时':
                self.context.training.drag(lon,lat)
            return
        if self.observing:
            self.observing = False
            self.observation_mode_requested.emit(lat, lon)
            return
        now = system_now(CHINA)
        query = FlightQuery(((lon,lat),), 'circle', 1, now, now+timedelta(minutes=15), 70, '轻型', demo=False)
        city = self.context.public_city
        data = self.context.civic.book(city).evaluate(query, alternatives=False)
        check = build_flight_check(query,data,city_id=city,scan=Scan())
        clear(self.point_check.body)
        self.point_check.body.addWidget(ActionButton('返回事件',self.return_event,variant='quiet'))
        self.point_check.body.addWidget(VerdictCard(check, f'{self.context.civic.cities()[city]["name"]} · 70 米 · 轻型'))
        for item in check.items:
            self.point_check.body.addWidget(CheckRow(item,False))
        self.point_check.body.addWidget(ActionButton('查看完整查飞',lambda: self.flight_point_requested.emit(lon,lat),
                                                    variant='primary'))
        self.point_check.body.addStretch()
        self.right.setCurrentWidget(self.point_check)

    def open_training(self):
        self.recorded.timeline.hide()
        self.training.open()
        self.map_changed.emit()

    def actions(self):
        if self.training.enabled:
            return ()
        return (('态势演练', self.open_training, 'play', 'secondary'), *super().actions(), ('导入报备包', self._import_filing, 'import', 'secondary'))

    def _import_filing(self):
        def run():
            text = load_json_file(self, '导入报备包')
            return self.context.civic.import_filing(text) if text else None
        if self.context.safe(run):
            self.context.status.emit('已导入报备包', 5000)
            self.refresh()
            self.map_changed.emit()

    def _filings(self):
        payload_city = self.context.public_city
        now = system_now(CHINA)
        at = DEMO_START + timedelta(seconds=self.context.service.scene.clock)
        return today_filings(self.context.civic, payload_city, now, at)

    def _refresh_filings(self):
        filings = self._filings()
        key = [(record['id'], record['state']) for record, _ in filings]
        if key == self._filing_list_key:
            return
        self._filing_list_key = key
        clear(self.filing_list.body)
        self.filing_count.set_value(str(len(filings)), 'info')
        if not filings:
            self.filing_list.body.addWidget(label('今日暂无报备'))
        for record, _ in filings:
            query = record['snapshot']['query']
            when = flight_window(instant(query['start']), instant(query['end']))
            self.filing_list.body.addWidget(ActionButton(record['snapshot']['title'],
                lambda fid=record['id']: self.map_selected('filing:' + fid), variant='quiet'))
            kind = '航线' if query.get('route') else '旧版范围报备'
            self.filing_list.body.addWidget(label(when + ' · ' + record['state'] + ' · ' + kind))

    def map_payload(self):
        if self.training.enabled:
            session=self.context.training
            city=session.scenario['city']
            return dict(city=city,center=session.scenario['station'],training_bounds=session.scenario.get('bounds'),
                        training_scene=session.scenario['name'],
                        public=False,observations=[],areas=[],grid=[],**session.map_layers())
        payload = super().map_payload()
        if self.recorded.visible_now():
            plan = self.recorded.plan
            if plan:
                payload.update(self.context.recorded.map_layers(plan, self.recorded.cursor))
                payload['zones'] = [row for row in self.context.civic.book(plan['city']).overlays(
                    self.recorded.cursor or system_now(CHINA), plan['query'].get('demo',False))
                    if row['kind']=='restriction']
                return payload
        for record, now in self._filings():
            if route_filing(record):
                route = FlightRoute.parse(record['snapshot']['query']['route'])
                payload.setdefault('routes', []).append(dict(display_route(route), style='filing',
                                                             id='filing:' + record['id']))
            else:
                payload['zones'].extend(overlay(record, now))
        return payload

    def map_selected(self, identifier):
        if self.training.enabled:
            self.training.select_object(identifier.removeprefix('sim:'))
            return
        if identifier.startswith('recorded:'):
            self.recorded.show(identifier.removeprefix('recorded:'))
            self.map_changed.emit()
            return
        if identifier == 'planned':
            return
        self.recorded.timeline.hide()
        if identifier.startswith('filing:'):
            self.selected_filing = identifier.removeprefix('filing:')
            self.right.setCurrentWidget(self.filing_summary)
            self.refresh()
        else:
            self.selected_filing = ''
            super().map_selected(identifier)

    def _busy_changed(self, busy):
        self.detail.setEnabled(not busy)
        self.kpis.buttons['tasks'].setEnabled(not busy)

    def _apply_action(self, event_id, action, note, conclusion):
        if self.context.busy:
            return
        try:
            self.context.service.incident_action(event_id, action, note, conclusion)
        except Exception as error:
            self.detail.show_error(str(error))
            return
        self.refresh_selection()
        self.map_changed.emit()

    def change(self, kind):
        """Apply one of the exercise controls to the current simulated event."""
        view = self._views.get(self.context.selected)
        if not view or self.context.busy or view['source'] != 'synthetic' or not self._is_active(view):
            return
        self.context.safe(lambda: self.context.service.event_change(view['id'], kind))
        self.refresh()
        self.map_changed.emit()

    def _associate(self, track_id):
        if self.context.busy:
            return
        try:
            self.context.service.associate(self.context.selected, track_id)
        except Exception as error:
            self.detail.evidence.error.setText(str(error))
            self.detail.evidence.error.show()
            return
        self.refresh()
        self.map_changed.emit()

    def _open_attachment(self):
        path = self._views[self.context.selected].get('attachment', '')
        if path and Path(path).is_file() and QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            return
        self.detail.evidence.error.setText('当前报告没有可访问的本地附件。')
        self.detail.evidence.error.show()

