"""Analysis of the operator's own recorded flight against its filed route."""
from datetime import timedelta

from .attention_level import assess
from .disposal import feedback
from .flight_route import ROUTE_LIMITS, FlightRoute, WGS84, deviation, planned_position
from .flight_rules import instant
from .zone_catalog import zone_hits


def route_for(plan):
    filed = FlightRoute.parse(plan['query']['route'])
    return filed.shifted(instant(plan['started_at'])) if plan.get('started_at') else filed


def track_review(plan, points=None, *, zones=(), rules=None, width=None):
    points = plan.get('actual_track', ()) if points is None else points
    if not points:
        return dict(horizontal_m=0, height_m=0, duration_s=0, intervals=[], attention=None, position=None)
    route = route_for(plan)
    filed = FlightRoute.parse(plan['query']['route'])
    first, last = filed.filing_window
    allowed = ROUTE_LIMITS[route.category]
    horizontal_limit = width or allowed[2]
    attention = None
    intervals = []
    run = None
    lateral_max = vertical_max = 0
    actions = plan.get('flight_actions', ())
    for p in points:
        at = instant(p['at'])
        delta = deviation(route, min(at, route.end - timedelta(microseconds=1)), p['lon'], p['lat'], p['height'])
        if delta is None:
            continue
        horizontal, vertical, _ = delta
        vertical = abs(vertical or 0)
        lateral_max = max(lateral_max, horizontal)
        vertical_max = max(vertical_max, vertical)
        zone = next(iter(zone_hits((p['lon'], p['lat']), at, zones)), None)
        rating = zone.rating(at) if zone else ''
        sensitive = bool(zone and zone.restricted)
        excess = horizontal > horizontal_limit or vertical > allowed[3] or not first <= route.start < last or at >= last
        if excess:
            if run is None or p['t'] - run['last_t'] > 1:
                run = dict(start=p['at'], end=p['at'], start_t=p['t'], last_t=p['t'],
                           horizontal_m=0, height_m=0, area=zone.name if zone else '当前航段', duration_s=0)
                intervals.append(run)
            run.update(end=p['at'], last_t=p['t'], duration_s=p['t'] - run['start_t'] + 1,
                       horizontal_m=round(max(run['horizontal_m'], horizontal), 1),
                       height_m=round(max(run['height_m'], vertical), 1))
        else:
            run = None
        noticed = bool(run and run['duration_s'] >= 10)
        notice = [action for action in actions if action['at'] <= p['t'] and action.get('finished_at',action['at']) <= p['t']]
        attention = assess(dict(rating=rating, restricted=sensitive, outside=noticed, in_plan=not excess,
                               lost=p['mode']=='失联', high=p['height']>min(120,allowed[1]),
                               impact=1 if rating=='S1' else .4 if rating=='S2' else 0,
                               urgency=1 if sensitive else 0, anomaly=min(horizontal/(horizontal_limit*2),1),
                               **feedback(notice)), p['t'], attention, rules=rules)
    position = planned_position(route, min(instant(points[-1]['at']), route.end - timedelta(microseconds=1)))
    return dict(horizontal_m=round(lateral_max, 1), height_m=round(vertical_max, 1),
                duration_s=sum(x['duration_s'] for x in intervals), intervals=intervals,
                attention=attention, position=position)


def summary_text(result):
    return (f"偏离 {len(result['intervals'])} 次 · 最大横向 {result['horizontal_m']:g} 米"
            f" · 最大高度偏离 {result['height_m']:g} 米 · 累计 {result['duration_s']} 秒")


def track_layers(plan, report, *, cursor=None, public=False):
    recorded = plan.get('actual_track', [])
    track = recorded if cursor is None else [p for p in recorded if instant(p['at']) <= cursor]
    route = route_for(plan)
    path = [list(p.xy) for p in (route.waypoints)]
    path.insert(0, list(route.takeoff))
    routes = [dict(id='recorded:' + plan['id'], takeoff=route.takeoff, path=path, style='filing', waypoints=[])]
    if not track:
        if cursor is None:
            return dict(routes=routes, aircraft=[])
        point = planned_position(route, cursor)
        aircraft = ([dict(id='planned', lon=point.lon, lat=point.lat, altitude=point.altitude_m,
                          planned=True, state='预计', tail=[], label='计划位置', level=1)] if point else [])
        return dict(routes=routes, aircraft=aircraft)
    last = track[-1]
    level = report['attention'].level if report.get('attention') else 1
    samples = [[p['lon'], p['lat']] for p in track]
    plans = [dict(id='recorded:'+plan['id'], lon=last['lon'], lat=last['lat'], altitude=last['height'],
                  heading=last['heading'], level=level, state=last['mode'], selected=True, public=public,
                  tail=samples, label='鼠标飞行' if public else f'实际位置 · {level} 级')]
    if not public and report.get('position'):
        point = report['position']
        plans.append(dict(id='planned', lon=point.lon, lat=point.lat, altitude=point.altitude_m,
                          heading=last['heading'], level=1, state='预计', planned=True,
                          tail=[], label='计划位置', connector=[last['lon'], last['lat']]))
    if len(samples)>1:
        routes.append(dict(id='recorded:'+plan['id'], takeoff=samples[0], path=samples, style='actual', waypoints=[]))
    return dict(routes=routes, aircraft=plans)
