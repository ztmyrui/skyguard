"""Filter buttons and status counts for the duty event list."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .common import label


class KpiStrip(QWidget):
    """Display stable totals and one optional event filter."""

    filter_changed = Signal(str)
    tasks_requested = Signal()
    METRICS = (
        ('active', '待处理'), ('priority', '优先核验'), ('tasks', '任务进行中'),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.buttons = {}
        self.values = {}
        self.active_filter = ''
        for key, title in self.METRICS:
            button = QPushButton()
            button.setObjectName('kpiButton')
            button.setFocusPolicy(Qt.TabFocus)
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(f'筛选{title}，再次点击清除')
            button.clicked.connect(lambda checked=False, metric=key: self._clicked(metric, checked))
            body = QVBoxLayout(button)
            body.setContentsMargins(12, 4, 12, 4)
            body.setSpacing(0)
            value = label('0', 'kpiValue', numeric=True)
            value.setProperty('priority', key == 'priority')
            caption = label(title)
            for widget in (value, caption):
                widget.setAttribute(Qt.WA_TransparentForMouseEvents)
                body.addWidget(widget)
            self.buttons[key] = button
            self.values[key] = value
            row.addWidget(button, 1)

    def _clicked(self, key, checked):
        self.set_filter(key if checked else '')
        self.filter_changed.emit(self.active_filter)

    def set_filter(self, key):
        self.active_filter = key
        for metric, button in self.buttons.items():
            button.setChecked(metric == key)

    def set_counts(self, counts):
        for key, title in self.METRICS:
            value = str(counts[key])
            self.values[key].setText(value)
            self.buttons[key].setAccessibleName(f'{title}，{value}')

