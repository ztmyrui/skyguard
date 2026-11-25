"""Deterministic, local civil exercise models and replay."""
from dataclasses import dataclass, field, replace
from datetime import date, time
from math import asin, atan2, cos, degrees, dist, radians, sin, sqrt

Point = tuple[float, float]
DURATION = 120
WEATHERS = ('微风晴好', '阵风 5~6 级', '微雨低能见度')
CITY_EVENT_TEMPLATES = {
    'show': ('大型演出 / 灯光秀', '演出人群区', 150),
    'school': ('学校放学 / 考点静音', '校园及周边区', 140),
    'shopping': ('商圈周末人流', '商圈广场人群区', 160),
    'park': ('公园亲子游览', '公园游览核心区', 150),
    'race': ('沿江 / 马拉松赛事', '赛事赛道核心区', 170),
    'residential': ('居民区夜间防扰民', '居民生活静音区', 140),
    'transit': ('交通枢纽 / 主干道', '枢纽通行核心区', 150),
    'office': ('写字楼 / 产业园区', '楼宇办公核心区', 140),
}
LANDING_TYPES = {'park_lawn': '公园草坪', 'plaza': '空旷广场', 'parking': '露天停车场',
                 'rooftop': '体育场 / 开阔空地'}
DIRECTOR_ACTIONS = {'airdrop': '空降违规机', 'gust': '突发阵风', 'battery': '触发低电',
                    'blocked': '人员受阻 25s'}
DRONE_TYPES = {
    'camera': ('消费级多旋翼', 7, 65, True),
    'fpv': ('穿越机（FPV）', 16, 50, False),
    'logistics': ('城市物流无人机', 11, 85, True),
    'inspection': ('行业巡检无人机', 6, 70, True),
    'custom': ('未报备自组航空器', 9, 60, False),
}
EMERGENCY_TYPES = {
    'wind_drift': '阵风致航线偏移',
    'altitude_exceed': '超真高限制（>120 m）',
    'crowd_intrusion': '逼近密集人群区域',
    'signal_occlusion': '楼宇遮挡信号丢失',
    'low_battery': '低电量/链路异常紧急备降',
    'nuisance_hover': '低空滞留扰民',
}
STAFF_ROLES = {
    'foot': ('网格徒步巡查员', 2.2, 120, '巡查员'),
    'vehicle': ('移动巡逻处置员', 6.0, 150, '机动员'),
    'guard': ('定点值守安保员', 0, 150, '安保员'),
    'recovery': ('应急备降保障员', 2.5, 90, '保障员'),
}


def verification_radius(role, weather):
    """Effective on-site verification range used by the replay."""
    radius = STAFF_ROLES['guard'][2] if role == 'guard' else 24
    return radius * (0.85 if weather == WEATHERS[2] else 1)


def within_city_radius(city_center, point, max_km=15.0):
    if not -180 <= point[0] <= 180 or not -90 <= point[1] <= 90:
        return False, float('inf')
    lon, lat = map(radians, city_center)
    x, y = map(radians, point)
    h = sin((y - lat) / 2)**2 + cos(lat) * cos(y) * sin((x - lon) / 2)**2
    km = 12742 * asin(sqrt(min(1, h)))
    return km <= max_km, km


def offset(point, east, north):
    return (point[0] + east / (111195 * cos(radians(point[1]))), point[1] + north / 111195)


def metres(a, b):
    return dist((0, 0), ((b[0]-a[0]) * 111195 * cos(radians((a[1]+b[1])/2)),
                         (b[1]-a[1]) * 111195))


def advance(point, destination, distance):
    span = metres(point, destination)
    if span <= distance:
        return destination
    ratio = distance / span
    return tuple(a + (b-a)*ratio for a, b in zip(point, destination))


@dataclass(frozen=True)
class SandboxSite:
    id: str
    kind: str
    lon: float
    lat: float
    radius_m: int = 150
    template: str = 'park'
    landing_kind: str = 'park_lawn'

    @property
    def point(self):
        return self.lon, self.lat

    @property
    def name(self):
        if self.kind == 'event':
            return CITY_EVENT_TEMPLATES[self.template][1]
        return f'应急降落 · {LANDING_TYPES[self.landing_kind]}'


@dataclass(frozen=True)
class SandboxEmergency:
    kind: str
    trigger_s: int


@dataclass(frozen=True)
class SandboxDrone:
    id: str
    name: str
    drone_type: str = 'camera'
    filed: bool = True
    altitude_m: int = 65
    start_delay_s: int = 0
    waypoints: tuple[Point, ...] = ()
    emergencies: tuple[SandboxEmergency, ...] = ()


@dataclass(frozen=True)
class SandboxStaff:
    id: str
    name: str
    role: str
    lon: float
    lat: float
    custom_routes: tuple[tuple[Point, ...], ...] = ()

    @property
    def point(self):
        return self.lon, self.lat


