from dataclasses import replace
from datetime import datetime,timedelta

from skyguard.aerial import CHINA, Forecast, WeatherHour, route_center, touched_hours
from skyguard.aerial_plan import load_scenes, build_shot, recommend
from skyguard.flight_check import Verdict
from skyguard.flight_route import FlightRoute, timeline
from skyguard.flight_rules import FlightQuery
from skyguard.photo_mission import plan_frame, shot_list
from skyguard.photo_quality import score_photo

NOW = datetime(2026,11,23,13,tzinfo=CHINA)


def route_and_subject():
    scene = next(s for s in load_scenes('changsha') if '江河' in s.tags and s.tracks)
    _, route = build_shot(scene,('江河',),NOW,'轻型',None,'promo')
    return route,scene.point


def test_phases_include_climb_and_landing_and_shots_follow_real_legs():
    route, _ = route_and_subject()
    pieces = timeline(route)
    assert pieces[0].phase == '起飞爬升' and pieces[0].a.altitude_m == 0
    assert pieces[-2].phase == '返航' and pieces[-1].phase == '降落'
    assert pieces[-1].b.xy == route.takeoff and pieces[-1].b.altitude_m == 0
    assert sum(piece.metres for piece in pieces) > 80
    assert FlightRoute.parse(route.record()) == route
    shots = shot_list(route,'promo')
    assert [shot['seconds'] for shot in shots] == [piece.begin_s for piece in pieces if piece.shot]
    assert plan_frame(route,shots[0]['seconds'])['shot'] == shots[0]['title']


def test_shooting_weather_can_score_without_specific_aircraft_and_keeps_gaps():
    route,target = route_and_subject()
    start = NOW.replace(minute=59,second=50)
    route = route.shifted(start)
    query = FlightQuery((),'route',25,start,route.end,0,route.category,demo=False,route=route)
    good = tuple(WeatherHour(time,'晴',3,4,.05,0,12,.2) for time in touched_hours(start,route.end))
    actual = score_photo(query,route_center(query),Forecast(good,NOW),start,None,'日间清晰',target)
    partial = score_photo(query,route_center(query),Forecast(good[:1],NOW),start,None,'日间清晰',target)
    no_target = score_photo(query,route_center(query),Forecast(good,NOW),start,None,'日间清晰')
    assert len(good) == 2 and actual.score is not None and actual.weather is not None
    assert partial.score is None and partial.weather is None and partial.light > 0
    assert no_target.score is None and no_target.weather == actual.weather


def test_forecast_backups_do_not_replace_the_requested_day():
    end = NOW + timedelta(days=3)
    data = Forecast(tuple(WeatherHour(time,'晴',2,3,.03,0,14,.1)
                          for time in touched_hours(NOW,end)),NOW)
    wanted = NOW+timedelta(days=10)
    rows = recommend('hangzhou',('江河',),wanted,wanted+timedelta(hours=7),'轻型',None,
                     lambda p:data,lambda q:Verdict.CHECK,None,coverage_alternatives=True)
    with_alternate = next(row for row in rows if any(w.alternative_date for w in row.windows))
    assert with_alternate.windows[0].query.start.date() == wanted.date()
    assert with_alternate.windows[0].score is None
    assert any(w.score is not None and w.query.start < end for w in with_alternate.windows if w.alternative_date)
