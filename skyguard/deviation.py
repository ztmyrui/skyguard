"""Review continuous route deviations for training tracks."""
from datetime import timedelta

from .flight_route import ROUTE_LIMITS, deviation as measures


def assess_deviation(route, points, origin, *, approaches_sensitive=False, wind_speed=None):
    if not route or not points:
        return None
    _, _, allowed_distance, allowed_altitude = ROUTE_LIMITS[route.category]
    latest = points[-1]
    sampled = []
    for p in points[-20:]:
        delta = measures(route, origin+timedelta(seconds=p['t']),p['lon'],p['lat'],p.get('height'))
        if delta:
            horizontal, vertical, delay = delta
            width = max(horizontal/allowed_distance, abs(vertical or 0)/allowed_altitude, abs(delay)/60)
            sampled.append((p['t'], delta, width))
    if not sampled:
        return None
    latest_t, delta, width = sampled[-1]
    since = latest_t
    for before, _, earlier_width in reversed(sampled):
        if earlier_width <= 1 or since-before > 1:
            break
        since = before
    seconds = latest_t-since
    level = '容差内' if width <= 1 else '等待确认' if seconds < 10 else (
        '偏向敏感区' if approaches_sensitive else '轻微' if width <= 1.5 else '明显')
    weather = latest.get('speed')
    hint = ''
    if level in ('明显','偏向敏感区','轻微'):
        if latest.get('mode') == '返航':
            hint = '规则估计 · 返航阶段待核实'
        elif wind_speed is not None and weather is not None and abs(weather-wind_speed) < 1:
            hint = '规则估计 · 可能随风漂移'
        elif len(points) > 5 and latest.get('height',0) + 8 < points[-6].get('height',0):
            hint = '规则估计 · 持续下降，待核实'
        elif latest.get('speed',0) < .5:
            hint = '规则估计 · 航线外停留'
    return dict(horizontal_m=delta[0],height_m=delta[1],delay_s=delta[2],seconds=seconds,
                level=level,hint=hint)
