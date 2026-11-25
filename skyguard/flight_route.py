"""Time, height and geometry of a planned civilian flight route."""
from bisect import bisect_right
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from functools import lru_cache
from math import atan2, cos, degrees, hypot, isfinite, radians

from geographiclib.geodesic import Geodesic
from shapely import transform
from shapely.geometry import LineString, Point

WGS84 = Geodesic.WGS84
MAX_WAYPOINTS = 60
MAX_DURATION = 24 * 3600
ROUTE_RULES_VERSION = 'P9.1'
# Engineering planning limits. No category-wide performance guarantee is assumed.
ROUTE_LIMITS = {
    '微型': (8, 120, 20, 15), '轻型': (12, 120, 30, 20), '小型': (16, 120, 45, 25),
    '中型': (20, 120, 60, 30), '大型': (20, 120, 80, 40), '未知': (8, 120, 30, 20),
}


def _coordinates(point):
    x, y = (float(value) for value in point)
    if not (-180 <= x <= 180 and -89 <= y <= 89):
        raise ValueError('请填写有效的经纬度')
    return x, y


def _time(value):
    date = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(date, datetime) or date.utcoffset() is None:
        raise ValueError('航线时间需要时区')
    return date


@dataclass(frozen=True)
class Waypoint:
    lon: float
    lat: float
    altitude_m: float
    hover_s: int = 0
    camera_deg: float | None = None
    shot: str = ''
    recording: str = ''
    pitch_deg: float | None = None
    move: str = ''
    tip: str = ''

    def __post_init__(self):
        object.__setattr__(self, 'lon', _coordinates((self.lon, self.lat))[0])
        object.__setattr__(self, 'lat', float(self.lat))
        if (type(self.hover_s) is not int or not 0 <= self.hover_s <= MAX_DURATION
                or not isfinite(self.altitude_m) or not 0 <= self.altitude_m <= 10_000):
            raise ValueError('请核对航点高度和悬停时长')
        object.__setattr__(self, 'altitude_m', float(self.altitude_m))

        if self.camera_deg is not None:
            if type(self.camera_deg) not in (int,float) or not 0 <= self.camera_deg < 360:
                raise ValueError('请填写 0 至 360 度的镜头方向')
        if self.pitch_deg is not None and (type(self.pitch_deg) not in (int, float) or not -90 <= self.pitch_deg <= 30):
            raise ValueError('请填写 −90 至 30 度的云台俯仰角')
        if not isinstance(self.move, str) or not isinstance(self.tip, str) or len(self.tip) > 40:
            raise ValueError('请将构图建议控制在 40 字以内')

    def record(self):
        return {key: value for key, value in vars(self).items() if value not in (None, '')}

    @property
    def xy(self):
        return self.lon, self.lat


@dataclass(frozen=True)
class FlightRoute:
    waypoints: tuple[Waypoint, ...]
    speed_mps: float
    takeoff: tuple[float, float]
    start: datetime
    category: str
    window_start: datetime | None = None
    window_end: datetime | None = None
    complete_flight: bool = False

    def __post_init__(self):
        points = tuple(self.waypoints)
        if not 1 <= len(points) <= MAX_WAYPOINTS or not all(isinstance(w, Waypoint) for w in points):
            raise ValueError('请填写 1 至 60 个航点')
        speed = float(self.speed_mps)
        if self.category not in ROUTE_LIMITS or type(self.speed_mps) is bool or not .2 <= speed <= ROUTE_LIMITS[self.category][0]:
            raise ValueError('请按所选机型调整航线速度')
        object.__setattr__(self, 'waypoints', points)
        object.__setattr__(self, 'takeoff', _coordinates(self.takeoff))
        object.__setattr__(self, 'speed_mps', speed)
        object.__setattr__(self, 'start', _time(self.start))
        if not 0 < route_duration(self) <= MAX_DURATION:
            raise ValueError('请设置有效航程或悬停时长，单次最长 24 小时')
        if self.window_start is not None or self.window_end is not None:
            begin = _time(self.window_start) if self.window_start is not None else self.start
            end = _time(self.window_end) if self.window_end is not None else self.default_window_end
            if not begin <= self.start < self.end <= end or not timedelta(minutes=30) <= end - begin <= timedelta(hours=24):
                raise ValueError('报备时段须覆盖预计飞行，最短 30 分钟，最长 24 小时')
            object.__setattr__(self, 'window_start', begin)
            object.__setattr__(self, 'window_end', end)

    @property
    def filing_window(self):
        return self.window_start or self.start, self.window_end or self.end

    @property
    def default_window_end(self):
        return min(self.start + timedelta(days=1),
                   max(self.start + timedelta(minutes=30), self.end + timedelta(minutes=20)))

    def shifted(self, start, *, keep_window=False):
        start = _time(start)
        shift = start - self.start
        return replace(self, start=start,
                       window_start=self.window_start + shift if keep_window and self.window_start else None,
                       window_end=self.window_end + shift if keep_window and self.window_end else None)

    def record(self):
        data = {'waypoints': [w.record() for w in self.waypoints], 'speed_mps': self.speed_mps,
                'takeoff': list(self.takeoff), 'start': self.start.isoformat(), 'category': self.category}
        if self.complete_flight:
            data['complete_flight'] = True
        if self.window_start is not None:
            data.update(window_start=self.window_start.isoformat(), window_end=self.window_end.isoformat())
        return data

    @classmethod
    def parse(cls, data):
        if isinstance(data, cls):
            return data
        required = {'waypoints', 'speed_mps', 'takeoff', 'start', 'category'}
        if not isinstance(data, dict) or not required <= set(data) <= required | {'window_start', 'window_end', 'complete_flight'}:
            raise ValueError('航线字段不完整')
        try:
            return cls(tuple(Waypoint(**w) for w in data['waypoints']), data['speed_mps'],
                       data['takeoff'], data['start'], data['category'],
                       data.get('window_start'), data.get('window_end'), data.get('complete_flight') is True)
        except (TypeError, KeyError, OverflowError) as exc:
            raise ValueError('航点数据有误') from exc

    @property
    def end(self):
        return self.start + timedelta(seconds=route_duration(self))


