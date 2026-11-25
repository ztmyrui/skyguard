"""Public flight checks over versioned, explicitly scoped data."""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import ceil, cos, radians
from urllib.parse import urlsplit

from shapely import transform
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .flight_route import FlightRoute, corridor, route_geometry, timeline


AIRCRAFT = ('微型', '轻型', '小型', '中型', '大型', '未知')
DEMO_START = datetime.fromisoformat('2026-11-19T15:20:00+08:00')
ALL_CATEGORIES = AIRCRAFT[:-1]


def instant(value):
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(result, datetime) or result.utcoffset() is None:
        raise ValueError('时间必须包含时区，如 +08:00。')
    return result


def lonlat(point):
    x, y = map(float, point)
    if not (-180 <= x <= 180 and -90 <= y <= 90):
        raise ValueError('经纬度超出范围。')
    return x, y


def polygon(rings):
    if not isinstance(rings, list) or not rings or sum(map(len, rings)) > 5000:
        raise ValueError('多边形没有边界或坐标过多。')
    boundary = [[lonlat(point) for point in ring] for ring in rings]
    shape = Polygon(boundary[0], boundary[1:])
    if shape.is_empty or not shape.is_valid or shape.area <= 0:
        raise ValueError('多边形边界无效，请检查闭合、相交和孔洞。')
    return shape


def web_source(value):
    url = urlsplit(value)
    if url.scheme != 'https' or not url.hostname or url.username or url.password:
        raise ValueError('来源需要使用 HTTPS 网页地址。')
    return value


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    rings: list
    start: datetime
    end: datetime
    lower: float
    upper: float
    categories: tuple
    kind: str
    issuer: str
    source: str
    note: str
    demo: bool
    verified: bool

    @classmethod
    def parse(cls, row):
        if row.get('crs', 'WGS84') != 'WGS84' or row.get('altitude_ref', 'AGL') != 'AGL':
            raise ValueError('规则导入须先转换到 WGS84 和离地高度。')
        if any(key in row and type(row[key]) is not bool for key in ('demo', 'verified')):
            raise ValueError('演练及复核状态须为布尔值。')
        aliases = {'restriction', 'coordination', 'coverage'}
        kind = row['kind']
        if kind not in aliases:
            raise ValueError('规则类型无效。')
        start, end = instant(row['start']), instant(row['end'])
        lower, upper = float(row['lower']), float(row['upper'])
        cats = tuple(row.get('categories', ALL_CATEGORIES))
        if not start < end or not 0 <= lower <= upper <= 10000 or not cats or set(cats) - set(ALL_CATEGORIES):
            raise ValueError('请核对规则的时间、高度和适用机型。')
        polygon(row['rings'])
        demo, verified = row.get('demo', False), row.get('verified', False)
        issuer, source = str(row.get('issuer', '')).strip(), str(row.get('source', '')).strip()
        name, identifier = str(row['name']).strip(), str(row['id']).strip()
        if not name or not identifier:
            raise ValueError('需要规则名称和编号。')
        if source:
            web_source(source)
        if verified and not demo and (not issuer or not source):
            raise ValueError('正式规则须填写发布者和出处。')
        return cls(identifier, name, row['rings'], start, end, lower, upper, cats,
                   kind, issuer, source, str(row.get('note', '')), demo, verified)

    def record(self):
        return dict(vars(self), start=self.start.isoformat(), end=self.end.isoformat())


