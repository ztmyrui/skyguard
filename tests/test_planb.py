from datetime import datetime, timedelta

import httpx
import pytest

from skyguard.aerial import CHINA, Forecast, WeatherHour, touched_hours
from skyguard.aerial_plan import (
    best_track, derive_takeoff_candidates, distance, hard_filter, load_scenes,
    parse_preferences, recommend, scene_from_poi, select_variant,
)
from skyguard.flight_check import Verdict
from skyguard.flight_model import FlightModel, FlightModelConfig
from skyguard.flight_route import FlightRoute, Waypoint, camera_pitch, destination, timeline
from skyguard.photo_mission import plan_frame, shot_altitude, shot_list


START = datetime(2026, 11, 23, 13, tzinfo=CHINA)
POINT = (112.93, 28.13)


def results(*, location='', extra=(), kind=None, text='', check=lambda _: Verdict.CLEAR, stop=None):
    end = START + timedelta(days=2)
    forecast = Forecast(tuple(WeatherHour(t, '晴', 2, 3, .02, 0, 14, .1)
                              for t in touched_hours(START, end)), START)
    return recommend('changsha', (), START, end, '轻型', None, lambda _: forecast, check, None,
                     extra_scenes=extra, location_hint=location or text, mission_kind=kind, stop_minutes=stop)


def test_camera_waypoints_round_trip_interpolate_and_keep_old_records():
    end = destination(POINT, 90, 100)
    legacy = Waypoint(*POINT, 25, 7)
    shot = Waypoint(*end, 75, 8, 270, '收尾', 'track', -45, '拉升揭示', '露出地平线')
    route = FlightRoute((legacy, shot), 3, POINT, START, '轻型')
    record = route.record()
    assert not {'pitch_deg', 'move', 'tip'} & record['waypoints'][0].keys()
    assert FlightRoute.parse(record) == route
    for pitch in (-91, 31, float('nan')):
        with pytest.raises(ValueError):
            Waypoint(*POINT, 25, pitch_deg=pitch)
    assert camera_pitch(end, 115, POINT) == -45
    assert camera_pitch(POINT, 10, POINT) < 0 and camera_pitch(POINT, 115, POINT) == -85
    leg = next(piece for piece in timeline(route) if piece.a.xy != piece.b.xy)
    frame = plan_frame(route, (leg.begin_s + leg.end_s)/2, POINT)
    assert frame['pitch'] == camera_pitch(frame['point'], frame['altitude'], POINT)
    listing = shot_list(route, 'promo')
    assert shot_altitude(listing[0]) == '25→75' and listing[0]['move'] == '拉升揭示'


def test_preferences_extract_place_and_respect_explicit_choices():
    pref = parse_preferences('明天17:30想拍梅溪湖日落大片，慢速环绕，避开人多')
    assert (pref.subjects, pref.lights, pref.style, pref.avoid_crowds, pref.place_query) == (
        ('湖面',), ('日落',), 'cinematic', True, '梅溪湖')
    assert parse_preferences('拍谢子龙影像艺术馆，快速打卡').style == 'quick'
    assert parse_preferences('公园绿地', 'news').style == 'panoramic'
    assert parse_preferences('白天岳麓山', style='panoramic').lights == ('日间',)
    assert parse_preferences('电影运镜', 'repeat', style='quick').style == 'repeat'


def test_track_choice_follows_sun_and_changes_with_light(monkeypatch):
    scene = next(scene for scene in load_scenes('changsha') if len(scene.tracks) == 2)
    takeoff = scene.takeoffs[0]
    daylight_choices = set()
    sunset_choices = set()
    for azimuth in range(0, 360, 5):
        monkeypatch.setattr('skyguard.aerial_plan.sun_position', lambda *_, az=azimuth: (5, az))
        daylight_choices.add(best_track(scene, takeoff, START, '日间'))
        sunset_choices.add(best_track(scene, takeoff, START, '日落'))
    assert daylight_choices == sunset_choices == set(scene.tracks)


@pytest.mark.parametrize('kind,style', [('promo', 'cinematic'), ('news', 'panoramic'), ('repeat', 'repeat')])
def test_each_variant_has_distinct_three_dimensional_geometry_and_own_score(kind, style):
    proposal = results(location='梅溪湖', kind=kind)[0]
    window = proposal.windows[0]
    assert window.style_key == style and len(window.variants) == 3
    assert len({tuple(w.altitude_m for w in item.route.waypoints) for item in window.variants}) == 3
    for item in window.variants:
        selected = select_variant(window, item)
        route = selected.query.route
        assert route == item.route and route.complete_flight and hard_filter(route, load_scenes('changsha'))
        assert all(w.pitch_deg is not None and distance(route.takeoff, w.xy) <= 480 for w in route.waypoints)
        assert selected.score is not None and selected.why_recommended and selected.safety_notes
        assert timeline(route)[-1].phase == '降落'
    assert len({item.window.score for item in window.variants}) > 1


def test_catalog_derivation_and_poi_share_protection_and_restriction_checks():
    catalog = load_scenes('changsha')
    missing = next(scene for scene in catalog if scene.name == '湖南第一师范旧址')
    assert not missing.takeoffs and len(derive_takeoff_candidates(missing, START, (), lambda _: Verdict.CLEAR, '轻型', catalog)) == 2
    assert results(location=missing.name)[0].windows[0].source_kind == 'derived'
    poi = scene_from_poi('示例湿地公园', POINT, '风景名胜;公园广场')
    assert '公园绿地' in poi.tags
    dynamic = results(extra=(poi,))[0].windows[0]
    assert dynamic.source_kind == 'poi' and len(dynamic.variants) == 3
    assert any('起降净空' in note for note in dynamic.safety_notes)
    blocked = results(extra=(poi,), check=lambda _: Verdict.RESTRICTED)[0].windows[0]
    assert blocked.query.route is None and not blocked.variants
    short = results(extra=(poi,), kind='travel', stop=2)[0].windows[0]
    assert short.style_key == 'quick' and all(v.route.end - v.route.start <= timedelta(minutes=2) for v in short.variants)


def test_director_cleans_model_output_and_ignores_failure():
    config = FlightModelConfig('https://example.test/v1', 'sample', 'sample-key')

    def respond(request):
        assert request.extensions['timeout']['read'] == 10
        return httpx.Response(200, json={'choices': [{'message': {'content':
            '{"why":"用岸线带出主体。","camera_tips":"此建议不代表许可。锁定白平衡。",'
            '"shot_tips":["留出上方空间","改坐标起飞"],"cautions":"sample-key"}'}}]})

    model = FlightModel(config, httpx.MockTransport(respond))
    assert model.direct_plan('长沙', '日落', {'shots': [{}, {}]}) == {
        'why': '用岸线带出主体。', 'camera_tips': '锁定白平衡。',
        'shot_tips': ('留出上方空间', ''), 'cautions': '',
    }
    broken = FlightModel(config, httpx.MockTransport(lambda request: httpx.Response(500)))
    assert broken.direct_plan('长沙', '', {}) is None
    assert FlightModel(FlightModelConfig()).direct_plan('长沙', '', {}) is None
