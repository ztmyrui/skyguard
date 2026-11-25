"""Hourly flight-planning advice for the next 24 hours."""
from dataclasses import dataclass
from datetime import datetime, timedelta

from .aerial import CHINA, HOUR, WeatherHour

# Conservative engineering defaults for planning, independent of airspace rules.
DEFAULT_LIMITS = {'gust': 8.0, 'rain_probability': .45, 'rain_mm': .3, 'visibility': 3.0}
GOLDEN_HOUR = HOUR


@dataclass(frozen=True)
class FlightHour:
    start: datetime
    weather: WeatherHour | None
    score: int | None
    reasons: tuple[str, ...]
    golden: str = ''

    @property
    def tone(self):
        if self.score is None:
            return 'unknown'
        if self.score >= 75:
            return 'ok'
        return 'warn' if self.score >= 45 else 'danger'


@dataclass(frozen=True)
class TimeOption:
    start: datetime
    end: datetime
    score: float


def score_hour(start, forecast, events, gust_limit=None):
    begin = events.get('rise', (None,))[0]
    end = events.get('set', (None,))[0]
    midpoint = start + HOUR / 2
    golden = ''
    if begin and begin <= midpoint < begin + GOLDEN_HOUR:
        golden = '日出金光'
    if end and end - GOLDEN_HOUR <= midpoint < end:
        golden = '日落金光'
    if forecast is None:
        return FlightHour(start, None, None, ('暂无小时预报',), golden)
    gust = forecast.surface_gust_10m if forecast.surface_gust_10m is not None else forecast.gust
    probability, amount, visible = forecast.rain_probability, forecast.rain_mm, forecast.visibility_km
    limit = gust_limit or DEFAULT_LIMITS['gust']
    violations = []
    if forecast.thunder:
        violations.append('雷暴')
    if gust is not None and gust > limit:
        violations.append(f'阵风超过 {limit:g} m/s')
    if ((probability is not None and probability > DEFAULT_LIMITS['rain_probability'])
            or (amount is not None and amount > DEFAULT_LIMITS['rain_mm'])):
        violations.append('降雨超过计划参考线')
    if visible is not None and visible < DEFAULT_LIMITS['visibility']:
        violations.append('能见度低于 3 km')
    if begin and end and (start < begin or start + HOUR > end):
        violations.append('该小时包含夜间')
    if violations:
        return FlightHour(start, forecast, 0, tuple(violations), golden)
    if gust is None or (probability is None and amount is None) or visible is None or not (begin and end):
        return FlightHour(start, forecast, None, ('天气资料待补齐',), golden)
    deductions = (
        (round(25 * gust / limit), '阵风'),
        (round(30 * (probability or 0)), '降雨概率'),
        (round(max(0, 10 - visible) * 2), '能见度'),
    )
    return FlightHour(start, forecast, 100 - sum(value for value, _ in deductions),
                      tuple(f'{name} −{value} 分' for value, name in deductions if value), golden)


def hourly_window(hours, now, events_for_day, gust_limit=None):
    now = now.astimezone(CHINA)
    start = now.replace(minute=0, second=0, microsecond=0)
    by_time = {hour.time: hour for hour in hours}
    events = {}
    rows = []
    for i in range(24):
        time = start + i * HOUR
        if time.date() not in events:
            events[time.date()] = events_for_day(time)
        rows.append(score_hour(time, by_time.get(time), events[time.date()], gust_limit))
    return tuple(rows)


def recommend(rows, duration, now):
    if duration <= timedelta():
        raise ValueError('请填写飞行时长')
    runs = []
    for row in rows:
        if row.tone not in ('ok', 'warn'):
            continue
        if runs and row.start == runs[-1][-1].start + HOUR:
            runs[-1].append(row)
        else:
            runs.append([row])
    choices = []
    for run in runs:
        begin, end = max(now, run[0].start), run[-1].start + HOUR
        if end - begin < duration:
            continue
        average = sum(row.score * (row.start + HOUR - max(begin, row.start)).total_seconds()
                      for row in run if row.start + HOUR > begin) / (end - begin).total_seconds()
        choices.append(TimeOption(begin, end, average))
    return tuple(sorted(choices, key=lambda option: (-option.score, option.start))[:3])
