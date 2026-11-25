"""Public flight form and revision-bound query workflow."""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from ...system_clock import now as system_now
import json

from httpx import TimeoutException
from PySide6.QtCore import QDateTime, QSignalBlocker, QTimer, QTimeZone, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox, QDateTimeEdit, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QStackedWidget, QVBoxLayout, QWidget,
    QMessageBox, QSpinBox,
)

from ...aerial import FORECAST_OUTSIDE, FORECAST_PARTIAL, displayed_score, route_center, scenic_geometry, sun_position
from ...aerial_plan import SOURCE_LABELS, flight_metrics, select_variant
from ...filing_material import facts_for
from ...preflight import checklist
from ..widgets.flight_journey import PreparationCard, clear
from ...aerial_weather import AIRCRAFT_PROFILES
from ...flight_check import CHINA, HEADLINES, Verdict, build_flight_check, model_notice_facts
from ...flight_model import FlightModel
from ...flight_route import FlightRoute, ROUTE_LIMITS, Waypoint, display_route, route_points, timeline
from ...zone_catalog import read_table
from ..widgets.common import SegmentedControl
from ..widgets.route_editor import RouteEditor
from ..widgets.position_finder import PositionFinder
from ...flight_rules import AIRCRAFT, DEMO_START, FlightQuery, drawing, instant, lonlat
from ...official_airspace import published_evidence
from ...official_notices import Scan
from ...theme import themed_font
from ...photo_mission import TEMPLATES, mission_for, shot_altitude
from ..widgets.photo_preview import PhotoPreview
from ..presenters import flight_window
from ..widgets.common import (
    ActionButton, Badge, Card, SectionHeader, icon_label, label, scroll_contents,
)
from ..widgets.aerial_panel import AerialPanel
from ..widgets.flight_result import Disclosure, FlightResultPanel, _link
from .base import Page

MODEL_WAIT_SECONDS = 30
MODEL_TIMEOUT_STATUS = 'AI 解读超时'


def check_route_segments(query, city, book, scan):
    segments = []
    for piece in timeline(query.route):
        begin = query.start + timedelta(seconds=piece.begin_s)
        first = replace(piece.a, hover_s=0)
        points = (first, replace(piece.b, hover_s=0))
        if piece.a.xy == piece.b.xy and piece.a.altitude_m == piece.b.altitude_m:
            points = (replace(first, hover_s=max(1,round(piece.end_s-piece.begin_s))),)
        route = FlightRoute(points, query.route.speed_mps, piece.a.xy, begin, query.category)
        part = replace(query, start=begin, route=route)
        check = build_flight_check(part, book.evaluate(part, alternatives=False), city_id=city, scan=scan)
        local = next((item for item in check.items if item.state.value in ('restricted','permit')
                      and item.key in ('base_airspace','local_rules','notices')), None)
        missing = any(item.key == 'base_airspace' and item.state.value == 'unknown' for item in check.items)
        segments.append({'path':[piece.a.xy,piece.b.xy], 'state':check.verdict.value,
                         'missing_base':missing, 'local_reason':local.summary if local else ''})
    return segments


def _step_number(number):
    tag = label(str(number), 'stepNumber')
    tag.setFixedSize(24, 24)
    tag.setAlignment(Qt.AlignCenter)
    return tag


def _step_heading(number, title):
    heading = QHBoxLayout()
    heading.setSpacing(8)
    heading.addWidget(_step_number(number))
    heading.addWidget(SectionHeader(title), 1)
    return heading


def _form_step(number, title):
    card = Card()
    heading = _step_heading(number, title)
    complete = icon_label('check', 'ok', 16)
    complete.setAccessibleName('已填写')
    complete.setToolTip('已填写')
    heading.addWidget(complete)
    card.body.addLayout(heading)
    form = QFormLayout()
    form.setSpacing(8)
    form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
    card.body.addLayout(form)
    return card, form, complete


