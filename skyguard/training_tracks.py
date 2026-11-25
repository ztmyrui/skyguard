"""Scenarios, recorded flights and deterministic time-indexed training frames."""
from bisect import bisect_right
from dataclasses import asdict
from datetime import timedelta
from functools import lru_cache
from math import isfinite
import json

from shapely.geometry import Point
from shapely.strtree import STRtree

from .attention_level import assess
from .deviation import assess_deviation
from .flight_route import FlightRoute
from .flight_route import destination, planned_position, route_duration, tracking
from .flight_rules import DEMO_START, instant
from .sim_flight import advance, measurement, new_flight
from .track_predict import heading_for, predict
from .zone_catalog import read_table, zone_from_record
from .training_city_cases import CASES, CIVIC_CASES, EXTENDED_CASES, FLAGSHIPS, city_case, civic_case, extended_case
from .training_flagships import flagship_case


SCHEMA = 1
SOURCE = '演练'
BEGIN = DEMO_START.replace(hour=17,minute=30)
HOME = (112.946,28.174)
DAYS_SECONDS = 86400
EXAMPLES = ('合规飞行','风漂后修正','临时改航','商圈夜景','自动返航','失联漂移')


def demo_zones():
    return [
        dict(id='public-square', name='滨江商圈（演练）', kind='mall', point=destination(HOME,90,280), radius=150),
        dict(id='campus-demo', name='城南校园（演练）', kind='school', point=destination(HOME,25,400), radius=125),
        dict(id='green-demo', name='滨江空旷区（演练）', kind='open', point=destination(HOME,270,150), radius=170),
    ]


def _empty(name, *, origin=BEGIN, city='changsha'):
    return dict(version=SCHEMA, name=name, source=SOURCE, city=city,
                start=origin.isoformat(), station=list(HOME), tracks=[], filings=[], zones=demo_zones(), presets={})


@lru_cache(maxsize=16)
def example(name):
    if name in CIVIC_CASES:
        return json.dumps(civic_case(name, BEGIN, _empty), ensure_ascii=False)
    if name in FLAGSHIPS:
        return json.dumps(flagship_case(name, BEGIN, _empty), ensure_ascii=False)
    if name in EXTENDED_CASES:
        return json.dumps(extended_case(name, BEGIN, _empty), ensure_ascii=False)
    if name in CASES:
        return json.dumps(city_case(name, BEGIN, _empty),ensure_ascii=False)
    if name not in EXAMPLES:
        raise ValueError('请选择演练情景')
    scenario = _empty(name)
    route = tracking(HOME,90,400,70,start=BEGIN,category='轻型',speed=4)
    track = dict(id='T01', name=name, route=route.record() if name!='商圈夜景' else None,
                 plan_id='', source='情景预设', points=[])
    state = new_flight(route, endurance=125)
    track['points'].append(measurement(state))
    for i in range(1,190):
        target,mode,wind=None,'航线',None
        if name in ('风漂后修正','临时改航') and 30 <= i < 65:
            mode,target='手动',(*destination(HOME,40,340),80)
        if name == '商圈夜景':
            mode,target='手动',(*destination(HOME,90,330),70)
        if name == '自动返航' and i >= 40:
            mode='返航'
        if name == '失联漂移' and i >= 40:
            mode,wind='失联',(22,3)
        state=advance(state,route,target=target,mode=mode,wind=wind)
        if state.second == i:
            track['points'].append(measurement(state))
        if state.mode == '已降落':
            break
    scenario['tracks'].append(track)
    if track['route']:
        scenario['filings'].append(dict(track=track['id'],route=route.record()))
    scenario['presets'] = {track['id']: '现场已完成核验'}
    return json.dumps(scenario,ensure_ascii=False)


def decode_scenario(text):
    if len(text.encode('utf8')) > 8_000_000:
        raise ValueError('演练文件过大')
    data=json.loads(text)
    if data.get('version') != SCHEMA or data.get('source') != SOURCE:
        raise ValueError('请选择此版本的演练文件')
    instant(data['start'])
    lon,lat=data['station']
    if not _point(lon,lat):
        raise ValueError('演练站点坐标无效')
    identifiers=set()
    for track in data['tracks']:
        identifier=track['id']
        if not isinstance(identifier,str) or not identifier or identifier in identifiers:
            raise ValueError('演练航迹编号无效')
        identifiers.add(identifier)
        if track.get('route'):
            FlightRoute.parse(track['route'])
        previous=-1
        for p in track['points']:
            if type(p['t']) is not int or not previous < p['t'] < 345600 or not _point(p['lon'],p['lat']):
                raise ValueError('演练航迹的时间或位置无效')
            if not isfinite(p['height']) or not 0 <= p['height'] <= 10000:
                raise ValueError('演练航迹高度无效')
            previous=p['t']
    if len(data['tracks']) > 150 or len(data['zones']) > 100:
        raise ValueError('演练对象或区域数量过多')
    for row in data['zones']:
        zone_from_record(row)
    posts = data.get('posts', [])
    if len(posts) > 30 or len({p['id'] for p in posts}) != len(posts) or any(
            not p['id'] or not p['name'] or not _point(p['lon'], p['lat']) for p in posts):
        raise ValueError('演练观察岗配置无效')
    for filing in data.get('filings', ()):
        FlightRoute.parse(filing['route'])
    return data


