
"""Deterministic assessment, prediction and civilian exercise planning."""
from itertools import combinations

from shapely import transform
from shapely.geometry import MultiPoint, Point, LineString
from shapely.ops import nearest_points

from .aerial import local_metres
from .disposal import walking
from .flight_route import WGS84
from .zone_catalog import read_table


def coordinates(obj):
    return obj['lon'], obj['lat']


def separation(a, b):
    return WGS84.Inverse(a[1], a[0], b[1], b[0])


def heading_delta(a, b):
    return abs((a-b+180) % 360-180)


def detect_formations(objects, zones):
    active = {o['id']: o for o in objects if o['age'] < 8 and o['altitude'] > 10
              and (not o['filed'] or o['facts']['outside'] or o['attention'].level >= 2)}
    sensitive = {z.id: z.name for z, grade in zones if grade in ('S1', 'S2')}
    approaches = {
        key: {e['zone'] for e in o['forecast_entries'] if e['zone'] in sensitive
              and e['zone'] not in o['regions'] and e['seconds'] <= 90}
        for key, o in active.items()
    }
    groups = {key: {key} for key in active}
    shared_zones = {}
    for a, b in combinations(sorted(active), 2):
        left, right = active[a], active[b]
        distance = separation(coordinates(left), coordinates(right))['s12']
        shared = approaches[a] & approaches[b]
        parallel = (min(left['speed'], right['speed']) >= 1
                    and abs(left['speed']-right['speed']) <= 4
                    and heading_delta(left['heading'],right['heading']) <= 45)
        if distance <= 90 and parallel or distance <= 110 and shared and heading_delta(left['heading'],right['heading']) <= 55:
            merged = groups[a] | groups[b]
            for key in merged:
                groups[key] = merged
            if shared:
                shared_zones[a,b] = shared
    result = []
    for key in sorted(groups):
        members = sorted(groups[key])
        if key != members[0] or len(members) < 2:
            continue
        planes = [active[m] for m in members]
        center = coordinates(planes[0])
        project, inverse = local_metres(center)
        hull = MultiPoint([project(*coordinates(o)) for o in planes]).convex_hull.buffer(28, quad_segs=4)
        strongest = max((o['attention'] for o in planes), key=lambda a: (a.level,a.score))
        zones_together = sorted(set().union(*(shared_zones.get(pair,set()) for pair in combinations(members,2))))
        shared = sensitive[zones_together[0]] if zones_together else ''
        filed = sum(not o['filed'] for o in planes)
        summary = f'{len(members)} 架聚集 · {filed} 架无报备'
        if shared:
            summary += f' · 协同逼近{shared}'
        result.append(dict(id=f'G{len(result)+1:02d}', members=members, level=strongest.level,
                           trend=strongest.trend, score=strongest.score, summary=summary,
                           coordinated=bool(shared) or all(not o['filed'] for o in planes), hull=[inverse(x,y) for x,y in hull.exterior.coords],
                           points=[coordinates(o) for o in planes]))
    return result


