"""Local civilian coordination on the training data store."""
from copy import deepcopy
from ...system_clock import now as system_now
from datetime import timedelta
import json

from ...aerial import CHINA
from ...civic_replay import CivicReplay
from ...disposal import OUTCOMES, REPLIES, apply, feedback, fork_at, movement, observe_point, recommendation, simulated_landing, summarize, walking
from ...flight_route import FlightRoute, display_route, planned_position
from ...flight_rules import instant
from ...outlook import review
from ...sim_flight import measurement
from ...training_tracks import _empty
from ... import situation_agent as agent
from .training_session import TrainingSession


class WorkflowSession(TrainingSession):
    def __init__(self, context):
        super().__init__(context)
        self.data.setdefault('actions',{})
        self.data.setdefault('roster',[])
        self.data.setdefault('public_notes',[])
        self.data.setdefault('contacts',{})
        self.selected_outlook=None
        self.future_scope='演练'
        self.comparison=None
        self.outlook=()
        self.outlook_zones=()
        self.autopilot_mode='assist'
        self.overlay_public=False
        self.sync_public=False
        self._sync_city=None
        self._sync_anchor=None
        self._public_actions=[]
        self._injection=None
        self._agent_history={}
        self._agent_events=[]
        self.civic_replay = None

    def open_dataset(self):
        self._reset_agent()
        self.civic_replay = None
        from ...infrastructure import SQLiteRepository
        self.repo = SQLiteRepository(self.context.data_root / 'training.sqlite3')
        self.data = self.repo.load() or dict(version=1, library={}, types=None, rules=None, zones={})
        for key, default in (('actions', {}), ('roster', []), ('public_notes', []), ('contacts', {})):
            self.data.setdefault(key, default)
        self.scenario = self.frames = self.comparison = None
        self.now = self.cursor = 0
        self.following = True
        self.clock_running = False
        self.future_scope = '演练'
        self.selected = ''
        self.outlook = self.outlook_zones = ()

    def key(self):
        return self.scenario['name']+'@'+self.scenario['start'] if self.scenario else ''

    @property
    def actions(self):
        return self.data['actions'].get(self.key(),[]) + self._public_actions

    def _write(self, actions, title, detail, key=None):
        scope = key or self.key()
        if scope == self.key():
            self._public_actions=[r for r in actions if r.get('public_twin')]
        persisted=[r for r in actions if not r.get('public_twin')]
        if persisted != self.data['actions'].get(scope, []):
            self.data['actions'][scope]=persisted
            self._store(title,detail)

    def save_shift(self, person, start, end):
        if end<=start:
            raise ValueError('排班结束须晚于开始')
        rows=self.data['roster']
        item=dict(person=person,start=start.isoformat(),end=end.isoformat())
        if item not in rows:
            rows.append(item)
            self._store('登记排班',item)
        self.changed.emit()

    def rebuild(self):
        super().rebuild()
        self._agent_history.clear()
        self._agent_events.clear()
        if self.overlay_public:
            self._overlay_tracks()
        self.frames.feedback=self.feedback_at

    def feedback_at(self, identifier, second):
        rows=[row for row in self.actions if row['target']==identifier and row['at']<=second
              and row.get('finished_at',row['at'])<=second]
        return dict(feedback(rows),observer_verified=any(
            row['kind']=='dispatch' and row['state']=='已完成' for row in rows))

    def invalidate_after(self, second):
        self.frames._cache={k:v for k,v in self.frames._cache.items() if k<second}
        self._agent_history={k:v for k,v in self._agent_history.items() if k<second}
        self._agent_events=[e for e in self._agent_events if e['at']<second]

    def action_marks(self):
        return tuple((f"{r['target']} · {r.get('title',r['kind'])}",r['at'])
                     for r in self.actions if r['kind']!='watch')

    def selected_object(self):
        return next((o for o in self.objects() if o['id']==self.selected),None)

    def batch(self, kind, identifiers):
        if self.mode!='实时' or kind not in ('watch','nudge'):
            raise ValueError('回到实时演练选择观察或提醒')
        chosen=set(identifiers)
        count = 0
        for obj in self.objects():
            if obj['id'] in chosen and (kind=='watch' or obj['filed']) and not self.is_closed(obj['id']):
                self._action_at(obj['id'],kind,self.now,title='持续观察' if kind=='watch' else '本机提醒',
                                plan_id=obj['plan_id'],result='' if kind=='watch' else '请核对飞行计划')
                count += 1
        self.changed.emit()
        return count

    def reassign_candidate(self,obj):
        on={p['id'] for p in self.context.civic.data['staff'] if p['available']}
        claimed={r.get('person_id') for r in self.actions if r.get('state')=='进行中' and r.get('walk')}
        if on-claimed:
            return None
        grades={row['id']:row['attention'].level for row in self.objects()}
        options=[r for r in self.actions if r.get('state')=='进行中' and r.get('walk')
                 and r['target']!=obj['id'] and grades.get(r['target'],0)<obj['attention'].level]
        return min(options,key=lambda r:(grades.get(r['target'],0),r['at'],r['id']),default=None)

    def redirect(self,identifier):
        obj=self.selected_object()
        if not obj or self.mode!='实时':
            raise ValueError('请返回实时状态')
        old=self.reassign_candidate(obj)
        if not old or old['id']!=identifier:
            raise ValueError('请重新查看人员建议')
        task=next(r for r in self.actions if r['id']==identifier)
        task.update(state='已取消',result='已改派',finished_at=self.now)
        self._write(self.actions,'确认改派',{'from':task['target'],'to':obj['id'],'person':task['person']})
        self.perform('dispatch')

    def _action_at(self,target,kind,second,*,policy='值班员确认',result='',**details):
        if target.startswith('PUB-'):
            details['public_twin']=True
        new,row=apply(self.actions,target,kind,second,source=policy,result=result,**details)
        if new!=self.actions:
            self._write(new,'演练 · '+row.get('title',kind),{'id':row['id'],'target':target,'source':policy})
            self.invalidate_after(second)
        return row

    def perform(self, kind, *, result='', lead=120):
        if self.mode!='实时':
            raise ValueError('回到现在才能操作')
        obj=self.selected_object()
        if not obj:
            raise ValueError('请选择演练对象')
        if self.is_closed(obj['id']):
            raise ValueError('该对象已有结案记录')
        if kind in ('dispatch','search'):
            details=self._assignment(obj,search=(kind=='search'))
        elif kind in ('call','nudge','notify','handoff','watch','trigger'):
            details={'plan_id':obj['plan_id']}
            if kind=='trigger':
                details['lead']=lead
        else:
            raise ValueError('请选择处置动作')
        titles={'dispatch':'观察岗核验','search':'查找演练设备','call':'联络飞手','nudge':'本机提醒',
                'notify':'区域负责人联络','handoff':'移交清单','watch':'持续观察','trigger':'观察触发条件'}
        row=self._action_at(obj['id'],kind,self.now,result=result,title=titles[kind],**details)
        self.changed.emit()
        return row

    def _assignment(self,obj,search=False):
        when=self.frames.origin+timedelta(seconds=self.now)
        city=self.context.civic.cities().get(self.scenario['city'],{})
        posts=[post for post in city.get('posts',()) if post.get('enabled',True)]
        if not posts:
            station=self.scenario['station']
            posts=[dict(id='training-station',name='演练定点观察岗',lon=station[0],lat=station[1])]
        assigned={r.get('person_id') for r in self.actions if r['state']=='进行中' and r['kind'] in ('dispatch','search')}
        roster=self.data['roster']
        on_shift={r['person'] for r in roster if instant(r['start'])<=when<instant(r['end'])}
        staff=[p for p in self.context.civic.data['staff'] if p['available'] and p['id'] not in assigned
               and (not roster or p['id'] in on_shift)]
        if not staff:
            raise ValueError('暂无空闲人员，请调整排班或现有任务')
        track,_,plan=self.frames.tracks[obj['id']]
        # Work toward an available observation post or the simulator's own recorded device.
        shared=plan.takeoff if plan else tuple(self.scenario['station'])
        if search and track['points']:
            sample=next(p for p in reversed(track['points']) if p['t']<=self.now)
            landing=simulated_landing(sample,self.wind)
            shared=tuple(landing['point'])
        finish,label=observe_point(posts,shared)
        post_index={p['id']:p for p in posts}
        choices=[]
        for person in staff:
            start=post_index.get(person['post_id'])
            start=(start['lon'],start['lat']) if start else self.scenario['station']
            choices.append((walking(start,finish),person))
        route,person=min(choices,key=lambda p:(p[0]['seconds'],p[1]['id']))
        return dict(person_id=person['id'],person=person['name'],walk=route,place=label,plan_id=obj['plan_id'])

    def is_closed(self,target):
        return any(r['target']==target and r['kind']=='close' and r['at']<=self.cursor for r in self.actions)

    def finish_action(self, action_id, result):
        if self.mode!='实时' or not result.strip():
            raise ValueError('回到现在并填写处置结果')
        rows=deepcopy(self.actions)
        row=next((r for r in rows if r['id']==action_id and r['state']=='进行中'),None)
        if not row:
            raise ValueError('请选择进行中的处置')
        row.update(state='已完成',finished_at=self.now,result=result.strip())
        self._write(rows,'完成演练处置',{'id':action_id,'result':row['result']})
        self.invalidate_after(self.now)
        self.changed.emit()

    def close_event(self,result):
        if result not in OUTCOMES or self.mode!='实时':
            raise ValueError('请选择结论并回到实时')
        obj=self.selected_object()
        if not obj:
            raise ValueError('请选择演练对象')
        if self.is_closed(obj['id']):
            return
        self._action_at(obj['id'],'close',self.now,result=result,title='结案',plan_id=obj['plan_id'])
        for filing in self.scenario['filings']:
            if filing['track']==obj['id']:
                filing['outcome']=result
        self.save()
        self.changed.emit()

    def pilot_messages(self,plan_id):
        rows=[]
        for scope,actions in self.data['actions'].items():
            for row in actions:
                if row.get('plan_id')==plan_id and row['kind'] in ('nudge','close','request_ack','reminder_reply'):
                    rows.append((scope,row))
        rows.extend((self.key(),row) for row in self._public_actions if row.get('plan_id')==plan_id
                    and row['kind'] in ('nudge','close','reminder_reply'))
        return tuple(rows)

    def pilot_reply(self,scope,identifier,plan_id,value):
        if value not in REPLIES:
            raise ValueError('请选择回复')
        rows=self.actions if scope==self.key() else self.data['actions'].get(scope,[])
        original=next((r for r in rows if r['kind']=='nudge' and r['id']==identifier and r.get('plan_id')==plan_id),None)
        if not original:
            raise ValueError('提醒已更新')
        if any(r.get('reply_to')==identifier for r in rows):
            return
        at=self.now if self.key()==scope else original['at']
        changed,row=apply(rows,original['target'],'reminder_reply',at,source='飞手',
                          title='飞手回复',result=value,plan_id=plan_id,reply_to=identifier,
                          public_twin=original.get('public_twin',False))
        self._write(changed,'记录飞手回复',{'id':row['id'],'value':value},scope)
        if self.key()==scope:
            self.invalidate_after(at)
        self.changed.emit()

    def lost_request(self,plan):
        if not self.state or not self.track or self.track['plan_id']!=plan['id']:
            self.start_flight(plan)
        landing=simulated_landing(measurement(self.state),self.wind)
        self._action_at(self.track['id'],'request',self.now,policy='飞手',title='失联求助',plan_id=plan['id'],
                        landing=landing)
        self.command('失联')

    def acknowledge(self):
        obj=self.selected_object()
        if not obj or self.mode!='实时':
            raise ValueError('请选择实时求助对象')
        if any(row['kind']=='request_ack' and row['target']==obj['id'] for row in self.actions):
            return
        self._action_at(obj['id'],'request_ack',self.now,title='失联回执',plan_id=obj['plan_id'],
                        result='观察人员已登记核验结果')
        self.changed.emit()

    def tick(self):
        if self.civic_replay:
            super().tick()
            return
        if self.frames and (self.scenario.get('flagship') or self.overlay_public or self.autopilot_mode=='auto') and not self.state:
            previous = self.now
            blocked = self.blockSignals(True)
            try:
                super().tick()
                current, cursor = self.now,self.cursor
                try:
                    for at in range(previous+1,current+1):
                        self.now = self.cursor = at
                        self.agent_step()
                finally:
                    self.now,self.cursor = current,cursor
                if self.sync_public:
                    self._sync_rules()
            finally:
                self.blockSignals(blocked)
            if previous != self.now:
                self.ticked.emit()
            return
        super().tick()
        if not self.frames or self.mode!='实时':
            return
        for obj in self.objects():
            prior=[r for r in self.actions if r['target']==obj['id']]
            if self.is_closed(obj['id']):
                continue
            group,actions=recommendation(obj['attention'].level,obj['filed'])
            for kind,title,policy in actions:
                if self.autopilot_mode!='observe' and policy=='auto' and not any(r['kind']==kind for r in prior):
                    self._action_at(obj['id'],kind,self.now,policy='系统自动',title=title,plan_id=obj['plan_id'],
                                    result='请核对计划航线与当前飞行' if kind=='nudge' else '')
            calls=[r for r in prior if r['kind']=='call']
            if calls and obj['dev'] and obj['dev']['level']=='容差内' and not any(r['kind']=='returned' for r in prior):
                call=calls[-1]
                was=self.frames.frame(max(0,call['at']-1))
                old=next((x for x in was if x['id']==obj['id']),None)
                if old and old['dev'] and old['dev']['level'] not in ('容差内','等待确认'):
                    self._action_at(obj['id'],'returned',self.now,title='回到航线',
                                    result=f"联系后 {self.now-call['at']} 秒回到航线",plan_id=obj['plan_id'])
            for row in prior:
                if row['kind']=='trigger' and row['state']=='进行中':
                    entry=obj['entry']
                    if entry and entry['seconds']<=row['lead']:
                        self.finish_action(row['id'],f"预计 {entry['seconds']} 秒后进入{entry['name']}")
                        self.context.status.emit(f"{obj['id']} · 已触发区域观察提醒",6000)
                if row['kind']=='search' and row['state']=='已完成' and not any(r['kind']=='request_ack' for r in prior):
                    self._action_at(obj['id'],'request_ack',self.now,title='失联回执',
                                    plan_id=obj['plan_id'],result=row['result'])
                if row.get('walk') and row['state']=='进行中' and self.now>=row['at']+row['walk']['seconds']:
                    preset=self.scenario.get('presets',{}).get(obj['id'])
                    if preset:
                        self.finish_action(row['id'],'情景预设 · '+preset)

    def map_layers(self,public=False):
        if self.civic_replay and not public:
            return self.civic_replay.layers(self.cursor, self.selected)
        layers=super().map_layers(public)
        if public or not layers:
            return layers
        active={r['target'] for r in self.actions if r['at']<=self.cursor
                and r['kind'] not in ('watch','request','trigger') and
                (r['state']=='进行中' or r.get('finished_at',-1)>self.cursor)}
        for row in layers['aircraft']:
            if row['id'] in active and row['state'] not in ('失联','已降落'):
                row['state']='处置中'
        layers['pins']=[] if self.scenario.get('flagship') else [
            dict(lon=where[0],lat=where[1],name=r['person'])
            for r in self.actions if (where:=movement(r,self.cursor))]
        if self.mode=='预演' and self.future_scope=='本机报备':
            for record in self.outlook_filings():
                snap=record['snapshot']
                if not snap['query'].get('route'):
                    continue
                route=FlightRoute.parse(snap['query']['route'])
                p=planned_position(route,self.solar_time())
                if p:
                    identifier='future:'+record['id']
                    layers['aircraft'].append(dict(id=identifier,lon=p.lon,lat=p.lat,altitude=p.altitude_m,
                                                   state='预计',level=1,label=snap['title']+' · 预计',tail=[]))
                    layers['routes'].append(dict(display_route(route),style='filing',id=identifier))
        if self.scenario.get('flagship') or self.overlay_public:
            formations, _ = self.agent_snapshot()
            layers.update(formations=formations, agent_links=self.agent_links(), observation_posts=self.agent_posts())
            zones={z.id:z for z in self.frames.zones}
            for layer in layers['zones']:
                if zone := zones.get(layer['id']):
                    layer.update(kind={'school':'zone-school','arterial':'zone-road','open':'zone-recovery',
                                       'mall':'zone-buffer','park':'zone-buffer',
                                       'event':'zone-s1'}.get(zone.kind, layer['kind']))
            trends={o['id']:o['attention'].trend for o in self.objects()}
            latest={r['target']:r['title'] for r in self.agent_log if r['at']<=self.cursor}
            for row in layers['aircraft']:
                row.update(label=f'{row["id"]} · L{row["level"]} {trends.get(row["id"],"—")} · {row["altitude"]:.0f}m',
                           public=row.get('public_plan',False), instant=self.mode!='实时', action=latest.get(row['id']))
                if row.get('facts') and (row.get('selected') or row['level']>=3):
                    row['cpa']=agent.analyse_target(row,self.frames.zones_at(self.cursor))['cpa']
        return layers

    def fork(self):
        if self.mode=='预演':
            raise ValueError('请从已纪实的时间点建立演练分支')
        if self.state:
            raise ValueError('请先结束并保存飞手模拟')
        baseline=deepcopy(self.actions)
        frame_time=self.cursor
        branch,actions=fork_at(self.scenario,baseline,frame_time)
        original=self.scenario['name']
        self.scenario=branch
        self.now=self.cursor=frame_time
        self.following=True
        self.clock_running=False
        self._write(actions,'创建演练分支',{'origin':original,'second':frame_time})
        self.comparison=dict(original=original,baseline=baseline,branch=branch['name'])
        self.rebuild()
        self.changed.emit()

    def outlook_filings(self):
        if self.future_scope=='本机报备':
            return [f for f in self.context.civic.data['filings']
                    if f['snapshot']['city']==self.scenario['city'] and not f['snapshot']['query'].get('demo')]
        rows=[]
        for entry in self.scenario.get('filings',()):
            route=FlightRoute.parse(entry['route'])
            query={'points':[],'form':'route','radius_m':20,'start':route.start.isoformat(),
                   'end':route.end.isoformat(),'altitude_agl':0,'category':route.category,'route':route.record()}
            rows.append(dict(id=entry['track'],state='已报备',snapshot=dict(
                city=self.scenario['city'],title=entry['track']+' · 演练报备',query=query)))
        return rows

    def estimate(self,scope):
        if self.state and scope=='本机报备':
            raise ValueError('请先结束当前模拟飞行')
        previous=self.future_scope
        self.future_scope=scope
        self.outlook=()
        now=self.frames.origin+timedelta(seconds=self.now)
        if scope=='本机报备':
            if previous=='演练' and self.scenario and self.scenario['tracks']:
                self.save()
            from datetime import datetime
            now=system_now(CHINA)
            self.scenario=_empty('本机报备预演',origin=now,city=self.context.public_city)
            self.now=self.cursor=0
            self.rebuild()
        rows=self.outlook_filings()
        if scope=='演练':
            zones=self.frames.zones
        else:
            from ...zone_catalog import Zone
            city=self.context.civic.book(self.scenario['city'])
            zones=tuple(Zone(rule.id,rule.name,'notice',geometry,rule.start,rule.end,
                             rule.source,True,True)
                        for rule,geometry in zip(city.rules,city.shapes)
                        if rule.kind=='restriction' and rule.verified and not rule.demo)
        self.outlook_zones=zones
        roster=deepcopy(self.data['roster'])
        token=(self.key(),scope)

        def done(hints):
            if token==(self.key(),self.future_scope):
                self.outlook=hints
                self.changed.emit()

        def without_weather():
            done(review(now,rows,zones,lambda *_:None,roster))

        if scope=='演练' or not self.context.weather_lookup.configured():
            without_weather()
            return

        def collect():
            return review(now,rows,zones,lambda _,point:self.context.weather_lookup.forecast(point),roster)

        self.context.run_aerial_lookup(collect,done,without_weather)

    def record_ahead_action(self,hint,kind):
        if kind not in ('提前联络','预置观察岗'):
            raise ValueError('请选择提前安排')
        target='未来:'+(hint.plans[0] if hint.plans else hint.place)
        seconds=max(0,round((hint.at-self.frames.origin).total_seconds()))
        details=dict(place=hint.place,title=kind,result='本机已登记',when=hint.at.isoformat())
        if kind=='预置观察岗':
            city=self.context.civic.cities().get(self.scenario['city'],{})
            posts=city.get('posts',())
            if not posts:
                raise ValueError('请在管理中设置观察岗')
            location,post=observe_point(posts)
            details.update(place=post,point=list(location))
        self._action_at(target,kind,seconds,**details)
        self.changed.emit()

    def publish_note(self, hint):
        if hint.kind!='热点':
            raise ValueError('请选择区域热点提示')
        zone=next((zone for zone in self.outlook_zones if zone.name==hint.place),None)
        if not zone:
            raise ValueError('该提示缺少可核对的区域边界')
        center=zone.geometry.centroid
        row=dict(city=self.scenario['city'],time=hint.at.isoformat(),place=hint.place,point=[center.x,center.y],
                 amount=len(hint.plans),demo=self.future_scope=='演练')
        if row not in self.data['public_notes']:
            self.data['public_notes'].append(row)
            self._store('保存本机区域提示',row)

    def report(self):
        summary=summarize(self.actions)
        closed='\n'.join(f'- {name} {num} 起' for name,num in sorted(summary['counts'].items())) or '- 暂无结案'
        pending='、'.join(summary['pending']) or '无'
        future='\n'.join(f'- {h.at:%m-%d %H:%M} {h.place} · {h.reason}' for h in self.outlook[:10]) or '- 暂无提示'
        average=f"{summary['average']:g} 秒" if summary['average'] is not None else '暂无'
        mode = ' · 演示' if self.context.clock.demo else ''
        _,events=self.agent_snapshot(self.now)
        decisions='\n'.join(f'- +{r["at"]}s {r["target"]} · {r["trigger"]} → {r["title"]} · {r["result"]}'
                            for r in self.agent_log if not r['target'].startswith('PUB-')) or '- 暂无决策'
        timeline='\n'.join(f'- +{e["at"]}s {e["target"]} · {e["title"]}'
                           for e in events if not e['target'].startswith('PUB-')) or '- 暂无事件'
        return (f"# 低空守望交接班{mode}\n\n演练：{self.scenario['name']}\n\n## 处置\n\n"
                f"事件 {summary['events']} 起，平均用时 {average}。\n\n{closed}\n\n## 未结\n\n{pending}\n"
                f"\n## 未来提示\n\n{future}\n\n## 智能体决策\n\n{decisions}\n\n## 重要事件\n\n{timeline}\n")

    def _reset_agent(self):
        if self._sync_city:
            self.context.civic.set_exercise_rules(self._sync_city, ())
        self._sync_city=None
        self._sync_anchor=None
        self.sync_public=self.overlay_public=False
        self._injection=None
        self._public_actions=[]
        self._agent_history.clear()
        self._agent_events.clear()
        self.autopilot_mode='assist'

    def load(self, text):
        from ...training_tracks import decode_scenario
        from ...training_city_cases import CIVIC_CASES
        from ...training_tracks import example
        scene=decode_scenario(text)
        mode = self.autopilot_mode
        if scene.get('civic_replay'):
            if scene['name'] not in CIVIC_CASES or scene['city'] != CIVIC_CASES[scene['name']]:
                raise ValueError('请选择三个精选城市场景的演练文件')
            text = example(scene['name'])
            scene = decode_scenario(text)
        self._reset_agent()
        self.autopilot_mode='auto' if scene.get('flagship') else 'assist'
        self.civic_replay = None
        if scene.get('civic_replay'):
            self.autopilot_mode = mode if mode in ('auto', 'assist') else 'auto'
            self.civic_replay = CivicReplay(scene, self.autopilot_mode)
        super().load(text)

    def seek_civic(self, second):
        self.now = self.cursor = max(0, min(self.playback_end, second))
        self.following = True
        self.changed.emit()

    def reset_civic(self):
        self.civic_replay = CivicReplay(self.scenario, self.autopilot_mode)
        self.selected = ''
        self.clock_running = False
        self.seek_civic(0)

    def civic_decide(self, target, choice):
        replay = self.civic_replay
        frame = replay.frame(self.cursor)
        if choice in ('accept', 'decline') and target not in frame['pending']:
            return
        replay.command(self.cursor, target, choice)
        self.changed.emit()

    def set_public_overlay(self, enabled):
        self.overlay_public=enabled
        if not enabled:
            self._injection=None
            self._public_actions=[]
        self.rebuild()
        self.changed.emit()

    def _overlay_tracks(self):
        from ...citizen_twin import overlay_tracks
        for row, route in overlay_tracks(self.scenario, self.frames, self.context.civic.data['plans'],
                                         self.playback_end, self._injection):
            self.frames.tracks[row['id']]=(row,tuple(p['t'] for p in row['points']),route)

    def inject_public_deviation(self):
        if 'PUB-01' not in self.frames.tracks or self.mode!='实时':
            raise ValueError('请叠加本城公众计划并返回实时演练')
        self._injection=self.now
        self.rebuild()
        self.changed.emit()

    @property
    def playback_end(self):
        if self.civic_replay:
            return self.scenario['duration']
        end=super().playback_end
        return max(end,self._injection+75) if self._injection is not None else end

    def set_public_sync(self, enabled):
        if enabled and not self.sync_public:
            self._sync_anchor=system_now(CHINA)-timedelta(seconds=self.now)
        self.sync_public=enabled
        self._sync_rules()
        self.changed.emit()

    def _sync_rules(self):
        from ...citizen_twin import activity_rules
        city=self.scenario['city'] if self.sync_public else None
        if self._sync_city and self._sync_city!=city:
            self.context.civic.set_exercise_rules(self._sync_city, ())
        self._sync_city=city
        if self._sync_city:
            self.context.civic.set_exercise_rules(self._sync_city,activity_rules(self.frames,self.now,self._sync_anchor))

    def agent_posts(self):
        posts=self.scenario.get('posts') or self.context.civic.cities().get(self.scenario['city'],{}).get('posts')
        lon, lat=self.scenario['station']
        return [p for p in (posts or [dict(id='station',name='演练观察岗',lon=lon,lat=lat)])
                if p.get('enabled',True)]

    def agent_staff(self, all_available=False):
        at=self.cursor
        now=self.frames.origin+timedelta(seconds=at)
        roster=self.data['roster']
        on={r['person'] for r in roster if instant(r['start'])<=now<instant(r['end'])}
        busy={r.get('person_id') for r in self.actions if r.get('walk') and r['at']<=at
              and (r['state']=='进行中' or r.get('finished_at',-1)>at)}
        available=[p for p in self.context.civic.data['staff'] if p['available']
                   and (not roster or p['id'] in on)]
        if self.scenario.get('staff_limit')==1:
            available=available[:1]
        return [p for p in available if all_available or p['id'] not in busy]

    def agent_snapshot(self, second=None):
        future = second is None and self.cursor > self.now
        end=min(self.cursor,self.now) if second is None else second
        if end<0:
            return [],[]
        if not (self.scenario.get('flagship') or self.overlay_public or self.autopilot_mode=='auto'):
            return [],[]
        start=max((k for k in self._agent_history if k<=end),default=-1)
        if start<end:
            # Skip empty timeline spans before first observed sample.
            earliest=min((ts[0] for _,ts,_ in self.frames.tracks.values() if ts),default=end)
            for at in range(max(start+1,earliest),end+1):
                rows=self.frames.frame(at)
                zones=self.frames.zones_at(at)
                groups=agent.detect_formations(rows,zones)
                path={o['id']:[self.frames._cache[s][o['id']] for s in range(max(0,at-13),at+1)
                               if o['id'] in self.frames._cache.get(s,{})] for o in rows}
                _, previous=self._agent_history.get(at-1,([],()))
                events=agent.detect_events(at,rows,previous,self.agent_zones(at),groups,
                                          self._agent_history.get(at-1,([],()))[0],path,self._agent_events[-80:])
                self._agent_events.extend(events)
                self._agent_history[at]=(groups,rows)
        return ([] if future else self._agent_history.get(end,([],()))[0],
                [e for e in self._agent_events if e['at']<=end])

    def agent_zones(self, second):
        current={z.id:grade for z,grade in self.frames.zones_at(second)}
        return [(z,current.get(z.id,'未生效')) for z in self.frames.zones]

    def agent_plans(self,obj, *, group=False, staff=None, starts=None):
        detail=agent.analyse_target(obj,self.agent_zones(self.cursor))
        groups,_=self.agent_snapshot()
        group=group or any(g['coordinated'] and obj['id'] in g['members'] for g in groups)
        posts=self.agent_posts()
        bases={p['id']:(p['lon'],p['lat']) for p in posts}
        people=self.agent_staff() if staff is None else staff
        exercise=bool(self.scenario.get('flagship'))
        positions={p['id']:tuple(self.scenario['station']) if exercise else
                   bases.get(p.get('post_id'),tuple(self.scenario['station']))
                   for p in self.agent_staff(all_available=True)}
        for row in self.actions:
            if row.get('walk') and row.get('finished_at',self.cursor+1)<=self.cursor and row['state']=='已完成':
                positions[row['person_id']]=row['walk']['finish']
        positions.update(starts or {})
        plans=agent.generate_agent_plans(obj,posts,people,detail['recovery_zone'],group,starts=positions,exercise=exercise)
        return detail,plans

    @property
    def agent_log(self):
        return [r['decision'] for r in self.actions if r.get('decision')]

    def agent_links(self):
        objects={o['id']:o for o in self.objects()}
        links=[]
        for row in self.actions:
            obj=objects.get(row['target'])
            age=self.cursor-row['at']
            if not obj or age<0 or row['state']=='已取消' and row.get('finished_at',self.cursor+1)<=self.cursor:
                continue
            kind=row['kind']
            walk=row.get('walk')
            if walk and row.get('finished_at',self.cursor+1)+12>self.cursor:
                origin=movement(row,self.cursor)
                label='核验中' if row.get('finished_at',self.cursor+1)>self.cursor else '已核验'
            elif kind=='recover' and row.get('recovery') and age<=75 and obj['state']!='已降落':
                origin=row['recovery']['point']
                label='引导回收'
            elif kind=='nudge' and age<=25:
                origin=self.scenario['station']
                label='自动提醒' if row['source']=='智能体自动' else '提醒'
            elif kind=='notify' and row['state']=='进行中' and obj['attention'].level>=3:
                origin=self.scenario['station']
                label='区域协同'
            else:
                continue
            if origin:
                links.append(dict(start=origin,end=agent.coordinates(obj),label=label,target=obj['id'],
                                  walking=label=='核验中',post_id=row.get('post_id')))
        return links

    def ask_agent(self, question):
        groups,events=self.agent_snapshot()
        return agent.answer_situation_query(question,self.objects(),groups,events,self.frames.zones_at(self.cursor),
                                            self.agent_plans,self.agent_staff())

    def adopt_agent_plan(self, code, obj=None, *, automatic=False, trigger='值班员采纳', redirect=None, plan=None):
        obj=obj or self.selected_object()
        if self.mode!='实时' or not obj or self.is_closed(obj['id']):
            raise ValueError('请选择未结案的实时演练对象')
        if plan is None:
            _,plans=self.agent_plans(obj)
            plan=next((p for p in plans if p['code']==code),None)
        if not plan:
            raise ValueError('请选择处置方案')
        target=obj['id']
        existing=self.actions
        updated=deepcopy(existing)
        source='智能体自动' if automatic else '值班员采纳'
        executed=[]
        if redirect and plan['assignment']:
            task=next(r for r in updated if r['id']==redirect['id'])
            task.update(state='已取消',result=f'改派至 {target}',finished_at=self.now)
            executed.append(f'{task["person"]} 从 {task["target"]} 改派至 {target}')
        labels=dict(nudge='飞手纠偏提醒',geofence='电子围栏模拟警示',watch='持续观察',
                    dispatch='观察岗核验',notify='区域协同通告',recover='安全回收引导',handoff='准备移交')
        for kind in plan['action_kinds']:
            if kind=='dispatch' and not plan['assignment'] or kind=='recover' and not plan['recovery_zone']:
                continue
            if any(r['target']==target and r['kind']==kind and r['state']!='已取消' for r in updated):
                continue
            details=plan['assignment'] if kind=='dispatch' else {}
            recovery={'recovery':plan['recovery_zone']} if kind=='recover' else {}
            updated,row=apply(updated,target,kind,self.now,source=source,result='已登记演练动作' if kind!='dispatch' else '',
                              title=labels[kind],public_twin=obj.get('public_plan',False),
                              **(dict(plan_id=obj['plan_id']) | details | recovery))
            executed.append(labels[kind]+(f' · {row["person"]} → {row["place"]}' if kind=='dispatch' else ''))
        if not executed:
            return False
        title=f'改派至 {target}' if redirect else f'{code} · {plan["name"]}'
        decision=dict(at=self.now,kind='agent',target=target,title=title, trigger=trigger,
                      detail='；'.join(executed)+('；等待空闲观察员' if code!='A' and not plan['assignment'] else ''),
                      result='；'.join(executed), point=agent.coordinates(obj),automatic=automatic)
        updated,_=apply(updated,target,'agent_decision',self.now,source=source,title=decision['title'],
                        decision=decision,public_twin=obj.get('public_plan',False))
        self._write(updated,'演练方案采纳',{'target':target,'code':code,'source':source})
        self.invalidate_after(self.now)
        if not automatic:
            self.changed.emit()
        return True

    def agent_step(self):
        groups,events=self.agent_snapshot()
        current={o['id']:o for o in self.objects()}
        previous={o['id']:o for o in self.frames.frame(self.now-1)}
        for row in tuple(self.actions):
            obj=current.get(row['target'])
            if not obj:
                continue
            if row.get('walk') and row['state']=='进行中' and self.now>=row['at']+row['walk']['seconds']:
                self.finish_action(row['id'],'演练观察岗已到位并完成核验')
                self._record_agent_result(obj,'观察岗核验完成',f'{row["person"]} · {row["place"]}')
            previous_facts=previous.get(obj['id'],{}).get('facts',{})
            if row['kind']=='nudge' and obj['facts']['in_plan'] and previous_facts.get('outside'):
                self._record_agent_result(obj,'飞手已回到航线','纠偏完成 · 继续观察')
        if self.autopilot_mode=='auto':
            for target,obj in current.items():
                old=previous.get(target)
                if not old or not any(r['target']==target and r.get('decision') for r in self.actions):
                    continue
                if obj['state']=='已降落' and old['state']!='已降落' and not self.is_closed(target):
                    self._action_at(target,'close',self.now,policy='智能体自动',title='演练闭环',
                                    plan_id=obj['plan_id'],result='模拟目标已降落')
                    self._record_agent_result(obj,'模拟目标已降落','登记演练闭环')
                elif old['grade'] in ('S1','S2') and obj['grade'] not in ('S1','S2') and (
                        not obj['facts'].get('verify_on_entry') or any(
                            r['target']==target and r['kind']=='notify' for r in self.actions)):
                    self._record_agent_result(obj,'驶离敏感区','区域风险解除 · 持续观察')
        if self.autopilot_mode=='observe':
            return
        for obj in current.values():
            if obj['state']=='已降落' or self.is_closed(obj['id']):
                continue
            level=obj['attention'].level
            group=any(g['coordinated'] and obj['id'] in g['members'] and g['level']>=3 for g in groups)
            recovery=obj['facts'].get('recovery_needed')
            if level<2 and not recovery:
                continue
            if self.autopilot_mode=='assist' and (level>=3 or group):
                continue
            prior=[r for r in self.actions if r['target']==obj['id']]
            event=next((e['title'] for e in reversed(events) if e['at']==self.now and e['target']==obj['id']),
                       f'{level} 级 · {obj["area"]}')
            if obj['facts'].get('timed_diversion'):
                self.adopt_agent_plan('A',obj,automatic=True,trigger=event)
                continue
            if level==2 and not group and not recovery or obj['facts'].get('verify_on_entry') and level<4:
                if obj['facts']['outside'] or obj['facts']['entry']:
                    self.adopt_agent_plan('A',obj,automatic=True,trigger=event)
                continue
            dispatched=any(r['kind']=='dispatch' and r['state']!='已取消' for r in prior)
            if dispatched:
                if (level!=4 and not obj['facts'].get('recovery_needed')) or any(r['kind']=='notify' for r in prior):
                    continue
            _,plans=self.agent_plans(obj,group=group)
            plan=next(p for p in plans if p['recommended'])
            redirect=None
            if not dispatched and not plan['assignment'] and (not recovery or level==4):
                staff={p['id']:p for p in self.agent_staff(all_available=True)}
                lower=[r for r in self.actions if r.get('person_id') in staff and r.get('walk')
                       and r['state']=='进行中' and r['target'] in current
                       and current[r['target']]['attention'].level<level]
                redirect=min(lower,key=lambda r:(current[r['target']]['attention'].level,r['at'],r['id']),default=None)
                if redirect:
                    person=staff[redirect['person_id']]
                    _,plans=self.agent_plans(obj,group=group,staff=[person],
                                             starts={person['id']:movement(redirect,self.now)})
                    plan=next(p for p in plans if p['recommended'])
            self.adopt_agent_plan(plan['code'],obj,automatic=True,trigger=event,redirect=redirect,plan=plan)

    def _record_agent_result(self,obj,title,result):
        target=obj['id']
        if any(r['target']==target and r['title']==title for r in self.agent_log):
            return
        decision=dict(at=self.now,kind='agent',target=target,title=title,trigger=title,detail=result,
                      result=result,point=agent.coordinates(obj),automatic=True)
        self._action_at(target,'agent_decision',self.now,policy='智能体自动',title=title,decision=decision)
