"""Recorded-flight details integrated into the regular duty workspace."""
from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QTabWidget

from ...actual_flight import summary_text
from ...disposal import recommendation
from ...filing import CALL_OUTCOMES, REVIEW_OUTCOMES
from ...flight_rules import instant
from ..presenters import flight_window
from .common import ActionButton, Badge, Card, SectionHeader, label
from .filing import export_file
from .flight_journey import clear
from .training_panel import MarkedSlider


class RecordedDuty:
    def __init__(self, page):
        self.page = page
        self.context = page.context
        self.session = page.context.recorded
        self.identifier = None
        self.cursor = None
        self.panel = Card()
        self.panel.body.addWidget(SectionHeader('报备飞行'))
        self.list = QListWidget()
        self.list.setAccessibleName('进行中及待核对的报备飞行')
        self.list.setMaximumHeight(156)
        self.list.itemActivated.connect(self.activate)
        self.list.itemClicked.connect(self.activate)
        self.panel.body.addWidget(self.list)
        self.right = QTabWidget()
        self.assess = Card()
        self.compare = Card()
        self.actions = Card()
        self.records = Card()
        for widget, title in ((self.assess,'研判'), (self.compare,'比对'), (self.actions,'处置'), (self.records,'记录')):
            self.right.addTab(widget,title)
        self.timeline = Card()
        bar = QHBoxLayout()
        self.chip = Badge('实时','info')
        bar.addWidget(self.chip)
        self.time = label('', numeric=True)
        bar.addWidget(self.time)
        self.scale = QComboBox()
        self.scale.addItems(('本次飞行', '未来 72 小时'))
        self.scale.setAccessibleName('时间轴范围')
        self.scale.currentIndexChanged.connect(lambda _: self.update_detail())
        bar.addWidget(self.scale)
        self.slider = MarkedSlider()
        self.slider.setAccessibleName('值班回看时间轴')
        self.slider.valueChanged.connect(self.seek)
        bar.addWidget(self.slider, 1)
        bar.addWidget(ActionButton('回到现在', self.return_now))
        self.jump = ActionButton('把系统时间拨到这里', self.advance_clock, variant='primary')
        bar.addWidget(self.jump)
        self.timeline.body.addLayout(bar)
        self.timeline.hide()
        self.panel.hide()
        self._list_key = None
        self._view_key = None

    @property
    def plan(self):
        return self.session.find(self.identifier)

    def visible_now(self):
        return self.page.right.currentWidget() is self.right and not self.page.training.enabled

    def refresh(self):
        if self.page.training.enabled:
            return
        candidates = [p for p in self.context.civic.data['plans']
                      if p.get('actual_track') and p['city'] == self.context.public_city]
        civic = self.context.civic
        def state(plan):
            if not plan.get('ended_at'):
                return '飞行中'
            return '已核对' if civic.plan_reviewed(plan['id']) else '待核对'
        items = [(state(p),p['id'],p['title']) for p in candidates]
        items.sort(key=lambda row: (('飞行中','待核对','已核对').index(row[0]),row[2]))
        key = tuple(items)
        if self._list_key != key:
            self._list_key = key
            self.list.clear()
            last_group = None
            for group, identifier, title in items:
                if group != last_group:
                    header = QListWidgetItem(group)
                    header.setFlags(Qt.NoItemFlags)
                    self.list.addItem(header)
                    last_group = group
                item = QListWidgetItem(title)
                item.setData(Qt.UserRole, identifier)
                self.list.addItem(item)
                if identifier == self.identifier:
                    self.list.setCurrentItem(item)
            self.panel.setVisible(bool(items))
        if self.visible_now():
            if self.plan:
                self.update_detail()
            else:
                self.clear_selection()
        elif items and self.page.right.currentWidget() is self.page.tabs and not self.identifier:
            self.show(items[0][1])

    def activate(self, item):
        if identifier := item.data(Qt.UserRole):
            self.show(identifier)
            self.page.map_changed.emit()

    def show(self, identifier):
        plan = self.session.find(identifier)
        if plan is None:
            return
        if self.identifier != identifier or not self.visible_now():
            self.cursor = None
            self._view_key = None
            self.identifier = identifier
            self.right.setCurrentWidget(self.compare)
            self.page.right.setCurrentWidget(self.right)
        self.update_detail()

    def clear_selection(self):
        self.identifier = self.cursor = self._view_key = None
        self.timeline.hide()
        if self.visible_now():
            self.page.right.setCurrentWidget(self.page.tabs)

    def seek(self, offset):
        if self.plan:
            self.cursor = instant(self.plan['started_at']) + timedelta(seconds=offset)
            self.update_detail()
            self.page.map_changed.emit()

    def return_now(self):
        self.cursor = None
        self.update_detail()
        self.page.map_changed.emit()

    def advance_clock(self):
        if self.cursor:
            self.context.safe(lambda: self.context.clock.jump_to(self.cursor))
            self.return_now()
            self.context.notify_clock(force=True)

    def update_detail(self):
        plan = self.plan
        if not plan:
            return
        now = self.context.clock.now()
        when = self.cursor or now
        start = instant(plan['started_at'])
        seconds = int((now-start).total_seconds())
        self.timeline.show()
        mode = '实时' if self.cursor is None else '预演' if when > now else '回看'
        self.chip.set_value(mode, 'info' if mode == '实时' else 'warn')
        self.time.setText(when.strftime('%m-%d %H:%M:%S'))
        self.jump.setVisible(mode == '预演' and self.context.clock.demo)
        with QSignalBlocker(self.slider):
            past_midnight = int((start.replace(hour=0, minute=0, second=0, microsecond=0)-start).total_seconds())
            extended = self.scale.currentIndex() == 1
            last_sample = plan['actual_track'][-1]['t']
            cursor_offset = int((when-start).total_seconds())
            low = min(past_midnight,0) if extended else min(0,cursor_offset)
            high = max(cursor_offset, 30, seconds + 72*3600 if extended else last_sample+10)
            self.slider.setRange(low, high)
            self.slider.setValue(int((when-start).total_seconds()))
            self.slider.events = (0,seconds,*(move['at'] for move in plan.get('flight_actions', ())))
            self.slider.update()
        report = self.session.report(plan, self.cursor)
        filing = self.context.civic.filing(plan['filing_id'])
        key = (plan['id'], len(plan['actual_track']), self.cursor, mode, filing['state'], repr(plan.get('flight_actions')))
        if self._view_key == key:
            return
        self._view_key = key
        for panel in (self.assess, self.compare, self.actions, self.records):
            clear(panel.body)
        attention = report['attention']
        if attention:
            self.assess.body.addWidget(SectionHeader(f'{attention.level} 级 · {attention.label}'))
            for rule, reason in attention.detail:
                self.assess.body.addWidget(label(f'{rule} · {reason}'))
        else:
            self.assess.body.addWidget(SectionHeader('此时暂无实际航迹'))
        self.assess.body.addWidget(label(f'已对应报备 · 编号 {filing["id"][:12]}'))
        self.assess.body.addStretch()

        self.compare.body.addWidget(SectionHeader(plan['title']))
        self.compare.body.addWidget(label(f'已对应报备 · 编号 {filing["id"][:12]}'))
        self.compare.body.addWidget(label(summary_text(report),'checkTitle'))
        intervals = QListWidget()
        intervals.setAccessibleName('偏离航段')
        intervals.setWordWrap(True)
        intervals.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        for leg in report['intervals']:
            span = flight_window(instant(leg['start']), instant(leg['end']))
            item = QListWidgetItem(f"{span} · {leg['area']}\n横向 {leg['horizontal_m']:g} 米 · 高度 {leg['height_m']:g} 米 · {leg['duration_s']} 秒")
            item.setData(Qt.UserRole, leg['start_t'])
            intervals.addItem(item)
        intervals.itemClicked.connect(lambda item: self.seek(item.data(Qt.UserRole)))
        if intervals.count():
            self.compare.body.addWidget(intervals,1)
        else:
            self.compare.body.addWidget(label('当前暂无偏离航段'))
            self.compare.body.addStretch()
        if plan.get('ended_at') and filing['state']=='已对应':
            check = ActionButton('填写核对结论', self.review, variant='primary')
            check.setEnabled(mode=='实时')
            self.compare.body.addWidget(check)
        if filing['state']=='已核对':
            review = filing['review']
            self.compare.body.addWidget(Badge(review['outcome'],'ok'))
            if review['note']:
                self.compare.body.addWidget(label(review['note']))
            self.compare.body.addWidget(ActionButton('导出核对回执', lambda: self.context.safe(
                lambda: export_file(self.right,self.context.civic,filing['id'],True))))

        group, _ = recommendation(attention.level if attention else 1, True)
        self.actions.body.addWidget(SectionHeader('建议 · '+group))
        for title, kind, confirm in (('发送本机提醒','nudge',False),('记录联系飞手','call',False),
                                     ('安排观察岗核验','dispatch',True),('回复失联求助','request_ack',False)):
            if kind=='request_ack' and not any(a['kind']=='request' for a in plan.get('flight_actions',())):
                continue
            button = ActionButton(title, lambda k=kind,c=confirm: self.act(k,c),
                                  variant='primary' if kind=='nudge' else 'secondary')
            button.setEnabled(mode=='实时' and not plan.get('ended_at'))
            self.actions.body.addWidget(button)
        self.actions.body.addStretch()
        self.records.body.addWidget(SectionHeader('处置与飞手回复'))
        listing = QListWidget()
        listing.setWordWrap(True)
        for action in reversed(plan.get('flight_actions', [])):
            at = start + timedelta(seconds=action['at'])
            if at <= when:
                listing.addItem(f'{at:%H:%M:%S} · {action["title"]}\n'+(action['result'] or action['state']))
        if listing.count():
            self.records.body.addWidget(listing,1)
        else:
            self.records.body.addWidget(label('暂无处置记录'))
            self.records.body.addStretch()

    def act(self, kind, confirm):
        plan = self.plan
        if self.cursor is not None or not plan or plan.get('ended_at'):
            return
        if confirm and QMessageBox.question(self.right,'确认安排','安排人员到设定观察岗核验？') != QMessageBox.Yes:
            return
        if kind == 'call':
            from PySide6.QtWidgets import QInputDialog
            contact = self.context.civic.filing(plan['filing_id'])['contact'] or '未填写联系方式'
            result, ok = QInputDialog.getItem(self.right,'记录联系飞手',contact,CALL_OUTCOMES,editable=False)
            if not ok:
                return
        else:
            result = '请核对飞行状态' if kind=='nudge' else '值班端已收到求助' if kind=='request_ack' else ''
        self.context.safe(lambda: self.session.perform(plan,kind,result=result))

    def review(self):
        if not self.plan or self.cursor is not None:
            return
        dialog = QDialog(self.right)
        dialog.setWindowTitle('核对飞行记录')
        form = QFormLayout(dialog)
        choice = QComboBox()
        choice.addItems(REVIEW_OUTCOMES)
        form.addRow('结论',choice)
        note = QLineEdit()
        note.setMaxLength(1200)
        form.addRow('备注',note)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText('确认核对')
        buttons.button(QDialogButtonBox.Cancel).setText('取消')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.context.safe(lambda: self.session.verify(self.plan,choice.currentText(),note.text()))
