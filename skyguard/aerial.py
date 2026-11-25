"""Geometry and deterministic scoring for the public photography planner."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import asin, atan2, cos, degrees, floor, pi, radians, sin

from geographiclib.geodesic import Geodesic
from shapely import transform
from shapely.geometry import LineString, Point


CHINA = timezone(timedelta(hours=8))
HOUR = timedelta(hours=1)
SCENE_RADIUS = 500
FORECAST_OUTSIDE = '计划时段超出预报范围'
FORECAST_PARTIAL = '预报仅覆盖部分计划时段'
WGS84 = Geodesic.WGS84


def route_center(query):
    if query.form == 'circle':
        return tuple(query.points[0])
    if query.form == 'area':
        point = query.geometry().representative_point()
        return point.x, point.y
    from .flight_route import route_points
    points = route_points(query.route) if query.route else query.points
    segments = [WGS84.InverseLine(a[1], a[0], b[1], b[0]) for a, b in zip(points, points[1:])]
    halfway = sum(segment.s13 for segment in segments) / 2
    for segment in segments:
        if 0 < segment.s13 and halfway <= segment.s13:
            pos = segment.Position(halfway)
            return pos['lon2'], pos['lat2']
        halfway -= segment.s13
    return tuple(query.points[-1])


def touched_hours(start, end):
    start, end = start.astimezone(CHINA), end.astimezone(CHINA)
    hour = start.replace(minute=0, second=0, microsecond=0)
    while start < end and hour < end:
        yield hour
        hour += HOUR


def local_metres(center):
    lon, lat = center
    east = WGS84.Inverse(lat, lon, lat, lon + .01)['s12'] * 100
    north = WGS84.Inverse(lat, lon, lat + .01, lon)['s12'] * 100
    return lambda x, y: ((x - lon) * east, (y - lat) * north), lambda x, y: (lon + x / east, lat + y / north)


def scenic_geometry(query, center):
    project, unproject = local_metres(center)
    if query.form == 'route':
        from .flight_route import route_points
        points = route_points(query.route) if query.route else query.points
        segments = tuple(LineString([project(*a), project(*b)])
                         for a, b in zip(points, points[1:]) if a != b)
        path = LineString([project(*pt) for pt in points]) if segments else Point(project(*points[0]))
        corridor = path.buffer(SCENE_RADIUS, quad_segs=24)
    else:
        segments = ()
        corridor = transform(query.geometry(), project, interleaved=False)
    return segments, corridor, project, unproject


@dataclass(frozen=True)
class ScenicPlace:
    id: str
    name: str
    point: tuple
    category: str
    evidence: tuple
    prominence: int = 80


@dataclass(frozen=True)
class ScenicMatch:
    place: ScenicPlace
    distance: float
    segment: int | None
    bearing: float | None

    @property
    def score(self):
        return .60 * self.place.prominence + .40 * (100 - 50 * self.distance / SCENE_RADIUS)


def scenic_matches(query, center, places):
    segments, corridor, project, _ = scenic_geometry(query, center)
    matches = []
    seen = set()
    for place in sorted(places, key=lambda item: -item.prominence):
        name = ''.join(c for c in place.name.casefold() if c.isalnum())
        if place.id in seen or name in seen:
            continue
        point = Point(project(*place.point))
        if segments:
            index, segment = min(enumerate(segments), key=lambda pair: pair[1].distance(point))
            distance = segment.distance(point)
            if distance > SCENE_RADIUS + 1e-7:
                continue
            foot = segment.interpolate(segment.project(point))
        else:
            if not corridor.covers(point):
                continue
            index, distance, foot = None, min(corridor.centroid.distance(point), SCENE_RADIUS), Point(0, 0)
        bearing = degrees(atan2(point.x - foot.x, point.y - foot.y)) % 360 if point != foot else None
        matches.append(ScenicMatch(place, distance, index, bearing))
        seen.update((place.id, name))
    return tuple(sorted(matches, key=lambda match: (-match.score, match.distance,
                        match.segment if match.segment is not None else 0, match.place.name))[:5])


def scenic_score(matches):
    return sum(weight * match.score for weight, match in zip((.70, .20, .10), matches))


@dataclass(frozen=True)
class WeatherHour:
    time: datetime
    condition: str = ''
    wind: float | None = None
    gust: float | None = None
    rain_probability: float | None = None
    rain_mm: float | None = None
    visibility_km: float | None = None
    clouds: float | None = None
    thunder: bool = False
    levels: tuple = ()
    surface_gust_10m: float | None = None
    temperature: float | None = None
    wind_from_deg: float | None = None


@dataclass(frozen=True)
class Forecast:
    hours: tuple
    fetched: datetime
    weather_error: str = ''
    upper_error: str = ''

    @property
    def coverage(self):
        if self.hours:
            return self.hours[0].time, self.hours[-1].time + HOUR
        return None


@dataclass(frozen=True)
class WeatherAssessment:
    rows: tuple
    weather: float | None
    light: float | None
    status: str = ''
    hazard: str = ''


def piecewise(value, knots):
    if value <= knots[0][0]:
        return knots[0][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return knots[-1][1]


def altitude_wind(levels, altitude):
    lower = next(((h, v) for h, v in reversed(levels) if h <= altitude), None)
    upper = next(((h, v) for h, v in levels if h >= altitude), None)
    if lower is None or upper is None:
        return None, ()
    return max(lower[1], upper[1]), tuple(dict.fromkeys((lower[0], upper[0])))


def sun_position(point, when):
    """NOAA's fractional-year solar equations, azimuth clockwise from north."""
    lon, lat = map(radians, point)
    when = when.astimezone(timezone.utc)
    hour = when.hour + when.minute / 60 + when.second / 3600
    days = datetime(when.year, 12, 31).timetuple().tm_yday
    gamma = 2 * pi / days * (when.timetuple().tm_yday - 1 + (hour - 12) / 24)
    eq = 229.18 * (.000075 + .001868 * cos(gamma) - .032077 * sin(gamma)
                   - .014615 * cos(2 * gamma) - .040849 * sin(2 * gamma))
    dec = (.006918 - .399912 * cos(gamma) + .070257 * sin(gamma) - .006758 * cos(2 * gamma)
           + .000907 * sin(2 * gamma) - .002697 * cos(3 * gamma) + .00148 * sin(3 * gamma))
    angle = radians(((hour * 60 + eq + 4 * degrees(lon)) % 1440) / 4 - 180)
    vertical = sin(lat) * sin(dec) + cos(lat) * cos(dec) * cos(angle)
    elevation = degrees(asin(max(-1, min(1, vertical))))
    azimuth = (degrees(atan2(sin(angle), cos(angle) * sin(lat) - sin(dec) / cos(dec) * cos(lat))) + 180) % 360
    return elevation, azimuth


