"""One clock control shared by the two workspaces."""
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import QComboBox, QDateTimeEdit, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QMessageBox, QVBoxLayout

from ...system_clock import SPEEDS
from .common import ActionButton, SectionHeader, label


class ClockPanel(QDialog):
    def __init__(self, context):
        super().__init__(context.parent())
        self.context = context
        self.clock = context.clock
        self.setWindowTitle('系统时间')
        self.setMinimumWidth(400)
        body = QVBoxLayout(self)
        self.heading = SectionHeader('')
        body.addWidget(self.heading)
        body.addWidget(ActionButton('切换到真实时间' if self.clock.demo else '切换到演示时间',
                                    self.switch_mode))
        if self.clock.demo:
            row = QHBoxLayout()
            self.edit = QDateTimeEdit()
            self.edit.setAccessibleName('跳转目标时间（北京时间）')
            self.edit.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
            self.edit.setCalendarPopup(True)
            self.edit.setDateTime(QDateTime.fromString(self.clock.now().strftime('%Y-%m-%d %H:%M:%S'),
                                                       'yyyy-MM-dd HH:mm:ss'))
            row.addWidget(self.edit, 1)
            row.addWidget(ActionButton('跳转', self.jump, variant='primary'))
            body.addLayout(row)
            quick = QHBoxLayout()
            for name, seconds in (('+10 分钟', 600), ('+1 小时', 3600)):
                quick.addWidget(ActionButton(name, lambda value=seconds: self.change(lambda: self.clock.advance(value))))
            quick.addWidget(ActionButton('到下一件事', self.next_event))
            body.addLayout(quick)
            playback = QHBoxLayout()
            self.play = ActionButton('', self.toggle)
            playback.addWidget(self.play, 1)
            speed = QComboBox()
            speed.setAccessibleName('演示倍速')
            for value in SPEEDS:
                speed.addItem(f'{value}×', value)
            speed.setCurrentIndex(SPEEDS.index(self.clock.speed))
            speed.currentIndexChanged.connect(lambda _: self.change(lambda: self.context.set_speed(speed.currentData())))
            playback.addWidget(speed)
            body.addLayout(playback)
            files = QHBoxLayout()
            files.addWidget(ActionButton('存档', self.save))
            files.addWidget(ActionButton('读档', self.load))
            files.addWidget(ActionButton('演示天气', self.wind))
            body.addLayout(files)
        context.clock_changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        value = self.clock.now()
        self.heading.setText(value.strftime('%Y-%m-%d  %H:%M:%S'))
        if hasattr(self, 'play'):
            self.play.setText('暂停' if self.clock.playing else '播放')

    def switch_mode(self):
        target = not self.clock.demo
        if self.context.safe(lambda: (self.context.use_demo(target), True)[1]):
            self.done(2)

    def change(self, action):
        self.context.safe(action)
        self.context.notify_clock(force=True)
        self.refresh()

    def jump(self):
        date = self.edit.dateTime().toPython().replace(tzinfo=self.clock.now().tzinfo)
        self.change(lambda: self.clock.jump_to(date))

    def next_event(self):
        target = self.context.next_event()
        if target:
            self.change(lambda: self.clock.jump_to(target))
        else:
            self.context.status.emit('暂无下一件事', 3500)

    def toggle(self):
        self.change(lambda: self.clock.play(not self.clock.playing))

    def save(self):
        filename, _ = QFileDialog.getSaveFileName(self, '保存演示存档', '低空守望演示.zip', '演示存档 (*.zip)')
        if filename:
            self.context.safe(lambda: self.context.save_demo_archive(Path(filename)))

    def load(self):
        filename, _ = QFileDialog.getOpenFileName(self, '读取演示存档', '', '演示存档 (*.zip)')
        if filename and QMessageBox.question(self, '读取演示存档', '读档将覆盖当前演示数据。继续读取？') == QMessageBox.Yes:
            if self.context.safe(lambda: (self.context.load_demo_archive(Path(filename)), True)[1]):
                self.accept()

    def wind(self):
        default = self.context.demo_wind
        text, ok = QInputDialog.getText(self, '演示天气', '来风方向（度），风速（米/秒） · 留空自动',
                                        text='' if default is None else f'{default[0]},{default[1]}')
        if ok:
            def apply():
                if not text.strip():
                    self.context.demo_wind = None
                    return
                angle, speed = (float(value) for value in text.replace('，', ',').split(','))
                if not (0 <= angle <= 360 and 0 <= speed <= 50):
                    raise ValueError('风向填写 0–360 度，风速填写 0–50 米/秒')
                self.context.demo_wind = angle % 360, speed
            self.context.safe(apply)
