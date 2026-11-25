"""Local city catalogs, flight plans, and resource imports."""
import csv
import io
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from .system_clock import now as system_now
from uuid import uuid4

from .filing import apply_receipt, change_state, create_filing, decode_package, encode_package, correspondence, route_filing
from .flight_rules import DEMO_START, Rule, RuleBook, demo_city, instant, lonlat, web_source


STAFF_FIELDS = ('id', 'name', 'role', 'start_s', 'end_s', 'post_id', 'available')
ROLES = ('现场核验', '联络核实')
PUBLIC_CITIES = {
    'guangzhou': ('广州', (113.2644, 23.1291)),
    'shanghai': ('上海', (121.4737, 31.2304)),
    'shenzhen': ('深圳', (114.0579, 22.5431)),
    'hangzhou': ('杭州', (120.1551, 30.2741)),
}


def _query_identity(left, right):
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def unique_ids(rows):
    ids = [str(row['id']).strip() for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError('编号为空或重复。')


def validate_city(document):
    city = deepcopy(document)
    city['id'], city['name'] = str(city['id']).strip(), str(city['name']).strip()
    if not city['id'] or not city['name'] or len(city['name']) > 40:
        raise ValueError('请填写城市编号和名称。')
    city['center'] = lonlat(city['center'])
    city['sources'] = [web_source(s) for s in city.get('sources', [])]
    rules = city.setdefault('rules', [])
    if len(rules) > 1000:
        raise ValueError('单个规则包最多 1000 条。')
    unique_ids(rules)
    city['rules'] = [Rule.parse(row).record() for row in rules]
    posts = city.setdefault('posts', [])
    unique_ids(posts)
    for post in posts:
        post['lon'], post['lat'] = lonlat((post['lon'], post['lat']))
        post['range_m'] = int(post['range_m'])
        post['open_s'], post['close_s'] = int(post['open_s']), int(post['close_s'])
        if not 1 <= post['range_m'] <= 5000 or not 0 <= post['open_s'] < post['close_s'] <= 1800:
            raise ValueError('请核对观察范围和岗位开放时间。')
        if not post['name'].strip() or type(post['enabled']) is not bool:
            raise ValueError('请核对岗位名称和启用状态。')
    ids = {post['id'] for post in posts}
    for edge in city.setdefault('walk', []):
        edge['seconds'] = int(edge['seconds'])
        if edge['from'] not in ids or edge['to'] not in ids or not 1 <= edge['seconds'] <= 1800:
            raise ValueError('步行时间需要引用有效岗位，且以秒填写。')
    return city


def default_staff():
    return [
        {'id': 'p1', 'name': '现场人员甲', 'role': '现场核验', 'start_s': 0, 'end_s': 1800,
         'post_id': 'east', 'available': True},
        {'id': 'p2', 'name': '值班人员乙', 'role': '联络核实', 'start_s': 0, 'end_s': 1800,
         'post_id': 'east', 'available': True},
        {'id': 'p3', 'name': '协作人员丙', 'role': '现场核验', 'start_s': 0, 'end_s': 1800,
         'post_id': 'north', 'available': True},
    ]


def parse_staff(rows, city):
    unique_ids(rows)
    posts = {post['id'] for post in city['posts']}
    result = []
    for row in rows:
        record = {key: row[key] for key in STAFF_FIELDS}
        record['id'], record['name'] = record['id'].strip(), record['name'].strip()
        record['start_s'], record['end_s'] = int(row['start_s']), int(row['end_s'])
        flag = str(row['available']).lower().strip()
        if flag not in {'true', 'false', '1', '0', '在岗', '不可用'}:
            raise ValueError('可用状态填写 true、false、在岗或不可用。')
        record['available'] = flag in {'true', '1', '在岗'}
        if (not record['name'] or record['role'] not in ROLES or record['post_id'] not in posts
                or not 0 <= record['start_s'] < record['end_s'] <= 1800):
            raise ValueError(f'人员 {record["id"]} 的姓名、岗位、班次或地点无效。')
        result.append(record)
    return result


class CivicService:
    def __init__(self, repository):
        self.repo = repository
        self.data = repository.load() or {'cities': {'changsha': demo_city()},
                                          'staff': default_staff(), 'plans': [], 'announcements': []}
        for identifier, (name, center) in PUBLIC_CITIES.items():
            self.data['cities'].setdefault(identifier, {
                'id': identifier, 'name': name, 'center': center, 'rules': [],
                'sources': ['https://uom.caac.gov.cn/'], 'posts': [], 'walk': [],
            })
        for key in ('favorite_places', 'flight_logs', 'filings'):
            self.data.setdefault(key, [])
        self.data.setdefault('watch_minutes', 30)
        for plan in self.data['plans']:
            plan.setdefault('preflight', [])
        self.books = {}
        self.exercise_rules = {}
        self._remove_retired_ranges()

    def _remove_retired_ranges(self):
        retired = {'circle', 'area'}
        removed = {p['id'] for p in self.data['plans'] if p['query'].get('form') in retired}
        retired_filings = {r['id'] for r in self.data['filings']
                           if r['plan_id'] in removed or r['snapshot']['query'].get('form') in retired}
        if not (removed or retired_filings):
            return
        state = deepcopy(self.data)
        state['plans'] = [p for p in state['plans'] if p['id'] not in removed]
        state['filings'] = [r for r in state['filings'] if r['id'] not in retired_filings]
        for log in state['flight_logs']:
            if log.get('plan_id') in removed:
                log['plan_id'] = None
        self.commit(state, '移除旧版圆形与多边形记录',
                    {'plans': len(removed), 'filings': len(retired_filings)})

    def cities(self):
        return self.data['cities']

    def book(self, city_id):
        if city_id not in self.books:
            self.books[city_id] = RuleBook(self.cities()[city_id]['rules'],
                                           exercise=self.exercise_rules.get(city_id, ()))
        return self.books[city_id]

    def set_exercise_rules(self, city, records):
        value = tuple(records)
        if value == self.exercise_rules.get(city, ()):
            return
        if value:
            self.exercise_rules[city] = value
        else:
            self.exercise_rules.pop(city, None)
        self.books.pop(city, None)

    def commit(self, data, action, detail):
        self.repo.commit(data, action, detail)
        self.data = data

    def import_city(self, text):
        city = validate_city(json.loads(text))
        if city['id'] == 'changsha':
            parse_staff(self.data['staff'], city)
        state = deepcopy(self.data)
        state['cities'][city['id']] = city
        self.commit(state, '发布城市规则包', {'city': city['id'], 'rules': len(city['rules'])})
        self.books.pop(city['id'], None)
        return city['id']

    def save_staff(self, rows):
        records = parse_staff(rows, self.cities()['changsha'])
        state = deepcopy(self.data)
        state['staff'] = records
        self.commit(state, '更新人员与班次', {'count': len(records)})

    def import_staff(self, text):
        self.save_staff(list(csv.DictReader(io.StringIO(text))))

    def save_announcement(self, city_id, title, source, content):
        if not title.strip() or not content.strip():
            raise ValueError('公告需要标题和正文。')
        state = deepcopy(self.data)
        identifier = uuid4().hex
        state['announcements'].append({'id': identifier, 'city': city_id, 'title': title.strip(),
                                       'source': web_source(source.strip()), 'text': content.strip()})
        self.commit(state, '登记公告草稿', {'city': city_id, 'id': identifier})

    def save_plan(self, city_id, title, query, result, identifier=None, *, verdict=None, aerial=None,
                  preflight=None, purpose=None, material=None, basis=None, draft=False, mission=None, trip_snapshot=None):
        if query.route is None:
            raise ValueError("请先画航线")
        query.validate()
        query.geometry()
        if not title.strip():
            raise ValueError('请输入计划名称。')
        state = deepcopy(self.data)
        existing = next((item for item in state['plans'] if item['id'] == identifier), None)
        if identifier and (existing is None or existing['cancelled']):
            raise ValueError('这份计划已经撤销或不存在。')
        if existing and not existing['query'].get('route'):
            raise ValueError('旧版范围报备 · 只读')
        if existing and existing.get('started_at'):
            raise ValueError('飞行记录已开始，请另存新计划')
        record = {'id': identifier or uuid4().hex, 'city': city_id, 'title': title.strip(), 'draft': draft,
                  'query': query.record(), 'result': result, 'cancelled': False,
                  'saved_at': system_now(timezone.utc).isoformat()}
        if self.repo.path.parent.name == 'demo':
            record['dataset'] = '演示'
        if preflight is not None:
            record['preflight'] = sorted(set(preflight))
        elif not existing:
            record['preflight'] = []
        for key, value in (('purpose', purpose), ('material', material), ('basis', basis),
                           ('mission', mission), ('trip_snapshot', trip_snapshot)):
            if value is not None:
                record[key] = value
        if verdict is not None:
            record['verdict'] = verdict
        if aerial is not None:
            record['aerial'] = aerial
        if existing:
            existing.update(record)
            if verdict is None:
                existing.pop('verdict', None)
            if aerial is None:
                existing.pop('aerial', None)
        else:
            state['plans'].append(record)
        self.commit(state, '保存本机飞行计划', {'id': record['id'], 'city': city_id})
        return record['id']

    def hide_plan(self, identifier):
        data = deepcopy(self.data)
        plan = next((plan for plan in data['plans'] if plan['id']==identifier), None)
        if plan and not plan['cancelled']:
            plan['cancelled'] = True
            self.commit(data, '移出飞行计划', {'id':identifier})

    def cancel_plan(self, identifier):
        state = deepcopy(self.data)
        plan = next((item for item in state['plans'] if item['id'] == identifier), None)
        if not plan or plan['cancelled']:
            raise ValueError('请选择一份有效计划。')
        if not plan['query'].get('route'):
            raise ValueError('旧版范围报备 · 只读')
        if plan.get('started_at') and not plan.get('ended_at'):
            raise ValueError('请先结束飞行')
        plan['cancelled'] = True
        self.commit(state, '撤销本机飞行计划', {'id': identifier})

    def save_favorite(self, city, name, point, identifier=None):
        point = lonlat(point)
        if city not in self.cities() or not name.strip():
            raise ValueError('请选择城市并填写收藏名称')
        state = deepcopy(self.data)
        existing = next((item for item in state['favorite_places'] if item['id'] == identifier), None)
        if identifier and existing is None:
            raise ValueError('请选择要修改的收藏')
        if existing:
            existing['name'] = name.strip()[:80]
        else:
            identifier = uuid4().hex
            state['favorite_places'].append(dict(id=identifier, city=city, name=name.strip()[:80],
                point=list(point), created_at=system_now(timezone.utc).isoformat(), last_check=None))
        self.commit(state, '保存常用飞行点', {'id': identifier})
        return identifier

    def update_favorite_checks(self, city, query, verdict, now):
        if query.demo:
            return
        place = query.route.takeoff if query.route else query.points[0]
        state = deepcopy(self.data)
        changed = []
        for item in state['favorite_places']:
            if item['city'] == city and all(abs(a-b) < .000005 for a,b in zip(item['point'], place)):
                item['last_check'] = {'verdict': verdict, 'at': now.isoformat()}
                changed.append(item['id'])
        if changed:
            self.commit(state, '更新收藏点核对结果', {'favorites': changed})

    def delete_favorite(self, identifier):
        self._delete('favorite_places', identifier, '删除常用飞行点')

    def _delete(self, key, identifier, action):
        state = deepcopy(self.data)
        state[key] = [row for row in state[key] if row['id'] != identifier]
        if len(state[key]) == len(self.data[key]):
            raise ValueError('请选择有效记录')
        self.commit(state, action, {'id': identifier})

    def save_log(self, fields, identifier=None):
        from .flight_log import validate_record
        record = validate_record(fields)
        state = deepcopy(self.data)
        if record['plan_id']:
            plan = next((p for p in state['plans'] if p['id'] == record['plan_id']), None)
            if plan is None:
                raise ValueError('关联计划不存在')
            if plan.get('actual_track'):
                record['track_plan_id'] = plan['id']
        if self.repo.path.parent.name == 'demo':
            record['dataset'] = '演示'
        existing = next((row for row in state['flight_logs'] if row['id'] == identifier), None)
        if identifier and existing is None:
            raise ValueError('请选择有效日志')
        if existing:
            existing.update(record)
        else:
            identifier = uuid4().hex
            state['flight_logs'].append(dict(record, id=identifier))
        self.commit(state, '保存飞行日志', {'id': identifier})
        return identifier

    def delete_log(self, identifier):
        self._delete('flight_logs', identifier, '删除飞行日志')

    def update_plan_details(self, identifier, **details):
        state = deepcopy(self.data)
        plan = next(row for row in state['plans'] if row['id'] == identifier and not row['cancelled'])
        plan.update(details)
        self.commit(state, '更新飞行准备', {'id': identifier})

    def watch_settings(self, minutes):
        if minutes not in (0, 15, 30, 60, 120):
            raise ValueError('请选择检查间隔')
        state = deepcopy(self.data)
        state['watch_minutes'] = minutes
        self.commit(state, '修改公告检查间隔', {'minutes': minutes})

    def check_plan_notices(self, scans, now):
        from .plan_watch import update_notice_watch
        state = deepcopy(self.data)
        count = 0
        changed = False
        for plan in state['plans']:
            if plan['city'] not in scans:
                continue
            watch, latest = update_notice_watch(plan, scans[plan['city']], now)
            changed |= watch != plan.get('watch', {})
            plan['watch'] = watch
            count += len(latest)
        if changed:
            self.commit(state, '检查计划公告', {'alerts': count})
        return count

    def read_plan_alerts(self, identifier):
        state = deepcopy(self.data)
        plan = next(row for row in state['plans'] if row['id'] == identifier)
        for alert in plan.get('watch', {}).get('alerts', ()):
            alert['read'] = True
        self.commit(state, '查看计划提醒', {'id': identifier})

    def public_filing(self, identifier, *, contact='', purpose=None, material=''):
        state = deepcopy(self.data)
        plan = next((p for p in state['plans'] if p['id'] == identifier and not p.get('cancelled')), None)
        if plan is None:
            raise ValueError('请选择有效的飞行计划')
        if not plan['query'].get('route'):
            raise ValueError('旧版范围报备 · 只读')
        if plan.get('draft') or plan.get('verdict') == 'restricted':
            raise ValueError('请核对航线。含红色航段的计划可保存草稿')
        from .flight_check import Verdict, build_flight_check
        from .flight_rules import FlightQuery
        query = FlightQuery(**plan['query'])
        evaluated = self.book(plan['city']).evaluate(query, alternatives=False)
        if build_flight_check(query, evaluated, city_id=plan['city']).verdict == Verdict.RESTRICTED:
            raise ValueError('航线有飞行限制 · 可保存草稿')
        record = create_filing(plan, system_now(timezone.utc), contact=contact,
                               purpose=purpose, material=material)
        attempt = 0
        indexed = {r['id']: r for r in state['filings']}
        while existing := indexed.get(record['id']):
            if existing['state'] != '已取消':
                return deepcopy(existing)
            attempt += 1
            record = create_filing(plan, system_now(timezone.utc), contact=contact,
                                   purpose=purpose, material=material, attempt=attempt)
        if self.repo.path.parent.name == 'demo':
            record['dataset'] = '演示'
        state['filings'].append(record)
        self.commit(state, '记入本机报备', {'filing': record['id'], 'plan': identifier, 'title': plan['title']})
        return deepcopy(record)

    def filing(self, identifier):
        record = next((r for r in self.data['filings'] if r['id'] == identifier), None)
        if not record:
            raise ValueError('请选择有效报备')
        return deepcopy(record)

    def import_filing(self, text):
        record = decode_package(text)
        if record.get('dataset') == '演示' and self.repo.path.parent.name != 'demo':
            raise ValueError('请切换到演示时间后导入')
        if record['snapshot']['query'].get('form') in ('circle', 'area'):
            raise ValueError('请选择航线报备包')
        if record['snapshot']['city'] not in self.cities():
            raise ValueError('导入前请在管理中添加对应城市')
        if any(row['id'] == record['id'] for row in self.data['filings']):
            return record['id']
        state = deepcopy(self.data)
        state['filings'].append(record)
        self.commit(state, '导入报备包', {'filing': record['id'], 'title': record['snapshot']['title']})
        return record['id']

    def save_receipt(self, text):
        receipt = decode_package(text, receipt=True)
        if receipt.get('dataset') == '演示' and self.repo.path.parent.name != 'demo':
            raise ValueError('请切换到演示时间后导入')
        state = deepcopy(self.data)
        for index, record in enumerate(state['filings']):
            if record['id'] == receipt['id']:
                updated = apply_receipt(record, receipt)
                if updated != record:
                    state['filings'][index] = updated
                    self.commit(state, '导入核对回执', {'filing': record['id'], 'plan': record['plan_id']})
                return record['id']
        raise ValueError('本机找不到与回执对应的报备')

    def filing_export(self, identifier, path, *, receipt=False):
        record = self.filing(identifier)
        path.write_text(encode_package(record, receipt=receipt), encoding='utf-8')
        self.repo.audit('导出核对回执' if receipt else '导出报备包',
                        {'filing': identifier, 'title': record['snapshot']['title']})

    def change_filing(self, identifier, action, *, event_view=None, clock=None, **options):
        state = deepcopy(self.data)
        record = next((r for r in state['filings'] if r['id'] == identifier), None)
        if record is None:
            raise ValueError('请选择有效报备')
        if action == 'match':
            result = correspondence(event_view, (record,), clock) if event_view else None
            if (result is None or result.title != '疑似对应' or not any(
                    row[0] == identifier and (route_filing(record) or row[2] <= event_view.get('accuracy_m', -1))
                    for row in result.candidates)):
                raise ValueError('该事件和报备的范围或时段需要重新核对')
            options['event_id'] = event_view['id']
        updated = change_state(record, action, **options)
        state['filings'][state['filings'].index(record)] = updated
        label = {'match':'确认对应报备','undo':'撤销对应报备','review':'回访飞手','cancel':'取消报备'}[action]
        self.commit(state, label, {'filing': identifier, 'event': updated['event_id'] or record['event_id'],
                                   'title': record['snapshot']['title'], 'status': updated['state']})
        return updated

    def plan_stage(self, plan, now):
        if self.plan_reviewed(plan['id']):
            return '已核对'
        has_log = any(log['plan_id'] == plan['id'] for log in self.data['flight_logs'])
        if plan.get('ended_at') or has_log:
            return '待核对' if has_log and plan.get('actual_track') else '已结束'
        if plan.get('started_at'):
            return '飞行中'
        from .flight_check import CHINA
        local_now = now.astimezone(CHINA)
        from .flight_route import FlightRoute
        route = FlightRoute.parse(plan['query']['route']) if plan['query'].get('route') else None
        if route and route.window_start is not None:
            first, last = route.filing_window
            if local_now >= last:
                return '已过期'
            filing = self.active_plan_filing(plan)
            if not filing:
                return '计划中'
            return '可起飞' if local_now >= first - timedelta(minutes=30) else '已报备'
        if not plan['query'].get('demo') and local_now >= instant(plan['query']['end']):
            return '已过期'
        active = any(r['plan_id'] == plan['id'] and r['state'] != '已取消'
                     and _query_identity(r['snapshot']['query'], plan['query']) for r in self.data['filings'])
        if not active:
            return '计划中'
        from .flight_check import CHINA
        day = instant(plan['query']['start']).astimezone(CHINA).date()
        return '当天' if day == now.astimezone(CHINA).date() else '已报备'

    def transition_plan(self, identifier, action, now):
        state = deepcopy(self.data)
        plan = next((p for p in state['plans'] if p['id'] == identifier and not p['cancelled']), None)
        if not plan or not plan['query'].get('route'):
            raise ValueError('请选择有效航线计划')
        stage = self.plan_stage(plan, now)
        if action == 'start' and stage in ('当天', '可起飞'):
            plan['started_at'] = now.isoformat()
        elif action == 'end' and stage == '飞行中':
            plan['ended_at'] = now.isoformat()
        else:
            raise ValueError('已更新计划阶段')
        self.commit(state, '开始飞行' if action == 'start' else '结束飞行', {'id': identifier})
        return True

    def active_plan_filing(self, plan):
        return next((r for r in self.data['filings'] if r['plan_id'] == plan['id']
                     and r['state'] != '已取消' and _query_identity(r['snapshot']['query'], plan['query'])), None)

    def start_recorded_flight(self, identifier, when, sample):
        state = deepcopy(self.data)
        plan = next((row for row in state['plans'] if row['id'] == identifier and not row['cancelled']), None)
        if not plan or self.plan_stage(plan, when) not in ('当天', '可起飞'):
            raise ValueError('当前计划尚未进入可起飞时段')
        if any(row.get('started_at') and not row.get('ended_at') and row.get('actual_track')
               for row in state['plans']):
            raise ValueError('请先结束当前飞行')
        filing = self.active_plan_filing(plan)
        if not filing:
            raise ValueError('请先完成本机报备')
        record = next(row for row in state['filings'] if row['id'] == filing['id'])
        if record['state'] == '已报备':
            record.update(change_state(record, 'match', event_id='recorded:'+identifier))
        elif record.get('event_id') != 'recorded:'+identifier or record['state'] != '已对应':
            raise ValueError('该报备已有核对对象')
        plan.update(started_at=when.isoformat(), actual_track=[sample], source='鼠标模拟记录',
                    filing_id=filing['id'], flight_actions=[])
        self.commit(state, '开始鼠标飞行并对应报备', {'id':identifier, 'filing':filing['id']})

    def append_flight_samples(self, identifier, samples, *, ended=None):
        state = deepcopy(self.data)
        plan = next(p for p in state['plans'] if p['id']==identifier)
        if plan.get('ended_at'):
            return
        track = plan.get('actual_track', [])
        last = track[-1]['t'] if track else -1
        track.extend(p for p in samples if p['t']>last)
        plan['actual_track'] = track
        if ended:
            plan['ended_at'] = ended.isoformat()
            self.commit(state, '结束鼠标飞行', {'id':identifier, 'samples':len(track)})
        elif samples:
            self.repo.save(state)
            self.data = state

    def save_observation_actions(self, identifier, actions, title):
        state = deepcopy(self.data)
        plan = next(row for row in state['plans'] if row['id']==identifier)
        if actions != plan.get('flight_actions', []):
            plan['flight_actions'] = actions
            self.commit(state, title, {'id':identifier,'filing':plan['filing_id']})

    def review_recorded_flight(self, identifier, outcome, note, when, comparison):
        plan = next(row for row in self.data['plans'] if row['id']==identifier)
        if not plan.get('ended_at') or not plan.get('filing_id'):
            raise ValueError('结束飞行后可核对')
        filing = self.filing(plan['filing_id'])
        if filing['state'] == '已核对':
            return filing
        return self.change_filing(filing['id'], 'review', outcome=outcome, note=note,
                                  at=when, comparison=comparison)

    def plan_reviewed(self, identifier):
        plan = next((p for p in self.data['plans'] if p['id'] == identifier), None)
        return any(item['plan_id'] == identifier and item['state'] == '已核对'
                   and (not plan or _query_identity(item['snapshot']['query'], plan['query']))
                   for item in self.data['filings'])

    def export_records(self):
        return {'flight_logs': deepcopy(self.data['flight_logs']),
                'filings': deepcopy(self.data['filings']), 'civic_audit': self.repo.history()}

    def planning_context(self):
        city = self.cities()['changsha']
        return {'posts': city['posts'], 'walk': city['walk'], 'staff': self.data['staff']}

    def radar_rules(self, view, clock):
        from shapely.geometry import LineString, Point
        at = DEMO_START + timedelta(seconds=clock)
        observed_at = DEMO_START + timedelta(seconds=view['observed_s'])
        measured = Point(view['lon'], view['lat'])
        predictions = view['prediction']
        book = self.book('changsha')
        hits = []
        for rule, shape in zip(book.rules, book.shapes):
            if not rule.demo or not rule.verified or rule.kind != 'restriction':
                continue
            if view['age'] <= 8 and rule.start <= observed_at < rule.end and shape.covers(measured):
                hits.append(f'{rule.name}：观察点水平范围重叠，高度待核对')
                continue
            for first, second in zip(predictions, predictions[1:]):
                left = at + timedelta(seconds=first['seconds'])
                right = at + timedelta(seconds=second['seconds'])
                lo, hi = max(left,rule.start), min(right,rule.end)
                if lo >= hi:
                    continue
                span = (right-left).total_seconds()
                line = LineString([(first['lon'], first['lat']), (second['lon'], second['lat'])])
                a = line.interpolate((lo-left).total_seconds()/span, normalized=True)
                b = line.interpolate((hi-left).total_seconds()/span, normalized=True)
                if shape.intersects(LineString([a, b])):
                    hits.append(f'{rule.name}：预测轨迹可能接近，高度待核对')
                    break
        return hits

    def plan_candidates(self, view):
        from shapely.geometry import Point
        from .flight_rules import FlightQuery
        if view['lat'] is None or view['age'] > 90:
            return []
        at = DEMO_START + timedelta(seconds=view['observed_s'])
        location = Point(view['lon'], view['lat'])
        results = []
        for plan in self.data['plans']:
            saved = plan['query']
            if plan['cancelled'] or plan['city'] != 'changsha' or not saved['demo']:
                continue
            start, end = instant(saved['start']), instant(saved['end'])
            if not start <= at < end:
                continue
            query = FlightQuery(**dict(saved, start=start, end=end))
            if query.geometry().covers(location):
                results.append(plan['title'])
        return results
