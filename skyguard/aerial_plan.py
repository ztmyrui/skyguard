"""Rank civilian camera positions from an engineering-curated city catalog."""
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import lru_cache
import json
import re
from pathlib import Path

from shapely.geometry import LineString, Point

from .aerial import local_metres, route_center, solar_events, sun_position
from .flight_check import HEADLINES, Verdict
from .flight_route import FlightRoute, ROUTE_LIMITS, WGS84, Waypoint, camera_pitch, destination, route_points
from .flight_rules import FlightQuery
from .photo_quality import score_photo

KEYWORDS = {
    '江河': ('湘江', '江河', '江面', '黄浦江', '珠江', '沿江', '钱塘江'),
    '湖面': ('湖面', '湖景', '西湖', '梅溪湖'), '城市天际线': ('天际线', '城景'),
    '高楼': ('高楼', '摩天'), '桥梁': ('桥',), '商圈夜景': ('夜景', '商圈', '灯光'),
    '山景': ('山景', '群山', '山脉', '岳麓山'), '历史建筑': ('历史建筑', '古建', '古镇', '古街'),
    '公园绿地': ('公园', '绿地', '草坪', '湿地'), '日出日落': ('日出', '日落', '夕阳', '晚霞', '黄金时段'),
}
TOPICS = tuple(KEYWORDS)
LIGHT = ('日出', '日落', '夜景')
SUBJECTS = tuple(t for t in TOPICS if t != '日出日落')
STYLE_CHOICES = (('auto', '智能匹配'), ('cinematic', '电影大片'), ('panoramic', '稳妥全景'), ('quick', '快速打卡'))
STYLE_LABELS = dict(STYLE_CHOICES, cinematic='电影运镜', repeat='精准复拍')
SOURCE_LABELS = {'reference': '精选机位', 'derived': '推演机位', 'poi': '高德定位 · 动态推演'}
STYLE_WORDS = {
    'cinematic': ('环绕', '刷锅', '大片', '电影', '拉升', '渐远', '揭示', '运镜', '震撼'),
    'panoramic': ('全景', '俯拍', '鸟瞰', '稳妥', '稳飞', '新手', '高空', '航拍全貌'),
    'quick': ('快速', '打卡', '简单', '省电', '短时间'),
}
CROWD_WORDS = ('避开人', '人少', '别飞人群', '清静')


def capture_preferences(text):
    clauses = re.split(r'[，。；、,;！!]|(?:而是|只想|改为|但是)', text)
    positive = '，'.join(c for c in clauses if not re.match(r'\s*(?:不要|不想|不拍|别|避开)', c))
    subjects = tuple(t for t in local_topics(positive)
                     if t != '日出日落' and (t != '商圈夜景' or '商圈' in positive))
    aliases = {'日出': ('日出','晨曦'), '日落': ('日落','晚霞','夕阳'), '夜景': ('夜景','灯光')}
    return subjects, tuple(t for t, terms in aliases.items() if any(term in positive for term in terms))


@dataclass(frozen=True)
class FlightPreference:
    subjects: tuple = ()
    lights: tuple = ()
    style: str = 'cinematic'
    avoid_crowds: bool = False
    place_query: str = ''
    shot_subjects: tuple = ()


