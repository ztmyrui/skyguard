"""Regression coverage for sandbox scoring, playback, and local data."""
from dataclasses import replace

import pytest

from skyguard.sandbox import (
    CITY_EVENT_TEMPLATES, DIRECTOR_ACTIONS, DRONE_TYPES, EMERGENCY_TYPES, LANDING_TYPES, WEATHERS,
    SandboxConfig, SandboxDrone, SandboxEmergency, SandboxSite,
    SandboxSimulation, SandboxStaff, SandboxStrategy, compare_strategies, default_sites, metres,
    offset, quick_example, suggest_staffing, typical_drones, within_city_radius,
)
from skyguard.sandbox_store import SandboxLibrary

CENTER = (112.953, 28.1792)


def exercise(kind='crowd_intrusion', airframe='fpv', *, start=(-330, 0), staff=(), **options):
    config = SandboxConfig('changsha', CENTER, **options)
    speed = DRONE_TYPES[airframe]
    drone = SandboxDrone('D01', '练习机', airframe, speed[3], speed[2],
                         waypoints=(offset(CENTER, *start), offset(CENTER, 420, 0)),
                         emergencies=(SandboxEmergency(kind, 10),))
    return replace(config, drones=(drone,), sites=default_sites(config), staff=staff)


def person(role, east=0, north=0):
    return SandboxStaff(role, role, role, *offset(CENTER, east, north))


def test_city_radius_and_waypoint_validation():
    assert within_city_radius(CENTER, offset(CENTER, 0, 14999))[0]
    inside, km = within_city_radius(CENTER, offset(CENTER, 0, 15001))
    assert not inside and km == pytest.approx(15.001)
    config = exercise(staff=(person('foot', 0, 15001),))
    with pytest.raises(ValueError, match='15 公里'):
        SandboxSimulation(config)


@pytest.mark.parametrize('kind', EMERGENCY_TYPES)
@pytest.mark.parametrize('airframe', DRONE_TYPES)
def test_models_and_incidents_are_deterministic(kind, airframe):
    config = exercise(kind, airframe, staff=(person('guard'), person('recovery', 245, -140)))
    first = SandboxSimulation(config)
    before = first.layers(30)
    first.frame(2)
    assert first.layers(30) == before == SandboxSimulation(config).layers(30)
    assert first.evaluate().total_incidents == 1
    assert 0 <= first.evaluate().score <= 100
    assert first.layers(0)['active_highlights'] == []


def test_occlusion_is_visible_for_twenty_seconds_and_keeps_last_seen_point():
    simulation = SandboxSimulation(exercise('signal_occlusion', 'inspection'))
    for at in range(10, 30):
        frame = simulation.frame(at)
        assert frame['aircraft'][0]['age'] == at-9
        assert frame['aircraft'][0]['lonlat'] == simulation.frame(9)['aircraft'][0]['lonlat']
        assert frame['attention_cards'][0]['status'] == '楼宇遮挡 · 等待重现'
    assert simulation.frame(30)['aircraft'][0]['age'] == 0
    assert simulation.frame(30)['attention_cards'] == []


def test_weather_and_school_peak_change_the_outcome():
    config = exercise('wind_drift', 'camera', time_str='15:00')
    calm = SandboxSimulation(config)
    gust = SandboxSimulation(replace(config, weather=WEATHERS[1]))
    assert metres(calm.frame(19)['aircraft'][0]['lonlat'], gust.frame(19)['aircraft'][0]['lonlat']) > 10
    calm = SandboxSimulation(exercise(time_str='15:00', strategy=SandboxStrategy(pre_alert=False)))
    peak = SandboxSimulation(replace(calm.config, time_str='17:30'))
    assert peak.evaluate().exposure_seconds > 0
    assert peak.evaluate().score < calm.evaluate().score
    guard = exercise(staff=(person('guard', 142),))
    assert SandboxSimulation(guard).evaluate().avg_arrival_s == 7
    assert SandboxSimulation(replace(guard, weather=WEATHERS[2])).evaluate().avg_arrival_s is None


def test_ground_roles_and_safe_landing():
    walk = SandboxSimulation(exercise(staff=(person('foot', 225),))).evaluate()
    vehicle = SandboxSimulation(exercise(staff=(person('vehicle', 225),))).evaluate()
    guard = SandboxSimulation(exercise(staff=(person('guard'),))).evaluate()
    assert guard.avg_arrival_s == 7 < vehicle.avg_arrival_s < walk.avg_arrival_s
    low = exercise('low_battery', 'logistics', start=(-215, 0))
    assert SandboxSimulation(low).evaluate().resolved_count == 0
    managed = SandboxSimulation(replace(low, staff=(person('recovery', 245, -140),)))
    assert managed.frame(80)['aircraft'][0]['altitude'] == 0
    assert managed.evaluate().resolved_count == 1
    assert '安全引导' in {link['label'] for link in managed.frame(18)['civic_links']}


