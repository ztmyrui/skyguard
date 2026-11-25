"""Deterministic movement for recorded, local civilian training flights."""
from dataclasses import dataclass, replace
from datetime import timedelta
from math import atan2, degrees, hypot, isfinite

from .flight_route import FlightRoute, ROUTE_LIMITS, WGS84, destination, planned_position


@dataclass(frozen=True)
class Aircraft:
    lon: float
    lat: float
    height: float = 0.0
    heading: float = 0.0
    speed: float = 0.0
    second: int = 0
    mode: str = '手动'
    battery: int = 1200
    route_second: int = 0

    @property
    def position(self):
        return self.lon, self.lat


def new_flight(route, endurance=1200):
    return Aircraft(*route.takeoff, battery=endurance)


def measurement(state):
    return dict(t=state.second, lon=state.lon, lat=state.lat, height=state.height,
                heading=state.heading, speed=state.speed, mode=state.mode, battery=state.battery)


def _approach(state, target, horizontal, climb):
    lon, lat, height = target
    inv = WGS84.Inverse(state.lat, state.lon, lat, lon)
    step = max(-climb, min(climb, height-state.height))
    distance = min(inv['s12'], max(0, horizontal**2-step**2)**.5)
    if inv['s12'] <= distance:
        point = (lon,lat)
    else:
        point = destination(state.position, inv['azi1'], distance)
    return replace(state, lon=point[0], lat=point[1], height=max(0, state.height+step),
                   heading=inv['azi1'] % 360 if distance > .01 else state.heading,
                   speed=hypot(distance, step))


def advance(state: Aircraft, route: FlightRoute, *, target=None, mode=None, wind=None, return_height=60):
    """Advance one simulated second. Wind is a direction toward and m/s pair."""
    mode = mode or state.mode
    if state.mode == '失联':
        mode = '失联'
    if state.mode == '已降落':
        return state
    if mode not in ('手动','航线','返航','失联'):
        raise ValueError('未知模拟状态')
    maximum = ROUTE_LIMITS[route.category][0]
    battery = max(0, state.battery-1)
    route_second = state.route_second
    if mode == '失联':
        direction, speed = wind or (0, 0)
        speed = min(50, max(0, speed))
        lon, lat = destination(state.position, direction, speed)
        height = max(0, state.height-1.5) if not battery else state.height
        result = replace(state, lon=lon, lat=lat, height=height, speed=speed,
                         heading=direction % 360 if speed else state.heading)
        if height == 0 and not battery:
            mode = '已降落'
    elif mode == '返航':
        if state.position != route.takeoff and state.height < return_height:
            goal = (*state.position, return_height)
        elif state.position != route.takeoff:
            goal = (*route.takeoff, state.height)
        else:
            goal = (*route.takeoff, 0)
        result = _approach(state, goal, maximum, 3)
        if result.position == route.takeoff and result.height == 0:
            mode = '已降落'
    elif mode == '航线':
        route_second = state.route_second + 1
        at = min(route.start + timedelta(seconds=route_second), route.end - timedelta(microseconds=1))
        waypoint = planned_position(route, at)
        result = _approach(state, (*waypoint.xy, waypoint.altitude_m), maximum, 3)
    else:
        if target is None or len(target) != 3 or not all(isfinite(x) for x in target):
            target = (*state.position, state.height)
        lon, lat, height = target
        if not -180 <= lon <= 180 or not -89 <= lat <= 89:
            raise ValueError('请在地图范围内模拟飞行')
        target = (lon, lat, max(0, min(500, height)))
        result = _approach(state, target, maximum, 3)
    return replace(result, second=state.second+1, mode=mode, battery=battery, route_second=route_second)
