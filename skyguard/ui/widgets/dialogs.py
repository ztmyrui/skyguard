"""Desktop dialogs for observations and provider configuration."""
from datetime import timedelta
from os import getenv
from pathlib import Path
from time import perf_counter

import httpx
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QSpinBox, QVBoxLayout, QWidget,
)

from ...amap_adapter import MapCredentials
from ...flight_model import FlightModel, FlightModelConfig
from ...flight_rules import DEMO_START, FlightQuery
from ...theme import themed_font
from .common import ActionButton, label


def _dialog_buttons(dialog, standard, text):
    buttons = QDialogButtonBox(standard | QDialogButtonBox.Cancel)
    buttons.button(standard).setText(text)
    buttons.button(QDialogButtonBox.Cancel).setText('取消')
    buttons.rejected.connect(dialog.reject)
    return buttons


class PrivilegePasswordDialog(QDialog):
    """Check the local password before opening the duty workspace."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle('切换权限模式')
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(8)
        layout.addWidget(label('请输入权限密码以进入「低空治理」模式：', 'bodyText'))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText('请输入权限密码')
        self.password.setAccessibleName('权限密码')
        layout.addWidget(self.password)
        self.error = label('密码错误，请重新输入', 'inlineError')
        policy = self.error.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.error.setSizePolicy(policy)
        self.error.hide()
        self.password.textChanged.connect(self.error.hide)
        layout.addWidget(self.error)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(ActionButton('取消', self.reject))
        self.confirm_button = ActionButton('确认进入', self._verify, variant='primary')
        self.confirm_button.setDefault(True)
        row.addWidget(self.confirm_button)
        layout.addLayout(row)
        self.password.setFocus()

    def _verify(self):
        expected_password = getenv('SKYGUARD_DUTY_PASSWORD')
        if expected_password and self.password.text() == expected_password:
            self.password.clear()
            self.accept()
            return
        self.error.setText('密码错误，请重新输入' if expected_password else '请先配置演练密码')
        self.error.show()
        self.password.setFocus()
        self.password.selectAll()


class ObservationDialog(QDialog):
    """Capture a local observation with an optional map point and attachment."""

    def __init__(self, parent, location=None, online=False):
        super().__init__(parent)
        self.setWindowTitle('新增观察记录')
        self.resize(440, 410)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        self.name = QLineEdit('新增模拟目击')
        form.addRow('报告名称', self.name)
        self.description = QLineEdit()
        self.description.setPlaceholderText('看到什么、向哪个方向移动')
        form.addRow('观察描述', self.description)
        self._location_fields(form, location, online)
        self.source = QComboBox()
        self.source.addItems(['模拟目击', '实际现场观察', '授权日志记录'])
        form.addRow('数据来源', self.source)
        form.addRow('本地附件（可选）', self._attachment_field())
        layout.addWidget(label(
            '在线地图点选已转回WGS84；位置误差仍需依据现场观察填写，默认150米。'
            if online else '地图点选仅作城区概览，默认误差1000米，可据实修正。照片为可选辅助材料。'
        ))
        buttons = _dialog_buttons(self, QDialogButtonBox.Ok, '保存观察')
        buttons.accepted.connect(self.validate)
        layout.addWidget(buttons)

    def _location_fields(self, form, location, online):
        self.known = QCheckBox('已知目标大致位置')
        self.known.setChecked(location is not None)
        form.addRow('', self.known)
        self.lat = QDoubleSpinBox()
        self.lat.setFont(themed_font(numeric=True))
        self.lat.setRange(-90, 90)
        self.lat.setDecimals(6)
        self.lat.setValue(location[0] if location else 28.182)
        self.lon = QDoubleSpinBox()
        self.lon.setFont(themed_font(numeric=True))
        self.lon.setRange(-180, 180)
        self.lon.setDecimals(6)
        self.lon.setValue(location[1] if location else 112.955)
        form.addRow('目标纬度 WGS84', self.lat)
        form.addRow('目标经度 WGS84', self.lon)
        self.accuracy = QSpinBox()
        self.accuracy.setRange(1, 5000)
        self.accuracy.setValue(150 if online or not location else 1000)
        self.accuracy.setSuffix(' 米')
        form.addRow('位置估计误差', self.accuracy)

    def _attachment_field(self):
        self.attachment = QLineEdit()
        field = QWidget()
        row = QHBoxLayout(field)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.attachment)
        row.addWidget(ActionButton('选择', self.choose))
        return field

    def choose(self):
        """Select a local evidence attachment."""
        name, _ = QFileDialog.getOpenFileName(
            self, '选择辅助证据', '', '图片或文件 (*.png *.jpg *.jpeg *.pdf);;全部文件 (*)',
        )
        if name:
            self.attachment.setText(name)

    def validate(self):
        """Require a report name before closing the dialog."""
        if not self.name.text().strip():
            QMessageBox.warning(self, '需要名称', '请填写报告名称')
            return
        self.accept()

    def data(self):
        """Return the original observation input fields."""
        return {
            'name': self.name.text().strip(),
            'description': self.description.text().strip(),
            'lat': self.lat.value() if self.known.isChecked() else None,
            'lon': self.lon.value() if self.known.isChecked() else None,
            'accuracy_m': self.accuracy.value(),
            'source': ('synthetic', 'field_observation', 'authorized_log')[self.source.currentIndex()],
            'attachment': self.attachment.text(),
        }


class MapSettingsDialog(QDialog):
    """Configure the local AMap proxy and optional credential storage."""

    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context
        self.setWindowTitle('高德地图设置')
        self.resize(570, 330)
        form = QFormLayout(self)
        form.addRow(label(
            '填写高德Web端（JS API）Key和配套安全密钥。连接后加载在线底图，并向高德发送坐标进行转换；'
            '附件、人员信息与原始陈述不发送。'
        ))
        self.key = QLineEdit(context.map_credentials.key)
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText('Web端（JS API）Key')
        form.addRow('地图 Key', self.key)
        self.code = QLineEdit(context.map_credentials.code)
        self.code.setEchoMode(QLineEdit.Password)
        self.code.setPlaceholderText('配套安全密钥 securityJsCode')
        form.addRow('安全密钥', self.code)
        self.remember = QCheckBox('使用Windows加密，仅在当前用户本机记住')
        self.remember.setChecked(context.map_store.path.exists())
        form.addRow(self.remember)
        form.addRow(label(
            '安全密钥由本机代理使用，不写入网页、业务档案或源码。'
            '当前为单机开发配置，不替代正式服务端的密钥隔离。域名限制如有设置，需允许本机127.0.0.1。'
        ))
        self.error = label('')
        form.addRow(self.error)
        buttons = _dialog_buttons(self, QDialogButtonBox.Save, '保存并连接')
        buttons.accepted.connect(self._save)
        form.addRow(buttons)

    def _save(self):
        try:
            value = MapCredentials(self.key.text(), self.code.text())
            value.validate()
            self.context.map_store.save(value, self.remember.isChecked())
            self.context.map_credentials = value
            self.context.map_server.configure(value)
            self.accept()
        except (ValueError, RuntimeError):
            self.error.setText('请核对完整Key与安全密钥；若本机加密不可用，请取消“记住”后重试。')


class FlightModelDialog(QDialog):
    """Edit the configured model endpoint, name and encrypted key."""

    def __init__(self, parent, context):
        super().__init__(parent)
        self.context = context
        self.setWindowTitle('大模型 API')
        self.setMinimumWidth(490)
        form = QFormLayout(self)
        config = context.flight_model_config
        self.endpoint = QLineEdit(config.endpoint)
        self.endpoint.setPlaceholderText('服务商提供的 HTTPS API 地址')
        self.model = QLineEdit(config.model)
        self.model.setPlaceholderText('服务商提供的模型名称')
        self.key = QLineEdit(config.key)
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText('API Key')
        form.addRow('服务地址', self.endpoint)
        form.addRow('模型名称', self.model)
        form.addRow('API Key', self.key)
        self.error = label('')
        self.error.hide()
        form.addRow(self.error)
        for field in (self.endpoint, self.model, self.key):
            field.textChanged.connect(self.error.hide)
        buttons = _dialog_buttons(self, QDialogButtonBox.Save, '保存')
        buttons.accepted.connect(self._save)
        self.test_button = buttons.addButton('测试连接', QDialogButtonBox.ActionRole)
        self.test_button.clicked.connect(self._test)
        if config.key:
            remove = buttons.addButton('移除配置', QDialogButtonBox.ResetRole)
            remove.clicked.connect(self._clear)
        form.addRow(buttons)

    def _input_config(self):
        return FlightModelConfig(
            self.endpoint.text().strip(), self.model.text().strip(), self.key.text().strip(),
        )

    def _test(self):
        value = self._input_config()
        try:
            value.validate()
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.error.hide()
        self.test_button.setEnabled(False)
        self.test_button.setText('测试中…')
        self.context.run_flight_lookup(
            lambda: (value, _test_model_connection(value)),
            self._test_finished,
            lambda: self._test_finished((value, '连接失败 · 请稍后重试。')),
        )

    @Slot(object)
    def _test_finished(self, result):
        value, status = result
        self.test_button.setText('测试连接')
        self.test_button.setEnabled(True)
        if self._input_config() != value:
            status = '输入已修改，请重新测试。'
        self.error.setText(status)
        self.error.show()

    def _save(self):
        value = self._input_config()
        try:
            self.context.flight_model_store.save(value)
        except (OSError, RuntimeError, ValueError) as error:
            self.error.setText(
                str(error) if isinstance(error, ValueError) else 'Windows 未能保存配置，请重试。'
            )
            self.error.show()
            return
        self.context.flight_model_config = value
        self.accept()

    def _clear(self):
        try:
            self.context.flight_model_store.clear()
        except OSError:
            self.error.setText('未能移除配置，请重试。')
            self.error.show()
            return
        self.context.flight_model_config = FlightModelConfig()
        self.accept()


def _test_model_connection(config):
    query = FlightQuery(((112.95, 28.18),), 'circle', 50, DEMO_START,
                        DEMO_START + timedelta(minutes=10), 30, '轻型')
    started = perf_counter()
    try:
        checks = FlightModel(config).assess('长沙（连接测试示例）', query)
    except httpx.TimeoutException:
        return '连接失败 · 请求超时。'
    except httpx.HTTPStatusError as error:
        return f'连接失败 · HTTP {error.response.status_code}。'
    except ValueError:
        return '连接失败 · 模型回复格式无效。'
    except httpx.RequestError:
        return '连接失败 · 网络错误。'
    return f'连接成功 · 用时 {perf_counter() - started:.1f} 秒 · 返回 {len(checks)} 条事项'


class ActivityDialog(QDialog):
    """Edit the end time and enabled state of an exercise activity."""

    def __init__(self, parent, area, scene):
        super().__init__(parent)
        self.setWindowTitle('修改演练活动')
        form = QFormLayout(self)
        form.addRow(QLabel(area.name))
        self.end = QSpinBox()
        self.end.setRange(area.start_s // 60 + 1, scene.duration_s // 60)
        self.end.setSuffix(f' 分钟后（相对 {scene.clock_text(0)[:5]}）')
        self.end.setValue(area.end_s // 60)
        form.addRow('结束时间', self.end)
        self.active = QCheckBox('活动启用')
        self.active.setChecked(area.active)
        form.addRow(self.active)
        buttons = _dialog_buttons(self, QDialogButtonBox.Ok, '保存活动')
        buttons.accepted.connect(self.accept)
        form.addRow(buttons)

    def values(self):
        """Return the end in exercise seconds and the activation flag."""
        return self.end.value() * 60, self.active.isChecked()


class OperationDialog(QDialog):
    """Capture the existing aerial photography time and purpose fields."""

    def __init__(self, parent, operation, scene):
        super().__init__(parent)
        self.setWindowTitle('航拍作业需求')
        form = QFormLayout(self)
        self.purpose = QLineEdit(operation.get('purpose', '校园活动全景拍摄'))
        form.addRow('拍摄目的', self.purpose)
        limit = scene.duration_s // 60
        origin = scene.clock_text(0)[:5]
        self.early = self._minutes(0, max(0, limit - 1), operation.get('earliest', min(600, (limit - 1) * 60)))
        self.late = self._minutes(1, limit, operation.get('latest', scene.duration_s))
        self.duration = self._minutes(1, limit, operation.get('duration', 300))
        form.addRow(f'最早开始（{origin} 后分钟）', self.early)
        form.addRow(f'最晚结束（{origin} 后分钟）', self.late)
        form.addRow('预计作业时长（分钟）', self.duration)
        form.addRow(label('地点沿用当前选中事件所在区域，系统比较准备时间与地面活动重叠，不生成飞控航线。'))
        buttons = _dialog_buttons(self, QDialogButtonBox.Save, '保存需求')
        buttons.accepted.connect(self.accept)
        form.addRow(buttons)

    def _minutes(self, minimum, maximum, seconds):
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        widget.setValue(seconds // 60)
        return widget

    def values(self):
        """Return the purpose and the time fields in seconds."""
        return (
            self.purpose.text(), self.early.value() * 60,
            self.late.value() * 60, self.duration.value() * 60,
        )
