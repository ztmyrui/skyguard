"""Connect desktop clock ticks to the user's own local recorded flight."""
from datetime import timedelta

from PySide6.QtCore import QObject, Signal

from ...actual_flight import track_layers, track_review, route_for
from ...disposal import REPLIES, apply, observe_point, walking
from ...flight_route import ROUTE_LIMITS, FlightRoute, planned_position
from ...flight_rules import instant
from ...sim_flight import Aircraft, advance, measurement, new_flight
from ...zone_catalog import Zone, read_table, zone_from_record, zone_hits


class RecordedFlight(QObject):
    changed = Signal()
    ticked = Signal()

    def __init__(self, context, *, resume=True):
        super().__init__(context)
        self.context = context
        self.state = None
        self.identifier = None
        self.target = None
        self.control = '手动'
        self.wind = None
        self.paused = False
        self.follow = True
        self.route = None
        self._zones_cache = {}
        self._review_cache = {}
        if resume:
            context.clock_changed.connect(self.tick)
            context.dataset_changed.connect(self.restore)
            self.restore()
        else:
            context.dataset_changed.connect(self.clear_cache)

    def clear_cache(self):
        self._zones_cache.clear()
        self._review_cache.clear()

    @property
    def clock(self):
        return self.context.clock

    @property
    def plan(self):
        return self.find(self.identifier)

    def find(self, target):
        return next((p for p in self.context.civic.data['plans'] if p['id'] == target), None)

    def restore(self):
        self._zones_cache.clear()
        self._review_cache.clear()
        plan = next((row for row in self.context.civic.data['plans'] if row.get('actual_track')
                     and row.get('started_at') and not row.get('ended_at')), None)
        if plan:
            sample = plan['actual_track'][-1]
            self.identifier = plan['id']
            self.route = route_for(plan)
            self.state = Aircraft(sample['lon'], sample['lat'], sample['height'], sample['heading'],
                                  sample['speed'], sample['t'], sample['mode'], sample['battery'])
            self.target = None
            self.control = '手动' if sample['mode'] == '已降落' else sample['mode']
        else:
            self.state = self.identifier = self.route = None
        self.changed.emit()

    def start(self, plan):
        if self.state:
            raise ValueError('请先结束当前飞行')
        when = self.clock.now().replace(microsecond=0)
        route = FlightRoute.parse(plan['query']['route']).shifted(when)
        state = new_flight(route)
        sample = dict(measurement(state), at=when.isoformat())
        self.context.civic.start_recorded_flight(plan['id'], when, sample)
        self.identifier, self.route, self.state = plan['id'], route, state
        self.context.public_city = plan['city']
        self.target, self.wind = None, None
        self.control, self.paused, self.follow = '手动', False, True
        self.context.cap_manual_speed()
        self.changed.emit()
        if self.context.weather_lookup.configured() and not (self.clock.demo and self.context.demo_wind):
            identifier = self.identifier

            def use_forecast(forecast):
                if identifier != self.identifier or self.state is None:
                    return
                hour_at = when.replace(minute=0, second=0, microsecond=0)
                hour = next((entry for entry in forecast.hours if entry.time == hour_at), None)
                if hour and hour.wind is not None and hour.wind_from_deg is not None:
                    self.wind = ((hour.wind_from_deg + 180) % 360, hour.wind)

            self.context.run_aerial_lookup(lambda: self.context.weather_lookup.forecast(route.takeoff),
                                           use_forecast, lambda: None)

    def command(self, mode):
        if self.state and self.state.mode != '失联':
            self.target = None
            self.control = mode
            self.context.cap_manual_speed()
            self.changed.emit()

    def drag(self, lon, lat):
        if self.state and self.state.mode != '失联':
            if self.control != '手动':
                self.command('手动')
            self.target = (lon, lat, self.target[2] if self.target else max(8, self.state.height))

    def altitude(self, delta):
        if self.state and self.state.mode != '失联':
            if self.control != '手动':
                self.command('手动')
            lon, lat, height = self.target or (*self.state.position, self.state.height)
            self.target = (lon, lat, max(0, min(500, height + delta)))

    def toggle_pause(self):
        if self.clock.demo:
            self.clock.play(not self.clock.playing)
            self.context.notify_clock(force=True)
        else:
            self.paused = not self.paused
            self.ticked.emit()

    def tick(self):
        if self.state is None:
            return
        elapsed = int((self.clock.now() - self.route.start).total_seconds())
        samples = []
        for _ in range(max(0, elapsed - self.state.second)):
            mode = self.control
            if mode == '航线' and self.state.route_second >= (self.route.end - self.route.start).total_seconds():
                mode = '返航'
            if not self.state.battery and mode != '失联':
                mode = '返航'
            wind = self.wind
            if self.clock.demo and self.context.demo_wind:
                direction, speed = self.context.demo_wind
                wind = ((direction + 180) % 360, speed)
            elif self.clock.demo and wind is None:
                wind = (180, 3)
            self.state = advance(self.state, self.route, target=None if self.paused else self.target,
                                 mode='手动' if self.paused and mode != '失联' else mode, wind=wind)
            at = self.route.start + timedelta(seconds=self.state.second)
            samples.append(dict(measurement(self.state), at=at.isoformat()))
            if self.state.mode == '已降落':
                self.context.civic.append_flight_samples(self.identifier, samples, ended=at)
                self.state = None
                self.changed.emit()
                return
        if samples:
            self.context.civic.append_flight_samples(self.identifier, samples)
            self.ticked.emit()

    def finish(self):
        if self.state is None:
            raise ValueError('暂无进行中的鼠标飞行')
        self.tick()
        if self.state:
            now = self.clock.now()
            self.context.civic.append_flight_samples(self.identifier, (), ended=max(
                now, self.route.start + timedelta(seconds=1)))
            self.state = None
            self.changed.emit()

    def zones(self, city, demo):
        key = (city, demo, id(self.context.civic.data.get('cities')), id(self.context.training.data.get('zones')))
        if key not in self._zones_cache:
            book = self.context.civic.book(city)
            rows = tuple(Zone(rule.id, rule.name, 'notice', geometry, rule.start, rule.end,
                              rule.source, True, True)
                         for rule, geometry in zip(book.rules, book.shapes)
                         if rule.kind == 'restriction' and rule.verified and rule.demo == demo)
            if self.clock.demo:
                rows += tuple(zone_from_record(row, self.context.training.data['types'])
                              for row in self.context.training.data['zones'].get(city, ()))
            self._zones_cache = {key: rows}
        return self._zones_cache[key]

    def report(self, plan, cursor=None):
        track = plan.get('actual_track', ())
        if cursor is not None:
            track = [p for p in track if instant(p['at']) <= cursor]
        rules = self.context.training.data['rules']
        zones = self.zones(plan['city'], plan['query'].get('demo', False))
        width = self.context.civic.data.get('route_tolerances', {}).get(plan['query']['category'],
                  read_table('route-tolerances.json')['by_category'][plan['query']['category']])
        key = (plan['id'], len(track), repr(plan.get('flight_actions')), id(zones), id(rules), width)
        if key not in self._review_cache:
            if len(self._review_cache) > 96:
                self._review_cache.clear()
            self._review_cache[key] = track_review(plan, track, zones=zones, rules=rules, width=width)
        return self._review_cache[key]

    def stats(self):
        if not self.state:
            return ''
        plan = self.plan
        future = planned_position(self.route, self.route.start + timedelta(seconds=self.state.second))
        from ...flight_route import WGS84
        distance = 0 if future is None else WGS84.Inverse(future.lat, future.lon, self.state.lat, self.state.lon)['s12']
        notice = zone_hits(self.state.position, self.clock.now(), self.zones(plan['city'], plan['query'].get('demo', False)))
        suffix = ' · 核对高度' if self.state.height > min(120, ROUTE_LIMITS[self.route.category][1]) else ''
        if notice:
            suffix += f' · {notice[0].name}'
        return f'高 {self.state.height:.0f} 米 · 速度 {self.state.speed:.1f} 米/秒 · 偏离 {distance:.0f} 米{suffix}'

    def map_layers(self, plan, cursor=None, public=False):
        layers = track_layers(plan, self.report(plan, cursor), cursor=cursor, public=public)
        if public:
            layers.update(flight_control=bool(self.state), flight_follow=self.follow)
        return layers

    def perform(self, plan, kind, *, result='', reply_to=None):
        titles = {'nudge': '核对飞行状态', 'call': '联络飞手', 'dispatch': '观察岗核验',
                  'reminder_reply': '飞手回复', 'request': '失联求助', 'request_ack': '求助回执',
                  'returned': '回到航线'}
        if kind == 'reminder_reply' and result not in REPLIES:
            raise ValueError('请选择回复内容')
        details = dict(title=titles[kind], plan_id=plan['id'])
        if reply_to:
            if kind == 'reminder_reply' and not any(
                    move['id'] == reply_to and move['kind'] == 'nudge' for move in plan.get('flight_actions', ())):
                raise ValueError('请刷新后重新选择提醒')
            details['reply_to'] = reply_to
        if kind == 'dispatch':
            city = self.context.civic.cities()[plan['city']]
            posts = [post for post in city.get('posts', ()) if post.get('enabled', True)]
            staff = [person for person in self.context.civic.data['staff'] if person['available']]
            if not posts or not staff:
                raise ValueError('请先在管理中设置开放的观察岗和空闲人员')
            point, name = observe_point(posts)
            index = {post['id']: post for post in posts}
            employed = {row.get('person_id') for saved in self.context.civic.data['plans']
                        for row in saved.get('flight_actions', ()) if row.get('state') == '进行中'}
            choices = []
            for person in staff:
                anchor = index.get(person['post_id'])
                if person['id'] not in employed and anchor:
                    route = walking((anchor['lon'], anchor['lat']), point)
                    choices.append((route['seconds'], person['id'], person['name'], route))
            if not choices:
                raise ValueError('暂无空闲人员')
            _, person, name_staff, route = min(choices, key=lambda row: row[:2])
            details.update(place=name, walk=route, person_id=person, person=name_staff)
        second = max(0, int((self.clock.now() - instant(plan['started_at'])).total_seconds()))
        actions, entry = apply(plan.get('flight_actions', []), plan['id'], kind, second, result=result, **details)
        self.context.civic.save_observation_actions(plan['id'], actions, titles[kind])
        self.changed.emit()
        return entry

    def verify(self, plan, outcome, note):
        result = self.report(plan)
        comparison = {key: result[key] for key in ('horizontal_m', 'height_m', 'duration_s')}
        comparison['count'] = len(result['intervals'])
        filing = self.context.civic.review_recorded_flight(
            plan['id'], outcome, note, self.clock.now(), comparison)
        self.changed.emit()
        return filing
