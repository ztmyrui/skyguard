"""Shared web map and the unchanged JavaScript channel boundary."""
import json

from PySide6.QtCore import QObject, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QCheckBox, QFrame, QHBoxLayout, QVBoxLayout, QWidget

from ..amap_adapter import display_payload
from ..airspace_display import visible_grid
from ..official_airspace import HUNAN_EFFECTIVE
from ..theme import DEFAULT_THEME, colors
from .widgets.common import ActionButton, SectionHeader, label
from .widgets.map_legend import MapLegend


class MapPage(QWebEnginePage):
    """Apply the map background and keep SDK console output out of native logs."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.refresh_appearance()

    def refresh_appearance(self):
        self.setBackgroundColor(QColor(colors()['ground']))

    def javaScriptConsoleMessage(self, level, message, line, source):
        """Suppress SDK messages that can contain a public API key."""
        pass


class Bridge(QObject):
    """Expose the existing slots used by the map frontend."""

    ready = Signal(int)
    unavailable = Signal()
    selected = Signal(str)
    clicked = Signal(float, float)
    public_clicked = Signal(str, float, float)
    public_resolved = Signal(int, str)
    scenic_resolved = Signal(int, str)
    place_resolved = Signal(int, str)
    sandbox_road_resolved = Signal(str, str)
    sandbox_edit_action = Signal(str, str, int, float, float)
    status = Signal(str)
    configure = Signal()
    viewport = Signal(float, float, float, float, float)
    grid_point = Signal(float, float)
    weather_resolved = Signal(int, str)

    route_action = Signal(str, str, int, float, float)
    route_batch = Signal(str, int, int, str)
    route_pending = Signal(str, int, bool)
    sim_input = Signal(str, float, float)

    @Slot(str, float, float)
    def simInput(self, action, x, y):
        self.sim_input.emit(action, x, y)

    @Slot(str, str, int, float, float)
    def routeAction(self, city, action, index, lon, lat):
        self.route_action.emit(city, action, index, lon, lat)

    @Slot(str, int, int, str)
    def routeBatch(self, city, epoch, revision, text):
        self.route_batch.emit(city, epoch, revision, text)

    @Slot(str, int, bool)
    def routePending(self, city, epoch, pending):
        self.route_pending.emit(city, epoch, pending)

    @Slot(int, str)
    def weatherResolved(self, revision, points):
        self.weather_resolved.emit(revision, points)

    @Slot(float, float, float, float, float)
    def mapViewport(self, west, south, east, north, zoom):
        self.viewport.emit(west, south, east, north, zoom)

    @Slot(float, float)
    def gridPoint(self, lon, lat):
        self.grid_point.emit(lon, lat)

    @Slot()
    def configureMap(self):
        """Request the map configuration dialog."""
        self.configure.emit()

    @Slot(str)
    def selectEvent(self, event_id):
        """Forward a marker selection."""
        self.selected.emit(event_id)

    @Slot(float, float)
    def mapClicked(self, lat, lon):
        """Forward a WGS84 click."""
        self.clicked.emit(lat, lon)

    @Slot(str, float, float)
    def publicPoint(self, city, lat, lon):
        """Forward a public map click in provider coordinates."""
        self.public_clicked.emit(city, lat, lon)

    @Slot(int, str)
    def publicPointsResolved(self, revision, coordinates):
        """Forward the results of a coordinate conversion."""
        self.public_resolved.emit(revision, coordinates)

    @Slot(int, str)
    def publicScenicResolved(self, revision, places):
        self.scenic_resolved.emit(revision, places)

    @Slot(int, str)
    def publicPlaceResolved(self, revision, places):
        self.place_resolved.emit(revision, places)

    @Slot(str, str)
    def sandboxRoadResolved(self, token, points):
        self.sandbox_road_resolved.emit(token, points)

    @Slot(str, str, int, float, float)
    def sandboxEditAction(self, action, identifier, index, lon, lat):
        self.sandbox_edit_action.emit(action, identifier, index, lon, lat)

    @Slot(int)
    def mapReady(self, count):
        """Notify the map widget that the frontend is ready."""
        self.ready.emit(count)

    @Slot(str)
    def mapStatus(self, text):
        """Forward the map's user-facing status."""
        self.status.emit(text)

    @Slot()
    def mapUnavailable(self):
        self.unavailable.emit()


