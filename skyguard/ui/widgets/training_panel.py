"""Controls for the civilian exercise and shared timeline slider."""
from datetime import timedelta

from PySide6.QtCore import QSignalBlocker, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QSlider, QStyle, QStyleOptionSlider,
                               QVBoxLayout, QWidget)

from ...training_city_cases import CIVIC_CASES
from .common import ALTITUDE_TIP, ActionButton, Badge, Card, SectionHeader, action_flow_row, label, with_info
from .flight_journey import clear
from .replay_slider import ReplaySlider


ACTION_BADGES = {
    '发现异常': '突发', '自动提醒': '提醒', '持续关注': '提醒', '待确认': '提醒',
    '人员撤回': '派员', '派员前往': '派员', '动态改派': '改派',
    '安全引导': '引导', '现场核验': '引导', '已化解': '化解',
}


class MarkedSlider(QSlider):
    def __init__(self):
        super().__init__(Qt.Horizontal)
        self.periods=()
        self.events=()

    def paintEvent(self, event):
        super().paintEvent(event)
        style=QStyleOptionSlider()
        self.initStyleOption(style)
        groove=self.style().subControlRect(QStyle.CC_Slider,style,QStyle.SC_SliderGroove,self)
        span=max(1,self.maximum()-self.minimum())
        def x(second):
            return groove.left()+round(max(0,min(1,(second-self.minimum())/span))*groove.width())
        center=groove.center().y()
        painter=QPainter(self)
        pen=QPen(self.palette().highlight().color(),2)
        painter.setPen(pen)
        for start,end in self.periods:
            painter.drawLine(x(start),center-6,max(x(start)+2,x(end)),center-6)
        painter.setPen(QPen(QColor('#c98531'),2))
        for second in self.events:
            painter.drawLine(x(second),center+4,x(second),center+8)
        painter.end()


class CivicTimeline(Card):
    def __init__(self, session):
        super().__init__()
        self.session = session
        row = QHBoxLayout()
        self.body.addLayout(row)
        self.play = ActionButton('播放', self.toggle, 'play', 'secondary')
        self.speed = QComboBox()
        self.speed.setAccessibleName('播放速度')
        for rate in (1, 2, 4):
            self.speed.addItem(f'{rate}×', rate)
        self.speed.currentIndexChanged.connect(lambda: setattr(session, 'play_speed', self.speed.currentData()))
        self.slider = ReplaySlider()
        self.slider.setAccessibleName('演练进度')
        self.slider.event_activated.connect(self.seek_event)
        self.slider.valueChanged.connect(session.seek_civic)
        self.time = label('00:00:00', 'clock')
        self.time.setToolTip('演练时间 · 北京时间')
        row.addWidget(self.play)
        row.addWidget(self.speed)
        row.addWidget(self.slider, 1)
        row.addWidget(self.time)
        row.addWidget(ActionButton('回到起点', lambda: session.seek_civic(0), variant='secondary'))

    def seek_event(self, at):
        self.session.clock_running = False
        self.session.seek_civic(at)

    def toggle(self):
        if not self.session.civic_replay:
            return
        if self.session.cursor >= self.session.playback_end:
            self.session.reset_civic()
        self.session.clock_running = not self.session.clock_running
        self.refresh()

    def refresh(self):
        session = self.session
        self.play.setText('暂停' if session.clock_running else
                          '重播' if session.cursor == session.playback_end else '播放')
        with QSignalBlocker(self.slider):
            self.slider.setRange(0, session.playback_end)
            self.slider.setValue(session.cursor)
        if session.frames:
            self.time.setText(session.solar_time().strftime('%H:%M:%S'))


