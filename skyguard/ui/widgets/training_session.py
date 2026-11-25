"""Own a local training session independently of page navigation."""
from copy import deepcopy
from datetime import timedelta
import json

from PySide6.QtCore import QObject, QTimer, Signal

from ...aerial import CHINA
from ...deviation import assess_deviation
from ...flight_route import FlightRoute, display_route, tracking
from ...flight_rules import drawing, instant
from ...infrastructure import SQLiteRepository
from ...sim_flight import advance, measurement, new_flight
from ...system_clock import now as system_now
from ...training_tracks import BEGIN, HOME, _empty, decode_scenario, example, TrainingFrames
from ...zone_catalog import read_table, zone_hits


class TrainingSession(QObject):
    changed = Signal()
    ticked = Signal()

    def __init__(self, context):
        super().__init__(context)
        self.context=context
        self.repo=SQLiteRepository(context.data_root/'training.sqlite3')
        self.data=self.repo.load() or {'version':1,'library':{},'types':None,'rules':None,'zones':{}}
        self.scenario=None
        self.frames=None
        self.now=self.cursor=0
        self.state=None
        self.pilot_route=None
        self.track=None
        self.target=None
        self.control='手动'
        self.sim_speed=1
        self.play_speed=1
        self.clock_running=False
        self.paused=False
        self.following=True
        self.selected=''
        self.wind=None
        self.geometry_edit=None
        self.clock=QTimer(self)
        self.clock.setInterval(1000)
        self.clock.timeout.connect(self.tick)
        self._system_at = system_now()
        self.clock.start()

    def _store(self, action, detail):
        self.repo.commit(deepcopy(self.data),action,detail)

    def update_types(self, values):
        self.data['types']=values
        self._store('更新区域类型', {'types':len(values or ())})

    def load(self, text):
        self.close_flight()
        self.frames=None
        self.scenario=decode_scenario(text)
        self.now=0
        self.cursor=0
        self._system_at=system_now()
        self.following=True
        self.clock_running=False
        self.rebuild()
        rows=self.objects()
        self.selected=next((o['id'] for o in rows if o['attention'].level>1),
                           next((o['id'] for o in rows if o['id']=='T04'),rows[0]['id'] if rows else ''))
        self.changed.emit()

    def load_example(self, name):
        self.load(example(name))

    def rebuild(self):
        source=deepcopy(self.scenario)
        city=source['city']
        corrected={z['id']:z for z in self.data['zones'].get(city,())}
        source['zones']=[corrected.pop(z['id'],z) for z in source['zones']]+list(corrected.values())
        previous=self.frames
        self.frames=TrainingFrames(source,types=self.data['types'],rules=self.data['rules'])
        if previous and previous.origin == self.frames.origin:
            self.frames.overrides.update(previous.overrides)

    def confirm_polygon(self, identifier, points):
        if len(set(points))<3:
            raise ValueError('请添加至少三个不同的边界点')
        from ...zone_catalog import zone_from_record
        city=self.scenario['city']
        original=next(r for r in self.scenario['zones'] if r['id']==identifier)
        row=dict(original, geometry={'type':'Polygon','coordinates':[list(points)+[points[0]]]},
                 confirmed=True,source='演练 · 已确认')
        zone_from_record(row,self.data['types'])
        self.scenario['zones']=[row if old['id']==identifier else old for old in self.scenario['zones']]
        self.data['zones'].setdefault(city,[])
        self.data['zones'][city]=[old for old in self.data['zones'][city] if old['id']!=identifier]+[row]
        self._store('校正演练区域范围',{'city':city,'id':identifier})
        self.geometry_edit=None
        self.rebuild()
        self.changed.emit()

    def save(self):
        text=self.export()
        self.data['library'][self.scenario['name']]=json.loads(text)
        self._store('保存演练情景', {'name':self.scenario['name']})
        return text

    def export(self):
        if not self.scenario:
            raise ValueError('请先选择演练情景')
        return json.dumps(self.scenario,ensure_ascii=False,indent=2)

    def start_flight(self, plan=None):
        if self.state and plan and self.track['plan_id']==plan['id']:
            self.changed.emit()
            return
        self.close_flight()
        if plan:
            route=FlightRoute.parse(plan['query']['route'])
            if not self.scenario or self.scenario['city']!=plan['city'] or instant(self.scenario['start'])!=route.start:
                self.scenario=_empty('公众模拟 · '+plan['title'], origin=route.start,city=plan['city'])
                self.scenario['zones']=[] if plan['city']!='changsha' else self.scenario['zones']
                self.now=self.cursor=0
        else:
            route=tracking(HOME,90,300,65,start=BEGIN,category='轻型')
            if not self.scenario:
                self.scenario=_empty('手绘航迹')
            route=tracking(tuple(self.scenario['station']),90,300,65,
                           start=instant(self.scenario['start'])+timedelta(seconds=self.now),
                           category='轻型')
        self.pilot_route=route
        self.state=new_flight(route)
        identifier=f"T{len(self.scenario['tracks'])+1:02d}"
        existing={t['id'] for t in self.scenario['tracks']}
        while identifier in existing:
            identifier+='A'
        self.track=dict(id=identifier,name=plan['title'] if plan else '手绘对象',
                        route=route.record() if plan else None,plan_id=plan['id'] if plan else '',
                        source='公众模拟飞行' if plan else '演练手绘',points=[])
        self.scenario['tracks'].append(self.track)
        if plan:
            self.scenario['filings'].append(dict(track=identifier,route=route.record()))
        self.track_shift=max(0, round((route.start-instant(self.scenario['start'])).total_seconds()))
        self.wind=None
        self.target=None
        self.control='手动'
        self.paused=False
        self.selected=identifier
        self.append_sample()
        self.following=True
        self.rebuild()
        self.changed.emit()
        if plan and self.context.weather_lookup.configured():
            captured=self.track
            def update_weather(forecast):
                if self.track is captured:
                    at=route.start.replace(minute=0,second=0,microsecond=0)
                    hour=next((hour for hour in forecast.hours if hour.time==at),None)
                    if hour and hour.wind is not None and hour.wind_from_deg is not None:
                        self.wind=((hour.wind_from_deg+180)%360,hour.wind)
            self.context.run_aerial_lookup(lambda: self.context.weather_lookup.forecast(route.takeoff), update_weather, lambda: None)

    def append_sample(self):
        row=measurement(self.state)
        row['t']+=self.track_shift
        self.track['points'].append(row)

    def close_flight(self):
        if self.state:
            self.save()
        self.state=self.track=self.pilot_route=None
        self.target=None

    def command(self, mode):
        if self.state:
            self.target=None
            self.control=mode
            self.changed.emit()

    def drag(self, lon, lat):
        if self.state and self.control!='失联' and self.state.mode!='已降落':
            height=self.target[2] if self.target else max(8,self.state.height)
            self.control='手动'
            self.target=(lon,lat,height)

    def altitude(self, delta):
        if self.state and self.control!='失联':
            lon,lat,height=self.target or (*self.state.position,self.state.height)
            self.control='手动'
            self.target=(lon,lat,max(0,min(500,height+delta)))

    @property
    def playback_end(self):
        return max((p['t'] for t in self.scenario['tracks'] for p in t['points']), default=0) if self.scenario else 0

    def tick(self):
        if not self.scenario:
            return
        changed=False
        at=system_now()
        elapsed=max(0,int((at-self._system_at).total_seconds()))
        if elapsed:
            self._system_at+=timedelta(seconds=elapsed)
        if self.state and not self.paused and self.state.mode!='已降落':
            for _ in range(min(1800, elapsed if self.context.clock.demo else self.sim_speed)):
                update=advance(self.state,self.pilot_route,target=self.target,mode=self.control,wind=self.wind)
                if update.second==self.state.second:
                    break
                self.state=update
                self.append_sample()
            self.now=max(self.now,self.track_shift+self.state.second)
            if self.state.mode=='已降落':
                self.save()
            previous=self.frames
            row,_,route=previous.tracks[self.track['id']]
            points=deepcopy(self.track['points'])
            row=dict(row,points=points)
            previous.tracks[self.track['id']]=(row,tuple(p['t'] for p in points),route)
            changed=True
        if self.clock_running and not self.state:
            limit=self.playback_end
            self.now=min(self.now+self.play_speed,limit)
            self.clock_running=self.now<limit
            changed=True
        if changed:
            if self.following:
                self.cursor=self.now
            self.ticked.emit()

    @property
    def mode(self):
        return '回看' if self.cursor<self.now else '预演' if self.cursor>self.now else '实时'

    def seek(self, second):
        self.cursor=second
        self.following=second==self.now
        self.changed.emit()

    def return_now(self):
        self.seek(self.now)

    def override(self, level, cause):
        if self.mode!='实时' or not self.selected or not cause.strip():
            raise ValueError('请返回实时状态并填写调整原因')
        self.frames.overrides[self.selected]=(self.now,level,cause)
        self.frames._cache={k:v for k,v in self.frames._cache.items() if k<self.now}
        self._store('人工调整关注等级', {'id':self.selected,'level':level,'reason':cause})
        self.changed.emit()

    def objects(self):
        return self.frames.frame(self.cursor) if self.frames and self.mode!='预演' else ()

    def solar_time(self):
        return self.frames.origin+timedelta(seconds=self.cursor)

    def map_layers(self, public=False):
        if not self.frames:
            return {}
        layers=[]
        at=self.now if public else self.cursor
        for zone,rating in self.frames.zones_at(at):
            layers.extend(dict(name=zone.name,id=zone.id,rings=r,kind='zone-s1' if rating=='S1' else 'zone',
                               highlight=rating=='S1') for r in drawing(zone.geometry))
        data=self.frames.planned(self.cursor) if not public and self.mode=='预演' else [
            dict(o,attention=None,level=o['attention'].level, selected=o['id']==self.selected,
                 state='失联' if o['quality']=='观测中断' else o['state'],
                 label=(f"{o['id']} · {o['attention'].level} 级" + (' · 最后观测' if o['age']>=8 else '') +
                        (f" · 高 {o['altitude']:.0f} m" if o['id']==self.selected else '')))
            for o in (self.frames.frame(self.now) if public else self.objects())]
        if self.mode=='预演' and not public:
            for current in self.frames.frame(self.now):
                if current['id']==self.selected and current['prediction']:
                    data.append(dict(current,attention=None,level=current['attention'].level,
                                     selected=True,state='预计',label='最后观察 · 预计',tail=[]))
        if self.geometry_edit and len(self.geometry_edit[1])>=3:
            points=self.geometry_edit[1]
            layers.append(dict(name='待确认范围',id='editing-zone',rings=[points+[points[0]]],
                               kind='zone',highlight=True))
        if public:
            data=[dict(o,label='我的模拟飞行',selected=True,public=True) for o in data
                  if self.track and o['id']==self.track['id']]
        routes=[]
        for identifier,(_,_,route) in self.frames.tracks.items():
            if route and (not public or self.track and self.track['id']==identifier):
                routes.append(dict(display_route(route),style='filing',id='sim:'+identifier))
        return dict(zones=layers,routes=routes,aircraft=data,flight_control=bool(self.state and (public or self.mode=='实时')))

    def stats(self):
        if not self.state:
            return '模拟飞行已结束'
        row=self.state
        dev=assess_deviation(self.pilot_route,self.track['points'],self.frames.origin)
        radius=f"{dev['horizontal_m']:.0f}" if dev else '—'
        values=f'高度 {row.height:.0f} m   速度 {row.speed:.1f} m/s   离航线 {radius} m'
        when=self.frames.origin+timedelta(seconds=self.now)
        zone=next(iter(zone_hits(row.position,when,self.frames.zones)),None)
        warning=f' · 留意{zone.name}' if zone else ''
        if row.height>120:
            warning=' · 高度超过计划参考范围'
        if self.control=='失联' and self.wind is None:
            warning+=' · 风况缺失，采用无风'
        return values+warning
