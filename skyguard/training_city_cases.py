"""Fixed, fictional flight logs on shared city maps for the operator exercise."""
from datetime import timedelta

from .flight_route import destination, tracking
from .sim_flight import advance, measurement, new_flight

CASES = {
    '长沙 · 湘江风光带': ('changsha',(112.9569,28.1883),'ranking'),
    '广州 · 海心沙（平时）': ('guangzhou',(113.3228,23.1135),'routine'),
    '广州 · 海心沙（活动）': ('guangzhou',(113.3228,23.1135),'event'),
    '上海 · 徐汇滨江': ('shanghai',(121.4645,31.1887),'gap'),
}
REGION_SOURCES = {
    'changsha': 'https://whhlyt.hunan.gov.cn/whhlyt/news/sxxw/201909/t20190910_5475088.html',
    'guangzhou': 'https://sthjj.gz.gov.cn/attachment/7/7230/7230989/8794369.pdf',
    'shanghai': 'https://www.shanghai.gov.cn/nw4411/20240514/b36723181bff4ba4b596b6ecb69a3cad.html',
}

CIVIC_CASES = {
    '长沙 · 橘子洲与湘江沿江游览保障': 'changsha',
    '广州 · 海心沙夜景与周边校园防护': 'guangzhou',
    '上海 · 徐汇滨江楼宇遮挡与人手紧张调度': 'shanghai',
}

# Longitudes and latitudes are WGS84. The drills and their outcomes are fictional.
CIVIC_SITES = {
    'changsha': {
        'bounds': (112.9538, 28.1682, 112.9666, 28.1891),
        'staff': '李明',
        'walk': {
            'crowd': ((112.9547915, 28.1701235), (112.9548005, 28.1702225),
                      (112.9548185, 28.1703575)),
            # From the axial plaza via the island's east walk to the lawn entrance.
            'lawn': ((112.9547915, 28.1701235), (112.9548185, 28.1703575),
                     (112.9547841, 28.1704595), (112.9553195, 28.1704768),
                     (112.9554923, 28.1706150), (112.9556045, 28.1708655),
                     (112.9557341, 28.1716773), (112.9558723, 28.1723077),
                     (112.9560882, 28.1732059), (112.9562005, 28.1741905),
                     (112.9562609, 28.1750368), (112.9563473, 28.1759523),
                     (112.9564077, 28.1769455), (112.9564682, 28.1777573),
                     (112.9564682, 28.1786295), (112.9564941, 28.1794327),
                     (112.9565459, 28.1801409), (112.9565545, 28.1810045),
                     (112.9566150, 28.1819718), (112.9566668, 28.1829564),
                     (112.9567186, 28.1841136), (112.9567618, 28.1852364),
                     (112.9567618, 28.1860050), (112.9567359, 28.1866873),
                     (112.9565373, 28.1869291), (112.9560105, 28.1869464)),
        },
        'sites': {
            'crowd': ('观景人群区', (112.954832, 28.1704925), 14),
            'lawn': ('橘子洲北侧安全草坪', (112.95589, 28.187125), 12),
        },
    },
    'guangzhou': {
        'bounds': (113.311, 23.106, 113.336, 23.121),
        'staff': '陈宁',
        'walk': {
            'school': ((113.3329945, 23.117578), (113.332978, 23.11771),
                       (113.332978, 23.117842)),
            # Approach to the park entrance via Linjiang Avenue and the north pedestrian bridge.
            'show': ((113.3329945, 23.117578), (113.332978, 23.117842),
                     (113.332920, 23.118320), (113.332920, 23.118830), (113.332900, 23.119310),
                     (113.332870, 23.119860), (113.332850, 23.120280), (113.332730, 23.120340),
                     (113.331950, 23.120370), (113.331000, 23.120460), (113.329800, 23.120570),
                     (113.328800, 23.120680), (113.328570, 23.120630), (113.328550, 23.120380),
                     (113.328550, 23.119800), (113.328580, 23.119200), (113.328610, 23.118500),
                     (113.328640, 23.117730), (113.328640, 23.116960), (113.328740, 23.116120),
                     (113.328700, 23.115680), (113.328440, 23.115630), (113.328050, 23.115630),
                     (113.327300, 23.115750), (113.326390, 23.115860), (113.325490, 23.116030),
                     (113.324770, 23.116060), (113.323640, 23.116120), (113.322610, 23.116160),
                     (113.321450, 23.116200), (113.320390, 23.116240), (113.320120, 23.116190),
                     (113.319590, 23.115880), (113.319210, 23.115570), (113.319150, 23.115290),
                     (113.319140, 23.115070), (113.319100, 23.114980)),
        },
        'sites': {
            'show': ('演出人流区', (113.31777, 23.11425), 24),
            'school': ('临江校园及社区', (113.3315645, 23.118513), 20),
            'lawn': ('二沙岛安全降落草坪', (113.3135468, 23.1128625), 14),
        },
    },
    'shanghai': {
        'bounds': (121.4568, 31.1655, 121.4630, 31.1703),
        'staff': '周明',
        'walk': {
            'south': ((121.4582505, 31.1676592), (121.4582413, 31.1675529),
                      (121.4582597, 31.1673771)),
            'market': ((121.4582505, 31.1676592), (121.4582551, 31.1677703),
                       (121.4582597, 31.1678581)),
        },
        'sites': {
            'market': ('展馆市集区', (121.4584586, 31.1680431), 13),
            'lawn': ('徐汇滨江安全草坪', (121.4604104, 31.1665863), 12),
        },
    },
}


