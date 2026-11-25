"""Civilian exercise scripts on WGS84 city anchors. Local coordinates are metres."""
from datetime import timedelta
from math import atan2, cos, degrees, hypot, sin, tau

from shapely.geometry import mapping

from .aerial import local_metres
from .flight_route import corridor, planned_position, tracking
from .training_city_cases import FLAGSHIPS, REGION_SOURCES


# Track points are (second, east, north, altitude). A separate filing defines the
# expected corridor for the aircraft whose simulated pilot can correct a deviation.
SCRIPTS = {
    'changsha': {
        'station': (-110, -295),
        'zones': (
            ('core', '湘江核心活动区', 'event', 0, 0, 120, 'S1', True),
            ('buffer', '沿江外围缓冲带', 'park', 0, 0, 250, 'S2', False),
            ('road', '江岸主干廊道', 'arterial', 310, 0, 30, 'S2', False),
            ('recovery', '橘子洲北滨江安全回收区', 'open', -102, 290, 65, '低影响', False),
        ),
        'posts': (('活动区南侧', -48, -118), ('沿江东侧', 530, -195),
                  ('回收区入口', -115, 230), ('沿江西侧', -570, -229)),
        'tracks': {
            'T01': dict(filing=(-550,-390,0,5,56), background=True),
            'T02': dict(filing=(447,-360,0,5,62), correction=True,
                        offsets=((0,0),(3,0),(6,45),(24,51),(29,0),(120,0))),
            'T04': dict(path=((0,595,-360,63),(18,427,-79,67),(24,361,-27,68),
                              (28,334,-26,69),(42,116,-20,76),(62,40,-18,76),
                              (67,30,-20,75),(87,272,-92,56),(120,596,-220,53)), verify_on_entry=True),
            'T05': dict(path=((0,584,355,78),(18,429,95,75),(24,360,28,73),
                              (28,334,26,72),(42,114,20,82),(62,41,18,79),
                              (67,31,21,78),(87,270,83,60),(120,590,227,52)), verify_on_entry=True),
            'T06': dict(path=((0,-542,375,70),(29,-337,201,69),(35,-295,197,67),
                              (36,-293,195,66),(72,-293,195,52),(88,-117,284,31),
                              (105,-102,290,0),(120,-102,290,0)),
                        orbit=(38,72,-293,218,23,60), gap=(36,58), recovery=(35,105)),
        },
    },
    'guangzhou': {
        'station': (225, -305),
        'zones': (
            ('core', '海心沙活动核心区', 'event', 0, 0, 120, 'S1', False),
            ('buffer', '珠江新城商圈缓冲带', 'mall', -70, 180, 230, 'S2', False),
            ('school', '临江校园敏感区', 'school', 360, 153, 112, 'S1', True),
            ('recovery', '二沙岛公园安全回收区', 'open', -798, -42, 58, '低影响', False),
        ),
        'posts': (('活动区外围', 104,-141), ('校园外侧', 357,26),
                  ('商圈外围', -34,-141), ('回收区入口', -748,-43)),
        'tracks': {
            'T01': dict(filing=(620,-305,0,5,55),background=True),
            'T02': dict(filing=(28,-12,180,6,64),correction=True,
                        offsets=((0,0),(10,0),(14,14),(18,40),(24,72),(40,0),(120,0)), timed_diversion=True),
            'T03': dict(path=((0,-575,110,56),(16,-390,134,117),(25,-263,157,138),
                              (41,-230,219,138),(68,-430,303,93),(120,-601,473,75))),
            'T04': dict(path=((0,632,447,68),(26,452,224,72),(37,389,153,73),
                              (74,390,153,67),(86,523,211,59),(120,612,374,52)),
                        orbit=(37,74,360,153,29,72), verified_exit=True),
            'T05': dict(path=((0,-250,-440,66),(26,-179,-362,57),(44,-156,-330,53),
                              (81,-785,-48,24),(99,-798,-42,0),(120,-798,-42,0)),
                        recovery=(26,99)),
        },
    },
    'shanghai': {
        'station': (220,-75),
        'zones': (
            ('core', '西岸展馆核心活动区', 'event', 0, 88, 112, 'S1', True),
            ('buffer', '徐汇滨江缓冲带', 'park', -20,-70, 224, 'S2', False),
            ('road', '龙腾大道主干廊道', 'arterial', -324,0, 28, 'S2', False),
            ('recovery', '滨江草坪安全回收区', 'open', 489, -179, 105, '低影响', False),
        ),
        'posts': (('展馆北侧', 120,180), ('滨江南侧', 10,-221),
                  ('沿江西侧', -114,-114), ('草坪入口', 392,-140)),
        'tracks': {
            'T01': dict(filing=(-555,-380,0,5,59),background=True),
            'T02': dict(path=((0,-516,9,69),(18,-340,-18,65),(27,-212,-47,65),
                              (50,-207,-55,61),(72,-363,-65,54),(120,-619,-140,47)),
                        gap=(28,50), occlusion=True),
            'T03': dict(path=((0,5,-455,58),(18,8,-287,61),(40,-28,-188,66),
                              (70,-23,-231,57),(92,33,-393,52),(120,93,-459,47))),
            'T04': dict(path=((0,18,476,71),(14,15,378,76),(25,8,205,80),
                              (26,8,192,80),(52,24,68,76),(61,27,56,74),
                              (88,31,346,58),(120,235,470,49))),
            'T05': dict(filing=(499,430,180,5,58),correction=True,
                        offsets=((0,0),(16,0),(24,56),(38,60),(46,0),(120,0)),
                        landing=((97,499,-55,58),(106,490,-176,20),(117,489,-179,0),
                                 (120,489,-179,0))),
        },
    },
}


