from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from skyguard.aerial import (CHINA, FORECAST_OUTSIDE, FORECAST_PARTIAL, Forecast, ScenicPlace, WeatherHour, assess_weather, displayed_score,
                             local_metres, route_center, route_score, scenic_matches, scenic_score,
                             solar_events, touched_hours)
from skyguard.aerial_scenery import ScenicLookup
from skyguard.aerial_weather import qweather_hours
from skyguard.flight_rules import FlightQuery


def query(points=((112.950, 28.180), (112.980, 28.180)), duration=40):
    start = datetime(2026, 11, 21, 16, 40, tzinfo=CHINA)
    return FlightQuery(points, 'route', 75, start, start + timedelta(minutes=duration), 70, '轻型', demo=False)


def test_route_center_follows_cumulative_length_along_a_bend():
    curved = query(((112.95, 28.18), (112.96, 28.18), (112.96, 28.23)))
    lon, lat = route_center(curved)
    assert lon == pytest.approx(112.96, abs=1e-7)
    assert 28.18 < lat < 28.23


def test_windows_are_half_open_and_use_beijing_time():
    q = query(duration=20)
    assert [hour.hour for hour in touched_hours(q.start, q.end)] == [16]
    q = query(duration=21)
    assert [hour.hour for hour in touched_hours(q.start, q.end)] == [16, 17]
    start = q.start.replace(hour=23)
    assert [(h.day, h.hour) for h in touched_hours(start, start + timedelta(minutes=21))] == [(21, 23), (22, 0)]


def test_scenic_cutoff_and_duplicate_names_do_not_add_points():
    q = query()
    center = route_center(q)
    _, unproject = local_metres(center)
    q = replace(q, points=(unproject(-100, 0), unproject(100, 0)))
    nearby = ScenicPlace('1', '同一景点', unproject(0, 250), '景点', ())
    boundary = ScenicPlace('2', '边界景点', unproject(0, 500), '景点', ())
    outside = ScenicPlace('3', '范围外', unproject(0, 501), '景点', ())
    duplicate = replace(nearby, id='4')
    matches = scenic_matches(q, center, (nearby, boundary, outside, duplicate))
    assert [match.place.id for match in matches] == ['1', '2']
    assert scenic_score(matches) > scenic_score(matches[:1]) > 0
    assert scenic_score(()) == 0


def test_weather_uses_lowest_hour_and_holds_incomplete_score():
    q = query()
    hours = list(touched_hours(q.start, q.end))
    first = WeatherHour(hours[0], rain_probability=0, visibility_km=10, clouds=.15,
                        levels=((10, 1), (80, 2)), surface_gust_10m=2)
    second = replace(first, time=hours[1], surface_gust_10m=6)
    center = route_center(q)
    result = assess_weather(Forecast((first, second), q.start), center, q, q.start, 8, '日落')
    assert result.weather == result.rows[1][2] < result.rows[0][2]
    expired = assess_weather(Forecast((first,), q.start), center, q, q.start, 8, '日落')
    assert expired.status == FORECAST_PARTIAL and expired.weather is None
    next_year = q.start.replace(year=q.start.year + 1)
    beyond = assess_weather(Forecast((first, second), q.start), center, q, next_year, 8, '日落')
    assert beyond.status == FORECAST_OUTSIDE and beyond.weather is None
    unavailable = assess_weather(Forecast((), q.start, '和风天气请求超时'), center, q, q.start, 8, '日落')
    assert unavailable.status == '和风天气请求超时'
    risk = assess_weather(Forecast((replace(first, thunder=True),), q.start), center, q, q.start, 8, '日落')
    assert '雷暴' in risk.hazard


def test_score_example_and_idempotent_catalog_limits():
    assert displayed_score(route_score(82, 90, 79.4)) == 83
    assert displayed_score(route_score(82, 90, 0)) == 63
    assert route_score(82, 90, None) is None
    lookup = ScenicLookup()
    for city in ('changsha', 'guangzhou', 'shanghai', 'shenzhen', 'hangzhou'):
        scenes = lookup.search(city)
        assert 10 <= len(scenes) <= 20
        assert all(place.evidence[0].startswith('https://') for place in scenes)
    assert lookup.search('unknown') is None


def test_hourly_weather_preserves_missing_fields_and_units():
    hours = qweather_hours({'hours': [{'forecastTime': '2026-11-21T08:00:00Z',
            'wind': {'speed': {'value': 36, 'unit': 'km/h'}}, 'visibility': {'value': 4500, 'unit': 'm'},
            'condition': {'text': '雷阵雨', 'code': '302'}, 'cloudCover': None}]})
    first, = hours.values()
    assert first.time.hour == 16 and first.time.utcoffset() == timedelta(hours=8)
    assert first.wind == 10 and first.visibility_km == 4.5 and first.thunder
    assert first.rain_probability is None and first.clouds is None


def test_changsha_sun_events_are_local_and_ordered():
    summary = solar_events((112.96, 28.18), query().start)
    sunrise, rise_azimuth = summary['rise']
    sunset, set_azimuth = summary['set']
    assert sunrise.hour == 6 or sunrise.hour == 7
    assert sunset.hour == 17
    assert 90 < rise_azimuth < 135 and 220 < set_azimuth < 270
