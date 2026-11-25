"""Run isolated diagnostics through the normal desktop components."""
from copy import deepcopy
from datetime import datetime
import json
import os
from pathlib import Path
from secrets import token_hex, token_urlsafe
import sys
import time
import traceback
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from .amap_adapter import CredentialStore, MapCredentials
from .flight_check import CHINA, ItemState, Verdict
from .flight_model import FlightModel, FlightModelConfig
from .flight_rules import DEMO_START, FlightQuery
from .official_notices import Notice, Scan, interpret_periods
from .ui.shell import MainWindow


def _selected_item(public, key):
    return next(item for item in public.result.items if item.key == key)


def _flight_done(window):
    return not window.context.flight_workers and window.flight_query.check_button.isEnabled()


def _check_cities(window):
    public = window.flight_query
    window.context.notice_lookup.search = lambda _: Scan(('https://example.test/index.html',))
    for identifier in ('guangzhou', 'shanghai', 'shenzhen', 'hangzhou'):
        city = window.context.civic.cities()[identifier]
        public.city.setCurrentIndex(public.city.findData(identifier))
        assert window.map_view.heading.text() == city['name'] + '地图'
        assert not public.points
        lon, lat = city['center']
        public.set_takeoff((lon, lat))
        public.map_point(lat, lon)
        public.map_point(lat+.001, lon+.001)
        public.check()
        assert public.result.provisional and len(public.result.items) == 6
        yield lambda: _flight_done(window)
        assert _selected_item(public, 'base_airspace').state is ItemState.UNKNOWN
        assert _selected_item(public, 'notices').state is ItemState.OK
        assert public.result.verdict is Verdict.CHECK and not public.result.provisional
    public.city.setCurrentIndex(public.city.findData('changsha'))


def _check_demo(window, output):
    public = window.flight_query
    public.clear()
    window.settings.demo.setChecked(True)
    public.set_takeoff((112.950,28.180))
    public.map_point(28.180, 112.950)
    public.map_point(28.180, 112.960)
    assert len(public.points) == 2
    public.route_editor.undo_edit()
    assert len(public.points) == 1
    public.route_editor.redo_edit()
    assert len(public.points) == 2 and public.query().geometry().area > 0
    public.check()
    assert public.result.verdict is Verdict.RESTRICTED and public.result.alternatives
    assert public.result.demo
    yield
    window.grab().save(str(output / '公众_查飞.png'))
    start = public.result.alternatives[0][0]
    public.alternate(start)
    assert public.checked_query.start == start and public.result.verdict is Verdict.CLEAR


def _example_notices(city):
    assert city == 'changsha'
    expired = '2026年11月15日0时至11月15日15时'
    active = '2026年11月19日16时至11月19日18时'
    return Scan(('https://example.test/index.html',), (), (
        Notice('已结束公告', 'https://example.test/expired.html', expired, interpret_periods(expired)),
        Notice('本次测试公告', 'https://example.test/active.html', active, interpret_periods(active)),
    ), datetime(2026, 11, 19, 16, 32, tzinfo=CHINA))


def _start_official_check(window):
    window.context.notice_lookup.search = _example_notices
    window.context.flight_model_config = FlightModelConfig('https://example.test/v1', 'test', 'temporary-test-key')
    public = window.flight_query
    window.settings.demo.setChecked(False)
    public.set_time(datetime(2026, 11, 19, 16, 32, tzinfo=CHINA))
    public.clear()
    public.route_editor.default_height.setValue(150)
    public.radius.setValue(200)
    public.set_takeoff((112.939867574218,28.180547690102994))
    public.map_point(28.180547690102994, 112.939867574218)
    public.map_point(28.18075, 112.94000)
    public.check()
    assert public.result.provisional and public.result.verdict is Verdict.PERMIT
    assert _selected_item(public, 'base_airspace').state is ItemState.OK