@dataclass(frozen=True)
class SandboxStrategy:
    name: str = '当前策略'
    mode: str = 'auto'
    priority: str = 'crowd_first'
    allow_reassign: bool = True
    pre_alert: bool = True


@dataclass(frozen=True)
class DirectorCue:
    action: str
    at: int
    targets: tuple[str, ...] = ()

    @property
    def incident_count(self):
        return 1 if self.action in ('airdrop', 'blocked') else len(self.targets)


@dataclass(frozen=True)
class SandboxConfig:
    city: str
    center: Point
    date_str: str = '2026-11-24'
    time_str: str = '17:30'
    weather: str = WEATHERS[0]
    sites: tuple[SandboxSite, ...] = ()
    drones: tuple[SandboxDrone, ...] = ()
    staff: tuple[SandboxStaff, ...] = ()
    strategy: SandboxStrategy = field(default_factory=SandboxStrategy)
    cues: tuple[DirectorCue, ...] = ()

    def validate(self, *, draft=False):
        date.fromisoformat(self.date_str)
        time.fromisoformat(self.time_str)
        if self.weather not in WEATHERS:
            raise ValueError('请选择有效天气')
        if self.strategy.mode not in ('auto', 'confirm') or self.strategy.priority not in (
                'crowd_first', 'fifo', 'urgent_first'):
            raise ValueError('请选择有效的处置策略')
        for rows in (self.sites, self.drones, self.staff):
            if len({row.id for row in rows}) != len(rows):
                raise ValueError('同类对象的编号必须唯一')
        for kind in ('event', 'lawn'):
            count = sum(site.kind == kind for site in self.sites)
            if count > 2 or (kind == 'event' and not draft and count < 1):
                raise ValueError('请设置一至两个主要事件，应急降落点最多两个')
        for site in self.sites:
            if (site.kind not in ('event', 'lawn') or site.template not in CITY_EVENT_TEMPLATES
                    or site.landing_kind not in LANDING_TYPES or not 80 <= site.radius_m <= 350):
                raise ValueError('场地半径须在 80–350 米内')
        if any(cue.action not in DIRECTOR_ACTIONS or not 0 <= cue.at <= DURATION for cue in self.cues):
            raise ValueError('请检查临场事件及触发时间')
        for drone in self.drones:
            if drone.drone_type not in DRONE_TYPES or not 30 <= drone.altitude_m <= 150 or not 0 <= drone.start_delay_s <= 60:
                raise ValueError('请检查无人机机型、相对真高与起飞延迟')
            if not (0 if draft else 2) <= len(drone.waypoints) <= 8:
                raise ValueError(f'{drone.name}需要 2–8 个航点')
            if len(drone.emergencies) > 2 or any(e.kind not in EMERGENCY_TYPES or not 0 <= e.trigger_s <= DURATION
                                                for e in drone.emergencies):
                raise ValueError('每架无人机最多设置两项突发，触发时间须在 0–120 秒内')
        if not draft and not self.drones:
            raise ValueError('请先添加至少一架无人机')
        if any(person.role not in STAFF_ROLES for person in self.staff):
            raise ValueError('请选择有效的人员角色')
        points = [site.point for site in self.sites] + [person.point for person in self.staff]
        points.extend(point for drone in self.drones for point in drone.waypoints)
        if any(not within_city_radius(self.center, point)[0] for point in points):
            raise ValueError('点位须在市中心 15 公里内')


def local_anchor(config, anchor):
    # Reserve room for generated routes even when the selected view is near the edge.
    return advance(config.center, anchor or config.center, 13700)


def default_sites(config, anchor=None, template='school', radius=None):
    base = anchor or config.center
    if not within_city_radius(config.center, base)[0]:
        base = local_anchor(config, base)
    landing = offset(base, 245, -140)
    if not within_city_radius(config.center, landing)[0]:
        landing = advance(base, config.center, 285)
    return (SandboxSite('E1', 'event', *base, radius or CITY_EVENT_TEMPLATES[template][2], template),
            SandboxSite('L1', 'lawn', *landing, 100))


