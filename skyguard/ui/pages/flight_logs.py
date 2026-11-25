"""Record completed flights and view the local flight summary."""
from datetime import datetime, timedelta
from ...system_clock import now as system_now

from PySide6.QtCore import QDateTime, QTimeZone, Qt
from PySide6.QtWidgets import QComboBox, QDateTimeEdit, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QSpinBox

from ...aerial import CHINA
from ...flight_log import ISSUES, statistics
from ...flight_rules import instant
from ...actual_flight import summary_text
from ..presenters import duration_text, flight_window
from ..widgets.common import ActionButton, Badge, Card, EmptyState, SectionHeader, label, scroll_contents
from ..widgets.flight_journey import clear
from .base import Page


class FlightLogsPage(Page):
    title = '飞行日志'

    def __init__(self, context):
        super().__init__(context)
        scroll, self.content = scroll_contents()
        self.body.addWidget(scroll, 1)
        self.editor = None

    def actions(self):
        return (('新建日志', self.new_record, 'plus', 'primary'),)

    def refresh(self):
        # The form keeps its values while the workspace refreshes.
        if self.editor:
            return
        clear(self.content)
        records = self.context.civic.data['flight_logs']
        stats = statistics(records, system_now(CHINA))
        if not records:
            self.content.addWidget(EmptyState('飞完后在这里记录', glyph='plan'), 1)
            return
        overview = Card()
        row = QHBoxLayout()
        for title, value in (('本月次数', f'{stats["month_count"]} 次'),
                             ('累计时长', duration_text(stats['seconds'])),
                             ('常飞地点', '、'.join(place for place, _ in stats['places'])),
                             ('遇到问题', f'{stats["issue_share"]:.0%}')):
            card = Card()
            card.body.addWidget(SectionHeader(title))
            card.body.addWidget(label(value, 'bodyText'))
            row.addWidget(card, 1)
        overview.body.addLayout(row)
        self.content.addWidget(overview)
        for record in sorted(records, key=lambda r: instant(r['start']), reverse=True):
            card = Card()
            heading = QHBoxLayout()
            heading.addWidget(SectionHeader(record['place']), 1)
            if record['plan_id'] and self.context.civic.plan_reviewed(record['plan_id']):
                heading.addWidget(Badge('已核对', 'ok'))
            card.body.addLayout(heading)
            card.body.addWidget(label(f'{flight_window(instant(record["start"]), instant(record["end"]), show_year=True)}'
                                      f' · 相对真高 {record["altitude"]:g} m · {record["model"]}'))
            if record['issue'] != '无' or record['note'] or record['photo_note']:
                card.body.addWidget(label(' · '.join(w for w in (record['issue'] if record['issue'] != '无' else '',
                                                               record['note'], record['photo_note']) if w)))
            associated = self.context.recorded.find(record.get('track_plan_id'))
            if associated and associated.get('actual_track'):
                report = self.context.recorded.report(associated)
                card.body.addWidget(label(summary_text(report)))
                card.body.addWidget(ActionButton('查看航迹', lambda plan=associated: self.show_track(plan)))
            actions = QHBoxLayout()
            actions.addWidget(ActionButton('编辑', lambda r=record: self.new_record(record=r)))
            actions.addWidget(ActionButton('删除', lambda r=record: self._delete(r['id']), variant='danger'))
            actions.addStretch()
            card.body.addLayout(actions)
            self.content.addWidget(card)
        self.content.addStretch()

    def show_track(self, plan):
        from PySide6.QtWidgets import QDialog, QVBoxLayout
        from ..map_view import MapView
        panel = QDialog(self)
        panel.setWindowTitle('飞行航迹 · '+plan['title'])
        panel.resize(820, 620)
        layout = QVBoxLayout(panel)
        map_view = MapView(self.context, panel)
        layout.addWidget(map_view)
        city = self.context.civic.cities()[plan['city']]
        payload = dict(city=plan['city'], center=city['center'], public=False, zones=[],
                       observations=[],areas=[],grid=[],**self.context.recorded.map_layers(plan))
        map_view.ready.connect(lambda: map_view.push(payload, ''))
        layout.addWidget(label('虚线为计划航线，实线为实际航迹'))
        panel.exec()
        panel.deleteLater()

    def _delete(self, identifier):
        if QMessageBox.question(self, '删除日志', '删除这条飞行日志？') != QMessageBox.Yes:
            return
        self.context.civic.delete_log(identifier)
        self.refresh()

    def new_record(self, plan=None, record=None):
        clear(self.content)
        self.editor = Card()
        self.editor.setMaximumWidth(720)
        self.editor.body.addWidget(SectionHeader('编辑飞行日志' if record else '记录飞行'))
        query = plan['query'] if plan else {}
        record = record or {}
        form = QFormLayout()
        plans = QComboBox()
        plans.addItem('手动填写', None)
        all_plans = self.context.civic.data['plans']
        for saved in all_plans:
            suffix = '（已移出）' if saved.get('cancelled') else ''
            plans.addItem(saved['title'] + suffix, saved['id'])
        source_id = plan['id'] if plan else record.get('plan_id')
        plans.setCurrentIndex(max(0, plans.findData(source_id)))
        form.addRow('从已有计划带入（选填）', plans)
        now = system_now(CHINA).replace(second=0, microsecond=0)
        fields = {}
        for key, title, default in (('start', '实际开始', now - timedelta(minutes=15)),
                                     ('end', '实际结束', now)):
            control = QDateTimeEdit()
            control.setTimeZone(QTimeZone(8 * 3600))
            control.setDisplayFormat('yyyy-MM-dd HH:mm:ss')
            control.setCalendarPopup(True)
            actual = (plan or {}).get({'start': 'started_at', 'end': 'ended_at'}[key])
            value = instant(record.get(key) or actual or query.get(key) or default).astimezone(CHINA)
            control.setDateTime(QDateTime.fromString(value.isoformat(), Qt.ISODate))
            fields[key] = control
            form.addRow(title, control)
        altitude = QSpinBox()
        altitude.setRange(1, 10000)
        track = (plan or {}).get('actual_track', ())
        track_height = max((p['height'] for p in track), default=query.get('altitude_agl',70))
        altitude.setValue(max(1, round(record.get('altitude', track_height))))
        fields['altitude'] = altitude
        form.addRow('最高相对真高（m）', altitude)
        model = (plan.get('aerial') or {}).get('model', '') if plan else ''
        city = self.context.civic.cities()[plan['city']]['name'] if plan else ''
        for key, title, default in (('place', '地点', f'{city} · {plan["title"]}' if plan else ''),
                                    ('model', '机型', model or query.get('category', '')),
                                    ('note', '备注', ''), ('photo_note', '照片备注', '')):
            control = QLineEdit(record.get(key, default))
            fields[key] = control
            form.addRow(title, control)
        issue = QComboBox()
        issue.addItems(ISSUES)
        issue.setCurrentText(record.get('issue', '无'))
        form.addRow('遇到问题', issue)
        self.editor.body.addLayout(form)
        error = label('', 'formWarning')
        self.editor.body.addWidget(error)
        row = QHBoxLayout()
        submit = ActionButton('保存日志', lambda: save(), variant='primary')
        row.addWidget(submit)
        row.addWidget(ActionButton('取消', self._close))
        row.addStretch()
        self.editor.body.addLayout(row)
        self.content.addWidget(self.editor)
        self.content.addStretch()

        def validate_time():
            valid = fields['end'].dateTime() > fields['start'].dateTime()
            error.setText('结束时间须晚于开始时间' if not valid else '')
            submit.setEnabled(valid)

        def populate_plan(_):
            saved = next((item for item in all_plans if item['id'] == plans.currentData()), None)
            if saved is None:
                return
            query = saved['query']
            city = self.context.civic.cities()[saved['city']]['name']
            fields['place'].setText(f'{city} · {saved["title"]}')
            fields['model'].setText((saved.get('aerial') or {}).get('model') or query.get('category', ''))
            altitude.setValue(max(1, round(query.get('altitude_agl', 70))))
            for key, actual in (('start', 'started_at'), ('end', 'ended_at')):
                stamp = saved.get(actual) or query.get(key)
                if stamp:
                    value = instant(stamp).astimezone(CHINA)
                    fields[key].setDateTime(QDateTime.fromString(value.isoformat(), Qt.ISODate))
            validate_time()

        plans.activated.connect(populate_plan)
        for key in ('start', 'end'):
            fields[key].dateTimeChanged.connect(validate_time)
        validate_time()

        def save():
            data = {key: fields[key].dateTime().toString(Qt.ISODate) for key in ('start', 'end')}
            data.update({key: fields[key].text() for key in ('place', 'model', 'note', 'photo_note')})
            data.update(altitude=altitude.value(), issue=issue.currentText(),
                        plan_id=plans.currentData())
            try:
                self.context.civic.save_log(data, record.get('id'))
            except ValueError as exc:
                error.setText(str(exc))
            else:
                self._close()

    def _close(self):
        self.editor = None
        self.refresh()