def parse_preferences(text, mission_kind=None, explicit_topics=(), style='auto'):
    subjects, lights = capture_preferences(text)
    parts = re.split(r'[，。；、,;！!？?]|(?:而是|只想|改为|但是)', text)
    positive = tuple(part for part in parts if not re.match(r'\s*(?:不要|不想|不拍|别|避开)', part))
    if any('日间' in part or '白天' in part for part in positive):
        lights = (*lights, '日间')
    if mission_kind == 'repeat':
        style = 'repeat'
    elif style not in STYLE_WORDS:
        style = next((key for key, words in STYLE_WORDS.items()
                      if any(word in part for part in positive for word in words)),
                     'panoramic' if mission_kind == 'news' else 'cinematic')
    remove = ('今天', '明天', '后天', '本周末', '周末', '日出', '日落', '夜景', '日间', '白天',
              '晚霞', '夕阳', '晨曦', '黄金时段', '灯光', '几点', '想拍', '帮我找', '航拍', '慢速', '拍摄')
    words = sorted((*remove, *(w for choices in STYLE_WORDS.values() for w in choices)), key=len, reverse=True)
    pattern = '|'.join(map(re.escape, words))
    places = []
    for part in positive:
        part = re.sub(r'(?:避开人(?:群|多的地方|多)?|人少|清静|别飞人群).*$', '', part)
        term = re.sub(rf'{pattern}|\d{{1,2}}[:：]\d{{2}}|\d{{1,2}}\s*[点时](?:半|\d{{1,2}}分?)?', '', part)
        term = re.sub(r'^(?:我想|想|拍|找|去|在)+|(?:附近|周边|的|一下|和|与)$', '', term.strip())
        if len(term) >= 2:
            places.append(term[:40])
    explicit_lights = tuple(t for t in explicit_topics if t in (*LIGHT, '日间'))
    explicit_subjects = tuple(t for t in explicit_topics if t in SUBJECTS)
    generic = set(TOPICS) | {word for terms in KEYWORDS.values() for word in terms if len(word) <= 4}
    place = next((p for p in places if p not in generic or p in ('梅溪湖', '西湖', '岳麓山', '湘江', '珠江', '黄浦江', '钱塘江')), '')
    return FlightPreference(explicit_subjects or subjects, explicit_lights or lights, style,
                            any(word in text for word in CROWD_WORDS), place, tuple(places[:4]))


@dataclass(frozen=True)
class Scene:
    name: str
    point: tuple
    tags: tuple
    axis: float
    takeoffs: tuple
    stand: int
    avoid: int
    reference: str = ''
    reference_path: tuple = ()
    tracks: tuple = ()
    source_kind: str = ''
    address: str = ''


@dataclass(frozen=True)
class StyleVariant:
    key: str
    label: str
    subtitle: str
    shot: str
    route: FlightRoute
    window: 'Window | None' = None


@dataclass(frozen=True)
class Window:
    query: FlightQuery
    verdict: Verdict
    score: float | None
    components: tuple
    status: str
    timing: float
    weather_summary: str = ''
    unsuitable: bool = False
    light: str = ''
    camera_deg: float | None = None
    reasons: tuple = ()
    alternative_date: bool = False
    why_recommended: str = ''
    camera_advice: str = ''
    safety_notes: tuple[str, ...] = ()
    variants: tuple[StyleVariant, ...] = ()
    source_kind: str = 'reference'
    style_key: str = ''


@dataclass(frozen=True)
class Proposal:
    scene: Scene
    shot: str
    windows: tuple[Window, ...]

    @property
    def headline(self):
        return HEADLINES[self.windows[0].verdict]


def select_variant(window, variant):
    return replace(variant.window, variants=window.variants, alternative_date=window.alternative_date)


def local_topics(text):
    return tuple(tag for tag, words in KEYWORDS.items() if any(word in text for word in words))


def model_topics(data):
    if not isinstance(data, dict) or not isinstance(data.get('tags'), list):
        return ()
    return tuple(tag for tag in TOPICS if tag in data['tags'])


