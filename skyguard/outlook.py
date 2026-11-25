"""Three-day review from filed routes, schedules and supplied forecasts."""
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from itertools import combinations
from math import cos,radians,hypot

from .aerial import CHINA, route_center, touched_hours
from .attention_level import assess
from .flight_route import FlightRoute, planned_position
from .flight_rules import FlightQuery, instant


@dataclass(frozen=True)
class Hint:
    kind: str
    at: object
    place: str
    reason: str
    plans: tuple = ()
    level: int | None = None


def _separation(a,b):
    return 111195*hypot((a.lon-b.lon)*cos(radians((a.lat+b.lat)/2)),a.lat-b.lat)


def _overlap(route, zone, width, first, last):
    query=FlightQuery((),'route',width,route.start,route.end,0,route.category,route=route)
    lo=max(route.start,first)
    hi=min(route.end,last)
    if zone.start:
        lo=max(lo,zone.start)
    if zone.end:
        hi=min(hi,zone.end)
    if lo>=hi or not query.geometry().intersects(zone.geometry):
        return None
    probe=lo
    while probe<hi:
        point=planned_position(route,probe)
        if point and zone.contains(point.xy,probe):
            return probe
        probe+=timedelta(seconds=1)
    return None


def _weather(at, forecast):
    if forecast is None or not forecast.hours:
        return '暂无天气预报',False
    hour=at.replace(minute=0,second=0,microsecond=0)
    item=next((row for row in forecast.hours if row.time==hour),None)
    if not item:
        bounds=forecast.coverage
        return ('超出预报范围' if bounds and (at<bounds[0] or at>=bounds[1]) else '该时段天气待更新'),False
    if item.thunder:
        return '预报有雷暴',True
    max_wind=max(item.wind or 0,item.gust or 0,item.surface_gust_10m or 0)
    if max_wind>=8:
        return f'阵风或风速 {max_wind:g} 米/秒 · 关注风况',True
    if (item.rain_probability or 0)>=.65 or (item.rain_mm or 0)>0:
        return '关注降雨',True
    return item.condition or '已获取天气预报',False


def review(now, filings, zones, forecast_for, roster=(), *, hotspot=4):
    now=instant(now).astimezone(CHINA)
    last=now+timedelta(days=3)
    items=[]
    buckets=defaultdict(set)
    staffing=defaultdict(set)
    prepared=[]
    for record in filings:
        if record.get('state')=='已取消':
            continue
        snapshot=record['snapshot']
        q=snapshot['query']
        if not q.get('route'):
            continue
        route=FlightRoute.parse(q['route'])
        first,end=max(now,route.start),min(last,route.end)
        if first>=end:
            continue
        pid=record['id']
        title=snapshot['title']
        city=snapshot['city']
        query=FlightQuery(**q)
        forecast=forecast_for(pid,route_center(query))
        prepared.append((pid,city,title,route,query.geometry(),first,end))
        hits=[]
        for zone in zones:
            crossing=_overlap(route,zone,q['radius_m'],first,end)
            if crossing is None:
                continue
            grade=zone.rating(crossing)
            hits.append((zone,grade))
            for hour in touched_hours(crossing,end):
                buckets[(zone.name,hour)].add(pid)
            if zone.restricted or zone.kind=='school' and grade=='S1':
                message='计划经过本时段生效的限制' if zone.restricted else '计划经过学校高峰时段'
                items.append(Hint('关注',crossing,zone.name,message,(pid,)))
        rating=min((g for _,g in hits),default='')
        peak=', '.join(z.name for z,grade in hits if grade=='S1')
        levels=[]
        for hour in touched_hours(first,end):
            when=max(hour,first)
            weather, concern=_weather(when,forecast)
            if concern:
                items.append(Hint('天气',when,title,weather,(pid,)))
            att=assess(dict(rating=rating,high=q['altitude_agl']>120,night=when.hour>=19 or when.hour<6,
                           impact=1 if rating=='S1' else .4 if rating=='S2' else 0,
                           anomaly=.5 if concern else 0),0)
            levels.append((att.level,when,weather))
            if att.level>=2:
                staffing[hour].add(pid)
        level,when,weather=max(levels,key=lambda row:(row[0],-row[1].timestamp()))
        items.append(Hint('报备',when,title,' · '.join(filter(None,(peak,weather))), (pid,),level))
    for a,b in combinations(prepared,2):
        a_id,city,title,route,geom,first,end=a
        b_id,bcity,btitle,broute,bgeom,bfirst,bend=b
        first,end=max(first,bfirst),min(end,bend)
        if city!=bcity or first>=end or geom.distance(bgeom)>.0006:
            continue
        at=first
        while at<end:
            left,right=planned_position(route,at),planned_position(broute,at)
            if left and right and _separation(left,right)<50:
                items.append(Hint('冲突',at,title+' / '+btitle,'计划水平间距小于 50 米',(a_id,b_id)))
                break
            at+=timedelta(seconds=1)
    for (name,hour),ids in buckets.items():
        if len(ids)>=hotspot:
            items.append(Hint('热点',hour,name,f'{len(ids)} 条报备航线',tuple(sorted(ids))))
    for hour,ids in staffing.items():
        people={person['person'] for person in roster
                if instant(person['start'])<hour+timedelta(hours=1) and hour<instant(person['end'])}
        if len(people)<len(ids):
            reason=f'预计关注 {len(ids)} 条 · 在岗 {len(people)} 人' if roster else '未设置排班'
            items.append(Hint('排班',hour,'值班站点',reason,tuple(sorted(ids))))
    return tuple(sorted(items,key=lambda item:(item.at,item.kind,item.place)))
