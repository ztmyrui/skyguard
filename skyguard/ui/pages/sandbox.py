"""Map-based editor, replay and side-by-side comparison for civil exercises."""
import json
from dataclasses import replace

from PySide6.QtCore import QDate, QTime, QTimer, Qt
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDateEdit, QFormLayout, QGridLayout, QHBoxLayout, QInputDialog,
    QLineEdit, QMenu, QMessageBox, QPushButton, QScrollArea, QSlider,
    QHeaderView, QSizePolicy, QSpinBox, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from ...sandbox import (
    CITY_EVENT_TEMPLATES, DIRECTOR_ACTIONS, DRONE_TYPES, DURATION, EMERGENCY_TYPES, LANDING_TYPES, STAFF_ROLES, WEATHERS,
    SandboxConfig, SandboxDrone, SandboxEmergency, SandboxSimulation, SandboxSite, SandboxStaff,
    compare_strategies, metres, offset, point_on_route, quick_example, suggest_staffing, typical_drones,
    verification_radius, within_city_radius,
)
from ...sandbox_store import SandboxLibrary
from ..widgets.common import ALTITUDE_TIP, ActionButton, Badge, Card, FEED_TONES, action_flow_row, label, with_info
from ..widgets.flight_journey import clear
from ..widgets.replay_slider import ReplaySlider
from .base import DutyMapPage


STEPS = ('① 时空事件', '② 无人机', '③ 人员策略', '④ 推演对比')
PLACE_REVISION = 1_000_000
ROLE_TONES = {'foot': 'info', 'vehicle': 'dispatch', 'guard': 'demo', 'recovery': 'ok'}
DIRECTOR_ICONS = {'airdrop': '⚡', 'gust': '🌪', 'battery': '🔋', 'blocked': '🚧'}
PRIORITIES = {'crowd_first': '人群与校园优先', 'fifo': '按发现顺序',
              'urgent_first': '高速与紧急备降优先'}
METRIC_TIPS = {
    'exposure_seconds': '未报备或异常尚未化解的飞行器在人群区域内的累计停留时长。',
    'unmet_dispatches': '需要现场人员处置时，缺少可调度人员的次数。',
    'score': '满分 100 分，计入处置闭环、人群暴露、响应时效、人员投入和外围备降等因素。',
}