def civic_case(name, begin, blank):
    """Build the portable scene envelope. The replay owns responsive flight paths."""
    city = CIVIC_CASES[name]
    config = CIVIC_SITES[city]
    start = begin.replace(hour=18, minute=59, second=50) if city == 'guangzhou' else begin.replace(
        hour=15, minute=0, second=0)
    scene = blank(name, origin=start, city=city)
    station = next(iter(config['walk'].values()))[0]
    scene.update(civic_replay=1, duration=120, bounds=config['bounds'], station=station,
                 staff_limit=1, map_reference=REGION_SOURCES[city], zones=[])
    return scene


def zone_polygon(center, west, south, east, north):
    lon, lat = center
    points = [(lon+west,lat+south),(lon+east,lat+south),(lon+east,lat+north),
              (lon+west,lat+north),(lon+west,lat+south)]
    return {'type':'Polygon','coordinates':[points]}


def city_case(name, begin, blank):
    city, center, kind = CASES[name]
    start = begin.replace(hour=14,minute=0)
    scene = blank(name,origin=start,city=city)
    scene['station'] = list(center)
    lon,lat = center
    scene['bounds'] = [lon-.007,lat-.005,lon+.007,lat+.005]
    scene['map_reference'] = REGION_SOURCES[city]
    geometry = zone_polygon(center,-.004,-.002,.004,.002)
    area = {'ranking':'湘江滨水演练区','routine':'海心沙演练区',
            'event':'海心沙演练区','gap':'徐汇滨江演练区'}[kind]
    scene['zones'] = [dict(id='public-area',name=area,kind='park' if city!='guangzhou' else 'mall',
                           geometry=geometry,confirmed=True,source='已配置演练区块',
                           map_reference=REGION_SOURCES[city],region_confidence='演练区块')]
    if kind == 'event':
        scene['zones'].append(dict(id='activity',name='模拟活动区',kind='event',geometry=geometry,
                                   start=(start+timedelta(seconds=22)).isoformat(),
                                   end=(start+timedelta(seconds=112)).isoformat(),
                                   confirmed=True,source='情景设定 · 活动时段',
                                   region_confidence='演练区块'))
    for number in range(1,3):
        anchor=destination(center,180,65 if number==1 else 125)
        route=tracking(anchor,90,350,62,start=start,category='轻型',speed=4)
        state=new_flight(route,endurance=200)
        points=[measurement(state)]
        for sec in range(1,125):
            drift = (30 <= sec < (42 if number==1 else 82)) if kind=='ranking' else (30<=sec<63 and number==2)
            target = (*destination(anchor,14,210 + 60*(number==2)),72) if drift else None
            state=advance(state,route,target=target,mode='手动' if drift else '航线')
            if not(kind=='gap' and number==1 and 36<=sec<60):
                points.append(measurement(state))
        identifier=f'T{number:02d}'
        scene['tracks'].append(dict(id=identifier,name='演练航迹 '+identifier,route=route.record(),
                                    plan_id='',source='固定模拟',points=points))
        scene['filings'].append(dict(track=identifier,route=route.record()))
        scene['presets'][identifier]='已联系并核验演练航迹'
    return scene

# WGS84 anchors for fictional exercises. Relative offsets are in metres.
FLAGSHIPS = {
    '长沙 · 湘江大型活动低空综合治理': ('changsha',(112.9570,28.1881),120,5),
    '广州 · 海心沙商圈夜景与校园防护': ('guangzhou',(113.3228,23.1135),120,5),
    '上海 · 徐汇滨江遮挡中断与人力瓶颈改派': ('shanghai',(121.4645,31.1887),120,5),
}
EXTENDED_CASES = {
    '深圳 · 科技园高密度低空走廊治理': (
        'shenzhen', (113.9535, 22.5381), 150, 6, '科技园核心活动区', '科苑校园风险区', '科技园低空走廊', '科技园公园安全回收区'),
}


