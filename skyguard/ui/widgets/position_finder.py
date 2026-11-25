"""Pick photography topics and browse route proposals."""
from concurrent.futures import ThreadPoolExecutor
from ...system_clock import now as system_now
from datetime import timedelta
import json

from PySide6.QtCore import QDateTime, QTimeZone, QTimer, Qt, Slot
from PySide6.QtWidgets import (
    QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox, QFormLayout,
    QGridLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget,
)

from ...aerial import Forecast, displayed_score, solar_events
from ...aerial_plan import (
    LIGHT, SOURCE_LABELS, STYLE_CHOICES, STYLE_LABELS, SUBJECTS, distance, load_scenes,
    matching_place, parse_preferences, recommend, scene_from_poi, select_variant,
)
from ...aerial_weather import AIRCRAFT_PROFILES
from ...flight_check import CHINA, build_flight_check
from ...flight_model import FlightModel
from ...official_notices import Scan
from ...photo_mission import deadline, travel_stop_minutes
from .common import ActionButton, Badge, Card, label, scroll_contents
from .flight_journey import clear
from .flight_result import Disclosure


def date_window(index, now):
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if index == 1:
        return today + timedelta(days=1), today + timedelta(days=2)
    if index == 2:
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)
        if today.weekday() == 6:
            saturday = today - timedelta(days=1)
        return max(now, saturday), saturday + timedelta(days=2)
    return now, today + timedelta(days=1)


