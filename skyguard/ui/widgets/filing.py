"""Public filing forms and duty correspondence for the same local records."""
from ...system_clock import now as system_now
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit

from ...aerial import CHINA
from ...filing import CALL_OUTCOMES, MAX_BYTES, _query, correspondence, route_filing
from ...filing_material import facts_for
from ...flight_rules import DEMO_START, drawing, instant
from ...flight_route import FlightRoute, display_route
from ..presenters import flight_window
from .common import ActionButton, Badge, Card, SectionHeader, label
from .flight_journey import clear

FILING_TONES = {'已报备':'info','已对应':'warn','已核对':'ok','已取消':'unknown'}
MATCH_TONES = {'疑似对应':'info','无对应报备':'unknown','计划外越界':'warn','无法判断':'unknown','偏离':'warn'}


def load_json_file(parent, caption):
    name, _ = QFileDialog.getOpenFileName(parent, caption, '', 'JSON (*.json)')
    if name:
        with Path(name).open('rb') as source:
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('请选择较小的报备文件')
        return data.decode('utf-8-sig')
    return ''


def export_file(parent, civic, identifier, receipt=False):
    title = '核对回执' if receipt else '报备包'
    name, _ = QFileDialog.getSaveFileName(parent, '导出' + title, title + '.json', 'JSON (*.json)')
    if name:
        civic.filing_export(identifier, Path(name), receipt=receipt)
        return True
    return False


class PublicFilingCard(Card):
    changed = Signal()

    def __init__(self, context, plan, city):
        super().__init__()
        self.context, self.plan = context, plan
        self.body.addWidget(SectionHeader('本机报备'))
        facts = facts_for(city, plan['query'], plan.get('result',''))
        start, end = map(instant, facts['时段'])
        details = f'{plan["title"]} · {flight_window(start,end,show_year=True)}\n{facts["范围概述"]}'
        if plan['query'].get('route'):
            route = FlightRoute.parse(plan['query']['route'])
            minutes = max(1, round((route.end-route.start).total_seconds()/60))
            details += f'\n预计飞行 {minutes} 分钟'
        self.body.addWidget(label(details + f'\n{facts["高度"]} 米 · {facts["机型"]}', 'bodyText'))
        form = QFormLayout()
        self.purpose = QLineEdit(plan.get('purpose',''))
        self.purpose.setMaxLength(200)
        self.contact = QLineEdit()
        self.contact.setMaxLength(300)
        form.addRow('飞行目的', self.purpose)
        form.addRow('联系方式（选填）', self.contact)
        self.body.addLayout(form)
        self.attach = QCheckBox('附带已生成的报备说明')
        self.body.addWidget(self.attach)
        self.attach.setChecked(bool(plan.get('material')))
        self.attach.setEnabled(bool(plan.get('material')))
        self.body.addWidget(label('导出报备包后可交给场地方。', 'bodyText'))
        row = QHBoxLayout()
        self.submit = ActionButton('记入本机报备', self.create, variant='primary')
        self.export = ActionButton('导出报备包', lambda: self.create(True))
        row.addWidget(self.submit)
        row.addWidget(self.export)
        self.body.addLayout(row)
        self.status = label('', 'bodyText')
        self.body.addWidget(self.status)
        self.records = QHBoxLayout()
        self.body.addLayout(self.records)
        self.refresh()

    def refresh(self):
        plan = next(p for p in self.context.civic.data['plans'] if p['id'] == self.plan['id'])
        blocked = plan.get('draft') or plan.get('verdict') == 'restricted'
        self.submit.setEnabled(not blocked)
        self.export.hide()
        if blocked:
            self.status.setText('请先核对草稿航线')
        material = plan.get('material','')
        if material and not self.attach.isEnabled():
            self.attach.setEnabled(True)
            self.attach.setChecked(True)
            self.purpose.setText(plan.get('purpose',''))
        clear(self.records)
        records = [r for r in self.context.civic.data['filings'] if r['plan_id'] == plan['id']]
        current = [r for r in records if _query(r['snapshot']['query']).record() == _query(plan['query']).record()]
        if current:
            latest = current[-1]
            self.records.addWidget(Badge(latest['state'], FILING_TONES[latest['state']]))
            filed = latest['state'] != '已取消'
            self.submit.setEnabled(not blocked and not filed)
            self.export.setVisible(filed)
            if latest['state'] in ('已报备','已对应') and route_filing(latest):
                self.records.addWidget(ActionButton('取消报备', lambda: self.cancel(latest['id']), variant='quiet'))
        self.records.addStretch()

    def create(self, export=False):
        civic = self.context.civic
        plan = next(p for p in civic.data['plans'] if p['id'] == self.plan['id'])
        try:
            if export:
                existing = next((r for r in reversed(civic.data['filings'])
                                 if r['plan_id'] == plan['id'] and r['state'] != '已取消'
                                 and _query(r['snapshot']['query']).record() == _query(plan['query']).record()), None)
                if existing:
                    if export_file(self, civic, existing['id']):
                        self.status.setText('已导出')
                return
            record = civic.public_filing(plan['id'], contact=self.contact.text(), purpose=self.purpose.text(),
                                         material=plan.get('material','') if self.attach.isChecked() else '')
            self.status.setText('已记入本机报备')
            self.refresh()
            self.context.civic_changed.emit()
            self.changed.emit()
        except (ValueError, OSError) as exc:
            self.status.setText(str(exc))

    def cancel(self, identifier):
        self.context.civic.change_filing(identifier,'cancel')
        self.status.setText('已取消报备')
        self.refresh()
        self.context.civic_changed.emit()
        self.changed.emit()


