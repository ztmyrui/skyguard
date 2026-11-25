"""Deterministic replay of civilian flight events and on-foot patrol decisions."""
from dataclasses import dataclass
from math import atan2, degrees

from .domain import distance
from .training_city_cases import CIVIC_SITES


def metres(a, b):
    return distance(a[::-1], b[::-1])


def mix(a, b, fraction):
    return tuple(x + (y - x) * fraction for x, y in zip(a, b))


@dataclass(frozen=True)
class Incident:
    target: str
    start: int
    end: int
    title: str
    response: str
    site: str = ''
    post: str = ''
    occluded: bool = False


INCIDENTS = {
    'changsha': (
        Incident('T02', 16, 28, '阵风致航线偏移', '请沿东岸回到已报备航线。'),
        Incident('T04', 24, 88, '逼近密集人群区域', '两架未报备航拍机请远离观景台上空。', 'crowd', 'crowd'),
        Incident('T03', 35, 105, '低电量/链路异常紧急备降', '请飞往橘子洲开阔草坪备降。', 'lawn'),
    ),
    'guangzhou': (
        Incident('T02', 10, 26, '海心沙演出开始', '演出已开始，请绕行。', 'show'),
        Incident('T03', 29, 43, '超真高限制（>120 m）', '请降低至相对真高 95 m。'),
        Incident('T04', 45, 79, '未报备无人机在校园附近盘旋', '请离开校园及社区上空。', 'school', 'school'),
        Incident('T05', 53, 111, '低电量紧急备降', '请飞往二沙岛开阔草坪备降。', 'lawn'),
    ),
    'shanghai': (
        Incident('T03', 18, 66, '南侧跑道低空滞留扰民', '请离开跑道附近。', post='south'),
        Incident('T04', 26, 86, '西岸展馆市集人群上空出现未报备低飞无人机',
                 '请离开西岸展馆人群上空。', 'market', 'market'),
        Incident('T02', 28, 50, '楼宇遮挡信号丢失', '沿用最后观测位置，等待接续观测。', occluded=True),
        Incident('T05', 59, 108, '低电量紧急备降', '请飞往徐汇滨江开阔草坪备降。', 'lawn'),
    ),
}

# Imagery-checked landmarks and flight staging points serve different purposes. Airborne
# staging points may be above water, and are never used for foot patrol routing.
AIR = {
    'changsha': ((112.9624, 28.1746), (112.9631, 28.1739), (112.9594, 28.1852)),
    'guangzhou': ((113.3227, 23.1155), (113.3182, 23.11397), (113.3196, 23.1099)),
    'shanghai': ((121.4592, 31.1687), (121.45728, 31.16771), (121.4606, 31.16731)),
}


class Walk:
    def __init__(self, target, at, points, redirected=False):
        self.target, self.at, self.points, self.redirected = target, at, tuple(points), redirected
        self.lengths = tuple(metres(a, b) for a, b in zip(self.points, self.points[1:]))
        self.seconds = max(12, round(sum(self.lengths) / 1.5))

    def position(self, at):
        """Return the interpolated position, remaining route and path already walked."""
        remaining = sum(self.lengths) * min(1, max(0, (at - self.at) / self.seconds))
        for index, length in enumerate(self.lengths):
            if remaining < length:
                point = mix(self.points[index], self.points[index + 1], remaining / length)
                return point, (point, *self.points[index + 1:]), (*self.points[:index + 1], point)
            remaining -= length
        return self.points[-1], (), self.points

    def return_path(self, at, station):
        _, _, travelled = self.position(at)
        back = list(reversed(travelled))
        return tuple(back[:back.index(station) + 1])


