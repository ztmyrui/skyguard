"""Shared event ticks on the governance replay controls."""
from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider, QToolTip

from ...theme import colors


class ReplaySlider(QSlider):
    event_activated = Signal(int)

    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.events = ()
        self.setMouseTracking(True)
        self.setMinimumHeight(30)

    def set_events(self, events):
        self.events = tuple(events)
        self.update()

    def _axis(self):
        option = QStyleOptionSlider()
        self.initStyleOption(option)
        handle = self.style().subControlRect(QStyle.CC_Slider, option, QStyle.SC_SliderHandle, self)
        return handle.width() // 2, self.width() - handle.width(), option.upsideDown

    def _x(self, at, axis):
        start, span, reverse = axis
        return start + QStyle.sliderPositionFromValue(self.minimum(), self.maximum(), at, span, reverse)

    def _nearby(self, x):
        axis = self._axis()
        distances = [(abs(x - self._x(at, axis)), at, title) for at, title, _ in self.events]
        return [(at, title) for distance, at, title in sorted(distances) if distance <= 6]

    def event(self, event):
        if event.type() == QEvent.ToolTip:
            matches = self._nearby(event.pos().x())
            if matches:
                at = matches[0][0]
                names = ' / '.join(dict.fromkeys(title for second, title in matches if second == at))
                QToolTip.showText(event.globalPos(), f'{at//60:02}:{at%60:02} · {names}', self)
                return True
            QToolTip.hideText()
        return super().event(event)

    def mousePressEvent(self, event):
        matches = self._nearby(event.position().x())
        if event.button() == Qt.LeftButton and matches:
            at = matches[0][0]
            self.setFocus(Qt.MouseFocusReason)
            self.event_activated.emit(at)
            self.setValue(at)
            event.accept()
        else:
            super().mousePressEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setPen(Qt.NoPen)
        axis = self._axis()
        palette = colors()
        for at, _, tone in self.events:
            painter.setBrush(QColor(palette[tone]))
            painter.drawRoundedRect(self._x(at, axis) - 2, 2, 4, 6, 2, 2)
