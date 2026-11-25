"""Waypoint controls for the public route form."""
from dataclasses import replace

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QSpinBox, QWidget

from ...flight_route import ROUTE_LIMITS, Waypoint, orbit, rise, tracking
from .common import ActionButton, Card, label
from .flight_result import Disclosure


def field(value, lower, upper, unit):
    box = QSpinBox()
    box.setRange(lower, upper)
    box.setValue(value)
    box.setSuffix(unit)
    return box


class RouteEditor(Card):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.waypoints = []
        self.takeoff = None
        self.complete_flight = True
        self.pick_takeoff = False
        self.history = []
        self.future = []
        form = QFormLayout()
        form.setSpacing(6)
        self.body.addLayout(form)
        self.index = QComboBox()
        self.height = field(70, 0, 2000, ' 米')
        self.default_height = field(70, 0, 2000, ' 米')
        self.hover = field(0, 0, 3600, ' 秒')
        self.speed = QDoubleSpinBox()
        self.speed.setRange(.2, 20)
        self.speed.setValue(5)
        self.speed.setDecimals(1)
        self.speed.setSuffix(' 米/秒')
        self.form = form
        form.addRow('默认相对真高', self.default_height)
        self.undo = ActionButton('撤销', self.undo_edit)
        self.redo = ActionButton('重做', self.redo_edit)
        self.takeoff_button = ActionButton('选起飞点', self.choose_takeoff)
        self.delete_button = ActionButton('删除当前航点', lambda: self.remove(self.index.currentIndex()))
        form.addRow('航速', self.speed)
        controls = QHBoxLayout()
        controls.addWidget(ActionButton('填写坐标', page.coordinates))
        controls.addWidget(ActionButton('清空航线', page.clear))
        form.addRow(controls)
        self.template = QComboBox()
        self.template.addItems(('环绕', '横移', '拉升'))
        row = QHBoxLayout()
        row.addWidget(self.template, 1)
        row.addWidget(ActionButton('用模板生成', self.generate))
        form.addRow(row)
        self.point_fields = QWidget()
        point_form = QFormLayout(self.point_fields)
        point_form.setContentsMargins(0, 4, 0, 0)
        self.camera = field(-1, -1, 359, '°')
        self.camera.setSpecialValueText('未设置')
        point_form.addRow(label('单点参数', 'checkTitle'))
        for title, widget in (('航点', self.index), ('相对真高', self.height),
                              ('悬停', self.hover), ('镜头朝向', self.camera)):
            point_form.addRow(title, widget)
        point_form.addRow(self.delete_button)
        point_form.addRow(label('拖动航点移动，点＋插点，单击改参数，右键删除。'))
        self.body.insertWidget(0, self.point_fields)
        self.hint = label('')
        self.body.addWidget(self.hint)
        self.draft = ActionButton('保存草稿', page.save_route_draft)
        self.draft.hide()
        self.index.currentIndexChanged.connect(self.select)
        self.index.activated.connect(self.focus_waypoint)
        self.height.editingFinished.connect(self.change_waypoint)
        self.hover.editingFinished.connect(self.change_waypoint)
        self.camera.editingFinished.connect(self.change_waypoint)
        self.speed.valueChanged.connect(self.change_speed)
        self.last_speed = self.speed.value()
        self.undo_shortcut = QShortcut(QKeySequence.Undo, page)
        self.undo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.undo_shortcut.activated.connect(self.undo_edit)
        self.redo_shortcut = QShortcut(QKeySequence.Redo, page)
        self.redo_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        self.redo_shortcut.activated.connect(self.redo_edit)
        self.select(-1)
        self.undo.setEnabled(False)
        self.redo.setEnabled(False)

    def snapshot(self):
        return (tuple(self.waypoints), self.takeoff, self.last_speed, self.complete_flight)

    def change_speed(self, speed):
        if speed != self.last_speed:
            self.remember()
            self.last_speed = speed
            self.undo.setEnabled(True)
            self.redo.setEnabled(False)
            self.page.invalidate()

    def remember(self):
        self.history.append(self.snapshot())
        self.history[:] = self.history[-100:]
        self.future.clear()

    def sync(self, index=None):
        self.page.points = [point.xy for point in self.waypoints]
        with QSignalBlocker(self.index):
            selected = self.index.currentIndex() if index is None else index
            self.index.clear()
            self.index.addItems([f'航点 {i+1} · {w.altitude_m:g} 米' for i, w in enumerate(self.waypoints)])
            self.index.setCurrentIndex(min(max(0, selected), len(self.waypoints)-1))
        self.select(self.index.currentIndex())
        self.undo.setEnabled(bool(self.history))
        self.redo.setEnabled(bool(self.future))
        self.page.describe_points()
        self.page.invalidate()

    def select(self, index):
        self.point_fields.setVisible(index >= 0)
        if index >= 0:
            point = self.waypoints[index]
            with QSignalBlocker(self.height), QSignalBlocker(self.hover), QSignalBlocker(self.camera):
                self.height.setValue(round(point.altitude_m))
                self.hover.setValue(point.hover_s)
                self.camera.setValue(round(point.camera_deg) % 360 if point.camera_deg is not None else -1)

    def focus_waypoint(self, index):
        view = self.page.map_view
        if 0 <= index < len(self.waypoints) and view and view.is_ready:
            view.focus_point(self.waypoints[index].xy)
        self.page.map_changed.emit()

    def popup(self, index):
        if self.page.point_mode or not 0 <= index < len(self.waypoints):
            return
        self.index.setCurrentIndex(index)
        self.page.map_changed.emit()
        previous = getattr(self, 'popup_widget', None)
        if previous:
            previous.close()
        dialog = QDialog(self.page, Qt.Popup)
        self.popup_widget = dialog
        form = QFormLayout(dialog)
        current = self.waypoints[index]
        altitude = field(round(current.altitude_m), 0, 2000, ' 米')
        hover = field(current.hover_s, 0, 3600, ' 秒')
        form.addRow(f'航点 {index + 1} 相对真高', altitude)
        form.addRow('悬停', hover)
        camera = field(round(current.camera_deg) % 360 if current.camera_deg is not None else -1, -1, 359, '°')
        camera.setSpecialValueText('未设置')
        form.addRow('镜头朝向', camera)
        warning = label('', 'formWarning')
        form.addRow(warning)
        limit = min(120, ROUTE_LIMITS[self.page.category.currentText()][1])

        def update():
            if index >= len(self.waypoints):
                return
            warning.setText(f'超过真高 {limit} m 条件' if altitude.value() > limit else '')
            changed = replace(self.waypoints[index], altitude_m=altitude.value(), hover_s=hover.value(),
                              camera_deg=None if camera.value() < 0 else camera.value())
            if changed != self.waypoints[index]:
                self.remember()
                self.waypoints[index] = changed
                self.sync(index)

        altitude.valueChanged.connect(update)
        hover.valueChanged.connect(update)
        camera.valueChanged.connect(update)
        def remove():
            self.remove(index)
            dialog.close()
        form.addRow(ActionButton('删除航点', remove, variant='danger'))
        escape = QShortcut(QKeySequence('Esc'), dialog)
        escape.activated.connect(dialog.close)
        warning.setText(f'超过真高 {limit} m 条件' if current.altitude_m > limit else '')
        dialog.move(QCursor.pos())
        dialog.show()

    def restore(self, snapshot):
        points, self.takeoff, self.last_speed, *profile = snapshot
        self.complete_flight = profile[0] if profile else self.complete_flight
        self.waypoints = list(points)
        with QSignalBlocker(self.speed):
            self.speed.setValue(self.last_speed)
        self.sync()

    def undo_edit(self):
        if self.history:
            self.future.append(self.snapshot())
            self.restore(self.history.pop())

    def redo_edit(self):
        if self.future:
            self.history.append(self.snapshot())
            self.restore(self.future.pop())

    def reset(self):
        self.page.route_epoch += 1
        self.page.confirm_takeoff = False
        self.history.clear()
        self.future.clear()
        self.pick_takeoff = False
        self.takeoff_button.setText('选起飞点')
        self.restore(((), None, 5, True))

    def load(self, route):
        self.page.route_epoch += 1
        self.page.confirm_takeoff = False
        self.pick_takeoff = False
        self.takeoff_button.setText('选起飞点')
        self.history.clear()
        self.future.clear()
        self.restore((route.waypoints, route.takeoff, route.speed_mps, route.complete_flight))

    def choose_takeoff(self):
        self.pick_takeoff = not self.pick_takeoff
        if self.pick_takeoff:
            self.page.set_step(0)
        self.takeoff_button.setText('点击地图选点' if self.pick_takeoff else '选起飞点')

    def add(self, lon, lat):
        if not self.pick_takeoff and len(self.waypoints) >= 60:
            return
        self.remember()
        if self.pick_takeoff:
            self.takeoff = (lon, lat)
            self.page.confirm_takeoff = False
            self.choose_takeoff()
            self.sync()
            return
        self.waypoints.append(Waypoint(lon, lat, self.default_height.value()))
        self.sync(len(self.waypoints)-1)

    def insert(self, index, lon, lat):
        if 0 < index < len(self.waypoints) < 60:
            self.remember()
            altitude = (self.waypoints[index-1].altitude_m + self.waypoints[index].altitude_m) / 2
            self.waypoints.insert(index, Waypoint(lon, lat, altitude))
            self.sync(index)

    def remove(self, index):
        if 0 <= index < len(self.waypoints):
            self.remember()
            self.waypoints.pop(index)
            self.sync(index)

    def move(self, index, lon, lat):
        if 0 <= index < len(self.waypoints):
            self.remember()
            self.waypoints[index] = replace(self.waypoints[index], lon=lon, lat=lat)
            self.sync(index)

    def change_waypoint(self):
        index = self.index.currentIndex()
        if index >= 0:
            point = replace(self.waypoints[index], altitude_m=self.height.value(), hover_s=self.hover.value(),
                            camera_deg=None if self.camera.value() < 0 else self.camera.value())
            if point != self.waypoints[index]:
                self.remember()
                self.waypoints[index] = point
                self.sync(index)

    def generate(self):
        if not self.waypoints:
            self.hint.setText('先在地图选模板起点或中心')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('镜头模板 · ' + self.template.currentText())
        form = QFormLayout(dialog)
        template = self.template.currentIndex()
        settings = (
            (('半径', 125, 10, 500, ' 米'), ('相对真高', 75, 0, 2000, ' m'), ('圈数', 1, 1, 4, ' 圈')),
            (('方向', 90, 0, 359, '°'), ('长度', 350, 1, 2000, ' 米'), ('相对真高', 90, 0, 2000, ' m')),
            (('起始相对真高', 25, 0, 1999, ' m'), ('结束相对真高', 85, 0, 2000, ' m')),
        )[template]
        controls = []
        for name, *args in settings:
            control = field(*args)
            form.addRow(name, control)
            controls.append(control)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        buttons.button(QDialogButtonBox.Ok).setText('生成航线')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        form.addRow(buttons)
        if dialog.exec() != QDialog.Accepted:
            return
        route = self.page.context.safe(lambda: (orbit, tracking, rise)[template](
            self.waypoints[max(0, self.index.currentIndex())].xy, *(c.value() for c in controls),
            takeoff=self.takeoff, start=self.page.form_start(), category=self.page.category.currentText(),
            speed=min(self.speed.value(), ROUTE_LIMITS[self.page.category.currentText()][0])))
        if route:
            self.remember()
            self.restore((route.waypoints, route.takeoff, route.speed_mps))