def interpolate(second, path):
    for (a, *start), (b, *finish) in zip(path, path[1:]):
        if second <= b:
            fraction = (second-a)/(b-a)
            return tuple(x+(y-x)*fraction for x,y in zip(start,finish))
    return path[-1][1:]


def flagship_case(name, begin, blank):
    city, center, duration, _ = FLAGSHIPS[name]
    start = begin.replace(hour=18,minute=59,second=50) if city=='guangzhou' else begin.replace(hour=15,minute=0,second=0)
    script = SCRIPTS[city]
    project, xy = local_metres(center)
    scene = blank(name,origin=start,city=city)
    scene.update(station=xy(*script['station']),flagship=True,flagship_timeline=True,
                 bounds=(*xy(-880 if city=='guangzhou' else -680,-515),*xy(686,520)),
                 map_reference=REGION_SOURCES[city])
    if city=='shanghai':
        scene['staff_limit']=1
    scene['zones'] = []
    for identifier,title,kind,x,y,radius,grade,restricted in script['zones']:
        zone = dict(id=identifier,name=title,kind=kind,point=xy(x,y),radius=radius,
                    confirmed=True,override=grade,restricted=restricted)
        if kind=='arterial':
            south,north=(-65,65) if city=='changsha' else (-365,370)
            zone['geometry']=mapping(corridor((xy(x,south),xy(x+14,north)),radius))
        if city=='guangzhou' and identifier=='core':
            zone['start']=(start+timedelta(seconds=10)).isoformat()
        scene['zones'].append(zone)
    scene['posts'] = [
        dict(id=f'exercise-{i}',name=f'{title}观察岗',lon=lon,lat=lat)
        for i,(title,east,north) in enumerate(script['posts'],1)
        for lon,lat in (xy(east,north),)
    ]
    for identifier,track in script['tracks'].items():
        route=None
        if filing:=track.get('filing'):
            x,y,bearing,speed,height=filing
            route=tracking(xy(x,y),bearing,speed*(duration+1),height,start=start,category='轻型',speed=speed)
        points=[]
        previous=None
        for second in range(duration+1):
            mode='手动'
            if track.get('landing') and second>=track['landing'][0][0]:
                x,y,height=interpolate(second,track['landing'])
                mode='回收引导'
            elif route:
                plan=planned_position(route,start+timedelta(seconds=second))
                x,y=project(*plan.xy)
                height=plan.altitude_m
                drift,=interpolate(second,track.get('offsets',((0,0),(duration,0))))
                x+=drift
                mode='偏航' if drift>30 else '航线'
            else:
                x,y,height=interpolate(second,track['path'])
                if track.get('orbit') and track['orbit'][0]<=second<track['orbit'][1]:
                    a,b,cx,cy,radius,height=track['orbit']
                    angle=(second-a)*tau/(b-a)
                    x,y=cx+radius*cos(angle),cy+radius*sin(angle)
                if track.get('recovery') and track['recovery'][0]<=second<track['recovery'][1]:
                    mode=('失联' if city=='changsha' and second<72 else '低电'
                          if city=='guangzhou' and second<44 else '回收引导')
            speed=hypot(x-previous[0],y-previous[1]) if previous else 0
            heading=degrees(atan2(x-previous[0],y-previous[1]))%360 if speed else 0
            previous=x,y
            if track.get('gap') and track['gap'][0]<=second<track['gap'][1]:
                continue
            lon,lat=xy(x,y)
            points.append(dict(t=second,lon=lon,lat=lat,height=height,heading=heading,speed=speed,
                               mode='已降落' if height==0 else mode))
        record=route.record() if route else None
        scene['tracks'].append(dict(id=identifier,name=f'演练航迹 {identifier}',route=record,
                                    source='固定模拟',points=points,background=track.get('background',False),
                                    **{flag:True for flag in ('correction','timed_diversion','occlusion',
                                                             'verify_on_entry','verified_exit')
                                       if track.get(flag)}))
        if record:
            scene['filings'].append(dict(track=identifier,route=record))
    return scene
