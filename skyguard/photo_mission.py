"""Shared shot lists, flight rehearsal positions and media matching."""
from bisect import bisect_right
from datetime import datetime, timedelta
import re

from .aerial import sun_position
from .flight_route import WGS84, camera_pitch, destination, timeline

TEMPLATES = {
    'travel': ('旅行留念', ('主镜头', '补充画面'), '游玩地点和想拍的画面', '停留分钟数'),
    'promo': ('风景或商家宣传', ('环境全景', '主体展示', '特色细节', '收尾'), '用逗号分隔要拍的对象', '截止时间，如 17:30'),
    'news': ('新闻与活动纪实', ('场地全景', '环境交代', '重点画面'), '活动或必拍画面', '最晚时刻，如 15:30'),
    'repeat': ('工程进度复拍', ('进度全景', '固定对比机位'), '工程名称或观察对象', '复拍间隔，如 7 天'),
}


def travel_stop_minutes(text):
    match = re.fullmatch(r'\s*(\d{1,3})\s*(?:分钟|分)?\s*', text)
    return int(match[1]) if match and int(match[1]) > 0 else None


def repeat_interval(text):
    match = re.fullmatch(r'\s*(\d{1,3})\s*(天|周)?\s*', text)
    days = int(match[1]) * (7 if match[2] == '周' else 1) if match else 1
    return timedelta(days=max(1, days))


def deadline(text, date):
    match = re.fullmatch(r'\s*(\d{1,2})[:：](\d{2})\s*', text)
    if match and int(match[1]) < 24 and int(match[2]) < 60:
        return date.replace(hour=int(match[1]), minute=int(match[2]), second=0, microsecond=0)
    return None


def camera_at(piece, fraction):
    a,b = piece.a.camera_deg,piece.b.camera_deg
    if a is None:
        return b
    if b is None:
        return a
    return (a+fraction*((b-a+180)%360-180))%360


def plan_frame(route, seconds, subject=None):
    pieces = timeline(route)
    seconds = min(pieces[-1].end_s,max(0.,seconds))
    piece = pieces[min(len(pieces)-1,bisect_right([item.end_s for item in pieces],seconds))]
    fraction = (seconds-piece.begin_s)/(piece.end_s-piece.begin_s)
    point = piece.at(fraction)
    heading = camera_at(piece,fraction)
    if heading is None and subject:
        heading = WGS84.Inverse(point.lat,point.lon,subject[1],subject[0])['azi1'] % 360
    a, b = piece.a.pitch_deg, piece.b.pitch_deg
    pitch = a + fraction * (b - a) if a is not None and b is not None else (
        camera_pitch(point.xy, point.altitude_m, subject) if subject else a if a is not None else b)
    move = piece.b.move or piece.a.move
    tip = piece.b.tip or piece.a.tip
    body_heading = (piece.bearing if piece.metres > .01 else
                    next((leg.bearing for leg in reversed(pieces) if leg.end_s <= seconds and leg.metres > .01),
                         next((leg.bearing for leg in pieces if leg.metres > .01), 0.)))
    timestamp = route.start + timedelta(seconds=seconds)
    elevation, azimuth = sun_position(point.xy, timestamp)
    return {'point': point.xy, 'altitude': point.altitude_m, 'heading': heading,
            'body_heading': body_heading, 'phase': piece.phase, 'shot': piece.shot,
            'pitch': pitch, 'move': move, 'tip': tip,
            'ray': [point.xy, destination(point.xy, heading, 60)] if heading is not None else [],
            'time': timestamp.isoformat(), 'sun_elevation': elevation, 'sun_azimuth': azimuth}


