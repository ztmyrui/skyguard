"""Public planning cards shared by flight query and saved plans."""
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QHBoxLayout, QLineEdit,
                               QGridLayout, QVBoxLayout, QWidget)

from ...aerial import CHINA, HOUR, solar_events
from ...flight_model import FlightModel
from ...filing_material import DECLARATION, SECTIONS, template
from ...flight_window import hourly_window, recommend
from ...preflight import progress
from ...theme import colors
from ..presenters import flight_window
from .common import ActionButton, Badge, Card, SectionHeader, label
from .flight_result import Disclosure, _link


def clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            clear(item.layout())


class TodayWindow(Card):
    chosen = Signal(object)

    def __init__(self):
        super().__init__()
        self.body.addWidget(SectionHeader('今日飞行窗口'))
        self.summary = label('正在读取预报…', 'bodyText')
        self.body.addWidget(self.summary)
        self.track = QGridLayout()
        self.track.setSpacing(3)
        self.body.addLayout(self.track)
        self.content = QVBoxLayout()
        self.body.addLayout(self.content)
        self.source = label('')
        self.body.addWidget(self.source)
        self.rows = ()
        self.last = None

    def render(self, forecast, point, query, model, gust_limit, now, failed=False):
        for layout in (self.track, self.content):
            clear(layout)
        if forecast and forecast.hours:
            self.last = (forecast, point)
        elif self.last and self.last[1] == point:
            forecast = self.last[0]
            failed = True
        if not forecast or not forecast.hours:
            self.summary.setText('天气暂不可用' if failed else '正在读取预报…')
            self.source.clear()
            self.rows = ()
            return
        self.now = now
        self.duration = query.end - query.start
        self.rows = hourly_window(forecast.hours, now, lambda day: solar_events(point, day), gust_limit)
        source = '和风天气' + (' · Open-Meteo 阵风' if any(h.surface_gust_10m is not None for h in forecast.hours) else '')
        self.source.setText(f'{source} · {forecast.fetched.astimezone(CHINA):%m-%d %H:%M} 北京时间'
                            + (' · 上次收到的预报' if failed else ''))
        self.failed = failed
        options = recommend(self.rows, self.duration, now) if not failed else ()
        if failed:
            text = '天气暂不可用'
        elif options:
            best = options[0]
            values = [row.weather.wind for row in self.rows
                      if row.weather and row.weather.wind is not None and best.start < row.start + HOUR
                      and row.start < best.end]
            wind = f' · 风速最高 {max(values):g} m/s' if values else ''
            text = f'{flight_window(best.start, best.end)} 最合适{wind}'
        else:
            text = '未来 24 小时暂无符合时长的窗口'
        self.summary.setText(text)
        palette = colors()
        planned_start, planned_end = query.start, query.end
        for i, row in enumerate(self.rows):
            word = {'ok': '适合', 'warn': '一般', 'danger': '不推荐', 'unknown': '暂无预报'}[row.tone]
            planned = row.start < planned_end and planned_start < row.start + HOUR
            button = ActionButton(f'{row.start:%H}' + ('＊' if row.golden else ''), lambda j=i: self._hour(j))
            button.setAccessibleName(f'{row.start:%m-%d %H:%M} {word} {row.golden}')
            button.setToolTip(button.accessibleName())
            ink = palette.get(row.tone, palette['muted'])
            bg = palette.get(row.tone + '_bg', palette['field'])
            border = palette['pri'] if planned else bg
            button.setStyleSheet(f'background:{bg}; color:{ink}; border:2px solid {border};'
                                 'padding:0; border-radius:6px; min-width:15px; min-height:25px')
            self.track.addWidget(button, i // 12, i % 12)
        events = solar_events(point, now)
        milestones = '  '.join(f'{name} {events[key][0]:%H:%M}' for key, name in (('rise', '日出'), ('set', '日落'))
                                if key in events)
        model_name = model if gust_limit else '轻型参考值'
        self.content.addWidget(label(f'{milestones} · ＊黄金时段\n天气建议 · {model_name}'))
        for option in options:
            self.content.addWidget(ActionButton(f'改到 {flight_window(option.start, option.end)}',
                                                lambda t=option.start: self.chosen.emit(t), variant='quiet'))

    def _hour(self, index):
        clear(self.content)
        row = self.rows[index]
        data = row.weather
        self.content.addWidget(label(f'{row.start:%m-%d %H:%M}  {row.golden}', 'checkTitle'))
        if data:
            def value(key, unit, multiplier=1):
                number = getattr(data, key)
                return f'{number * multiplier:g}{unit}' if number is not None else '—'
            gust = value('surface_gust_10m', ' m/s') if data.surface_gust_10m is not None else value('gust', ' m/s')
            weather = ('风 ' + value('wind', ' m/s') + ' · 阵风 ' + gust + '\n降雨 ' +
                       value('rain_probability', '%', 100) + ' / ' + value('rain_mm', ' mm') +
                       ' · 能见度 ' + value('visibility_km', ' km') + '\n温度 ' + value('temperature', ' ℃'))
            self.content.addWidget(label(weather, 'bodyText'))
        details = Disclosure('为什么是这个分数')
        details.row.addWidget(label('为什么是这个分数', 'checkTitle'), 1)
        details.add_arrow()
        self.content.addWidget(details)
        detail = label(' · '.join(row.reasons) or '各项达到计划参考条件', 'bodyText')
        detail.hide()
        details.toggled.connect(detail.setVisible)
        self.content.addWidget(detail)
        start = max(self.now, row.start)
        button = ActionButton('改到这个时间', lambda: self.chosen.emit(start))
        button.setEnabled(bool(data and not self.failed and start < row.start + HOUR))
        self.content.addWidget(button)


class PreparationCard(Card):
    changed = Signal(object)

    def __init__(self):
        super().__init__()
        self.body.addWidget(SectionHeader('飞前准备'))
        self.status = label('', 'bodyText')
        self.body.addWidget(self.status)
        self.toggle = Disclosure('查看准备清单')
        self.toggle.row.addWidget(label('查看准备清单', 'checkTitle'), 1)
        self.toggle.add_arrow()
        self.body.addWidget(self.toggle)
        self.list = QWidget()
        self.items = QVBoxLayout(self.list)
        self.items.setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.list)
        self.list.hide()
        self.toggle.toggled.connect(self.list.setVisible)
        self.checked = set()
        self.entries = ()

    def render(self, entries, checked):
        if self.entries == entries and self.checked == set(checked):
            return
        self.entries = entries
        self.checked = set(checked)
        clear(self.items)
        for item in entries:
            row = QHBoxLayout()
            check = QCheckBox(item.title, self)
            check.setChecked(item.id in self.checked)
            check.setToolTip(item.detail)
            check.toggled.connect(lambda on, key=item.id: self._checked(key, on))
            row.addWidget(check, 1)
            if item.required:
                row.addWidget(Badge('必须', 'warn'))
            self.items.addLayout(row)
            self.items.addWidget(label(item.detail))
            if item.url:
                self.items.addWidget(_link(item.reference, item.url))
        self._status()

    def _status(self):
        done, total = progress(self.entries, self.checked)
        self.status.setText('已准备好' if total and done == total else f'必做 {done}/{total}')

    def _checked(self, key, checked):
        if checked:
            self.checked.add(key)
        else:
            self.checked.discard(key)
        self._status()
        self.changed.emit(set(self.checked))


