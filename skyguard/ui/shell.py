"""Desktop shell for the public and duty workspaces."""
from datetime import datetime, timedelta
from ..system_clock import now as system_now
import json
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QDialog, QFileDialog, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
)

from shiboken6 import isValid

from .. import __version__
from ..aerial import CHINA
from ..photo_mission import repeat_interval
from ..theme import THEME_NAMES, apply_theme, save_theme, set_themed_icon
from .app import asset_path, install_style
from .context import AppContext
from .map_view import MapPage, MapView
from .pages.flight_query import FlightQueryPage
from .pages.flight_logs import FlightLogsPage
from .pages.my_plans import MyPlansPage
from .pages.resources import ResourcesPage
from .pages.settings import SettingsPage
from .pages.situation import SituationPage
from .pages.sandbox import SandboxPage
from .pages.tasks import TasksPage
from .widgets.common import ActionButton, Badge, label
from .widgets.dialogs import FlightModelDialog, MapSettingsDialog, ObservationDialog, PrivilegePasswordDialog
from .widgets.help_dialog import HelpDialog
from .widgets.timeline import ExerciseTimeline


PUBLIC = 'public'
DUTY = 'duty'
NAVIGATION = {
    'flight_query': (PUBLIC, '航拍规划', 'map'),
    'my_plans': (PUBLIC, '我的计划', 'user'),
    'flight_logs': (PUBLIC, '飞行日志', 'plan'),
    'situation': (DUTY, '态势演练', 'layers'),
    'sandbox': (DUTY, '自由沙盘', 'tasks'),
    'settings': (None, '设置', 'settings'),
}


class _NavigationButton(QToolButton):
    """Keep keyboard focus visible and clear it when the mouse takes over."""

    def __init__(self):
        super().__init__()
        self.setFocusPolicy(Qt.TabFocus)

    def mousePressEvent(self, event):
        if self.hasFocus():
            self.clearFocus()
        super().mousePressEvent(event)