def shot_list(route, kind, brief=''):
    _, titles, _, _ = TEMPLATES[kind]
    names = [value.strip() for value in brief.replace('、', '，').replace(',', '，').split('，') if value.strip()]
    if kind in ('news', 'promo') and len(names) > 1:
        titles = names[:8]
    pieces = timeline(route)
    actual = [piece for piece in pieces if piece.shot]
    if route.complete_flight and not actual:
        actual = [piece for piece in pieces if piece.phase in ('进场','转场','悬停')][:len(titles)]
    if actual:
        rows = []
        for index, piece in enumerate(actual):
            frame = plan_frame(route, (piece.begin_s+piece.end_s)/2)
            rows.append({'id':f'{kind}-{index+1}', 'title':piece.shot or titles[index],
                         'seconds':piece.begin_s, 'end':piece.end_s,
                         'path':[piece.a.xy,piece.b.xy], 'bearing':piece.bearing % 360 if piece.metres > .01 else None,
                         'speed':route.speed_mps if piece.metres > .01 else 0.,
                         'action':'沿段录制' if piece.metres > .01 or piece.a.altitude_m != piece.b.altitude_m else '悬停拍摄',
                         'record_s':max(1,round(piece.end_s-piece.begin_s)), 'point':list(frame['point']),
                         'heading':frame['heading'],'altitude':frame['altitude'],
                         'pitch': frame['pitch'], 'move': frame['move'], 'tip': frame['tip'],
                         'altitude_start': piece.a.altitude_m, 'altitude_end': piece.b.altitude_m})
        return rows
    duration = pieces[-1].end_s
    rows = []
    for index, title in enumerate(titles):
        start = duration * index / len(titles)
        end = duration * (index + 1) / len(titles)
        frame = plan_frame(route, (start + end) / 2)
        rows.append({'id': f'{kind}-{index+1}', 'title': title, 'seconds': start,
                     'record_s': min(12, max(1, round(end - start))),
                     'point': list(frame['point']), 'heading': frame['heading'],
                     'altitude': frame['altitude'], 'pitch': frame['pitch'],
                     'move': frame['move'], 'tip': frame['tip'],
                     'altitude_start': plan_frame(route, start)['altitude'],
                     'altitude_end': plan_frame(route, end)['altitude']})
    return rows


def shot_altitude(shot):
    start = round(shot.get('altitude_start', shot['altitude']))
    end = round(shot.get('altitude_end', shot['altitude']))
    return str(start) if start == end else f'{start}→{end}'


def mission_for(route, kind, brief, timing, previous=None):
    if kind not in TEMPLATES and kind != 'free':
        return None
    previous = previous or {}
    shots = shot_list(route, 'travel' if kind == 'free' else kind, brief)
    previous_titles = {shot['id']:shot['title'] for shot in previous.get('shots', shots)}
    identifiers = {shot['id'] for shot in shots if previous_titles.get(shot['id']) == shot['title']}
    materials = [dict(item, shot=item['shot'] if item.get('shot') in identifiers else '')
                 for item in previous.get('materials', ())]
    return {'kind': kind, 'brief': brief.strip(), 'timing': timing.strip(), 'shots': shots,
            'materials': materials, 'previous_plan': previous.get('previous_plan'),
            'completed': [identifier for identifier in previous.get('completed', ()) if identifier in identifiers]}


def match_shot(item, route, shots):
    stamp = item.get('time')
    point = item.get('point')
    if not stamp and not point:
        return ''
    elapsed = None
    if stamp:
        try:
            elapsed = (datetime.fromisoformat(stamp).astimezone(route.start.tzinfo) - route.start).total_seconds()
        except (TypeError, ValueError):
            pass
    ranked = []
    for shot in shots:
        offset = abs(shot['seconds'] + shot['record_s']/2 - elapsed) if elapsed is not None else 0
        metres = WGS84.Inverse(point[1], point[0], shot['point'][1], shot['point'][0])['s12'] if point else 0
        if (point or elapsed is not None) and offset <= 120 and metres <= 100:
            ranked.append((offset / 120 + metres / 100, shot['id']))
    ranked.sort()
    if not ranked or len(ranked) > 1 and ranked[1][0] - ranked[0][0] < .12:
        return ''
    return ranked[0][1]
