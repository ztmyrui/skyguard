"""Native widgets for flight conditions, evidence and local plan actions."""
import html
from urllib.parse import urlsplit

from PySide6.QtCore import QSize, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QTextLayout, QTextOption
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from ...flight_check import ItemState, Verdict
from ...theme import set_themed_icon
from ..presenters import flight_window
from .common import (
    ActionButton, Badge, Card, EmptyState, SectionHeader, icon_label, label, scroll_contents,
)

UOM = 'https://uom.caac.gov.cn/'
VERDICT_TONES = {
    Verdict.CLEAR: 'ok',
    Verdict.CHECK: 'unknown',
    Verdict.PERMIT: 'warn',
    Verdict.RESTRICTED: 'danger',
}
VERDICT_ICONS = {
    Verdict.CLEAR: 'condition-ok',
    Verdict.CHECK: 'help',
    Verdict.PERMIT: 'condition-permit',
    Verdict.RESTRICTED: 'condition-restricted',
}
ITEM_STYLES = {
    ItemState.OK: ('condition-ok', 'ok', '已核对'),
    ItemState.ATTENTION: ('condition-attention', 'warn', '需留意'),
    ItemState.PERMIT: ('condition-permit', 'warn', '需申请'),
    ItemState.RESTRICTED: ('condition-restricted', 'danger', '受限'),
    ItemState.UNKNOWN: ('help', 'unknown', '待核对'),
    ItemState.PENDING: ('condition-pending', 'info', '检索中'),
}


class VerdictCard(Card):
    """Show the current verdict and the context it applies to."""

    def __init__(self, check, context, parent=None):
        super().__init__(parent)
        self.setObjectName('verdictCard')
        tone = VERDICT_TONES[check.verdict]
        self.setProperty('tone', tone)
        heading = QHBoxLayout()
        heading.setSpacing(8)
        heading.addWidget(icon_label(VERDICT_ICONS[check.verdict], tone, 24))
        self.headline = label(check.headline, 'verdictHeadline')
        heading.addWidget(self.headline, 1)
        if check.demo:
            heading.addWidget(Badge('演示资料', 'demo'), 0, Qt.AlignTop)
        self.body.addLayout(heading)
        self.body.addWidget(label(context))


class Disclosure(QPushButton):
    """A keyboard-operable disclosure heading with a full-row hit area."""

    def __init__(self, accessible_name):
        super().__init__()
        self.setObjectName('disclosure')
        self.setAccessibleName(accessible_name)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(8, 6, 8, 6)
        self.row.setSpacing(8)
        policy = QSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.arrow = icon_label('chevron-down', size=16)
        self.toggled.connect(self._toggle_arrow)
        self._toggle_arrow(False)

    def sizeHint(self):
        return self.row.sizeHint()

    def minimumSizeHint(self):
        return QSize(0, self.row.minimumSize().height())

    def heightForWidth(self, width):
        return max(self.minimumSizeHint().height(), self.row.heightForWidth(width))

    def _toggle_arrow(self, opened):
        glyph = 'chevron-up' if opened else 'chevron-down'
        set_themed_icon(self.arrow, glyph, size=16)
        self.setAccessibleDescription('已展开' if opened else '已收起')

    def add_arrow(self):
        self.row.addWidget(self.arrow)


def _link(text, url=None, callback=None):
    """Make a wrapping link with a keyboard target and explicit activation."""
    if url and QUrl(url).scheme().lower() not in ('http', 'https'):
        url = None
    if not url and callback is None:
        return label(text)
    link = label('', 'evidenceLink')
    link.setTextFormat(Qt.RichText)
    link.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
    link.setFocusPolicy(Qt.TabFocus)
    link.setText(f'<a href="open">{html.escape(text)}</a>')
    link.linkActivated.connect(
        lambda _: callback() if callback is not None else QDesktopServices.openUrl(QUrl(url)),
    )
    return link