@dataclass(frozen=True)
class RoutePiece:
    a: Waypoint
    b: Waypoint
    begin_s: float
    end_s: float
    bearing: float
    metres: float
    phase: str = '进场'
    shot: str = ''

    def at(self, fraction):
        if not self.metres:
            lon,lat = self.a.xy
        else:
            point = WGS84.Direct(self.a.lat,self.a.lon,self.bearing,self.metres*fraction)
            lon,lat = point['lon2'],point['lat2']
        return Waypoint(lon,lat,self.a.altitude_m+(self.b.altitude_m-self.a.altitude_m)*fraction)


@lru_cache(maxsize=1024)
def _inverse(a, b):
    answer = WGS84.Inverse(a[1], a[0], b[1], b[0])
    return answer['s12'], answer['azi1']


def camera_pitch(origin, altitude_m, subject, target_height_m=15.0):
    metres, _ = _inverse(tuple(origin), tuple(subject))
    ref_h = altitude_m * .35 if altitude_m <= target_height_m else target_height_m
    return round(max(-85., min(0., -degrees(atan2(max(0., altitude_m - ref_h), max(metres, 1.))))), 1)


@lru_cache(maxsize=192)
def timeline(route):
    first = route.waypoints[0]
    begin = Waypoint(*route.takeoff, 0 if route.complete_flight else first.altitude_m)
    pieces = []
    elapsed = 0.0

    def add(end, phase, seconds=None, shot=''):
        nonlocal begin, elapsed
        metres, bearing = _inverse(begin.xy, end.xy)
        vertical = end.altitude_m - begin.altitude_m
        if seconds is None:
            if route.complete_flight:
                vertical_speed = 2.5 if vertical >= 0 else 1.8
                seconds = max(metres / route.speed_mps, abs(vertical) / vertical_speed)
                if metres > .01:
                    # Stop and accelerate at each timed leg; allow a low-speed settling interval.
                    seconds += min(3.0, route.speed_mps / 2)
            else:
                seconds = hypot(metres, vertical) / route.speed_mps
        if seconds > 0:
            pieces.append(RoutePiece(begin, end, elapsed, elapsed+seconds,
                                     bearing if metres > .01 else 0., metres, phase, shot))
            elapsed += seconds
        begin = end

    if route.complete_flight:
        add(Waypoint(*route.takeoff, first.altitude_m), '起飞爬升')
    for index, point in enumerate(route.waypoints):
        filming = point.recording == 'track'
        add(point, '拍摄' if filming else '进场' if index == 0 else '转场',
            shot=point.shot if filming else '')
        if point.hover_s:
            add(point, '拍摄' if point.shot else '悬停', point.hover_s, point.shot)
    if route.complete_flight:
        add(Waypoint(*route.takeoff, begin.altitude_m), '返航')
        add(Waypoint(*route.takeoff, 0), '降落')
    return tuple(pieces)


def route_duration(route):
    parts = timeline(route)
    return parts[-1].end_s if parts else 0.0