def extended_case(name, begin, blank):
    from math import atan2, cos, degrees, hypot, sin

    from shapely.geometry import mapping

    from .aerial import local_metres
    from .flight_route import corridor, planned_position

    city, center, duration, count, core, school, road, recovery = EXTENDED_CASES[name]
    start = begin.replace(hour=18 if city == 'guangzhou' else 14, minute=59, second=40)
    scene = blank(name, origin=start, city=city)
    project, xy = local_metres(center)
    scene.update(station=xy(-300, -160), flagship=True,
                 bounds=(*xy(-780, -550), *xy(650, 540)))
    if city=='shenzhen':
        scene['staff_limit']=1
    scene['zones'] = [
        dict(id='core', name=core, kind='event', point=xy(0, 0), radius=120, confirmed=True,
             restricted=True, start=(start+timedelta(seconds=20)).isoformat()),
        dict(id='school', name=school, kind='school', point=xy(280, 130), radius=115, confirmed=True),
        dict(id='road', name=road, kind='arterial', override='S2', confirmed=True,
             geometry=mapping(corridor((xy(164, -330), xy(180, 345)), 28))),
        dict(id='recovery', name=recovery, kind='open', point=xy(-75, -280), radius=125, confirmed=True),
    ]
    scene['posts'] = [
        dict(id=f'exercise-{i}', name=f'{label}观察岗', lon=point[0], lat=point[1])
        for i, (label, point) in enumerate((
            ('活动区南侧', xy(-65, -145)), ('校园外侧', xy(225, 25)),
            ('回收区入口', xy(-150, -260)), ('沿江步道', xy(-360, 40))), 1)
    ]

    def track_point(second, path):
        for (begin_s, a), (end_s, b) in zip(path, path[1:]):
            if second <= end_s:
                fraction = (second-begin_s)/(end_s-begin_s)
                return tuple(x+(y-x)*fraction for x, y in zip(a, b))
        return path[-1][1]

    for number in range(1, count+1):
        identifier = f'T{number:02d}'
        anchor = xy(-640 if number < 3 else -80, -430+45*number)
        shenzhen_drift=city=='shenzhen' and number==3
        if shenzhen_drift:
            anchor=xy(160,-295)
        route = (tracking(anchor, 90, 390 if number < 3 else 480, 58, speed=3.5 if shenzhen_drift else 5,
                          start=start, category='轻型') if number < 4 else None)
        if city=='shenzhen' and number==6:
            route=tracking(xy(-560,-185),90,680,65,start=start,category='轻型',speed=6)
        points = []
        previous = None
        for second in range(duration+1):
            when = start+timedelta(seconds=second)
            heading = 0
            if route:
                p = planned_position(route, min(when, route.end-timedelta(milliseconds=1)))
                local = (*project(*p.xy), p.altitude_m)
                if number == 3:
                    east, north, height = local
                    peak = 48 if shenzhen_drift else 76
                    drift = max(0, 1-abs(second-peak)/40)*425
                    local = east, north+drift, height
            elif city=='guangzhou' and number==5:
                if second<64:
                    local=track_point(second,((0,(490,-360,76)),(32,(330,-265,76)),
                        (46,(294,-180,76)),(64,(335,130,128))))
                elif second<111:
                    angle=(second-64)*.15
                    local=(280+55*cos(angle),130+55*sin(angle),112)
                else:
                    local=track_point(second,((110,(325,162,112)),(135,(455,-165,60)),(duration,(445,-300,0))))
            elif number in (4, 5) and not (city == 'shanghai' and number == 5):
                offset = -45 if number == 4 else 45
                if second < 57:
                    local = track_point(second, ((0, (-530, offset, 65)), (25, (-430, offset, 65)),
                                                (37, (-370, offset, 65)), (57, (-68, offset, 72))))
                elif second < 112:
                    angle = (second-57)*.16 + (3.14 if number == 4 else 0)
                    local = (68*cos(angle), 68*sin(angle), 72 if number == 4 else 78)
                else:
                    angle = (111-57)*.16 + (3.14 if number == 4 else 0)
                    local = track_point(second, ((111, (68*cos(angle),68*sin(angle),72)),
                                                (145, (-365, -285+offset, 22)), (duration, (-380,-285+offset,0))))
            else:
                local = track_point(second, ((0, (-480,-230,66)), (45, (-350,-200,68)),
                    (80, (-180,-215,65)), (98, ((60 if city=='shanghai' else -110),
                                                (-36 if city=='shanghai' else -250),50)),
                    (130, (-70,-275,0)), (duration, (-70,-275,0))))
            x, y, height = local
            speed = hypot(x-previous[0], y-previous[1]) if previous else 0
            heading = degrees(atan2(x-previous[0], y-previous[1])) % 360 if speed else 0
            previous = local
            if number == count and city in ('changsha','shanghai') and 46 <= second < 77:
                continue
            lon, lat = xy(x,y)
            points.append(dict(t=second, lon=lon, lat=lat, height=height, heading=heading,
                               speed=speed, mode='已降落' if height < 1 and second > 110 else '航线' if route else '手动'))
        record = route.record() if route else None
        scene['tracks'].append(dict(id=identifier, name=f'演练航迹 {identifier}', route=record,
                                    source='固定模拟', points=points))
        if record:
            scene['filings'].append(dict(track=identifier, route=record))
        scene['presets'][identifier] = '观察岗已核对演练航迹'
    return scene