@dataclass(frozen=True)
class FlightQuery:
    points: tuple
    form: str
    radius_m: int
    start: datetime
    end: datetime
    altitude_agl: int
    category: str
    special: bool = False
    demo: bool = True
    special_unknown: bool = False
    route: FlightRoute | None = None

    def __post_init__(self):
        object.__setattr__(self, 'start', instant(self.start))
        object.__setattr__(self, 'end', instant(self.end))
        if self.route is not None:
            route = FlightRoute.parse(self.route)
            if route.start != self.start:
                route = route.shifted(self.start, keep_window=True)
            if route.category != self.category:
                route = replace(route, category=self.category)
            object.__setattr__(self, 'route', route)
            object.__setattr__(self, 'form', 'route')
            object.__setattr__(self, 'points', tuple(w.xy for w in route.waypoints))
            object.__setattr__(self, 'end', route.end)
            object.__setattr__(self, 'altitude_agl', ceil(max(w.altitude_m for w in route.waypoints)))

    def geometry(self):
        if self.route is not None:
            return route_geometry(self.route, self.radius_m)
        points = [lonlat(p) for p in self.points]
        if not points or len(points) > 60:
            raise ValueError('请在地图上选取位置，最多 60 个点。')
        if self.form == 'area':
            if len(set(points)) < 3:
                raise ValueError('区域至少需要三个不同的顶点。')
            return polygon([points])
        if self.form not in ('circle', 'route') or not 1 <= self.radius_m <= 10000:
            raise ValueError('请选择范围类型，并填写合理的半径或航线宽度。')
        if self.form == 'route' and len(set(points)) < 2:
            raise ValueError('航线至少需要两个不同的点。')
        # Local metres around the query. Polygon and route widths use the same basis.
        x0, y0 = points[0]
        east = 111195 * cos(radians(y0))
        if east < 1000:
            raise ValueError('查询范围超出本地城市计算区域。')
        meters = [(float((x - x0) * east), float((y - y0) * 111195)) for x, y in points]
        target = Point(meters[0]) if self.form == 'circle' else LineString(meters)
        return transform(target.buffer(self.radius_m, quad_segs=12),
                         lambda x, y: (x0 + x / east, y0 + y / 111195),
                         interleaved=False)

    def validate(self):
        start, end = instant(self.start), instant(self.end)
        if not start < end <= start + timedelta(days=1):
            raise ValueError('结束时间须晚于开始时间，单次查询最长 24 小时。')
        if self.category not in AIRCRAFT or not 0 <= self.altitude_agl <= 10000:
            raise ValueError('请核对机型类别和离地高度。')

    def record(self):
        data = dict(vars(self), start=self.start.isoformat(), end=self.end.isoformat())
        data.pop('route')
        if self.route is not None:
            data['route'] = self.route.record()
        return data


def _flown_intersection(route, width, rule, area):
    for piece in timeline(route):
        duration = piece.end_s - piece.begin_s
        low = max(0, (rule.start - route.start).total_seconds() - piece.begin_s)
        high = min(duration, (rule.end - route.start).total_seconds() - piece.begin_s)
        if low >= high:
            continue
        slope = (piece.b.altitude_m - piece.a.altitude_m) / duration
        if slope:
            limits = sorted(((rule.lower - piece.a.altitude_m) / slope,
                             (rule.upper - piece.a.altitude_m) / slope))
            low, high = max(low, limits[0]), min(high, limits[1])
            if low > high:
                continue
        elif not rule.lower <= piece.a.altitude_m <= rule.upper:
            continue
        a, b = piece.at(low / duration), piece.at(high / duration)
        if area.intersects(corridor((a.xy, b.xy), width)):
            return True
    return False


