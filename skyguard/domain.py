"""Framework-free domain and numerical tools. Coordinates are WGS84 degrees."""
from dataclasses import dataclass, field, asdict
from math import cos, radians, hypot, isfinite, sqrt
from typing import Any


DEFAULT_START_OF_DAY_S = 15 * 3600 + 20 * 60
DEFAULT_DURATION_S = 1800
DEFAULT_SPEEDS = (1, 5, 15)


def clock_text(seconds, start_of_day_s=DEFAULT_START_OF_DAY_S):
    """Display an exercise time using the configured local clock origin."""
    seconds = (int(seconds) + start_of_day_s) % 86400
    return f'{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}'


def distance(a: tuple, b: tuple) -> float:
    lat = radians((a[0] + b[0]) / 2)
    return hypot((a[0]-b[0])*111195, (a[1]-b[1])*111195*cos(lat))


def advance(lat, lon, vx, vy, seconds):
    return lat + vy*seconds/111195, lon + vx*seconds/(111195*cos(radians(lat)))


@dataclass
class Observation:
    id: str
    name: str
    lat: float | None
    lon: float | None
    vx: float = 0
    vy: float = 0
    observed_s: float = 0
    accuracy_m: float = 50
    source: str = "synthetic"
    description: str = ""
    conflict: bool = False
    cooperation: bool = False
    moving: bool = False
    attachment: str = ""

    def __post_init__(self):
        if not self.id.strip() or not self.name.strip() or len(self.id)>128 or len(self.name)>100:
            raise ValueError('请填写有效长度的报告编号和名称')
        if (self.lat is None) != (self.lon is None):
            raise ValueError("经纬度需要同时填写或同时留空")
        nums=[self.vx,self.vy,self.observed_s,self.accuracy_m]
        if self.lat is not None: nums += [self.lat,self.lon]
        if not all(isfinite(v) for v in nums): raise ValueError("数值必须有限")
        if self.lat is not None and not (-90<=self.lat<=90 and -180<=self.lon<=180):
            raise ValueError("经纬度超出范围")
        if self.accuracy_m<=0 or hypot(self.vx,self.vy)>100: raise ValueError("精度或速度不合理")
        if self.source not in {"synthetic","field_observation","authorized_log"}: raise ValueError("未知来源")
        if self.moving and self.lat is None: raise ValueError('动态位置流需要已知目标位置')


@dataclass
class Area:
    id: str
    name: str
    lat: float
    lon: float
    radius_m: float
    base: float
    start_s: int
    end_s: int
    active: bool = True
    description: str = "演练活动范围，非官方边界"


@dataclass
class Person:
    id: str
    name: str
    role: str
    available: bool = True


@dataclass
class Scenario:
    observations: list[Observation]
    areas: list[Area]
    people: list[Person]
    clock: float = 0
    revision: int = 1
    tasks: list[dict] = field(default_factory=list)
    operation: dict = field(default_factory=dict)
    start_of_day_s: int = DEFAULT_START_OF_DAY_S
    duration_s: int = DEFAULT_DURATION_S
    speeds: tuple[int, ...] = DEFAULT_SPEEDS

    def clock_text(self, seconds):
        return clock_text(seconds, self.start_of_day_s)

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls([Observation(**x) for x in d['observations']], [Area(**x) for x in d['areas']],
                   [Person(**x) for x in d['people']], float(d.get('clock',0)),
                   int(d.get('revision', 1)), d.get('tasks', []), d.get('operation', {}),
                   int(d.get('start_of_day_s', DEFAULT_START_OF_DAY_S)),
                   int(d.get('duration_s', DEFAULT_DURATION_S)), tuple(d.get('speeds', DEFAULT_SPEEDS)))


def demo() -> Scenario:
    # All observations, tasks and activity windows are fictional; the geographic locations are real Changsha.
    return Scenario([
        Observation('obs-a','摄影协作 A',28.1792,112.953,4.5,1.0, accuracy_m=18,
                    cooperation=True,moving=True,description='虚拟合作摄影位置流；型号、飞行许可未由本软件核验。'),
        Observation('obs-b','临时目击 B',28.1848,112.961,-2.0,-1.0,observed_s=-18,accuracy_m=160,
                    description='演练目击：沿湘江西岸向西南移动，位置为地图估计。'),
        Observation('obs-c','巡检协作 C',28.1740,112.956,1.5,2.2,accuracy_m=25,
                    cooperation=True,moving=True,description='虚拟巡检任务位置流。'),
    ],[
        Area('area-a','湖南大学周边活动',28.1797,112.9500,450,35,0,480),
        Area('area-b','湘江西岸摄影协作区',28.1835,112.9620,350,25,180,720),
        Area('area-c','岳麓山脚关注区',28.1850,112.945,400,30,600,1200),
    ],[Person('p1','现场人员甲','现场核验'),Person('p2','值班人员乙','联络核实'),Person('p3','协作人员丙','现场核验')])