def _check_model(window, output):
    captured = []
    advice = '150 米计划需核对飞行活动申请，并打开公告原文确认时段。'

    def assess(_model, city, query, facts, notices):
        captured.append((city, query.altitude_agl, len(facts), len(notices)))
        return (advice,)

    public = window.flight_query
    panel = public.result_panel
    with patch.object(FlightModel, 'assess', assess):
        _start_official_check(window)
        yield lambda: _flight_done(window)
        assert captured == [('长沙', 150, 2, 1)]
        assert public.result.verdict is Verdict.PERMIT and not public.result.provisional
        assert panel.verdict_card.headline.text() == '需申请后飞行'
        notices = _selected_item(public, 'notices')
        assert [source.title for source in notices.evidence] == ['本次测试公告']
        assert not panel.model.toggle.isChecked()
        panel.model.toggle.click()
        assert panel.model.content.isVisible()
        assert any(label.text() == advice for label in panel.model.content.findChildren(QLabel))
        panel.check_rows[-1].toggle.click()
        yield
        tag = panel.check_rows[-1].findChild(QLabel, 'noticeTag')
        assert tag.height() >= tag.heightForWidth(tag.width())
        window.grab().save(str(output / '公众_依据与公告.png'))

    with patch.object(FlightModel, 'assess', side_effect=RuntimeError('isolated service failure')):
        public.check()
        yield lambda: _flight_done(window)
        assert _selected_item(public, 'notices').evidence[0].title == '本次测试公告'
        assert public.result.verdict is Verdict.PERMIT
        assert any('AI 解读失败' in label.text() for label in panel.model.content.findChildren(QLabel))
    window.context.flight_model_config = FlightModelConfig()


def _check_duty(window, app, output, result):
    window.switch_page('situation')
    assert window.context.selected_view()['track_state'] == '模拟雷达跟踪'
    assert len(window.context.selected_view()['prediction']) > 1
    assert any(v['track_state'] == '仅目击待核实' for v in window.context.service.views())
    yield
    window.grab().save(str(output / '工作人员_态势.png'))
    page = window.situation
    window.context.select('obs-b')
    assert not window.map_view.legend.isVisible() and page.detail.status.text() == '待核验'
    page.detail.buttons['start'].click()
    page.detail.buttons['conclude'].click()
    page.detail.note.setPlainText('诊断用演练核验记录。')
    page.detail.submit.click()
    assert page.detail.status.text() == '待复核 · 已澄清'
    page.detail.buttons['approve'].click()
    assert page.detail.status.text() == '已关闭 · 已澄清'
    assert page.event_scope.currentIndex() == 1 and page.cards['obs-b'].isChecked()
    page.tabs.setCurrentIndex(1)
    assert page.audit_table.item(0, 1).text() == '事件状态 · 待复核 → 已关闭'
    page.tabs.setCurrentIndex(0)
    assert int(page.kpis.values['active'].text()) == len(window.context.service.views())
    window.context.select('obs-a')
    window.switch_page('planning')
    window.planning.generate()
    yield lambda: not window.context.busy
    service = window.context.service
    plans = service.pending['plans']
    assert plans and all(2 <= len(plan['tasks']) <= 3 for plan in plans)
    observe = next(task for task in plans[0]['tasks'] if task['key'] == 'observe')
    assert observe['post'] and observe['end_s'] - observe['start_s'] == 180
    service.confirm(0)
    window.refresh()
    assert len(service.scene.tasks) == len(plans[0]['tasks'])
    assert page.detail.status.text() == '核验中'
    assert page.right.currentWidget() is page.planner
    yield
    window.grab().save(str(output / '工作人员_筹划.png'))
    check_task_cycle(window)
    check_resources(window)
    yield from check_settings(window)
    window.switch_page('resources')
    yield
    window.grab().save(str(output / '工作人员_资源.png'))
    result.update(task_execution_cycle=True, staff_and_city_preview=True, settings_and_demo_sync=True)
    result.update(candidate_plans=len(plans), created_tasks=len(service.scene.tasks), situation_lifecycle=True)


