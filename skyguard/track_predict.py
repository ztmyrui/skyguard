"""Repeatable short-horizon track forecast for recorded civilian exercises."""
from datetime import timedelta
from dataclasses import dataclass
from math import atan2, degrees, hypot

from shapely.geometry import LineString

from .aerial import local_metres


@dataclass(frozen=True)
class Entry:
    zone: str
    name: str
    seconds: int
    point: tuple[float,float]


def _velocity(points, project):
    pts = [(p['t'], *project(p['lon'],p['lat'])) for p in points]
    if len(pts) < 2 or pts[-1][0] <= pts[0][0]:
        return 0.,0.
    dt = pts[-1][0]-pts[0][0]
    return (pts[-1][1]-pts[0][1])/dt, (pts[-1][2]-pts[0][2])/dt


def predict(history, when, zones=(), *, seconds=300):
    if not history:
        return (), ()
    last = history[-1]
    center = (last['lon'], last['lat'])
    project, unproject = local_metres(center)
    east, north = _velocity([p for p in history if p['t'] >= last['t']-15], project)
    if hypot(east,north) < .5:
        return (), ()
    points = tuple(unproject(east*t,north*t) for t in range(1,min(300,int(seconds))+1))
    corridor = LineString((center,points[-1])) if points else None
    zones=tuple(z for z in zones if corridor is not None and z.geometry.intersects(corridor))
    if not zones:
        return points, ()
    entries = {}
    for i, pos in enumerate(points, 1):
        at = when + timedelta(seconds=i)
        for zone in zones:
            if zone.id not in entries and zone.contains(pos, at):
                entries[zone.id] = Entry(zone.id, zone.name, i, pos)
        if len(entries)==len(zones):
            break
    return points, tuple(entries.values())


def heading_for(history):
    if len(history) < 2:
        return history[-1].get('heading',0) if history else 0
    a,b = history[-2:]
    project, _ = local_metres((a['lon'],a['lat']))
    x,y=project(b['lon'],b['lat'])
    return degrees(atan2(x,y))%360 if hypot(x,y)>.01 else b.get('heading',a.get('heading',0))
