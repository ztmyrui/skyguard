
"""Ephemeral projections between public planning and the local exercise."""
from datetime import timedelta
from math import isfinite

from .flight_route import FlightRoute, ROUTE_LIMITS, destination, planned_position
from .flight_rules import drawing
from .situation_agent import separation


def overlay_tracks(scenario, frames, plans, end, injection):
    number = 0
    for plan in plans:
        if plan.get('city') != scenario['city'] or plan.get('cancelled'):
            continue
        record = plan.get('query',{}).get('route')
        if not record:
            continue
        try:
            route = FlightRoute.parse(record).shifted(frames.origin)
        except (TypeError, ValueError, KeyError):
            continue
        number += 1
        identifier=f'PUB-{number:02d}'
        if identifier in frames.tracks:
            continue
        deviation = injection if number==1 else None
        nearest = None
        if deviation is not None:
            base=planned_position(route,min(route.end-timedelta(milliseconds=1),
                                            frames.origin+timedelta(seconds=deviation)))
            sensitive=[z for z in frames.zones if z.kind in ('event','school','notice','venue')]
            positions=[tuple(z.geometry.centroid.coords[0]) for z in sensitive]
            if positions:
                nearest=min((separation(base.xy,p) for p in positions),key=lambda d:d['s12'])
        points=[]
        for second in range(end+1):
            at=frames.origin+timedelta(seconds=second)
            pos=planned_position(route,min(at,route.end-timedelta(milliseconds=1)))
            lon,lat=pos.xy
            height=pos.altitude_m
            mode='航线'
            if deviation is not None and 0 < (elapsed:=second-deviation) < 35:
                metres=max(140,3*ROUTE_LIMITS[route.category][2])
                ratio=min(1,elapsed/10,(35-elapsed)/10)
                lon,lat=destination((lon,lat),nearest['azi1'] if nearest else 0,metres*ratio)
                if elapsed>=25:
                    mode='返航'
            if not (isfinite(lon) and isfinite(lat)):
                break
            motion=separation((points[-1]['lon'],points[-1]['lat']),(lon,lat)) if points else None
            points.append(dict(t=second,lon=lon,lat=lat,height=height,mode=mode,
                               speed=motion['s12'] if motion else 0,heading=motion['azi1']%360 if motion else 0))
        if points:
            yield dict(id=identifier,name=plan['title'],route=route.record(),plan_id=plan['id'],
                       points=points,public=True,source='本机公众计划演练投影'),route
        if number>=2:
            return


def activity_rules(frames, second, anchor):
    fallback_end=frames.origin+timedelta(seconds=max(
        second+1, max((ts[-1] for _,ts,_ in frames.tracks.values() if ts),default=0)+1))
    shift=anchor-frames.origin
    return [dict(id=f'exercise:{zone.id}:{index}',name=f'{zone.name} · 态势联动演练',
                 kind='coordination',rings=rings,demo=True,verified=True,lower=0,upper=10000,
                 start=((zone.start or frames.origin)+shift).isoformat(),
                 end=((zone.end or fallback_end)+shift).isoformat())
            for zone,grade in frames.zones_at(second) if grade=='S1' and zone.kind in ('event','notice','venue')
            for index,rings in enumerate(drawing(zone.geometry))]
