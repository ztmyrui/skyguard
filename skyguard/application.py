"""Use cases; depend on domain and ports, not Qt or storage implementations."""
from dataclasses import asdict
from datetime import datetime, timezone
from .system_clock import now as system_now
import csv
import io
import json
from uuid import uuid4
from threading import RLock
from . import __version__
from .domain import Scenario, Observation, Person, demo, snapshot, impact_grid, distance
from .incident import IncidentStatus, incident_record, transition
from .planning import MODES, parameters, planning_snapshot, plan_staleness, schedule_templates
from .planning_migration import migrate_planning
from .ports import Repository
from .task_flow import ACTIVE, replace as replace_task, tasks_resolved, transition as transition_task


class Service:
    def __init__(self,repo: Repository,workflow,civic=None):
        self.repo=repo;self.workflow=workflow;self.civic=civic
        data=repo.load()
        self.scene=Scenario.from_dict(data['scene']) if data else demo()
        self.pending=data.get('pending') if data else None
        self.settings=data.get('settings',{}) if data else {}
        self.incidents = self._load_incidents(data)
        self.links=dict(data.get('links',{})) if data else {}
        self._planning_lock = RLock()
        audits = [] if not data or data.get('schema', 0) >= 4 else repo.history()
        migrated = migrate_planning(self.scene, self.pending, audits)
        for task in self.scene.tasks:
            task.setdefault('history', [])
        if self.civic:
            self.sync_staff()
        if migrated:
            self.repo.commit_many(self.state(), [
                ('迁移筹划任务', migrated),
                ('启动会话', {'version': __version__}),
            ])
        else:
            self.persist('启动会话', {'version': __version__})

    def state(self):
        return {'schema':4,'scene':self.scene.to_dict(),'pending':self.pending,'settings':self.settings,
                'incidents':self.incidents,'links':self.links}

    @property
    def closed(self):
        return {eid for eid, record in self.incidents.items() if record['status'] == IncidentStatus.CLOSED}

    def _load_incidents(self, data):
        records = dict(data.get('incidents', {})) if data else {}
        for observation in self.scene.observations:
            records.setdefault(observation.id, incident_record(IncidentStatus.PENDING))
        if data and 'incidents' not in data:
            at = system_now(timezone.utc).isoformat()
            closed = set(data.get('closed', []))
            active = {task['event_id'] for task in self.scene.tasks if task['status'] in ACTIVE}
            for eid in closed | (active & records.keys()):
                status, note = (IncidentStatus.CLOSED, '迁移自 v0.7') if eid in closed else (
                    IncidentStatus.VERIFYING, '迁移自 v0.7，存在活跃任务')
                records[eid] = {
                    **incident_record(status),
                    'history': [{'at': at, 'action': 'migrate', 'from': None,
                                 'to': status.value, 'note': note}],
                }
        return records

    def persist(self,action='保存状态',detail=None):
        self.repo.commit(self.state(),action,detail or {})

    def changed(self,action,detail=None):
        self.scene.revision += 1
        try:
            self.persist(action, detail)
        except Exception:
            self.scene.revision -= 1
            raise

    def sync_staff(self):
        self.scene.people=[Person(r['id'],r['name'],r['role'],r['available'])
                            for r in self.civic.data['staff']]

    def view(self, observation, *, closed=None, measure_impact=True):
        v = snapshot(self.scene, observation, radar=self.civic is not None, measure_impact=measure_impact)
        incident = self.incidents[observation.id]
        v.update(status=incident['status'], conclusion=incident['conclusion'],
                 tasks_resolved=self.event_tasks_resolved(observation.id))
        if not self.civic:
            return v
        v['evidence_candidates'] = []
        v['linked_track'] = self.links.get(observation.id)
        v['linked_reports'] = [key for key, value in self.links.items() if value == observation.id]
        v['rule_hits'] = []
        v['plan_candidates'] = self.civic.plan_candidates(v)
        if v['track_state'] == '仅目击待核实':
            from .radar import candidate_tracks
            if closed is None:
                closed = self.closed
            v['evidence_candidates'] = [r for r in candidate_tracks(v,self.scene.clock,distance)
                                        if r['id'] not in closed]
        else:
            v['rule_hits'] = self.civic.radar_rules(v,self.scene.clock)
            if v['rule_hits']:
                v['priority'] = '优先核验'
        return v

    def views(self, include_closed=False, *, measure_impact=True):
        closed = self.closed
        return [self.view(o, closed=closed, measure_impact=measure_impact) for o in self.scene.observations
                if (include_closed if o.id in closed else
                    not self.civic or o.moving or o.observed_s <= self.scene.clock)]

    def associate(self, report_id, track_id):
        report = next((v for v in self.views() if v['id'] == report_id), None)
        if not report or not any(r['id'] == track_id for r in report['evidence_candidates']):
            raise ValueError('当前没有对应的轨迹候选，请重新核对。')
        self.links[report_id] = track_id
        self.changed('人工关联目击与轨迹',{'report':report_id,'track':track_id})

    def map_payload(self, *, include_grid=True):
        return {'observations':self.views(measure_impact=include_grid),'areas':[{**asdict(a),
            'index':round(a.base*(1.8 if a.active and a.start_s<=self.scene.clock<a.end_s else .65),1)} for a in self.scene.areas],
            'grid':impact_grid(self.scene) if include_grid else []}

    def tick(self, seconds):
        self.scene.clock = max(0, min(self.scene.duration_s, self.scene.clock + seconds))

    def add_observation(self,data):
        data={**data};data.setdefault('id',str(uuid4())[:8]);data.setdefault('observed_s',self.scene.clock)
        if data['id'] in self.incidents:
            raise ValueError('报告编号已存在，请使用新的编号')
        o=Observation(**data)
        if o.observed_s>self.scene.clock: raise ValueError('请选择当前演练时刻或更早的观察时间')
        self.scene.observations.append(o)
        self.incidents[o.id] = incident_record()
        try:
            self.changed('新增报告', {'id': o.id, 'source': o.source})
        except Exception:
            self.scene.observations.pop()
            self.incidents.pop(o.id)
            raise
        return o.id

    def event_change(self,eid,kind):
        o=next(o for o in self.scene.observations if o.id==eid)
        if kind=='conflict': o.conflict=not o.conflict
        elif kind=='stale':
            if o.moving:
                v=snapshot(self.scene,o);o.lat=v['lat'];o.lon=v['lon']
                o.observed_s=self.scene.clock-70;o.moving=False
            else: o.observed_s=self.scene.clock-70
        elif kind=='refresh':
            v=snapshot(self.scene,o);o.lat=v['lat'];o.lon=v['lon'];o.observed_s=self.scene.clock;o.conflict=False
        else: raise ValueError('未知事件动作')
        self.changed('演练信息变化',{'id':eid,'kind':kind})

    def set_activity(self, aid, end, active):
        area = next(a for a in self.scene.areas if a.id == aid)
        if not area.start_s < end <= self.scene.duration_s:
            raise ValueError('结束时间必须晚于开始，并位于演练范围内')
        area.end_s = int(end)
        area.active = bool(active)
        self.changed('修改活动', {'id': aid, 'end_s': end, 'active': active})

    def set_person(self, pid, available):
        person = next(p for p in self.scene.people if p.id == pid)
        person.available = bool(available)
        self.changed('人员状态变化', {'id': pid, 'available': available})

    def set_operation(self, purpose, earliest, latest, duration):
        if not purpose.strip() or not 0 <= earliest < latest <= self.scene.duration_s or not 60 <= duration <= latest - earliest:
            raise ValueError('请填写拍摄目的，并保证时间窗口容纳作业时长')
        self.scene.operation = dict(purpose=purpose.strip(), earliest=earliest, latest=latest, duration=duration)
        self.changed('更新航拍需求', self.scene.operation)

    def planning_context(self):
        return self.civic.planning_context() if self.civic else {}

    def generate(self, eid, mode, observation_minutes=3, preference='更快核验', selected_people=None):
        params = parameters(mode, observation_minutes, preference, selected_people)
        with self._planning_lock:
            view = next((v for v in self.views() if v['id'] == eid), None)
            if view is None:
                raise ValueError('事件已关闭或在当前时刻尚不可用。')
            return self._generate(view, params)

    def assign_templates(self, event_id, chosen):
        if not any(view['id'] == event_id for view in self.views()):
            raise ValueError('请选择处理中的事件')
        with self._planning_lock:
            active = {t['key'] for t in self.scene.tasks if t['event_id'] == event_id
                      and t.get('mode') == '协同模板' and t['status'] in ACTIVE}
            requested = [key for key in chosen if key not in active]
            if chosen and not requested:
                return []
            schedule = schedule_templates(self.scene, self.planning_context(), requested)
            thread = 'template-' + uuid4().hex
            tasks = [dict(task, id=f'{thread}-{i}', event_id=event_id, plan_thread=thread,
                          mode='协同模板', status='待接单', feedback='', history=[])
                     for i,task in enumerate(schedule)]
            self.scene.tasks.extend(tasks)
            try:
                self.changed('建立协同任务', {'event': event_id, 'tasks': [t['name'] for t in tasks]})
            except Exception:
                del self.scene.tasks[-len(tasks):]
                raise
            return tasks

    def _generate(self, view, params):
        scene = self.scene
        context = self.planning_context()
        inputs = planning_snapshot(scene, view, context, params)
        thread = f'plan-{uuid4()}'
        result = self.workflow.start(thread, scene.to_dict(), view, context, inputs)
        before = self.pending
        self.pending = dict(thread=thread, event_id=view['id'], snapshot=inputs, confirmed=False,
                            **{key: result[key] for key in ('plans', 'gaps', 'trace')})
        try:
            self.persist('生成候选方案', {'event': view['id'], 'count': len(result['plans']),
                                     'thread': thread, 'clock': inputs['clock']})
        except Exception:
            self.pending = before
            raise
        return self.pending

    def staleness(self, pending, index=0, params=None):
        """Use the same snapshots for the page and its confirmation action."""
        if not pending or pending.get('confirmed'):
            return []
        inputs = pending.get('snapshot')
        if not inputs:
            return plan_staleness(pending, {}, self.scene.clock)
        view = next((v for v in self.views(include_closed=True) if v['id'] == pending['event_id']), None)
        current = planning_snapshot(self.scene, view or {'id': pending['event_id']},
                                    self.planning_context(), inputs['params'] if params is None else params)
        return plan_staleness(dict(pending, selected=index), current, self.scene.clock)

    def confirm(self, index, *, params=None):
        with self._planning_lock:
            return self._confirm(index, params)

    def _confirm(self, index, params):
        pending = self.pending
        if not pending or not 0 <= index < len(pending['plans']):
            raise ValueError('请选择有效方案')
        if pending.get('confirmed'):
            if pending['selected'] == index:
                return pending
            raise ValueError('当前方案批次已经确认')
        reasons = self.staleness(pending, index, params)
        if reasons:
            raise ValueError('方案已过期，请重新生成：\n' + '\n'.join(reasons))
        plan = pending['plans'][index]
        result = self.workflow.resume(pending['thread'], index, self.scene.clock)
        if result['chosen'] != plan:
            raise ValueError('复核记录与所选方案不一致，请重新生成候选方案')
        self._adopt_plan(pending, index, plan['tasks'], result['trace'])
        return pending

    def _adopt_plan(self, pending, index, tasks, trace):
        eid, thread = pending['event_id'], pending['thread']
        previous_incident = self.incidents[eid]
        auto_start = previous_incident['status'] in (IncidentStatus.NEW, IncidentStatus.PENDING)
        entries = [('人工确认方案', {'event': eid, 'thread': thread, 'index': index})]
        if auto_start:
            record, detail = self._incident_transition(eid, 'start', '确认方案后自动开始核验', None)
            entries.append(('事件状态', detail))
            self.incidents[eid] = record
        tasks_before = len(self.scene.tasks)
        replaced = []
        at = system_now(timezone.utc).isoformat()
        for t in self.scene.tasks:
            if t['event_id'] == eid and t['status'] == '待接单' and t.get('mode') != '协同模板':
                replaced.append((t, dict(t)))
                t.update(replace_task(t, at=at))
                entries.append(self._task_audit(t))
        # Deterministic IDs make a resume/retry unable to duplicate business tasks.
        for i, t in enumerate(tasks):
            tid = f'{thread}-{i}'
            if not any(v['id']==tid for v in self.scene.tasks):
                self.scene.tasks.append({**t, 'id': tid, 'event_id': eid, 'status': '待接单',
                                         'feedback': '', 'history': [], 'plan_thread': thread,
                                         'mode': pending['plans'][index]['mode']})
        previous_trace = pending['trace']
        pending['trace'] = trace
        pending['confirmed'] = True
        pending['selected'] = index
        self.scene.revision += 1
        try:
            self.repo.commit_many(self.state(), entries)
        except Exception:
            self.scene.revision -= 1
            self.incidents[eid] = previous_incident
            del self.scene.tasks[tasks_before:]
            for task, original in replaced:
                task.clear()
                task.update(original)
            pending['confirmed'] = False
            pending.pop('selected', None)
            pending['trace'] = previous_trace
            raise

    def task_transition(self, tid, note=''):
        """Preserve the original three-step API and its review note requirement."""
        with self._planning_lock:
            status = self._find_task(tid)['status']
            action = {'待接单': 'accept', '进行中': 'submit', '待复核': 'approve'}.get(status)
            if action is None:
                raise ValueError('任务已经结束')
            if action != 'accept' and (not isinstance(note, str) or not note.strip()):
                raise ValueError('请填写执行结果或复核说明')
            self.task_action(tid, action, note)

    def _find_task(self, tid):
        task = next((item for item in self.scene.tasks if item['id'] == tid), None)
        if task is None:
            raise ValueError('找不到该任务')
        return task

    def event_tasks_resolved(self, event_id):
        record = self.incidents.get(event_id)
        return bool(record and record['status'] == IncidentStatus.VERIFYING and tasks_resolved(
            [task for task in self.scene.tasks if task['event_id'] == event_id],
        ))

    @staticmethod
    def _task_audit(task):
        entry = task['history'][-1]
        timed_out = entry['action'] == 'accept' and entry['to'] == '已替代'
        return ('观察任务超时' if timed_out else '任务状态更新',
                {'id': task['id'], 'event': task['event_id'], 'status': task['status'], **entry})

    def task_action(self, tid, action, note='', outcome=None):
        """Apply a task action and persist its audit and any event transition together."""
        with self._planning_lock:
            task = self._find_task(tid)
            updated = transition_task(task, action, tasks=self.scene.tasks, clock=self.scene.clock,
                                      note=note, outcome=outcome, at=system_now(timezone.utc).isoformat())
            self._commit_task_action(task, updated, action)
            return task

    def _commit_task_action(self, task, updated, action):
        eid = task['event_id']
        previous = self.incidents.get(eid)
        entries = [self._task_audit(updated)]
        if (action == 'unable' and previous and previous['status'] == IncidentStatus.VERIFYING
                and not any(other is not task and other['event_id'] == eid and other['status'] in ACTIVE
                            for other in self.scene.tasks)):
            record, detail = self._incident_transition(eid, 'block', '任务无法完成', None)
            self.incidents[eid] = record
            entries.append(('事件状态', detail))
        original = dict(task)
        task.update(updated)
        self.scene.revision += 1
        try:
            self.repo.commit_many(self.state(), entries)
        except Exception:
            self.scene.revision -= 1
            task.clear()
            task.update(original)
            if previous is not None:
                self.incidents[eid] = previous
            raise

    def _incident_transition(self, eid, action, note, conclusion):
        if eid not in self.incidents:
            raise ValueError('找不到该事件')
        open_tasks = sum(t['event_id'] == eid and t['status'] in ACTIVE
                         for t in self.scene.tasks)
        record = transition(self.incidents[eid], action, note=note, conclusion=conclusion,
                            open_tasks=open_tasks, at=system_now(timezone.utc).isoformat())
        detail = {'id': eid, **record['history'][-1]}
        if record['conclusion']:
            detail['conclusion'] = record['conclusion']
        return record, detail

    def incident_action(self, event_id, action, note='', conclusion=None):
        with self._planning_lock:
            record, detail = self._incident_transition(event_id, action, note, conclusion)
            previous = self.incidents[event_id]
            self.incidents[event_id] = record
            try:
                self.changed('事件状态', detail)
            except Exception:
                self.incidents[event_id] = previous
                raise
            return record

    def close_event(self,eid,note):
        if not note.strip():
            raise ValueError('请填写关闭说明')
        incident = self.incidents.get(eid)
        action = 'approve' if incident and incident['status'] == IncidentStatus.REVIEW else 'force_close'
        self.incident_action(eid, action, note)

    def reset(self):
        self.scene=demo(); self.pending=None;self.links={}
        self.incidents = self._load_incidents(None)
        if self.civic:self.sync_staff()
        self.persist('重置演练',{})

    def import_observations(self,text,fmt):
        rows=json.loads(text) if fmt=='json' else list(csv.DictReader(io.StringIO(text)))
        if not isinstance(rows,list) or len(rows)>1000: raise ValueError('需要不超过1000条的报告数组')
        prepared=[];ids=set(self.incidents)
        for row in rows:
            d=dict(row)
            for key in ['lat','lon','vx','vy','observed_s','accuracy_m']:
                if key in d: d[key]=None if d[key] in ('',None) and key in ('lat','lon') else float(d[key])
            for key in ['conflict','cooperation','moving']:
                if key in d and isinstance(d[key],str): d[key]=d[key].lower() in ('true','1','yes')
            d.setdefault('id',str(uuid4())[:8]);d.setdefault('source','synthetic');d.setdefault('observed_s',self.scene.clock)
            o=Observation(**d)
            if o.id in ids: raise ValueError('重复报告编号：'+o.id)
            if o.observed_s>self.scene.clock: raise ValueError('存在未来时间报告')
            ids.add(o.id);prepared.append(o)
        self.scene.observations.extend(prepared)
        self.incidents.update((o.id, incident_record()) for o in prepared)
        try:
            self.changed('导入报告', {'count': len(prepared), 'events': [o.id for o in prepared]})
        except Exception:
            if prepared:
                del self.scene.observations[-len(prepared):]
            for o in prepared:
                self.incidents.pop(o.id)
            raise
        return len(prepared)

    def export(self):
        return {'label':'低空守望内部记录 · 含演练数据','version':__version__,
                'exported_at':system_now(timezone.utc).isoformat(),
                **self.state(),'closed':sorted(self.closed),'assessments':self.views(),'audit':self.repo.history(), **(self.civic.export_records() if self.civic else {})}