def typical_drones(config, anchor=None, variant=0):
    site = next((site for site in config.sites if site.kind == 'event'), None)
    base = site.point if site else local_anchor(config, anchor)
    template = site.template if site else 'park'
    hour = time.fromisoformat(config.time_str).hour
    variant %= 4
    crowd = 'nuisance_hover' if template == 'residential' and (hour >= 19 or hour < 6) else 'crowd_intrusion'
    peak = template == 'school' and 16 <= hour <= 18
    secondary = ('wind_drift', 'altitude_exceed', 'nuisance_hover', 'signal_occlusion')[variant]
    if template in ('office', 'shopping'):
        secondary = 'signal_occlusion'
    if config.weather == WEATHERS[1]:
        secondary = 'wind_drift'
    landing = any(site.kind == 'lawn' for site in config.sites)
    primary = 'altitude_exceed' if template in ('office', 'shopping') and secondary == 'signal_occlusion' else crowd
    specifications = (
        (('fpv', 'camera', 'custom', 'fpv')[variant], (SandboxEmergency(primary, 10 if peak else 12+variant),)),
        (('camera', 'inspection', 'camera', 'logistics')[variant], (SandboxEmergency(secondary, 34+variant),)),
        (('logistics', 'custom', 'inspection', 'camera')[variant],
         (SandboxEmergency('low_battery', 23+variant),) if landing or variant % 2 == 0 else ()),
    )
    drones = []
    for i, (kind, events) in enumerate(specifications):
        angle = 45 + variant * 83 + i * 113
        bends = ((330+i*15, 0), (180+i*20, 30), (235, 105), (420-i*20, 155))
        route = []
        for radius, turn in (bends if (variant+i) % 2 == 0 else bends[:3]):
            bearing = radians(angle+turn)
            for rotation in (0, 90, -90, 180):
                direction = bearing+radians(rotation)
                point = offset(base, radius*sin(direction), radius*cos(direction))
                if within_city_radius(config.center, point)[0]:
                    route.append(point)
                    break
        name, _, altitude, filed = DRONE_TYPES[kind]
        drones.append(SandboxDrone(f'D{i+1:02}', name, kind, filed, altitude, i*3, tuple(route), events))
    return tuple(drones)


def suggest_staffing(config):
    events = [site for site in config.sites if site.kind == 'event']
    lawns = [site for site in config.sites if site.kind == 'lawn']
    center = events[0].point if events else config.center
    approaching = []
    low = []
    for drone in config.drones:
        if not drone.waypoints:
            continue
        for event in drone.emergencies:
            point = point_on_route(drone.waypoints, max(0, event.trigger_s-drone.start_delay_s)
                                   * DRONE_TYPES[drone.drone_type][1])
            (low if event.kind == 'low_battery' else approaching).append(point)
    perimeter = approaching[0] if approaching else offset(center, 190, 60)
    hour = time.fromisoformat(config.time_str).hour
    edge = 55 if hour >= 19 or hour < 6 or config.weather == WEATHERS[2] else 80
    assignments = [('guard', offset(center, -edge, 0)), ('vehicle', advance(center, perimeter, 220))]
    reason = ['在核心区边缘安排值守员', '让机动员靠近可能出现异常的航线']
    if low and lawns:
        lawn = min(lawns, key=lambda site: min(metres(site.point, point) for point in low))
        assignments.append(('recovery', lawn.point))
        reason.append('让接应员守在附近的应急降落点')
    if len(events) > 1 or config.weather == WEATHERS[1]:
        assignments.append(('foot', events[-1].point if len(events)>1 else advance(center, perimeter, 125)))
        reason.append('增设步行巡查员补足现场观察')
    staff = [SandboxStaff(f'P{i:02}', f'{STAFF_ROLES[role][3]} {i}', role,
                          *(point if within_city_radius(config.center, point)[0] else center))
             for i, (role, point) in enumerate(assignments, 1)]
    return staff, '，'.join(reason) + '。'


def point_on_route(points, distance):
    for start, end in zip(points, points[1:]):
        length = metres(start, end)
        if distance < length:
            return advance(start, end, distance)
        distance -= length
    return points[-1]


@dataclass(frozen=True)
class StrategyMetrics:
    name: str
    resolved_count: int
    total_incidents: int
    avg_first_response_s: float | None
    avg_arrival_s: float | None
    exposure_seconds: int
    unmet_dispatches: int
    staff_count: int
    total_patrol_m: float
    score: float
    suggestions: tuple[str, ...]


def compare_strategies(results):
    rows = sorted(results, key=lambda row: (-row.score, row.exposure_seconds, row.staff_count))
    if not rows:
        return {'best': None, 'conclusion': '请先保存要比较的方案。'}
    best = rows[0]
    conclusion = f'「{best.name}」综合评分最高，为 {best.score:g} 分。'
    if len(rows) > 1:
        other = rows[1]
        delta = other.exposure_seconds - best.exposure_seconds
        relation = f'少 {delta} 秒' if delta > 0 else f'多 {-delta} 秒' if delta < 0 else '相同'
        conclusion = f'与「{other.name}」相比，人群暴露时长{relation}，投入 {best.staff_count} 人。'
    return {'best': best.name, 'conclusion': conclusion}


def quick_example(config):
    anchor = offset(config.center, -1450, 320) if config.city == 'changsha' else config.center
    seeded = replace(config, sites=default_sites(config, anchor), drones=(), staff=())
    seeded = replace(seeded, drones=typical_drones(seeded))
    return replace(seeded, staff=tuple(suggest_staffing(seeded)[0]))


