"""Exercise clock timeline for the selected verification plan."""
from math import ceil

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from ...theme import colors, themed_font


class Gantt(QWidget):
    """Show one lane per person with dependency links and current clock."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tasks = []
        self.clock = 0
        self.format_time = str
        self.adopted = False
        self.setMinimumHeight(145)
        self.setAccessibleName('任务时间安排')

    def set_plan(self, tasks, clock, format_time, adopted=False):
        self.tasks = tasks
        self.clock = clock
        self.format_time = format_time
        self.adopted = adopted
        self.update()

    def set_clock(self, clock):
        if self.clock != clock:
            self.clock = clock
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), QColor(colors()['surface']))
        if not self.tasks:
            return
        painter.setFont(themed_font(12))
        left, right, top = 134, self.width() - 28, 33
        first = min(self.clock, *(t['start_s'] for t in self.tasks))
        last = max(self.clock, *(t['end_s'] for t in self.tasks))
        span = max(60, last - first)
        count = max(2, min(14, (right - left) // 64))
        step = next((value for value in (60, 120, 300) if span <= count * value), 300)
        origin, limit = int(first // step) * step, ceil(last / step) * step
        limit = max(limit, origin + step)
        to_x = lambda seconds: left + (seconds - origin) * (right - left) / (limit - origin)
        horizon = self._draw_lanes(painter, top)
        self._draw_axis(painter, to_x, origin, limit, step, top, horizon)
        bars = self._bars(to_x, top)
        self._draw_links(painter, bars)
        self._draw_tasks(painter, bars)
        x = to_x(self.clock)
        painter.setPen(QPen(QColor(colors()['accent']), 2))
        painter.drawLine(QPointF(x, top - 4), QPointF(x, horizon))
        painter.setFont(themed_font(12, numeric=True))
        painter.drawText(12, 21, f'当前 {self.format_time(self.clock)}')

    def _lanes(self):
        people = {t['person_id']: t['person'] for t in self.tasks}
        return {pid: (index, name) for index, (pid, name) in enumerate(people.items())}

    def _draw_lanes(self, painter, top):
        lanes = self._lanes()
        for index, name in lanes.values():
            y = top + index * 34
            if index % 2 == 0:
                painter.fillRect(QRectF(0, y, self.width(), 34), QColor(colors()['field']))
            painter.setPen(QColor(colors()['ink']))
            painter.drawText(QRectF(12, y, 115, 34), Qt.AlignVCenter,
                             painter.fontMetrics().elidedText(name, Qt.ElideRight, 110))
        return top + len(lanes) * 34

    def _draw_axis(self, painter, to_x, origin, limit, step, top, bottom):
        painter.save()
        painter.setFont(themed_font(12, numeric=True))
        for tick in range(origin, limit + 1, step):
            x = to_x(tick)
            painter.setPen(QColor(colors()['tonal']))
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))
            painter.setPen(QColor(colors()['muted']))
            painter.drawText(QRectF(x - 24, 3, 48, 24), Qt.AlignCenter, self.format_time(tick)[:5])
        painter.restore()

    def _bars(self, to_x, top):
        lanes = self._lanes()
        return {t['key']: QRectF(to_x(t['start_s']), top + 5 + lanes[t['person_id']][0] * 34,
                                max(3, to_x(t['end_s']) - to_x(t['start_s'])), 24)
                for t in self.tasks}

    def _draw_links(self, painter, bars):
        painter.setPen(QPen(QColor(colors()['muted']), 1, Qt.DashLine))
        painter.setBrush(Qt.NoBrush)
        for task in self.tasks:
            end = bars[task['key']]
            for key in task['depends_on']:
                source = bars.get(key)
                if source is None:
                    continue
                start = QPointF(source.right(), source.center().y())
                target = QPointF(end.left(), end.center().y())
                path = QPainterPath(start)
                bend = max(start.x() + 4, (start.x() + target.x()) / 2)
                if abs(start.y() - target.y()) < 1:
                    path.lineTo(start.x(), source.top() - 4)
                    path.lineTo(target.x() + 4, source.top() - 4)
                    path.lineTo(target.x() + 4, target.y())
                else:
                    path.lineTo(bend, start.y())
                    path.lineTo(bend, target.y())
                path.lineTo(target)
                painter.drawPath(path)

    def _draw_tasks(self, painter, bars):
        for task in self.tasks:
            rect = bars[task['key']]
            adopted = self.adopted and task['status'] != '已替代'
            painter.setPen(QColor(colors()['ok' if adopted else 'sel_line']))
            painter.setBrush(QColor(colors()['ok_bg' if adopted else 'info_bg']))
            painter.drawRoundedRect(rect, 5, 5)
            painter.setPen(QColor(colors()['ok' if adopted else 'ink']))
            painter.drawText(rect, Qt.AlignCenter, painter.fontMetrics().elidedText(
                task['name'], Qt.ElideRight, max(0, int(rect.width()) - 8)))