def today_filings(civic, city, wall_time, exercise_time):
    records = []
    for record in civic.data['filings']:
        saved = record['snapshot']
        q = saved['query']
        if saved['city'] != city or record['state'] == '已取消':
            continue
        now = exercise_time if q.get('demo') else wall_time
        today = now.astimezone(CHINA).replace(hour=0, minute=0, second=0, microsecond=0)
        if instant(q['start']) < today + timedelta(days=1) and instant(q['end']) > today:
            records.append((record, now))
    return tuple(sorted(records, key=lambda item: instant(item[0]['snapshot']['query']['start'])))


def overlay(record, now):
    query = _query(record['snapshot']['query'])
    if query.route:
        return []
    style = 'past' if now >= query.end else 'future' if now < query.start else 'current'
    return [dict(id='filing:' + record['id'], name=record['snapshot']['title'], kind='filing',
                 rings=rings, filing_style=style, highlight=False) for rings in drawing(query.geometry())]


class FilingDetail(Card):
    updated = Signal()

    def __init__(self, context):
        super().__init__()
        self.context = context
        self.signature = None

    def render(self, view, clock, chosen=''):
        civic = self.context.civic
        result = correspondence(view, civic.data['filings'], clock) if view else None
        related = [r for r in civic.data['filings'] if (view and r['event_id'] == view['id']) or r['id'] == chosen]
        signature = (result, repr(related), chosen, view['id'] if view else '')
        if self.signature == signature:
            return
        self.signature = signature
        clear(self.body)
        if result:
            self.body.addWidget(Badge(result.title, MATCH_TONES[result.title]))
        self.setVisible(bool(related or result))
        related_ids = {r['id'] for r in related}
        by_route = {d.identifier: d for d in result.deviations} if result else {}
        for drift in by_route.values():
            record = civic.filing(drift.identifier)
            alt = '高度待核对' if drift.altitude_m is None else f'高度 {drift.altitude_m:+.0f} 米'
            timing = f'晚 {drift.delay_s:.0f} 秒' if drift.delay_s >= 0 else f'早 {-drift.delay_s:.0f} 秒'
            self.body.addWidget(label(f'{record["snapshot"]["title"]} · {drift.conclusion}\n'
                                      f'横向 {drift.horizontal_m:.0f} 米 · {alt} · {timing}', 'bodyText'))
        if result and result.title == '疑似对应':
            for identifier, ratio, _ in result.candidates:
                if identifier in related_ids:
                    continue
                record = civic.filing(identifier)
                if identifier not in by_route:
                    self.body.addWidget(label(f'{record["snapshot"]["title"]} · 误差范围重叠 {ratio:.0%}', 'bodyText'))
                if record['state'] == '已报备':
                    self.body.addWidget(ActionButton('确认对应此报备', lambda fid=identifier: self.change(
                        fid,'match', event_view=view, clock=clock), variant='primary'))
        elif result and result.title == '无对应报备':
            self.body.addWidget(label('可发起现场核验', 'bodyText'))
        elif result and result.title == '计划外越界':
            for identifier, _, distance in result.candidates:
                record = civic.filing(identifier)
                self.body.addWidget(label(f'{record["snapshot"]["title"]} · 距边界约 {distance:.0f} 米', 'bodyText'))
        for record in related:
            self._record(record, view)

    def _record(self, record, view):
        self.body.addWidget(SectionHeader(record['snapshot']['title']))
        q = record['snapshot']['query']
        period = flight_window(instant(q['start']), instant(q['end']), show_year=True)
        self.body.addWidget(label(f'{period} · {q["altitude_agl"]} 米 · {q["category"]}', 'bodyText'))
        self.body.addWidget(Badge(record['state'], FILING_TONES[record['state']]))
        if not route_filing(record):
            self.body.addWidget(Badge('旧版范围报备', 'unknown'))
        contact = record['contact']
        if contact:
            self.body.addWidget(label(f'联络方式 {contact}', 'bodyText'))
        if record['state'] == '已对应' and view and record['event_id'] == view['id']:
            self.body.addWidget(ActionButton('撤销对应', lambda: self.change(record['id'],'undo'), variant='quiet'))
            form = Card()
            outcomes = QComboBox()
            outcomes.addItems(CALL_OUTCOMES)
            note = QLineEdit()
            note.setPlaceholderText('回访备注')
            form.body.addWidget(outcomes)
            form.body.addWidget(note)
            form.body.addWidget(ActionButton('保存回访', lambda: self.change(
                record['id'],'review', outcome=outcomes.currentText(), note=note.text(), at=system_now(CHINA))))
            form.hide()
            self.body.addWidget(ActionButton('回访飞手', form.show))
            self.body.addWidget(form)
        if record['state'] == '已核对':
            self.body.addWidget(label(record['review']['outcome'], 'bodyText'))
            self.body.addWidget(ActionButton('导出核对回执', lambda: self.context.safe(
                lambda: export_file(self, self.context.civic, record['id'], receipt=True))))

    def change(self, identifier, action, **options):
        if self.context.safe(lambda: self.context.civic.change_filing(identifier,action,**options)):
            self.signature = None
            self.context.civic_changed.emit()
            self.updated.emit()