class MaterialCard(Card):
    prepared = Signal(str, str)
    settings_requested = Signal()

    def __init__(self, context, facts_provider):
        super().__init__()
        self.context = context
        self.facts_provider = facts_provider
        self.version = 0
        self.body.addWidget(SectionHeader('报备说明'))
        self.purpose = QLineEdit()
        self.purpose.setMaxLength(200)
        self.purpose.setPlaceholderText('飞行目的（选填）')
        self.purpose.setAccessibleName('飞行目的')
        self.body.addWidget(self.purpose)
        self.body.addWidget(ActionButton('生成报备说明', self.generate))
        self.output = QVBoxLayout()
        self.body.addLayout(self.output)

    def reset(self):
        self.version += 1
        clear(self.output)

    def generate(self):
        facts = self.facts_provider(self.purpose.text())
        if not facts:
            return
        self.reset()
        example = template(facts)
        config = self.context.flight_model_config
        if not config.key:
            self.show_material(example)
            self.output.addWidget(ActionButton('去设置配置 AI 解读', self.settings_requested.emit, variant='quiet'))
            return
        self.output.addWidget(label('正在整理报备说明…', 'bodyText'))
        version = self.version

        def finish(value):
            if version == self.version:
                self.version += 1
                self.show_material(value)

        from PySide6.QtCore import QTimer
        QTimer.singleShot(30000, self, lambda: finish(example))
        self.context.run_flight_lookup(lambda: FlightModel(config).material(facts),
                                       finish, lambda: finish(example))

    def show_material(self, report):
        clear(self.output)
        self.output.addWidget(Badge('AI 整理' if report.ai else '模板生成，未经 AI 润色', 'info'))
        for title, text in zip(SECTIONS, report.sections):
            row = QHBoxLayout()
            row.addWidget(label(title, 'checkTitle'), 1)
            row.addWidget(ActionButton('复制', lambda value=text: QApplication.clipboard().setText(value), variant='quiet'))
            self.output.addLayout(row)
            self.output.addWidget(label(text, 'bodyText'))
        self.output.addWidget(label(DECLARATION, 'bodyText'))
        self.output.addWidget(ActionButton('复制全文', lambda: QApplication.clipboard().setText(report.text)))
        self.output.addWidget(ActionButton('附加到报备', lambda: self.prepared.emit(self.purpose.text(), report.text)))