def test_staff_suggestion_and_shared_question_comparison():
    config = quick_example(SandboxConfig('changsha', CENTER))
    team, reason = suggest_staffing(config)
    assert {person.role for person in team} == {'guard', 'vehicle', 'recovery'}
    assert all(within_city_radius(config.center, person.point)[0] for person in team)
    assert '值守员' in reason and '应急降落点' in reason
    event = config.sites[0].point
    basic = replace(config, staff=(SandboxStaff('P01', '巡查员 1', 'foot', *offset(event, 300, -175)),),
                    strategy=SandboxStrategy('方案 1', allow_reassign=False, pre_alert=False))
    improved = replace(config, staff=tuple(team), strategy=SandboxStrategy('方案 2'))
    left, right = (SandboxSimulation(option).evaluate() for option in (basic, improved))
    assert right.exposure_seconds < left.exposure_seconds
    assert right.score > left.score
    assert left.total_incidents == right.total_incidents
    assert left.avg_first_response_s == 4 and right.avg_first_response_s == 2
    assert right.avg_arrival_s > 0
    assert compare_strategies([left, right])['best'] == '方案 2'


def test_confirmations_bind_to_replay_time():
    config = exercise(staff=(person('guard'),), strategy=SandboxStrategy(mode='confirm', pre_alert=False))
    simulation = SandboxSimulation(config)
    card = simulation.frame(14)['attention_cards'][0]
    assert card['actions'] == ('approve', 'decline')
    assert simulation.decide(14, card['id'], 'approve')
    assert simulation.frame(9)['attention_cards'] == []
    assert simulation.evaluate().avg_arrival_s == 9
    refused = SandboxSimulation(config)
    assert refused.decide(14, card['id'], 'decline')
    assert refused.evaluate().resolved_count == 0


def test_local_library_round_trip_and_name_validation(tmp_path):
    library = SandboxLibrary(tmp_path)
    config = quick_example(SandboxConfig('changsha', CENTER))
    library.save('放学演练', config)
    assert library.list() == ['放学演练']
    assert library.load('放学演练') == config
    assert (tmp_path/'sandboxes'/'放学演练.json').is_file()
    with pytest.raises(ValueError):
        library.save('../外部', config)
    library.delete('放学演练')
    assert library.list() == []



def test_high_priority_request_can_redirect_a_patrol_after_confirmation():
    config = exercise(staff=(person('vehicle', 420),), strategy=SandboxStrategy(mode='confirm', pre_alert=False))
    crowd = replace(config.drones[0], emergencies=(SandboxEmergency('crowd_intrusion', 24),))
    slow = SandboxDrone('D02', '偏航练习', 'custom', False, 60,
                        waypoints=(offset(CENTER, -810, 30), offset(CENTER, -800, 45)),
                        emergencies=(SandboxEmergency('wind_drift', 6),))
    config = replace(config, drones=(crowd, slow))
    simulation = SandboxSimulation(config)
    assert simulation.decide(10, 'D02:0', 'approve')
    assert simulation.decide(28, 'D01:0', 'reassign')
    assert simulation.layers(28)['staff_markers'][0]['status'] == '已改派'
    locked = SandboxSimulation(replace(config, strategy=replace(config.strategy, allow_reassign=False)))
    assert locked.decide(10, 'D02:0', 'approve')
    assert next(card for card in locked.frame(28)['attention_cards'] if card['id'] == 'D01:0')['actions'] == ()


def test_cached_road_changes_the_patrol_path():
    patrol = person('vehicle', 285)
    config = exercise(staff=(patrol,))
    route = (patrol.point, offset(CENTER, 285, 230), offset(CENTER, 0, 230), CENTER)
    direct = SandboxSimulation(config)
    road = SandboxSimulation(replace(config, staff=(replace(patrol, custom_routes=(route,)),)))
    first = direct.layers(14)['staff_markers'][0]
    second = road.layers(14)['staff_markers'][0]
    assert first['lat'] == patrol.lat < second['lat']
    assert second['eta_s'] > first['eta_s']


def test_peripheral_landing_and_mobile_fallback():
    staffed = exercise('low_battery', 'logistics', start=(-215, 0), staff=(person('vehicle', -180),))
    no_site = replace(staffed, sites=staffed.sites[:1])
    simulation = SandboxSimulation(no_site)
    assert simulation.evaluate().resolved_count == 1
    assert any('临时警戒' in entry['text'] for entry in simulation.frame(120)['feed'])
    assert any('外围平稳降落' in entry['text'] for entry in simulation.frame(120)['feed'])
    assert simulation.evaluate().avg_arrival_s >= 15


