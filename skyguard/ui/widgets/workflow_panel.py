"""P11 operator controls and pilot messages for the local exercise."""
from datetime import timedelta
import json
from pathlib import Path

from PySide6.QtCore import QDateTime, QSignalBlocker, QTimeZone, Qt
from PySide6.QtWidgets import QComboBox, QDateTimeEdit, QDialog, QFileDialog, QHBoxLayout, QInputDialog, QLineEdit, QListWidget, QMessageBox, QSpinBox

from ...aerial import CHINA
from ...disposal import OUTCOMES, REPLIES, recommendation, summarize
from ...filing import CALL_OUTCOMES
from ..widgets.common import ActionButton, Badge, Card, SectionHeader, dialog_form, label, scroll_contents
from .flight_journey import clear


class WorkflowPanel:
    def __init__(self, training):
        self.training=training
        self.session=training.session
        self.context=training.page.context
        self._key=None
        self._comparison_key=None
        self._future_key=None
        self.card=Card()
        area,self.actions=scroll_contents()
        self.card.body.addWidget(area,1)
        training.right.addTab(self.card,'筹划与处置')
        self.review=Card()
        area,self.review_body=scroll_contents()
        self.review.body.addWidget(area,1)
        training.right.addTab(self.review,'复盘')
        self.future=Card()
        row=QHBoxLayout()
        self.scope=QComboBox()
        self.scope.addItems(('演练',))
        self.scope.hide()
        row.addWidget(self.scope,1)
        row.addWidget(ActionButton('更新研判',self.estimate,variant='primary'))
        self.future.body.addLayout(row)
        self.future_list=QListWidget()
        self.future_list.setWordWrap(True)
        from PySide6.QtCore import Qt
        self.future_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.future_list.itemClicked.connect(self.jump_to_hint)
        self.future.body.addWidget(self.future_list,1)
        row=QHBoxLayout()
        row.addWidget(ActionButton('登记排班',self.shift))
        row.addWidget(ActionButton('保存演练提示',self.publish))
        self.future.body.addLayout(row)
        row=QHBoxLayout()
        row.addWidget(ActionButton('登记提前联络',lambda:self.ahead('提前联络')))
        row.addWidget(ActionButton('预置观察岗',lambda:self.ahead('预置观察岗')))
        self.future.body.addLayout(row)
        training.views.addTab(self.future,'未来')

    def estimate(self):
        self.context.safe(lambda:self.session.estimate(self.scope.currentText()))

    def jump_to_hint(self,item):
        i=self.future_list.row(item)
        if 0<=i<len(self.session.outlook):
            hint=self.session.outlook[i]
            self.session.seek(int((hint.at-self.session.frames.origin).total_seconds()))

    def publish(self):
        i=self.future_list.currentRow()
        if not 0<=i<len(self.session.outlook):
            self.context.status.emit('先选择一条未来提示',4000)
            return
        hint=self.session.outlook[i]
        if self.context.safe(lambda:(self.session.publish_note(hint),True)[1]):
            self.context.status.emit('已保存在本机',5000)

    def ahead(self,kind):
        index=self.future_list.currentRow()
        if not 0<=index<len(self.session.outlook):
            self.context.status.emit('先选择一条未来提示',4000)
            return
        hint=self.session.outlook[index]
        if QMessageBox.question(self.future,'确认提前安排','保存这项本机安排？')==QMessageBox.Yes:
            self.context.safe(lambda:self.session.record_ahead_action(hint,kind))

    def shift(self):
        staff=self.context.civic.data['staff']
        if not staff:
            self.context.status.emit('请在管理里添加值班人员',4500)
            return
        dialog, form, buttons = dialog_form(self.future, '登记排班')
        person = QComboBox()
        for row in staff:
            person.addItem(row['name'], row['id'])
        start = QDateTimeEdit()
        start.setTimeZone(QTimeZone(8 * 3600))
        start.setCalendarPopup(True)
        start.setDisplayFormat('yyyy-MM-dd HH:mm')
        local = self.session.solar_time().astimezone(CHINA).replace(second=0, microsecond=0)
        start.setDateTime(QDateTime.fromString(local.isoformat(), Qt.ISODate))
        minutes = QSpinBox()
        minutes.setRange(1, 720)
        minutes.setValue(60)
        minutes.setSuffix(' 分钟')
        form.addRow('值班人员', person)
        form.addRow('开始时间（北京时间）', start)
        form.addRow('时长', minutes)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            begin = start.dateTime().toPython().replace(tzinfo=CHINA)
            end = begin + timedelta(minutes=minutes.value())
            self.context.safe(lambda: self.session.save_shift(person.currentData(), begin, end))

    def _perform(self, kind):
        def run():
            if kind=='call':
                text,ok=QInputDialog.getItem(self.card,'记录联系结果','结果',CALL_OUTCOMES,editable=False)
                if ok:
                    self.session.perform(kind,result=text)
            elif kind=='handoff':
                obj=self.session.selected_object()
                if not obj:
                    raise ValueError('请选择演练对象')
                name,_=QFileDialog.getSaveFileName(self.card,'导出移交清单','演练移交.json','JSON (*.json)')
                if name:
                    from dataclasses import asdict
                    track=self.session.frames.tracks[obj['id']][0]
                    data=dict(source='本机演练',target=obj['id'],assessment=asdict(obj['attention']),
                              points=[p for p in track['points'] if p['t']<=self.session.now],
                              actions=[r for r in self.session.actions if r['target']==obj['id']])
                    Path(name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
                    self.session.perform(kind,result='已导出移交清单')
                    self.session.close_event('已移交')
            elif kind=='trigger':
                seconds,ok=QInputDialog.getInt(self.card,'观察触发条件','预计进入敏感区的提前秒数',120,10,300)
                if ok:
                    self.session.perform(kind,lead=seconds)
            elif kind in ('dispatch','notify','search'):
                if QMessageBox.question(self.card,'确认演练处置','确认记入本机演练处置？') == QMessageBox.Yes:
                    self.session.perform(kind)
            else:
                self.session.perform(kind)
        self.context.safe(run)

    def _complete(self, row):
        if row['kind'] in ('dispatch','search'):
            dialog, form, buttons = dialog_form(self.card, '现场核验')
            outcome = QComboBox()
            outcome.addItems(('已找到飞手', '未找到飞手', '已找到设备', '继续核验'))
            report = QLineEdit()
            report.setPlaceholderText('机型、报备状态、处置方式')
            form.addRow('现场结果', outcome)
            form.addRow('补充说明', report)
            form.addRow(buttons)
            if dialog.exec() == QDialog.Accepted:
                result = outcome.currentText() + ' · ' + (report.text().strip() or '资料待确认')
                self.context.safe(lambda: self.session.finish_action(row['id'], result))
        else:
            reason,ok=QInputDialog.getText(self.card,'处置结果','结果')
            if ok:
                self.context.safe(lambda:self.session.finish_action(row['id'],reason))

    def _close(self):
        result,ok=QInputDialog.getItem(self.card,'结案','结论',OUTCOMES,editable=False)
        if ok:
            self.context.safe(lambda:self.session.close_event(result))

    def _report(self):
        name,_=QFileDialog.getSaveFileName(self.review,'导出交接班报告','低空守望交接.md','Markdown (*.md)')
        if name:
            self.context.safe(lambda:Path(name).write_text(self.session.report(),encoding='utf8'))

    def refresh(self, obj):
        session=self.session
        key=(session.mode,session.selected,obj['attention'].level if obj else None,session.cursor//4,
             tuple((r['id'],r['state'],r['result']) for r in session.actions))
        if key!=self._key:
            self._key=key
            self.show_actions(obj)
            self.show_review()
        if session.comparison is not self._comparison_key:
            self._comparison_key=session.comparison
            self.show_review()
        if session.outlook is not self._future_key:
            self._future_key=session.outlook
            with QSignalBlocker(self.future_list):
                self.future_list.clear()
                for item in session.outlook:
                    status=f'预计 {item.level} 级 · ' if item.level else ''
                    self.future_list.addItem(f'{item.at:%m-%d %H:%M}  {item.kind} · {item.place}\n{status}{item.reason}')

    def show_actions(self,obj):
        session=self.session
        clear(self.actions)
        if not obj:
            self.actions.addWidget(label('选择对象查看处置'))
            return
        records=[r for r in session.actions if r['target']==obj['id'] and r['at']<=session.cursor]
        staff=sum(p['available'] for p in self.context.civic.data['staff'])
        active={r.get('person_id') for r in session.actions
                if r['state']=='进行中' and r.get('person_id')}
        group,steps=recommendation(obj['attention'].level,obj['filed'])
        self.actions.addWidget(SectionHeader(f"{obj['id']} · {group}"))
        self.actions.addWidget(label(f'人员空闲 {max(0,staff-len(active))} · 演练'))
        in_progress={r['kind'] for r in records if r['state']=='进行中'}
        ended={r['kind'] for r in records if r['state']=='已完成'}
        live=session.mode=='实时' and not session.is_closed(obj['id'])
        _, plans=session.agent_plans(obj)
        for plan in plans:
            item=Card()
            item.body.setContentsMargins(9,7,9,7)
            item.body.setSpacing(3)
            header=QHBoxLayout()
            header.addWidget(SectionHeader(f'{plan["code"]} · {plan["name"]}'),1)
            if plan['recommended']:
                header.addWidget(Badge('智能体推荐','info'))
            item.body.addLayout(header)
            timing=f'约 {plan["eta_s"]} 秒' if plan['eta_s'] is not None else '等待人员'
            item.body.addWidget(label(f'{timing} · 需要 {plan["staff_needed"]} 人'))
            if assigned:=plan['assignment']:
                item.body.addWidget(label(f'{assigned["person"]} → {assigned["place"]}'))
            if plan['code']!='A':
                item.body.addWidget(label(plan['coverage_label']))
            item.body.addWidget(label(' → '.join(plan['steps'])))
            adopt=ActionButton('采纳此方案',lambda code=plan['code']:self.context.safe(
                lambda:session.adopt_agent_plan(code)),variant='primary' if plan['recommended'] else 'secondary')
            adopt.setEnabled(live)
            item.body.addWidget(adopt)
            self.actions.addWidget(item)
        for kind,title,policy in steps:
            if policy=='auto':
                self.actions.addWidget(label(title+(' · 已执行' if kind in in_progress|ended else ' · 待运行')))
            elif live and kind not in in_progress:
                self.actions.addWidget(ActionButton(title,lambda k=kind:self._perform(k),
                                                    variant='primary' if kind=='dispatch' else 'secondary'))
        if live:
            detail=label('')
            if obj['filed']:
                if dev:=obj['dev']:
                    detail.setText(f"联络要点 · 核对 {dev['horizontal_m']:.0f} 米横向偏离")
                    self.actions.addWidget(detail)
                if group!='提醒':
                    self.actions.addWidget(ActionButton('记录联系飞手',lambda:self._perform('call')))
            if suggestion:=session.reassign_candidate(obj):
                self.actions.addWidget(ActionButton('确认改派 '+suggestion['person'],
                                       lambda i=suggestion['id']:self.context.safe(lambda:session.redirect(i))))
            self.actions.addWidget(ActionButton('设置观察提醒',lambda:self._perform('trigger')))
            request=next((r for r in records if r['kind']=='request'),None)
            if request:
                landing=request['landing']
                self.actions.addWidget(label(f"失联求助 · {landing['basis']} · 半径约 {landing['radius_m']:g} 米"))
                if 'search' not in in_progress|ended:
                    self.actions.addWidget(ActionButton('安排演练搜寻',lambda:self._perform('search'),variant='primary'))
                if 'search' in ended and 'request_ack' not in ended:
                    self.actions.addWidget(ActionButton('发送本机回执',lambda:self.context.safe(session.acknowledge)))
            self.actions.addWidget(ActionButton('结案',self._close))
        else:
            self.actions.addWidget(label('回到现在才能操作' if session.mode!='实时' else '已结案'))
        for row in reversed(records):
            state='进行中' if row.get('finished_at',-1)>session.cursor else row['state']
            text=f"{row.get('title',row['kind'])} · {state} · {row['source']}"
            if row.get('person'):
                text+=f" · {row['person']}到{row['place']} · {row['walk']['seconds']}秒（估算）"
            if row['result'] and state=='已完成':
                text+=' · '+row['result']
            self.actions.addWidget(label(text,'bodyText'))
            if live and row['state']=='进行中' and row['kind'] in ('dispatch','search','notify'):
                self.actions.addWidget(ActionButton('填写结果',lambda r=row:self._complete(r)))
        self.actions.addStretch()

    def show_review(self):
        session=self.session
        clear(self.review_body)
        self.review_body.addWidget(SectionHeader('演练复盘'))
        self.review_body.addWidget(ActionButton('从这里重来',lambda:self.context.safe(session.fork),variant='primary'))
        self.review_body.addWidget(ActionButton('导出交接班报告',self._report))
        if session.comparison:
            original=summarize(session.comparison['baseline'])
            branch=summarize(session.actions)
            for title,stats in (('原演练',original),('当前分支',branch)):
                closed='、'.join(f'{key} {n}' for key,n in stats['counts'].items()) or '暂无'
                average=stats['average'] if stats['average'] is not None else '待记录'
                self.review_body.addWidget(label(f'{title} · 结案 {closed} · 用时 {average} · 步骤 {len(session.comparison["baseline"] if title=="原演练" else session.actions)}'))
        for row in session.actions:
            if row['kind']=='close':
                self.review_body.addWidget(label(f"{row['target']} · 结案 · {row['result']}"))
        self.review_body.addStretch()