class MapView(QWidget):
    """Host one map, its controls and the current provider state."""

    ready = Signal()
    viewport_changed = Signal(float, float, float, float, float)
    selected = Signal(str)
    clicked = Signal(float, float)
    public_clicked = Signal(str, float, float)
    public_resolved = Signal(int, str)
    scenic_resolved = Signal(int, str)
    place_resolved = Signal(int, str)
    sandbox_road_resolved = Signal(str, str)
    sandbox_edit_action = Signal(str, str, int, float, float)
    status = Signal(str)
    configure = Signal()
    reconnecting = Signal()
    sim_input = Signal(str, float, float)
    route_action = Signal(str, str, int, float, float)
    route_batch = Signal(str, int, int, str)
    route_pending = Signal(str, int, bool)
    grid_point = Signal(float, float)
    layers_changed = Signal()
    weather_resolved = Signal(int, str)

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self._theme = getattr(QApplication.instance(), 'skyguard_theme', DEFAULT_THEME)
        self._ready = False
        self._document_loaded = False
        self.airspace_city = ''
        self.civic_exercise = False
        self.airspace_public = True
        self.latest_bounds = None
        self.grid_timer = QTimer(self)
        self.grid_timer.setSingleShot(True)
        self.grid_timer.setInterval(300)
        self.grid_timer.timeout.connect(self._draw_grid)
        self.setObjectName('mapSurface')
        self.setAttribute(Qt.WA_StyledBackground)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        layout.addLayout(self._controls())
        self.editor_hint = label("")
        self.editor_hint.hide()
        editor_controls = QHBoxLayout()
        editor_controls.setSpacing(6)
        editor_controls.addWidget(self.editor_hint,1)
        editor_controls.addWidget(self.undo_route)
        editor_controls.addWidget(self.redo_route)
        layout.addLayout(editor_controls)
        self.layer_row = QWidget()
        layers = QHBoxLayout(self.layer_row)
        layers.setContentsMargins(0, 0, 0, 0)
        self.grid_check = QCheckBox('适飞网格')
        self.grid_check.setChecked(False)
        self.grid_check.hide()
        self._drawn_grid = None
        self.grid_check.toggled.connect(self._grid_switch)
        layers.addWidget(self.grid_check)
        self.duty_layers = {}
        for key, title in (('events','其他事件'),('areas','关注区域'),('posts','观察岗')):
            toggle = QCheckBox(title)
            toggle.toggled.connect(lambda _: self.layers_changed.emit())
            self.duty_layers[key] = toggle
            layers.addWidget(toggle)
            toggle.hide()
        self.airspace_note = label('')
        layers.addWidget(self.airspace_note, 1)
        self.legend = MapLegend()
        self.legend.hide()
        layout.addWidget(self.legend)
        layout.addWidget(self.layer_row)
        self.layer_row.hide()
        self.web = self._make_web_view()
        layout.addWidget(self.web, 1)
        self.web.loadFinished.connect(self._theme_after_load)
        self.note = label('', 'mapnote')
        self.note.hide()
        self.note_timer = QTimer(self, singleShot=True, interval=6500)
        self.note_timer.timeout.connect(lambda: self.set_note(''))
        layout.addWidget(self.note)
        self._load_map(QUrl(context.map_server.url))

    def _controls(self):
        row = QHBoxLayout()
        row.setSpacing(4)
        self.heading = SectionHeader('长沙地图')
        row.addWidget(self.heading)
        from .widgets.common import SegmentedControl
        self.query_mode = SegmentedControl(('查飞行地点', '编辑航线'))
        self.query_mode.setCurrentIndex(1)
        row.addWidget(self.query_mode)
        self.undo_route = ActionButton('撤销', None, variant='quiet')
        self.redo_route = ActionButton('重做', None, variant='quiet')
        for button, name in ((self.undo_route, '撤销'), (self.redo_route, '重做')):
            button.setAccessibleName(name)
            button.setToolTip(name)
            button.setMinimumWidth(50)
            button.hide()
        row.addStretch()
        self.legend_button = ActionButton('图例', self._toggle_legend, variant='quiet')
        self.legend_button.setCheckable(True)
        self.legend_button.setToolTip('展开或收起当前场景的地图符号说明')
        row.addWidget(self.legend_button)
        self.configure_button = ActionButton('配置地图', self.configure.emit, variant='quiet')
        self.configure_button.hide()
        row.addWidget(self.configure_button)
        self.home = ActionButton('居中全览', lambda: self.run_js('home()'), variant='quiet')
        self.home.setToolTip('恢复当前城市或场景的默认地图视野')
        row.addWidget(self.home)
        return row

    def set_civic_exercise(self, enabled):
        self.civic_exercise = enabled
        self._sync_layers()

    def set_legend_mode(self, mode):
        if self.legend.mode != mode:
            self.legend.set_mode(mode)

    def _toggle_legend(self):
        self.legend.setVisible(self.legend_button.isChecked())
        self._sync_layers()

    def _sync_layers(self):
        self.layer_row.setVisible(self.legend_button.isChecked() and not self.civic_exercise
                                  and bool(self.airspace_city))

    def attach_clock(self, timeline):
        self.timeline = timeline
        self.clock_overlay = QFrame(self)
        self.clock_overlay.setObjectName('timeline')
        self.clock_overlay.setAttribute(Qt.WA_StyledBackground)
        controls = QVBoxLayout(self.clock_overlay)
        controls.setContentsMargins(4,4,4,4)
        self.clock_button = ActionButton('', self._toggle_clock)
        controls.addWidget(self.clock_button)
        controls.addWidget(timeline)
        timeline.hide()
        self.clock_overlay.hide()

    def set_clock_visible(self, visible):
        self.clock_overlay.setVisible(visible)
        if not visible:
            self.timeline.hide()
        self.refresh_clock()

    def _toggle_clock(self):
        self.timeline.setVisible(self.timeline.isHidden())
        self.refresh_clock()

    def refresh_clock(self):
        if not hasattr(self, 'clock_overlay') or self.clock_overlay.isHidden():
            return
        self.clock_button.setText('演练控制 · ' + self.timeline.clock.text())
        area = self.web.geometry()
        width = min(475, area.width()-20) if not self.timeline.isHidden() else self.clock_button.sizeHint().width()+8
        self.clock_overlay.setFixedWidth(max(170,width))
        self.clock_overlay.adjustSize()
        self.clock_overlay.move(area.x()+8, area.bottom()-self.clock_overlay.height()-28)
        self.clock_overlay.raise_()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.refresh_clock()

    def layer_flags(self):
        return {key: box.isChecked() for key,box in self.duty_layers.items()}

    def set_compact(self, compact):
        """Keep the map, legend and attribution readable above planning tabs."""
        self.setProperty('compact', compact)
        self.layout().setContentsMargins(8, 4 if compact else 8, 8, 4 if compact else 8)
        self.layout().setSpacing(2 if compact else 4)
        self.home.setMinimumHeight(24 if compact else 32)

    def _make_web_view(self):
        web = QWebEngineView(self)
        web.setPage(MapPage(web))
        web.setMinimumSize(420, 300)
        profile = web.page().profile()
        profile.setHttpUserAgent(profile.httpUserAgent() + ' SkyguardDesktop/0.3')
        profile.setPersistentStoragePath(str(self.context.root / 'web-profile'))
        profile.setCachePath(str(self.context.root / 'map-cache'))
        profile.setHttpCacheType(QWebEngineProfile.DiskHttpCache)
        settings = web.settings()
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        settings.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        self.bridge = Bridge(self)
        self.bridge.ready.connect(self._map_ready)
        self.bridge.unavailable.connect(self._map_unavailable)
        self.bridge.status.connect(self.status)
        self.bridge.selected.connect(self.selected)
        self.bridge.clicked.connect(self.clicked)
        self.bridge.route_action.connect(self.route_action)
        self.bridge.sim_input.connect(self.sim_input)
        self.bridge.public_clicked.connect(self.public_clicked)
        self.bridge.public_resolved.connect(self.public_resolved)
        self.bridge.scenic_resolved.connect(self.scenic_resolved)
        self.bridge.place_resolved.connect(self.place_resolved)
        self.bridge.sandbox_road_resolved.connect(self.sandbox_road_resolved)
        self.bridge.sandbox_edit_action.connect(self.sandbox_edit_action)
        self.bridge.viewport.connect(self._viewport)
        self.bridge.weather_resolved.connect(self.weather_resolved)
        self.bridge.grid_point.connect(self.grid_point)
        self.bridge.configure.connect(self.configure)
        self.status.connect(self.set_note)
        self.channel = QWebChannel(web.page())
        self.channel.registerObject('bridge', self.bridge)
        web.page().setWebChannel(self.channel)
        return web

    @property
    def is_ready(self):
        """Return whether the AMap frontend has reported readiness."""
        return self._ready

    def reconnect(self):
        """Reload AMap after a settings change."""
        self._load_map(QUrl(self.context.map_server.url))
        self.reconnecting.emit()

    def _load_map(self, url):
        self._ready = False
        self._document_loaded = False
        self.latest_bounds = None
        self.configure_button.setVisible(not self.context.map_credentials.configured)
        self.set_note('底图载入中…')
        url.setQuery(f'theme={self._theme}')
        # Reload the current page after a settings change.
        if self.web.url() == url:
            self.web.reload()
        else:
            self.web.setUrl(url)

    def set_theme(self, name):
        """Recolor the shared map in place while retaining pan, zoom and selections."""
        self._theme = name
        self.web.page().refresh_appearance()
        self._sync_document_theme()

    def _theme_after_load(self, success):
        self._document_loaded = success
        if success:
            self._sync_document_theme()
        else:
            self._map_unavailable()
            self.set_note('底图载入失败，请重新配置或连接地图。')

    def _sync_document_theme(self):
        if self._document_loaded or self._ready:
            self.run_js(f'if (typeof setTheme === "function") setTheme({json.dumps(self._theme)})')

    def _map_ready(self, _count):
        self._ready = True
        self.configure_button.hide()
        self.set_note('')
        self._sync_document_theme()
        self._drawn_grid = None
        self.ready.emit()

    def _map_unavailable(self):
        self.configure_button.show()

    def push(self, payload, selected):
        """Push display-safe scene data, selection and risk state to the map."""
        if self.is_ready:
            city = payload.get('city', '')
            public = bool(payload.get('public'))
            changed = (city, public) != (self.airspace_city, self.airspace_public)
            if changed:
                self.airspace_city, self.airspace_public = city, public
                self.latest_bounds = None
                self._drawn_grid = None
                self.grid_check.setVisible(public and city == 'changsha')
                self.grid_check.setEnabled(public and city == 'changsha')
                for toggle in self.duty_layers.values():
                    toggle.setVisible(not public)
                self.airspace_note.setText(
                    f'水平范围 · 其他区域需核对 · 湖南省发改委 {HUNAN_EFFECTIVE:%Y-%m-%d}'
                    if city == 'changsha' else '尚未接入边界')
                self._sync_layers()
                self.grid_timer.start()
            active = self.grid_check.isChecked() and public and city == "changsha"
            payload = dict(payload, grid_visible=active)
            scene = json.dumps(display_payload(payload), ensure_ascii=False, allow_nan=False)
            self.run_js(
                f'updateScene({scene});selectEvent({json.dumps(selected)});'
                'setRisk(false)'
            )

    def _viewport(self, west, south, east, north, zoom):
        self.viewport_changed.emit(west, south, east, north, zoom)
        if not self.grid_check.isChecked() or not self.airspace_public or self.airspace_city != 'changsha':
            return
        self.latest_bounds = tuple(round(value, 5) for value in (west, south, east, north)), round(zoom, 2)
        self.grid_timer.start()

    def _grid_switch(self, enabled):
        self._drawn_grid = None
        self.layers_changed.emit()
        if self.is_ready:
            if enabled:
                self.run_js('reportViewport()')
            else:
                self.grid_timer.stop()
                self.run_js('drawGrid([], [])')

    def _draw_grid(self):
        if not self.is_ready:
            return
        active = self.grid_check.isChecked() and self.airspace_public and self.airspace_city == 'changsha'
        if active and self.latest_bounds and self.latest_bounds != self._drawn_grid:
            bounds, zoom = self.latest_bounds
            self._drawn_grid = self.latest_bounds
            self.run_js(f'drawGrid({json.dumps(visible_grid(bounds, zoom))}, {json.dumps(bounds)})')

    def focus_point(self, point, zoom=None, pin=None):
        self.run_js(f'focusPoint(...{json.dumps([tuple(point), zoom, pin])})')

    def lookup_sandbox_place(self, revision, city_name, keyword, nearby):
        args = json.dumps([revision, city_name, keyword, nearby], ensure_ascii=True)
        self.run_js(f'lookupSandboxPlace(...{args})')

    def lookup_city_place(self, revision, city_name, keyword):
        if self.is_ready:
            args = json.dumps([revision, city_name, keyword], ensure_ascii=True)
            self.run_js(f'lookupCityPlace(...{args})')

    def focus_route(self, points):
        self.run_js(f'focusRoute({json.dumps(points)})')

    def run_js(self, code, callback=None):
        """Invoke a frontend function, optionally receiving its result."""
        if callback is None:
            self.web.page().runJavaScript(code)
        else:
            self.web.page().runJavaScript(code, callback)

    def set_title(self, text):
        """Update the title above the map."""
        self.heading.setText(text)

    def set_note(self, text):
        """Show transient map feedback below the map."""
        self.note_timer.stop()
        self.note.setText(text)
        self.note.setVisible(bool(text))
        if text and self.is_ready:
            self.note_timer.start()
