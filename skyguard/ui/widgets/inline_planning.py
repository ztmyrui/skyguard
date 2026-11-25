"""Inline recommendation for civil on-site observation."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

from ...planning import MODES, parameters
from .common import ActionButton, Badge, Card, SectionHeader, SegmentedControl, label, scroll_contents
from .flight_journey import clear
from .flight_result import Disclosure

DURATIONS = (1,3,5)
TEMPLATES = (('contact','联系负责人核实'),('second','补充独立观察'),('review','复核证据'))


class InlinePlanning(Card):
    returned = Signal()
    updated = Signal()

    def __init__(self, context):
        super().__init__()
        self.context = context
        self.index = 0
        self.pending_thread = None
        self.render_key = None
        self.body.addWidget(ActionButton('返回事件', self.returned.emit, variant='quiet'))
        self.body.addWidget(SectionHeader('派人核验'))
        self.duration = SegmentedControl(tuple(f'{value} 分钟' for value in DURATIONS))
        self.duration.setCurrentIndex(1)
        self.duration.setAccessibleName('观察时长')
        self.duration.currentIndexChanged.connect(lambda _: self.refresh())
        self.body.addWidget(self.duration)
        self.generate_btn = ActionButton('生成核验建议', self.generate, variant='primary')
        self.body.addWidget(self.generate_btn)
        scroll, self.output = scroll_contents()
        self.body.addWidget(scroll, 1)
        self.confirm_btn = ActionButton('确认方案并建立任务', self.confirm, variant='primary')
        self.body.addWidget(self.confirm_btn)
        self.error = label('', 'formWarning')
        self.body.addWidget(self.error)
        self.context.busy_changed.connect(lambda _: self.refresh())

    def active(self):
        pending = self.context.service.pending
        return pending if (pending and pending.get('event_id') == self.context.selected
                           and (pending.get('snapshot') or {}).get('params',{}).get('mode') == MODES[0]) else None

    def params(self):
        return parameters(MODES[0], DURATIONS[self.duration.currentIndex()])

    def generate(self):
        self.error.clear()
        event, minutes = self.context.selected, DURATIONS[self.duration.currentIndex()]
        if not event:
            self.error.setText('请选择要核验的事件')
            return
        self.context.run_worker(lambda: self.context.service.generate(event, MODES[0], minutes), self._done)

    def _done(self, _):
        self.refresh()
        self.updated.emit()

    def refresh(self):
        service = self.context.service
        pending = self.active()
        busy = self.context.busy
        self.generate_btn.setEnabled(not busy and bool(self.context.selected))
        if pending and self.pending_thread != pending['thread']:
            self.pending_thread = pending['thread']
            self.index = pending.get('selected',0)
        plans = pending['plans'] if pending else []
        if pending and pending.get('confirmed'):
            self.index = pending['selected']
        stale = service.staleness(pending, self.index, self.params()) if plans else []
        confirmed = bool(pending and pending.get('confirmed'))
        self.confirm_btn.setEnabled(bool(plans and not stale and not busy and not confirmed))
        key = (self.context.selected, self.pending_thread if pending else None, self.index, tuple(stale), confirmed)
        if key == self.render_key:
            return
        self.render_key = key
        clear(self.output)
        if stale:
            self.output.addWidget(Badge('方案待更新','warn'))
            for reason in stale:
                self.output.addWidget(label(reason, 'bodyText'))
        if plans:
            plan = plans[self.index]
            observer = next(step for step in plan['tasks'] if step['key'] == 'observe')
            clock = service.scene.clock_text
            cover = plan['metrics'].get('coverage')
            coverage = f' · 视野覆盖 {cover:.0%}' if cover is not None else ''
            title = '已确认' if confirmed else '推荐' if self.index == 0 else '已选'
            self.output.addWidget(SectionHeader(f'{title} · {observer["person"]} · {observer["post"]}'))
            self.output.addWidget(label(f'{clock(observer["start_s"])} 到岗 · 观察 '
                f'{(observer["end_s"]-observer["start_s"])//60} 分钟{coverage}', 'bodyText'))
            basis = plan['basis'][0] if plan['basis'] else '按可用时段与岗位视野排序'
            self.output.addWidget(label(basis, 'bodyText'))
            if len(plans) > 1 and not confirmed:
                options = Disclosure('换一个人或岗位')
                options.row.addWidget(label('换一个人或岗位','checkTitle'), 1)
                options.add_arrow()
                group = Card()
                for index, candidate in enumerate(plans):
                    if index == self.index:
                        continue
                    step = next(task for task in candidate['tasks'] if task['key'] == 'observe')
                    group.body.addWidget(ActionButton(f'{step["person"]} · {step["post"]} · {clock(step["start_s"])}',
                                                      lambda i=index: self.select(i), variant='quiet'))
                group.hide()
                options.toggled.connect(group.setVisible)
                self.output.addWidget(options)
                self.output.addWidget(group)
            self.output.addWidget(SectionHeader('时间线'))
            names = {task['key']:task['name'] for task in plan['tasks']}
            for task in plan['tasks']:
                previous = '、'.join(names.get(key,key) for key in task['depends_on'])
                line = (f'{clock(task["start_s"])}–{clock(task["end_s"])}  {task["name"]}\n'
                        f'{task["person"]}' + (f' · 前置 {previous}' if previous else ''))
                self.output.addWidget(label(line, 'bodyText'))
        elif pending:
            for gap in pending.get('gaps', []):
                self.output.addWidget(label(gap.get('reason', str(gap)) if isinstance(gap,dict) else str(gap), 'bodyText'))
        else:
            self.output.addWidget(label('选择观察时长，生成现场核验建议。', 'bodyText'))
        self.output.addWidget(SectionHeader('协同任务模板'))
        self.template_checks = {}
        for key, name in TEMPLATES:
            self.template_checks[key] = QCheckBox(name)
            self.output.addWidget(self.template_checks[key])
        self.output.addWidget(ActionButton('建立所选任务', self.create_templates))
        self.output.addStretch()

    def select(self, index):
        self.index = index
        self.refresh()

    def confirm(self):
        try:
            self.context.service.confirm(self.index, params=self.params())
        except ValueError as exc:
            self.error.setText(str(exc))
        else:
            self.error.clear()
            self.refresh()
            self.updated.emit()

    def create_templates(self):
        chosen = [key for key,box in self.template_checks.items() if box.isChecked()]
        try:
            self.context.service.assign_templates(self.context.selected, chosen)
        except ValueError as exc:
            self.error.setText(str(exc))
        else:
            self.error.setText('已建立协同任务')
            self.updated.emit()