def solar_events(point, day):
    start = day.astimezone(CHINA).replace(hour=0, minute=0, second=0, microsecond=0)
    events = {}
    was_day = sun_position(point, start)[0] >= -.833
    for minute in range(1, 1441):
        time = start + timedelta(minutes=minute)
        elevation, azimuth = sun_position(point, time)
        is_day = elevation >= -.833
        if was_day != is_day:
            events['rise' if is_day else 'set'] = (time, azimuth)
        was_day = is_day
    return events


def _light(point, start, end, rows, style):
    if any(hour is None or hour.clouds is None for _, hour, _, _ in rows):
        return None
    n = max(1, int((end - start).total_seconds() / 300) + 1)
    total = 0
    for index in range(n):
        time = start + (end - start) * ((index + .5) / n)
        angle, bearing = sun_position(point, time)
        if style in ('日出', '日落'):
            morning = bearing < 180
            total += 100 if 0 <= angle <= 6 and morning == (style == '日出') else 0
        else:
            total += piecewise(angle, ((0, 0), (10, 55), (30, 100)))
    clouds = sum(hour.clouds for _, hour, _, _ in rows) / len(rows)
    return .70 * (total / n) + .30 * (100 * (1 - clouds))


def assess_weather(forecast, point, query, start, ceiling, style):
    end = start + (query.end - query.start)
    by_time = {hour.time: hour for hour in forecast.hours}
    rows = []
    hazard = ''
    for time in touched_hours(start, end):
        hour = by_time.get(time)
        wind, heights = altitude_wind(hour.levels, query.altitude_agl) if hour else (None, ())
        score = None
        if hour and hour.thunder:
            hazard = hazard or f'{time:%m-%d %H:%M} 预报有雷暴'
        gust = (hour.surface_gust_10m if hour.surface_gust_10m is not None else hour.gust) if hour else None
        if ceiling and max(v for v in (wind, gust, 0) if v is not None) >= ceiling:
            hazard = hazard or f'{time:%m-%d %H:%M} 达到所选机型的计划参考风限'
        if ceiling and hour and all(value is not None for value in (wind, gust, hour.rain_probability, hour.visibility_km)):
            wind_score = piecewise(max(wind, gust) / ceiling, ((.5, 100), (.8, 50), (1, 0)))
            rain = 100 * (1 - hour.rain_probability)
            visible = piecewise(hour.visibility_km, ((1, 0), (4, 50), (10, 100)))
            score = .55 * wind_score + .25 * rain + .20 * visible
        rows.append((time, hour, score, heights))
    if not forecast.hours:
        status = forecast.weather_error or '暂无逐小时预报'
    elif any(hour is None for _, hour, _, _ in rows):
        first, last = forecast.coverage
        if start >= last or end <= first:
            status = FORECAST_OUTSIDE
        elif start < first or end > last:
            status = FORECAST_PARTIAL
        else:
            status = '部分小时暂无预报'
    elif not ceiling:
        status = '选择具体机型以计算天气分'
    elif any(score is None for _, _, score, _ in rows):
        status = '计划高度风况或关键天气资料不足'
    else:
        status = ''
    scores = [row[2] for row in rows if row[2] is not None]
    return WeatherAssessment(tuple(rows), min(scores) if scores and len(scores) == len(rows) else None,
                             _light(point, start, end, rows, style) if rows else None, status, hazard)


def route_score(weather, light, scenic):
    if any(value is None for value in (weather, light, scenic)):
        return None
    return 50 + .60 * (weather - 50) + .15 * (light - 50) + .25 * (scenic - 50)


def displayed_score(value):
    return floor(value + .5)