def check_task_cycle(window, capture=None):
    """Check task execution through inline controls and the event deep link."""
    window.switch_page('tasks')
    tasks = window.tasks
    service = window.context.service
    event_id = service.pending['event_id']
    steps = [task for task in service.scene.tasks if task['event_id'] == event_id and task['status'] == '待接单']
    assert len(steps) >= 2 and not window.map_view.isVisible()
    first = steps[0]
    tasks.cards[first['id']].click()
    QApplication.processEvents()
    if capture:
        capture('任务中心_有任务')
    replan = next(button for button in tasks.detail.findChildren(QPushButton)
                  if button.isVisible() and button.text() == '换人重新筹划')
    replan.click()
    assert window.current_page is window.situation and window.situation.right.currentWidget() is window.planning
    assert window.context.selected == event_id
    assert first['status'] == '待接单' and not first['history']
    window.switch_page('tasks')
    tasks.cards[first['id']].click()
    tasks.action_buttons['accept'].click()
    assert first['status'] == '进行中'
    old_clock = service.scene.clock
    window.timeline.seek(first['end_s'] + 1)
    assert int(tasks.filter_buttons['逾期'].text().split()[-1]) >= 1
    assert any(widget.text() == '逾期' for widget in tasks.cards[first['id']].findChildren(QLabel))
    window.timeline.seek(old_clock)
    tasks.action_buttons['submit'].click()
    assert tasks.form.isVisible()
    tasks.form_submit.click()
    assert tasks.error.isVisible() and first['status'] == '进行中'
    tasks.note.setPlainText('演练记录：已核对现场情况。')
    tasks.outcome.setCurrentIndex(1)
    if capture:
        capture('任务中心_内联反馈')
    tasks.form_submit.click()
    assert first['status'] == '待复核' and first['history'][-1]['outcome'] == '观察到'
    tasks.action_buttons['reject'].click()
    tasks.note.setPlainText('请补充核对依据。')
    tasks.form_submit.click()
    assert first['status'] == '进行中'
    _submit_and_approve(tasks, '已补充演练记录。')
    assert [entry['action'] for entry in first['history']] == ['accept', 'submit', 'reject', 'submit', 'approve']
    for task in steps[1:]:
        tasks.cards[task['id']].click()
        tasks.action_buttons['accept'].click()
        _submit_and_approve(tasks, '演练步骤已核对。')
        assert task['status'] == '已完成'
    assert service.incidents[event_id]['status'] == '核验中'
    QApplication.processEvents()
    assert tasks.completion_buttons[event_id].isVisible()
    tasks.completion_buttons[event_id].click()
    assert window.current_page is window.situation and window.context.selected == event_id
    suggestion = window.situation.detail.sections['next_steps']
    assert any('该事件的任务已全部完成，可记录结论' in line.text()
               for line in suggestion.findChildren(QLabel))


def _submit_and_approve(page, note):
    page.action_buttons['submit'].click()
    page.note.setPlainText(note)
    page.form_submit.click()
    page.action_buttons['approve'].click()
    page.form_submit.click()


def check_resources(window, capture=None):
    """Save edited shift times, preview changes and check the audit entry."""
    window.switch_page('resources')
    page = window.resources
    context = window.context
    page.tabs.setCurrentIndex(0)
    assert not window.map_view.isVisible()
    first_start = page.staff.item(0, 3)
    first_end = page.staff.item(0, 4)
    initial_time = first_start.text()
    first_end.setText(initial_time)
    assert not page.staff_save.isEnabled() and '结束须晚于开始' in page.staff_errors.text()
    first_end.setText('15:48:00')
    first_start.setText('15:21:00')
    assert page.staff_save.isEnabled()
    page.staff_save.click()
    saved = context.civic.data['staff'][0]
    assert (saved['start_s'], saved['end_s']) == (60, 1680)
    plans = context.service.generate('obs-c', '事件协同', selected_people=[saved['id']])['plans']
    assert plans and all(task['start_s'] >= 60 for task in plans[0]['tasks'])
    assert plans[0]['snapshot']['people'][saved['id']]['end_s'] == 1680
    if capture:
        capture('资料资源_人员与值班')
    page.tabs.setCurrentIndex(1)
    city = deepcopy(context.civic.cities()['changsha'])
    original = deepcopy(city)
    city['posts'][0]['range_m'] += 1
    first_rule = city['rules'].pop(0)
    city['rules'].append(dict(first_rule, id=first_rule['id'] + '-p5', name='新增示例规则',
                              demo=False, verified=False))
    page.preview_city(json.dumps(city))
    assert page.city_stack.currentIndex() == 1 and not page.preview_warning.isHidden()
    differences = {page.diff.item(index, 1).data(Qt.AccessibleTextRole) for index in range(page.diff.rowCount())}
    assert differences == {'新增', '删除', '修改'}
    assert context.civic.cities()['changsha'] == original
    if capture:
        capture('资料资源_规则包导入预览')
    page.city_confirm.click()
    assert page.city_stack.currentIndex() == 0
    assert context.civic.cities()['changsha']['posts'][0]['range_m'] == city['posts'][0]['range_m']
    page.tabs.setCurrentIndex(3)
    page.audit_action.setCurrentIndex(page.audit_action.findData('发布城市规则包'))
    page.audit_search.setText('changsha')
    assert page.audit_table.rowCount() >= 1
    assert page.audit_table.item(0, 1).text() == '发布城市规则包'
    context.civic.import_city(json.dumps(original))
    page.refresh_cities()
    page.audit_search.clear()
    page.tabs.setCurrentIndex(0)
    records = deepcopy(context.civic.data['staff'])
    records[0]['start_s'], records[0]['end_s'] = 0, 1800
    page.load_staff(records)
    page.staff_save.click()