class TrainingPanel:
    def __init__(self, page):
        self.page = page
        self.session = page.context.training
        self.enabled = False
        self._cards_key = None
        self._marked_replay = None
        self.toolbar = QWidget()
        self.toolbar.setObjectName('civicTrainingToolbar')
        self.toolbar.setFixedHeight(42)
        controls = QHBoxLayout(self.toolbar)
        controls.setContentsMargins(0, 0, 0, 0)
        self.scenes = QComboBox()
        self.scenes.setAccessibleName('城市演练情景')
        self.scenes.addItems(CIVIC_CASES)
        self.scenes.currentTextChanged.connect(self.load_selected)
        controls.addWidget(self.scenes)
        modes = QButtonGroup(self.toolbar)
        for mode, text in (('auto', '自动处置'), ('assist', '关键确认')):
            button = ActionButton(text, lambda checked=False, value=mode: self.set_mode(value), variant='secondary')
            button.setCheckable(True)
            button.setProperty('civicMode', True)
            modes.addButton(button)
            button.setChecked(mode == 'auto')
            controls.addWidget(button)
        self.modes = modes
        controls.addWidget(ActionButton('重新播放', self.restart, variant='secondary'))
        controls.addStretch()
        self.status = Badge('', 'ok')
        controls.addWidget(self.status)

        self.timeline = CivicTimeline(self.session)
        self.right = Card()
        self.right.body.addWidget(label('城市低空智能值班助手', 'section'))
        self.right.body.addWidget(SectionHeader('当前重点关注'))
        self.focus_cards = QWidget()
        self.focus_body = QVBoxLayout(self.focus_cards)
        self.focus_body.setContentsMargins(0, 0, 0, 0)
        self.right.body.addWidget(self.focus_cards)
        self.right.body.addWidget(SectionHeader('实时行动流'))
        self.stream = QListWidget()
        self.stream.setAccessibleName('实时行动流')
        self.stream.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.stream.setWordWrap(True)
        self.stream.itemActivated.connect(self.focus_event)
        self.stream.itemClicked.connect(self.focus_event)
        self.right.body.addWidget(self.stream, 1)

        self.right.body.addWidget(SectionHeader('询问值班助手'))
        quick = QHBoxLayout()
        for question in ('现在有风险吗？', '巡查员在哪？', '为什么这样处理？'):
            quick.addWidget(ActionButton(question, lambda checked=False, text=question: self.ask(text), variant='secondary'))
        self.right.body.addLayout(quick)
        ask_row = QHBoxLayout()
        self.question = QLineEdit()
        self.question.setAccessibleName('询问值班助手')
        self.question.setPlaceholderText('询问当前情况')
        self.question.returnPressed.connect(lambda: self.ask(self.question.text()))
        ask_row.addWidget(self.question, 1)
        ask_row.addWidget(ActionButton('发送', lambda: self.ask(self.question.text()), variant='secondary'))
        self.right.body.addLayout(ask_row)
        self.answer = label('')
        self.answer.setMinimumHeight(42)
        self.right.body.addWidget(self.answer)
        self.session.changed.connect(self.update)
        self.session.ticked.connect(self.update)

    def open(self):
        self.enabled = True
        self.page.left_stack.hide()
        self.page.right.setCurrentWidget(self.right)
        self.page.right.setMinimumWidth(360)
        self.page.right.setMaximumWidth(420)
        self.page.columns.setSizes([0, 820, 390])
        self.toolbar.show()
        self.timeline.show()
        self.page.kpis.hide()
        if self.session.civic_replay:
            self.update()
        else:
            self.session.autopilot_mode = 'auto'
            self.load_selected()

    def close(self):
        self.enabled = False
        self.session.clock_running = False
        self.page.left_stack.show()
        self.page.right.setCurrentWidget(self.page.tabs)
        self.page.right.setMinimumWidth(320)
        self.page.right.setMaximumWidth(380)
        self.page.columns.setSizes([300, 640, 340])
        self.toolbar.hide()
        self.timeline.hide()
        self.page.kpis.show()
        self.page.refresh()
        self.page.map_changed.emit()

    def load_selected(self):
        if self.enabled:
            self.answer.clear()
            self.session.load_example(self.scenes.currentText())

    def set_mode(self, mode):
        if self.session.autopilot_mode == mode:
            return
        self.session.autopilot_mode = mode
        replay = self.session.civic_replay
        if replay:
            replay.set_mode(self.session.cursor, mode)
            self.session.changed.emit()

    def restart(self):
        self.answer.clear()
        self.session.reset_civic()

    def ask(self, question):
        if not question.strip():
            return
        at = self.session.cursor
        replay = self.session.civic_replay
        if '巡查' in question or '人员' in question:
            marker = replay.layers(at)['staff_markers'][0]
            answer = f'{marker["name"]} · {marker["status"]}。'
            if marker['eta_s']:
                answer += f'沿步道前往，约 {marker["eta_s"]} 秒到达。'
        elif '为什么' in question or '处理' in question:
            active = replay.active(at)
            answer = '\n'.join(i.title + '。' + i.response for i in active[:2]) or '当前飞行秩序平稳，继续例行关注。'
        else:
            active = replay.active(at)
            answer = '\n'.join(i.title + '。' for i in active[:3]) or '当前城市低空秩序平稳。'
        self.answer.setText(answer)
        self.question.clear()

    def focus_event(self, item):
        record = item.data(Qt.UserRole)
        self.session.clock_running = False
        self.session.selected = record['target']
        self.session.seek_civic(record['at'])
        self.focus_point(record['point'])

    def focus_point(self, point):
        if point and self.page.map_view:
            self.page.map_view.focus_point(point)

    def select_object(self, identifier):
        self.session.selected = identifier
        self.update()

    def update(self):
        if self.enabled and self.session.civic_replay:
            self.render()
            self.page.map_changed.emit()

    def takeover(self, button, target):
        menu = QMenu(button)
        for title, choice in (('发送提醒', 'remind'), ('撤回派员', 'withdraw'), ('标记结束', 'finish')):
            menu.addAction(title, lambda checked=False, action=choice: self.session.civic_decide(target, action))
        menu.exec(button.mapToGlobal(button.rect().bottomLeft()))

    def render(self):
        replay = self.session.civic_replay
        if not replay:
            return
        at = self.session.cursor
        frame = replay.frame(at)
        layers = replay.layers(at, self.session.selected)
        aircraft = {row['id']: row for row in layers['aircraft']}
        staff = layers['staff_markers'][0]
        active = replay.active(at)
        affected = {i.target for i in active}
        if replay.city == 'changsha' and 'T04' in affected:
            affected.add('T05')
        compliant = sum(row['filed'] and row['state'] == '飞行中' and row['id'] not in affected for row in aircraft.values())
        self.status.set_value(f'空域关注 · {len(active)} 起处理中' if active else f'空域平稳 · {compliant} 架合规',
                              'warn' if active else 'ok')
        if replay is not self._marked_replay:
            self.timeline.slider.set_events((event.start, event.title, 'danger') for event in replay.incidents)
            self._marked_replay = replay
        self.timeline.refresh()
        if self.stream.property('lastRecords') != repr(frame['log']):
            scroll = self.stream.verticalScrollBar().value()
            self.stream.clear()
            for record in reversed(frame['log']):
                when = (self.session.frames.origin + timedelta(seconds=record['at'])).strftime('%H:%M:%S')
                item = QListWidgetItem()
                item.setData(Qt.UserRole, record)
                item.setData(Qt.AccessibleTextRole, f'{when} · {record["tag"]} · {record["text"]}')
                row = action_flow_row(ACTION_BADGES[record['tag']], when, record['text'])
                row.setAttribute(Qt.WA_TransparentForMouseEvents)
                item.setSizeHint(QSize(0, max(48, row.layout().heightForWidth(300))))
                self.stream.addItem(item)
                self.stream.setItemWidget(item, row)
            self.stream.verticalScrollBar().setValue(scroll)
            self.stream.setProperty('lastRecords', repr(frame['log']))
        selected = self.session.selected
        cards = sorted(active, key=lambda event: (
            event.target not in frame['pending'], event.target != selected, -event.start))[:2]
        selected_active = next((event for event in active if event.target == selected), None)
        if selected_active and selected_active not in cards:
            cards[-1] = selected_active
        selected_row = aircraft.get(selected) if selected not in affected else None
        key = (tuple((i.target, round(aircraft[i.target]['altitude'])) for i in cards),
               tuple(frame['pending'].items()), staff['status'], staff['eta_s'], selected,
               selected_row and round(selected_row['altitude']), compliant, self.session.autopilot_mode)
        if key == self._cards_key:
            return
        self._cards_key = key
        clear(self.focus_body)
        if selected_row and len(cards) < 2:
            text = f'{selected_row["name"]} · {selected_row["state"]} · 相对真高 {selected_row["altitude"]:.0f} m'
            self.focus_body.addWidget(label(text, 'bodyText'))
        if not cards:
            if not selected_row:
                self.focus_body.addWidget(Badge('当前城市低空秩序平稳', 'ok'))
                self.focus_body.addWidget(label(f'{compliant} 架已报备无人机正常飞行'))
            return
        for event in cards:
            row = aircraft[event.target]
            self.focus_body.addWidget(label(event.title, 'section'))
            pending = event.target in frame['pending']
            redirect = frame['pending'].get(event.target)
            badge = '待确认' if pending else '遮挡等待' if row['age'] >= 3 else '处理中'
            summary = label(f'{badge} · 相对真高 {row["altitude"]:.0f} m')
            summary.setWordWrap(False)
            self.focus_body.addLayout(with_info(summary, ALTITUDE_TIP))
            if redirect:
                text = '1 名巡查员正在前往南侧跑道，建议在途改派至西岸展馆。'
            elif frame['walk'] and frame['walk'].target == event.target:
                text = f'{staff["name"]} · {staff["status"]}' + (f'，约 {staff["eta_s"]} 秒到达。' if staff['eta_s'] else '。')
            else:
                text = event.response
            self.focus_body.addWidget(label(text, 'bodyText'))
            actions = QHBoxLayout()
            if pending:
                for title, choice in (('同意改派' if redirect else '同意执行', 'accept'),
                                      ('保持原任务' if redirect else '暂不派员', 'decline')):
                    actions.addWidget(ActionButton(title, lambda checked=False, t=event.target, c=choice:
                                                   self.session.civic_decide(t, c), variant='primary' if choice == 'accept' else 'secondary'))
            else:
                actions.addWidget(ActionButton('定位', lambda checked=False, t=event.target:
                                               self.locate_aircraft(t), variant='secondary'))
                if self.session.autopilot_mode == 'auto':
                    button = QPushButton('人工接管')
                    button.clicked.connect(lambda checked=False, b=button, t=event.target: self.takeover(b, t))
                    actions.addWidget(button)
            actions.addStretch()
            self.focus_body.addLayout(actions)

    def locate_aircraft(self, target):
        for row in self.session.map_layers()['aircraft']:
            if row['id'] == target:
                self.select_object(target)
                self.focus_point((row['lon'], row['lat']))
                break