class NoticeExcerpt(QLabel):
    """Wrap a notice excerpt and elide its last line when needed."""

    def __init__(self, text):
        super().__init__()
        self.setObjectName('noticeExcerpt')
        self.setTextFormat(Qt.PlainText)
        self.source = ' '.join(text.split())
        self.setToolTip(f'<p>{html.escape(text)}</p>')
        self.setMinimumWidth(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        encoded = self.source.encode('utf-16-le')
        layout = QTextLayout(self.source, self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        rows = []
        for index in range(3):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(self.contentsRect().width())
            start = 2 * line.textStart()
            end = start + 2 * line.textLength()
            remaining = encoded[start:].decode('utf-16-le')
            text = encoded[start:end].decode('utf-16-le').rstrip()
            if index == 2 and end < len(encoded):
                text = self.fontMetrics().elidedText(remaining, Qt.ElideRight, self.width())
            rows.append(text)
        layout.endLayout()
        self.setText('\n'.join(rows))


def _notice_card(evidence, excerpt):
    card = Card()
    title = _link(evidence.title, evidence.url)
    card.body.addWidget(title)
    try:
        source = urlsplit(evidence.url).hostname
    except ValueError:
        source = ''
    if source:
        card.body.addWidget(label(f'来源网站 · {source}'))
    card.body.addWidget(label(evidence.note, 'noticeTag'))
    card.body.addWidget(NoticeExcerpt(excerpt))
    return card


class CheckRow(QWidget):
    """Display a check item and reveal evidence when it is available."""

    def __init__(self, item, opened=False):
        super().__init__()
        self.item = item
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        self.toggle = None
        if item.details or item.evidence:
            self.toggle = Disclosure(f'{item.title} · {ITEM_STYLES[item.state][2]} · {item.summary}')
            self._fill_heading(self.toggle.row)
            self.toggle.add_arrow()
            body.addWidget(self.toggle)
            details = self._details()
            self.toggle.toggled.connect(details.setVisible)
            body.addWidget(details)
            details.setVisible(opened)
            self.toggle.setChecked(opened)
        else:
            heading = QHBoxLayout()
            heading.setContentsMargins(8, 6, 8, 6)
            heading.setSpacing(8)
            self._fill_heading(heading)
            body.addLayout(heading)

    def _fill_heading(self, row):
        glyph, tone, state_text = ITEM_STYLES[self.item.state]
        status = icon_label(glyph, tone)
        status.setAccessibleName(state_text)
        status.setToolTip(state_text)
        row.addWidget(status, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(label(self.item.title, 'checkTitle'))
        words.addWidget(label(self.item.summary))
        row.addLayout(words, 1)

    def _details(self):
        panel = QWidget()
        body = QVBoxLayout(panel)
        body.setContentsMargins(28, 0, 8, 8)
        body.setSpacing(8)
        details = self.item.details
        if self.item.key == 'notices':
            for evidence, excerpt in zip(self.item.evidence, details):
                body.addWidget(_notice_card(evidence, excerpt))
            details = details[len(self.item.evidence):]
        else:
            for evidence in self.item.evidence:
                body.addWidget(_link(evidence.title, evidence.url))
        for detail in details:
            body.addWidget(label(detail))
        return panel


def _alternatives(periods, choose):
    card = Card()
    card.body.addWidget(SectionHeader('可选时段'))
    for start, end in periods:
        row = QHBoxLayout()
        row.addWidget(label(flight_window(start, end), 'bodyText'), 1)
        button = ActionButton('选用', lambda time=start: choose(time))
        button.setAccessibleName(f'选用 {flight_window(start, end)}')
        row.addWidget(button)
        card.body.addLayout(row)
    return card


def _progress(text):
    progress = QWidget()
    body = QVBoxLayout(progress)
    body.setContentsMargins(4, 0, 4, 0)
    body.setSpacing(4)
    meter = QProgressBar()
    meter.setObjectName('lookupProgress')
    meter.setRange(0, 0)
    meter.setTextVisible(False)
    meter.setFixedHeight(4)
    meter.setAccessibleName(text)
    body.addWidget(meter)
    body.addWidget(label(text))
    return progress


class ModelInterpretation(Card):
    """Keep advisory model output visually distinct from the verdict."""

    def __init__(self, open_settings):
        super().__init__()
        self.open_settings = open_settings
        self.toggle = Disclosure('AI 辅助解读')
        self.toggle.row.addWidget(label('AI 辅助解读', 'checkTitle'), 1)
        self.toggle.add_arrow()
        self.body.addWidget(self.toggle)
        self.content = QWidget()
        self.items = QVBoxLayout(self.content)
        self.items.setContentsMargins(8, 0, 8, 4)
        self.items.setSpacing(8)
        self.body.addWidget(self.content)
        self.content.hide()
        self.toggle.toggled.connect(self.content.setVisible)

    def set_status(self, status, checks=()):
        _clear(self.items)
        for text in checks or (status,):
            self.items.addWidget(label(text, 'bodyText'))
        if status == '模型未配置':
            self.items.addWidget(_link('去设置', callback=self.open_settings))


def _clear(layout):
    while layout.count():
        widget = layout.takeAt(0).widget()
        widget.hide()
        widget.deleteLater()


class FlightResultPanel(Card):
    """Manage the scrollable results and keep the plan actions in reach."""

    settings_requested = Signal()
    alternative_requested = Signal(object)
    save_started = Signal()
    save_requested = Signal(str)
    new_save_requested = Signal(str)
    aerial_requested = Signal()

    def __init__(self):
        super().__init__()
        self.verdict_card = None
        self.check_rows = []
        self._current = None
        self._build_results()
        self._build_actions()
        self.reset()

    def _build_results(self):
        self.verdict_slot = QVBoxLayout()
        self.body.addLayout(self.verdict_slot)
        self.aerial_launch = ActionButton('天气与光线', self.aerial_requested.emit)
        self.body.addWidget(self.aerial_launch)
        self.scroll, body = scroll_contents()
        self.body.addWidget(self.scroll, 1)
        self.empty = EmptyState(
            '在地图上选择飞行范围，填写时间与相对真高后查询',
            glyph='range-guide',
            steps=('选择城市和飞行范围', '填写时段、最高相对真高和机型', '查询后逐项核对飞行条件'),
        )
        body.addWidget(self.empty, 1)
        self.message = Card()
        self.message.body.addWidget(SectionHeader('飞行查询'))
        self.feedback = label('')
        self.message.body.addWidget(self.feedback)
        body.addWidget(self.message)
        self.results = QWidget()
        self.result_body = QVBoxLayout(self.results)
        self.result_body.setContentsMargins(0, 0, 0, 0)
        self.result_body.setSpacing(8)
        self.journey_slot = QVBoxLayout()
        body.addLayout(self.journey_slot)
        self.aerial_slot = QVBoxLayout()
        body.addLayout(self.aerial_slot)
        self.evidence = Disclosure('查看空域依据')
        self.evidence.row.addWidget(label('查看空域依据', 'checkTitle'), 1)
        self.evidence.add_arrow()
        self.evidence.toggled.connect(self.results.setVisible)
        self.evidence.setChecked(True)
        body.addWidget(self.evidence)
        body.addWidget(self.results)
        self.model = ModelInterpretation(self.settings_requested.emit)
        body.addWidget(self.model)
        body.addStretch()

    def _build_actions(self):
        self.editor = Card()
        self.save_name = QLineEdit()
        self.save_name.setPlaceholderText('计划名称')
        self.save_name.setAccessibleName('计划名称')
        self.editor.body.addWidget(self.save_name)
        row = QHBoxLayout()
        self.save_confirm = ActionButton('保存', self._request_save, variant='primary')
        row.addWidget(self.save_confirm)
        self.save_new = ActionButton('另存为新计划', lambda: self.new_save_requested.emit(self.save_name.text()))
        row.addWidget(self.save_new)
        self.save_new.hide()
        row.addWidget(ActionButton('取消', self.editor.hide))
        row.addStretch()
        self.editor.body.addLayout(row)
        self.body.addWidget(self.editor)
        self.save_name.returnPressed.connect(self._request_save)
        self.save_name.textChanged.connect(lambda text: self.save_new.setEnabled(bool(text.strip())))
        self.save_name.textChanged.connect(
            lambda text: self.save_confirm.setEnabled(bool(text.strip())),
        )
        row = QHBoxLayout()
        self.save_button = ActionButton('保存计划', self.save_started.emit, variant='primary')
        row.addWidget(self.save_button, 1)
        row.addWidget(ActionButton('到 UOM 核对 ↗', lambda: QDesktopServices.openUrl(QUrl(UOM))), 1)
        self.body.addLayout(row)

    def reset(self):
        self._current = None
        self.verdict_card = None
        self.check_rows = []
        _clear(self.result_body)
        _clear(self.verdict_slot)
        self.evidence.hide()
        self.results.hide()
        self.aerial_launch.hide()
        self.message.hide()
        self.model.hide()
        self.model.toggle.setChecked(False)
        self.editor.hide()
        self.save_button.setEnabled(False)
        self.empty.show()
        self.scroll.verticalScrollBar().setValue(0)

    def show_message(self, text):
        self.empty.hide()
        self.feedback.setText(text)
        self.message.show()

    def show_check(self, check, context):
        if self._current == (check, context):
            return
        self._current = check, context
        expanded = {row.item.key for row in self.check_rows if row.toggle and row.toggle.isChecked()}
        _clear(self.result_body)
        _clear(self.verdict_slot)
        self.empty.hide()
        self.message.hide()
        self.verdict_card = VerdictCard(check, context)
        self.verdict_slot.addWidget(self.verdict_card)
        self.evidence.show()
        self.results.setVisible(self.evidence.isChecked())
        if check.provisional:
            self.result_body.addWidget(_progress('正在检索临时公告…'))
        checklist = Card()
        checklist.body.setSpacing(0)
        checklist.body.addWidget(SectionHeader('核对清单'))
        self.check_rows = [CheckRow(item, item.key in expanded) for item in check.items]
        for row in self.check_rows:
            checklist.body.addWidget(row)
        self.result_body.addWidget(checklist)
        if check.alternatives:
            self.result_body.addWidget(_alternatives(check.alternatives, self.alternative_requested.emit))
        self.model.setVisible(not check.demo)
        self.save_button.setEnabled(not check.provisional)

    def show_aerial(self, opened):
        self.aerial_launch.setVisible(not opened)
        self.evidence.setChecked(not opened)

    def open_editor(self, title):
        if self.editor.isVisible():
            self.save_name.setFocus()
            return
        self.save_name.setText(title)
        self.editor.show()
        self.save_name.setFocus()
        self.save_name.selectAll()

    def set_editing(self, editing):
        self.save_new.setVisible(editing)
        self.save_button.setText('更新计划' if editing else '保存计划')

    def _request_save(self):
        title = self.save_name.text().strip()
        if title:
            self.save_requested.emit(title)