def detect_events(second, objects, previous=(), zones=(), formations=(), prior_formations=(),
                  history=(), recent=()):
    old = {o['id']:o for o in previous}
    zone_map = {z.id:z for z, _ in zones}
    events = []

    def event(obj, kind, code, title, detail):
        if any(e['target']==obj['id'] and e['code']==code and second-e['at']<8 for e in recent):
            return
        events.append(dict(at=second, kind=kind, code=code, target=obj['id'],
                           title=title, detail=detail, point=coordinates(obj)))

    for obj in objects:
        prev = old.get(obj['id'])
        before = prev['regions'] if prev else ()
        for key in sorted(set(obj['regions']) ^ set(before)):
            entering = key in obj['regions']
            zone = zone_map.get(key)
            if zone:
                verb = '进入' if entering else '离开'
                if entering and zone.kind == 'arterial':
                    verb = '穿越'
                event(obj, 'alert' if entering and zone.kind != 'open' else 'info',
                      f'{verb}:{key}', verb+zone.name, '区域边界关系变化')
        if not prev:
            continue
        if obj['attention'].level != prev['attention'].level:
            up = obj['attention'].level > prev['attention'].level
            event(obj, 'up' if up else 'down', f'level:{obj["attention"].level}', '关注等级上调' if up else '关注等级下调',
                  f'{prev["attention"].level} → {obj["attention"].level} 级')
        if obj['facts'].get('occlusion') and obj['age']==1:
            event(obj, 'alert', 'gap', '高楼遮挡 · 观测中断', '进入降级观察窗，保持关注等级')
        elif obj['age'] >= 20 > prev['age'] and not obj['facts'].get('occlusion'):
            event(obj, 'alert', 'gap', '观测信号中断', '20 秒未收到新观测')
        if prev['age'] >= 8 and obj['age'] == 0:
            event(obj, 'info', 'lock', '观测恢复重锁', f'连续缺少 {prev["age"]} 秒观测')
        if obj['age'] or prev['age']:
            continue
        delta = obj['speed']-prev['speed']
        if second>1 and abs(delta) >= 4:
            event(obj,'alert','speed_up' if delta>0 else 'speed_down','骤然加速' if delta>0 else '骤然降速',
                  f'一秒速度变化 {delta:+.1f} m/s')
        angle = heading_delta(obj['heading'],prev['heading'])
        if min(obj['speed'],prev['speed']) >= 2 and angle >= 65:
            event(obj,'alert','turn','急转折返',f'航向变化 {angle:.0f}°')
        for flag, title in (('high','高度超过 120 米'),('outside','超出报备航线容差'),('paused_area','持续悬停')):
            if obj['facts'][flag] and not prev['facts'][flag]:
                event(obj,'alert',flag,title,obj['area'])
        if (obj['regions'] or obj['facts'].get('recovery_needed')) and len(
                path := history.get(obj['id'], ()) if history else ()) >= 14:
            turns = [heading_delta(a['heading'],b['heading']) for a,b in zip(path,path[1:])]
            def orbiting(angles, samples):
                return (all(p['speed'] >= 2 and not p['age'] for p in samples)
                        and all(value<40 for value in angles) and sum(angles)>=100)
            if orbiting(turns[1:],path[1:]) and not orbiting(turns[:-1],path[:-1]):
                title='敏感区域持续盘旋' if obj['grade'] in ('S1','S2') else '持续盘旋'
                event(obj,'alert','orbit',title,obj['area'])
    old_groups = {tuple(g['members']) for g in prior_formations}
    for group in formations:
        if tuple(group['members']) not in old_groups:
            event(next(o for o in objects if o['id']==group['members'][0]), 'formation',
                  group['id'], '识别编队 '+group['id'], group['summary'])
    return events


def level_rows(obj, rules=None):
    rules = rules or read_table('attention-rules.json')
    facts, att = obj['facts'],obj['attention']
    base = 3 if facts['rating']=='S1' or facts['near_s1'] else 2 if facts['rating']=='S2' else 1
    conditions = {key:(f'{sign}{delta} 级', tone, text) for rows, sign, tone in
                  ((rules['add'], '+', 'danger'), (rules['subtract'], '−', 'good'))
                  for key,text,_,delta in rows}
    fixed = dict(A00=(f'起评 {base} 级','info','区域敏感度起评'),
                 A30=('保持' if att.since is not None else '−1 级','info','降级观察窗'),
                 A40=('直定 4 级','danger','生效限制范围'), A41=('直定 1 级','good','现场核实'),
                 A42=('保持','info','观测待补'), A43=('回落至 2 级','good','核验离区'),
                 A44=('回落至 1 级','good','演练闭环'), A99=(f'调整为 {att.level} 级','info','人工调整'))
    conditions.update(fixed)
    triggers = dict(A00=f'{obj["area"]}（{facts["rating"] or ("临近 S1" if facts["near_s1"] else "普通区域")}）',
                    A11='未匹配到有效本机报备航线',
                    A30=f'{rules["hold_seconds"]} 秒降级观察窗' + ('尚未结束' if att.since is not None else '已结束'),
                    A40=obj['area'])
    return [(badge,tone,key,name,triggers.get(key,detail)) for key, detail in att.detail
            for badge,tone,name in (conditions.get(key,('生效','info',detail)),)]