def snapshot(s: Scenario, o: Observation, radar=False, measure_impact=True) -> dict[str, Any]:
    age=max(0,s.clock-o.observed_s)
    track = None
    if radar and o.moving and o.id in {'obs-a', 'obs-c'}:
        from .radar import track_view
        track = track_view(o.id, s.clock)
    if track:
        lat,lon,age = track['lat'],track['lon'],track['age']
    elif o.moving and not radar:
        lat,lon=advance(o.lat,o.lon,o.vx,o.vy,s.clock)
        age=0
    else: lat,lon=o.lat,o.lon
    uncertainty=track['accuracy_m'] if track else o.accuracy_m+age*2
    quality = (
        '存在冲突' if o.conflict else '信息陈旧' if age > 45
        else '信息有限' if lat is None or uncertainty > 100 else '较充分'
    )
    predicted=[]
    if track:
        predicted=track['prediction']
        quality='存在冲突' if o.conflict else track['quality']
    elif lat is not None and not radar:
        for dt in (0,15,30,60):
            p=advance(lat,lon,o.vx,o.vy,dt if o.moving else min(age+dt,120))
            predicted.append({'lat':p[0],'lon':p[1],'seconds':dt,'radius_m':uncertainty+dt*2})
    effects=[]
    high_impact=False
    for a in s.areas:
        current_active=a.active and a.start_s<=s.clock<a.end_s
        hit=False
        weights=[]
        points=predicted or ([{'lat':lat,'lon':lon,'seconds':0,'radius_m':uncertainty}]
                             if radar and lat is not None and age<=90 else [])
        for p in points:
            if distance((p['lat'],p['lon']),(a.lat,a.lon))<=a.radius_m+p['radius_m']:
                future_active=a.active and a.start_s<=s.clock+p['seconds']<a.end_s
                hit=True
                estimate=a.base*(1.8 if future_active else .65)
                high_impact = high_impact or estimate >= 49.95
                if measure_impact:
                    weights.append(estimate)
        if hit:
            item = {'id':a.id,'name':a.name,'active':current_active,'basis':'预测范围与演练活动范围相交'}
            if measure_impact:
                item['index'] = round(min(100,max(weights)),1)
            effects.append(item)
    known = bool(predicted or (radar and lat is not None and age<=90))
    impact=max((x['index'] for x in effects),default=0) if measure_impact and known else None
    priority='优先核验' if o.conflict or age>45 or lat is None or high_impact else '持续关注'
    return {'id':o.id,'name':o.name,'lat':lat,'lon':lon,'age':round(age), 'quality':quality,
            'accuracy_m':round(uncertainty),'speed':track['speed'] if track else None if radar else round(hypot(o.vx,o.vy),1), 'prediction':predicted,
            'effects':effects,'impact':impact,'priority':priority,'source':o.source,
            'observed_accuracy_m':track['accuracy_m'] if track else o.accuracy_m,
            'description':o.description,'conflict':o.conflict,'cooperation':o.cooperation,
            'attachment':o.attachment,'identity':'合作记录待核实' if o.cooperation else '身份未知',
            'track_state':track['track_state'] if track else '仅目击待核实',
            'observed_s':track['observed_s'] if track else o.observed_s,
            'heading':track['heading'] if track else None, 'history':track['history'] if track else []}


def impact_grid(s: Scenario):
    # Modest local grid; no assumed population counts or physical crash probabilities.
    cells=[]
    for a in s.areas:
        active=a.active and a.start_s<=s.clock<a.end_s
        for dx in range(-3,4):
            for dy in range(-3,4):
                dist=hypot(dx,dy)*120
                if dist>a.radius_m: continue
                lat,lon=advance(a.lat,a.lon,dx*120,dy*120,1)
                cells.append([lat,lon,round(a.base*(1.8 if active else .65)*(1-.45*dist/a.radius_m),1)])
    return cells


def build_plans(scene: Scenario, event_id: str, mode='事件协同') -> list[dict]:
    """Compatibility entry point for callers that only need the plan list."""
    from .planning import plan_for_event

    return plan_for_event(scene, event_id, mode)
