"""Focusable cards for event selection."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout

from ..presenters import QUALITY_TONES, SOURCE_LABELS, STATUS_TONES, priority_badge
from .common import Badge, label


class EventCard(QPushButton):
    """Show one event's lifecycle, triage and source in a selectable card."""

    def __init__(self, event_id, select, parent=None):
        super().__init__(parent)
        self.event_id = event_id
        self.setObjectName('eventCard')
        self.setCheckable(True)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(lambda: select(event_id))
        body = QVBoxLayout(self)
        body.setContentsMargins(10, 8, 10, 8)
        body.setSpacing(4)
        title = QHBoxLayout()
        title.setSpacing(4)
        self.name = label('', 'eventCardTitle')
        title.addWidget(self.name, 1)
        self.demo = Badge('演练', 'demo')
        title.addWidget(self.demo, 0, Qt.AlignTop)
        body.addLayout(title)
        badges = QHBoxLayout()
        badges.setSpacing(4)
        self.badges = [Badge('') for _ in range(3)]
        for badge in self.badges:
            badges.addWidget(badge)
        badges.addStretch()
        body.addLayout(badges)
        self.source = label('')
        body.addWidget(self.source)
        for widget in (self.name, self.demo, *self.badges, self.source):
            widget.setAttribute(Qt.WA_TransparentForMouseEvents)

    def sizeHint(self):
        return self.layout().sizeHint()

    def minimumSizeHint(self):
        return self.layout().minimumSize()

    def heightForWidth(self, width):
        return self.layout().heightForWidth(width)

    def update_view(self, view, selected):
        self.setChecked(selected)
        self.name.setText(view['name'])
        self.demo.setVisible(view['source'] == 'synthetic')
        self.source.setText(f'{SOURCE_LABELS[view["source"]]} · {view["age"]} 秒前')
        states = (
            (view['status'], STATUS_TONES[view['status']]), priority_badge(view),
            (view['filing_label'], view['filing_tone']) if 'filing_label' in view else
            (view['quality'], QUALITY_TONES[view['quality']]),
        )
        for badge, (text, tone) in zip(self.badges, states):
            badge.set_value(text, tone)
        self.setAccessibleName('，'.join([view['name'], *(text for text, _ in states), self.source.text()]))