def score_rows(obj, rules=None):
    weights = (rules or read_table('attention-rules.json'))['weights']
    labels = dict(urgency='紧迫性',impact='区域影响',gap='观测缺口',anomaly='异常程度')
    return [dict(name=labels[key], weight=round(weight*100), value=round(value,1),
                 contribution=round(value*weight,2))
            for key, weight in weights.items() for value in (100*max(0,min(1,obj['facts'].get(key,0))),)]


def analyse_target(obj, zones):
    here = coordinates(obj)
    project, inverse = local_metres(here)
    origin = Point(0,0)
    distances = []
    closest = []
    safe = []
    prediction = obj['prediction']
    line = LineString([project(*p) for p in prediction]) if len(prediction) >= 2 else None
    sensitive = {zone.id:zone for zone,grade in zones if grade in ('S1','S2')}
    for zone, grade in zones:
        geom = transform(zone.geometry, project, interleaved=False)
        distance = geom.boundary.distance(origin)
        inside = geom.covers(origin)
        distances.append(dict(name=zone.name, grade=grade, inside=inside,
                              metres=round(-distance if inside else distance,1)))
        if zone.kind == 'open':
            center = inverse(*geom.centroid.coords[0])
            bearing = separation(here,center)
            safe.append(dict(name=zone.name, point=center, metres=round(bearing['s12'],1),
                             bearing=round(bearing['azi1']%360)))
        if zone.id in sensitive and line and line.length:
            point, boundary = nearest_points(line,geom)
            closest.append((point.distance(boundary),line.project(point),distance,zone.name,point))
    entries = sorted((e for e in obj['forecast_entries'] if e['zone'] in sensitive
                      and e['zone'] not in obj['regions']), key=lambda e:(e['seconds'],e['zone']))
    cpa = None
    if entries:
        cpa = dict(entries[0], text=f'预计 +{entries[0]["seconds"]} 秒进入{entries[0]["name"]}', entering=True)
    elif closest:
        distance, along, initial, name, point = min(closest,key=lambda row:row[:4])
        seconds = round(along/line.length*obj['prediction_seconds'])
        text = f'预计 +{seconds} 秒最接近{name} · {distance:.0f} 米' if along else f'当前最接近{name} · {initial:.0f} 米'
        cpa = dict(name=name, seconds=seconds, point=inverse(point.x,point.y), entering=False,text=text)
    if obj['age'] >= 8:
        intent, missing = '疑似链路异常或失联漂移', '链路状态、电池余量、飞手回执'
    elif obj['facts']['outside']:
        intent, missing = '超出报备航线，待纠偏', '偏离原因、飞手回执'
    elif obj['filed']:
        intent, missing = '已报备航拍', '实时飞手回执'
    else:
        intent = '疑似未报备逼近敏感区' if cpa and cpa['entering'] else '未匹配报备的飞行'
        missing = '飞手身份、补充报备、机载载荷情况'
    return dict(cpa=cpa, zone_distances=distances,
                recovery_zone=min(safe,key=lambda z:z['metres'],default=None),
                intent=intent, missing_evidence=missing)


