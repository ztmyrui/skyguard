"""Public route score, hourly forecast and photo cards in one result panel."""
from datetime import timedelta

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QProgressBar, QVBoxLayout, QWidget

from ...aerial import (FORECAST_OUTSIDE, Forecast, displayed_score, route_center,
                       scenic_matches, solar_events)
from ...photo_quality import score_photo
from ...aerial_weather import AIRCRAFT_PROFILES
from ...flight_check import Verdict
from ..presenters import flight_window
from .common import ActionButton, Badge, Card, SectionHeader, SegmentedControl, label
from .flight_result import Disclosure


def _value(value, unit='', precision=0):
    return '—' if value is None else f'{value:.{precision}f}{unit}'


def _bearing(value):
    return '沿线' if value is None else ('北', '东北', '东', '东南', '南', '西南', '西', '西北')[int((value + 22.5) // 45) % 8]


class AerialPanel(QWidget):
    map_changed = Signal()
    choose_time = Signal(object)
    resolved = Signal()
    pick_subject = Signal()

    def __init__(self, context, check_window):
        super().__init__()
        self.context = context
        self.check_window = check_window
        self.sequence = 0
        self.scene_focus = 0
        self.query = None
        self.active = False
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(0, 0, 0, 0)
        self.heading = SectionHeader('航拍助手')
        self.root.addWidget(self.heading)
        self.tabs = SegmentedControl(('总览', '小时天气', '沿途景点'))
        self.tabs.setAccessibleName('航拍分析页签')
        self.tabs.currentIndexChanged.connect(self._tab)
        self.root.addWidget(self.tabs)
        self.content = QVBoxLayout()
        self.content.setSpacing(8)
        self.root.addLayout(self.content)
        self.hide()

    def reset(self):
        self.sequence += 1
        self.query = None
        self.active = False
        self.hide()

    def start(self, query, city, model, style, verdict, subject=None):
        if self.active and (query, city, model, style, subject) == (self.query, self.city, self.model, self.style, self.subject):
            self.set_verdict(verdict)
            return
        self.sequence += 1
        self.query, self.city, self.model, self.style = query, city, model, style
        self.subject = subject
        self.center = route_center(query)
        self.events = solar_events(self.center, query.start)
        self.active = True
        self.verdict = verdict
        self.forecast = None
        self.weather_error = False
        self.assessment = None
        self.scenes = None
        self.scenic_status = ''
        self.selected = ''
        self.selected_hour = ''
        self.alternatives = ()
        self.score = None
        self.tabs.setCurrentIndex(0)
        self.show()
        self._fetch_weather()
        places = self.context.scenic_lookup.search(city)
        if places is not None:
            self.scenes = scenic_matches(query, self.center, places)
        self._refresh()

    def discover(self, candidates):
        if not candidates:
            self._discovered(())
            return
        city = self.city
        self._job(lambda: self.context.scenic_lookup.discover(city, candidates),
                  self._discovered, lambda: self.scenic_search_status('error'))

    def scenic_search_status(self, status):
        self.scenic_status = status
        self._refresh()

    def _discovered(self, places):
        self.scenic_status = ''
        if not places:
            self._refresh()
            return
        base = self.context.scenic_lookup.search(self.city) or ()
        self.scenes = scenic_matches(self.query, self.center, base + places)
        if self.selected and not any(match.place.id == self.selected for match in self.scenes):
            self.selected = ''
        self._refresh()

    def set_verdict(self, verdict, *, refresh=False):
        if self.active and (self.verdict != verdict or refresh):
            self.verdict = verdict
            self._refresh()

    def _job(self, run, success, failure):
        sequence = self.sequence
        self.context.run_aerial_lookup(run, lambda value: success(value) if sequence == self.sequence else None,
                                       lambda: failure() if sequence == self.sequence else None)

    def _fetch_weather(self):
        center = self.center
        self.weather_error = False
        query = self.query
        self._job(lambda: self.context.forecast_for(center, query.start, query.end),
                  self._weather_loaded, self._weather_failed)

    def _weather_loaded(self, forecast):
        self.forecast = forecast
        self._refresh()

    def _weather_failed(self):
        self.weather_error = True
        self._refresh()

    def set_subject(self, point):
        self.subject = point
        if self.active:
            self._refresh()

    def _calculate(self):
        self.score = None
        self.alternatives = ()
        ceiling = AIRCRAFT_PROFILES[self.model][0]
        query = self.query
        feed = self.forecast or Forecast((),query.start,weather_error='天气查询失败' if self.weather_error else '正在读取小时预报')
        self.quality = score_photo(query,self.center,feed,query.start,ceiling,self.style,self.subject)
        self.assessment = self.quality.forecast if self.forecast else None
        if self.verdict != Verdict.RESTRICTED:
            self.score = self.quality.score
        if not feed.hours:
            return
        from ...system_clock import now
        from ...aerial import CHINA

        duration = query.end - query.start
        step = timedelta(minutes=30)
        earliest, end_coverage = feed.coverage
        start = max(earliest,now(CHINA) + timedelta(minutes=15))
        latest = min(start + timedelta(hours=72),end_coverage)
        candidates = []
        while start + duration <= latest:
            if abs(start-query.start) >= step:
                alternative = score_photo(query,self.center,feed,start,ceiling,self.style,self.subject)
                if alternative.weather is not None and not alternative.forecast.hazard:
                    score = alternative.score if alternative.score is not None else .65*alternative.weather + .35*alternative.light
                    candidates.append((score,start,alternative))
            start += step
        accepted = []
        for score,start,alternative in sorted(candidates,key=lambda row:(-row[0],row[1])):
            if any(abs(start-other[1]) < max(step,duration) for other in accepted):
                continue
            verdict = self.check_window(start)
            if verdict != Verdict.RESTRICTED:
                accepted.append((alternative.score,start,alternative.forecast,verdict))
            if len(accepted) == 3:
                break
        self.alternatives = tuple(accepted)

    def _refresh(self):
        self._calculate()
        self._render()
        self.map_changed.emit()
        self.resolved.emit()

    def _tab(self, index):
        if self.active:
            self._render()
            self.map_changed.emit()

    def _render(self):
        while self.content.count():
            item = self.content.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.deleteLater()
        (self._overview, self._hourly, self._scenery)[self.tabs.currentIndex()]()

    def _overview(self):
        card = Card()
        card.body.addWidget(SectionHeader('航拍评分'))
        quality = self.quality
        if self.verdict == Verdict.RESTRICTED:
            card.body.addWidget(Badge('空域受限', 'danger'))
        if quality.forecast.hazard:
            card.body.addWidget(label(quality.forecast.hazard,'formWarning'))
        if self.score is not None:
            card.body.addWidget(label(f'{displayed_score(self.score)} / 100','kpiValue',numeric=True))
        elif quality.forecast.status == FORECAST_OUTSIDE:
            card.body.addWidget(label('计划时段超出预报范围，推荐指数待更新','bodyText'))
        for index, (name, value) in enumerate(quality.components):
            weight = (45, 25, 30)[index]
            title = f'{name}（{weight}%）'
            header = f'{title} · {displayed_score(value)} 分' if value is not None else f'{title} · 待补资料'
            card.body.addWidget(label(header, 'checkTitle'))
            bar = QProgressBar()
            bar.setFixedHeight(6)
            bar.setTextVisible(False)
            bar.setAccessibleName(header)
            score = displayed_score(value) if value is not None else 0
            bar.setValue(score)
            bar.setProperty('scoreTone', 'ok' if score >= 80 else 'warn' if score >= 60 else 'danger')
            card.body.addWidget(bar)
            card.body.addWidget(label(quality.reasons[index],'bodyText'))
        if self.query.route:
            card.body.addWidget(ActionButton('更换拍摄主体' if self.subject else '在地图上标出主体',
                                             self.pick_subject.emit))
        self.content.addWidget(card)
        if self.alternatives:
            choices = Card()
            choices.body.addWidget(SectionHeader('备选时段'))
            for score, start, alternative, verdict in self.alternatives:
                end = start + (self.query.end - self.query.start)
                row = QHBoxLayout()
                total = f' · {displayed_score(score)} 分' if score is not None else f' · 天气 {displayed_score(alternative.weather)} 分'
                row.addWidget(label(f'{flight_window(start,end)}{total}', 'bodyText'),1)
                row.addWidget(ActionButton('一键改期', lambda time=start: self.choose_time.emit(time)))
                choices.body.addLayout(row)
                note = self._difference(alternative)
                if verdict == Verdict.PERMIT:
                    note += ' · 需申请'
                elif verdict == Verdict.CHECK:
                    note += ' · 空域待核对'
                choices.body.addWidget(label(note))
            self.content.addWidget(choices)

    def _difference(self, other):
        if self.assessment is None or self.assessment.weather is None:
            return f'天气 {displayed_score(other.weather)} 分'
        changes = [(other.weather - self.assessment.weather, '天气'),
                   (other.light - self.assessment.light, '光线')] if self.assessment.light is not None else [
                   (other.weather - self.assessment.weather, '天气')]
        return '，'.join(f'{word}{abs(amount):.0f} 分{"更高" if amount > 0 else "更低"}'
                         for amount, word in changes if abs(amount) >= 1) or '与原时段条件相近'

    def _hourly(self):
        card = Card()
        card.body.addWidget(SectionHeader('航线中心点附近'))
        lon, lat = self.center
        card.body.addWidget(label(f'{lon:.5f}°E，{lat:.5f}°N · 北京时间', numeric=True))
        if self.forecast and self.forecast.coverage:
            start, end = self.forecast.coverage
            card.body.addWidget(label(f'预报范围 {flight_window(start, end, show_year=True)}'))
        for key, word in (('rise', '日出'), ('set', '日落')):
            if key in self.events:
                time, bearing = self.events[key]
                card.body.addWidget(label(f'{word} {time:%H:%M} · {_bearing(bearing)} {bearing:.0f}°', numeric=True))
        if self.assessment:
            valid = [row for row in self.assessment.rows if row[2] is not None]
            lowest = min(valid, key=lambda row: row[2])[0] if valid else None
            if self.assessment.status:
                card.body.addWidget(label(self.assessment.status, 'formWarning'))
            if self.assessment.status != FORECAST_OUTSIDE:
                for time, hour, score, heights in self.assessment.rows:
                    self._weather_row(card, time, hour, score, heights, time == lowest)
            if self.forecast.weather_error or self.forecast.upper_error:
                card.body.addWidget(ActionButton('重试天气', self._fetch_weather))
        elif self.weather_error:
            card.body.addWidget(label('天气查询失败'))
            card.body.addWidget(ActionButton('重试天气', self._fetch_weather))
        else:
            card.body.addWidget(label('正在读取逐小时预报…'))
        self.content.addWidget(card)

    def _weather_row(self, card, time, hour, score, heights, lowest):
        key = time.isoformat()
        detail = Disclosure(f'{time:%m-%d %H:%M} 天气详情')
        rain = _value(hour.rain_probability * 100 if hour and hour.rain_probability is not None else None, '%')
        gust = _value(hour.gust if hour else None, ' m/s', 1)
        summary = (f'{time:%m-%d %H:%M} · 阵风 {gust} · 降水 {rain}' if hour else
                   f'{time:%m-%d %H:%M} · 暂无预报')
        if score is not None:
            summary += f'\n{displayed_score(score)} 分' + (' · 影响本次评分' if lowest else '')
        detail.row.addWidget(label(summary, 'bodyText', numeric=True), 1)
        detail.add_arrow()
        card.body.addWidget(detail)
        if hour:
            text = (f'{hour.condition or "天气现象待查"} · 和风风速 {_value(hour.wind, " m/s", 1)}\n'
                    f'离地风速层 {" / ".join(str(height) for height in heights) + " 米" if heights else "待核对"}\n'
                    f'能见度 {_value(hour.visibility_km, " km", 1)} · 云量 '
                    f'{_value(hour.clouds * 100 if hour.clouds is not None else None, "%")}')
        else:
            text = '该小时暂无预报'
        child = label(text, numeric=True)
        child.setVisible(self.selected_hour == key)
        detail.setChecked(self.selected_hour == key)
        detail.toggled.connect(child.setVisible)
        detail.toggled.connect(lambda opened: self._select_hour(key if opened else ''))
        card.body.addWidget(child)

    def _select_hour(self, key):
        self.selected_hour = key
        self.map_changed.emit()

    def _scenery(self):
        card = Card()
        for match in self.scenes or ():
            place = match.place
            row = QHBoxLayout()
            row.addWidget(label(place.name, 'bodyText'), 1)
            locate = ActionButton('定位', lambda identifier=place.id: self.select_scene(identifier))
            locate.setAccessibleName(f'定位{place.name}')
            row.addWidget(locate)
            card.body.addLayout(row)
        if not self.scenes:
            message = {'searching':'正在查找景点…','error':'景点查询失败'}.get(self.scenic_status,'无沿途景点')
            card.body.addWidget(label(message,'bodyText'))
        self.content.addWidget(card)

    def select_scene(self, identifier):
        if not self.active or not any(match.place.id == identifier for match in (self.scenes or ())):
            return
        self.selected = identifier
        self.scene_focus += 1
        if self.tabs.currentIndex() != 2:
            self.tabs.setCurrentIndex(2)
        else:
            self.map_changed.emit()

    def payload(self):
        if not self.active:
            return None
        hour = self.selected_hour[11:16] if self.selected_hour else ''
        line = self.query.points if self.query.form == 'route' else ()
        data = {'point': self.center, 'time': hour, 'scenes': [], 'line': line,
                'segment': [], 'selected': self.selected, 'focus': self.scene_focus}
        if self.tabs.currentIndex() != 2:
            return data
        for index, match in enumerate((self.scenes or ()), 1):
            data['scenes'].append({'id': match.place.id, 'point': match.place.point,
                                   'number': index, 'name': match.place.name})
            if match.place.id == self.selected and match.segment is not None:
                data['segment'] = self.query.points[match.segment:match.segment + 2]
        return data
