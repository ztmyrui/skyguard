"""Contextual key for map overlays in planning, playback and editing."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QStackedLayout, QVBoxLayout, QWidget

from ...theme import colors
from .common import label


ENTRIES = {
    'public': (
        ('', (('block', 'ok', '适飞参考网格（水平范围）'), ('block', 'danger', '限制或关注空域'))),
        ('', (('dot', 'info', '起降点'), ('line', 'info', '规划航线与航点'))),
    ),
    'civic_replay': (
        ('无人机', (('dot', 'green', '已报备合规机'), ('dot', 'amber', '异常待核'),
                 ('dot', 'red', '逼近重点区域'), ('ring', 'gray', '楼宇信号遮挡'))),
        ('地面力量', (('巡', 'foot', '徒步巡查'), ('机', 'vehicle', '移动巡逻'),
                   ('岗', 'guard', '定点安保'), ('备', 'recovery', '备降保障'))),
        ('动态连线', (('dash', 'blue', '电子警示'), ('dash', 'green', '安全备降引导'),
                   ('dash', 'orange', '现场核验劝导'))),
    ),
    'sandbox_edit': (
        ('场地', (('ring', 'gray', '市中心 15 km 作业范围'), ('circle', 'red', '重点管控现场'),
                ('ring', 'green', '应急备降点'))),
        ('编排', (('⚡', 'orange', '预设突发触发点'), ('巡机岗备', 'foot', '地面处置人员（可拖动微调）'),
                ('ring', 'info', '所选人员有效核验半径'))),
    ),
}
MAP_COLORS = {
    'green': '#25aa87', 'amber': '#d99820', 'red': '#ec405f', 'gray': '#7c8794',
    'blue': '#5295d0', 'orange': '#c77739', 'foot': '#3975be', 'vehicle': '#087f91',
    'guard': '#5549a2', 'recovery': '#228365',
}


class _Swatch(QWidget):
    def __init__(self, symbol, tone):
        super().__init__()
        self.symbol, self.tone = symbol, tone
        self.setFixedSize(64 if symbol == '巡机岗备' else 20, 18)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        tone = QColor(MAP_COLORS.get(self.tone) or colors()[self.tone])
        if self.symbol in ('line', 'dash'):
            painter.setPen(QPen(tone, 2, Qt.DashLine if self.symbol == 'dash' else Qt.SolidLine))
            painter.drawLine(1, 9, 19, 9)
        elif self.symbol in ('dot', 'block', 'ring', 'circle'):
            painter.setPen(QPen(tone, 1.5, Qt.DashLine if self.symbol == 'ring' else Qt.SolidLine))
            if self.symbol in ('dot', 'block'):
                painter.setBrush(tone)
            if self.symbol == 'block':
                painter.drawRoundedRect(4, 4, 12, 10, 2, 2)
            else:
                painter.drawEllipse(5, 4, 10, 10)
        else:
            roles = ('foot', 'vehicle', 'guard', 'recovery') if len(self.symbol) == 4 else (self.tone,)
            for index, (glyph, role) in enumerate(zip(self.symbol, roles)):
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(MAP_COLORS[role]))
                painter.drawRoundedRect(index * 16 + 1, 1, 15, 16, 4, 4)
                painter.setPen(Qt.white)
                painter.drawText(index * 16 + 1, 1, 15, 16, Qt.AlignCenter, glyph)


class MapLegend(QWidget):
    """Show the symbols used by the active map surface."""

    def __init__(self):
        super().__init__()
        self.setAccessibleName('地图图例')
        self.setObjectName('mapLegend')
        self.setAttribute(Qt.WA_StyledBackground)
        self.pages = {}
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        for mode, groups in ENTRIES.items():
            page = QWidget()
            body = QVBoxLayout(page)
            body.setContentsMargins(10, 6, 10, 6)
            body.setSpacing(2)
            columns = QHBoxLayout()
            body.addLayout(columns)
            for heading, entries in groups:
                group = QVBoxLayout()
                group.setSpacing(1)
                if heading:
                    group.addWidget(label(heading, 'section'))
                for symbol, tone, description in entries:
                    row = QHBoxLayout()
                    row.setSpacing(4)
                    row.addWidget(_Swatch(symbol, tone))
                    row.addWidget(label(description, 'mapLegendText'), 1)
                    group.addLayout(row)
                group.addStretch()
                columns.addLayout(group, 1)
            if mode == 'public':
                body.addWidget(label('适飞参考网格只表示水平范围，起飞前请按机型、真高和临时管制复查。'))
            self.pages[mode] = page
            self.stack.addWidget(page)
        self.set_mode('public')

    def set_mode(self, mode):
        self.mode = mode
        self.stack.setCurrentWidget(self.pages[mode])
        self.updateGeometry()

    def sizeHint(self):
        return self.stack.currentWidget().sizeHint()

    def minimumSizeHint(self):
        return self.stack.currentWidget().minimumSizeHint()