class MainWindow(QMainWindow):
    """Manage page navigation, the common toolbar and a single workspace map."""

    def __init__(self, root=None):
        super().__init__()
        install_style(QApplication.instance(), root)
        self.theme_name = QApplication.instance().skyguard_theme
        self.setWindowTitle(f'低空守望 v{__version__}')
        self.resize(1520, 960)
        self.setMinimumSize(1180, 760)
        self.context = AppContext(root, self)
        self.role = PUBLIC
        self.current_page = None
        self.last_location = None
        self._header_content = None
        self._build_shell()
        self.map_view = MapView(self.context, self.stack)
        self.timeline = ExerciseTimeline(self.context, self.stack)
        self.map_view.attach_clock(self.timeline)
        self._build_pages()
        self._connect()
        self._sync_theme_controls()
        self.switch_page('flight_query')
        self.flight_query.city.setFocus(Qt.OtherFocusReason)
        self.autosave = QTimer(self)
        self.autosave.setInterval(20000)
        self.autosave.timeout.connect(self.save_clock)
        self.autosave.start()
        self._clock_update()

    def _build_shell(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 4)
        layout.setSpacing(8)
        layout.addWidget(self._navigation())
        workspace = QWidget()
        workspace.setObjectName('workspace')
        body = QVBoxLayout(workspace)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        body.addWidget(self._top_bar())
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        layout.addWidget(workspace, 1)
        status = self.statusBar()
        status.setSizeGripEnabled(False)
        status.messageChanged.connect(lambda message: status.setVisible(bool(message)))
        status.hide()

    def _navigation(self):
        rail = QWidget()
        rail.setObjectName('rail')
        rail.setFixedWidth(96)
        layout = QVBoxLayout(rail)
        layout.setContentsMargins(8, 16, 8, 12)
        layout.setSpacing(4)
        mark = QLabel()
        mark.setAlignment(Qt.AlignCenter)
        mark.setAccessibleName('低空守望')
        mark.setPixmap(QPixmap(str(asset_path() / 'skyguard.png')).scaled(
            52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation,
        ))
        layout.addWidget(mark)
        self.role_badge = QLabel()
        self.role_badge.setObjectName('roleBadge')
        self.role_badge.setFixedSize(80, 30)
        self.role_badge.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.role_badge, 0, Qt.AlignHCenter)
        self.nav_buttons = {}
        group = QButtonGroup(rail)
        for key, (_role, title, glyph) in NAVIGATION.items():
            if key == 'settings':
                layout.addStretch()
            button = self._nav_button(title, glyph, lambda checked=False, page=key: self.open_workspace(page))
            button.setCheckable(True)
            group.addButton(button)
            self.nav_buttons[key] = button
            layout.addWidget(button)
        layout.addWidget(self._nav_button('帮助', 'help', self.help_dialog))
        version = label(f'v{__version__}', 'navVersion', numeric=True)
        version.setAlignment(Qt.AlignCenter)
        layout.addWidget(version)
        return rail

    def _nav_button(self, title, glyph, callback=None):
        button = _NavigationButton()
        button.setText(title)
        set_themed_icon(button, glyph, 'nav_ink', background='nav')
        button.toggled.connect(lambda checked: self._nav_icon(button, glyph, checked))
        button.setIconSize(QSize(24, 24))
        button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        button.setProperty('navigation', True)
        button.setFixedSize(80, 64)
        button.setAccessibleName(title)
        button.setCursor(Qt.PointingHandCursor)
        if callback is not None:
            button.clicked.connect(callback)
        return button

    @staticmethod
    def _nav_icon(button, glyph, checked):
        ink, background = ('nav_on_ink', 'nav_on') if checked else ('nav_ink', 'nav')
        set_themed_icon(button, glyph, ink, background=background)

    def _top_bar(self):
        header = QWidget()
        header.setObjectName('topBar')
        header.setFixedHeight(56)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)
        self.page_title = label('', 'pageTitle')
        self.page_title.setWordWrap(False)
        layout.addWidget(self.page_title)
        self.tags = QHBoxLayout()
        self.tags.setSpacing(4)
        layout.addLayout(self.tags)
        layout.addStretch()
        self.page_actions = QHBoxLayout()
        self.page_actions.setSpacing(4)
        layout.addLayout(self.page_actions)
        self.mode_switch_button = ActionButton('', self._request_role_switch)
        layout.addWidget(self.mode_switch_button)
        self.model_button = ActionButton('', lambda: self.switch_page('settings'), variant='quiet')
        self.model_button.setToolTip('配置后，向所选服务商发送查飞摘要和公开依据，整理核对建议。')
        layout.addWidget(self.model_button)
        self.system_time_button = label('', 'bodyText', numeric=True)
        self.system_time_button.setAccessibleName('本机时间')
        layout.addWidget(self.system_time_button)
        self.theme_button = ActionButton('', self._toggle_theme)
        self.theme_button.setObjectName('themeToggle')
        self.theme_button.setFixedSize(32, 32)
        self.theme_button.setIconSize(QSize(16, 16))
        layout.addWidget(self.theme_button)
        return header

    def _open_system_time(self):
        from .widgets.clock_panel import ClockPanel
        panel = ClockPanel(self.context)
        while panel.exec() == 2:
            panel.deleteLater()
            panel = ClockPanel(self.context)
        panel.deleteLater()

    def _clock_update(self):
        clock = self.context.clock
        when = clock.now()
        day = '一二三四五六日'[when.weekday()]
        text = (f'演示 · {when:%m-%d} 周{day} {when:%H:%M} ' +
                (f'▶ {clock.speed}×' if clock.playing else '暂停')
                if clock.demo else f'真实时间 · {when:%H:%M}')
        if self.system_time_button.text() != text:
            self.system_time_button.setText(text)

    def _dataset_changed(self):
        if self.situation.training.enabled:
            self.situation.training.close()
        self.situation.recorded.clear_selection()
        self.flight_logs.editor = None
        if self.my_plans.in_results:
            self.my_plans.close_results()
        self.flight_query.new_query()
        self.flight_query.set_time(system_now(CHINA).replace(second=0, microsecond=0))
        self.refresh()

    def _toggle_theme(self):
        self.set_theme('klein' if self.theme_name == 'night' else 'night')

    def _sync_theme_controls(self):
        dark = self.theme_name == 'night'
        target = 'klein' if dark else 'night'
        text = f'切换到{THEME_NAMES[target]}'
        self.theme_button.setToolTip(text)
        self.theme_button.setAccessibleName(text)
        set_themed_icon(self.theme_button, 'sun' if dark else 'moon')
        self.settings.set_theme(self.theme_name)

    def set_theme(self, name):
        """Switch appearance without re-entering pages or discarding input."""
        if name == self.theme_name:
            return
        self.theme_name = name
        apply_theme(QApplication.instance(), name)
        self._sync_theme_controls()
        self.map_view.set_theme(name)
        self.resources.refresh_appearance()
        if hasattr(self, 'official_dialog'):
            self.official_dialog.findChild(QWebEngineView).page().refresh_appearance()
        try:
            save_theme(self.context.root, name)
        except OSError:
            self.statusBar().showMessage('本次主题已切换，外观设置未能保存。', 8000)

    def _build_pages(self):
        self.flight_query = FlightQueryPage(self.context)
        self.my_plans = MyPlansPage(self.context)
        self.flight_logs = FlightLogsPage(self.context)
        self.situation = SituationPage(self.context)
        self.planning = self.situation.planner
        self.tasks = TasksPage(self.context)
        self.sandbox = SandboxPage(self.context)
        self.resources = ResourcesPage(self.context)
        self.settings = SettingsPage(self.context)
        self.settings.set_management(self.resources)
        self.pages = {
            'flight_query': self.flight_query, 'my_plans': self.my_plans, 'situation': self.situation,
            'flight_logs': self.flight_logs, 'tasks': self.tasks, 'sandbox': self.sandbox, 'settings': self.settings,
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
            if page.uses_map:
                page.map_view = self.map_view
                page.map_slot.installEventFilter(self)
                page.map_slot.parentWidget().installEventFilter(self)
            page.map_changed.connect(lambda source=page: self._page_map_changed(source))

    def _connect(self):
        self.context.clock_changed.connect(self._clock_update)
        self.context.dataset_changed.connect(self._dataset_changed)
        self.map_view.query_mode.currentIndexChanged.connect(self.flight_query.set_map_mode)
        self.map_view.undo_route.clicked.connect(self.flight_query.route_editor.undo_edit)
        self.map_view.redo_route.clicked.connect(self.flight_query.route_editor.redo_edit)
        self.context.status.connect(self.statusBar().showMessage)
        self.context.busy_changed.connect(self._busy_changed)
        self.context.selection_changed.connect(self._selected)
        self.context.observation_requested.connect(self.situation.begin_observation)
        self.context.import_requested.connect(self.import_records)
        self.context.export_requested.connect(self.export_records)
        self.timeline.changed.connect(self.refresh)
        self.map_view.ready.connect(self.push_map)
        self.map_view.grid_point.connect(self._grid_point)
        self.map_view.layers_changed.connect(self.push_map)
        self.map_view.ready.connect(self.flight_query.lookup_aerial_sceneries)
        self.map_view.selected.connect(lambda event_id: self.current_page.map_selected(event_id))
        self.map_view.clicked.connect(lambda lat, lon: self.current_page.map_clicked(lat, lon))
        self.map_view.public_clicked.connect(
            lambda city, lat, lon: self.current_page.public_map_clicked(city, lat, lon),
        )
        # A coordinate reply belongs to its query even if the user navigated away.
        self.map_view.bridge.route_batch.connect(lambda *args: self.flight_query.online_route_batch(*args)
                                                       if self.current_page is self.flight_query else None)
        self.map_view.bridge.route_pending.connect(lambda *args: self.flight_query.online_route_pending(*args)
                                                         if self.current_page is self.flight_query else None)
        self.map_view.route_action.connect(lambda *args: self.flight_query.route_action(*args)
                                          if self.current_page is self.flight_query else None)
        self.map_view.scenic_resolved.connect(self.flight_query.aerial_scenic_resolved)
        self.map_view.reconnecting.connect(self._map_reconnecting)
        self.map_view.configure.connect(self.map_settings_dialog)
        self.situation.observation_location.connect(self._observation_location)
        self.situation.flight_point_requested.connect(self._open_flight_point)
        self.situation.observation_mode_requested.connect(self._observation_from_point)
        self.situation.tasks_requested.connect(lambda: self.switch_page('tasks'))
        self.my_plans.plan_requested.connect(self._open_plan)
        self.my_plans.log_requested.connect(self._open_log)
        self.my_plans.copy_requested.connect(self._copy_plan)
        self.my_plans.repeat_requested.connect(self._repeat_plan)
        self.my_plans.supplement_requested.connect(self._supplement_plan)
        self.flight_query.proposed_plan.connect(self._show_new_plan)
        self.my_plans.reschedule_requested.connect(self._edit_plan_time)
        self.context.civic_changed.connect(self._show_unread)
        self.context.notice_reminders.connect(lambda _: self.my_plans.refresh() if self.current_page is self.my_plans else None)
        self._show_unread()
        self.my_plans.plan_cancelled.connect(self.flight_query.clear_editing)
        self.tasks.situation_requested.connect(lambda eid: self._open_event('situation', eid))
        self.tasks.planning_requested.connect(lambda eid: self._open_event('planning', eid))
        self.resources.staff_changed.connect(self._staff_changed)
        self.resources.cities_changed.connect(self._cities_changed)
        self.settings.theme_requested.connect(self.set_theme)
        self.settings.demo_changed.connect(self._set_demo_mode)
        self.flight_query.demo_changed.connect(self.settings.demo.setChecked)
        self.flight_query.result_panel.settings_requested.connect(lambda: self.switch_page('settings'))
        self.settings.model_requested.connect(self.settings_dialog)
        self.settings.map_requested.connect(self.map_settings_dialog)
        self.settings.official_reference_requested.connect(self.show_official_reference)

    def _set_demo_mode(self, enabled):
        self.flight_query.set_demo_mode(enabled)
        self._update_top_bar()

    def _open_event(self, page, event_id):
        if not self.context.busy:
            self.context.select(event_id)
            self.switch_page(page)
            self.situation.refresh_selection()
            if page == 'planning':
                self.situation.open_planning()

    def open_workspace(self, page):
        self.switch_page(page)
        if page == 'situation' and self.current_page is self.situation:
            self.situation.open_training()

    def switch_role(self, role):
        if self.role != role:
            self.open_workspace('flight_query' if role == PUBLIC else 'situation')

    def _request_role_switch(self):
        if self.role == DUTY:
            self.switch_role(PUBLIC)
            return
        dialog = PrivilegePasswordDialog(self)
        accepted = dialog.exec() == QDialog.Accepted
        dialog.deleteLater()
        if accepted:
            self.switch_role(DUTY)

    def switch_page(self, name):
        """Switch workspaces and attach the map to the incoming page if needed."""
        if self.context.busy:
            return
        if name == 'planning':
            self.switch_page('situation')
            self.situation.open_planning()
            return
        if name == 'resources':
            self.switch_role(DUTY)
            self.switch_page('settings')
            return
        incoming = self.pages[name]
        outgoing = self.current_page
        if incoming is outgoing:
            return
        self.timeline.pause()
        self.last_location = None
        if outgoing is not None:
            outgoing.on_leave()
        self.current_page = incoming
        self.role = (DUTY if name == 'tasks' else NAVIGATION[name][0]) or self.role
        self.settings.set_duty(self.role == DUTY)
        self._update_navigation(name)
        self.stack.setCurrentWidget(incoming)
        self._attach_map(incoming)
        incoming.on_enter()
        self.refresh()

    def _attach_map(self, page):
        self._position_map()
        self.map_view.set_clock_visible(self.role == DUTY and page.uses_map)
        QTimer.singleShot(0, self, self._position_map)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.LayoutRequest):
            page = self.current_page
            if page and page.uses_map and isValid(page.map_slot) and watched in (page.map_slot,page.map_slot.parentWidget()):
                QTimer.singleShot(0, self, self._position_map)
        return super().eventFilter(watched, event)

    def _position_map(self):
        if (not self.current_page or not self.current_page.uses_map
                or self.current_page.map_slot.parentWidget().isHidden()):
            self.map_view.hide()
            return
        layout = self.current_page.map_slot
        slot = layout.parentWidget()
        geometry=layout.geometry()
        area = QRect(slot.mapTo(self.stack, geometry.topLeft()), geometry.size())
        if self.map_view.geometry() != area:
            self.map_view.setGeometry(area)
        self.map_view.show()
        self.map_view.raise_()

    def _update_navigation(self, name):
        self.role_badge.setText('低空治理' if self.role == DUTY else '航拍助手')
        for key, button in self.nav_buttons.items():
            button.setVisible(NAVIGATION[key][0] in (None, self.role))
        if name in self.nav_buttons:
            self.nav_buttons[name].setChecked(True)

    def _update_top_bar(self):
        page = self.current_page
        tags = tuple(page.context_tags())
        exercise=page is self.situation and page.training.enabled
        if self.role == DUTY:
            tags += (('模拟推演' if exercise or page is self.sandbox else '低空治理',
                      'demo' if exercise or page is self.sandbox else 'info'),)
        if self.role == DUTY:
            self.mode_switch_button.setText('切换普通模式')
            self.mode_switch_button.setToolTip('返回「航拍助手」普通模式')
        else:
            self.mode_switch_button.setText('切换权限模式')
            self.mode_switch_button.setToolTip('输入权限密码切换到「低空治理」模式')
        self.system_time_button.setVisible(not exercise and page is not self.sandbox)
        self.model_button.setVisible(not exercise and page is not self.sandbox)
        if (self.role == PUBLIC and self.flight_query.demo_mode
                and ('演示资料', 'demo') not in tags):
            tags += (('演示资料', 'demo'),)
        actions = page.actions()
        content = (page.title, tags, tuple(action[0] for action in actions))
        if content != self._header_content:
            self._header_content = content
            self.page_title.setText(page.title)
            self._clear_row(self.tags)
            self._clear_row(self.page_actions)
            for text, tone in tags:
                self.tags.addWidget(Badge(text, tone), 0, Qt.AlignVCenter)
            for text, callback, glyph, variant in actions:
                self.page_actions.addWidget(ActionButton(text, callback, glyph, variant))
        configured = bool(self.context.flight_model_config.key)
        text = 'AI 解读：已配置' if configured else 'AI 解读：未配置'
        if self.model_button.text() == text:
            return
        self.model_button.setText(text)
        set_themed_icon(self.model_button, 'dot', 'ok' if configured else 'unknown')
        self.model_button.setIconSize(QSize(12, 12))

    @staticmethod
    def _clear_row(layout):
        while layout.count():
            widget = layout.takeAt(0).widget()
            widget.hide()
            widget.deleteLater()

    def _busy_changed(self, busy):
        for button in (*self.nav_buttons.values(), self.mode_switch_button):
            button.setEnabled(not busy)
        if not busy:
            self.refresh()

    def _selected(self, event_id):
        if self.current_page is self.situation:
            self.situation.refresh_selection()
            self.push_map()

    def _page_map_changed(self, source):
        if self.current_page is source:
            self._update_top_bar()
            self.push_map()

    def _map_reconnecting(self):
        self.flight_query.route_epoch += 1
        self.flight_query.route_ack = 0
        self.flight_query.coordinates_pending = False
        self.flight_query.check_button.setEnabled(True)
        self.flight_query.route_editor.draft.setEnabled(True)
        self.settings.refresh()
        self.last_location = None
        self.push_map()

    def _show_unread(self):
        count = sum(not alert['read'] for plan in self.context.civic.data['plans'] if not plan.get('cancelled')
                    for alert in plan.get('watch', {}).get('alerts', ()))
        text = '我的计划' + (f' · {count}' if count else '')
        self.nav_buttons['my_plans'].setText(text)
        self.nav_buttons['my_plans'].setAccessibleName(text)

    def _open_log(self, plan):
        self.switch_page('flight_logs')
        self.flight_logs.new_record(plan=plan)

    def _show_new_plan(self, identifier):
        self.switch_page('my_plans')
        QTimer.singleShot(0, self, lambda: self.my_plans.focus_plan(identifier))

    def _copy_plan(self, plan):
        self.switch_page('flight_query')
        self.flight_query.load_plan(plan, recheck=False)
        self.flight_query._detach_plan()
        if self.flight_query.previous_mission:
            self.flight_query.previous_mission = dict(self.flight_query.previous_mission, materials=[], completed=[])
        self.flight_query.set_time(system_now(CHINA).replace(second=0, microsecond=0))
        self.flight_query.start.setFocus()
        self.flight_query.invalidate()

    def _repeat_plan(self, plan):
        self._open_plan(plan, False)
        page = self.flight_query
        page._detach_plan()
        if page.previous_mission:
            page.previous_mission = dict(page.previous_mission, previous_plan=plan['id'], materials=[], completed=[])
        prior = page.form_start() + repeat_interval(page.mission_time.text())
        tomorrow = system_now(CHINA) + timedelta(days=1)
        page.set_time(max(tomorrow, prior).replace(second=0,microsecond=0))
        page.start.setFocus()

    def _supplement_plan(self, plan, shot):
        from ..flight_route import FlightRoute, Waypoint

        self._repeat_plan(plan)
        page = self.flight_query
        original = FlightRoute.parse(plan['query']['route'])
        point = Waypoint(*shot['point'], shot['altitude'], max(1,shot['record_s']), shot['heading'])
        route = FlightRoute((point, Waypoint(*original.takeoff, point.altitude_m, camera_deg=point.camera_deg)),
                            original.speed_mps,original.takeoff,page.form_start(),original.category)
        page.route_editor.load(route)
        page.set_step(2)
        page.invalidate()
        self.context.status.emit('已生成补拍航线，核对时间后可另存为计划', 5000)

    def _edit_plan_time(self, plan):
        self._open_plan(plan, False)
        self.flight_query.set_step(2)
        self.flight_query.start.setFocus(Qt.OtherFocusReason)

    def _open_plan(self, plan, recheck):
        self.switch_page('flight_query')
        self.flight_query.load_plan(plan, recheck=recheck)

    def refresh(self):
        """Refresh the currently visible page, toolbar and map."""
        self.current_page.refresh()
        if self.current_page is self.settings and self.role == DUTY:
            self.resources.refresh()
        self.timeline.refresh()
        self.map_view.refresh_clock()
        self._update_top_bar()
        self.push_map()

    def push_map(self):
        """Push the active page's map data to the shared view."""
        page = self.current_page
        if not page.uses_map:
            return
        self._position_map()
        public = page is self.flight_query
        exercise = (page is self.situation and page.training.enabled) or page is self.sandbox
        mode = ('sandbox_edit' if page is self.sandbox and page.step < 3 else
                'civic_replay' if exercise else 'public')
        self.map_view.set_legend_mode(mode)
        self.map_view.set_civic_exercise(exercise)
        manual = public and page.manual_enabled
        self.map_view.query_mode.setVisible(manual)
        editing_route = manual and not page.point_mode and bool(page.route_editor.waypoints or page.route_editor.future)
        for button, available in ((self.map_view.undo_route, bool(self.flight_query.route_editor.history)),
                                  (self.map_view.redo_route, bool(self.flight_query.route_editor.future))):
            button.setVisible(editing_route)
            button.setEnabled(available)
        self.map_view.editor_hint.setVisible(manual)
        if manual:
            text = '点击地图查询这个地点' if page.point_mode else (
                '点击地图放置起飞点',
                '点击地图添加航点，拖动调整',
                '设置起飞时间',
            )[page.editor_step]
            if page.subject_pick:
                text = '点击地图标出想拍的主体'
            if page.confirm_takeoff and not page.subject_pick:
                text = ('点击地图更换起飞点' if page.route_editor.pick_takeoff
                        else '候选起飞点待确认，使用左侧按钮继续')
            self.map_view.editor_hint.setText(text)
        self.map_view.set_clock_visible(False)
        if page.map_slot.parentWidget().isHidden():
            return
        city_id = page.active['city'] if page is self.my_plans and page.active else self.context.public_city
        if page is self.situation and page.training.enabled and self.context.training.scenario:
            city_id = self.context.training.scenario['city']
        if page is self.sandbox:
            city_id = page.config.city
            self._update_top_bar()
        name = self.context.civic.cities()[city_id]['name']
        self.map_view.set_title(name + '地图')
        if not self.map_view.is_ready:
            return
        payload = page.map_payload()
        self.map_view.push(payload, page.map_selection())
        if public:
            page.sync_photo_frame()

    def _open_flight_point(self, lon, lat):
        self.switch_page('flight_query')
        self.flight_query.set_manual(True)
        self.flight_query.set_demo_mode(False)
        self.flight_query.set_time(system_now(CHINA))
        self.flight_query.select_start((lon, lat), focus=True)

    def _simulation_input(self, action,x,y):
        if self.current_page is not self.my_plans or not self.my_plans.pilot.isVisible():
            return
        session = self.context.recorded
        if action=='drag':
            session.drag(x,y)
        elif action=='height':
            session.altitude(x)
        elif action=='pause':
            session.toggle_pause()

    def _observation_from_point(self, lat, lon):
        self.last_location = lat,lon
        self.add_observation()

    def _grid_point(self, lon, lat):
        if self.current_page is self.flight_query:
            self.flight_query.map_point(lat, lon)

    def _observation_location(self, lat, lon):
        self.last_location = lat, lon
        self.statusBar().showMessage(f'已选取 {lat:.6f}, {lon:.6f}；点击“新增观察”使用该位置。', 12000)

    def add_observation(self):
        """Add an observation using the appropriate current map selection."""
        if self.context.busy:
            return
        location = self.last_location
        if self.current_page is self.flight_query:
            page = self.flight_query
            if not page.actions():
                return
            points = page.resolved_points if page.gcj_selection else page.points
            location = tuple(reversed(points[-1])) if points else None
        self.timeline.pause()
        dialog = ObservationDialog(self, location, True)
        dialog.source.setEnabled(self.role == DUTY)
        if dialog.exec() == QDialog.Accepted:
            event_id = self.context.safe(lambda: self.context.service.add_observation(dialog.data()))
            if event_id:
                self.context.select(event_id)
                self.refresh()

    def import_records(self):
        """Import existing JSON or CSV observation records."""
        if self.context.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, '导入观察记录', '', '观察记录 (*.json *.csv)')
        if not path:
            return
        result = self.context.safe(lambda: self.context.service.import_observations(
            Path(path).read_text(encoding='utf-8-sig'), 'json' if path.endswith('.json') else 'csv',
        ))
        if result is not None:
            self.refresh()
            self.statusBar().showMessage(f'已导入{result}条记录', 8000)

    def export_records(self):
        """Export the existing internal event archive."""
        if self.context.busy:
            return
        path, _ = QFileDialog.getSaveFileName(self, '导出内部档案', '低空守望_事件档案.json', 'JSON (*.json)')
        if path:
            self.context.safe(lambda: Path(path).write_text(
                json.dumps(self.context.service.export(), ensure_ascii=False, indent=2), encoding='utf8',
            ))

    def _staff_changed(self):
        self.refresh()

    def _cities_changed(self):
        self.flight_query.refresh_cities()
        self.flight_query.invalidate()
        self.refresh()

    def settings_dialog(self):
        """Open model configuration and refresh the query and status on save."""
        if FlightModelDialog(self, self.context).exec() == QDialog.Accepted:
            self.flight_query.invalidate()
            self.settings.refresh()
            self._update_top_bar()

    def map_settings_dialog(self):
        """Open AMap settings and reconnect after saving."""
        if MapSettingsDialog(self, self.context).exec() == QDialog.Accepted:
            self.map_view.reconnect()

    def show_official_reference(self):
        """Open the official map reference in its auxiliary browser."""
        if not hasattr(self, 'official_dialog'):
            self.official_dialog = self._official_reference()
        self.official_dialog.show()
        self.official_dialog.raise_()

    def _official_reference(self):
        url = QUrl('https://hunan.tianditu.gov.cn/TDTHN/portal/map/index.html')
        dialog = QDialog(self)
        dialog.setWindowTitle('天地图·湖南 · 官方在线核对')
        dialog.resize(1180, 780)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(
            '官方网页用于核对地名与路网，演练数据不会发送到该网页。可在网页中选择长沙市或搜索地标。'
        ))
        web = QWebEngineView(dialog)
        web.setPage(MapPage(web))
        layout.addWidget(web, 1)
        loading = '正在载入官方地图网页…'
        status = label(loading)
        layout.addWidget(status)
        web.loadStarted.connect(lambda: status.setText(loading))
        web.loadFinished.connect(lambda ok: status.setText(
            '官方网页已载入；地图内容由原网站提供。' if ok
            else '官方网站暂时无法载入，可重试或在浏览器打开。'
        ))
        actions = QHBoxLayout()
        actions.addWidget(ActionButton('重新加载', web.reload))
        actions.addWidget(ActionButton('在浏览器打开', lambda: QDesktopServices.openUrl(url)))
        actions.addStretch()
        layout.addLayout(actions)
        web.setUrl(url)
        return dialog

    def help_dialog(self):
        """Open the guide at the active workspace."""
        tab = {self.situation: 1, self.sandbox: 2, self.settings: 3}.get(self.current_page, 0)
        HelpDialog(self, tab).exec()

    def save_clock(self):
        """Persist the exercise time when there is no duty computation."""
        if not self.context.busy:
            service = self.context.service
            service.persist('自动保存', {'clock': int(service.scene.clock)})

    def reset(self):
        """Reset simulation values after the duty operator confirms."""
        if self.context.busy or self.role != DUTY:
            return
        if QMessageBox.question(
            self, '重置演练', '重置当前演练状态？既有审计记录保留。',
        ) != QMessageBox.Yes:
            return
        self.timeline.pause()
        service = self.context.service
        service.reset()
        self.context.select(next((item['id'] for item in service.views()), ''))
        self.refresh()

    def closeEvent(self, event):
        """Wait for active work and persist the local session on close."""
        if self.context.busy:
            QMessageBox.information(self, '后台任务进行中', '请等待本次后台任务结束后关闭。')
            event.ignore()
            return
        if any(worker.isRunning() for worker in self.context.flight_workers):
            self.statusBar().showMessage('等待查飞网络请求结束后即可关闭。', 7000)
            event.ignore()
            return
        if any(worker.isRunning() for worker in self.context.aerial_workers):
            self.statusBar().showMessage('等待航拍资料查询结束后即可关闭。', 7000)
            event.ignore()
            return
        self.context.training.close_flight()
        self.autosave.stop()
        self.timeline.pause()
        service = self.context.service
        service.persist('关闭会话', {'clock': int(service.scene.clock)})
        self.context.map_server.close()
        event.accept()
