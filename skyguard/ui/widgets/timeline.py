"""Shared playback controls for the duty map pages."""
from PySide6.QtCore import QSignalBlocker, Qt, QTimer, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QSlider, QWidget

from ...theme import set_themed_icon, themed_font
from .common import ActionButton, label


class ExerciseTimeline(QWidget):
    """Play or seek the configured exercise clock."""

    changed = Signal()

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self.playing = False
        self.setObjectName('timeline')
        self.setAttribute(Qt.WA_StyledBackground)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(8)
        self.play_btn = ActionButton('播放演练', self.toggle_play, 'play')
        layout.addWidget(self.play_btn)
        self.speed = QComboBox()
        self.speed.setFont(themed_font(numeric=True))
        self._speeds = ()
        self.speed.setFixedWidth(68)
        self.speed.setAccessibleName('演练播放速度')
        layout.addWidget(self.speed)
        self.clock = label('', 'clock', numeric=True)
        self.clock.setWordWrap(False)
        self.clock.setMinimumWidth(68)
        layout.addWidget(self.clock)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.valueChanged.connect(self.seek)
        self.slider.setAccessibleName('演练时间，秒')
        layout.addWidget(self.slider, 1)
        self.end = label('', numeric=True)
        layout.addWidget(self.end)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        context.busy_changed.connect(self._busy_changed)
        self.refresh()

    def refresh(self):
        """Bring the time readout and slider into sync with the service."""
        scene = self.context.service.scene
        if self._speeds != scene.speeds:
            self._speeds = scene.speeds
            self.speed.clear()
            self.speed.addItems([f'{value}×' for value in self._speeds])
        self.clock.setText(scene.clock_text(scene.clock))
        self.end.setText(scene.clock_text(scene.duration_s)[:5])
        with QSignalBlocker(self.slider):
            self.slider.setRange(0, scene.duration_s)
            self.slider.setValue(int(scene.clock))

    def pause(self):
        """Pause playback and restore its play button."""
        self.playing = False
        self.play_btn.setText('播放演练')
        set_themed_icon(self.play_btn, 'play')

    def toggle_play(self):
        """Toggle playback at the selected speed."""
        if self.context.busy:
            return
        self.playing = not self.playing
        self.play_btn.setText('暂停演练' if self.playing else '播放演练')
        set_themed_icon(self.play_btn, 'pause' if self.playing else 'play')

    def tick(self):
        """Advance the existing simulation clock during playback."""
        if self.playing and not self.context.busy:
            service = self.context.service
            service.tick(self._speeds[self.speed.currentIndex()])
            self.changed.emit()
            if service.scene.clock >= service.scene.duration_s:
                self.pause()

    def seek(self, value):
        """Pause and move directly to the requested exercise second."""
        if not self.context.busy:
            self.pause()
            self.context.service.scene.clock = value
            self.changed.emit()

    def _busy_changed(self, busy):
        if busy:
            self.pause()
        self.play_btn.setEnabled(not busy)
        self.slider.setEnabled(not busy)
