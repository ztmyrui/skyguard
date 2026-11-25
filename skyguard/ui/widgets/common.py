"""Common controls and layout helpers for the desktop."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QHeaderView, QHBoxLayout, QLabel,
    QDialog, QDialogButtonBox, QFormLayout, QPushButton, QScrollArea, QSizePolicy, QTableWidget, QTableWidgetItem, QToolButton,
    QVBoxLayout, QWidget,
)

from ...theme import set_themed_icon, themed_font


def label(text, kind='caption', *, numeric=False):
    """Make a wrapping label using the named theme style."""
    widget = QLabel(text)
    widget.setTextFormat(Qt.PlainText)
    widget.setObjectName(kind)
    widget.setWordWrap(True)
    if numeric:
        widget.setFont(themed_font(numeric=True))
    return widget


def info_tip(tooltip_text):
    widget = label('ⓘ', 'infoTip')
    widget.setWordWrap(False)
    widget.setToolTip(tooltip_text)
    widget.setAccessibleName(tooltip_text)
    widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return widget


def with_info(widget, tooltip_text):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(5)
    row.addWidget(widget)
    row.addWidget(info_tip(tooltip_text))
    row.addStretch()
    return row


ALTITUDE_TIP = '相对真高是飞行器距正下方地面的垂直高度。真高 120 m 为适飞上限。'
FEED_TONES = {'突发': 'danger', '提醒': 'info', '派员': 'dispatch', '改派': 'demo',
              '引导': 'guide', '化解': 'ok', '临场': 'warn'}


def action_flow_row(badge, time, text, callback=None):
    """Share the same action feed presentation in both governance pages."""
    widget = ActionButton('', callback, variant='quiet') if callback else QWidget()
    widget.setObjectName('actionFlowRow')
    widget.setToolTip(f'{time} · {text}')
    widget.setAccessibleName(f'{badge}，{time}，{text}')
    row = QHBoxLayout(widget)
    row.setContentsMargins(5, 4, 5, 4)
    row.setSpacing(7)
    chip = Badge(badge, FEED_TONES[badge])
    row.addWidget(chip, 0, Qt.AlignTop)
    content = QVBoxLayout()
    content.setSpacing(2)
    at = label(time, 'caption', numeric=True)
    detail = label(text, 'bodyText')
    detail.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
    content.addWidget(at)
    content.addWidget(detail)
    row.addLayout(content, 1)
    for child in (chip, at, detail):
        child.setAttribute(Qt.WA_TransparentForMouseEvents)
    return widget


class SectionHeader(QLabel):
    """Display the heading of a panel."""

    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName('section')


class Card(QWidget):
    """Provide a bordered panel with consistent spacing."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('card')
        self.setAttribute(Qt.WA_StyledBackground)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(12, 12, 12, 12)
        self.body.setSpacing(8)


def dialog_form(parent, title):
    dialog = QDialog(parent)
    dialog.setWindowTitle(title)
    form = QFormLayout(dialog)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText('确认')
    buttons.button(QDialogButtonBox.Cancel).setText('取消')
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    return dialog, form, buttons


class Badge(QLabel):
    """Display a short context label with a theme tone."""

    def __init__(self, text, tone='info', parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setTextFormat(Qt.PlainText)
        self.setObjectName('badge')
        self.setProperty('tone', tone)

    def set_value(self, text, tone):
        """Update the state text and refresh the theme only when its tone changes."""
        self.setText(text)
        if self.property('tone') != tone:
            self.setProperty('tone', tone)
            style = self.style()
            style.unpolish(self)
            style.polish(self)


class EmptyState(QWidget):
    """Display a prompt and optional visual guidance for an empty panel."""

    def __init__(self, title, *, description='', glyph=None, steps=(), parent=None):
        super().__init__(parent)
        self.setObjectName('emptyState')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        content = QWidget()
        content.setMaximumWidth(420)
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        if glyph:
            picture = icon_label(glyph, size=48)
            picture.setObjectName('emptyIllustration')
            body.addWidget(picture, 0, Qt.AlignHCenter)
        prompt = label(title, 'emptyPrompt')
        prompt.setAlignment(Qt.AlignCenter)
        body.addWidget(prompt)
        if description:
            detail = label(description)
            detail.setAlignment(Qt.AlignCenter)
            body.addWidget(detail)
        if steps:
            body.addWidget(self._steps(steps), 0, Qt.AlignHCenter)
        layout.addWidget(content, 0, Qt.AlignCenter)

    @staticmethod
    def _steps(steps):
        guidance = QWidget()
        body = QVBoxLayout(guidance)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(8)
        for number, step in enumerate(steps, 1):
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(Badge(str(number), 'info'), 0, Qt.AlignTop)
            row.addWidget(label(step), 1)
            body.addLayout(row)
        return guidance


class SegmentedControl(QWidget):
    """Choose one visible option from a compact button group."""

    currentIndexChanged = Signal(int)

    def __init__(self, choices, parent=None):
        super().__init__(parent)
        self.setObjectName('segmentedControl')
        self.setAttribute(Qt.WA_StyledBackground)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 4, 4, 4)
        row.setSpacing(4)
        self.group = QButtonGroup(self)
        for index, text in enumerate(choices):
            button = QToolButton()
            button.setText(text)
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            button.setCursor(Qt.PointingHandCursor)
            self.group.addButton(button, index)
            row.addWidget(button, 1)
            button.toggled.connect(lambda checked, i=index: self._selected(i, checked))
        self.setCurrentIndex(0)

    def _selected(self, index, checked):
        if checked:
            self.currentIndexChanged.emit(index)

    def currentIndex(self):
        return self.group.checkedId()

    def setCurrentIndex(self, index):
        self.group.button(index).setChecked(True)


def icon_label(glyph, role='tonal_ink', size=20):
    widget = QLabel()
    set_themed_icon(widget, glyph, role, size)
    widget.setAlignment(Qt.AlignCenter)
    return widget


def scroll_contents():
    """Return a borderless scroll area and the layout for its contents."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    contents = QWidget()
    body = QVBoxLayout(contents)
    body.setContentsMargins(0, 0, 4, 0)
    body.setSpacing(8)
    scroll.setWidget(contents)
    return scroll, body


class ActionButton(QPushButton):
    """Connect an action to a themed button and optional icon."""

    def __init__(self, text, callback, icon=None, variant='secondary', parent=None):
        super().__init__(text, parent)
        self.setMinimumHeight(32)
        self.setCursor(Qt.PointingHandCursor)
        if variant != 'secondary':
            self.setProperty(variant, True)
        if icon:
            role = {'primary': 'on_pri', 'danger': 'danger'}.get(variant, 'tonal_ink')
            set_themed_icon(self, icon, role)
            self.setIconSize(QSize(16, 16))
        if callback is not None:
            self.clicked.connect(lambda: callback())


def table(headers):
    """Make a read-only table with whole-row selection."""
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.verticalHeader().hide()
    widget.setAlternatingRowColors(True)
    return widget


def add_row(widget, values):
    """Append a row and expose its complete cell text as tooltips."""
    row = widget.rowCount()
    widget.insertRow(row)
    for column, value in enumerate(values):
        item = QTableWidgetItem(str(value))
        item.setToolTip(str(value))
        widget.setItem(row, column, item)
