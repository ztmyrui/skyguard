"""Shared desktop settings with credential-safe status and local tools."""
from urllib.parse import urlsplit

from PySide6.QtCore import QSize, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QButtonGroup, QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QWidget

from ... import __version__
from ...official_notices import CITY_SOURCES
from ...theme import THEME_NAMES, THEMES
from ..app import asset_path
from ..widgets.common import ActionButton, Badge, Card, SectionHeader, label, scroll_contents
from ..widgets.dialogs import _test_model_connection
from .base import Page


class SettingsPage(Page):
    """Show the same settings in the public and duty workspaces."""
    title = '设置'
    model_requested = Signal()
    map_requested = Signal()
    official_reference_requested = Signal()
    demo_changed = Signal(bool)
    theme_requested = Signal(str)

    def __init__(self, context):
        super().__init__(context)
        self._duty = False
        self._testing = False
        self._model_config = None
        scroll, body = scroll_contents()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body.addWidget(scroll)
        self.management = self._card('管理')
        self.management.hide()
        self.management.setParent(self)
        body.addWidget(self._appearance_card())
        body.addWidget(self._map_card())
        body.addWidget(self._model_card())
        body.addWidget(self._weather_card())
        body.addWidget(self._watch_card())
        demo_card = self._demo_card()
        body.addWidget(demo_card)
        demo_card.hide()
        body.addWidget(self._data_card())
        body.addWidget(self._about_card())
        body.addStretch()

    def set_management(self, resources):
        resources.setMinimumHeight(530)
        self.management.body.addWidget(resources)

    def context_tags(self):
        return (('演示资料', 'demo'),) if not self._duty and self.demo.isChecked() else ()

    @staticmethod
    def _card(title, status=None, *, public_service=False):
        card = Card()
        card.body.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(SectionHeader(title))
        if public_service:
            row.addWidget(Badge('航拍助手服务', 'info'))
        row.addStretch()
        if status:
            row.addWidget(status)
        card.body.addLayout(row)
        return card

    @staticmethod
    def _actions(card, *buttons):
        row = QHBoxLayout()
        for button in buttons:
            row.addWidget(button)
        row.addStretch()
        card.body.addLayout(row)

    @staticmethod
    def _link(text, callback):
        button = ActionButton(text, callback, variant='quiet')
        button.setProperty('settingsLink', True)
        return button

    def _appearance_card(self):
        card = self._card('外观')
        self.theme_buttons = {}
        group = QButtonGroup(card)
        for name, title in THEME_NAMES.items():
            button = ActionButton(title, lambda theme=name: self.theme_requested.emit(theme))
            button.setProperty('appearance', True)
            button.setCheckable(True)
            button.setIcon(self._theme_preview(THEMES[name]))
            button.setIconSize(QSize(52, 16))
            group.addButton(button)
            self.theme_buttons[name] = button
        self._actions(card, *self.theme_buttons.values())
        return card

    @staticmethod
    def _theme_preview(palette):
        preview = QPixmap(104, 32)
        preview.setDevicePixelRatio(2)
        preview.fill(Qt.transparent)
        painter = QPainter(preview)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        for x, role in enumerate(('ground', 'surface', 'pri')):
            painter.setBrush(QColor(palette[role]))
            painter.drawRoundedRect(17 * x + 1, 1, 15, 14, 3, 3)
        painter.end()
        return QIcon(preview)

    def set_theme(self, name):
        self.theme_buttons[name].setChecked(True)

    def _map_card(self):
        self.map_status = Badge('', 'unknown')
        card = self._card('地图', self.map_status)
        self.map_provider = label('', 'bodyText')
        card.body.addWidget(self.map_provider)
        self._actions(card, ActionButton('修改高德配置', self.map_requested.emit))
        return card

    def _model_card(self):
        self.model_status = Badge('', 'unknown')
        card = self._card('AI 解读', self.model_status, public_service=True)
        self.model_details = label('', 'bodyText')
        card.body.addWidget(self.model_details)
        self.test_result = label('')
        self.test_result.hide()
        card.body.addWidget(self.test_result)
        self.test_button = ActionButton('测试连接', self.test_model)
        self._actions(card, ActionButton('修改配置', self.model_requested.emit), self.test_button)
        return card

    def _watch_card(self):
        card = self._card('计划提醒', public_service=True)
        choices = QComboBox()
        choices.setAccessibleName('公告检查间隔')
        for minutes in (0, 15, 30, 60, 120):
            choices.addItem(f'每 {minutes} 分钟检查' if minutes else '关闭检查', minutes)
        choices.setCurrentIndex(choices.findData(self.context.civic.data['watch_minutes']))
        def save(index):
            self.context.civic.watch_settings(choices.itemData(index))
            self.context.schedule_notices()
        choices.currentIndexChanged.connect(save)
        card.body.addWidget(choices)
        card.body.addWidget(label('提醒仅在程序运行时检查', 'bodyText'))
        return card

    def _demo_card(self):
        card = self._card('演示模式')
        self.demo = QCheckBox('启用演示模式')
        self.demo.toggled.connect(self.demo_changed.emit)
        explanation = label('使用演练日期与规则，结果标注为演示资料')
        explanation.setWordWrap(False)
        self._actions(card, self.demo, explanation)
        return card

    def _weather_card(self):
        self.weather_status = Badge('', 'unknown')
        card = self._card('航拍天气', self.weather_status, public_service=True)
        self.weather_test_result = label('读取本机加密 JWT 配置，查询航线中心点的逐小时天气。')
        card.body.addWidget(self.weather_test_result)
        self.weather_test_button = ActionButton('测试天气接口', self.test_weather)
        self._actions(card, self.weather_test_button)
        return card

    def test_weather(self):
        self.weather_test_button.setEnabled(False)
        self.weather_test_result.setText('正在读取长沙逐小时预报…')
        self.context.run_aerial_lookup(lambda: self.context.weather_lookup.forecast((112.9617, 28.1857)),
                                       self._weather_test_done, lambda: self._weather_test_done(None))

    def _weather_test_done(self, forecast):
        self.weather_test_button.setEnabled(self.context.weather_lookup.configured())
        if forecast and forecast.hours:
            suffix = '；高度层风速可用' if any(hour.levels for hour in forecast.hours) else '；高度层风速暂无'
            informational = f'和风天气已返回 {len(forecast.hours)} 小时预报{suffix}。'
            self.weather_status.set_value('连接成功', 'ok')
        else:
            informational = forecast.weather_error if forecast and forecast.weather_error else '天气查询未完成，请重试。'
            self.weather_status.set_value('连接待查', 'warn')
        self.weather_test_result.setText(informational)

    def _data_card(self):
        card = self._card('本机数据目录')
        row = QHBoxLayout()
        path = QLineEdit(str(self.context.root))
        path.setReadOnly(True)
        path.setAccessibleName('数据目录路径')
        row.addWidget(path, 1)
        row.addWidget(ActionButton('复制路径', self.copy_path))
        card.body.addLayout(row)
        card.body.addWidget(label('所有航拍计划、飞行日志与自定义沙盘预案均仅保存在本机', 'bodyText'))
        self._actions(card, ActionButton('打开数据目录', self.open_data_directory))
        return card

    def _about_card(self):
        card = self._card('关于', Badge(f'v{__version__}', 'info'))
        card.body.addWidget(SectionHeader('数据来源'))
        card.body.addWidget(label(
            '长沙市城区范围图 · 湘S（2024）151号  ｜  湖南适飞空域网格 · 2026 年 5 月版（2026-05-14 生效）'
            '  ｜  高德地图 · JS API 在线底图'
        ))
        links = []
        for identifier, urls in CITY_SOURCES.items():
            city = self.context.civic.cities()[identifier]['name']
            for index, url in enumerate(urls, 1):
                suffix = f' {index}' if len(urls) > 1 else ''
                links.append(self._link(f'{city}公告{suffix}', lambda url=url: QDesktopServices.openUrl(QUrl(url))))
        self._actions(card, *links, self._link('官方在线核对', self.official_reference_requested.emit),
                      self._link('第三方许可', self.open_licenses))
        return card

    def set_duty(self, duty):
        self._duty = duty
        self.management.hide()

    def refresh(self):
        configured = self.context.map_credentials.configured
        self.map_status.set_value('已配置' if configured else '未配置', 'ok' if configured else 'unknown')
        self.map_provider.setText('底图由高德地图提供，地理坐标采用 WGS-84 标准校准')
        config = self.context.flight_model_config
        configured = bool(config.key)
        self.model_status.set_value('已配置' if configured else '未配置', 'ok' if configured else 'unknown')
        host = urlsplit(config.endpoint).hostname if configured else None
        self.model_details.setText(
            f'服务主机  {host or "—"}  ·  模型  {config.model if configured else "—"}  ·  Key  {"已保存" if configured else "—"}'
        )
        self.test_button.setEnabled(configured and not self._testing)
        has_weather = self.context.weather_lookup.configured()
        self.weather_status.set_value('凭据已保存' if has_weather else '未配置', 'ok' if has_weather else 'unknown')
        self.weather_test_button.setEnabled(has_weather)
        if config != self._model_config:
            self._model_config = config
            if not self._testing:
                self.test_result.hide()

    def test_model(self):
        config = self.context.flight_model_config
        if not config.key or self._testing:
            return
        self._testing = True
        self.test_result.setText('正在测试连接…')
        self.test_result.show()
        self.refresh()
        self.context.run_flight_lookup(
            lambda: (config, _test_model_connection(config)), self._model_test_done,
            lambda: self._model_test_done((config, '连接失败 · 请稍后重试。')),
        )

    @Slot(object)
    def _model_test_done(self, result):
        config, status = result
        self._testing = False
        self.refresh()
        self.test_result.setText(status if config == self.context.flight_model_config else '配置已更改，请重新测试。')
        self.test_result.show()

    def copy_path(self):
        QApplication.clipboard().setText(str(self.context.root))
        self.context.status.emit('已复制数据目录路径。', 4000)

    def open_data_directory(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.context.root)))

    @staticmethod
    def open_licenses():
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(asset_path() / 'THIRD_PARTY.md')))