def _point(lon,lat):
    return isinstance(lon,(int,float)) and isinstance(lat,(int,float)) and -180<=lon<=180 and -89<=lat<=89


class TrainingFrames:
    def __init__(self, scenario, *, types=None, rules=None):
        self.origin = instant(scenario['start'])
        defaults = read_table('zone-types.json')['types']
        self.types = defaults | (types or {})
        self.zones = tuple(zone_from_record(z,self.types) for z in scenario['zones'])
        self.zone_index = STRtree([zone.geometry for zone in self.zones])
        self._ratings = {}
        self.rules = rules
        self.enhanced = bool(scenario.get('flagship'))
        self.authored = bool(scenario.get('flagship_timeline'))
        self.tracks = {}
        routes = {f['track']: f['route'] for f in scenario.get('filings', ())}
        for row in scenario['tracks']:
            route = row.get('route') or routes.get(row['id'])
            self.tracks[row['id']] = (row, tuple(p['t'] for p in row['points']), FlightRoute.parse(route) if route else None)
        self._cache = {}
        self.overrides = {}

    def _frame_object(self, identifier, now):
        row, times, route = self.tracks[identifier]
        index = bisect_right(times,now)-1
        if index < 0 or not times or now > times[-1]+60:
            return None
        history=row['points'][max(0,index-59):index+1]
        sample=history[-1]
        when=self.origin+timedelta(seconds=now)
        point=(sample['lon'],sample['lat'])
        pos=Point(*point)
        ratings = self.zone_ratings(now,when)
        hits = [(self.zones[i],ratings[i]) for i in self.zone_index.query(pos,predicate='covered_by') if ratings[i]]
        age=now-times[index]
        forecast,entries=predict(history[-16:],when,self.zones,seconds=180 if self.enhanced else 300) if age<8 else ([],[])
        sensitive={zone.id for zone,grade in zip(self.zones,ratings) if grade=='S1'}
        current_regions={zone.id for zone,_ in hits}
        entry=next((entry for entry in entries if entry.zone in sensitive and entry.zone not in current_regions
                    and entry.seconds<=120),None)
        dev=assess_deviation(route,history,self.origin,approaches_sensitive=bool(entry))
        grades={'S1':0,'S2':1,'S3':2,'低影响':3}
        zone,grade=min(hits,key=lambda row:grades[row[1]]) if hits else (None,None)
        nearby = any(zone.geometry.distance(pos)<(.00115 if self.enhanced else .0018) for zone,rating in zip(self.zones,ratings)
                              if rating=='S1')
        quality = '观测中断' if age>=20 else '待补观测' if age>=8 else '正常'
        facts=dict(rating=grade,near_s1=nearby,unfiled=route is None, high=sample['height']>120,
                   outside=dev is not None and dev['level'] in ('轻微','明显','偏向敏感区'),
                   in_plan=dev is not None and dev['level']=='容差内',
                   night=when.hour>=19 or when.hour<6,entry=bool(entry),
                   restricted=any(z.restricted for z,_ in hits),insufficient=age>=8 or not self.zones,
                   paused_area=sample.get('speed',0)<.5 and sample['height']>10 and len(history)>=12
                   and all(p.get('speed',0)<.5 for p in history[-12:]),
                   urgency=1-entry.seconds/120 if entry else .1,impact=1 if grade=='S1' else .45 if grade=='S2' else 0,
                   gap=1 if not route else 0,anomaly=.75 if dev and dev['level']=='明显' else 0)
        feedback=getattr(self,'feedback',None)
        if feedback:
            facts.update(feedback(identifier,now))
        if self.enhanced:
            facts['gap'] = max(facts['gap'], min(1, age / 20))
            if row.get('public') and facts['in_plan'] and not facts['restricted']:
                for flag in ('near_s1', 'entry', 'night'):
                    facts[flag] = False
        if self.authored:
            # Nearness informs the forecast. The exercise distinguishes approach from entry.
            facts['near_s1'] = False
            facts['recovery_needed'] = sample.get('mode') in ('失联','低电','回收引导')
            facts['unreachable'] = sample.get('mode') == '失联'
            facts['verify_on_entry'] = row.get('verify_on_entry',False)
            facts['timed_diversion'] = bool(row.get('timed_diversion') and grade=='S1' and not facts['restricted'])
            facts['occlusion'] = row.get('occlusion',False)
            if facts['recovery_needed'] and not grade:
                facts['rating'] = 'S2'
            if facts['occlusion'] and age:
                facts['insufficient'] = True
            if age >= 8 or facts['occlusion'] and age:
                quality = '观测中断'
            if row.get('background') and facts['in_plan']:
                facts.update(entry=False,night=False)
            if sample['mode']=='已降落' or row.get('correction') and facts['in_plan'] and age==0:
                # A positive measurement can verify the exercise outcome.
                if sample['mode']=='已降落' or any(p.get('mode')=='偏航' for p in history):
                    facts['exercise_resolved'] = not facts['restricted'] and grade!='S1'
            if row.get('timed_diversion') and facts['in_plan'] and age==0 and now>10 and grade!='S1':
                facts['exercise_resolved'] = True
            facts['verified_exit'] = (
                (row.get('verified_exit') or facts['verify_on_entry']) and facts.get('observer_verified')
                and age==0 and grade not in ('S1','S2') and not facts['entry'])
        old=self._cache.get(now-1,{}).get(identifier)
        adjustment=self.overrides.get(identifier)
        attention=assess(facts,now,old['attention'] if old else None,rules=self.rules,
                         override=adjustment[1:] if adjustment and now>=adjustment[0] else None)
        return dict(id=identifier,lon=point[0],lat=point[1],altitude=sample['height'],heading=heading_for(history),
                    label=identifier,state='失联' if self.authored and age>=8 else sample.get('mode','手动'),tail=[[p['lon'],p['lat']] for p in history],
                    area=zone.name if zone else '普通区域',grade=grade,attention=attention,dev=dev,
                    region_weight={'S1':3,'S2':2,'S3':1,'低影响':1}.get(grade,1),
                    region_confidence=zone.confidence if zone else '',
                    age=age,quality=quality,
                    prediction=([point,*forecast[::29],forecast[-1]] if self.enhanced
                                else [point,*forecast[::29]]) if forecast else [],
                    prediction_seconds=len(forecast),entry=asdict(entry) if entry else None,
                    plan_id=row.get('plan_id',''),filed=route is not None, public_plan=bool(row.get('public')),
                    speed=sample.get('speed',0), facts=facts, regions=tuple(z.id for z,_ in hits),
                    forecast_entries=tuple(asdict(e) for e in entries))

    def frame(self, now):
        now=int(now)
        if now not in self._cache:
            first=min((times[0] for _,times,_ in self.tracks.values() if times),default=0)
            last=max((times[-1] for _,times,_ in self.tracks.values() if times),default=-1)+60
            if not first<=now<=last:
                return ()
            start=max((key for key in self._cache if key<now),default=first-1)
            for sec in range(start+1,now+1):
                self._cache[sec]={obj['id']:obj for identifier in self.tracks
                                  if (obj:=self._frame_object(identifier,sec))}
        return tuple(sorted(self._cache[now].values(),
                            key=lambda o:(-o['attention'].level,-o['attention'].score,o['id'])))

    def planned(self, now):
        when=self.origin+timedelta(seconds=now)
        result=[]
        from .track_predict import heading_for
        for identifier,(_,_,route) in self.tracks.items():
            point=planned_position(route,when) if route else None
            if point:
                result.append(dict(id=identifier,lon=point.lon,lat=point.lat,altitude=point.altitude_m,
                                   heading=0,label=identifier+' · 预计',state='预计',level=1,tail=[]))
        return result

    def zone_ratings(self, second, when):
        minute = second//60
        # Explicit activity start/end can fall inside a minute.
        key = (minute,tuple(z.start is None or z.start<=when for z in self.zones),
               tuple(z.end is None or when<z.end for z in self.zones))
        if key not in self._ratings:
            self._ratings[key]=tuple(zone.rating(when,self.types) for zone in self.zones)
        return self._ratings[key]

    def zones_at(self, second):
        when=self.origin+timedelta(seconds=second)
        return tuple((zone,grade) for zone,grade in zip(self.zones,self.zone_ratings(second,when)) if grade)