def test_landing_types_and_scene_driven_fleets():
    assert len(CITY_EVENT_TEMPLATES) == 8
    assert [SandboxSite('L1', 'lawn', *CENTER, landing_kind=kind).name for kind in LANDING_TYPES] == [
        '应急降落 · 公园草坪', '应急降落 · 空旷广场', '应急降落 · 露天停车场', '应急降落 · 体育场 / 开阔空地']
    config = exercise()
    fleets = [typical_drones(config, variant=variant) for variant in range(4)]
    assert len({tuple(drone.waypoints for drone in fleet) for fleet in fleets}) == 4
    assert all(3 <= len(drone.waypoints) <= 4 for fleet in fleets for drone in fleet)
    config = replace(config, sites=(replace(config.sites[0], template='residential'),))
    assert typical_drones(replace(config, time_str='21:00'))[0].emergencies[0].kind == 'nuisance_hover'
    assert typical_drones(replace(config, time_str='13:00'))[0].emergencies[0].kind == 'crowd_intrusion'
    assert typical_drones(replace(config, weather=WEATHERS[1]), variant=1)[1].emergencies[0].kind == 'wind_drift'
    office = replace(config, sites=(replace(config.sites[0], template='office'),))
    assert {d.emergencies[0].kind for d in typical_drones(office)[:2]} == {'altitude_exceed', 'signal_occlusion'}


def test_later_incident_stays_in_denominator_after_early_landing():
    config = exercise('low_battery', 'logistics', start=(-180, 0), staff=(person('recovery', 245, -140),))
    drone = config.drones[0]
    config = replace(config, drones=(replace(drone, emergencies=(*drone.emergencies, SandboxEmergency('wind_drift', 100))),))
    supported = SandboxSimulation(config).evaluate()
    unsupported = SandboxSimulation(replace(config, staff=())).evaluate()
    assert supported.resolved_count == 1
    assert supported.total_incidents == unsupported.total_incidents == 2


@pytest.mark.parametrize('action', DIRECTOR_ACTIONS)
def test_director_recomputes_and_persists(action, tmp_path):
    config = exercise(airframe='camera', staff=(person('guard'), person('vehicle')))
    live = SandboxSimulation(config)
    earlier, original = live.frame(15), live.frame(16)
    assert live.inject(action, 16)
    assert live.frame(15) == earlier and live.frame(16) != original
    assert any(entry['badge'] == '临场' and entry['at'] == 16 for entry in live.frame(16)['feed'])
    if action == 'blocked':
        assert live.frame(16)['staff_markers'][0]['blocked_s'] == 25
        assert live.frame(40)['staff_markers'][0]['blocked_s'] == 1
        assert live.frame(41)['staff_markers'][0]['blocked_s'] == 0
    assert live.evaluate().total_incidents == 2
    library = SandboxLibrary(tmp_path)
    library.save('临场考题', live.solidify())
    saved = SandboxSimulation(library.load('临场考题'))
    assert [live.frame(at) for at in range(121)] == [saved.frame(at) for at in range(121)]


def test_wizard_role_tools_custom_snapshot_and_autoplay(tmp_path, monkeypatch):
    monkeypatch.setenv('QT_QPA_PLATFORM', 'offscreen')
    monkeypatch.delenv('SKYGUARD_AMAP_KEY', raising=False)
    monkeypatch.delenv('SKYGUARD_AMAP_SECURITY_CODE', raising=False)
    from PySide6.QtWidgets import QApplication, QComboBox
    from skyguard.ui.shell import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow(tmp_path)
    try:
        window.switch_page('sandbox')
        page = window.sandbox
        assert window.system_time_button.isHidden() and window.model_button.isHidden()
        page.load_example()
        page.set_step(2)
        assert set(page.tool_buttons) == {'foot', 'vehicle', 'guard', 'recovery'}
        assert len(page.scroll.widget().findChildren(QComboBox)) == 2
        baseline = len(page.config.staff)
        page.tool_buttons['vehicle'].click()
        for east in (10, 20):
            lon, lat = offset(page.config.center, east, 0)
            page.map_clicked(lat, lon)
            assert page.tool_buttons['vehicle'].isChecked()
        assert [p.role for p in page.config.staff[baseline:]] == ['vehicle', 'vehicle']
        page.tool_buttons['vehicle'].click()
        assert page.tool is None
        page._strategy(name='协同值守')
        page.save_snapshot()
        page.save_snapshot()
        assert [saved.strategy.name for saved in page.snapshots] == ['协同值守', '方案 1']
        page.set_step(3)
        assert page.timer.isActive() and page.speed.currentText() == '2×'
        assert len(page.cursor.events) == 3
    finally:
        window.close()
        app.processEvents()