@dataclass
class _Flight:
    drone: SandboxDrone
    point: Point
    height: int
    progress: float = 0
    heading: float = 0
    landed: bool = False
    withdrawn: bool = False
    occluded_at: int = -100


@dataclass
class _Incident:
    id: str
    flight: _Flight
    kind: str
    start: int
    site: SandboxSite | None = None
    first: int | None = None
    arrival: int | None = None
    resolved: bool = False
    expired: bool = False
    declined: bool = False
    unmet: bool = False
    reminder_at: int | None = None
    changed_at: int | None = None
    ready_since: int | None = None
    peripheral: bool = False
    sudden_gust: bool = False


@dataclass
class _Patrol:
    staff: SandboxStaff
    point: Point
    incident: _Incident | None = None
    route: list[Point] = field(default_factory=list)
    moved: float = 0
    redirected: bool = False
    blocked_until: int = 0


class SandboxSimulation:
    """Calculate 121 one-second snapshots. Decisions are replayed at their recorded time."""

    def __init__(self, config, *, assumed_confirmations=False):
        config.validate()
        self.config = config
        self.assumed_confirmations = assumed_confirmations
        self.decisions = {}
        self.live_cues = []
        self.frames = []
        self.metrics = None

    def frame(self, at):
        if not self.frames:
            self._run()
        return self.frames[max(0, min(DURATION, int(at)))]

    def layers(self, at):
        frame = self.frame(at)
        return {key: frame[key] for key in ('aircraft', 'active_highlights', 'staff_markers', 'civic_links',
                                             'attention_cards', 'feed')} | dict(civic_replay=True, zones=[], routes=[])

    def decide(self, at, incident_id, action):
        at = max(0, min(DURATION, int(at)))
        card = next((card for card in self.frame(at)['attention_cards'] if card['id'] == incident_id), None)
        if not card or action not in card['actions']:
            return False
        self.decisions = {key: value for key, value in self.decisions.items() if key[0] <= at}
        self.decisions[at, incident_id] = action
        self.frames.clear()
        return True

    def evaluate(self):
        self.frame(DURATION)
        return self.metrics

    @property
    def cues(self):
        return self.config.cues + tuple(self.live_cues)

    def inject(self, action, at):
        if action not in DIRECTOR_ACTIONS:
            raise ValueError('未知临场事件')
        at = max(0, min(DURATION, int(at)))
        frame = self.frame(at)
        flying = [row for row in frame['aircraft'] if row['state'] != '已降落']
        if action == 'gust':
            targets = tuple(row['id'] for row in flying if row['drone_type'] in ('camera', 'fpv', 'custom'))
        elif action == 'battery':
            targets = tuple(row['id'] for row in flying[:1])
        elif action == 'blocked':
            staff = sorted((row for row in frame['staff_markers'] if not row['blocked_s']),
                           key=lambda row: row['status'] == '待命')
            targets = tuple(row['id'] for row in staff[:1])
        else:
            targets = ()
        if not targets and action != 'airdrop':
            return False
        self.live_cues.append(DirectorCue(action, at, targets))
        self.frames.clear()
        self.frame(at)
        return True

    def solidify(self):
        return replace(self.config, cues=self.cues)

    def _apply_cue(self, cue, index, flights):
        at = cue.at
        point = self.events[0].point
        if cue.action == 'airdrop':
            start = offset(point, -235, 100)
            if not within_city_radius(self.config.center, start)[0]:
                start = advance(point, self.config.center, 255)
            drone = SandboxDrone(f'C{index+1:02}', '临场未报备航拍机', 'custom', False, 55, at, (start, point))
            flight = _Flight(drone, start, drone.altitude_m)
            flights.append(flight)
            point = start
            self._start(flight, f'C{index}', SandboxEmergency('crowd_intrusion', at), at)
        elif cue.action == 'blocked':
            patrol = next((row for row in self.patrols if row.staff.id in cue.targets), None)
            if patrol is None:
                patrol = next((row for row in self.patrols if row.blocked_until <= at), None)
            if patrol:
                point = patrol.point
                patrol.blocked_until = at + 25
                self.staff_incidents.append((patrol.blocked_until, patrol.staff.id))
                if patrol.incident and patrol.incident.arrival is None and self.config.strategy.allow_reassign:
                    patrol.incident.ready_since = None
                    patrol.incident = None
                    patrol.route.clear()
        else:
            kind = 'wind_drift' if cue.action == 'gust' else 'low_battery'
            for flight in flights:
                if flight.drone.id in cue.targets and not flight.landed:
                    point = flight.point
                    self._start(flight, f'C{index}', SandboxEmergency(kind, at), at, sudden_gust=cue.action == 'gust')
        self._log(at, DIRECTOR_ACTIONS[cue.action], '临场', point)

    def _log(self, at, text, badge='突发', point=None):
        self.feed.append({'at': at, 'text': text, 'badge': badge, 'point': point})

    def _resolve(self, incident, at, text):
        incident.resolved = True
        self._log(at, f'{incident.flight.drone.name} · {text}', '化解', incident.flight.point)

    def _start(self, flight, index, spec, at, *, sudden_gust=False):
        kind = spec.kind
        sites = self.lawns if kind == 'low_battery' else self.events
        site = min(sites, key=lambda site: metres(site.point, flight.point), default=None)
        peripheral = kind == 'low_battery' and site is None
        if peripheral:
            base = self.events[0].point
            candidates = [offset(base, radius*cos(radians(angle)), radius*sin(radians(angle)))
                          for radius in (220, 360, 520, 750) for angle in range(0, 360, 30)]
            point = min((point for point in candidates if within_city_radius(self.config.center, point)[0]
                         and all(metres(point, event.point) >= event.radius_m+60 for event in self.events)),
                        key=lambda point: metres(point, flight.point))
            site = SandboxSite('peripheral', 'lawn', *point, 80)
        incident = _Incident(f'{flight.drone.id}:{index}', flight, kind, at, site, peripheral=peripheral,
                             sudden_gust=sudden_gust)
        self.incidents.append(incident)
        self._log(at, f'{flight.drone.name} · {EMERGENCY_TYPES[kind]}', '突发', flight.point)
        if kind == 'signal_occlusion':
            flight.occluded_at = at
        return incident

    def _priority(self, incident):
        kind = incident.kind
        site = incident.site
        focused = kind in ('crowd_intrusion', 'nuisance_hover')
        elevated = focused and site and ((site.template == 'school' and self.school_peak) or
                                        (site.template in ('show', 'residential') and self.night))
        policy = self.config.strategy.priority
        if policy == 'fifo':
            rank = 0
        elif policy == 'urgent_first':
            rank = 4 if kind == 'low_battery' else 3 if incident.flight.drone.drone_type == 'fpv' else 1
        else:
            rank = 4 if elevated else 3 if focused else 2 if kind == 'low_battery' else 1
        return -rank, incident.start, incident.id

    def _destination(self, incident):
        if incident.site and incident.kind in ('low_battery', 'crowd_intrusion', 'nuisance_hover'):
            return incident.site.point
        return incident.flight.point

    def _coverage(self, patrol):
        # A fixed post uses its observation radius. Mobile staff approach the scene.
        return verification_radius(patrol.staff.role, self.config.weather)

    def _candidate(self, incident, at):
        destination = self._destination(incident)
        choices = []
        for patrol in self.patrols:
            role = patrol.staff.role
            low = incident.kind == 'low_battery'
            if patrol.blocked_until > at or (low and role == 'guard'):
                continue
            current = patrol.incident
            redirect = bool(current and not current.resolved and not current.expired)
            if redirect and (current.arrival is not None or not self.config.strategy.allow_reassign
                             or self._priority(incident)[0] >= self._priority(current)[0]):
                continue
            gap = max(0, metres(patrol.point, destination) - self._coverage(patrol))
            speed = STAFF_ROLES[role][1]
            if gap and not speed:
                continue
            fallback = (role != 'recovery') if low else (role == 'recovery')
            choices.append((redirect, fallback, gap / speed if gap else 0, patrol.staff.id, patrol))
        if not choices:
            return None, False
        redirect, _, _, _, patrol = min(choices)
        return patrol, redirect

    def _dispatch(self, at):
        pending = {}
        assigned = {patrol.incident.id for patrol in self.patrols if patrol.incident}
        for incident in sorted(self.incidents, key=self._priority):
            if at < incident.start + (2 if self.config.strategy.pre_alert else 4):
                continue
            if incident.first is None:
                incident.first = at
                drone = incident.flight.drone
                electronic = (incident.kind == 'crowd_intrusion' or
                              drone.filed and incident.kind in ('wind_drift', 'altitude_exceed'))
                if self.config.strategy.pre_alert and electronic:
                    incident.reminder_at = at
                    self._log(at, f'已向{drone.name}发出电子提醒', '提醒', incident.flight.point)
                else:
                    self._log(at, f'已发现{drone.name}的异常情况', '提醒', incident.flight.point)
            if (incident.resolved or incident.expired or incident.declined or incident.id in assigned
                    or incident.kind == 'signal_occlusion' or
                    (incident.reminder_at is not None and incident.kind in ('wind_drift', 'altitude_exceed'))):
                continue
            if incident.arrival is not None:
                continue
            patrol, redirect = self._candidate(incident, at)
            if patrol is None:
                incident.unmet = True
                continue
            choice = 'reassign' if redirect else 'approve'
            if self.config.strategy.mode == 'confirm' and not self.assumed_confirmations:
                action = self.decisions.get((at, incident.id))
                if action == 'decline':
                    incident.declined = True
                    self._log(at, '暂不派员，继续观察')
                    continue
                if action != choice:
                    pending[incident.id] = (choice, 'decline')
                    continue
            if patrol:
                if redirect:
                    patrol.incident.ready_since = None
                    assigned.discard(patrol.incident.id)
                patrol.incident = incident
                patrol.redirected = redirect
                target = self._destination(incident)
                cached = next((path for path in patrol.staff.custom_routes if len(path) >= 2
                               and metres(path[0], patrol.point) < 25 and metres(path[-1], target) < 40), ())
                patrol.route = list(cached[1:]) if cached else []
                assigned.add(incident.id)
                self._log(at, f'{patrol.staff.name}{"改去" if redirect else "前往"}核验 · {incident.flight.drone.name}',
                          '改派' if redirect else '派员', target)
        return pending

    def _move_staff(self, at):
        for patrol in self.patrols:
            event = patrol.incident
            if patrol.blocked_until > at:
                if event and event.arrival is None:
                    event.ready_since = None
                continue
            if not event or event.resolved or event.expired:
                patrol.incident = None
                continue
            target = self._destination(event)
            if metres(patrol.point, target) > self._coverage(patrol) or patrol.route:
                speed = STAFF_ROLES[patrol.staff.role][1]
                destination = patrol.route[0] if patrol.route else target
                next_point = advance(patrol.point, destination, speed)
                patrol.moved += metres(patrol.point, next_point)
                patrol.point = next_point
                if patrol.route and next_point == destination:
                    patrol.route.pop(0)
            if not patrol.route and metres(patrol.point, target) <= self._coverage(patrol) and event.arrival is None:
                if event.ready_since is None:
                    event.ready_since = at
                role = patrol.staff.role
                prep = 5 if role == 'guard' else 4
                if event.kind == 'low_battery':
                    prep += (0 if role == 'recovery' else 4) + (5 if event.peripheral else 0)
                if at - event.ready_since >= prep:
                    event.arrival = at
                    low = event.kind == 'low_battery'
                    if low:
                        event.changed_at = at
                    detail = '已设临时警戒，引导外围缓降' if event.peripheral else '已就位，引导应急降落' if low else '已到位'
                    self._log(at, f'{patrol.staff.name}{detail} · {event.flight.drone.name}',
                              '引导' if low else '派员', target)

    @staticmethod
    def _exit_point(event):
        start = event.flight.drone.waypoints[0]
        if not event.site or metres(start, event.site.point) > event.site.radius_m + 20:
            return start
        return offset(event.site.point, -(event.site.radius_m + 60), 0)

    def _move_aircraft(self, flight, at, events):
        drone = flight.drone
        age = at - flight.occluded_at
        speed = DRONE_TYPES[drone.drone_type][1]
        gust = self.config.weather == WEATHERS[1] and drone.drone_type in ('camera', 'custom')
        old = flight.point
        flight.progress += speed if at > drone.start_delay_s else 0
        target = old if flight.withdrawn else point_on_route(drone.waypoints, flight.progress)
        flight.height = min(drone.altitude_m, 120) if any(e.kind == "altitude_exceed" and e.resolved
                                                         for e in events) else drone.altitude_m
        active = [e for e in events if not e.resolved and not e.expired]
        kinds = {e.kind: e for e in active}
        hover = kinds.get('nuisance_hover')
        intrude = kinds.get('crowd_intrusion')
        low = kinds.get('low_battery')
        drift = kinds.get('wind_drift')
        height = kinds.get('altitude_exceed')
        if height:
            flight.height = 138
        if drift:
            displacement = 40 if drift.sudden_gust else 0
            growth = 7 if gust or drift.sudden_gust else 3
            target = offset(target, 0, min(250, displacement+(at-drift.start+1)*growth))
        if intrude and intrude.site:
            target = intrude.site.point if intrude.arrival is None else self._exit_point(intrude)
        if hover:
            flight.height = 35
            target = old if hover.arrival is None else self._exit_point(hover)
        if low:
            if low.changed_at is not None and low.site:
                target = low.site.point
                flight.height = max(0, min(drone.altitude_m, round(metres(old, target)/3)))
            elif gust:
                target = offset(target, 0, min(250, (at-low.start)*6))
        if flight.landed:
            flight.height = 0
        else:
            response_speed = speed * (0.65 if intrude and intrude.reminder_at is not None and
                                      intrude.arrival is None else 1)
            flight.point = advance(old, target, response_speed)
        moved = metres(old, flight.point)
        if moved > 0.01:
            flight.heading = degrees(atan2(flight.point[0]-old[0], flight.point[1]-old[1]))
        return moved, age

    def _update_incidents(self, flight, at, events):
        for event in events:
            if event.resolved or event.expired:
                continue
            elapsed = at - event.start
            if event.kind == 'signal_occlusion' and elapsed >= 20:
                self._resolve(event, at, '信号已恢复，继续跟踪')
            elif event.kind in ('wind_drift', 'altitude_exceed'):
                if ((event.reminder_at is not None and elapsed >= (10 if self.config.weather == WEATHERS[1] else 5))
                        or event.arrival is not None and at - event.arrival >= 3):
                    self._resolve(event, at, '已回到原航线' if event.kind == 'wind_drift' else '相对真高已降至 120 m 以内')
            elif event.kind in ('crowd_intrusion', 'nuisance_hover') and event.arrival is not None:
                if at - event.arrival >= 5 and (not event.site or metres(flight.point, event.site.point) > event.site.radius_m):
                    flight.withdrawn = True
                    self._resolve(event, at, '现场劝导后已离开')
            elif event.kind == 'low_battery':
                deadline = (43 if self.config.weather == WEATHERS[1] else 57) + (16 if event.peripheral else 12)
                if elapsed <= deadline and event.changed_at is not None and event.site and (
                        event.arrival is not None and metres(flight.point, event.site.point) <= 4):
                    flight.landed = True
                    flight.height = 0
                    self._resolve(event, at, '已在外围平稳降落' if event.peripheral else '已在应急点平稳降落')
                elif elapsed >= deadline:
                    event.expired = True
                    self._log(at, f'{flight.drone.name} · 接应未及时完成，仍有迫降风险', '突发', flight.point)

    def _snapshot(self, at, aircraft, pending):
        highlights, links, cards = [], [], []
        for event in self.incidents:
            flight = event.flight
            point = flight.point
            if event.reminder_at is not None and at - event.reminder_at < 4:
                origin = self.patrols[0].point if self.patrols else self.config.center
                links.append(dict(start=origin, end=point, label='已发提醒'))
            if event.resolved:
                continue
            site = event.site
            recovery = event.kind == 'low_battery' and event.changed_at is not None and not event.expired
            close = event.kind in ('crowd_intrusion', 'nuisance_hover') and site and metres(site.point, point) <= site.radius_m + 30
            if site and (recovery or close):
                highlights.append(dict(point=site.point, name='外围缓降区' if event.peripheral else site.name,
                                       radius=site.radius_m, recovery=recovery))
            if recovery and site:
                links.append(dict(start=point, end=site.point, label='安全引导', recovery=True))
            for patrol in self.patrols:
                if patrol.incident is event and event.arrival is not None:
                    links.append(dict(start=patrol.point, end=point, label='现场核验'))
            status = ('楼宇遮挡 · 等待重现' if event.kind == 'signal_occlusion' else
                      '接应未及时完成' if event.expired else '待确认' if event.id in pending else
                      '现场核验中' if event.arrival is not None else '持续关注')
            elevated = (event.kind == 'nuisance_hover' and site and site.template == 'residential' and self.night)
            cards.append(dict(id=event.id, title=flight.drone.name,
                              detail=('请优先现场劝离 · ' if elevated else '') + EMERGENCY_TYPES[event.kind],
                              status=status, point=point, actions=pending.get(event.id, ())))
        staff = []
        for patrol in self.patrols:
            event = patrol.incident
            target = self._destination(event) if event and not (event.expired or event.resolved) else None
            travelling = target is not None and event.arrival is None
            role = patrol.staff.role
            speed = STAFF_ROLES[role][1]
            path = [patrol.point, *patrol.route, target] if travelling else []
            distance = sum(metres(a, b) for a, b in zip(path, path[1:]))
            staff.append(dict(id=patrol.staff.id, name=patrol.staff.name, role=role,
                              lon=patrol.point[0], lat=patrol.point[1], route=path, target_point=target,
                              blocked_s=max(0, patrol.blocked_until-at),
                              status=f'临时受阻 · 剩余 {patrol.blocked_until-at}s' if patrol.blocked_until > at else
                              ('已改派' if patrol.redirected else '前往中') if travelling else
                              '现场核验' if target is not None else '待命',
                              eta_s=max(0, round((distance-self._coverage(patrol))/speed)) if travelling and speed else 0))
        return dict(at=at, aircraft=aircraft, active_highlights=highlights, staff_markers=staff,
                    civic_links=links, attention_cards=cards, feed=tuple(self.feed))

    def _run(self):
        config = self.config
        self.events = tuple(site for site in config.sites if site.kind == 'event')
        self.lawns = tuple(site for site in config.sites if site.kind == 'lawn')
        start = time.fromisoformat(config.time_str)
        self.school_peak = time(16, 30) <= start <= time(18, 30)
        self.night = start >= time(19) or start <= time(6)
        self.incidents = []
        self.staff_incidents = []
        self.feed = []
        self.patrols = [_Patrol(person, person.point) for person in config.staff]
        flights = [_Flight(drone, drone.waypoints[0], drone.altitude_m) for drone in config.drones]
        tails = {}
        exposure = 0
        for at in range(DURATION+1):
            for until, identifier in self.staff_incidents:
                if until == at:
                    patrol = next(row for row in self.patrols if row.staff.id == identifier)
                    self._log(at, f'{patrol.staff.name} · 临时受阻解除', '化解', patrol.point)
            for index, cue in enumerate(self.cues):
                if cue.at == at:
                    self._apply_cue(cue, index, flights)
            active = [flight for flight in flights if at >= flight.drone.start_delay_s]
            for flight in active:
                for index, spec in enumerate(flight.drone.emergencies):
                    if not flight.landed and at == max(flight.drone.start_delay_s, spec.trigger_s):
                        self._start(flight, index, spec, at)
            pending = self._dispatch(at)
            self._move_staff(at)
            aircraft = []
            for flight in active:
                events = [event for event in self.incidents if event.flight is flight]
                observed = flight.point
                moved, age = self._move_aircraft(flight, at, events)
                if 0 <= age < 20:
                    observed = next((row['lonlat'] for row in reversed(self.frames[-1]['aircraft'])
                                     if row['id'] == flight.drone.id), observed) if self.frames else observed
                else:
                    observed = flight.point
                self._update_incidents(flight, at, events)
                tail = tails.setdefault(flight.drone.id, [])
                tail.append(observed)
                del tail[:-11]
                unresolved = [e for e in events if not e.resolved]
                if at < DURATION and not flight.landed and any(metres(site.point, flight.point) <= site.radius_m
                        for site in self.events) and (unresolved or not flight.drone.filed):
                    exposure += 1
                aircraft.append(dict(id=flight.drone.id, name=flight.drone.name, drone_type=flight.drone.drone_type,
                                     lon=observed[0], lat=observed[1],
                                     lonlat=observed, bearing=flight.heading, heading=flight.heading,
                                     altitude=flight.height, altitude_m=flight.height, speed=moved,
                                     status=('楼宇遮挡 · 等待重现' if 0 <= age < 20 else
                                             '已安全降落' if flight.landed else '需要关注' if unresolved else
                                             '已离开核心区' if flight.withdrawn else '按航线飞行'),
                                     age=age+1 if 0 <= age < 20 else 0, observed=not 0 <= age < 20,
                                     attention=bool(unresolved), filed=flight.drone.filed,
                                     tag='楼宇遮挡 · 等待重现' if 0 <= age < 20 else
                                     '已安全降落' if flight.landed else '需要关注' if unresolved else '',
                                     label='楼宇遮挡 · 等待重现' if 0 <= age < 20 else flight.drone.name,
                                     state='失联' if 0 <= age < 20 else '已降落' if flight.landed else '飞行中',
                                     level=2 if unresolved else 1, civic=True,
                                     action='已安全降落' if flight.landed else
                                     '已离开核心区' if flight.withdrawn else '', tail=list(tail), prediction=[]))
            self.frames.append(self._snapshot(at, aircraft, pending))
        self.metrics = self._metrics(exposure)

    def _metrics(self, exposure):
        def mean(values):
            return round(sum(values)/len(values), 1) if values else None

        total = sum(len(drone.emergencies) for drone in self.config.drones) + sum(cue.incident_count for cue in self.cues)
        resolved = sum(event.resolved for event in self.incidents) + sum(until <= DURATION for until, _ in self.staff_incidents)
        unmet = sum(event.unmet for event in self.incidents)
        first = mean([event.first-event.start for event in self.incidents if event.first is not None])
        arrival = mean([event.arrival-event.start for event in self.incidents if event.arrival is not None])
        sensitive_sites = {site.id for site in self.events if
                           site.template == 'school' and self.school_peak or
                           site.template in ('show', 'residential') and self.night}
        exposure_cost = 0.7 if sensitive_sites else 0.44
        late = sum(max(0, (event.first if event.first is not None else DURATION)-event.start
                       - (7 if event.site and event.site.id in sensitive_sites else 14))
                   for event in self.incidents)
        score = 100 - 48*(total-resolved)/max(1,total) - min(32 if sensitive_sites else 24, exposure*exposure_cost)
        score -= min(16, late*0.14) + min(10, 4*unmet) + min(7, len(self.patrols)*1.2)
        score -= min(12, 5*sum(event.peripheral for event in self.incidents))
        suggestions = [
            '在预计最早接近人群的入口安排值守员。' if exposure else '保持核心区附近的观察覆盖。',
            '在应急降落点附近安排接应员，缩短准备时间。' if any(
                event.kind == 'low_battery' and not event.resolved for event in self.incidents) else
            '把机动员前移到容易同时出现异常的航段。' if unmet else
            '试着减少一名人员，再查看到位时间是否仍可接受。',
        ]
        return StrategyMetrics(self.config.strategy.name, resolved, total, first, arrival, exposure, unmet,
                               len(self.patrols), round(sum(patrol.moved for patrol in self.patrols), 1),
                               round(max(0, score), 1), tuple(suggestions))