def generate_agent_plans(obj, posts, staff, recovery_zone=None, formation=False, *, starts=None, exercise=False):
    starts = starts or {}
    location = coordinates(obj)
    choices = []
    for post in posts:
        if not post.get('enabled',True):
            continue
        finish = post['lon'], post['lat']
        distance = separation(location,finish)['s12']
        for person in staff:
            if person.get('available',True):
                walk = walking(starts.get(person['id'],location),finish,speed=9 if exercise else 1.25)
                if exercise:
                    walk.update(seconds=max(12,walk['seconds']),basis='演练步行快进')
                choices.append((distance>350,distance if exercise else walk['seconds'],
                                walk['seconds'] if exercise else distance,person['id'],post['id'],walk,person,post))
    candidate = min(choices,key=lambda row:row[:5],default=None)
    assignment = None
    coverage = '暂无空闲观察员或观察岗'
    if candidate:
        far, _, _, _, _, walk, person, post = candidate
        distance=separation(location,walk['finish'])['s12']
        coverage = f'{post["name"]} · 距目标 {distance:.0f} 米' + (' · 视距待核对' if far else ' · 预计近距覆盖')
        assignment = dict(person_id=person['id'], person=person['name'], walk=walk, place=post['name'],
                          plan_id=obj['plan_id'],post_id=post['id'])
    level = obj['attention'].level
    recommended = 'C' if level==4 or formation or obj['age']>=20 or obj['facts'].get('recovery_needed') else 'B' if level==3 else 'A'
    recovery = f'引导至{recovery_zone["name"]}' if recovery_zone else '核对安全回收点'
    result = []
    for code, name, steps, kinds in (
        ('A','尽远警示劝离',('飞手纠偏提醒','电子围栏警示','持续观察'),('nudge','geofence','watch')),
        ('B','最快观察岗核验',('飞手提醒','观察岗核验','预备安全回收'),('nudge','dispatch')),
        ('C','综合应急协同',('飞手提醒','观察岗核验','区域协同通告',recovery,'准备移交'),
         ('nudge','dispatch','notify','recover','handoff')),
    ):
        needs_staff = code != 'A'
        staffed = needs_staff and candidate is not None
        result.append(dict(code=code,name=name,recommended=code==recommended,eta_s=assignment['walk']['seconds']
                           if staffed else None if needs_staff else 2,staff_needed=int(needs_staff),
                           coverage_label=coverage if needs_staff else '本机模拟提醒覆盖',
                           summary=' · '.join(steps),steps=steps,action_kinds=kinds,
                           assignment=assignment if staffed else None,
                           recovery_zone=recovery_zone if code=='C' else None))
    return result


def answer_situation_query(question, objects, formations, events, zones, plans_for, free_staff=()):
    lookup = {o['id'].upper():o for o in objects}
    import re
    identifier = re.search(r'(?:PUB-\d+|T\d+)',question.upper())
    if identifier:
        obj = lookup.get(identifier[0])
        if not obj:
            return f'当前帧未观测到 {identifier[0]}。'
        details, plans = plans_for(obj)
        cpa = details['cpa']
        plan = next(p for p in plans if p['recommended'])
        return f'{obj["id"]} · {obj["attention"].level} 级。{cpa["text"] if cpa else "当前缺少有效逼近预测"}。建议 {plan["code"]} · {plan["name"]}。'
    if any(word in question for word in ('编队','异常','事件')):
        groups = '；'.join(f'{g["id"]} {g["summary"]}' for g in formations) or '当前无编队'
        current = '；'.join(f'{e["at"]}s {e["target"]} {e["title"]}' for e in events[-5:]) or '暂无重要事件'
        return f'{groups}。近期事件：{current}。'
    if any(word in question for word in ('区域','分布','空闲')):
        counts = '；'.join(f'{z.name}（{grade}）{sum(z.id in o["regions"] for o in objects)} 架' for z,grade in zones)
        return f'{counts or "暂无生效区域"}。空闲观察员：{"、".join(p["name"] for p in free_staff) or "暂无"}。'
    if not objects:
        return '当前帧暂无观测目标。'
    top = max(objects,key=lambda o:(o['attention'].level,o['attention'].score))
    details,plans = plans_for(top)
    plan = next(p for p in plans if p['recommended'])
    return (f'当前优先关注 {top["id"]} · {top["attention"].level} 级，位于{top["area"]}。'
            f'{details["intent"]}，{top["attention"].detail[-1][1]}。建议 {plan["code"]} · {plan["name"]}。')