def check_settings(window, capture=None):
    """Verify credential-safe settings and the background connection test."""
    context = window.context
    initial_model, initial_map = context.flight_model_config, context.map_credentials
    key = token_urlsafe(18)
    map_key, map_code = token_hex(16), token_hex(16)
    context.flight_model_config = FlightModelConfig('https://api.example.test:8443/v1', 'example-model', key)
    context.map_credentials = MapCredentials(map_key, map_code)
    window.switch_role('public')
    window.model_button.click()
    page = window.settings
    assert window.current_page is page and page.management.isHidden()
    assert all(button.text() != '重置演练' for button in page.findChildren(QPushButton))
    assert not window.map_view.isVisible() and page.map_status.text() == '已配置'
    texts = [item.text() for item in page.findChildren(QLabel)]
    assert all(secret[index:index + 6] not in text for text in texts for secret in (key, map_key, map_code)
               for index in range(len(secret) - 5))
    assert 'api.example.test' in page.model_details.text()
    assert all(value not in page.model_details.text() for value in ('8443', '/v1'))
    assert '已保存' in page.model_details.text()
    page.demo.setChecked(True)
    assert window.flight_query.demo_mode
    window.flight_query.set_demo_mode(False)
    assert not page.demo.isChecked()
    with patch.object(FlightModel, 'assess', return_value=('连接测试示例',)):
        page.test_button.click()
        assert not page.test_button.isEnabled()
        yield lambda: not context.flight_workers
        assert '连接成功' in page.test_result.text() and page.test_button.isEnabled()
    window.switch_role('duty')
    window.nav_buttons['settings'].click()
    assert window.current_page is page and page.management.isHidden()
    if capture:
        capture('设置')
    context.flight_model_config, context.map_credentials = initial_model, initial_map
    window.refresh()