def planned_position(route, when):
    when = _time(when)
    elapsed = (when - route.start).total_seconds()
    parts = timeline(route)
    if elapsed < 0 or when >= route.end:
        return None
    index = min(bisect_right([piece.end_s for piece in parts], elapsed), len(parts)-1)
    piece = parts[index]
    return piece.at(min(1.0,(elapsed-piece.begin_s)/(piece.end_s-piece.begin_s)))


def route_points(route):
    points = (route.takeoff, *(point.xy for point in route.waypoints))
    return (*points, route.takeoff) if route.complete_flight and points[-1] != route.takeoff else points


def corridor(points, width_m):
    if not isfinite(width_m) or not 1 <= width_m <= 10000:
        raise ValueError('请填写有效的航线单侧宽度')
    lon,lat=points[0]
    east = 111195*cos(radians(lat))
    north = 111195.0
    xy = [((x-lon)*east,(y-lat)*north) for x,y in points]
    shape = Point(xy[0]) if len(set(xy))==1 else LineString(xy)
    return transform(shape.buffer(width_m,quad_segs=12),
                     lambda x,y: (lon+x/east,lat+y/north), interleaved=False)


def route_geometry(route,width_m):
    # Preserve revisits and backtracking in the same corridor.
    return corridor(route_points(route), width_m)


def destination(point, bearing, metres):
    p = WGS84.Direct(point[1],point[0],bearing,metres)
    return p['lon2'],p['lat2']


def orbit(center, radius, altitude, laps, *, takeoff=None, speed=5, start, category):
    if not 10 <= radius <= 500 or type(laps) is not int or not 1 <= laps <= 4:
        raise ValueError('环绕半径取 10 至 500 米，圈数取 1 至 4')
    points = tuple(Waypoint(*destination(center, i*30, radius), altitude) for i in range(laps*12+1))
    return FlightRoute(points,speed,takeoff or points[0].xy,start,category)


def tracking(begin, bearing, length, altitude, *, takeoff=None, speed=5, start, category):
    if not isfinite(bearing) or not 1 <= length <= 2000:
        raise ValueError('请填写方向和 1 至 2000 米的横移长度')
    points = (Waypoint(*begin,altitude), Waypoint(*destination(begin,bearing,length),altitude))
    return FlightRoute(points,speed,takeoff or begin,start,category)


def rise(point, low, high, *, takeoff=None, speed=3, start, category):
    if not low < high:
        raise ValueError('拉升终点高度须大于起点高度')
    return FlightRoute((Waypoint(*point,low),Waypoint(*point,high)),speed,takeoff or point,start,category)


def display_route(route):
    parts = timeline(route)
    return {'waypoints':[[w.lon,w.lat,w.altitude_m] for w in route.waypoints],
            'takeoff':route.takeoff,
            'path':route_points(route),
            'moving':[{'a':p.a.xy,'b':p.b.xy,'start':p.begin_s,'end':p.end_s} for p in parts]}


def deviation(route, when, lon, lat, altitude):
    planned = planned_position(route, when)
    if planned is None:
        return None
    if altitude is not None and not isfinite(altitude):
        altitude = None
    lon, lat = _coordinates((lon, lat))
    elapsed = (_time(when) - route.start).total_seconds()
    east = 111195*cos(radians(lat))
    north = 111195.0
    candidates = []
    for piece in timeline(route):
        a = ((piece.a.lon-lon)*east, (piece.a.lat-lat)*north)
        b = ((piece.b.lon-lon)*east, (piece.b.lat-lat)*north)
        dx,dy = b[0]-a[0],b[1]-a[1]
        denom = dx*dx+dy*dy
        vertical = piece.b.altitude_m-piece.a.altitude_m
        if denom:
            f = max(0,min(1,-(a[0]*dx+a[1]*dy)/denom))
        elif vertical and altitude is not None:
            f = max(0,min(1,(altitude-piece.a.altitude_m)/vertical))
        else:
            f = max(0,min(1,(elapsed-piece.begin_s)/(piece.end_s-piece.begin_s)))
        dist = hypot(a[0]+f*dx,a[1]+f*dy)
        h = piece.a.altitude_m+f*vertical
        along = piece.begin_s + f*(piece.end_s-piece.begin_s)
        delay = elapsed - along
        candidates.append((dist, abs(altitude-h) if altitude is not None else 0, abs(delay), delay))
    cross,_,_,delay = min(candidates,key=lambda row:(round(row[0],3),row[1],row[2]))
    return cross, None if altitude is None else altitude - planned.altitude_m, delay