class CivicReplay:
    def __init__(self, scene, mode):
        self.city = scene['city']
        self.config = CIVIC_SITES[self.city]
        self.incidents = INCIDENTS[self.city]
        self.station = tuple(scene['station'])
        self.name = '巡查员·' + self.config['staff']
        self.mode = mode
        self.mode_changes = {}
        self.commands = {}
        self.frames = {}

    def command(self, at, target, choice):
        self.commands.setdefault(at, []).append((target, choice))
        self.frames = {second: frame for second, frame in self.frames.items() if second < at}

    def set_mode(self, at, mode):
        self.mode_changes[at] = mode
        self.frames = {second: frame for second, frame in self.frames.items() if second < at}

    def aircraft(self, at, active, selected=''):
        first, second, low = AIR[self.city]
        rows = []
        for number in range(1, 6):
            target = f'T{number:02d}'
            event = next((i for i in self.incidents if i.target == target or
                          self.city == 'changsha' and target == 'T05' and i.site == 'crowd'), None)
            start = (first, second, low, first, low)[number - 1]
            if self.city == 'changsha' and number == 3 and at < 33:
                continue
            if self.city == 'shanghai' and number == 3:
                start = (121.45844, 31.16734)
            incident = bool(event and event.start <= at < active.get(event.target, event.end))
            end = active.get(event.target, event.end) if event else 120
            land = bool(event and event.site == 'lawn')
            height = 62
            label = f'已报备航拍机 {number:02d}'
            filed = number != 4 and not (self.city == 'changsha' and number == 5)
            if not filed and event and at < max(0, event.start - (21 if self.city == 'changsha' else 5)):
                continue
            if not filed and at >= end:
                continue
            if not filed:
                label = f'未报备航拍机 {number:02d}'
            finish = (start[0] + .0014, start[1] + .00012)
            if self.city == 'shanghai' and number in (2, 3):
                finish = (start[0] + .00055, start[1] - .00051)
            if self.city == 'guangzhou' and number == 3:
                start, finish = (113.3238, 23.1149), (113.3269, 23.1146)
            point = mix(start, finish, at / 120)
            state, age, level, heading = '飞行中', 0, 1, 90
            if event:
                site = self.config['sites'].get(event.site)
                spot = site[1] if site else start
                if land and at >= event.start:
                    travel = max(1, end - event.start)
                    fraction = min(1, (at - event.start) / travel)
                    point = mix(mix(start, finish, event.start/120), spot, fraction)
                    heading = degrees(atan2(spot[0] - start[0], spot[1] - start[1])) % 360
                    height = 62 * min(1, (1 - fraction) * 4)
                    if at >= end:
                        state, label = '已降落', '已在草坪降落'
                elif event.site in ('crowd', 'show', 'school', 'market'):
                    spread = .00005 if number == 5 else 0
                    spot = (spot[0] + spread, spot[1] + spread)
                    if at < event.start:
                        if filed:
                            point = mix(start, spot, at / event.start)
                        else:
                            approach = 21 if self.city == 'changsha' else 5
                            inbound = ((112.9625, 28.1734 + spread) if self.city == 'changsha' else
                                       (spot[0] + .00055, spot[1] - .00027))
                            point = mix(inbound, spot, (at - event.start + approach) / approach)
                    else:
                        leaving = max(event.start, end - (28 if event.site == 'crowd' else 11))
                        exit_point = start if filed or self.city == 'changsha' else (spot[0] + .001, spot[1] - .0005)
                        point = mix(spot, exit_point, min(1, max(0, (at - leaving) / max(1, end - leaving))))
                        height = 45 if not filed else 62
                elif event.occluded and incident:
                    point = mix(start, finish, (event.start - 1) / 120)
                    age = at - event.start + 1
                    state = '失联' if age >= 3 else '飞行中'
                elif self.city == 'guangzhou' and number == 3:
                    height = (min(136, 95 + max(0, at - 24) * 6) if at < 32 else
                              max(95, 136 - (at - 32) * 4.6))
                elif self.city == 'changsha' and number == 2 and incident:
                    point = (point[0], point[1] - .00062 * (1 - abs((at - 16) / 6 - 1)))
                if incident:
                    level = 3 if not filed or land else 2
                    label = ('高楼遮挡 · 等待重现' if state == '失联' else
                             '超高 ' + str(round(height)) + 'm' if self.city == 'guangzhou' and number == 3 else
                             '偏航提醒' if self.city == 'changsha' and number == 2 else event.title)
            rows.append(dict(id=target, name=f'{"已报备" if filed else "未报备"}航拍机 {number:02d}',
                             lon=point[0], lat=point[1], altitude=height, heading=heading,
                             label=label, state=state, level=level, filed=filed, age=age,
                             selected=selected == target, civic=True))
        return rows

    def frame(self, at):
        """Rebuild only when decisions change; seeking has no side effects."""
        if at in self.frames:
            return self.frames[at]
        previous = self.frame(at - 1) if at else dict(log=(), pending={}, ends={}, walk=None, arrivals={}, mode=self.mode)
        mode = self.mode_changes.get(at, previous['mode'])
        pending, ends, arrivals = (previous[key].copy() for key in ('pending', 'ends', 'arrivals'))
        log = list(previous['log'])
        walk = previous['walk']
        aircraft = {r['id']: (r['lon'], r['lat']) for r in self.aircraft(at, ends)}

        def record(event, tag, text, point=None):
            log.append(dict(at=at, target=event.target, tag=tag, text=text,
                            point=point or aircraft.get(event.target)))

        def dispatch(event):
            nonlocal walk
            path = self.config['walk'][event.post]
            redirected = bool(walk and walk.target and walk.target != event.target and walk.target not in arrivals)
            if walk:
                back = walk.return_path(at, self.station) if walk.target else walk.position(at)[1]
                path = (*back[:-1], *path)
            walk = Walk(event.target, at, path, redirected)
            if event.site:
                exit_time = 28 if event.site == 'crowd' else 11
                ends[event.target] = max(event.end, min(120, at + walk.seconds + 3 + exit_time))
            if redirected:
                record(self.incidents[0], '自动提醒', '南侧飞手已收到电子提醒，请先避开跑道。')
            record(event, '动态改派' if redirected else '派员前往',
                   f'{self.name}{"折返，转往" if redirected else "沿步道前往"}现场，约 {walk.seconds} 秒到达。',
                   path[0])

        for event in self.incidents:
            if event.start == at:
                record(event, '发现异常', event.title)
                tag = '安全引导' if event.site == 'lawn' else '持续关注' if event.target == 'T02' and self.city == 'shanghai' else '自动提醒'
                record(event, tag, event.response)
                if event.post:
                    if mode == 'auto':
                        dispatch(event)
                    else:
                        pending[event.target] = bool(walk and walk.target and walk.target not in arrivals)
                        record(event, '待确认', '建议改派巡查员。' if pending[event.target] else '建议派巡查员现场核验。')
            if event.post and at == event.end - 8 and event.target in pending:
                pending.pop(event.target)
                record(event, '自动提醒', '现场请求已到期，继续通过电子提醒处理。')
        if mode == 'auto' and pending:
            for target in tuple(pending):
                dispatch(next(i for i in self.incidents if i.target == target))
                pending.pop(target)
        for target, choice in self.commands.get(at, ()):
            event = next((i for i in self.incidents if i.target == target), None)
            if not event:
                continue
            if target in pending and choice in ('accept', 'decline'):
                pending.pop(target)
                if choice == 'accept':
                    dispatch(event)
                else:
                    record(event, '自动提醒', '收到值班员决定，维持当前人员安排。')
            elif choice == 'remind':
                record(event, '自动提醒', event.response)
            elif choice == 'withdraw' and walk and walk.target == target:
                walk = Walk('', at, walk.return_path(at, self.station))
                record(event, '人员撤回', f'{self.name}返回服务驿站。')
            elif choice == 'finish' and event.site != 'lawn':
                ends[target] = at
                pending.pop(target, None)
                if walk and walk.target == target:
                    walk = Walk('', at, walk.return_path(at, self.station))
        if walk and at >= walk.at + walk.seconds and walk.target not in arrivals:
            if walk.target:
                event = next(i for i in self.incidents if i.target == walk.target)
                arrivals[walk.target] = at
                ends[walk.target] = max(ends.get(walk.target, event.end), min(120, at + 13))
                record(event, '现场核验', f'{self.name}已到达，正在现场核验和劝导。', walk.points[-1])
            else:
                walk = None
        for event in self.incidents:
            if at == ends.get(event.target, event.end):
                close = ('已在公园草坪安全降落。' if event.site == 'lawn' else
                         '无人机飞出楼影，已接续追踪。' if self.city == 'shanghai' and event.target == 'T02' else
                         '飞手已配合，事件已妥善结束。')
                record(event, '已化解', close)
                if walk and walk.target == event.target:
                    walk = Walk('', at, walk.return_path(at, self.station))
                pending.pop(event.target, None)
        result = dict(log=tuple(log), pending=pending, ends=ends, walk=walk, arrivals=arrivals, mode=mode)
        self.frames[at] = result
        return result

    def layers(self, at, selected=''):
        frame = self.frame(at)
        aircraft = self.aircraft(at, frame['ends'], selected)
        previous = {row['id']: (row['lon'], row['lat']) for row in self.aircraft(max(0, at - 1), frame['ends'])}
        by_id = {row['id']: (row['lon'], row['lat']) for row in aircraft}
        for row in aircraft:
            point = by_id[row['id']]
            row['speed'] = metres(previous.get(row['id'], point), point)
        highlights, links = [], []
        for event in self.active(at):
            point = by_id.get(event.target)
            if event.site:
                name, site, radius = self.config['sites'][event.site]
                if event.site != 'lawn' and (not point or metres(site, point) > radius + 27):
                    continue
                recovery = event.site == 'lawn'
                highlights.append(dict(point=site, name='安全降落草坪' if recovery else name,
                                       radius=radius, recovery=recovery))
                if recovery and point:
                    links.append(dict(start=point, end=site, label='安全引导', recovery=True))
        reminders = {r['target']: r['at'] for r in frame['log'] if r['tag'] == '自动提醒' and at - r['at'] < 4}
        for target in sorted(reminders):
            if target in by_id:
                links.append(dict(start=self.station, end=by_id[target], label='已发提醒',
                                  opacity=1 - (at - reminders[target]) / 5))
        walk = frame['walk']
        position, route, _ = walk.position(at) if walk else (self.station, (), ())
        eta = max(0, walk.at + walk.seconds - at) if walk else 0
        status = ('已改派' if walk.redirected else '前往中') if eta else '现场核验' if walk else '待命'
        marker = dict(id='patrol', name=self.name, lon=position[0], lat=position[1], status=status,
                      eta_s=eta, route=route, target_point=by_id.get(walk.target) if walk else None)
        return dict(civic_replay=True, aircraft=aircraft, zones=[], routes=[], active_highlights=highlights,
                    staff_markers=[marker], civic_links=links)

    def active(self, at):
        ends = self.frame(at)['ends']
        return [event for event in self.incidents if event.start <= at < ends.get(event.target, event.end)]
