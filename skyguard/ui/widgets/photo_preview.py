"""A timeline on the existing map for planned flight and camera direction."""
import json
from time import monotonic

from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QSlider

from ...flight_route import timeline
from ...photo_mission import plan_frame, shot_list
from .common import ActionButton, Badge, Card, label


class PhotoPreview(Card):
    frame_changed = Signal(object)
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.route = None
        self.timer = QTimer(self)
        self.timer.setInterval(125)
        self.timer.timeout.connect(self._tick)
        row = QHBoxLayout()
        self.play = ActionButton('播放', self._toggle)
        row.addWidget(self.play)
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setAccessibleName('预演时间')
        self.seek.valueChanged.connect(self._render)
        row.addWidget(self.seek, 1)
        self.time = label('')
        self.time.setWordWrap(False)
        row.addWidget(self.time)
        self.speed = QComboBox()
        self.speed.setAccessibleName('预演倍速')
        for rate in (1, 2, 4, 8):
            self.speed.addItem(f'{rate}×', rate)
        self.speed.setCurrentIndex(2)
        row.addWidget(self.speed)
        row.addWidget(ActionButton('退出预演', self.closed.emit, variant='quiet'))
        self.body.addLayout(row)
        row = QHBoxLayout()
        self.shots = QComboBox()
        self.shots.setAccessibleName('跳到镜头')
        self.shots.setPlaceholderText('跳到镜头')
        self.shots.activated.connect(self._jump)
        row.addWidget(self.shots, 1)
        self.phase = Badge('', 'info')
        row.addWidget(self.phase)
        self.detail = label('', 'bodyText')
        row.addWidget(self.detail, 2)
        self.body.addLayout(row)
        self.hide()

    def load(self, route, mission, *, preserve=False, subject=None):
        position = self.seek.value() if preserve else 0
        self.stop()
        self.route = route
        self.subject = subject
        self.rows = mission['shots'] if mission else shot_list(route, 'travel')
        self.duration = timeline(route)[-1].end_s
        with QSignalBlocker(self.seek), QSignalBlocker(self.shots):
            self.shots.clear()
            for shot in self.rows:
                self.shots.addItem(shot['title'], round(shot['seconds'] * 10))
            self.seek.setRange(0, round(self.duration * 10))
            self.seek.setValue(min(self.seek.maximum(), position))
        self.show()
        self._render(self.seek.value())

    def stop(self):
        self.timer.stop()
        self.play.setText('播放')

    def _toggle(self):
        if self.timer.isActive():
            self.stop()
            return
        if self.seek.value() >= self.seek.maximum():
            self.seek.setValue(0)
        self._last_tick = monotonic()
        self.timer.start()
        self.play.setText('暂停')

    def _tick(self):
        now = monotonic()
        self.seek.setValue(self.seek.value() + max(1, round((now - self._last_tick) * 10 * self.speed.currentData())))
        self._last_tick = now
        if self.seek.value() == self.seek.maximum():
            self.stop()
            self.play.setText('重播')

    def _jump(self, index):
        self.stop()
        if index >= 0:
            self.seek.setValue(self.shots.itemData(index))

    def _render(self, value):
        if self.route is None:
            return
        seconds = min(self.duration, value / 10)
        pieces = timeline(self.route)
        leg = next((piece for piece in pieces if piece.end_s > seconds), pieces[-1])
        frame = plan_frame(self.route, seconds, self.subject)
        frame.update(a=leg.a.xy, b=leg.b.xy, fraction=(seconds-leg.begin_s)/(leg.end_s-leg.begin_s))
        clock = frame['time'][11:19]
        self.time.setText(f'{clock} · {seconds:.0f}/{self.duration:.0f} 秒')
        selected = next((index for index, shot in reversed(tuple(enumerate(self.rows)))
                         if shot['seconds'] <= seconds+.05 < shot.get('end',self.duration+1)), -1)
        self.shots.setCurrentIndex(selected)
        frame['shot_id'] = self.rows[selected]['id'] if selected>=0 else None
        bearing = f"{frame['heading']:.0f}°" if frame['heading'] is not None else '待设置'
        self.phase.set_value(frame['phase'], 'info')
        pitch = f"俯角 {frame['pitch']:.1f}°" if frame['pitch'] is not None else ''
        fields = (frame['move'], f"{frame['altitude']:.0f} 米", f"朝向 {bearing}", pitch)
        self.detail.setText(' · '.join(filter(None, fields)))
        self.frame_changed.emit(frame)

    @staticmethod
    def draw(map_view, frame):
        if map_view and map_view.is_ready:
            map_view.run_js(f'window.setPhotoFrame?.({json.dumps(frame)})')