@lru_cache(maxsize=1)
def catalog():
    source = Path(__file__).with_name('assets') / 'scenic-catalog.json'
    return json.loads(source.read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def reference_routes():
    source = Path(__file__).with_name('assets') / 'photo-references.json'
    return json.loads(source.read_text(encoding='utf-8'))


def load_scenes(city):
    scenes = tuple(Scene(name, (lon, lat), tuple(meta['tags']), meta['axis_deg'],
                         tuple(tuple(p) for p in meta['takeoffs'] or ()), meta['standoff_m'],
                         meta.get('avoid_m', 0), meta.get('viewpoint_source',''))
                   for name, lon, lat, _genre, _alias, _source, meta in catalog().get(city, {}).get('places', ()))
    references = []
    for row in reference_routes().get(city, ()):
        path = tuple(tuple(point) for point in row['path'])
        references.append(Scene(row['name'], tuple(row['subject']), tuple(row['tags']),
                                0, (path[0],), 0, 0, row['land_source'], path,
                                tuple((tuple(item['end']),item['length_m'],item['bearing'])
                                      for item in row.get('tracks',()))))
    return (*references, *scenes)


def matching_place(scenes, text):
    labels = {re.sub(r'(远景|山景|水景)$','',part.strip()) for scene in scenes for part in scene.name.split('/')}
    term = max((label for label in labels if len(label)>=2 and label in text),key=len,default='')
    return tuple(scene for scene in scenes if term and term in scene.name)


def nearest(point, candidates):
    return min(candidates, key=lambda p: distance(point, p))


def distance(a, b):
    return WGS84.Inverse(a[1], a[0], b[1], b[0])['s12']


def camera_bearing(origin, subject):
    return WGS84.Inverse(origin[1],origin[0],subject[1],subject[0])['azi1'] % 360


def scene_from_poi(name, point, poi_type='', address=''):
    point = tuple(map(float, point))
    if len(point) != 2 or not (-180 <= point[0] <= 180 and -89 <= point[1] <= 89):
        raise ValueError('地标坐标无效')
    description = ' '.join((name, poi_type))
    types = (
        ('江河', ('水系', '河', '江', '水库')), ('湖面', ('湖',)),
        ('山景', ('山', '岭', '峰')), ('公园绿地', ('公园', '湿地', '广场')),
        ('桥梁', ('桥',)), ('历史建筑', ('古', '馆', '寺', '塔', '阁', '故居')),
        ('城市天际线', ('写字楼', '中心', '大厦', 'CBD', '商务住宅')),
        ('高楼', ('写字楼', '中心', '大厦', 'CBD', '商务住宅')),
    )
    tags = tuple(tag for tag, words in types if any(word in description for word in words))
    stand = 180 if set(tags) & {'山景', '公园绿地', '江河', '湖面'} else 160
    return Scene(name.strip(), point, tags, 0., (), stand, 60, source_kind='poi', address=address)


def angle_between(a, b):
    return abs((a - b + 180) % 360 - 180)


def light_alignment(heading, elevation, azimuth, light):
    if light == '夜景' or elevation < -6:
        return 0.
    target = 45 if light in ('日出', '日落') else 155
    return -abs(angle_between(heading, azimuth) - target)


def best_track(scene, takeoff, when, light):
    if not scene.tracks:
        return None
    elevation, azimuth = sun_position(scene.point, when)

    def rank(track):
        _, length, bearing = track
        midpoint = destination(takeoff, bearing, length / 2)
        heading = camera_bearing(midpoint, scene.point)
        if light == '夜景':
            return (-angle_between(heading, camera_bearing(takeoff, scene.point)), length)
        return (light_alignment(heading, elevation, azimuth, light), length)

    return max(scene.tracks, key=rank)


def derive_takeoff_candidates(scene, target_time, lights, check, category, scenes):
    radius = min(260, max(130, scene.stand, scene.avoid + 70))
    elevation, azimuth = sun_position(scene.point, target_time)
    light = ('夜景' if elevation < -1 else
             '日出' if elevation <= 10 and azimuth < 180 else
             '日落' if elevation <= 10 else '日间')
    if light not in lights and lights:
        light = next((item for item in LIGHT if item in lights), '日间')
    candidates = []
    for bearing in range(0, 360, 45):
        point = destination(scene.point, bearing, radius)
        if any(distance(point, protected.point) <= protected.avoid for protected in (*scenes, scene) if protected.avoid):
            continue
        check_point = FlightQuery((point,), 'circle', 25, target_time, target_time + timedelta(minutes=2),
                                  0, category, demo=False)
        verdict = check(check_point)
        if verdict != Verdict.RESTRICTED:
            candidates.append((verdict != Verdict.CLEAR,
                               -light_alignment((bearing + 180) % 360, elevation, azimuth, light), bearing, point))
    return tuple(row[-1] for row in sorted(candidates)[:2])


def _build_style(scene, takeoff, track, start, category, style, mission_kind, preference):
    speed, maximum, _, _ = ROUTE_LIMITS[category]
    maximum = min(120, maximum)
    heading = camera_bearing(takeoff, scene.point)
    _, length, axis = track or (None, 70, (heading + 90) % 360)
    radius = max(scene.stand, distance(scene.point, takeoff), scene.avoid + 65)
    radial = camera_bearing(scene.point, takeoff)

    def lateral(metres):
        return destination(takeoff, axis, min(metres, length))

    def orbit(angle):
        return destination(scene.point, radial + angle, radius)

    def waypoint(point, height, seconds, title, move, tip, recording='hold', target_height=15.):
        height = min(height, maximum)
        return Waypoint(*point, height, seconds, round(camera_bearing(point, scene.point), 1) % 360,
                        title, recording, camera_pitch(point, height, scene.point, target_height), move, tip[:40])

    def birdseye(point, height, seconds, title, move, tip):
        waypoint_at_subject = waypoint(point, height, seconds, title, move, tip, target_height=0.)
        # Keep the subject on the upper half of the wide composition and include foreground below it.
        ground_focus = destination(point, waypoint_at_subject.camera_deg, min(distance(point, scene.point), height * 1.2))
        return replace(waypoint_at_subject, pitch_deg=camera_pitch(point, waypoint_at_subject.altitude_m, ground_focus, 0.))

    name = scene.name.split(' / ')[0]
    def title_for(index, title):
        objects = preference.shot_subjects
        if mission_kind == 'promo' and len(objects) > 1:
            return f'{title} · {objects[min(index, len(objects)-1)][:12]}'
        return title

    titles = tuple(title_for(i, title) for i, title in enumerate(('环境全景', '主体展示', '细节推进', '拉升收尾')))
    if style == 'cinematic':
        sweep = 30 if preference.avoid_crowds else 34
        far = track[0] if track else orbit(sweep)
        midpoint = lateral(length / 2) if track else orbit(0)
        near = lateral(min(14, length / 5)) if track else orbit(-sweep)
        move = '侧移推进' if track else '弧线半环绕'
        high = destination(far, camera_bearing(far, scene.point) + 180, 18)
        points = (
            waypoint(takeoff, 28, 7, titles[0], '低空定场', f'让前景从画面下缘引向{name}'),
            waypoint(near, 32 if track else 38, 0, '', '', '', ''),
            waypoint(midpoint, 44 if track else 46, 0, titles[1], move, '慢推摇杆，让主体留在画面同一侧', 'track'),
            waypoint(far, 52 if track else 55, 0, titles[2], move, '用背景的视差衬托主体轮廓', 'track'),
            waypoint(high, 82, 0, titles[3], '拉升揭示全景', '随高度缓慢下压云台，逐步露出远景', 'track'),
            waypoint(high, 82, 9, '高空定格', '高空定格', '留出地平线上方空间，稳住画面再停录'),
        )
        pace = 3.5
    elif style == 'panoramic':
        far = lateral(36)
        low, high = (65, 85) if mission_kind == 'news' else (60, 96)
        points = (
            waypoint(takeoff, low, 7, title_for(0, '场地全景'), '高点定场', f'稳住地平线，给{name}四周留白'),
            waypoint(far, low, 0, title_for(1, '环境交代'), '稳态横移', '沿外围平移，避开人群正上方', 'track'),
            birdseye(far, high, 8, title_for(2, '环境鸟瞰'), '鸟瞰全景', '将主体放在画面上半部，留出地面引导线'),
        )
        pace = 2.8
    elif style == 'repeat':
        points = (
            waypoint(takeoff, 40, 8, '固定立面对比', '精准复拍', '记下主体边界与参照物，下次沿用同一画幅'),
            birdseye(takeoff, 90, 8, '固定全景对比', '精准复拍', '沿用本机位、朝向及俯角，保持画面对齐'),
        )
        pace = 3.
    else:
        far = lateral(15)
        points = (
            waypoint(takeoff, 35, 0, '', '', '', ''),
            waypoint(far, 35, 0, title_for(0, '地标定场'), '低空定场推进', f'用短推进把视线引向{name}', 'track'),
            waypoint(far, 75, 8, title_for(1, '地标全景'), '拉升定格', '拉升时缓慢下压云台，停稳后保留全景'),
        )
        pace = 5.
    return FlightRoute(points, min(pace, speed), takeoff, start, category, complete_flight=True)


def build_shot(scene, topics, start, category, origin, mission_kind=None):
    if not scene.takeoffs:
        return '地点推荐', None
    preference = parse_preferences('', mission_kind, topics)
    takeoff = nearest(origin or scene.point, scene.takeoffs)
    light = next((item for item in topics if item in LIGHT), '日间')
    track = best_track(scene, takeoff, start, light)
    return STYLE_LABELS[preference.style], _build_style(
        scene, takeoff, track, start, category, preference.style, mission_kind, preference)


def flight_metrics(route):
    return (max(w.altitude_m for w in route.waypoints),
            max(distance(route.takeoff, p) for p in route_points(route)))


def build_director_brief(scene, route, window_start, light, weather_hour, style_key, source_kind):
    _, azimuth = sun_position(scene.point, window_start)
    filming = [w for w in route.waypoints if w.recording == 'track'] or route.waypoints
    heading = filming[len(filming) // 2].camera_deg
    if heading is None:
        heading = camera_bearing(filming[0].xy, scene.point)
    relation = angle_between(heading, azimuth)
    lighting = ('正逆光' if relation < 15 else '侧逆光' if relation < 75 else
                '侧光' if relation < 105 else '侧顺光' if relation < 165 else '顺光')
    direction = ('北', '东北', '东', '东南', '南', '西南', '西', '西北')[round(azimuth / 45) % 8]
    line = (f'镜头朝向 {heading:.0f}°，让主体与城市灯光同框' if light == '夜景' else
            f'太阳在{direction} {azimuth:.0f}°，镜头朝向 {heading:.0f}°，呈{lighting}')
    hook = {'cinematic': '从前景逐步揭示全景', 'panoramic': '用稳态横移交代主体周边',
            'quick': '用短推进和高位定格保留两段画面', 'repeat': '用两层固定机位保留对比画面'}
    why = f'{window_start:%H:%M} {light}，{line}。{hook.get(style_key, "围绕主体取景")}。'
    camera = {
        '日出': '视频可用 25 或 30 fps，锁定白平衡。光比大时用 AEB 拍静态画面。',
        '日落': '视频可用 25 或 30 fps，锁定白平衡并保护高光。光比大时试 HDR 或 AEB。',
        '夜景': '取下 ND，锁定白平衡。减速或悬停取景，防止慢快门拖影。',
        '日间': '视频可用 25 或 30 fps。晴天可按曝光选择 ND，照片可用 AEB 保留高光。',
    }.get(light, '锁定白平衡并检查高光曝光。')
    if weather_hour and (wind := weather_hour.gust) is not None:
        camera += f'预报阵风 {wind:g} 米/秒，盯住画面稳定性和返航余量。'
    elif weather_hour and (wind := weather_hour.wind) is not None:
        camera += f'预报风速 {wind:g} 米/秒，注意画面稳定性。'
    high, far = flight_metrics(route)
    notes = [f'本航线最高 {high:.0f} 米、距起飞点最远 {far:.0f} 米，现场核对净空与视距。']
    if light == '夜景':
        notes.append('夜间视觉避障受限，现场核对照明与障碍。')
    if set(scene.tags) & {'江河', '湖面', '桥梁'}:
        notes.append('临水画面留足返航电量与抗风余量。')
    if source_kind in ('derived', 'poi'):
        notes.append('起飞点为几何与光线推演，到场请先确认起降净空与人群安全。')
    return why, camera, tuple(notes)


def direction_score(point, axis, when, silhouette):
    elevation, azimuth = sun_position(point, when)
    if elevation < -6:
        return 0.0
    heading = (axis + 270) % 360  # camera looks back toward the subject
    desired = heading if silhouette else (heading + 180) % 360
    angle = abs((azimuth - desired + 180) % 360 - 180)
    return 100 * (1 - angle/180)


def hard_filter(route, scenes):
    points = route_points(route)
    if any(distance(route.takeoff, p) > 480 for p in points):
        return False
    if any(w.altitude_m > min(120, ROUTE_LIMITS[route.category][1]) for w in route.waypoints):
        return False
    project, _ = local_metres(route.takeoff)
    line = LineString([project(*point) for point in points])
    return all(line.distance(Point(project(*s.point))) > s.avoid for s in scenes if s.avoid)


def time_windows(route, scene, topics, first, last, forecast, check, ceiling, *, origin=None,
                 fallback_category='轻型', mission_kind=None, earliest=False, overnight=False,
                 options_at=None):
    lights = set(topics).intersection((*LIGHT, '日间'))
    if not lights and '日出日落' in topics:
        lights.add('日落')
    if not lights and '商圈夜景' in topics:
        lights.add('夜景')
    skyline = bool(set(scene.tags) & {'城市天际线', '高楼'})
    position = route_center(FlightQuery((),'route',25,route.start,route.end,0,route.category,route=route)) if route else scene.point
    duration = route.end - route.start if route else timedelta(minutes=1)
    distance_score = max(0.,100 - distance(scene.point,origin)/150) if origin else 70.
    category = route.category if route else fallback_category
    times = set()
    cursor = first.replace(minute=0, second=0, microsecond=0)
    while cursor < last:
        times.add(max(first, cursor))
        cursor += timedelta(hours=1)
    if lights or skyline:
        day = first.replace(hour=0, minute=0, second=0, microsecond=0)
        while day < last:
            events = solar_events(position, day)
            for light in sorted((lights - {'日间'}) or {'日落'}):
                event = events.get('rise' if light == '日出' else 'set')
                if event:
                    offset = {'夜景': 30, '日出': 12, '日落': -18}[light]
                    times.add(event[0] + timedelta(minutes=offset))
            day += timedelta(days=1)
    chosen = []
    subjects = set(topics).difference((*LIGHT, '日间', '日出日落'))
    topic_score = 100 * len(set(scene.tags) & subjects)/len(subjects) if subjects else 100
    for start in sorted(times):
        if start < first or start + duration > last:
            continue
        elevation, azimuth = sun_position(position, start+duration/2)
        light = ('夜景' if elevation < -1 else
                 ('日出' if azimuth < 180 else '日落') if elevation <= 10 else '日间')
        if lights and light not in lights:
            continue
        if light == '夜景' and not overnight and not 16 <= start.hour < 23:
            continue
        kind = 'route' if route else 'circle'
        query = FlightQuery(() if route else (scene.point,), kind,25,start,start+duration,0,category,
                            demo=False,route=route)
        quality = score_photo(query, position, forecast, start, ceiling,
                              light if light in LIGHT else '日间清晰',
                              scene.point if route else None, sample_route=route)
        weather = quality.forecast
        score = quality.score
        terms = quality.components
        timing = .55*topic_score + .35*quality.light + .10*distance_score
        hour = next((hour for _, hour, _, _ in weather.rows if hour is not None), None)
        fields = []
        if hour:
            fields.append(hour.condition)
            if hour.gust is not None:
                fields.append(f'阵风 {hour.gust:g} 米/秒')
            if hour.visibility_km is not None:
                fields.append(f'能见度 {hour.visibility_km:g} 公里')
            if hour.rain_probability is not None:
                fields.append(f'降水 {hour.rain_probability:.0%}')
        chosen.append((query, score, terms, weather.hazard or weather.status,
                       timing, ' · '.join(filter(None, fields)), bool(weather.hazard), light, quality.reasons))
    chosen.sort(key=lambda w: (w[6], w[1] is None, w[0].start.date() if earliest else first.date(),
                               -(w[1] if w[1] is not None else w[4]), w[0].start))
    best = []
    for remaining in (set(lights), set()):
        for query, score, terms, status, timing, summary, unsuitable, light, reasons in chosen:
            if remaining and light not in remaining:
                continue
            if any(abs((query.start - w.query.start).total_seconds()) < 3600 for w in best):
                continue
            if options_at:
                result = options_at(query.start, light, forecast, last)
                if result:
                    best.append(result)
                else:
                    continue
            else:
                verdict = check(query) if route else Verdict.CHECK
                if verdict == Verdict.RESTRICTED:
                    continue
                status = status or ('拍摄时段参考 · 请选起飞点' if route is None else '')
                middle = route.waypoints[len(route.waypoints)//2].camera_deg if route else None
                best.append(Window(query,verdict,score,terms,status,timing,summary,unsuitable,light,middle,reasons))
            if remaining:
                remaining.discard(light)
                if not remaining:
                    break
            if len(best) == 2:
                break
        if len(best) == 2:
            break
    return tuple(best)


def _variant_window(scene, route, light, forecast, ceiling, verdict, style, source, preference):
    start = route.start
    query = FlightQuery((), 'route', 25, start, route.end, 0, route.category, demo=False, route=route)
    quality = score_photo(query, route_center(query), forecast, start, ceiling,
                          light if light in LIGHT else '日间清晰', scene.point, sample_route=route)
    weather = quality.forecast
    hour = next((item for _, item, _, _ in weather.rows if item is not None), None)
    why, advice, notes = build_director_brief(scene, route, start, light, hour, style, source)
    if preference.avoid_crowds or style == 'panoramic':
        notes += ('沿外围取景，遇到人群进入起降区或航线下方时先暂停。',)
    summary = hour.condition if hour else ''
    if hour and hour.gust is not None:
        summary += f' · 阵风 {hour.gust:g} 米/秒'
    return Window(query, verdict, quality.score, quality.components, weather.hazard or weather.status,
                  quality.light, summary, bool(weather.hazard), light,
                  route.waypoints[len(route.waypoints)//2].camera_deg, quality.reasons,
                  why_recommended=why, camera_advice=advice, safety_notes=notes, source_kind=source,
                  style_key=style)


def _style_options(scene, scenes, takeoffs, first, category, mission_kind, preference, check, ceiling, stop_minutes):
    styles = ('cinematic', 'panoramic', 'repeat' if mission_kind == 'repeat' else 'quick')
    requested = preference.style if preference.style in styles else styles[0]
    source = scene.source_kind or ('reference' if scene.takeoffs else 'derived')

    @lru_cache(maxsize=32)
    def template(takeoff, track, style):
        candidate = _build_style(scene, takeoff, track, first, category, style, mission_kind, preference)
        if hard_filter(candidate, scenes):
            return candidate
        return None

    def options_at(start, light, forecast, last):
        best = None
        for takeoff in takeoffs:
            variants = []
            track = best_track(scene, takeoff, start, light)
            for style in styles:
                for alignment in dict.fromkeys((track, *scene.tracks)):
                    route = template(takeoff, alignment, style)
                    if not route:
                        continue
                    route = route.shifted(start)
                    if route.end > last or (stop_minutes is not None and route.end - start > timedelta(minutes=stop_minutes)):
                        continue
                    query = FlightQuery((), 'route', 25, start, route.end, 0, category, demo=False, route=route)
                    verdict = check(query)
                    if verdict == Verdict.RESTRICTED:
                        continue
                    window = _variant_window(scene, route, light, forecast, ceiling, verdict, style, source, preference)
                    variants.append(StyleVariant(style, STYLE_LABELS[style], window.why_recommended,
                                                 STYLE_LABELS[style], route, window))
                    break
            if variants:
                selected = next((item for item in variants if item.key == requested),
                                min(variants, key=lambda item: item.route.end))
                window = replace(selected.window, variants=tuple(variants))
                if len(variants) == len(styles) and selected.key == requested:
                    return window
                if best is None or (selected.key == requested, len(variants)) > (best.style_key == requested, len(best.variants)):
                    best = window
        return best

    return options_at


def _target_time(scene, first, last, lights):
    if not lights or lights[0] not in LIGHT:
        return first
    light = lights[0]
    event_name = 'rise' if light == '日出' else 'set'
    delay = {'日出': 12, '日落': -18, '夜景': 30}[light]
    for offset in range((last - first).days + 2):
        event = solar_events(scene.point, first + timedelta(days=offset)).get(event_name)
        if event:
            when = event[0] + timedelta(minutes=delay)
            if first <= when < last:
                return when
    return first


def recommend(city, topics, first, last, category, origin, weather, check, ceiling, *, map_weather=map,
              mission_kind=None, earliest=False, stop_minutes=None, overnight=False,
              coverage_alternatives=False, location_hint='', extra_scenes=(), style='auto', preference=None):
    if last <= first or last - first > timedelta(days=7):
        return ()
    extra_scenes = tuple(extra_scenes)
    scenes = (*load_scenes(city), *extra_scenes)
    preference = preference or parse_preferences(location_hint, mission_kind, topics, style)
    if (mission_kind == 'travel' and stop_minutes is not None and stop_minutes <= 2
            and style == 'auto' and preference.style == 'cinematic'
            and not any(word in location_hint for word in STYLE_WORDS['cinematic'])):
        preference = replace(preference, style='quick')
    if preference.lights or preference.subjects:
        topics = tuple(dict.fromkeys((*topics, *preference.subjects, *preference.lights)))
    named = extra_scenes or matching_place(scenes, location_hint or preference.place_query)
    focus = set(topics).difference((*LIGHT, '日间', '日出日落'))
    fallback = {'商圈夜景','城市天际线','高楼'} if set(topics) == {'夜景'} else {'日出日落'}
    candidates = list(named) if named else [scene for scene in scenes if not topics or set(topics) == {'日间'} or set(scene.tags) & (focus or fallback)]
    candidates.sort(key=lambda s:(-len(set(s.tags)&(focus or fallback)),not s.takeoffs,
                                  distance(s.point,origin) if origin else 0,s.name))
    candidates_with_routes = []
    for scene in candidates[:8]:
        lights = tuple(t for t in topics if t in LIGHT)
        if not lights and '日出日落' in topics:
            lights = ('日落',)
        target = _target_time(scene, first, last, lights)
        takeoffs = tuple(sorted(scene.takeoffs, key=lambda p: distance(origin or scene.point, p)))
        if not takeoffs:
            takeoffs = derive_takeoff_candidates(scene, target, lights, check, category, scenes)
        options = _style_options(scene, scenes, takeoffs, first, category, mission_kind, preference,
                                 check, ceiling, stop_minutes)
        # Use the shortest geometry for time eligibility. Each variant is checked and scored at its actual duration.
        route = (_build_style(scene, takeoffs[0], best_track(scene, takeoffs[0], target, lights[0] if lights else '日间'),
                              first, category, 'quick', mission_kind, preference) if takeoffs else None)
        query = FlightQuery((), 'route', 25, first, route.end, 0, category, demo=False, route=route) if route else None
        candidates_with_routes.append((scene, options, route, route_center(query) if query else scene.point))
    forecasts = map_weather(weather, (row[3] for row in candidates_with_routes))
    proposals = []
    for (scene, options, route, _), forecast in zip(candidates_with_routes, forecasts):
        settings = dict(origin=origin, fallback_category=category, mission_kind=mission_kind,
                        earliest=earliest, overnight=overnight)
        windows = time_windows(route, scene, topics, first, last, forecast, check, ceiling,
                               options_at=options if route else None, **settings)
        if windows and route and coverage_alternatives and forecast.coverage and not any(w.score is not None for w in windows):
            earliest_covered = max(forecast.coverage[0], forecast.fetched + timedelta(minutes=15))
            latest_covered = min(forecast.coverage[1], earliest_covered + timedelta(days=3))
            if latest_covered > earliest_covered:
                covered = time_windows(route, scene, topics, earliest_covered, latest_covered, forecast,
                                       check, ceiling, options_at=options, **settings)
                windows += tuple(replace(w, alternative_date=True) for w in covered if w.score is not None
                                 and not first <= w.query.start < last)[:2]
        if not windows and route:
            windows = time_windows(None, scene, topics, first, last, forecast, check, ceiling, **settings)
        if windows:
            source = scene.source_kind or ('reference' if scene.takeoffs else 'derived')
            windows = tuple(replace(w, source_kind=source) for w in windows)
            proposals.append(Proposal(scene, STYLE_LABELS.get(windows[0].style_key, '地点推荐'), windows))
    proposals.sort(key=lambda p:(p.windows[0].unsuitable,p.windows[0].query.route is None,
                                  p.windows[0].score is None,
                                  not p.scene.reference_path if p.windows[0].score is None else False,
                                  -(p.windows[0].score if p.windows[0].score is not None else p.windows[0].timing),
                                  p.scene.name))
    final = []
    for proposal in proposals:
        if all(distance(proposal.scene.point, previous.scene.point) > 350 for previous in final):
            final.append(proposal)
        if len(final) == 3:
            break
    return tuple(final)