class FlightQueryPage(Page):
    """Collect a flight range and display the applicable conditions."""

    title = '航拍规划'
    uses_map = True
    demo_changed = Signal(bool)
    proposed_plan = Signal(str)

    def __init__(self, context):
        super().__init__(context)
        self.setObjectName('flightQuery')
        self.points = []
        self.editor_step = 0
        self.point_mode = False
        self.point = None
        self.takeoff_candidate = None
        self.save_after_check = False
        self.saved_revision = None
        self._pending_route_save = False
        self.drawing_route = True
        self.route_epoch = 0
        self.route_ack = 0
        self.coordinates_pending = False
        self._segment_job = None
        self.confirm_takeoff = False
        self.city_drafts = {}
        self.short_drafts = {}
        self._draft_city = None
        self._loading_plan = False
        self.editing = None
        self.demo_mode = False
        self.result = None
        self.checked_query = None
        self._revision = 0
        self._pending = None
        self._rule_result = None
        self._notices = None
        self._reopen_aerial = False
        self._aerial_check_cache = {}
        self._scenic_seq = None
        self.result_panel = FlightResultPanel()
        self._segments = ()
        self._preview = None
        self._preview_point = None
        self._preview_subject = ''
        self._route_scans = {}
        self.route_timer = QTimer(self)
        self.route_timer.setSingleShot(True)
        self.route_timer.setInterval(450)
        self.route_timer.timeout.connect(self._check_segments)
        self.score_timer = QTimer(self)
        self.score_timer.setSingleShot(True)
        self.score_timer.setInterval(500)
        self.score_timer.timeout.connect(self.recheck_open_score)
        self._prepared = set()
        self._favorite_checked_revision = None
        self.preparation = PreparationCard()
        self.preparation.changed.connect(self._set_preparation)
        self.public_note = label('', 'bodyText')
        self.public_note.hide()
        self.result_panel.journey_slot.insertWidget(0, self.public_note)
        self.rehearsal_note = label('', 'bodyText')
        self.rehearsal_note.hide()
        self.result_panel.journey_slot.insertWidget(0, self.rehearsal_note)
        self.trip_note = label('', 'bodyText')
        self.trip_note.hide()
        self.result_panel.journey_slot.addWidget(self.trip_note)
        self.result_panel.journey_slot.addWidget(self.preparation)
        self.segment_action = ActionButton('', self.focus_segment)
        self.segment_action.hide()
        self.segment_reason = label('', 'bodyText')
        self.segment_reason.hide()
        self.result_panel.journey_slot.addWidget(self.segment_reason)
        self.result_panel.journey_slot.addWidget(self.segment_action)
        self.edit_result_route = ActionButton('修改航线', self.continue_route, variant='primary')
        self.result_panel.journey_slot.addWidget(self.edit_result_route)
        self.edit_result_route.hide()
        self.preparation.hide()
        self.aerial = AerialPanel(context, self._check_aerial_window)
        self.result_panel.aerial_slot.addWidget(self.aerial)
        self.aerial.map_changed.connect(self.map_changed.emit)
        self.aerial.choose_time.connect(self._adopt_aerial_time)
        self.aerial.pick_subject.connect(self.pick_photo_subject)
        self.aerial.resolved.connect(self._show_forecast_window)
        self.aerial.resolved.connect(self._show_trip_changes)
        self.result_panel.aerial_requested.connect(self.start_aerial)
        self.result_panel.alternative_requested.connect(self.alternate)
        self.result_panel.save_started.connect(self.start_save)
        self.result_panel.save_requested.connect(self.save_plan)
        self.result_panel.new_save_requested.connect(lambda title: self.save_plan(title, as_new=True))
        self.previous_mission = None
        self.trip_baseline = None
        self.active_proposal = None
        self.manual_enabled = False
        self.capture_subject = None
        self.subject_pick = False
        self.shot_highlight = None
        self._photo_frame = None
        self.mission_bar = self._mission_controls()
        self.finder = PositionFinder(self)
        self.preview_panel = Card()
        preview_scroll, preview_contents = scroll_contents()
        preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.preview_panel.body.addWidget(preview_scroll,1)
        self.preview_actions = QVBoxLayout()
        self.preview_panel.body.addLayout(self.preview_actions)
        self.preview_panel.body = preview_contents
        self.recommend_empty = Card()
        self.recommend_empty.body.addStretch(1)
        self.recommend_empty.body.addWidget(SectionHeader('想拍什么画面？'))
        self.recommend_empty.body.addWidget(label('输入拍摄计划，地图会标出起降点和关键镜头。','bodyText'))
        self.recommend_empty.body.addStretch(2)
        self.result_stack = QStackedWidget()
        for panel in (self.recommend_empty,self.result_panel,self.preview_panel,self.finder.results):
            self.result_stack.addWidget(panel)
        self.columns = self.build_columns(self._left_panel(), self.result_stack)
        self.photo_preview = PhotoPreview()
        self.photo_preview.frame_changed.connect(self._show_photo_frame)
        self.photo_preview.closed.connect(self.close_photo_preview)
        center = self.map_slot
        map_area = QWidget()
        map_area.setMinimumHeight(240)
        self.map_slot = QVBoxLayout(map_area)
        self.map_slot.setContentsMargins(0, 0, 0, 0)
        center.addWidget(map_area, 1)
        center.addWidget(self.photo_preview)
        self.param_summary = self._parameter_summary()
        self.param_summary.hide()
        self._connect_form()
        self.refresh_cities()
        self._draft_city = self.city_id()
        self.set_time(system_now(CHINA).replace(second=0, microsecond=0))
        self.describe_points()
        self.finder.refresh_origins()
        self.set_step(0)

    def _left_panel(self):
        panel = Card()
        editor = self._query_panel()
        panel.body.addWidget(self.city_row)
        self.planning_stack = QStackedWidget()
        recommend = QWidget()
        content = QVBoxLayout(recommend)
        content.setContentsMargins(0,0,0,0)
        scroll, body = scroll_contents()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body.addWidget(self.mission_bar)
        body.addWidget(self.finder)
        body.addStretch()
        content.addWidget(scroll,1)
        self.planning_stack.addWidget(recommend)
        self.planning_stack.addWidget(editor)
        panel.body.addWidget(self.planning_stack,1)
        self.planning_mode = ActionButton('自己画航线', self.toggle_planning_mode, variant='quiet')
        panel.body.addWidget(self.planning_mode)
        return panel

    def toggle_planning_mode(self):
        self.set_manual(not self.manual_enabled)

    def set_manual(self, enabled):
        self.manual_enabled = enabled
        if not enabled:
            self.finder.hide_results()
        self.planning_stack.setCurrentIndex(1 if enabled else 0)
        self.planning_mode.setText('返回方案推荐' if enabled else '自己画航线')
        self.result_stack.setCurrentWidget(self.result_panel if enabled else self.recommend_empty)
        self.describe_points()
        self.map_changed.emit()

    def _mission_controls(self):
        bar = Card()
        bar.body.addWidget(label('拍摄目的', 'checkTitle'))
        self.mission_kind = QComboBox()
        self.mission_kind.addItem('自由规划', None)
        for key, (title, _, _, _) in TEMPLATES.items():
            self.mission_kind.addItem(title, key)
        self.mission_kind.setAccessibleName('拍摄任务')
        self.mission_brief = QLineEdit()
        self.mission_time = QLineEdit()
        self.mission_brief.setMaxLength(120)
        self.mission_time.setMaxLength(60)
        bar.body.addWidget(self.mission_kind)
        self.mission_kind.currentIndexChanged.connect(self._mission_controls_changed)
        for field in (self.mission_brief, self.mission_time):
            field.editingFinished.connect(self._reload_photo_preview)
        self._mission_controls_changed()
        return bar

    def _mission_controls_changed(self, *_):
        key = self.mission_kind.currentData()
        for widget, index in ((self.mission_brief, 2), (self.mission_time, 3)):
            if widget is self.mission_time:
                widget.setVisible(bool(key))
            if key:
                widget.setPlaceholderText(TEMPLATES[key][index])
                widget.setAccessibleName(TEMPLATES[key][index])
        if not key:
            self.mission_brief.setPlaceholderText('例如：梅溪湖日落大片，慢速环绕，避开人多')
            self.mission_brief.setAccessibleName('想拍什么')
        if hasattr(self, 'finder'):
            self.finder.inputs_changed()
        self._reload_photo_preview()

    def mission(self, route):
        current = self.previous_mission
        kind = self.mission_kind.currentData() or 'free'
        mission = mission_for(route, kind, self.mission_brief.text(), self.mission_time.text(), current)
        if self.capture_subject:
            mission['subject'] = self.capture_subject
        return mission

    def load_mission(self, mission):
        self.previous_mission = mission
        self.capture_subject = tuple(mission['subject']) if mission and mission.get('subject') else None
        self.mission_brief.setText(mission.get('brief','') if mission else '')
        self.mission_time.setText(mission.get('timing','') if mission else '')
        self.mission_kind.setCurrentIndex(max(0, self.mission_kind.findData(mission.get('kind') if mission else None)))

    def _preview_route(self):
        return self.active_proposal.query.route if self.active_proposal else self.query().route

    def _reload_photo_preview(self):
        if hasattr(self, 'photo_preview') and not self.photo_preview.isHidden():
            route = self._preview_route()
            if route:
                self.photo_preview.load(route, self.mission(route), preserve=True, subject=self._preview_point or self.capture_subject)
            else:
                self.close_photo_preview()

    def open_photo_preview(self):
        route = self._preview_route()
        if not route or self.coordinates_pending:
            self.context.status.emit('请先添加航线并确认坐标', 4000)
            return
        self.photo_preview.load(route, self.mission(route), subject=self._preview_point or self.capture_subject)
        self.map_changed.emit()
        self._focus_route(route)

    def _focus_route(self, route):
        if self.map_view and self.map_view.is_ready:
            self.map_view.focus_route(route_points(route))

    def _show_photo_frame(self, frame):
        self._photo_frame = frame
        if frame and self.shot_highlight != frame.get('shot_id'):
            self.shot_highlight = frame.get('shot_id')
            self.map_changed.emit()
        if frame and self.result_stack.currentWidget() is self.preview_panel:
            self.preview_live.setText(f"{frame['time'][11:19]} · {frame['shot'] or frame['phase']}")
            self.preview_live.show()
        elif hasattr(self,'preview_live'):
            self.preview_live.hide()
        active = bool(frame and self.result_stack.currentWidget() is self.result_panel)
        if active:
            bearing = f"{frame['heading']:.0f}°" if frame['heading'] is not None else '待设置'
            self.rehearsal_note.setText(f"航线预演 · {frame['time'][11:19]}\n"
                                        f"相对真高 {frame['altitude']:.0f} m · 镜头 {bearing} · "
                                        f"太阳高度 {frame['sun_elevation']:.0f}°")
        self.rehearsal_note.setVisible(active)
        if not self.result and self._pending is None:
            self.result_panel.empty.setVisible(not active)
        PhotoPreview.draw(self.map_view, frame)

    def sync_photo_frame(self):
        if self._photo_frame and not self.photo_preview.isHidden():
            PhotoPreview.draw(self.map_view, self._photo_frame)

    def close_photo_preview(self):
        was_open = not self.photo_preview.isHidden()
        self.photo_preview.stop()
        self.photo_preview.hide()
        self.shot_highlight = None
        self._show_photo_frame(None)
        if was_open:
            self.map_changed.emit()

    def _query_panel(self):
        panel = Card()
        scroll, body = scroll_contents()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        route = self._range_step()
        body.addWidget(route)
        self.time_card = self._time_step()
        body.addWidget(self.time_card)
        body.addWidget(self.route_editor)
        options = self._aerial_options()
        favorites = self._favorites_card()
        favorites.hide()
        options.body.addWidget(favorites)
        self.aerial_options.toggled.connect(favorites.setVisible)
        body.addWidget(options)
        body.addStretch()
        panel.body.addWidget(scroll, 1)
        footer = Card()
        self.check_button = ActionButton('查飞并保存为计划', self.check_and_save, variant='primary')
        self.takeoff_actions = QWidget()
        confirmation = QHBoxLayout(self.takeoff_actions)
        confirmation.setContentsMargins(0, 0, 0, 0)
        self.confirm_button = ActionButton('确认起飞点', self.confirm_candidate_takeoff, variant='primary')
        self.move_takeoff = ActionButton('在地图上改点', self.move_candidate_takeoff)
        confirmation.addWidget(self.confirm_button)
        confirmation.addWidget(self.move_takeoff)
        self.takeoff_actions.hide()
        footer.body.addWidget(self.takeoff_actions)
        footer.body.addWidget(self.check_button)
        footer.body.addWidget(self.route_editor.draft)
        self.preview_button = ActionButton('预演航线', self.open_photo_preview, variant='quiet')
        footer.body.addWidget(self.preview_button)
        self.score_button = ActionButton('查看航拍评分', self.show_route_score)
        footer.body.addWidget(self.score_button)
        panel.body.addWidget(footer)
        self.parameter_panel = panel
        return panel

    def _favorites_card(self):
        card = Card()
        card.body.addWidget(SectionHeader('常用飞行点'))
        self.favorites = QComboBox()
        self.favorites.setAccessibleName('常用飞行点')
        self.favorites.activated.connect(self._use_favorite)
        card.body.addWidget(self.favorites)
        row = QHBoxLayout()
        row.addWidget(ActionButton('收藏此地', self._new_favorite))
        row.addWidget(ActionButton('重命名', self._rename_favorite, variant='quiet'))
        row.addWidget(ActionButton('删除', self._delete_favorite, variant='quiet'))
        card.body.addLayout(row)
        self._refresh_favorites()
        return card

    def _favorite(self):
        return self.favorites.currentData()

    def _refresh_favorites(self, selected=None):
        records = self.context.civic.data['favorite_places']
        with QSignalBlocker(self.favorites):
            self.favorites.clear()
            self.favorites.addItem('选择收藏', None)
            for item in records:
                check = item.get('last_check')
                status = (HEADLINES[Verdict(check['verdict'])] + ' ' + instant(check['at']).astimezone(CHINA).strftime('%m-%d %H:%M')
                          if check else '未查询')
                self.favorites.addItem(f'{item["name"]} · {status}', item)
                if item['id'] == selected:
                    self.favorites.setCurrentIndex(self.favorites.count() - 1)

    def _new_favorite(self):
        if not self.points:
            self.context.status.emit('先在地图上选点', 5000)
            return
        name, accepted = QInputDialog.getText(self, '收藏此地', '名称')
        if not accepted or not name.strip():
            return
        self._store_favorite(name, self.query())

    def _store_favorite(self, name, query):
        point = query.route.takeoff if query.route else query.points[0]
        identifier = self.context.civic.save_favorite(self.city_id(), name, point)
        self._refresh_favorites(identifier)
        self.finder.refresh_origins()

    def _use_favorite(self):
        item = self._favorite()
        if item:
            self.city.setCurrentIndex(self.city.findData(item['city']))
            self.select_start(item['point'])

    def _rename_favorite(self):
        item = self._favorite()
        if item:
            name, accepted = QInputDialog.getText(self, '重命名收藏', '名称', text=item['name'])
            if accepted and name.strip():
                identifier = self.context.civic.save_favorite(item['city'], name, item['point'], item['id'])
                self._refresh_favorites(identifier)
                self.finder.refresh_origins()

    def _delete_favorite(self):
        item = self._favorite()
        if item:
            self.context.civic.delete_favorite(item['id'])
            self._refresh_favorites()
            self.finder.refresh_origins()

    def select_start(self, point, *, focus=True):
        self.takeoff_candidate = point
        self.candidate_button.setVisible(not self.point_mode and self.editor_step == 0)
        if focus and self.map_view and self.map_view.is_ready:
            self.map_view.focus_point(point)

    def use_candidate(self):
        if self.takeoff_candidate:
            self.set_takeoff(self.takeoff_candidate)

    def confirm_candidate_takeoff(self):
        if not self.confirm_takeoff or self.coordinates_pending:
            return
        self.confirm_takeoff = False
        self.route_editor.pick_takeoff = False
        self.route_editor.takeoff_button.setText('选起飞点')
        self.set_step(2)
        self.context.status.emit('起飞点已确认，可以查飞或保存草稿', 4000)

    def move_candidate_takeoff(self):
        if not self.coordinates_pending and not self.route_editor.pick_takeoff:
            self.route_editor.choose_takeoff()

    def set_takeoff(self, point):
        if not self.manual_enabled:
            self.set_manual(True)
        confirming = self.confirm_takeoff and bool(self.route_editor.waypoints)
        self.route_editor.remember()
        self.route_editor.takeoff = point
        self.route_editor.pick_takeoff = False
        self.route_editor.takeoff_button.setText('选起飞点')
        self.confirm_takeoff = False
        self.route_editor.sync()
        self.set_step(2 if confirming else 1)

    def continue_route(self):
        if not self.manual_enabled:
            self.set_manual(True)
        self.set_step(1)
        route = self.query().route
        if route:
            self._focus_route(route)

    def set_step(self, step):
        self.editor_step = step
        with QSignalBlocker(self.step_bar):
            self.step_bar.setCurrentIndex(step)
        self.time_card.setVisible(not self.point_mode and step == 2)
        self.route_editor.setVisible(not self.point_mode and step == 1)
        self.points_label.setVisible(not self.point_mode and step < 2)
        self.candidate_button.setVisible(not self.point_mode and step == 0 and self.takeoff_candidate is not None)
        self.drawing_route = step != 2
        self.describe_points()
        self.map_changed.emit()

    def set_map_mode(self, index):
        self.route_epoch += 1
        self.coordinates_pending = False
        self.point_mode = index == 0
        if not self.manual_enabled:
            self.set_manual(True)
        self.step_bar.setVisible(not self.point_mode)
        self.clear_preview()
        self.set_step(self.editor_step)
        self.invalidate()

    def new_query(self):
        self.city_drafts.clear()
        self.short_drafts.clear()
        self._detach_plan()
        self.load_mission(None)
        self.trip_baseline = None
        self._loading_plan = True
        self.refresh_cities()
        self.city.setCurrentIndex(max(0,self.city.findData(self.context.public_city)))
        self._loading_plan = False
        self.demo_mode = self.context.clock.demo
        self.point = self.takeoff_candidate = None
        self.route_editor.reset()
        self.set_step(0)
        self._refresh_favorites()

    def change_category(self):
        speeds = {'微型': 3, '轻型': 5, '小型': 8, '中型': 10, '大型': 12, '未知': 5}
        category = self.category.currentText()
        self.route_editor.speed.setMaximum(ROUTE_LIMITS[category][0])
        self.route_editor.speed.setValue(speeds[category])
        self.invalidate()

    def update_duration(self):
        editor = self.route_editor
        if not editor.takeoff or not editor.waypoints:
            self.duration_label.setText('—')
            return
        try:
            route = FlightRoute(tuple(editor.waypoints), editor.speed.value(), editor.takeoff,
                                self.form_start(), self.category.currentText(), complete_flight=editor.complete_flight)
        except ValueError:
            return
        seconds = int((route.end - route.start).total_seconds())
        minutes, seconds = divmod(seconds, 60)
        self.duration_label.setText(f'{minutes} 分 {seconds} 秒' if minutes else f'{seconds} 秒')
    def on_enter(self):
        available = not self.coordinates_pending
        self.route_editor.draft.setEnabled(available)
        self.confirm_button.setEnabled(available)
        self.move_takeoff.setEnabled(available)
        self.city.setFocus(Qt.OtherFocusReason)


    def on_leave(self):
        self.close_photo_preview()
        self.route_epoch += 1
        self.coordinates_pending = False

    def _weather_center(self):
        query = self.query()
        if query.route is None and self.points:
            return self.points[0]
        if query.points:
            try:
                query.geometry()
                return route_center(query)
            except ValueError:
                pass
        return tuple(self.context.civic.cities()[self.city_id()]['center'])

    def _set_preparation(self, checked):
        self._prepared = checked
        if self.editing:
            self.context.civic.update_plan_details(self.editing, preflight=sorted(checked))

    def _material_facts(self, purpose):
        if self.checked_query and self.result and not self.result.provisional:
            city = self.context.civic.cities()[self.city_id()]['name']
            status = [f'{item.title} · {item.summary}' for item in self.result.items]
            titles = [item['标题'] for item in model_notice_facts(self.checked_query, self._notices)] if self._notices else []
            return facts_for(city, self.checked_query, self.result.headline, purpose, status, titles)
        return None

    def _aerial_options(self):
        card = Card()
        self.aerial_options = Disclosure('航拍选项')
        self.aerial_options.row.addWidget(label('航拍选项', 'checkTitle'), 1)
        self.aerial_options.add_arrow()
        card.body.addWidget(self.aerial_options)
        details = QWidget()
        form = QFormLayout(details)
        form.setContentsMargins(0, 0, 0, 0)
        self.aircraft_model = QComboBox()
        for name in AIRCRAFT_PROFILES:
            self.aircraft_model.addItem(name or '请选择具体机型', name)
        self.capture_style = QComboBox()
        self.capture_style.addItems(['日间清晰', '日出', '日落', '夜景'])
        form.addRow('具体机型', self.aircraft_model)
        form.addRow('拍摄偏好', self.capture_style)
        details.hide()
        self.aerial_options.toggled.connect(details.setVisible)
        card.body.addWidget(details)
        self.aircraft_model.currentIndexChanged.connect(self._aerial_changed)
        self.capture_style.currentIndexChanged.connect(self._aerial_changed)
        return card

    def _parameter_summary(self):
        panel = Card()
        panel.setMaximumWidth(146)
        self.parameter_summary_label = label('', 'bodyText')
        panel.body.addWidget(SectionHeader('飞行条件'))
        panel.body.addWidget(self.parameter_summary_label)
        panel.body.addWidget(ActionButton('编辑参数', self._expand_parameters))
        panel.body.addStretch()
        return panel

    def _expand_parameters(self):
        self.param_summary.hide()
        self.parameter_panel.show()
        self.city.setFocus()

    def _range_step(self):
        card = Card()
        self.city = QComboBox()
        city_label = QLabel('城市')
        city_label.setBuddy(self.city)
        self.city_row = QWidget()
        row = QHBoxLayout(self.city_row)
        row.setContentsMargins(0,0,0,0)
        row.addWidget(city_label)
        row.addWidget(self.city, 1)
        row = QHBoxLayout()
        self.step_bar = SegmentedControl(('起飞点', '航点', '时间'))
        self.step_bar.setAccessibleName('航线编辑步骤')
        self.step_bar.currentIndexChanged.connect(self.set_step)
        card.body.addWidget(self.step_bar)
        row.addWidget(SectionHeader('航线'), 1)
        self.draw_button = ActionButton('修改航线', self.continue_route)
        row.addWidget(self.draw_button)
        card.body.addLayout(row)
        self.restore_point = ActionButton('恢复上次选点', self.restore_short_draft)
        self.restore_point.hide()
        card.body.addWidget(self.restore_point)
        self.points_label = label('', numeric=True)
        card.body.addWidget(self.points_label)
        self.candidate_button = ActionButton("使用收藏作为起飞点", self.use_candidate)
        self.candidate_button.hide()
        card.body.addWidget(self.candidate_button)
        self.radius = QSpinBox()
        self.radius.setRange(10, 2000)
        self.radius.setValue(75)
        self.radius.setSuffix(' 米')
        self.special = QComboBox()
        self.special.addItems(['常规飞行', '需核对特殊情形', '暂不确定'])
        self.route_editor = RouteEditor(self)
        return card

    def restore_short_draft(self):
        point = self.short_drafts.pop(self.city_id(), None)
        if point:
            self.route_editor.remember()
            prior = self.route_editor.snapshot()
            self.route_editor.restore(((point,), None, prior[2]))
            self.confirm_takeoff = False
            if self.route_editor.pick_takeoff:
                self.route_editor.choose_takeoff()
            self.drawing_route = True
            self.describe_points()
            self.map_changed.emit()

    def toggle_drawing(self):
        self.clear_preview()
        self.route_epoch += 1
        self.drawing_route = not self.drawing_route
        self.describe_points()
        self.map_changed.emit()

    def _time_step(self):
        card, form, self.time_complete = _form_step(3, '飞行时间')
        self.route_summary = label('')
        form.addRow(self.route_summary)
        form.addRow(ActionButton('调整航点', self.continue_route, variant='quiet'))
        self.start = QDateTimeEdit()
        self.start.setFont(themed_font(numeric=True))
        self.start.setCalendarPopup(True)
        self.start.setTimeZone(QTimeZone(8 * 3600))
        self.start.setDisplayFormat('yyyy-MM-dd HH:mm')
        start_label = label('起飞时间（北京时间）', 'bodyText')
        start_label.setBuddy(self.start)
        form.addRow(start_label)
        form.addRow(self.start)
        self.forecast_hint = label('', 'formWarning')
        form.addRow(self.forecast_hint)
        form.setRowVisible(self.forecast_hint, False)
        self.category = QComboBox()
        self.category.addItems(AIRCRAFT)
        self.category.setCurrentText('轻型')
        self.duration_label = label('—')
        form.addRow('预计飞行', self.duration_label)
        form.addRow('机型', self.category)
        form.addRow('特殊情形', self.special)
        self._time_form = form
        return card

    def _connect_form(self):
        self.city.currentIndexChanged.connect(self.change_city)
        self.start.dateTimeChanged.connect(self.invalidate)
        self.radius.valueChanged.connect(self.invalidate)
        self.category.currentIndexChanged.connect(self.change_category)
        self.special.currentIndexChanged.connect(self.invalidate)

    def context_tags(self):
        """Show the selected city and, when relevant, the exercise marker."""
        tags = [(self.context.civic.cities()[self.city_id()]['name'], 'info')]
        if self.demo_mode:
            tags.append(('演示资料', 'demo'))
        return tags

    def actions(self):
        """Offer reporting only in the Changsha exercise."""
        return ()

    def map_clicked(self, lat, lon):
        """Accept a resolved WGS84 map point."""
        self.map_point(lat, lon)

    def public_map_clicked(self, city, lat, lon):
        """Accept a provider point only for the current online city."""
        if city == self.city_id():
            self.map_point(lat, lon, gcj=True)

    def clear_editing(self, identifier):
        """Clear the edit target when the corresponding plan is cancelled."""
        if self.editing == identifier:
            self._set_editing(None)

    def _set_editing(self, identifier):
        self.editing = identifier
        self.result_panel.set_editing(identifier is not None)

    def city_id(self):
        """Return the current city identifier."""
        return self.city.currentData() or 'changsha'

    def refresh_cities(self):
        """Repopulate the city control while preserving the selected city."""
        current = self.city_id()
        self.city.blockSignals(True)
        self.city.clear()
        for identifier, city in self.context.civic.cities().items():
            self.city.addItem(city['name'], identifier)
        self.city.setCurrentIndex(max(0, self.city.findData(current)))
        self.city.blockSignals(False)

    def set_time(self, timestamp):
        """Set the visible Beijing time without invalidating the query."""
        date = timestamp.astimezone(CHINA).isoformat(timespec='seconds')
        with QSignalBlocker(self.start):
            self.start.setDateTime(QDateTime.fromString(date, Qt.ISODate))
        self.update_duration()

    def form_start(self):
        date, time = self.start.date(), self.start.time()
        return datetime(
            date.year(), date.month(), date.day(), time.hour(), time.minute(), time.second(),
            tzinfo=CHINA,
        )
    def query(self, *, strict=False):
        start = self.form_start()
        special = self.special.currentIndex()
        editor = self.route_editor
        category = self.category.currentText()
        demo = self.context.clock.demo or self.demo_mode
        if self.point_mode:
            start = system_now(CHINA)
            if strict and not self.point:
                raise ValueError('请在地图上选择查询位置')
            return FlightQuery((self.point,) if self.point else (), 'circle', 1, start,
                               start + timedelta(minutes=1), editor.default_height.value(), category,
                               special == 1, demo, special == 2)
        route = None
        if editor.waypoints and editor.takeoff:
            try:
                route = FlightRoute(tuple(editor.waypoints), editor.speed.value(), editor.takeoff, start, category,
                                    complete_flight=editor.complete_flight)
            except ValueError:
                if strict:
                    raise
        if strict and route is None:
            raise ValueError('请放置起飞点并添加航点')
        tolerance = self.context.civic.data.get('route_tolerances', {})
        width = tolerance.get(category, read_table('route-tolerances.json')['by_category'][category])
        return FlightQuery(tuple(self.points), 'route', width, start, start + timedelta(minutes=1),
                           0, category, special == 1, demo, special == 2, route)

    def change_city(self, *_):
        new_city = self.city_id()
        if self._draft_city and not self._loading_plan:
            self.city_drafts[self._draft_city] = (self.route_editor.snapshot(), self.form_start(),
                self.category.currentText(), self.radius.value(), self.editing)
        self._draft_city = new_city
        self.capture_subject = None
        self.subject_pick = False
        self.previous_mission = None
        self.trip_baseline = None
        self.context.public_city = new_city
        self._detach_plan()
        self.finder.city_changed()
        self.route_editor.reset()
        self.point = self.takeoff_candidate = None
        self.set_step(0)
        draft = self.city_drafts.get(new_city)
        if draft and not self._loading_plan:
            route, start, category, radius, identifier = draft
            self.set_time(start)
            self.category.setCurrentText(category)
            self.radius.setValue(radius)
            self.route_editor.restore(route)
            self.set_step(1 if route[1] else 0)
            self._set_editing(identifier)
        self.describe_points()

    def _detach_plan(self):
        self._set_editing(None)
        self._prepared.clear()

    def set_demo_mode(self, enabled):
        """Switch the public session between demonstration and formal queries."""
        if self.demo_mode == enabled:
            return
        self.demo_mode = enabled
        self._detach_plan()
        self.set_time(DEMO_START if enabled else system_now(CHINA).replace(second=0, microsecond=0))
        self.invalidate()
        self.demo_changed.emit(enabled)

    def describe_points(self):
        count = len(self.points)
        self.draw_button.setVisible(not self.point_mode and self.result is not None and not self.drawing_route)
        self.restore_point.hide()
        editor = self.route_editor
        if self.editor_step == 0:
            if editor.takeoff and editor.waypoints:
                from ...flight_route import WGS84
                lon, lat = editor.takeoff
                far = max(WGS84.Inverse(lat, lon, w.lat, w.lon)['s12'] for w in editor.waypoints)
                self.points_label.setText(f'离航线最远 {far:.0f} 米 · 目视建议 500 米内')
            else:
                self.points_label.setText('已放置起飞点' if editor.takeoff else '在地图上放置起飞点')
        else:
            self.points_label.setText(f'{count} 个航点' if count else '在地图上添加航点')
        self.route_summary.setText(
            f'已设 {count} 个航点 · 最高 {max(w.altitude_m for w in editor.waypoints):g} 米 · '
            f'航速 {editor.speed.value():.1f} 米/秒' if count else '尚未添加航点')
        ready_route = not self.point_mode and editor.takeoff is not None and count >= 1
        self.takeoff_actions.setVisible(ready_route and self.confirm_takeoff)
        self.check_button.setVisible(ready_route and not self.confirm_takeoff)
        self.preview_button.setVisible(ready_route)
        self.score_button.setVisible(ready_route)
        editor.draft.setVisible(ready_route)
        for index, ready in enumerate((bool(editor.takeoff) and not self.confirm_takeoff, count >= 1,
                                       bool(self.query().route))):
            self.step_bar.group.buttons()[index].setText(('起飞点', '航点', '时间')[index] + (' ✓' if ready else ''))

    def clear(self):
        self.route_epoch += 1
        self.confirm_takeoff = False
        self.drawing_route = True
        self.route_editor.remember()
        self.route_editor.restore(((), None, self.route_editor.last_speed, True))
        self.set_step(0)

    def map_point(self, lat, lon, gcj=False):
        if gcj or self._preview_point or not self.manual_enabled:
            return
        if self.subject_pick:
            self.finish_photo_subject((lon,lat))
            return
        if self.point_mode:
            self.point = (lon, lat)
            self._submit_query()
        elif self.editor_step == 0:
            self.set_takeoff((lon, lat))
        elif self.editor_step == 1 and self.drawing_route:
            self.route_editor.add(lon, lat)

    def check_and_save(self):
        if self.coordinates_pending:
            return
        self.save_after_check = True
        self.check()

    def draw_from_point(self):
        if self.point is not None:
            point = self.point
            if self.map_view:
                self.map_view.query_mode.setCurrentIndex(1)
            else:
                self.set_map_mode(1)
            self.set_takeoff(point)

    def coordinates(self):
        editor = self.route_editor
        current = '\n'.join(f'{p.lon:.8f},{p.lat:.8f}' for p in editor.waypoints)
        given, ok = QInputDialog.getMultiLineText(
            self, '航点坐标', '每行填写 WGS84 经度、纬度，顺序对应航点。', current)
        if not ok or given == current:
            return
        try:
            points = [lonlat(line.replace('，', ',').split(',')) for line in given.splitlines() if line.strip()]
            if not 1 <= len(points) <= 60:
                raise ValueError('请填写 1 至 60 个航点')
            if editor.waypoints and len(points) != len(editor.waypoints):
                raise ValueError('请在地图上添加或删除航点，再填写坐标')
            updated = tuple(
                replace(editor.waypoints[i], lon=x, lat=y) if editor.waypoints
                else Waypoint(x, y, editor.default_height.value())
                for i, (x, y) in enumerate(points))
        except (TypeError, ValueError) as error:
            QMessageBox.warning(self, '请核对坐标', str(error))
            return
        editor.remember()
        editor.restore((updated, editor.takeoff, editor.last_speed))

    def invalidate(self, *_):
        """Invalidate the current result and outstanding requests."""
        self._revision += 1
        self.saved_revision = None
        self._pending_route_save = False
        self.route_editor.draft.setText('保存草稿')
        self.update_duration()
        self._pending = None
        self._segments = ()
        self.route_timer.start()
        self.check_button.setEnabled(not self.coordinates_pending)
        self.result = None
        self.checked_query = None
        self._rule_result = None
        self._notices = None
        self.edit_result_route.hide()
        self.draw_button.hide()
        self.result_panel.reset()
        if self.aerial.active and self.manual_enabled:
            self.score_timer.start()
        self.aerial.reset()
        self.preparation.hide()
        self.public_note.hide()
        self.trip_note.hide()
        self.segment_reason.hide()
        self.segment_action.hide()
        self._time_form.setRowVisible(self.forecast_hint, False)
        self._aerial_check_cache.clear()
        if self.param_summary.isVisible():
            self._expand_parameters()
        self.time_complete.setVisible(bool(self.query().route))
        self.describe_points()
        self.map_changed.emit()
        self._reload_photo_preview()

    @Slot()
    def check(self):
        """Validate the current input and start a flight query."""
        if self.confirm_takeoff:
            self.save_after_check = False
            self.result_panel.show_message('请先在地图上确认起飞点')
            return
        if self.check_button.isEnabled():
            self._submit_query()

    def _submit_query(self):
        should_save = self.save_after_check
        self.save_after_check = False
        self.clear_preview()
        self.invalidate()
        try:
            query = self.query(strict=True)
            query.validate()
            query.geometry()
        except ValueError as exc:
            self.result_panel.show_message(str(exc))
            return
        revision = self._revision
        self._pending_route_save = should_save and not self.point_mode
        self._reopen_aerial = (self._reopen_aerial or should_save) and not self.point_mode and not query.demo
        self.check_button.setEnabled(False)
        self.drawing_route = False
        self.describe_points()
        self._start_query(revision, query)

    def _complete_query(self):
        self._pending = None
        self.check_button.setEnabled(not self.coordinates_pending)

    def _start_query(self, revision, query):
        self._pending = (revision, None)
        self.checked_query = query
        city_id = self.city_id()
        self._rule_result = self.context.civic.book(city_id).evaluate(query)
        if query.demo:
            self._complete_query()
            self._show_result()
            return
        self._show_result()
        self.result_panel.model.set_status(
            '等待公告检索完成…' if self.context.flight_model_config.key else '模型未配置',
        )
        self.context.run_flight_lookup(
            lambda: self.context.notice_lookup.search(city_id),
            lambda scan: self._notices_found(revision, query, scan),
            lambda: self._notices_found(revision, query, Scan()),
        )

    def _notices_found(self, revision, query, scan):
        if self._pending != (revision, None):
            return
        self._notices = scan
        self.check_button.setEnabled(True)
        self._route_scans[self.city_id()] = (system_now(CHINA), scan)
        if query.route:
            self._check_segments()
        self._show_result()
        self.aerial.set_verdict(self.result.verdict, refresh=True)
        config = self.context.flight_model_config
        if not config.key:
            self._complete_query()
            return
        city_id = self.city_id()
        city = self.context.civic.cities()[city_id]['name']
        geometry = self._rule_result['geometry']
        interpretation = self.result_panel.model
        interpretation.set_status('正在生成解读…')

        def assess():
            facts = [asdict(item) for item in published_evidence(city_id, query, geometry)]
            try:
                return FlightModel(config).assess(city, query, facts, model_notice_facts(query, scan))
            except TimeoutException:
                return None

        self.context.run_flight_lookup(
            assess,
            lambda checks: self._analysis_finished(
                revision, checks, MODEL_TIMEOUT_STATUS if checks is None else '暂无新增核对事项',
            ),
            lambda: self._analysis_finished(revision, (), 'AI 解读失败'),
        )
        QTimer.singleShot(MODEL_WAIT_SECONDS * 1000, self, lambda: self._analysis_finished(
            revision, (), MODEL_TIMEOUT_STATUS,
        ))

    def _analysis_finished(self, revision, checks, status=''):
        if self._pending != (revision, None):
            return
        self._complete_query()
        self._show_result()
        self.result_panel.model.set_status(status, checks)

    def _show_result(self):
        city_id = self.city_id()
        query = self.checked_query
        self.result = build_flight_check(query, self._rule_result, city_id=city_id, scan=self._notices)
        city = self.context.civic.cities()[city_id]['name']
        context = f'{city} · {flight_window(query.start, query.end, show_year=True)} · {query.altitude_agl} 米 · {query.category}'
        self.result_panel.show_check(self.result, context)
        self.edit_result_route.setVisible(not self.point_mode and query.route is not None)
        self.describe_points()
        missing = any(item.key == 'base_airspace' and item.summary == '尚未接入可计算边界'
                      for item in self.result.items)
        self.public_note.setText(f'{city}空域边界待核对' if missing else '')
        self.public_note.setVisible(missing)
        self._show_trip_changes()
        if self.point_mode and not self.result.provisional:
            self.result_panel.save_button.setEnabled(True)
        if self.result.verdict == Verdict.RESTRICTED:
            action = "定位受限航段"
        else:
            action = '更新计划' if self.editing else '保存计划'
        self.result_panel.save_button.setText('从这里画航线' if self.point_mode else action)
        if self._pending_route_save and not self.result.provisional and self.saved_revision != self._revision:
            self.saved_revision = self._revision
            self._pending_route_save = False
            if self.result.verdict != Verdict.RESTRICTED:
                current = next((row for row in self.context.civic.data['plans'] if row['id'] == self.editing), None)
                self.save_plan(current['title'] if current else f'{city}飞行计划 {query.start:%m-%d %H:%M}')
        if not self.result.provisional:
            model = self.aircraft_model.currentData()
            night = any(sun_position(route_center(query), instant(when))[0] < -.833 for when in (query.start, query.end))
            self.preparation.render(checklist(query.category, model, query.altitude_agl, night,
                                             query.special or query.special_unknown, self.result.verdict), self._prepared)
            self.preparation.show()
            if not query.demo and self._favorite_checked_revision != self._revision:
                self._favorite_checked_revision = self._revision
                self.context.civic.update_favorite_checks(city_id, query, self.result.verdict.value, system_now(CHINA))
                favorite = self._favorite()
                self._refresh_favorites(favorite['id'] if favorite else None)
        if not self.point_mode and (not query.demo or self.context.clock.demo):
            self.result_panel.aerial_launch.setVisible(not self.aerial.active)
            self.aerial.set_verdict(self.result.verdict)
            if self._reopen_aerial:
                self._reopen_aerial = False
                self.start_aerial()
        if not self.point_mode:
            self.route_timer.start()
        self.map_changed.emit()

    def start_aerial(self):
        if not self.checked_query or self.checked_query.demo and not self.context.clock.demo or not self.result:
            return
        city_id = self.city_id()
        city = self.context.civic.cities()[city_id]['name']
        self.result_panel.show_aerial(True)
        self.aerial.start(self.checked_query, city_id, self.aircraft_model.currentData(),
                          self.capture_style.currentText(), self.result.verdict, self.capture_subject)
        self.lookup_aerial_sceneries()
        self.result_panel.scroll.verticalScrollBar().setValue(0)

    def _aerial_changed(self, *_):
        if self.checked_query and self.result and not self.result.provisional:
            self._show_result()
        if self.aerial.active:
            self.start_aerial()

    def lookup_aerial_sceneries(self):
        if (not self.aerial.active or self.map_view is None
                or not self.map_view.is_ready or self._scenic_seq == self.aerial.sequence):
            return
        _, corridor, _, unproject = scenic_geometry(self.aerial.query, self.aerial.center)
        left, bottom, right, top = corridor.bounds
        if right - left > 20_000 or top - bottom > 20_000:
            return
        corners = (unproject(left, bottom), unproject(right, top))
        self._scenic_seq = self.aerial.sequence
        self.aerial.scenic_search_status('searching')
        self.map_view.run_js(f'lookupRouteScenery({self._scenic_seq},{json.dumps(corners)})')

    def _show_forecast_window(self):
        assessment = self.aerial.assessment
        if assessment and assessment.status in (FORECAST_OUTSIDE, FORECAST_PARTIAL):
            start, end = self.aerial.forecast.coverage
            window = flight_window(start, end, show_year=True)
            self.forecast_hint.setText(f'逐小时预报范围 {window}')
            self._time_form.setRowVisible(self.forecast_hint, True)
        else:
            self._time_form.setRowVisible(self.forecast_hint, False)

    @Slot(int, str)
    def aerial_scenic_resolved(self, revision, data):
        if not self.aerial.active or revision != self.aerial.sequence:
            return
        try:
            candidates = [(name[:100], lonlat(point)) for name, point in json.loads(data)[:10]
                          if isinstance(name, str)]
        except (ValueError, TypeError):
            self.aerial.scenic_search_status('error')
            return
        self.aerial.discover(candidates)

    def _check_aerial_window(self, start):
        base = self.aerial.query if self.aerial.active else self.checked_query
        if base is None:
            return Verdict.CHECK
        key = (base, start, id(self._notices))
        if key not in self._aerial_check_cache:
            query = replace(base,start=start,end=start+(base.end-base.start))
            city = self.city_id()
            geometry = self._rule_result['geometry'] if base is self.checked_query and self._rule_result else None
            result = self.context.civic.book(city).evaluate(query,geometry,alternatives=False)
            self._aerial_check_cache[key] = build_flight_check(query,result,city_id=city,scan=self._notices).verdict
        return self._aerial_check_cache[key]

    def _adopt_aerial_time(self, start):
        self._reopen_aerial = True
        self.alternate(start)

    def map_selected(self, identifier):
        if identifier.startswith('photo-shot:'):
            route = self._preview_route()
            rows = self.mission(route)['shots'] if route else ()
            if shot := next((row for row in rows if row['id']==identifier[11:]),None):
                self.focus_shot(shot)
        else:
            self.aerial.select_scene(identifier)

    def recheck_open_score(self):
        if self.manual_enabled and not self.point_mode and self.query().route:
            self.show_route_score()

    def show_route_score(self):
        if self.coordinates_pending:
            return
        try:
            query = self.query(strict=True)
        except ValueError as error:
            self.context.status.emit(str(error), 4500)
            return
        if not query.route:
            return
        self.result_stack.setCurrentWidget(self.result_panel)
        self.result_panel.empty.hide()
        self.result_panel.show_aerial(True)
        verdict = self.result.verdict if self.result and query == self.checked_query else Verdict.CHECK
        self.aerial.start(query,self.city_id(),self.aircraft_model.currentData(),
                          self.capture_style.currentText(),verdict,self.capture_subject)
        self.lookup_aerial_sceneries()

    def pick_photo_subject(self):
        self.subject_pick = not self.subject_pick
        self.map_changed.emit()

    def finish_photo_subject(self, point):
        self.subject_pick = False
        self.capture_subject = point
        self.aerial.set_subject(point)
        self._reload_photo_preview()
        self.map_changed.emit()

    def focus_shot(self, shot):
        self.shot_highlight = shot['id']
        self.map_changed.emit()
        route = self._preview_route()
        if route and self.photo_preview.isHidden():
            self.open_photo_preview()
        if route:
            self.photo_preview.stop()
            self.photo_preview.seek.setValue(round(shot['seconds'] * 10))
        if self.map_view and self.map_view.is_ready:
            self.map_view.focus_route(shot.get('path') or [shot['point']])

    def alternate(self, start):
        """Requery the selected alternative, superseding any pending interpretation."""
        self.set_time(start)
        self._submit_query()

    def start_save(self):
        """Reveal the local plan name next to the save action."""
        if self.point_mode:
            self.draw_from_point()
            return
        if self.result is None or self.result.provisional:
            return
        if self.result.verdict == Verdict.RESTRICTED:
            leg = next((leg for leg in self._segments if leg['state'] == 'restricted'), None)
            if leg and self.map_view:
                a, b = leg['path']
                self.map_view.focus_point(((a[0]+b[0])/2, (a[1]+b[1])/2))
            return
        civic = self.context.civic
        old = next((plan for plan in civic.data['plans'] if plan['id'] == self.editing), None)
        title = old['title'] if old else civic.cities()[self.city_id()]['name'] + '飞行计划'
        self.result_panel.open_editor(title)

    def save_plan(self, title, *, as_new=False):
        """Save the checked result in the local plan list."""
        if self.result is None or self.result.provisional:
            return
        identifier = self.context.safe(lambda: self.context.civic.save_plan(
            self.city_id(), title, self.checked_query, self.result.headline, None if as_new else self.editing,
            verdict=self.result.verdict.value, draft=self.result.verdict == Verdict.RESTRICTED,
            aerial=self._aerial_record(), preflight=self._prepared,
            basis=self._material_facts('') or {},
            mission=self.mission(self.checked_query.route) if self.checked_query.route else None,
            trip_snapshot=self.trip_snapshot(),
        ))
        if identifier:
            self.context.service.changed('更新本机计划候选')
            self._set_editing(identifier)
            self.result_panel.editor.hide()
            self.context.status.emit('已保存到我的计划', 5000)

    def trip_snapshot(self):
        assessment = getattr(self.aerial, 'assessment', None) if self.aerial.active else None
        weather = assessment.hazard or assessment.status if assessment else ''
        hours = []
        if assessment:
            for stamp, hour, *_ in assessment.rows:
                if hour:
                    gust = f'阵风 {hour.gust:g} 米/秒' if hour.gust is not None else ''
                    rain = f'降水 {hour.rain_probability:.0%}' if hour.rain_probability is not None else ''
                    hours.append(' · '.join(filter(None, (stamp.strftime('%H:%M'), hour.condition, gust, rain))))
        return {'flight': self.result.headline if self.result else '', 'weather': weather or '',
                'hours': hours}

    def _show_trip_changes(self):
        before = self.trip_baseline
        if before is None:
            self.trip_note.hide()
            return
        after = self.trip_snapshot()
        changes = []
        if before.get('flight') != after['flight']:
            changes.append(after['flight'])
        if self.aerial.active and getattr(self.aerial, 'assessment', None) is not None:
            if before.get('hours') != after['hours']:
                changes.append('；'.join(after['hours']) if after['hours'] else '小时预报待更新')
            if before.get('weather') != after['weather'] and after['weather']:
                changes.append(after['weather'])
        self.trip_note.setText('最新变化 · ' + '；'.join(changes) if changes else '与上次保存时相比，暂无变化')
        self.trip_note.show()

    def _aerial_record(self):
        if not self.aerial.active and not self.aircraft_model.currentData():
            return None
        score = self.aerial.score if self.aerial.active else None
        return {'model': self.aircraft_model.currentData(), 'style': self.capture_style.currentText(),
                'analyzed_at': system_now(CHINA).isoformat() if self.aerial.active else '',
                'score': displayed_score(score) if score is not None else None}

    def load_plan(self, plan, *, recheck=True):
        """Populate a stored flight plan and optionally query it immediately."""
        q = plan['query']
        self._loading_plan = True
        try:
            self.city.setCurrentIndex(self.city.findData(plan['city']))
        finally:
            self._loading_plan = False
        self.set_demo_mode(q['demo'])
        self.radius.setValue(q['radius_m'])
        self.category.setCurrentText(q['category'])
        self.special.setCurrentIndex(1 if q['special'] else 2 if q.get('special_unknown') else 0)
        self.set_time(instant(q['start']))
        if not q.get('route'):
            return
        if self.map_view:
            self.map_view.query_mode.setCurrentIndex(1)
        self.point_mode = False
        self.set_manual(True)
        route = FlightRoute.parse(q['route'])
        self.route_editor.load(route)
        self.set_step(2)
        self.drawing_route = True
        self._set_editing(None if plan.get('started_at') else plan['id'])
        self.describe_points()
        self.invalidate()
        aerial = plan.get('aerial') or {}
        self.aircraft_model.setCurrentIndex(max(0, self.aircraft_model.findData(aerial.get('model'))))
        self.capture_style.setCurrentIndex(max(0, self.capture_style.findText(aerial.get('style', ''))))
        self._prepared = set(plan.get('preflight', ()))
        self.load_mission(plan.get('mission'))
        self.trip_baseline = plan.get('trip_snapshot') if recheck else None
        if aerial.get('takeoff_pending'):
            self.confirm_takeoff = True
            self.set_step(2)
        self._focus_route(route)
        if recheck:
            self._reopen_aerial = not q['demo']
            self.check()

    def map_payload(self):
        """Build overlays and the current flight range for the shared map."""
        query = self.query()
        city_id = self.city_id()
        overlays = self._rule_overlays(query, city_id)
        if query.route and self.result:
            overlays.extend({'name': '计划飞行范围', 'rings': ring, 'kind': 'plan', 'highlight': True}
                            for ring in drawing(query.geometry()))
        route = self._preview_route()
        route_data = self._preview or (self._editable_route(query) if self.manual_enabled and not self.point_mode else None)
        if route_data and route:
            route_data = dict(route_data)
            if route.complete_flight:
                route_data['itinerary'] = [
                    {'path': [part.a.xy, part.b.xy], 'phase': part.phase} for part in timeline(route)
                ]
            if route_data['style'] == 'preview' or not self.photo_preview.isHidden():
                route_data.update(shots=self.mission(route)['shots'], selected_shot=self.shot_highlight)
        return {'observations': [], 'areas': [], 'grid': [], 'zones': [] if self._preview_point else overlays,
                'routes': [route_data] if route_data and (not self._preview_point or self._preview) else [],
                'route_editing': self.manual_enabled and (self.subject_pick or self.point_mode or self.drawing_route) and not self._preview_point,
                'subject_pick': self.subject_pick,
                'selected_waypoint': self.route_editor.index.currentIndex(),
                'photo_preview': not self.photo_preview.isHidden(),
                'route_epoch': self.route_epoch, 'route_ack':self.route_ack,
                'route_step':self.editor_step, 'route_height':self.route_editor.default_height.value(),
                'point_mode': self.point_mode,
                'pins': [dict(lon=self._preview_point[0],lat=self._preview_point[1],name=self._preview_subject)]
                if self._preview_point and not self._preview else [dict(lon=self.point[0],lat=self.point[1],name='查询点')]
                if self.point_mode and self.point else [dict(lon=self.capture_subject[0],lat=self.capture_subject[1],name='拍摄主体')]
                if self.capture_subject and self.manual_enabled else [],
                'city': city_id, 'center': self.context.civic.cities()[city_id]['center'],
                'public': True, 'aerial': None if self._preview else self.aerial.payload()}

    def _empty_preview(self):
        clear(self.preview_panel.body)
        clear(self.preview_actions)

    def show_proposals(self):
        self._preview = None
        self._preview_point = None
        self.active_proposal = None
        self.close_photo_preview()
        self.result_stack.setCurrentWidget(self.finder.results)
        self.map_changed.emit()

    def preview_proposal(self, proposal, window, *, reset_preview=False):
        self.active_proposal = window
        self.shot_highlight = None
        query = window.query
        key = getattr(self, '_preview_index', 0) + 1
        self._preview_index = key
        self._preview = dict(display_route(query.route),style='preview',key=key,provisional_takeoff=True,
                             subject=proposal.scene.point,subject_name=proposal.scene.name) if query.route else None
        self._preview_point = proposal.scene.point
        self._preview_subject = proposal.scene.name
        self._empty_preview()
        title = SectionHeader(proposal.scene.name)
        title.setTextFormat(Qt.PlainText)
        title.setWordWrap(True)
        self.preview_panel.body.addWidget(title)
        self.preview_panel.body.addWidget(label(SOURCE_LABELS[window.source_kind], 'bodyText'))
        if len(window.variants) > 1:
            styles = SegmentedControl(tuple(variant.label for variant in window.variants))
            styles.setAccessibleName('切换拍法')
            styles.setCurrentIndex(next(i for i, variant in enumerate(window.variants) if variant.key == window.style_key))
            styles.currentIndexChanged.connect(lambda index: self.preview_proposal(
                proposal, select_variant(window, window.variants[index]), reset_preview=True))
            self.preview_panel.body.addWidget(styles)
        if window.why_recommended:
            self.preview_panel.body.addWidget(label(window.why_recommended, 'bodyText'))
        summary = (f'综合评分 {displayed_score(window.score)} / 100' if window.score is not None else '天气待更新')
        components = [f'{title} {displayed_score(value)}' for title, value in window.components if value is not None]
        self.preview_panel.body.addWidget(label(' · '.join([summary, *components]), 'checkTitle'))
        self.preview_live = label('', 'checkTitle')
        self.preview_live.hide()
        self.preview_panel.body.addWidget(self.preview_live)
        self.preview_panel.body.addWidget(label(f'{query.start:%m-%d %H:%M}–{query.end:%H:%M} · 北京时间'))
        if query.route:
            arrival = query.start - timedelta(minutes=15)
            minutes, seconds = divmod(round((query.end-query.start).total_seconds()),60)
            high, far = flight_metrics(query.route)
            self.preview_panel.body.addWidget(label(
                f'预计 {minutes} 分 {seconds} 秒 · 最高 {high:.0f} 米 · 最远 {far:.0f} 米 · 建议到场 {arrival:%H:%M}'))
            path = self.mission(query.route)['shots']
            self.preview_panel.body.addWidget(SectionHeader('怎么拍'))
            for index, shot in enumerate(path,1):
                action = shot.get('action','拍摄')
                heading = f" · 朝向 {shot['heading']:.0f}°" if shot['heading'] is not None else ''
                pitch = f" · 云台 {shot['pitch']:.1f}°" if shot.get('pitch') is not None else ''
                shot_row = QHBoxLayout()
                shot_row.addWidget(ActionButton(f"{index}  {shot['title']}", lambda item=shot: self.focus_shot(item)))
                shot_row.addStretch(1)
                detail = Disclosure(f"镜头 {index} 飞行参数")
                detail.row.addWidget(label('参数'), 1)
                detail.add_arrow()
                shot_row.addWidget(detail)
                self.preview_panel.body.addLayout(shot_row)
                parts = (shot.get('move'), f"{shot_altitude(shot)} 米{heading}{pitch}", f"{shot['record_s']} 秒")
                self.preview_panel.body.addWidget(label(' · '.join(part for part in parts if part), 'bodyText'))
                if shot.get('tip'):
                    self.preview_panel.body.addWidget(label(shot['tip'], 'bodyText'))
                when = query.start + timedelta(seconds=shot['seconds'])
                direction = f" · 航向 {shot['bearing']:.0f}°" if shot.get('bearing') is not None else ''
                line = label(f"{when:%H:%M:%S} · {action} · "
                             f"{shot.get('speed',query.route.speed_mps):g} 米/秒{direction}")
                line.hide()
                detail.toggled.connect(line.setVisible)
                self.preview_panel.body.addWidget(line)
            self._director_details(proposal, window, path, key)
        alternatives = [(item, result) for item in self.finder.proposals for result in item.windows if result.query.route]
        if len(alternatives) > 1:
            compare = QComboBox()
            compare.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            compare.setMinimumContentsLength(14)
            compare.setAccessibleName('比较候选方案')
            for index, (item, result) in enumerate(alternatives):
                note = ' · 有预报的备选' if result.alternative_date else ''
                compare.addItem(f'{result.query.start:%m-%d %H:%M} · {item.scene.name}{note}')
                if item is proposal and result is window:
                    compare.setCurrentIndex(index)
            compare.activated.connect(lambda index: self.preview_proposal(*alternatives[index]))
            self.preview_panel.body.addWidget(compare)
        self.preview_panel.body.addWidget(Badge(HEADLINES[window.verdict] if query.route else '请先选起飞点',
                                                'ok' if window.verdict == Verdict.CLEAR else 'warn'))
        if not query.route and self.map_view and self.map_view.is_ready:
            self.map_view.focus_point(proposal.scene.point)
        if window.alternative_date:
            self.preview_panel.body.addWidget(label('预报覆盖范围内的备选时段','bodyText'))
        if window.status in (FORECAST_OUTSIDE, FORECAST_PARTIAL):
            self.preview_panel.body.addWidget(label(window.status,'bodyText'))
            backup = next(((item,result) for item,result in alternatives if result.alternative_date),None)
            if backup:
                self.preview_panel.body.addWidget(ActionButton('查看有预报的时段',
                                                             lambda pair=backup:self.preview_proposal(*pair)))
        if window.unsuitable and window.status:
            self.preview_panel.body.addWidget(label(window.status,'formWarning'))
        scoring = Disclosure('评分详情')
        scoring.row.addWidget(label('评分详情', 'checkTitle'), 1)
        scoring.add_arrow()
        details = Card()
        for index, (title, score) in enumerate(window.components):
            reason = window.reasons[index] if len(window.reasons) > index else ''
            value = f'{score:.0f} 分' if score is not None else '待补资料'
            details.body.addWidget(label(f'{title} · {value}', 'checkTitle'))
            if reason:
                details.body.addWidget(label(reason,'bodyText'))
        if details.body.count():
            details.hide()
            scoring.toggled.connect(details.setVisible)
            self.preview_panel.body.addWidget(scoring)
            self.preview_panel.body.addWidget(details)
        if query.route:
            self.preview_panel.body.addWidget(ActionButton('预演航线', self.open_photo_preview))
            self.preview_panel.body.addWidget(label('采用后确认候选起飞点','bodyText'))
            if proposal.scene.reference.startswith('https://www.openstreetmap.org/'):
                self.preview_panel.body.addWidget(_link('地面位置参考 · OpenStreetMap', proposal.scene.reference))
        else:
            self.preview_panel.body.addWidget(label('选择起飞点后可画航线并查询条件','bodyText'))
        self.preview_panel.body.addStretch()
        row = QHBoxLayout()
        row.addWidget(ActionButton('其他方案', self.show_proposals_only))
        row.addWidget(ActionButton('收起方案', self.finder.hide_results))
        self.preview_actions.addLayout(row)
        if window.unsuitable:
            self.preview_actions.addWidget(ActionButton('查看其他时段', self.show_proposals_only, variant='primary'))
        else:
            self.preview_actions.addWidget(ActionButton('采用这条航线' if query.route else '前往选起飞点',
                lambda: self.adopt_proposal(proposal, window, self.finder.selected_model), variant='primary'))
        self.result_stack.setCurrentWidget(self.preview_panel)
        self.map_changed.emit()
        if reset_preview and query.route:
            if not self.photo_preview.isHidden():
                self.photo_preview.load(query.route, self.mission(query.route), subject=proposal.scene.point)
        else:
            self._reload_photo_preview()

    def _director_details(self, proposal, window, shots, revision):
        disclosure = Disclosure('拍摄参数与现场提醒')
        disclosure.row.addWidget(label('拍摄参数与现场提醒', 'checkTitle'), 1)
        disclosure.add_arrow()
        self.preview_panel.body.addWidget(disclosure)
        if window.camera_advice:
            self.preview_panel.body.addWidget(label(window.camera_advice.split('。')[0] + '。', 'bodyText'))
        details = QWidget()
        body = QVBoxLayout(details)
        body.setContentsMargins(0, 0, 0, 0)
        for text in (window.camera_advice, *window.safety_notes):
            if text:
                body.addWidget(label(text, 'bodyText'))
        details.hide()
        disclosure.toggled.connect(details.setVisible)
        self.preview_panel.body.addWidget(details)
        config = self.context.flight_model_config
        if not config.key:
            return
        city, text = self.city_id(), self.mission_brief.text()
        elevation, azimuth = sun_position(proposal.scene.point, window.query.start)
        facts = {'place': proposal.scene.name, 'time': window.query.start.isoformat(),
                 'sun': {'elevation': round(elevation, 1), 'azimuth': round(azimuth, 1)},
                 'light': window.light, 'weather': window.weather_summary,
                 'shots': [{key: shot.get(key) for key in ('title', 'move', 'pitch', 'tip')} for shot in shots]}
        expired = False

        def expire():
            nonlocal expired
            expired = True

        def done(advice):
            if (not advice or expired or revision != self._preview_index or
                    self.active_proposal is not window or self.result_stack.currentWidget() is not self.preview_panel):
                return
            body.addWidget(label('AI 导演建议', 'checkTitle'))
            for text in (advice['why'], advice['camera_tips'],
                         *(f'镜头 {i} · {tip}' for i, tip in enumerate(advice['shot_tips'], 1) if tip),
                         advice['cautions']):
                if text:
                    item = label(text, 'bodyText')
                    item.setTextFormat(Qt.PlainText)
                    body.addWidget(item)

        QTimer.singleShot(10000, self, expire)
        self.context.run_aerial_lookup(lambda: FlightModel(config, timeout=10).direct_plan(city, text, facts),
                                       done, lambda: None)

    def show_proposals_only(self):
        self._preview = None
        self._preview_point = None
        self.active_proposal = None
        self.close_photo_preview()
        self.result_stack.setCurrentWidget(self.finder.results)
        self.map_changed.emit()

    def clear_preview(self):
        self._preview = None
        self._preview_point = None
        self.active_proposal = None
        self.close_photo_preview()
        self.shot_highlight = None
        self.result_stack.setCurrentWidget(self.result_panel if self.manual_enabled else self.recommend_empty)
        self.map_changed.emit()

    def adopt_proposal(self, proposal, window, model):
        city = self.city_id()
        query = window.query
        previous = self.query()
        if len(self.route_editor.waypoints) == 1 and previous.route is None:
            self.short_drafts[city] = self.route_editor.waypoints[0]
        if previous.route:
            old = next((p for p in self.context.civic.data['plans'] if p['id'] == self.editing), None)
            if not old or FlightQuery(**old['query']).record() != previous.record():
                identifier = self.context.safe(lambda: self.context.civic.save_plan(
                    city, '未完成的航线', previous, '待查飞', draft=True))
                if not identifier:
                    return
        self.finder.hide_results()
        self._detach_plan()
        self.previous_mission = None
        self.trip_baseline = None
        self.set_demo_mode(False)
        self.set_time(query.start)
        self.category.setCurrentText(query.category)
        self.radius.setValue(query.radius_m)
        if self.map_view:
            self.map_view.query_mode.setCurrentIndex(1)
        self.point_mode=False
        self.capture_subject = proposal.scene.point
        self.set_manual(True)
        if not query.route:
            self.route_editor.reset()
            self.set_step(0)
            if self.map_view and self.map_view.is_ready:
                self.map_view.focus_point(proposal.scene.point)
            return
        self.route_editor.load(query.route)
        self.confirm_takeoff = True
        self.set_step(2)
        self.aircraft_model.setCurrentIndex(max(0, self.aircraft_model.findData(model)))
        self.capture_style.setCurrentText(window.light if window.light in ('日出','日落','夜景') else '日间清晰')
        self._focus_route(query.route)
        self.context.status.emit('确认候选起飞点，或在地图上改点', 5000)

    def _editable_route(self, query):
        if not self.route_editor.waypoints:
            point = self.route_editor.takeoff
            return dict(path=[point], waypoints=[], takeoff=point, style="edit") if point else None
        if query.route:
            data = display_route(query.route)
        else:
            points = self.route_editor.waypoints
            data = {'path': [p.xy for p in points],
                    'waypoints': [[p.lon, p.lat, p.altitude_m] for p in points],
                    'takeoff': self.route_editor.takeoff or points[0].xy}
        return dict(data, style='edit', segments=self._segments, provisional_takeoff=self.confirm_takeoff,
                    heightLimit=min(120, ROUTE_LIMITS[self.category.currentText()][1]))

    def online_route_pending(self, city, epoch, pending):
        if city == self.city_id() and epoch == self.route_epoch:
            self.coordinates_pending = pending
            self.check_button.setEnabled(not pending and self._pending is None)
            self.route_editor.draft.setEnabled(not pending)
            self.confirm_button.setEnabled(not pending)
            self.move_takeoff.setEnabled(not pending)
            self.route_editor.hint.setText('正在确认地图坐标' if pending else '')
            if not pending:
                self.route_timer.start()

    def online_route_batch(self, city, epoch, revision, text):
        if city != self.city_id() or epoch != self.route_epoch or self._preview_point:
            return
        try:
            edits = json.loads(text)
        except (TypeError, ValueError):
            return
        if not isinstance(edits,list) or not 0 < len(edits) <= 64 or any(
                not isinstance(row,list) or len(row)!=4 or row[0] not in ('takeoff','waypoint','point','insert','move','subject')
                or type(row[1]) is not int or not all(isinstance(x,(float,int)) for x in row[2:])
                or not -180<=row[2]<=180 or not -89<=row[3]<=89 for row in edits):
            return
        self.route_ack = revision
        if self.point_mode:
            edits=edits[-1:]
        with QSignalBlocker(self):
            for action, index, lon, lat in edits:
                if action=='subject' and self.subject_pick:
                    self.finish_photo_subject((lon,lat))
                elif action=='takeoff' and not self.point_mode:
                    self.set_takeoff((lon,lat))
                elif action=='waypoint' and not self.point_mode:
                    self.route_editor.add(lon,lat)
                elif action=='point' and self.point_mode:
                    self.map_point(lat,lon)
                else:
                    self.route_action(city,action,index,lon,lat)
        self.map_changed.emit()

    def route_action(self, city, action, index, lon, lat):
        if city != self.city_id() or self._preview_point:
            return
        if action == 'add':
            self.map_point(lat, lon)
        elif action == 'insert' and self.drawing_route:
            self.route_editor.insert(index, lon, lat)
        elif action == 'move' and self.drawing_route:
            self.route_editor.move(index, lon, lat)
        elif action == 'remove' and self.drawing_route:
            self.route_editor.remove(index)
        elif action == 'select':
            self.route_editor.popup(index)

    def _check_segments(self):
        editor = self.route_editor
        if self.point_mode or self.coordinates_pending or not editor.waypoints:
            return
        try:
            query = self.query(strict=True)
        except ValueError as error:
            editor.hint.setText(str(error))
            return
        city = self.city_id()
        key = (self._revision, city)
        if self._segment_job is not None:
            if key != self._segment_job:
                self.route_timer.start()
            return
        book = self.context.civic.book(city)
        cached_at, cached_scan = self._route_scans.get(city, (None, None))
        scan = cached_scan if cached_at and system_now(CHINA)-cached_at < timedelta(minutes=10) else None
        self._segment_job = key
        editor.hint.setText('正在核对航段')

        def done(segments):
            self._segment_job = None
            if key == (self._revision, self.city_id()) and segments is not None:
                self._show_segments(query, segments)
            elif key != (self._revision, self.city_id()):
                self.route_timer.start()

        self.context.run_aerial_lookup(lambda: check_route_segments(query,city,book,scan), done, lambda:done(None))

    def _show_segments(self, query, segments):
        self._segments = segments
        editor = self.route_editor
        maximum = ROUTE_LIMITS[query.category][1]
        if query.altitude_agl > maximum:
            message = f'有航点超过 {maximum} 米'
        elif any(s['state'] == 'restricted' for s in segments):
            message = '红色航段有飞行限制 · 可保存草稿'
        elif any(s['missing_base'] for s in segments):
            message = '空域边界待核对'
        elif any(s['state'] in ('permit', 'check') for s in segments):
            message = '航线条件待核对'
        else:
            message = '航段核对完成'
        editor.hint.setText(message)
        first_issue = next(((i, row) for i, row in enumerate(segments, 1)
                            if row['state'] == 'restricted' and row['local_reason']), None)
        if first_issue is None:
            first_issue = next(((i, row) for i, row in enumerate(segments, 1)
                                if row['state'] == 'permit' and row['local_reason']), None)
        self.segment_target = first_issue[1]['path'] if first_issue else None
        self.segment_action.setVisible(bool(first_issue and self.result))
        self.segment_reason.setVisible(bool(first_issue and self.result))
        if first_issue:
            index, reason = first_issue
            state = '涉及限制' if reason['state']=='restricted' else '需核对'
            self.segment_action.setText(f'第 {index} 航段{state} · 定位')
            self.segment_action.setAccessibleDescription(reason['local_reason'])
            self.segment_reason.setText(reason['local_reason'])
            self.segment_action.setToolTip(reason['local_reason'])
        self.map_changed.emit()

    def focus_segment(self):
        if self.segment_target and self.map_view:
            (x1,y1),(x2,y2) = self.segment_target
            self.map_view.focus_point(((x1+x2)/2,(y1+y2)/2))

    def save_route_draft(self):
        def save():
            query = self.query(strict=True)
            if query.route is None:
                raise ValueError('请先添加航点')
            existing = next((p for p in self.context.civic.data['plans'] if p['id'] == self.editing), None)
            title = existing['title'] if existing else self.context.civic.cities()[self.city_id()]['name'] + '航线草稿'
            verdict = self.result.verdict.value if self.result else None
            headline = self.result.headline if self.result else '航线草稿'
            aerial = self._aerial_record() or {}
            if self.confirm_takeoff:
                aerial['takeoff_pending'] = True
            return self.context.civic.save_plan(self.city_id(), title, query, headline, self.editing,
                                                draft=True, verdict=verdict, aerial=aerial or None, mission=self.mission(query.route))
        identifier = self.context.safe(save)
        if identifier:
            self._set_editing(identifier)
            self.route_editor.draft.setText('草稿已保存')
            self.context.status.emit('已保存到我的计划', 5000)

    def _rule_overlays(self, query, city_id):
        restrictions = self._rule_result['restrictions'] if self._rule_result else ()
        marked = [rule.id for rule in restrictions]
        overlays = self.context.civic.book(city_id).overlays(query.start, query.demo, marked)
        for rule in restrictions:
            if not any(layer['name'] == rule.name for layer in overlays):
                overlays.append({
                    'name': rule.name, 'rings': rule.rings, 'kind': rule.kind, 'highlight': True,
                })
        return overlays

    @staticmethod
    def _draw_range(query):
        if query.points:
            try:
                return drawing(query.geometry())
            except ValueError:
                pass
        return []