class RuleBook:
    def __init__(self, records, *, exercise=()):
        self.rules = [Rule.parse(row) for row in records]
        self.shapes = [polygon(rule.rings) for rule in self.rules]
        self.tree = STRtree(self.shapes)
        self.exercise = RuleBook(exercise) if exercise else None

    def evaluate(self, query, geometry=None, alternatives=True):
        query.validate()
        shape = query.geometry() if geometry is None else geometry
        candidates = [int(i) for i in self.tree.query(shape, predicate='intersects')
                      if self.rules[int(i)].demo == query.demo]
        candidates.sort(key=lambda index: self.rules[index].id)
        known = query.category != '未知'
        applicable = [i for i in candidates if
                      (not known or query.category in self.rules[i].categories)
                      and self.rules[i].lower <= query.altitude_agl]
        first, last = query.route.filing_window if query.route else (query.start, query.end)
        active = [i for i in applicable if self.rules[i].start < last and first < self.rules[i].end]
        if query.route is not None:
            early, late = first - query.route.start, last - query.route.end
            active = [i for i in active if _flown_intersection(
                query.route, query.radius_m, replace(self.rules[i], start=self.rules[i].start - late,
                                                      end=self.rules[i].end - early), self.shapes[i])]
        restrictions = [self.rules[i] for i in active
                        if self.rules[i].kind == 'restriction' and self.rules[i].verified]
        unresolved = [self.rules[i] for i in active
                      if self.rules[i].kind == 'restriction' and not self.rules[i].verified]
        venue = [self.rules[i] for i in active if self.rules[i].kind == 'coordination']
        proofs = [i for i in applicable if self.rules[i].kind == 'coverage'
                  and self.rules[i].verified and self.rules[i].lower == 0
                  and self.rules[i].upper >= query.altitude_agl
                  and self.rules[i].start <= first and last <= self.rules[i].end]
        covered = bool(proofs and unary_union([self.shapes[i] for i in proofs]).covers(shape))
        advice = []
        if not known:
            advice.append('请补充机型类别。')
        if query.special_unknown:
            advice.append('请核对是否涉及飞越集会人群等特殊情形。')
        if not covered:
            advice.append('当前空域数据未完整覆盖所选范围、时段或高度，请到 UOM 核对。')
        if unresolved:
            advice.append('有尚未复核的公告，需核对原文。')
        needs_review = query.special or query.altitude_agl > 120 or query.category in ('中型','大型')
        if restrictions or needs_review:
            status = '涉及限制或需核对申请'
        elif not known or query.special_unknown or not covered or unresolved:
            status = '官方空域待核对' if not query.demo else '演练资料不足'
        else:
            status = '在本次数据范围内未发现限制'
        if needs_review:
            advice.append('填写的飞行条件需进一步核对官方申请要求。')
        suggestions = []
        if alternatives and (restrictions or venue) and known and not query.special:
            through = query.start + timedelta(hours=24)
            ends = {self.rules[i].end for i in applicable if
                    self.rules[i].kind != 'coverage' and query.start < self.rules[i].end <= through}
            duration = query.end - query.start
            for start in sorted(ends):
                plan = replace(query, start=start, end=start + duration)
                result = self.evaluate(plan, shape, alternatives=False)
                if result['status'] == '在本次数据范围内未发现限制' and not result['venue']:
                    suggestions.append((start, plan.end))
                    if len(suggestions) == 3:
                        break
        exercise = self.exercise.evaluate(replace(query, demo=True), shape, alternatives=False)['venue'] if self.exercise else []
        return {'status': status, 'restrictions': restrictions, 'unresolved': unresolved, 'exercise': exercise,
                'venue': venue, 'proofs': [self.rules[i] for i in proofs], 'advice': advice,
                'alternatives': suggestions, 'geometry': shape}

    def overlays(self, at, demo, marked=()):
        marked = set(marked)
        rows = [{'name': rule.name, 'rings': rule.rings, 'kind': rule.kind,
                 'highlight': rule.id in marked}
                for rule in self.rules if rule.demo == demo and rule.verified
                and rule.kind != 'coverage' and rule.start <= at < rule.end]
        if self.exercise:
            rows.extend(self.exercise.overlays(at, True))
        return rows


def drawing(geometry):
    shapes = geometry.geoms if isinstance(geometry, MultiPolygon) else [geometry]
    return [[list(shape.exterior.coords), *[list(r.coords) for r in shape.interiors]]
            for shape in shapes]


def demo_city():
    south, west = 28.170, 112.940
    def box(w, s, e, n):
        return [[[w, s], [e, s], [e, n], [w, n], [w, s]]]
    def rule(identifier, name, kind, rings, start, end, note):
        return {'id': identifier, 'name': name, 'kind': kind, 'rings': rings, 'demo': True,
                'verified': True, 'start': (DEMO_START + timedelta(minutes=start)).isoformat(),
                'end': (DEMO_START + timedelta(minutes=end)).isoformat(), 'lower': 0,
                'upper': 120, 'issuer': '原创演练数据', 'note': note}
    return {'id': 'changsha', 'name': '长沙', 'center': [112.953, 28.1792],
            'sources': ['https://uom.caac.gov.cn/', 'https://www.changsha.gov.cn/'],
            'rules': [
                rule('demo-coverage','演练数据覆盖范围','coverage',
                     box(west, south,112.977,28.195),0,90,'该范围仅代表演练资料齐全。'),
                rule('demo-restriction','滨江活动临时限制（演练）','restriction',
                     box(112.953,28.178,112.958,28.182),0,20,'模拟活动期间的查飞限制。'),
                rule('demo-venue','地面活动协调（演练）','coordination',
                     box(112.950,28.177,112.954,28.180),0,10,'活动时段建议错峰。')],
            'posts': [
                {'id':'east', 'name':'东门观察岗', 'lat':28.1788, 'lon':112.954, 'range_m':330,
                 'open_s':0, 'close_s':1800, 'enabled':True},
                {'id':'north', 'name':'北侧观察岗', 'lat':28.1811, 'lon':112.958, 'range_m':430,
                 'open_s':0, 'close_s':1800, 'enabled':True}],
            'walk': [{'from':'east','to':'north','seconds':55},
                     {'from':'north','to':'east','seconds':55}]}