def _check_aerial(window, output, result):
    from datetime import timedelta
    from .aerial import FORECAST_OUTSIDE, Forecast, WeatherHour, touched_hours
    from .ui.widgets.common import ActionButton
    from .official_notices import Scan

    public = window.flight_query
    window.switch_page('flight_query')
    public.city.setCurrentIndex(public.city.findData('changsha'))
    public.set_demo_mode(False)
    from .flight_route import Waypoint
    public.route_editor.reset()
    public.route_editor.default_height.setValue(70)
    for point in ((112.954, 28.180), (112.964, 28.184), (112.969, 28.190)):
        public.route_editor.add(*point)
    points = list(public.route_editor.waypoints)
    points[0] = Waypoint(*points[0].xy, 70, 1200)
    public.route_editor.restore((points, points[0].xy, 5))
    public.resolved_points = None
    public.gcj_selection = False
    public._notices = None
    start = DEMO_START.replace(hour=16, minute=40)
    public.set_time(start)
    public.category.setCurrentText('轻型')
    public.special.setCurrentIndex(0)
    public.aircraft_model.setCurrentIndex(1)
    public.capture_style.setCurrentText('日落')
    context = window.context
    context.notice_lookup.search = lambda _city: Scan()
    context.flight_model_config = FlightModelConfig()
    context.weather_lookup.forecast = lambda _center: Forecast(tuple(
        WeatherHour(hour, '少云', 2, 3, .05, 0, 10, .12, False, ((10, 2), (80, 3)), 3)
        for hour in touched_hours(start, start + timedelta(hours=48))
    ), start)
    public.describe_points()
    public.check()
    yield lambda: _flight_done(window)
    public.start_aerial()
    yield lambda: public.aerial.assessment is not None
    assert public.aerial.quality.weather is not None and public.aerial.quality.framing is None
    public.aerial.pick_subject.emit()
    assert public.subject_pick
    public.map_point(28.184,112.966)
    assert public.aerial.score is not None, (public.aerial.assessment.status or public.aerial.assessment.hazard
                                           or public.result.headline)
    assert len(public.aerial.assessment.rows) >= 2
    assert public.aerial.scenes
    assert len(public.aerial.alternatives) <= 3
    marker = public.aerial.payload()['point']
    assert 112.954 < marker[0] < 112.969
    public.aerial.tabs.setCurrentIndex(1)
    public.aerial.tabs.setCurrentIndex(2)
    scene = public.aerial.scenes[0]
    card = public.aerial.content.itemAt(0).widget()
    assert {widget.text() for widget in card.findChildren(QLabel)} == {m.place.name for m in public.aerial.scenes}
    buttons = card.findChildren(ActionButton)
    assert len(buttons) == len(public.aerial.scenes) and all(button.text() == '定位' for button in buttons)
    locate = next(button for button in buttons if button.accessibleName() == f'定位{scene.place.name}')
    locate.click()
    assert public.aerial.payload()['selected'] == scene.place.id
    focus = public.aerial.payload()['focus']
    locate.click()
    assert public.aerial.payload()['focus'] == focus + 1
    yield
    window.grab().save(str(output / '沿途景点.png'))
    yield lambda: not any(w.isRunning() for w in context.aerial_workers)
    public.save_plan('航拍功能验收')
    saved = next(p for p in context.civic.data['plans'] if p['title'] == '航拍功能验收')
    assert saved['aerial']['model'] == 'DJI Mini 4 Pro'
    assert saved['aerial']['score'] == round(public.aerial.score)
    result['aerial_route_hourly_scenery_and_plan'] = True
    public.set_time(start + timedelta(days=365))
    public.invalidate()
    public.check()
    yield lambda: _flight_done(window)
    public.start_aerial()
    yield lambda: public.aerial.assessment is not None
    assert public.aerial.assessment.status == FORECAST_OUTSIDE and public.aerial.score is None
    assert str(start.year) in public.forecast_hint.text()
    yield
    window.grab().save(str(output / '超期指数.png'))
    public.aerial.tabs.setCurrentIndex(1)
    yield
    window.grab().save(str(output / '超期小时天气.png'))
    result['forecast_range_and_compact_scenery'] = True


def _check_photo_workspace(window, output):
    from PySide6.QtGui import QColor, QImage
    from .flight_route import FlightRoute, Waypoint
    from .photo_mission import mission_for
    from .ui.widgets.task_card import write_task_card

    window.open_workspace('flight_query')
    page, civic = window.flight_query, window.context.civic
    start = datetime(2026, 11, 23, 16, tzinfo=CHINA)
    point = (112.949,28.180)
    route = FlightRoute((Waypoint(112.9494,28.1802,35,8,270), Waypoint(*point,35,0,260)),
                         3,point,start,'轻型')
    query = FlightQuery((),'route',25,start,route.end,0,route.category,demo=False,route=route)
    identifier = civic.save_plan('changsha','航拍自检',query,'待核对',
                                 mission=mission_for(route,'repeat','航拍自检','7 天'))
    plan = next(row for row in civic.data['plans'] if row['id']==identifier)
    page.load_plan(plan,recheck=False)
    page.open_photo_preview()
    page.photo_preview._jump(1)
    assert not page.photo_preview.isHidden() and page._photo_frame['heading'] is not None
    page.close_photo_preview()
    window.switch_page('my_plans')
    window.my_plans.open_results(plan)
    workflow = window.my_plans.results
    media = output/'DJI_20261123_160003.png'
    image = QImage(64,48,QImage.Format_RGB32)
    image.fill(QColor('#315e89'))
    assert image.save(str(media))
    assert workflow.import_files([str(media)])==1
    workflow._associate(1)
    assert workflow.plan['mission']['materials'][0]['shot']=='repeat-1'
    write_task_card(output/'任务卡.png',workflow.plan,'长沙')
    assert (output/'任务卡.png').is_file()
    window.my_plans.close_results()