class SandboxPage(DutyMapPage):
    title = '自由沙盘'

    def __init__(self, context):
        super().__init__(context)
        self.config = self._new_config(context.public_city)
        self.library = SandboxLibrary(context.root)
        self.snapshots = []
        self.step = 0
        self.drone_index = 0
        self.staff_index = 0
        self.site_index = 0
        self.tool = None
        self.anchor = None
        self.event_template = 'park'
        self.landing_kind = 'park_lawn'
        self.drone_batch = 0
        self.place_rows = []
        self.recent_places = []
        self.nearby_anchor = None
        self.search_text = ''
        self.search_status = ''
        self.located_status = ''
        self.nearby = False
        self.suggestion = ''
        self.simulation = None
        self.results = []
        self.compared = []
        self.road_cache = {}
        self.road_pending = {}
        self.road_sequence = 0
        self.variant = -1
        self.review = False
        self.place_revision = PLACE_REVISION
        self.visible_page = False

        columns = QHBoxLayout()
        columns.setSpacing(12)
        left, right = QWidget(), QWidget()
        left_box, right_box = QVBoxLayout(left), QVBoxLayout(right)
        left_box.setContentsMargins(0, 0, 0, 0)
        right_box.setContentsMargins(0, 0, 0, 0)
        right.setMinimumWidth(350)
        right.setMaximumWidth(420)
        columns.addWidget(left, 68)
        columns.addWidget(right, 32)
        self.body.addLayout(columns, 1)

        self.tool_bar = QWidget()
        self.tool_bar.setObjectName('sandboxToolBar')
        self.tool_bar.setAttribute(Qt.WA_StyledBackground)
        self.tool_bar.setFixedHeight(36)
        tools = QHBoxLayout(self.tool_bar)
        tools.setContentsMargins(8, 0, 4, 0)
        self.hint = label('', 'bodyText')
        self.hint.setWordWrap(True)
        tools.addWidget(self.hint, 1)
        self.undo = self._button(tools, '撤销一点', self._undo)
        self.done = self._button(tools, '完成', self._finish_tool)
        self.cancel_tool = self._button(tools, '取消', self._cancel_tool)
        left_box.addWidget(self.tool_bar)
        slot = QWidget()
        self.map_slot = QVBoxLayout(slot)
        self.map_slot.setContentsMargins(0, 0, 0, 0)
        left_box.addWidget(slot, 1)
        self.timeline = QWidget()
        play_row = QHBoxLayout(self.timeline)
        play_row.setContentsMargins(0, 0, 0, 0)
        self.play = self._button(play_row, '播放', self._toggle_play, 'primary')
        self.speed = self._combo(('1×', '2×', '4×', '8×'))
        play_row.addWidget(self.speed)
        self.cursor = ReplaySlider()
        self.cursor.setRange(0, DURATION)
        self.cursor.setAccessibleName('推演进度')
        self.cursor.event_activated.connect(self._pause)
        self.cursor.valueChanged.connect(self._render_live)
        play_row.addWidget(self.cursor, 1)
        self.clock = label('', 'bodyText')
        play_row.addWidget(self.clock)
        self._button(play_row, '重播', self._restart)
        left_box.addWidget(self.timeline)

        header = QHBoxLayout()
        header.addStretch()
        menu_button = QPushButton('预案库')
        menu = QMenu(menu_button)
        for title, handler in (('保存当前沙盘', self._save), ('载入已存沙盘', self._load),
                               ('删除已存沙盘', self._delete), ('载入本城示范考题', self.load_example)):
            menu.addAction(title, handler)
        menu_button.setMenu(menu)
        header.addWidget(menu_button)
        right_box.addLayout(header)
        steps = QHBoxLayout()
        steps.setSpacing(3)
        self.step_buttons = []
        self.step_group = QButtonGroup(self)
        for index, title in enumerate(STEPS):
            button = self._button(steps, title, lambda i=index: self.set_step(i))
            button.setCheckable(True)
            button.setObjectName("sandboxTab")
            self.step_group.addButton(button)
            self.step_buttons.append(button)
        right_box.addLayout(steps)
        self.status = label('', 'caption')
        self.status.setWordWrap(True)
        right_box.addWidget(self.status)
        self.status.hide()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_box.addWidget(self.scroll, 1)
        self.director = Card()
        self.director.body.addWidget(label('应急突发注入（压力测试）', 'section'))
        actions = tuple(DIRECTOR_ACTIONS)
        for pair in (actions[:2], actions[2:]):
            row = QHBoxLayout()
            self.director.body.addLayout(row)
            for action in pair:
                self._button(row, f'{DIRECTOR_ICONS[action]} {DIRECTOR_ACTIONS[action]}',
                             lambda a=action: self._inject(a), 'secondary')
        self.solidify = self._button(self.director.body, '把临场突发固化进考题', self._solidify, 'secondary')
        right_box.addWidget(self.director)
        footer = QWidget()
        self.footer = QHBoxLayout(footer)
        self.footer.setContentsMargins(0, 3, 0, 0)
        self.back = self._button(self.footer, '← 上一步', lambda: self.set_step(self.step-1))
        self.next = self._button(self.footer, '', lambda: self.set_step(self.step+1), 'primary')
        right_box.addWidget(footer)
        self.footer_widget = footer
        QShortcut(QKeySequence(Qt.Key_Escape), self, self._cancel_tool, context=Qt.WidgetWithChildrenShortcut)
        self.timer = QTimer(self, interval=1000)
        self.timer.timeout.connect(self._tick)
        self.set_step(0)

    @property
    def cities(self):
        return self.context.civic.cities()

    def _new_config(self, city):
        now = self.context.clock.now()
        return SandboxConfig(city, tuple(self.cities[city]['center']), now.date().isoformat(), '17:30')

    def context_tags(self):
        return ((self.cities[self.config.city]['name'], 'info'),)

    def actions(self):
        return ()

    def map_selection(self):
        return ''

    def on_enter(self):
        if self.config.city not in self.cities:
            self._reset(self._new_config(self.context.public_city))
        self.visible_page = True
        if self.map_view:
            self.map_view.place_resolved.connect(self._place_resolved)
            self.map_view.viewport_changed.connect(self._viewport_changed)
            self.map_view.sandbox_road_resolved.connect(self._road_resolved)
            self.map_view.sandbox_edit_action.connect(self._map_action)

    def on_leave(self):
        self.visible_page = False
        self.place_revision += 1
        self._pause()
        self.tool = None
        if self.map_view:
            self.map_view.place_resolved.disconnect(self._place_resolved)
            self.map_view.viewport_changed.disconnect(self._viewport_changed)
            self.map_view.sandbox_road_resolved.disconnect(self._road_resolved)
            self.map_view.sandbox_edit_action.disconnect(self._map_action)
            self.road_pending.clear()
        self._update_hint()

    @staticmethod
    def _combo(values):
        combo = QComboBox()
        entries = values.items() if isinstance(values, dict) else ((value, value) for value in values)
        for key, caption in entries:
            combo.addItem(caption, key)
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(8)
        return combo

    @staticmethod
    def _button(layout, title, callback, variant='quiet'):
        button = ActionButton(title, callback, variant=variant)
        layout.addWidget(button)
        return button

    @staticmethod
    def _number(low, high, value, suffix=''):
        number = QSpinBox()
        number.setRange(low, high)
        number.setSuffix(suffix)
        number.setValue(value)
        return number

    def _note(self, text=''):
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _update_config(self, **changes):
        new = replace(self.config, **changes)
        if new == self.config:
            return
        if self.snapshots and any(getattr(new, key) != getattr(self.config, key)
                                  for key in ('city', 'date_str', 'time_str', 'weather', 'sites', 'drones')):
            self.snapshots.clear()
            self._note('考题已更新，对比栏已清空。')
        self.config = new
        self.simulation = None
        self.variant = -1
        self.results = []
        self.map_changed.emit()

    def set_step(self, step):
        changed = self.step != step
        entering = self.step != 3 and step == 3
        if step == 3 and self.simulation is None:
            self._start_simulation()
            if self.simulation is None:
                return
        self._pause()
        self.tool = None
        self.step = step
        for index, button in enumerate(self.step_buttons):
            button.setChecked(step == index)
        self.timeline.setVisible(step == 3)
        if entering:
            self.review = False
            self.speed.setCurrentText('2×')
            self.cursor.setValue(0)
            self._toggle_play()
        self._render_step()
        if changed:
            self.scroll.verticalScrollBar().setValue(0)
        self.map_changed.emit()

    def _render_step(self):
        self.footer_widget.setVisible(self.step < 3)
        self.director.setVisible(self.step == 3 and not self.review)
        self.back.setVisible(self.step in (1, 2))
        self.next.setText(('下一步：设置无人机 →', '下一步：人员与策略 →', '▶ 启动沙盘推演', '')[self.step])
        self.tool_buttons = {}
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)
        (self._time_step, self._drone_step, self._staff_step, self._replay_step)[self.step](layout)
        layout.addStretch()
        position = self.scroll.verticalScrollBar().value()
        old = self.scroll.takeWidget()
        self.scroll.setWidget(page)
        if old:
            old.deleteLater()
        self.scroll.verticalScrollBar().setValue(position)
        self._update_hint()
        if self.step == 2:
            QTimer.singleShot(0, self, self._prefetch_roads)

    def _card(self, box, title=None):
        card = Card()
        box.addWidget(card)
        if title:
            card.body.addWidget(label(title, 'section'))
        return card.body

    def _tool_button(self, box, title, key, make_tool):
        button = self._button(box, title, lambda: self._set_tool(make_tool()))
        button.setCheckable(True)
        button.setProperty('sandboxTool', True)
        self.tool_buttons[key] = button
        return button

    def _list_row(self, box, caption, select, remove, *, selected=False, badge=None):
        row = QHBoxLayout()
        if badge:
            row.addWidget(Badge(*badge))
        item = self._button(row, caption, select, 'secondary' if selected else 'quiet')
        item.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        if '\n' in caption:
            item.setProperty('sandboxMultiline', True)
        item.setToolTip(caption)
        row.setStretchFactor(item, 1)
        delete = self._button(row, '×', remove, 'danger')
        delete.setAccessibleName(f'删除 {caption}')
        delete.setFixedWidth(32)
        box.addLayout(row)
        return item

    def _text(self, box, text):
        widget = label(text, 'bodyText')
        widget.setWordWrap(True)
        box.addWidget(widget)
        return widget

    def _time_step(self, box):
        root = box
        if not self.config.sites:
            guide = self._card(root)
            row = QHBoxLayout()
            row.addWidget(label('快速上手：搜索定位街区并点放现场，或'), 1)
            self._button(row, '一键载入本城示范考题', self.load_example, 'secondary')
            guide.addLayout(row)
        box = self._card(root, '时空与城市事件')
        form = QFormLayout()
        box.addLayout(form)
        city = self._combo({key: row['name'] for key, row in self.cities.items()})
        city.setCurrentIndex(city.findData(self.config.city))
        city.currentIndexChanged.connect(lambda _: self._change_city(city.currentData()))
        form.addRow('城市', city)
        row = QHBoxLayout()
        search = QLineEdit(placeholderText='搜索 15 公里内的地标')
        search.setAccessibleName('地标名称')
        search.setText(self.search_text)
        search.textEdited.connect(self._edit_search)
        search.returnPressed.connect(lambda: self._locate(search.text()))
        row.addWidget(search, 1)
        self._button(row, '定位', lambda: self._locate(search.text()))
        box.addLayout(row)
        self.search_feedback = self._text(box, self._search_message())
        self.search_feedback.setVisible(bool(self._search_message()))
        self.candidates = QVBoxLayout()
        self.candidates.setSpacing(2)
        box.addLayout(self.candidates)
        self._show_candidates()
        self._button(box, '探测周边实景', lambda: self._locate('', nearby=True))

        date = QDateEdit(QDate.fromString(self.config.date_str, Qt.ISODate), calendarPopup=True)
        date.setDisplayFormat('yyyy-MM-dd')
        date.dateChanged.connect(lambda value: self._update_config(date_str=value.toString(Qt.ISODate)))
        clock = QTimeEdit(QTime.fromString(self.config.time_str, 'HH:mm'))
        clock.setDisplayFormat('HH:mm')
        clock.setAccessibleName('时刻')
        clock.timeChanged.connect(lambda value: self._update_config(time_str=value.toString('HH:mm')))
        date_time = QHBoxLayout()
        date_time.addWidget(date, 3)
        date_time.addWidget(clock, 2)
        form.addRow('日期与时刻', date_time)
        quick = QHBoxLayout()
        for caption, time_str in (('放学 17:30', '17:30'), ('夜景 19:30', '19:30'), ('深夜 22:30', '22:30')):
            self._button(quick, caption, lambda value=time_str: clock.setTime(QTime.fromString(value, 'HH:mm')))
        box.addLayout(quick)
        weather = self._combo(WEATHERS)
        weather.setCurrentText(self.config.weather)
        weather.currentTextChanged.connect(lambda value: self._update_config(weather=value))
        weather_form = QFormLayout()
        weather_form.addRow('天气', weather)
        box.addLayout(weather_form)

        sites = self.config.sites
        site = sites[self.site_index] if self.site_index < len(sites) else None
        box = self._card(root, '地图落位')
        tools = QHBoxLayout()
        box.addLayout(tools)
        for index, entry in enumerate(sites):
            self._list_row(box, f'{entry.name} · {entry.radius_m} 米', lambda i=index: self._select_site(i),
                           lambda i=index: self._remove_site(i), selected=index == self.site_index,
                           badge=('现场', 'danger') if entry.kind == 'event' else ('备降', 'ok'))
        place = QFormLayout()
        box.addLayout(place)
        template = self._combo({k: value[0] for k, value in CITY_EVENT_TEMPLATES.items()})
        template.setCurrentIndex(template.findData(site.template if site and site.kind == 'event' else self.event_template))
        place.addRow('事件类型', template)
        landing = self._combo(LANDING_TYPES)
        landing.setCurrentIndex(landing.findData(site.landing_kind if site and site.kind == 'lawn' else self.landing_kind))
        place.addRow('降落点类型', landing)
        radius = QSlider(Qt.Horizontal)
        radius.setRange(80, 350)
        radius.setValue(site.radius_m if site else CITY_EVENT_TEMPLATES[template.currentData()][2])
        radius.setAccessibleName('影响半径')
        radius.setTracking(False)
        radius_row = QHBoxLayout()
        radius_row.addWidget(radius, 1)
        value = label(f'{radius.value()} 米', 'bodyText')
        radius_row.addWidget(value)
        place.addRow('范围', radius_row)

        def set_radius(new):
            value.setText(f'{new} 米')
            if site:
                self._edit_site(radius_m=new)
                self._render_step()

        def set_template():
            self.event_template = template.currentData()
            if site and site.kind == 'event':
                self._edit_site(template=self.event_template)
                self._render_step()
            elif not site:
                radius.setValue(CITY_EVENT_TEMPLATES[self.event_template][2])

        def set_landing():
            self.landing_kind = landing.currentData()
            if site and site.kind == 'lawn':
                self._edit_site(landing_kind=self.landing_kind)
                self._render_step()

        radius.valueChanged.connect(set_radius)
        template.currentIndexChanged.connect(set_template)
        landing.currentIndexChanged.connect(set_landing)
        for kind, title in (('event', '📍 点放事件核心'), ('lawn', '🌿 点放应急降落点')):
            self._tool_button(tools, title, kind, lambda k=kind: (
                k, template.currentData(), radius.value() if site is None or site.kind == k else
                CITY_EVENT_TEMPLATES[template.currentData()][2] if k == 'event' else 100, landing.currentData()))
        defaults = QHBoxLayout()
        self._button(defaults, '居中查看场景', self._center_scene)
        box.addLayout(defaults)

    def _select_site(self, index):
        self.site_index = index if index >= 0 else len(self.config.sites)
        self._finish_tool()
        self._render_step()

    def _edit_site(self, **values):
        rows = list(self.config.sites)
        rows[self.site_index] = replace(rows[self.site_index], **values)
        self._update_config(sites=tuple(rows))

    def _remove_site(self, index):
        self._update_config(sites=tuple(s for i, s in enumerate(self.config.sites) if i != index))
        self.site_index = len(self.config.sites)
        self._finish_tool()
        self._render_step()

    def _change_city(self, city):
        new = self._new_config(city)
        self.anchor = None
        self.place_revision += 1
        self.suggestion = ''
        self.search_text, self.search_status, self.located_status, self.place_rows = '', '', '', []
        self.recent_places = []
        self._update_config(city=city, center=new.center, sites=(), drones=(), staff=(), cues=())
        self.site_index = self.staff_index = self.drone_index = 0
        self.set_step(0)

    def _viewport_changed(self, west, south, east, north, zoom):
        if self.step != 3:
            self.anchor = ((west+east)/2, (south+north)/2)

    def _center_scene(self):
        if self.map_view:
            points = [site.point for site in self.config.sites]
            points.extend(point for drone in self.config.drones for point in drone.waypoints)
            points.extend(person.point for person in self.config.staff)
            if points:
                self.map_view.focus_route(points)
            else:
                self.map_view.focus_point(self.config.center, 10.8)

    def _edit_search(self, text):
        self.search_text = text
        self.located_status = ''
        self.place_revision += 1
        self.place_rows = []
        self._search_feedback('')
        self._show_candidates()

    def _search_feedback(self, status):
        self.search_status = status
        message = self._search_message()
        self.search_feedback.setText(message)
        self.search_feedback.setVisible(bool(message))

    def _search_message(self):
        if self.nearby and self.located_status and self.located_status != self.search_status:
            return f'{self.located_status}\n{self.search_status}'
        return self.search_status

    def _locate(self, text, *, nearby=False):
        if not nearby and not text.strip():
            return
        if not nearby and not self.nearby and self.place_rows:
            self._focus_place(self.place_rows[0])
            return
        self.nearby = nearby
        self.nearby_anchor = self.anchor if nearby else None
        if not self.map_view or not self.map_view.is_ready:
            self._search_feedback('周边未查到典型地标，可直接点地图自选' if nearby else '连接高德后可搜索地标')
            return
        self.place_revision += 1
        self.place_rows = []
        self._show_candidates()
        self._note()
        self._search_feedback('正在搜索…')
        self.map_view.lookup_sandbox_place(self.place_revision, self.cities[self.config.city]['name'], text.strip(), nearby)

    @staticmethod
    def _place_category(item):
        description = f'{item["name"]} {item.get("type") or ""}'
        if any(word in description for word in ('地铁站', '公交站', '出入口')):
            return None, None
        for words, event, landing in ((('学校', '大学', '中学', '小学'), 'school', None),
                                       (('商场', '购物中心'), 'shopping', None),
                                       (('公园',), 'park', 'park_lawn'), (('广场',), None, 'plaza'),
                                       (('停车场',), None, 'parking')):
            if any(word in description for word in words):
                return event, landing
        return None, None

    def _place_resolved(self, revision, payload):
        if not self.visible_page or self.step != 0 or revision != self.place_revision:
            return
        self.place_rows = []
        for item in json.loads(payload or '[]'):
            point = tuple(item['location'])
            inside, distance = within_city_radius(self.config.center, point)
            if inside and (not self.nearby_anchor or metres(self.nearby_anchor, point) <= 800):
                self.place_rows.append(dict(item, point=point, distance=distance))
        if self.nearby_anchor:
            seen = {(item['name'], item['point']) for item in self.place_rows}
            for item in self.recent_places:
                template, landing = self._place_category(item)
                if ((template or landing) and metres(self.nearby_anchor, item['point']) <= 800
                        and (item['name'], item['point']) not in seen):
                    self.place_rows.append(dict(item, template=template, landing=landing))
                    seen.add((item['name'], item['point']))
        elif not self.nearby:
            self.recent_places = self.place_rows[:20]
        self.place_rows = self.place_rows[:5]
        self._show_candidates()
        if self.place_rows and not self.nearby:
            self._focus_place(self.place_rows[0])
        else:
            self._search_feedback('请选择周边地标' if self.place_rows else
                                  '周边未查到典型地标，可直接点地图自选' if self.nearby else
                                  '未在 15 公里内找到该地标')

    def _show_candidates(self):
        while self.candidates.count():
            self.candidates.takeAt(0).widget().deleteLater()
        for row in self.place_rows:
            caption = f'{row["name"]} · {row["distance"]:.1f} 公里'
            button = self._button(self.candidates, caption, lambda item=row: self._use_place(item))
            button.setObjectName('sandboxPlaceResult')
            button.setToolTip(caption)
            button.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def _focus_place(self, row):
        self.anchor = row['point']
        self.map_view.focus_point(row['point'], 15.5, row['name'])
        self.located_status = f'已定位：{row["name"]}（距市中心 {row["distance"]:.1f} 公里）'
        self._search_feedback(self.located_status)

    def _use_place(self, row):
        self._focus_place(row)
        if self.nearby:
            kind = row.get('landing')
            if kind:
                self._add_site('lawn', row['point'], self.event_template, 100, kind)
            else:
                template = row.get('template') or self.event_template
                self._add_site('event', row['point'], template, CITY_EVENT_TEMPLATES[template][2], self.landing_kind)
            self._render_step()

    def _drone_step(self, box):
        root = box
        box = self._card(root, '无人机与航线')
        row = QHBoxLayout()
        self._button(row, '+ 添加无人机', self._add_drone, 'secondary')
        self._button(row, '切换预设机群' if self.config.drones else '生成预设机群', self._typical)
        box.addLayout(row)
        if not self.config.drones:
            self._text(box, '添加无人机后，在地图上点选起点、航点和终点。')
            return
        self.drone_index = min(self.drone_index, len(self.config.drones)-1)
        drone = self.config.drones[self.drone_index]
        self.drone_cards = []
        for index, entry in enumerate(self.config.drones):
            card = self._list_row(box, self._drone_caption(entry), lambda i=index: self._select_drone(i),
                                  lambda i=index: self._delete_drone(i), selected=index == self.drone_index)
            self.drone_cards.append(card)
        box = self._card(root, f'编辑 {drone.id}')
        form = QFormLayout()
        box.addLayout(form)
        name = QLineEdit(drone.name)
        name.textEdited.connect(lambda value: self._edit_drone(name=value.strip() or DRONE_TYPES[drone.drone_type][0]))
        form.addRow('名称', name)
        kind = self._combo({key: spec[0] for key, spec in DRONE_TYPES.items()})
        kind.setCurrentIndex(kind.findData(drone.drone_type))
        kind.currentIndexChanged.connect(lambda _: self._change_airframe(kind.currentData()))
        form.addRow('机型', kind)
        filed = QCheckBox('已报备')
        filed.setChecked(drone.filed)
        filed.toggled.connect(lambda state: self._edit_drone(filed=state))
        form.addRow('报备', filed)
        altitude = self._number(30, 150, drone.altitude_m)
        altitude.valueChanged.connect(lambda value: self._edit_drone(altitude_m=value))
        caption = QWidget()
        caption.setLayout(with_info(label('相对真高（m）', 'bodyText'), ALTITUDE_TIP))
        form.addRow(caption, altitude)
        delay = self._number(0, 60, drone.start_delay_s, ' 秒')
        delay.valueChanged.connect(lambda value: self._edit_drone(start_delay_s=value))
        form.addRow('起飞延迟', delay)
        draw = QHBoxLayout()
        self._tool_button(draw, '绘制航线', 'route', lambda: ('route',))
        self._button(draw, '清空航线', lambda: self._replace_waypoints(()), 'danger')
        box.addLayout(draw)
        self.route_count = self._text(box, f'航点 {len(drone.waypoints)} / 8')
        box.addWidget(label('突发事件', 'section'))
        for index in range(2):
            event = drone.emergencies[index] if index < len(drone.emergencies) else None
            editor = QHBoxLayout()
            event_kind = self._combo({'': '无突发'} | EMERGENCY_TYPES)
            event_kind.setCurrentIndex(event_kind.findData(event.kind if event else ''))
            trigger = self._number(0, 120, event.trigger_s if event else 20 + 20*index, ' 秒')
            trigger.setAccessibleName(f'第 {index+1} 项突发的触发秒数')
            trigger.setEnabled(event is not None)
            event_kind.currentIndexChanged.connect(
                lambda _, i=index, c=event_kind, t=trigger: self._edit_emergency(i, c.currentData(), t.value(), True))
            trigger.valueChanged.connect(
                lambda value, i=index, c=event_kind: self._edit_emergency(i, c.currentData(), value, False))
            editor.addWidget(event_kind, 1)
            editor.addWidget(trigger)
            box.addLayout(editor)

    @staticmethod
    def _drone_caption(drone):
        events = ' / '.join(EMERGENCY_TYPES[event.kind] for event in drone.emergencies) or '无'
        return f'{drone.id} · {drone.name} · {len(drone.waypoints)} 航点\n突发：{events}'

    def _select_drone(self, index):
        self.drone_index = index
        self.tool = None
        self._render_step()
        self.map_changed.emit()

    def _add_drone(self):
        ids = {drone.id for drone in self.config.drones}
        index = next(i for i in range(1, len(ids)+2) if f'D{i:02}' not in ids)
        self._update_config(drones=(*self.config.drones, SandboxDrone(f'D{index:02}', DRONE_TYPES['camera'][0])))
        self._select_drone(len(self.config.drones)-1)

    def _typical(self):
        self._update_config(drones=typical_drones(self.config, self.anchor, self.drone_batch), cues=())
        self.drone_batch += 1
        self._select_drone(0)

    def _edit_drone(self, **values):
        rows = list(self.config.drones)
        rows[self.drone_index] = replace(rows[self.drone_index], **values)
        self._update_config(drones=tuple(rows))
        caption = self._drone_caption(rows[self.drone_index])
        self.drone_cards[self.drone_index].setText(caption)
        self.drone_cards[self.drone_index].setToolTip(caption)

    def _change_airframe(self, kind):
        name, _, height, filed = DRONE_TYPES[kind]
        drone = self.config.drones[self.drone_index]
        old = DRONE_TYPES[drone.drone_type][0]
        automatic = {old, '日常巡检', '穿越练习', '物流航线'}
        self._edit_drone(drone_type=kind, altitude_m=height, filed=filed,
                         name=name if drone.name in automatic else drone.name)
        self._render_step()

    def _delete_drone(self, index):
        self._update_config(drones=tuple(drone for i, drone in enumerate(self.config.drones) if i != index))
        self._select_drone(max(0, min(self.drone_index, len(self.config.drones)-1)))

    def _edit_emergency(self, index, kind, trigger, rebuild):
        events = list(self.config.drones[self.drone_index].emergencies)
        if index < len(events):
            if kind:
                events[index] = SandboxEmergency(kind, trigger)
            else:
                events.pop(index)
        elif kind:
            events.append(SandboxEmergency(kind, trigger))
        self._edit_drone(emergencies=tuple(events))
        if rebuild:
            self._render_step()

    def _replace_waypoints(self, points):
        self._edit_drone(waypoints=points)
        self.route_count.setText(f'航点 {len(points)} / 8')
        self._update_hint()

    def _undo(self):
        if self.step == 1 and self.config.drones:
            self._replace_waypoints(self.config.drones[self.drone_index].waypoints[:-1])

    def _staff_step(self, box):
        root = box
        box = self._card(root, '人员布设')
        self._button(box, '建议布设', self._suggest, 'secondary')
        if self.suggestion:
            self._text(box, self.suggestion)
        for pair in (('foot', 'vehicle'), ('guard', 'recovery')):
            row = QHBoxLayout()
            box.addLayout(row)
            for role in pair:
                button = self._tool_button(row, STAFF_ROLES[role][0], role, lambda r=role: ('staff', r))
                button.setProperty('staffTone', ROLE_TONES[role])
                row.setStretchFactor(button, 1)
        if self.config.staff:
            self.staff_index = min(self.staff_index, len(self.config.staff)-1)
            event_points = [s.point for s in self.config.sites if s.kind == 'event']
            for index, person in enumerate(self.config.staff):
                gap = min((metres(person.point, point) for point in event_points), default=None)
                caption = person.name if gap is None else f'{person.name} · 距核心 {gap:.0f} 米'
                self._list_row(box, caption, lambda i=index: self._select_staff(i), lambda i=index: self._remove_staff(i),
                               selected=index == self.staff_index,
                               badge=(STAFF_ROLES[person.role][3], ROLE_TONES[person.role]))
            person = self.config.staff[self.staff_index]
            radius = verification_radius(person.role, self.config.weather)
            self._text(box, f'{person.name}的有效核验半径 · {radius:g} m')

        box = self._card(root, '对比方案')
        self._button(box, '保存到对比栏', self.save_snapshot, 'secondary')
        for index, saved in enumerate(self.snapshots):
            roles = ' + '.join(f'{count}{STAFF_ROLES[role][3]}' for role in STAFF_ROLES
                               if (count := sum(person.role == role for person in saved.staff)))
            policy = PRIORITIES[saved.strategy.priority]
            if saved.strategy.allow_reassign:
                policy += ' / 在途可改派'
            row = QHBoxLayout()
            snapshot = label(f'{saved.strategy.name} · {len(saved.staff)} 人\n{roles or "无现场人员"} · {policy}', 'bodyText')
            snapshot.setWordWrap(True)
            row.addWidget(snapshot, 1)
            self._button(row, '载入', lambda i=index: self._load_snapshot(i))
            self._button(row, '×', lambda i=index: self._remove_snapshot(i), 'danger').setAccessibleName('删除方案')
            box.addLayout(row)

        strategy = self.config.strategy
        box = self._card(root, '处置策略')
        form = QFormLayout()
        box.addLayout(form)
        name = QLineEdit(strategy.name)
        name.textEdited.connect(lambda value: self._strategy(name=value))
        form.addRow('方案名称', name)
        mode = self._combo({'auto': '自动处置', 'confirm': '关键确认'})
        mode.setCurrentIndex(mode.findData(strategy.mode))
        mode.currentIndexChanged.connect(lambda _: self._strategy(mode=mode.currentData()))
        form.addRow('协同模式', mode)
        priority = self._combo(PRIORITIES)
        priority.setCurrentIndex(priority.findData(strategy.priority))
        priority.currentIndexChanged.connect(lambda _: self._strategy(priority=priority.currentData()))
        form.addRow('调度优先级', priority)
        for key, title, tip in (
            ('allow_reassign', '允许在途动态改派', '新事件优先级更高时，可调整正在前往现场的人员任务。'),
            ('pre_alert', '开启电子围栏前置警示', '飞行器接近重点区域时，提前发出模拟电子警示。'),
        ):
            check = QCheckBox(title)
            check.setChecked(getattr(strategy, key))
            check.toggled.connect(lambda value, field=key: self._strategy(**{field: value}))
            box.addLayout(with_info(check, tip))

    def _select_staff(self, index):
        self.staff_index = index
        self._render_step()
        self.map_changed.emit()
        if self.map_view and self.map_view.is_ready:
            self.map_view.focus_point(self.config.staff[index].point, 16.6)

    def _remove_staff(self, index):
        self.staff_index = max(0, min(self.staff_index, len(self.config.staff) - 2))
        self._update_config(staff=tuple(p for i, p in enumerate(self.config.staff) if i != index))
        self._render_step()

    def _suggest(self):
        staff, self.suggestion = suggest_staffing(self.config)
        self.staff_index = 0
        self._update_config(staff=tuple(staff))
        self._render_step()

    def _strategy(self, **values):
        self._update_config(strategy=replace(self.config.strategy, **values))

    def _remove_snapshot(self, index):
        del self.snapshots[index]
        self.results = []
        self._render_step()

    def _load_snapshot(self, index):
        saved = self.snapshots[index]
        self.staff_index = 0
        self._update_config(staff=saved.staff, strategy=saved.strategy)
        self._render_step()

    def save_snapshot(self):
        try:
            self.config.validate()
        except ValueError as error:
            self._note(str(error))
            return
        if len(self.snapshots) >= 3:
            self._note('对比栏已满，可先清空再保存。')
            return
        names = {snapshot.strategy.name for snapshot in self.snapshots}
        name = self.config.strategy.name.strip()
        if not name or name == '当前策略' or name in names:
            name = next(f'方案 {i}' for i in range(1, 4) if f'方案 {i}' not in names)
        self.snapshots.append(replace(self.config, strategy=replace(self.config.strategy, name=name)))
        self.results = []
        self._note(f'已保存{name}')
        self._render_step()

    def _set_tool(self, tool):
        self.tool = None if self.tool == tool else tool
        self._note()
        self._update_hint()
        self.map_changed.emit()

    def _finish_tool(self):
        if self.tool and self.tool[0] == 'route' and self.config.drones and self.step == 1:
            count = len(self.config.drones[self.drone_index].waypoints)
            if count < 2:
                self._note('每条航线至少需要两个航点。')
                return
        self._cancel_tool()

    def _cancel_tool(self):
        if self.tool:
            self.tool = None
            self._update_hint()
            self.map_changed.emit()

    def _update_hint(self):
        route = bool(self.tool and self.tool[0] == 'route')
        if route:
            drone = self.config.drones[self.drone_index]
            text = f'点击地图添加 {drone.id} 航点（{len(drone.waypoints)}/8）'
        elif self.tool:
            text = {'event': '点击地图放置事件核心', 'lawn': '点击地图放置应急降落点',
                    'staff': '点击地图放置' + (STAFF_ROLES[self.tool[1]][3] if self.tool[0] == 'staff' else '')}[self.tool[0]]
        else:
            text = ''
        self.hint.setText(text)
        self.tool_bar.setVisible(bool(self.tool) and self.step < 3)
        self.undo.setVisible(route)
        key = (self.tool[1] if self.tool[0] == 'staff' else self.tool[0]) if self.tool else None
        for name, button in self.tool_buttons.items():
            button.setChecked(name == key)

    def map_clicked(self, lat, lon):
        if self.step == 3:
            return
        point = (lon, lat)
        inside, distance = within_city_radius(self.config.center, point)
        if not inside:
            if self.tool:
                self._note(f'该点距市中心 {distance:.1f} 公里，请在 15 公里内选点。')
            return
        if not self.tool:
            return
        kind = self.tool[0]
        if kind == 'route':
            points = self.config.drones[self.drone_index].waypoints
            if len(points) >= 8:
                self._note('已达 8 个航点，可撤销或完成画线。')
            elif not points or metres(points[-1], point) >= 1:
                self._replace_waypoints((*points, point))
            return
        if kind == 'staff':
            role = self.tool[1]
            ids = {person.id for person in self.config.staff}
            index = next(i for i in range(1, len(ids)+2) if f'P{i:02}' not in ids)
            person = SandboxStaff(f'P{index:02}', f'{STAFF_ROLES[role][3]} {index}', role, *point)
            self.staff_index = len(self.config.staff)
            self._update_config(staff=(*self.config.staff, person))
            self._render_step()
            return
        else:
            _, template, radius, landing = self.tool
            if not self._add_site(kind, point, template, radius, landing):
                return
        self._finish_tool()
        self._render_step()

    def _add_site(self, kind, point, template, radius, landing):
        rows = self.config.sites
        if sum(site.kind == kind for site in rows) >= 2:
            self._note('同类点位最多两个，可在地图上拖动已有点位。')
            return False
        ids = {site.id for site in rows}
        prefix = 'E' if kind == 'event' else 'L'
        identifier = next(f'{prefix}{i}' for i in (1, 2) if f'{prefix}{i}' not in ids)
        self.site_index = len(rows)
        self._update_config(sites=(*rows, SandboxSite(identifier, kind, *point, radius, template, landing)))
        return True

    def _map_action(self, action, identifier, waypoint, lon, lat):
        if self.step == 3:
            return
        if action == 'center':
            self.anchor = (lon, lat)
            return
        if action == 'cancel':
            self._cancel_tool()
            return
        point = (lon, lat)
        if not within_city_radius(self.config.center, point)[0]:
            self._note('拖动点位须在市中心 15 公里内。')
            if self.map_view:
                self.map_view.run_js('redrawSandbox()')
            return
        if action == 'site':
            self._update_config(sites=tuple(replace(site, lon=lon, lat=lat) if site.id == identifier else site
                                            for site in self.config.sites))
        elif action == 'staff' and self.step == 2:
            self._update_config(staff=tuple(replace(person, lon=lon, lat=lat, custom_routes=())
                                            if person.id == identifier else person for person in self.config.staff))
        elif action == 'waypoint' and self.step >= 1:
            self._update_config(drones=tuple(replace(drone, waypoints=tuple(point if index == waypoint else old
                                            for index, old in enumerate(drone.waypoints))) if drone.id == identifier else drone
                                            for drone in self.config.drones))
        self._render_step()

    def _prefetch_roads(self):
        if self.step != 2 or not self.visible_page or not self.map_view or not self.map_view.is_ready:
            return
        if self.road_pending:
            return
        for person in self.config.staff:
            if person.role not in ('foot', 'vehicle'):
                continue
            for site in self.config.sites:
                if site.kind != 'event' or metres(person.point, site.point) <= 150:
                    continue
                key = self.config.city, person.role, person.point, site.point
                if key in self.road_cache:
                    continue
                self.road_sequence += 1
                token = str(self.road_sequence)
                self.road_pending[token] = key
                args = json.dumps([token, person.role, person.point, site.point])
                self.map_view.run_js(f'lookupSandboxRoad(...{args})')
                return

    def _road_resolved(self, token, payload):
        key = self.road_pending.pop(token, None)
        if key is None:
            return
        points = tuple(map(tuple, json.loads(payload)))
        if len(self.road_cache) >= 100:
            self.road_cache.pop(next(iter(self.road_cache)))
        self.road_cache[key] = (key[2], *points, key[3]) if len(points)>=2 else ()
        self._prefetch_roads()

    def _with_roads(self, config):
        staff = []
        for person in config.staff:
            routes = tuple(path for site in config.sites if
                           (path := self.road_cache.get((config.city, person.role, person.point, site.point))))
            staff.append(replace(person, custom_routes=(*person.custom_routes, *routes)))
        return replace(config, staff=tuple(staff))

    def _start_simulation(self, variant=-1):
        self._pause()
        config = self._with_roads(self.config) if variant == -1 else (
            self.compared[variant] if self.results else self._with_roads(self.snapshots[variant]))
        try:
            self.simulation = SandboxSimulation(config)
        except ValueError as error:
            self.simulation = None
            self._note(str(error))
            return
        self._note()
        self.variant = variant
        self.cursor.blockSignals(True)
        self.cursor.setValue(0)
        self.cursor.blockSignals(False)
        self._refresh_event_marks()

    def _refresh_event_marks(self):
        events = [(max(event.trigger_s, drone.start_delay_s), EMERGENCY_TYPES[event.kind], FEED_TONES['突发'])
                  for drone in self.simulation.config.drones for event in drone.emergencies]
        events.extend((cue.at, DIRECTOR_ACTIONS[cue.action], FEED_TONES['临场']) for cue in self.simulation.cues)
        self.cursor.set_events(events)

    def _replay_step(self, box):
        row = QHBoxLayout()
        for review, caption in ((False, '实时推演'), (True, '复盘与策略对比')):
            button = self._button(row, caption, lambda value=review: self._show_review(value))
            button.setCheckable(True)
            button.setObjectName('sandboxTab')
            button.setChecked(self.review == review)
        box.addLayout(row)
        self.timeline.setVisible(not self.review)
        self.play.setEnabled(self.simulation is not None)
        if self.review:
            self._comparison(box)
            return
        self.live_box = self._card(box)
        self._live_content()

    def _inject(self, action):
        if self.simulation and self.simulation.inject(action, self.cursor.value()):
            self._note()
            self._refresh_event_marks()
            self._render_live()
        else:
            self._note('当前时刻没有可受影响的飞行器或人员。')

    def _solidify(self):
        if not self.simulation or not self.simulation.live_cues:
            return
        cues = self.simulation.cues
        self.config = replace(self.config, cues=cues)
        self.snapshots = [replace(snapshot, cues=cues) for snapshot in self.snapshots]
        self.simulation.config = replace(self.simulation.config, cues=cues)
        self.simulation.live_cues.clear()
        self.results = []
        self._note('临场事件已纳入当前考题及对比方案。')
        self._render_live()

    def _show_review(self, review):
        self._pause()
        self.review = review
        self._note()
        if review and len(self.snapshots) >= 2:
            self._evaluate_comparison()
        self._render_step()

    def _live_content(self):
        box = self.live_box
        clear(box)
        if not self.simulation:
            self._text(box, '完成场地和航线设置后即可推演。')
            return
        at = self.cursor.value()
        self.solidify.setVisible(bool(self.simulation.live_cues))
        frame = self.simulation.frame(at)
        t = self.simulation.config.time_str
        seconds = int(t[:2])*3600 + int(t[3:5])*60 + at
        self.clock.setText(f'{seconds//3600%24:02}:{seconds//60%60:02}:{seconds%60:02}')
        box.addWidget(label('城市低空智能值班助手', 'section'))
        box.addWidget(label('当前关注', 'section'))
        if not frame['attention_cards']:
            self._text(box, '当前飞行平稳。')
        captions = {'approve': '同意执行', 'reassign': '同意改派', 'decline': '暂不派员'}
        for card in frame['attention_cards']:
            self._button(box, f'{card["title"]} · {card["status"]}', lambda p=card['point']: self._focus(p), 'secondary')
            self._text(box, card['detail'])
            for action in card['actions']:
                self._button(box, captions[action], lambda cid=card['id'], a=action: self._decide(cid, a))
        box.addWidget(label('实时行动流', 'section'))
        for entry in reversed(frame['feed'][-7:]):
            at = entry['at']
            row = action_flow_row(entry['badge'], f'{at//60:02}:{at%60:02}', entry['text'],
                                  lambda p=entry['point']: self._focus(p))
            row.setMinimumHeight(max(48, row.layout().heightForWidth(max(245, self.scroll.width()-56))))
            box.addWidget(row)
        if not frame['feed']:
            self._text(box, '推演开始后，行动会显示在这里。')
        if at == DURATION or (self.cursor.events and at >= max(t for t, _, _ in self.cursor.events)
                              and not frame['attention_cards']):
            self._button(box, '查看本轮复盘与策略对比 →', lambda: self._show_review(True), 'primary')

    def _decide(self, identifier, action):
        if self.simulation.decide(self.cursor.value(), identifier, action):
            self._render_live()

    def _render_live(self):
        if self.step != 3:
            return
        if not self.review:
            self._live_content()
        self.map_changed.emit()

    def _focus(self, point):
        if self.map_view and point:
            self.map_view.focus_point(point)

    def _pause(self):
        self.timer.stop()
        self.play.setText('播放')

    def _toggle_play(self):
        if not self.simulation:
            return
        if self.timer.isActive():
            self._pause()
        else:
            if self.cursor.value() == DURATION:
                self._restart()
            self.play.setText('暂停')
            self.timer.start()

    def _tick(self):
        # At faster rates, stop at the first decision instead of skipping it.
        end = min(DURATION, self.cursor.value() + 2**self.speed.currentIndex())
        for at in range(self.cursor.value()+1, end+1):
            if any(card['actions'] for card in self.simulation.frame(at)['attention_cards']):
                end = at
                self._pause()
                break
        self.cursor.setValue(end)
        if end == DURATION:
            self._pause()

    def _restart(self):
        self.cursor.setValue(0)
        self._render_live()

    def _comparison(self, box):
        heading = QHBoxLayout()
        heading.addWidget(label('方案对比', 'section'), 1)
        if len(self.snapshots) >= 2:
            self._button(heading, '刷新对比', self._compare)
        box.addLayout(heading)
        if self.results:
            self._comparison_table(box)
        else:
            self._text(box, f'已保存 {len(self.snapshots)} 套方案，保存至少两套后即可对比。')
        if self.simulation:
            box.addWidget(label('本轮复盘', 'section'))
            metrics = self.simulation.evaluate()
            score = label(f'{metrics.name} · 综合治理评分 {metrics.score:g} 分', 'bodyText')
            score.setWordWrap(False)
            box.addLayout(with_info(score, METRIC_TIPS['score']))
            grid = QGridLayout()
            box.addLayout(grid)
            for column in range(3):
                grid.setColumnStretch(column, 1)
            fields = (('closure_rate', '处置闭环率'), ('avg_first_response_s', '首次响应'),
                      ('avg_arrival_s', '平均到位'), ('exposure_seconds', '人群暴露时长'),
                      ('unmet_dispatches', '运力缺口'))
            for index, (key, name) in enumerate(fields):
                metric = Card()
                metric.body.setContentsMargins(8, 5, 8, 5)
                metric.body.setSpacing(2)
                if key in METRIC_TIPS:
                    caption = label(name)
                    caption.setWordWrap(False)
                    metric.body.addLayout(with_info(caption, METRIC_TIPS[key]))
                else:
                    metric.body.addWidget(label(name))
                metric.body.addWidget(label(self._metric(metrics, key), 'section'))
                grid.addWidget(metric, index//3, index%3)
            for suggestion in metrics.suggestions:
                self._text(box, suggestion)

    def _comparison_table(self, box):
        best = compare_strategies(self.results)
        box.addWidget(Badge(f'推荐方案 · {best["best"]}', 'ok'))
        self._text(box, best['conclusion'])
        fields = (('staff_count', '投入人数'), ('closure_rate', '处置闭环率'), ('avg_first_response_s', '首次响应'),
                  ('avg_arrival_s', '平均到位'), ('exposure_seconds', '人群暴露时长'),
                  ('unmet_dispatches', '运力缺口'), ('score', '综合治理评分'))
        table = QTableWidget(len(fields), len(self.results)+1)
        table.setObjectName('sandboxComparison')
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setHorizontalHeaderLabels(['指标', *(row.name for row in self.results)])
        table.verticalHeader().hide()
        for index, (key, caption) in enumerate(fields):
            item = QTableWidgetItem(caption)
            table.setItem(index, 0, item)
            if key in METRIC_TIPS:
                item.setText('')
                item.setData(Qt.AccessibleTextRole, caption)
                item.setToolTip(METRIC_TIPS[key])
                metric = QWidget()
                name = label(caption, 'bodyText')
                name.setWordWrap(False)
                layout = with_info(name, METRIC_TIPS[key])
                layout.setContentsMargins(4, 0, 0, 0)
                metric.setLayout(layout)
                table.setCellWidget(index, 0, metric)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        table.setColumnWidth(0, 128)
        table.verticalHeader().setDefaultSectionSize(24)
        table.setFixedHeight(204)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for column, row in enumerate(self.results):
            for index, (key, _) in enumerate(fields):
                value = self._metric(row, key)
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(index, column+1, item)
                recommended = row.name == best['best']
                if recommended or key == 'score':
                    cell = label(value, 'sandboxCompareCell')
                    cell.setAlignment(Qt.AlignCenter)
                    cell.setProperty('tone', 'ok' if recommended else 'info')
                    cell.setProperty('score', key == 'score')
                    item.setText('')
                    item.setData(Qt.AccessibleTextRole, value)
                    table.setCellWidget(index, column+1, cell)
        box.addWidget(table)
        row = QHBoxLayout()
        for index, metrics in enumerate(self.results):
            self._button(row, f'回放{metrics.name}', lambda i=index: self._replay_variant(i))
        box.addLayout(row)

    @staticmethod
    def _metric(metrics, key):
        if key == 'closure_rate':
            return f'{metrics.resolved_count/metrics.total_incidents:.0%}' if metrics.total_incidents else '—'
        value = getattr(metrics, key)
        unit = {'staff_count': ' 人', 'unmet_dispatches': ' 次', 'score': ' 分'}.get(key, ' 秒')
        return '—' if value is None else f'{value:g}{unit}'

    def _compare(self):
        if len(self.snapshots) < 2:
            self._note('请在第 3 步保存至少两套方案。')
            return
        self._evaluate_comparison()
        self._note()
        self._render_step()

    def _evaluate_comparison(self):
        self.compared = [self._with_roads(config) for config in self.snapshots]
        self.results = [SandboxSimulation(config, assumed_confirmations=True).evaluate() for config in self.compared]

    def _replay_variant(self, index):
        self._start_simulation(index)
        self._show_review(False)
        self.map_changed.emit()

    def _save(self):
        name, ok = QInputDialog.getText(self, '保存沙盘', '预案名称')
        if ok:
            try:
                if name in self.library.list() and QMessageBox.question(self, '覆盖预案', f'覆盖「{name}」？') != QMessageBox.Yes:
                    return
                self.library.save(name, self.config)
                self._note(f'已保存「{name}」')
            except (ValueError, OSError) as error:
                self._note(str(error) if isinstance(error, ValueError) else '预案无法写入。')

    def _choose_plan(self, title):
        plans = self.library.list()
        if not plans:
            self._note('预案库还是空的。')
            return None
        name, ok = QInputDialog.getItem(self, title, '本机预案', plans, editable=False)
        return name if ok else None

    def _load(self):
        name = self._choose_plan('载入沙盘')
        if name:
            try:
                config = self.library.load(name)
                if config.city not in self.cities:
                    raise ValueError('城市已不可用')
                config = replace(config, center=tuple(self.cities[config.city]['center']))
                config.validate(draft=True)
                self._reset(config)
            except (KeyError, ValueError, TypeError, OSError):
                self._note('预案无法读取。')

    def _delete(self):
        name = self._choose_plan('删除沙盘')
        if name and QMessageBox.question(self, '删除预案', f'删除「{name}」？') == QMessageBox.Yes:
            try:
                self.library.delete(name)
                self._note(f'已删除「{name}」')
            except OSError:
                self._note('预案无法删除。')

    def load_example(self):
        self._reset(quick_example(self._new_config(self.config.city)))
        self._note()

    def _reset(self, config):
        self.snapshots.clear()
        self.results.clear()
        self.simulation = None
        self.variant = -1
        self.suggestion = ''
        self.config = config
        self.anchor = None
        self.search_text, self.search_status, self.located_status, self.place_rows = '', '', '', []
        self.recent_places = []
        self.drone_batch = 1 if config.drones else 0
        self.place_revision += 1
        self.staff_index = self.site_index = self.drone_index = 0
        self.set_step(0)

    def map_payload(self):
        config = self.config
        running = self.step == 3
        center = config.center
        payload = dict(city=config.city, center=center, public=False, grid=[], areas=[], observations=[],
                       pins=[], zones=[], routes=[], training_scene='sandbox-run' if running else 'sandbox-edit')
        if running:
            if self.simulation:
                config = self.simulation.config
            points = [point for drone in config.drones for point in drone.waypoints]
            points.extend(site.point for site in config.sites)
            points.extend(person.point for person in config.staff)
            points.extend(point for person in config.staff for route in person.custom_routes for point in route)
            if not points:
                points.append(center)
            west, south = offset((min(p[0] for p in points), min(p[1] for p in points)), -360, -360)
            east, north = offset((max(p[0] for p in points), max(p[1] for p in points)), 360, 360)
            payload.update(training_bounds=(west, south, east, north), civic_replay=True)
            if self.simulation:
                payload.update(self.simulation.layers(self.cursor.value()))
        else:
            payload['sandbox_edit'] = dict(
                center=center, tool=self.tool, step=self.step, selected=self.drone_index,
                sites=[dict(id=site.id, point=site.point, name=site.name, radius=site.radius_m, kind=site.kind)
                       for site in config.sites],
                drones=[dict(id=drone.id, points=drone.waypoints, selected=self.step == 1 and i == self.drone_index,
                             triggers=[dict(point=point_on_route(drone.waypoints,
                                 max(0, event.trigger_s-drone.start_delay_s) * DRONE_TYPES[drone.drone_type][1]),
                                 text=f'{event.trigger_s} 秒 · {EMERGENCY_TYPES[event.kind]}')
                                 for event in drone.emergencies if drone.waypoints])
                        for i, drone in enumerate(config.drones)],
                staff=[dict(id=p.id, point=p.point, role=p.role, name=p.name,
                            coverage_m=verification_radius(p.role, config.weather)
                            if self.step == 2 and i == self.staff_index else None)
                       for i, p in enumerate(config.staff)],
            )
        return payload