class PositionFinder(Card):
    def __init__(self, page):
        super().__init__()
        self.page = page
        self.context = page.context
        self.token = 0
        self.selected_model = ''
        self._place_map = None
        self._pending_place = None
        self.origin_point = None
        self.now = lambda: system_now(CHINA)
        self.custom = None
        self.topics = {}
        self.results = Card()
        self.results.hide()
        header = QHBoxLayout()
        header.addWidget(label('航拍方案', 'section'), 1)
        header.addWidget(ActionButton('收起方案', self.hide_results, variant='quiet'))
        self.results.body.addLayout(header)
        self.criteria = label('')
        self.results.body.addWidget(self.criteria)
        scroll, self.list = scroll_contents()
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.results.body.addWidget(scroll, 1)
        self.text = page.mission_brief
        self.text.setMaxLength(120)
        self.text.setPlaceholderText('例如：梅溪湖日落大片，慢速环绕，避开人多')
        self.text.setAccessibleName('想拍什么')
        self.text.returnPressed.connect(self.search)
        self.body.addWidget(label('想拍什么','checkTitle'))
        self.body.addWidget(self.text)
        self.body.addWidget(page.mission_time)
        page.mission_time.textChanged.connect(self.inputs_changed)
        self.period = QComboBox()
        self.period.addItems(('最近合适时段','今天','明天','本周末','自定义日期'))
        self.period.setAccessibleName('拍摄时间')
        self.period.currentIndexChanged.connect(self.choose_date)
        row = QHBoxLayout()
        row.addWidget(label('拍摄时间'),0)
        row.addWidget(self.period,1)
        self.body.addLayout(row)
        self.style = QComboBox()
        self.style.setAccessibleName('运镜风格')
        for key, title in STYLE_CHOICES:
            self.style.addItem(title, key)
        style_row = QHBoxLayout()
        style_row.addWidget(label('运镜风格'))
        style_row.addWidget(self.style, 1)
        self.body.addLayout(style_row)
        self.style.activated.connect(lambda _: self.search())
        self.custom_button = ActionButton('编辑日期', lambda: self.choose_date(4))
        self.custom_button.hide()
        self.body.addWidget(self.custom_button)
        quick = QGridLayout()
        quick.setSpacing(5)
        self.body.addLayout(quick)
        options = Disclosure('更多筛选')
        options.row.addWidget(label('更多筛选','checkTitle'),1)
        options.add_arrow()
        self.body.addWidget(options)
        filters = QWidget()
        self.body.addWidget(filters)
        filters.hide()
        options.toggled.connect(filters.setVisible)
        filters_body = QVBoxLayout(filters)
        filters_body.setContentsMargins(0,0,0,0)
        choices = QGridLayout()
        choices.setSpacing(5)
        featured = ('江河', '湖面', '城市天际线', '公园绿地', '日落', '夜景')
        other = tuple(topic for topic in (*SUBJECTS,*LIGHT) if topic not in featured)
        for topic in (*featured, *other):
            button = ActionButton(topic, None)
            button.setCheckable(True)
            button.toggled.connect(lambda active, b=button: self.highlight(b, active))
            button.toggled.connect(self.inputs_changed)
            grid, items = (quick, featured) if topic in featured else (choices, other)
            index = items.index(topic)
            grid.addWidget(button, index//3, index%3)
            self.topics[topic] = button
        filters_body.addLayout(choices)
        self.model = QComboBox()
        self.model.setAccessibleName('无人机型号（选填）')
        self.model.addItem('无人机型号（选填）', '')
        for name in AIRCRAFT_PROFILES:
            if name:
                self.model.addItem(name, name)
        filters_body.addWidget(self.model)
        self.origin = QComboBox()
        self.origin.setAccessibleName('从哪里出发（选填）')
        filters_body.addWidget(self.origin)
        self.search_button = ActionButton('找航拍方案', self.search, variant='primary')
        self.body.addWidget(self.search_button)
        self.message = label('')
        self.message.hide()
        self.body.addWidget(self.message)
        self.tomorrow = ActionButton('看明天日出', self._tomorrow)
        self.tomorrow.hide()
        self.body.addWidget(self.tomorrow, 0, Qt.AlignLeft)
        self.proposals = ()
        self.text.textChanged.connect(self.inputs_changed)
        self.period.currentIndexChanged.connect(self.inputs_changed)
        self.model.currentIndexChanged.connect(self.inputs_changed)
        self.origin.currentIndexChanged.connect(self.inputs_changed)

    @staticmethod
    def highlight(button, active):
        button.setProperty('primary', active)
        button.style().unpolish(button)
        button.style().polish(button)

    def refresh_origins(self):
        selection = self.origin.currentData()
        self.origin.clear()
        self.origin.addItem('从哪里出发（选填）', '')
        city = self.page.city_id()
        for favorite in self.context.civic.data['favorite_places']:
            if favorite['city'] == city:
                self.origin.addItem(favorite['name'], favorite['id'])
        self.origin.setCurrentIndex(max(0, self.origin.findData(selection)))

    def choose_date(self, index):
        self.custom_button.setVisible(index == 4)
        if index != 4:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('机位推荐日期')
        form = QFormLayout(dialog)
        now = system_now(CHINA)
        first, last = self.custom or (now, now + timedelta(days=1))
        fields = []
        for text, value in (('起始', first), ('结束', last)):
            editor = QDateTimeEdit()
            editor.setTimeZone(QTimeZone(8 * 3600))
            editor.setDateTime(QDateTime.fromString(value.isoformat(timespec='seconds'), Qt.ISODate))
            editor.setCalendarPopup(True)
            editor.setDisplayFormat('yyyy-MM-dd HH:mm')
            form.addRow(text, editor)
            fields.append(editor)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() == QDialog.Accepted:
            self.custom = tuple(editor.dateTime().toPython().replace(tzinfo=CHINA) for editor in fields)
        elif self.custom is None:
            self.period.setCurrentIndex(0)

    def hide_results(self):
        self.token += 1
        self._pending_place = None
        self.search_button.setEnabled(True)
        self.results.hide()
        self.page.clear_preview()

    def inputs_changed(self, *_):
        if self.token:
            self.hide_results()
        self.message.hide()

    def city_changed(self):
        self.hide_results()
        self.message.hide()
        self.refresh_origins()

    def _tomorrow(self):
        self.period.setCurrentIndex(2)
        self.search()

    @Slot(int, str)
    def _place_resolved(self, revision, payload):
        if revision != self.token or self._pending_place is None:
            return
        callback, self._pending_place = self._pending_place, None
        try:
            items = json.loads(payload)
        except (ValueError, TypeError):
            items = []
        scenes = []
        for item in items[:5] if isinstance(items, list) else ():
            try:
                name = item['name'].strip()
                if name:
                    scene = scene_from_poi(name, item['location'], item.get('type', ''), item.get('address', ''))
                    if all(distance(scene.point, previous.point) > 350 for previous in scenes):
                        scenes.append(scene)
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
        callback(tuple(scenes))

    def search(self):
        page = self.page
        city = page.city_id()
        self.tomorrow.hide()
        kind = page.mission_kind.currentData()
        text = self.text.text().strip()
        chosen = tuple(name for name, button in self.topics.items() if button.isChecked())
        model = self.model.currentData() or page.aircraft_model.currentData()
        category = page.category.currentText()
        origin = next((f['point'] for f in self.context.civic.data['favorite_places']
                       if f['id'] == self.origin.currentData()), None)
        if origin is None and page.point is not None:
            origin = page.point
        now = self.now()
        index = self.period.currentIndex() - 1
        upcoming = index == -1 and not any(word in text for word in ('今天','明天','周末'))
        if index == -1:
            index = 2 if '周末' in text else 1 if '明天' in text else 0
        first, last = ((now, now + timedelta(days=7)) if upcoming else
                       self.custom if index == 3 and self.custom else date_window(index, now))
        first = max(first, now + timedelta(minutes=15))
        timing = page.mission_time.text()
        stop = deadline(timing, first) if kind in ('promo','news') else None
        if stop is not None and stop <= first and upcoming:
            first = (first + timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
            stop = deadline(timing, first)
        if stop is not None:
            last = min(last, stop)
        if last <= first or last-first > timedelta(days=7):
            self.message.setText('请选择未来七天内的查询时段')
            self.message.show()
            return
        self.token += 1
        token = self.token
        self._pending_place = None
        lookup_generation = 0
        self.search_button.setEnabled(False)
        self.selected_model = model
        self.origin_point = origin
        style = self.style.currentData()
        preference = parse_preferences(text, kind, chosen, style)
        all_topics = (*preference.subjects, *preference.lights)
        named = matching_place(load_scenes(city), text)
        if not upcoming and index == 0 and set(all_topics).intersection(LIGHT) == {'日出'}:
            rise = solar_events(origin or self.context.civic.cities()[city]['center'], now).get('rise')
            if rise and now > rise[0] + timedelta(minutes=30):
                self.search_button.setEnabled(True)
                self.message.setText('今天日出已过')
                self.message.show()
                self.tomorrow.show()
                return
        description = ' · '.join((self.context.civic.cities()[city]['name'], first.strftime('%m-%d'), *all_topics))
        self.criteria.setText(description)
        self.message.setText('正在查找航拍方案…')
        self.message.show()
        civic, lookup = self.context.civic, self.context.weather_lookup
        book = civic.book(city)
        config = self.context.flight_model_config
        notices = self.context.notice_lookup
        ceiling = AIRCRAFT_PROFILES[model][0] if model in AIRCRAFT_PROFILES else None

        def check_with(scan):
            def check(query):
                result = book.evaluate(query, alternatives=False)
                return build_flight_check(query, result, city_id=city, scan=scan).verdict
            return check

        def proposals(topics, weather_at, scan, extra_scenes, **options):
            return recommend(city, topics, first, last, category, origin, weather_at, check_with(scan),
                             ceiling, mission_kind=kind, earliest=upcoming,
                             stop_minutes=travel_stop_minutes(timing) if kind == 'travel' else None,
                             overnight=index == 3, coverage_alternatives=not upcoming, location_hint=text,
                             extra_scenes=extra_scenes, style=style, preference=preference, **options)

        empty_weather = Forecast((), now)

        def first_pass(extra_scenes):
            topics = all_topics
            if not topics and named:
                topics = tuple(tag for tag in named[0].tags if tag != '日出日落')[:1]
            if not extra_scenes and not topics and text and config.key and not preference.place_query:
                try:
                    topics = FlightModel(config, timeout=10).scene_tags(text)
                except Exception:
                    topics = ()
            return topics, proposals(topics, lambda _: empty_weather, None, extra_scenes)

        def latest(topics, extra_scenes):
            try:
                scan = notices.search(city)
            except Exception:
                scan = Scan()

            def weather_at(point):
                try:
                    return lookup.forecast(point)
                except Exception:
                    return empty_weather

            with ThreadPoolExecutor(max_workers=4) as workers:
                return proposals(topics, weather_at, scan, extra_scenes, map_weather=workers.map)

        def present(result, extra_scenes, generation):
            if token != self.token or generation != lookup_generation:
                return
            topics, values = result
            self.display(token, values, model, city)
            initial = page.active_proposal
            if not values:
                return
            self.message.setText('方案已显示，正在更新天气与空域')
            self.message.show()

            def refreshed(updated):
                if token != self.token or generation != lookup_generation:
                    return
                self.message.hide()
                if not updated:
                    return
                selected = page.active_proposal
                self.display(token, updated, model, city, activate=False)
                if selected and page.result_stack.currentWidget() is page.preview_panel:
                    match = next(((proposal, window) for proposal in updated for window in proposal.windows
                                  if proposal.scene.name == page._preview_subject
                                  and window.query.start == selected.query.start), None)
                    best = updated[0].windows[0]
                    if page.photo_preview.isHidden() and upcoming and selected is initial and best.score is not None:
                        page.preview_proposal(updated[0],best)
                    elif match:
                        proposal, window = match
                        variant = next((item for item in window.variants if item.key == selected.style_key), None)
                        page.preview_proposal(proposal, select_variant(window, variant) if variant else window)

            self.context.run_aerial_lookup(lambda: latest(topics, extra_scenes), refreshed, lambda: refreshed(()))

        def begin(extra_scenes):
            nonlocal lookup_generation
            if token == self.token:
                if lookup_generation and not extra_scenes:
                    return
                lookup_generation += 1
                generation = lookup_generation

                def failed():
                    if generation == lookup_generation:
                        self.display(token, (), model, city, failed=True)

                self.context.run_aerial_lookup(lambda: first_pass(extra_scenes),
                    lambda result: present(result, extra_scenes, generation), failed)

        map_view = page.map_view
        if text and preference.place_query and not named and map_view and map_view.is_ready:
            if map_view is not self._place_map:
                if self._place_map is not None:
                    self._place_map.place_resolved.disconnect(self._place_resolved)
                map_view.place_resolved.connect(self._place_resolved)
                self._place_map = map_view
            self._pending_place = begin
            QTimer.singleShot(3500, self, lambda: begin(()) if not lookup_generation else None)
            map_view.lookup_city_place(token, civic.cities()[city]['name'], preference.place_query)
        else:
            begin(())

    def display(self, token, values, model, city, failed=False, *, activate=True):
        if token != self.token:
            return
        self.search_button.setEnabled(True)
        self.proposals = values
        clear(self.list)
        self.message.hide()
        if not values:
            self.page.clear_preview()
            suggestions = {'changsha':'湘江江面、梅溪湖', 'guangzhou':'珠江、广州塔', 'shanghai':'外滩、城市天际线',
                           'shenzhen':'海岸公园、山景', 'hangzhou':'西湖、桥梁'}
            self.message.setText('暂时搜不到机位，请重试' if failed else
                                 '当前条件下没有匹配机位 · 可试试' + suggestions.get(city, '江河或公园绿地'))
            self.message.show()
            return
        for proposal in values:
            best = proposal.windows[0]
            scene = proposal.scene
            card = Card()
            heading = QHBoxLayout()
            heading.addWidget(label(scene.name, 'section'), 1)
            score = Badge('', 'info')
            heading.addWidget(score)
            card.body.addLayout(heading)
            recommendation = label('', 'bodyText')
            card.body.addWidget(recommendation)
            if best.query.route:
                arrival = best.query.start - timedelta(minutes=15)
                card.body.addWidget(label(f'建议到场 {arrival:%H:%M}'))
            if self.origin_point:
                km = distance(scene.point, self.origin_point) / 1000
                card.body.addWidget(label(f'距出发点 {km:.1f} 公里 · 直线距离'))
            times = QComboBox()
            times.setAccessibleName('推荐拍摄时段')
            for window in proposal.windows:
                start, end = window.query.start, window.query.end
                suffix = ' · 有预报的备选' if window.alternative_date else ''
                times.addItem(f'{start:%m-%d %H:%M} · {window.light or "可选时段"}{suffix}')
            card.body.addWidget(times)
            badge = Badge('', 'info')
            card.body.addWidget(badge)
            weather = label('', 'bodyText')
            card.body.addWidget(weather)

            def update(index, p=proposal, tag=badge, line=weather, score_badge=score, brief=recommendation):
                from ...flight_check import HEADLINES
                result = p.windows[index]
                source = SOURCE_LABELS[result.source_kind]
                style = STYLE_LABELS.get(result.style_key, '地点推荐')
                brief.setText(f'{source} · {style}' + (f'\n{result.why_recommended}' if result.why_recommended else ''))
                score_badge.set_value(f'{displayed_score(result.score)} 分' if result.score is not None
                                      else '天气待更新', 'info')
                tag.set_value(HEADLINES[result.verdict] if result.query.route else '先选起飞点',
                              'ok' if result.verdict.value=='clear' else 'warn')
                line.setText(('建议改期 · ' + result.status) if result.unsuitable else
                             (result.weather_summary or result.status or '天气资料待补充'))
            times.currentIndexChanged.connect(update)
            update(0)
            card.body.addWidget(ActionButton('在地图上看',
                lambda p=proposal,t=times: self.page.preview_proposal(p, p.windows[t.currentIndex()]), variant='primary'))
            self.list.addWidget(card)
        self.list.addStretch()
        if activate:
            self.page.preview_proposal(values[0], values[0].windows[0])