def _steps(window, app, output, result):
    yield
    yield lambda: window.map_view.is_ready
    result.update(map_provider='amap', live_map_checked=True)
    window.resize(1440, 900)
    yield from _check_cities(window)
    yield from _check_demo(window, output)
    result.update(public_check_and_alternate=True, city_and_range_controls=True, formal_queries=5)
    yield from _check_model(window, output)
    result['official_basis_and_model_fallback'] = True
    yield from _check_duty(window, app, output, result)
    initial = window.theme_name
    window.theme_button.click()
    assert window.theme_name != initial
    for name, page in window.pages.items():
        window.switch_page(name)
        page.refresh()
    yield
    window.set_theme(initial)
    result['theme_switch_and_page_refresh'] = True
    window.switch_page('sandbox')
    sandbox = window.sandbox
    sandbox.load_example()
    sandbox.set_step(2)
    sandbox.save_snapshot()
    sandbox._strategy(pre_alert=False)
    sandbox.save_snapshot()
    sandbox.set_step(3)
    sandbox._show_review(True)
    assert len(sandbox.results) == 2
    assert {option.total_incidents for option in sandbox.results} == {3}
    assert {option.avg_first_response_s for option in sandbox.results} == {2, 4}
    result['sandbox_replay_and_comparison'] = True
    yield from _check_aerial(window, output, result)
    _check_photo_workspace(window, output)
    result['photo_plan_rehearsal_and_media'] = True
    from .training_city_cases import CIVIC_CASES
    window.open_workspace('situation')
    panel = window.situation.training
    assert panel.enabled and window.situation.left_stack.isHidden()
    training = window.context.training
    training.clock.stop()
    assert panel.scenes.count() == 3
    for index, city in enumerate(CIVIC_CASES.values()):
        panel.scenes.setCurrentIndex(index)
        layers = window.situation.map_payload()
        assert layers['city'] == city and not layers['zones'] and not layers['active_highlights']
        assert len(layers['staff_markers']) == 1
    result['independent_city_exercises']=True
    training.seek_civic(34)
    layers = training.map_layers()
    assert len(layers['aircraft']) == 5
    assert layers['staff_markers'][0]['status'] == '已改派'
    assert layers['staff_markers'][0]['route']
    assert layers['active_highlights'][0]['name'] == '展馆市集区'
    assert any(row['state'] == '失联' for row in layers['aircraft'])
    assert any(panel.stream.item(i).data(Qt.UserRole)['tag'] == '动态改派' for i in range(panel.stream.count()))
    result['city_situation_agent']=True


def connect_test_map(window):
    """Connect an isolated QA profile to the locally configured AMap account."""
    profile = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'SkyguardDesktop'
    credentials = CredentialStore(profile).load()
    if not credentials.configured:
        raise RuntimeError('高德地图验收需要本机已保存的高德配置')
    window.context.map_credentials = credentials
    window.context.map_server.configure(credentials)
    window.map_view.reconnect()


def run(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    for name in ('SKYGUARD_AMAP_KEY', 'SKYGUARD_AMAP_SECURITY_CODE'):
        os.environ.pop(name, None)
    app = QApplication([])
    window = MainWindow(output / 'isolated-data')
    connect_test_map(window)
    window.show()
    result = {'frozen': bool(getattr(sys, 'frozen', False)), 'live_map_checked': False}
    started = time.perf_counter()
    steps = _steps(window, app, output, result)
    ready = None

    def finish(passed, error=''):
        result.update(passed=passed, elapsed_seconds=round(time.perf_counter() - started, 2))
        if error:
            result['error'] = error
        (output / '独立程序验证.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf8')
        if passed:
            window.close()
        app.exit(0 if passed else 1)

    def advance():
        nonlocal ready
        try:
            if ready is None or ready():
                ready = next(steps)
        except StopIteration:
            finish(True)
            return
        except Exception as exc:
            traceback.print_exc()
            finish(False, str(exc) or type(exc).__name__)
            return
        QTimer.singleShot(300, advance)

    QTimer.singleShot(0, advance)
    QTimer.singleShot(90000, lambda: finish(False, '诊断超时'))
    return app.exec()
