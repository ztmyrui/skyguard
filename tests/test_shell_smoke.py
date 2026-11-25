"""Exercise the real desktop shell with an isolated, offline data directory."""
import pytest


@pytest.fixture(scope='module')
def qt_app():
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv('QT_QPA_PLATFORM', 'offscreen')
        environment.delenv('SKYGUARD_AMAP_KEY', raising=False)
        environment.delenv('SKYGUARD_AMAP_SECURITY_CODE', raising=False)
        pytest.importorskip('PySide6.QtWebEngineWidgets', reason='QtWebEngine 不可用')
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        yield app


@pytest.fixture
def desktop(qt_app, tmp_path):
    from PySide6.QtCore import QEvent

    from skyguard.ui.shell import MainWindow

    window = MainWindow(tmp_path)
    window.resize(1180, 760)
    window.show()
    try:
        yield window, qt_app
    finally:
        window.map_view.web.stop()
        window.close()
        window.deleteLater()
        qt_app.sendPostedEvents(window, QEvent.DeferredDelete)


def test_shell_navigation_and_shared_map(desktop, tmp_path):
    from skyguard.theme import THEMES
    from skyguard.ui.map_view import MapView

    window, app = desktop
    shared_map = window.map_view
    assert shared_map.web.page().backgroundColor().name() == THEMES['klein']['ground'].lower()
    shared_map.reconnect()
    assert not shared_map.is_ready and shared_map.note.text() == '底图载入中…'
    assert window.context.root == tmp_path
    assert window.current_page is window.flight_query
    assert window.nav_buttons['flight_query'].isChecked()
    assert window.settings.management.isHidden()
    assert 'resources' not in window.nav_buttons and 'planning' not in window.nav_buttons
    window.nav_buttons['settings'].click()
    assert window.current_page is window.settings and window.role == 'public'
    assert window.settings.management.isHidden()
    window.nav_buttons['flight_query'].click()
    window.activateWindow()
    app.processEvents()
    assert app.focusWidget() is window.flight_query.city
    for name, title in (
        ('flight_query', '航拍规划'), ('my_plans', '我的计划'), ('flight_logs', '飞行日志'),
        ('situation', '态势演练'), ('sandbox', '自由沙盘'), ('settings', '设置'),
    ):
        if name == 'situation':
            window.switch_role('duty')
        window.nav_buttons[name].click()
        page = window.current_page
        window.refresh()
        app.processEvents()
        assert page is window.pages[name]
        assert window.nav_buttons[name].isChecked()
        assert page.title == window.page_title.text() == title
        assert window.findChildren(MapView) == [shared_map]
        assert window.size().toTuple() == (1180, 760)
        if page.uses_map and not page.map_slot.parentWidget().isHidden():
            assert shared_map.parent() is window.stack and shared_map.isVisible()
            assert shared_map.web.width() >= 420 and shared_map.web.height() >= 300
            assert window.timeline.isHidden()
            assert shared_map.clock_overlay.isHidden()
            assert window.system_time_button.isVisible() == (name not in ('situation', 'sandbox'))
        else:
            assert shared_map.isHidden() and window.timeline.isHidden()

    assert window.settings.management.isHidden() and window.role == 'duty'
    assert window.resources.staff.rowCount() > 0
    window.nav_buttons['sandbox'].click()
    assert window.current_page is window.sandbox and window.sandbox.uses_map
    window.switch_page('tasks')
    assert window.tasks.empty.isVisible() and not window.tasks.action_buttons
    window.switch_page('flight_query')
    _check_theme_switch(window, app, tmp_path)


def test_topbar_role_switch_checks_password(desktop, monkeypatch):
    monkeypatch.setenv('SKYGUARD_DUTY_PASSWORD', 'test-duty-password')
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractButton, QLabel, QLineEdit

    from skyguard.ui.shell import DUTY, PUBLIC
    from skyguard.ui.widgets.dialogs import PrivilegePasswordDialog

    window, app = desktop
    badge, toggle = window.role_badge, window.mode_switch_button
    rail = badge.parentWidget()
    topbar = toggle.parentWidget().layout()
    assert window.role == PUBLIC and window.current_page is window.flight_query
    assert isinstance(badge, QLabel) and badge.objectName() == 'roleBadge'
    assert badge.text() == '航拍助手' and badge.focusPolicy() == Qt.NoFocus
    assert badge.size().toTuple() == (80, 30)
    rail_buttons = set(rail.findChildren(QAbstractButton))
    nav_buttons = set(window.nav_buttons.values())
    assert nav_buttons <= rail_buttons
    assert [button.text() for button in rail_buttons - nav_buttons] == ['帮助']
    assert toggle.isVisible() and toggle.text() == '切换权限模式'
    assert toggle.toolTip() == '输入权限密码切换到「低空治理」模式'
    assert topbar.indexOf(toggle) + 1 == topbar.indexOf(window.model_button)

    observations = []

    def enter_wrong_password_and_cancel():
        dialog = app.activeModalWidget()
        try:
            assert isinstance(dialog, PrivilegePasswordDialog)
            field = dialog.password
            observations.append((field.echoMode(), field.placeholderText(), dialog.focusWidget() is field))
            field.setText('000000')
            QTest.keyClick(field, Qt.Key_Return)
            observations.append((dialog.isVisible(), dialog.error.isVisible(), dialog.error.text(),
                                 dialog.focusWidget() is field, field.selectedText(), window.role))
            next(b for b in dialog.findChildren(QAbstractButton) if b.text() == '取消').click()
        finally:
            dialog.reject()

    QTimer.singleShot(0, enter_wrong_password_and_cancel)
    toggle.click()
    assert observations == [
        (QLineEdit.Password, '请输入权限密码', True),
        (True, True, '密码错误，请重新输入', True, '000000', PUBLIC),
    ]
    assert window.role == PUBLIC

    def enter_correct_password():
        dialog = app.activeModalWidget()
        dialog.password.setText('test-duty-password')
        QTest.keyClick(dialog.password, Qt.Key_Return)
        if dialog.isVisible():
            dialog.reject()

    QTimer.singleShot(0, enter_correct_password)
    toggle.click()
    assert window.role == DUTY and window.current_page is window.situation
    assert badge.text() == '低空治理' and toggle.text() == '切换普通模式'
    assert toggle.toolTip() == '返回「航拍助手」普通模式'
    assert toggle.isVisible() and window.model_button.isHidden() and window.system_time_button.isHidden()
    window.nav_buttons['sandbox'].click()
    assert window.current_page is window.sandbox and toggle.isVisible()
    assert window.model_button.isHidden() and window.system_time_button.isHidden()

    def unexpected_dialog(*_args):
        pytest.fail('返回普通模式时打开了密码框')

    monkeypatch.setattr(PrivilegePasswordDialog, 'exec', unexpected_dialog)
    toggle.click()
    assert window.role == PUBLIC and window.current_page is window.flight_query
    assert badge.text() == '航拍助手' and toggle.text() == '切换权限模式'


def test_workspace_guide_map_modes_and_transient_status(desktop, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QLabel, QPushButton

    from skyguard.ui.widgets.help_dialog import HelpDialog

    window, app = desktop
    map_view = window.map_view
    monkeypatch.setattr(map_view, 'run_js', lambda *_: None)
    assert window.statusBar().isHidden()
    window.context.status.emit('已复制数据目录路径。', 20)
    assert window.statusBar().isVisible()
    window.statusBar().clearMessage()
    assert window.statusBar().isHidden()

    assert map_view.configure_button.isVisible()
    map_view._map_ready(1)
    assert map_view.configure_button.isHidden() and map_view.note.isHidden()
    map_view._map_unavailable()
    assert map_view.configure_button.isVisible()
    map_view._map_ready(1)
    assert map_view.legend.isHidden()
    map_view.legend_button.click()
    for name, mode, symbol in (
        ('flight_query', 'public', '起降点'),
        ('situation', 'civic_replay', '楼宇信号遮挡'),
        ('sandbox', 'sandbox_edit', '市中心 15 km 作业范围'),
    ):
        window.open_workspace(name)
        assert map_view.legend.mode == mode
        assert map_view.legend_button.isVisible() and map_view.legend.isVisible()
        assert any(w.isVisible() and w.text() == symbol for w in map_view.legend.findChildren(QLabel))

    shown = []

    def inspect_guide():
        dialog = app.activeModalWidget()
        if isinstance(dialog, HelpDialog):
            shown.append((dialog.windowTitle(), dialog.tabs.currentIndex(),
                          tuple(dialog.tabs.tabText(i) for i in range(dialog.tabs.count()))))
        dialog.reject()

    QTimer.singleShot(0, inspect_guide)
    window.help_dialog()
    assert shown == [('低空守望 · 使用指南', 2, ('航拍助手', '态势演练', '自由沙盘', '科学口径与图例'))]
    for role in ('public', 'duty'):
        window.switch_role(role)
        window.switch_page('settings')
        assert window.settings.management.isHidden()
        assert all(w.text() != '重置演练' for w in window.settings.findChildren(QPushButton))


def test_sandbox_coverage_event_ticks_and_automatic_comparison(desktop):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QTableWidget

    from skyguard.sandbox import WEATHERS, SandboxStaff

    window, _ = desktop
    window.open_workspace('sandbox')
    page = window.sandbox
    assert page.tool_bar.isHidden()
    page.load_example()
    page.set_step(2)
    lon, lat = page.config.sites[0].point
    guard = SandboxStaff('P01', '安保员 1', 'guard', lon, lat)
    patrol = SandboxStaff('P02', '巡查员 2', 'foot', lon, lat)
    page._update_config(staff=(guard, patrol), weather=WEATHERS[2])
    page._select_staff(1)
    assert [person['coverage_m'] for person in page.map_payload()['sandbox_edit']['staff']] == [None, 20.4]
    page._select_staff(0)
    assert [person['coverage_m'] for person in page.map_payload()['sandbox_edit']['staff']] == [127.5, None]
    page.save_snapshot()
    page._strategy(allow_reassign=not page.config.strategy.allow_reassign)
    page.save_snapshot()
    page.set_step(3)
    assert window.map_view.legend.mode == 'civic_replay'
    second, title, _ = page.cursor.events[0]
    x = page.cursor._x(second, page.cursor._axis())
    assert (second, title) in page.cursor._nearby(x)
    QTest.mouseClick(page.cursor, Qt.LeftButton, pos=QPoint(x, 5))
    assert page.cursor.value() == second and not page.timer.isActive()
    assert not page.results
    page._show_review(True)
    assert len(page.results) == 2
    table = page.scroll.widget().findChild(QTableWidget, 'sandboxComparison')
    assert table.isVisible() and table.columnCount() == 3



def _check_theme_switch(window, app, tmp_path):
    from PySide6.QtGui import QPalette

    from skyguard.theme import THEMES, load_theme

    assert window.theme_name == 'klein'
    assert window.settings.theme_buttons['klein'].isChecked()
    assert window.theme_button.toolTip() == '切换到跑道夜航（深色）'
    assert window.theme_button.size().toTuple() == (32, 32)
    assert window.map_view.web.url().query() == 'theme=klein'
    query = _demo_query(window, app)
    verdict = query.result_panel.verdict_card
    window.theme_button.click()
    assert window.theme_name == load_theme(tmp_path) == 'night'
    assert window.settings.theme_buttons['night'].isChecked()
    assert window.theme_button.toolTip() == '切换到克莱因蓝（浅色）'
    assert app.palette().color(QPalette.Window).name().upper() == THEMES['night']['ground']
    assert window.map_view.web.page().backgroundColor().name().upper() == THEMES['night']['ground']
    assert query.result_panel.verdict_card is verdict
    for name, page in window.pages.items():
        window.switch_page(name)
        page.refresh()
        assert window.current_page is page
    window.settings.theme_buttons['klein'].click()
    assert window.theme_name == load_theme(tmp_path) == 'klein'
    assert window.theme_button.toolTip() == '切换到跑道夜航（深色）'


def _demo_query(window, app):
    from skyguard.flight_rules import DEMO_START
    from skyguard.ui.widgets.common import ActionButton

    query = window.flight_query
    query.set_manual(True)
    assert query.result_panel.empty.isVisible()
    window.settings.demo.setChecked(True)
    app.processEvents()
    assert window.settings.demo.isChecked() and query.query().start == DEMO_START
    assert ('演示资料', 'demo') in query.context_tags()
    assert not any(button.text() == '演练目击上报' and button.isVisible()
               for button in window.findChildren(ActionButton))
    query.set_takeoff((112.9530, 28.1792))
    window.map_view.clicked.emit(28.1792, 112.953)
    window.map_view.clicked.emit(28.180, 112.955)
    query.check()
    app.processEvents()
    return query


def test_demo_query_pushes_highlighted_rule_and_flight_range(desktop, monkeypatch):
    from skyguard.ui.map_view import MapView

    window, _app = desktop
    view = window.map_view
    query = window.flight_query
    pushes = []
    monkeypatch.setattr(MapView, 'push', lambda _view, payload, _selected: pushes.append(payload))
    view.reconnect()
    view.bridge.ready.emit(1)
    window.settings.demo.setChecked(True)

    query.set_takeoff((112.9530, 28.1792))
    query.map_point(28.1792, 112.9530)
    assert len(pushes[-1]['routes'][0]['waypoints']) == 1
    assert not any(zone['kind'] == 'plan' for zone in pushes[-1]['zones'])
    query.map_point(28.180, 112.955)
    query.check()

    payload = pushes[-1]
    assert len(payload['routes'][0]['waypoints']) == 2
    assert not payload['route_editing']
    assert any(zone['name'] == '计划飞行范围' and zone['rings'] for zone in payload['zones'])
    assert any(zone['name'] == '滨江活动临时限制（演练）' and zone['highlight'] is True
               for zone in payload['zones'])

    view.reconnect()
    assert not view.is_ready
    pushes.clear()
    view.bridge.ready.emit(1)
    assert len(pushes)==1 and pushes[0]['route_epoch']>payload['route_epoch']
    assert {k:v for k,v in pushes[0].items() if k!='route_epoch'} == {k:v for k,v in payload.items() if k!='route_epoch'}

    assert query.edit_result_route.isVisible()
    query.edit_result_route.click()
    assert query.editor_step == 1 and pushes[-1]['route_editing']
    assert not query.route_editor.point_fields.isHidden()
    assert not pushes[-1]['routes'][0].get('shots')
    from skyguard.amap_adapter import display_payload
    query.open_photo_preview()
    assert display_payload(pushes[-1])['photo_preview']
    assert pushes[-1]['routes'][0].get('shots')
    query.close_photo_preview()
    assert not display_payload(pushes[-1])['photo_preview']
    query.route_editor.index.setCurrentIndex(0)
    query.route_editor.focus_waypoint(0)
    assert display_payload(pushes[-1])['selected_waypoint'] == 0
    query.route_editor.camera.setValue(135)
    query.route_editor.camera.editingFinished.emit()
    assert query.route_editor.waypoints[0].camera_deg == 135
    query.route_editor.undo_edit()
    assert query.route_editor.waypoints[0].camera_deg is None
    query.route_editor.redo_edit()
    assert query.route_editor.waypoints[0].camera_deg == 135
    query.set_step(2)
    assert '已设 2 个航点' in query.route_summary.text()


def _action(widget, text):
    from skyguard.ui.widgets.common import ActionButton

    return next(button for button in widget.findChildren(ActionButton) if button.text() == text)


def test_demo_check_inline_save_plan_card_and_recheck(desktop, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from skyguard.flight_check import Verdict
    from skyguard.ui.widgets.flight_result import VerdictCard

    window, app = desktop
    from skyguard.flight_rules import DEMO_START
    monkeypatch.setattr(window.context.clock, '_real', lambda tz: DEMO_START.astimezone(tz))
    query = _demo_query(window, app)
    panel = query.result_panel
    assert query.result.verdict is Verdict.RESTRICTED
    assert isinstance(panel.verdict_card, VerdictCard)
    assert panel.verdict_card.headline.text() == query.result.headline == '禁止或受限'
    assert len(panel.check_rows) == 5
    assert query.result.demo
    restriction = next(row for row in panel.check_rows if row.item.key == 'local_rules')
    restriction.toggle.click()
    assert restriction.toggle.isChecked()

    assert panel.save_button.text() == '定位受限航段'
    query.route_editor.draft.click()
    identifier = query.editing
    assert identifier and not panel.editor.isVisible()
    assert not panel.editor.isVisible()
    window.nav_buttons['my_plans'].click()
    card, = window.my_plans.cards
    assert card.plan['id'] == identifier and card.plan['draft'] and card.plan['verdict'] == 'restricted'
    assert card.primary.isEnabled()
    _action(card, '重新查飞').click()
    assert window.current_page is query
    assert query.result.verdict is Verdict.RESTRICTED and query.editing == identifier

    window.nav_buttons['my_plans'].click()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a: QMessageBox.Yes)
    window.my_plans.cancel_plan(identifier)
    assert not window.my_plans.cards
    assert query.editing is None


def test_legacy_plan_is_read_only(desktop):
    from PySide6.QtWidgets import QPushButton
    from skyguard.ui.widgets.common import Badge

    window, app = desktop
    query = _demo_query(window, app)
    civic = window.context.civic
    old = query.checked_query.record()
    old.pop('route')
    civic.data['plans'].append(dict(id='legacy', title='旧版航线', city='changsha',
        query=old, result='旧状态', cancelled=False))
    window.switch_page('my_plans')
    card, = window.my_plans.cards
    assert any(badge.text() == '旧版范围报备' and badge.property('tone') == 'unknown'
               for badge in card.findChildren(Badge))
    assert any(badge.text() == '旧版范围报备' for badge in card.findChildren(Badge))
    assert not any(b.text() in ('载入编辑', '去报备') for b in card.findChildren(QPushButton))
    assert any(b.text() == '移出计划列表' for b in card.findChildren(QPushButton))


def test_formal_query_accepts_delayed_model_and_discards_stale_replies(desktop, monkeypatch):
    from skyguard.flight_check import ItemState, Verdict
    from skyguard.flight_model import FlightModelConfig
    from skyguard.official_notices import Scan
    from skyguard.ui.pages import flight_query

    window, app = desktop
    query = window.flight_query
    requests, timers, pushes = [], [], []
    now = 0
    view = window.map_view
    monkeypatch.setattr(view, 'push', lambda payload, _selected: pushes.append(payload))
    view.reconnect()
    view.bridge.ready.emit(1)
    monkeypatch.setattr(window.context, 'run_flight_lookup', lambda _fn, done, fail: requests.append((done, fail)))

    class Timer:
        @staticmethod
        def singleShot(delay, _parent, callback):
            timers.append((now + delay, callback))

    def advance_to(target):
        nonlocal now
        start = now
        for deadline, callback in sorted(timers, key=lambda timer: timer[0]):
            if start < deadline <= target:
                now = deadline
                callback()
        now = target

    def model_text():
        items = query.result_panel.model.items
        return [items.itemAt(index).widget().text() for index in range(items.count())]

    monkeypatch.setattr(flight_query, 'QTimer', Timer)
    window.context.flight_model_config = FlightModelConfig('https://example.test/v1', 'test', 'test-key')
    query.set_takeoff((112.939867574218,28.180547690102994))
    query.map_point(28.180547690102994, 112.939867574218)
    query.map_point(28.18074, 112.94001)
    query.check()
    assert query.result.provisional and not query.result_panel.save_button.isEnabled()
    delayed_scan, _ = requests.pop(0)
    query.route_editor.height.setValue(150)
    query.route_editor.change_waypoint()
    pushes.clear()
    delayed_scan(Scan(('旧请求栏目',)))
    assert query.result is None and not pushes

    query.check()
    expected_map = pushes[-1]
    assert len(expected_map['routes'][0]['waypoints']) == 2
    assert any(zone['kind'] == 'plan' for zone in expected_map['zones'])
    pushes.clear()
    scan_done, _ = requests.pop(0)
    scan_done(Scan(('已读栏目',)))
    assert pushes and pushes[-1]['routes'][0]['waypoints'] == expected_map['routes'][0]['waypoints']
    assert not query.result.provisional and query.result.verdict is Verdict.PERMIT
    assert query.result.items[-1].state is ItemState.OK
    app.processEvents()
    panel = query.result_panel
    verdict_card, check_rows = panel.verdict_card, panel.check_rows
    assert verdict_card.isVisible() and check_rows
    assert [row.item for row in check_rows] == list(query.result.items)
    assert all(row.isVisible() for row in check_rows)
    assert not panel.model.content.isVisible() and panel.save_button.isEnabled()
    panel.model.toggle.click()
    assert panel.model.content.isVisible()
    assert model_text() == ['正在生成解读…']
    model_done, _ = requests.pop(0)
    (deadline, _), = timers
    assert deadline == flight_query.MODEL_WAIT_SECONDS * 1000 == 30000
    timers.append((10000, lambda: model_done(('10 秒后返回的解读',))))
    advance_to(6500)
    assert model_text() == ['正在生成解读…']
    assert query.check_button.isEnabled()
    advance_to(10000)
    assert model_text() == ['10 秒后返回的解读'] and query.check_button.isEnabled()
    assert panel.verdict_card is verdict_card and panel.check_rows is check_rows
    advance_to(30000)
    assert model_text() == ['10 秒后返回的解读']

    query.check()
    scan_done, _ = requests.pop(0)
    scan_done(Scan(('已读栏目',)))
    expired_model, _ = requests.pop(0)
    query.route_editor.height.setValue(151)
    query.route_editor.change_waypoint()
    expired_model(('条件已修改的解读',))
    assert query.result is None and panel.model.isHidden()

    query.check()
    scan_done, _ = requests.pop(0)
    scan_done(Scan(('已读栏目',)))
    late_model, _ = requests.pop(0)
    advance_to(60000)
    assert model_text() == ['AI 解读超时']
    late_model(('超时后返回的解读',))
    assert model_text() == ['AI 解读超时']
    assert query.check_button.isEnabled() and query.result.verdict is Verdict.PERMIT



@pytest.mark.parametrize(('outcome', 'expected'), [
    ('ok', '连接成功 · 用时 0.5 秒 · 返回 2 条事项'),
    ('timeout', '连接失败 · 请求超时。'),
    ('auth', '连接失败 · HTTP 401。'),
    ('format', '连接失败 · 模型回复格式无效。'),
    ('network', '连接失败 · 网络错误。'),
])
def test_model_dialog_tests_unsaved_inputs_in_background(desktop, monkeypatch, outcome, expected):
    import json
    from threading import Event, get_ident
    from time import monotonic, sleep

    import httpx

    from skyguard.flight_model import FlightModel, FlightModelConfig
    from skyguard.ui.widgets import dialogs

    window, app = desktop
    context = window.context
    previous_config = context.flight_model_config = FlightModelConfig(
        'https://saved.example/v1', 'saved-model', 'saved-key',
    )
    test_key = 'unsaved-test-key'
    ui_thread = get_ident()
    received = []
    release = Event()

    def exchange(request):
        received.append((get_ident(), request))
        assert release.wait(timeout=2.0)
        if outcome == 'timeout':
            raise httpx.ReadTimeout(test_key, request=request)
        if outcome == 'network':
            raise httpx.ConnectError(test_key, request=request)
        if outcome == 'auth':
            return httpx.Response(401, text=test_key)
        content = '{"checks":["示例一",{"item":"示例二"}]}' if outcome == 'ok' else test_key
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})

    transport = httpx.MockTransport(exchange)
    monkeypatch.setattr(dialogs, 'FlightModel', lambda config: FlightModel(config, transport))
    monkeypatch.setattr(dialogs, 'perf_counter', iter((1.0, 1.5)).__next__)
    dialog = dialogs.FlightModelDialog(window, context)
    dialog.show()
    try:
        dialog.endpoint.setText('https://unsaved.example/v1')
        dialog.model.setText('unsaved-model')
        dialog.key.setText(test_key)
        dialog.test_button.click()
        assert dialog.test_button.text() == '测试中…' and not dialog.test_button.isEnabled()
        dialog.test_button.click()
    finally:
        release.set()
        deadline = monotonic() + 3
        while context.flight_workers and monotonic() < deadline:
            app.processEvents()
            sleep(0.01)
        dialog.close()
    assert not context.flight_workers
    assert dialog.test_button.text() == '测试连接' and dialog.test_button.isEnabled()
    assert dialog.error.text() == expected and test_key not in dialog.error.text()
    (worker_thread, request), = received
    assert worker_thread != ui_thread
    assert str(request.url) == 'https://unsaved.example/v1/chat/completions'
    assert request.headers['authorization'] == f'Bearer {test_key}'
    payload = json.loads(request.content)
    assert payload['model'] == 'unsaved-model' and test_key not in request.content.decode()
    assert '连接测试示例' in payload['messages'][1]['content']
    assert context.flight_model_config == previous_config
    assert not context.flight_model_store.path.exists()


def test_situation_lifecycle_scope_and_metric_filters(desktop):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window, app = desktop
    page, service = window.situation, window.context.service
    window.resize(1280, 800)
    window.switch_page('situation')
    window.activateWindow()
    app.processEvents()
    assert all(button.focusPolicy() == Qt.TabFocus for button in page.kpis.buttons.values())
    assert app.focusWidget() is page.event_scope.group.checkedButton()
    first_metric = page.kpis.buttons['active']
    first_metric.setFocus(Qt.TabFocusReason)
    QTest.keyClick(first_metric, Qt.Key_Tab)
    assert app.focusWidget() is page.kpis.buttons['priority']
    assert all(card.name.visibleRegion().boundingRect().height() >= 14 for card in page.cards.values())
    views = service.views()
    counts = {'active': len(views), 'priority': sum(view['priority'] == '优先核验' for view in views),
              'tasks': 0}
    assert {key: int(value.text()) for key, value in page.kpis.values.items()} == counts
    QTest.mouseClick(page.kpis.buttons['tasks'], Qt.LeftButton)
    assert page.kpis.buttons['tasks'].isChecked() and page.events_empty.isVisible()
    QTest.mouseClick(page.kpis.buttons['tasks'], Qt.LeftButton)
    assert not page.kpis.buttons['tasks'].isChecked() and len(page.cards) == len(views)

    QTest.mouseClick(page.cards['obs-c'], Qt.LeftButton)
    assert window.context.selected == 'obs-c' and page.detail.name.text() == '巡检协作 C'
    window.map_view.selected.emit('obs-b')
    assert page.cards['obs-b'].isChecked() and page.detail.status.text() == '待核验'
    page.detail.buttons['start'].click()
    assert page.detail.status.text() == '核验中'
    page.detail.buttons['conclude'].click()
    page.detail.submit.click()
    assert page.detail.form_error.text() == '请填写核验结论'
    assert page.detail.form_error.isVisible()
    page.detail.note.setPlainText('已补齐演练观察记录。')
    page.detail.submit.click()
    assert page.detail.status.text() == '待复核 · 已澄清'
    page.detail.buttons['approve'].click()
    assert 'obs-b' in service.closed
    page.event_scope.setCurrentIndex(0)
    assert 'obs-b' not in page.cards
    page.event_scope.setCurrentIndex(1)
    assert set(page.cards) == {'obs-b'} and page.cards['obs-b'].isChecked()
    assert page.detail.status.text() == '已关闭 · 已澄清'
    page.tabs.setCurrentIndex(1)
    assert page.audit_table.item(0, 1).text() == '事件状态 · 待复核 → 已关闭'
    page.tabs.setCurrentIndex(0)
    assert int(page.kpis.values['active'].text()) == len(service.views())
    page.detail.buttons['reopen'].click()
    page.detail.note.setPlainText('收到一条补充记录。')
    page.detail.submit.click()
    assert page.detail.status.text() == '待核验' and page.event_scope.currentIndex() == 0
    assert 'obs-b' not in service.closed
    page.kpis.buttons['tasks'].click()
    assert page.events_empty.isVisible() and window.current_page is window.situation


def test_situation_new_observation_and_inline_association(desktop):
    from PySide6.QtCore import QTimer

    window, app = desktop
    service = window.context.service
    window.switch_page('situation')
    page = window.situation
    window.context.select('obs-a')
    track = window.context.selected_view()
    window.map_view.clicked.emit(track['lat'], track['lon'])
    assert page.right.currentWidget() is page.point_check

    def save_observation():
        dialog = app.activeModalWidget()
        dialog.name.setText('新目击')
        dialog.accuracy.setValue(50)
        dialog.validate()

    service.close_event('obs-b', '演练关闭')
    page.event_scope.setCurrentIndex(1)
    QTimer.singleShot(0, save_observation)
    page.begin_observation()
    window.map_view.clicked.emit(track['lat'], track['lon'])
    eid = window.context.selected
    assert page.event_scope.currentIndex() == 0 and page.cards[eid].isChecked()
    assert page.detail.status.text() == '新报告'
    assert 'obs-a' in page.detail.evidence.rows
    ask, yes, no = page.detail.evidence.rows['obs-a']
    app.processEvents()
    page.detail.basis_toggle.setChecked(True)
    app.processEvents()
    page.detail.scroll.ensureWidgetVisible(ask)
    assert ask.visibleRegion().boundingRect().height() >= 32
    ask.click()
    assert yes.isVisible() and no.isVisible() and eid not in service.links
    no.click()
    assert ask.isVisible() and eid not in service.links
    ask.click()
    yes.click()
    app.processEvents()
    assert service.links[eid] == 'obs-a'
    assert page.detail.evidence.linked.text().endswith('obs-a')
    page.detail.buttons['accept'].click()
    assert page.detail.status.text() == '待核验'
    page.detail.buttons['plan'].click()
    assert window.current_page is window.situation
    assert page.right.currentWidget() is window.planning
    assert not window.map_view.legend.isVisible()



def test_resource_roster_csv_validation_and_draft_audit(desktop, tmp_path, monkeypatch):
    import csv

    from PySide6.QtWidgets import QFileDialog

    window, _ = desktop
    window.switch_role('duty')
    window.switch_page('settings')
    page = window.resources
    first_id = page.staff.item(0, 0)
    first_post = page.staff.item(0, 5)
    first_id.setText(page.staff.item(1, 0).text())
    assert not page.staff_save.isEnabled() and '工号' in first_id.toolTip()
    first_id.setText('p1')
    first_post.setText('missing-post')
    assert not page.staff_save.isEnabled() and '长沙规则包' in first_post.toolTip()
    first_post.setText('east')
    assert page.staff_save.isEnabled()

    roster_file = tmp_path / 'roster.csv'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_: (str(roster_file), ''))
    page.export_staff()
    with roster_file.open(encoding='utf-8-sig', newline='') as stream:
        row = next(csv.DictReader(stream))
    assert row['start_s'] == '0' and row['end_s'] == '1800'
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *_: (str(roster_file), ''))
    page.import_staff()
    assert page.staff.item(0, 3).text() == '15:20:00' and page.staff_save.isEnabled()

    page.tabs.setCurrentIndex(2)
    page.draft_title.setText('演练公告草稿')
    page.draft_source.setText('https://example.test/notice')
    page.draft_text.setPlainText('演练公告正文')
    page.save_draft()
    assert window.context.civic.data['announcements'][-1]['title'] == '演练公告草稿'
    assert page.draft_title.text() == ''
    page.tabs.setCurrentIndex(3)
    page.audit_action.setCurrentIndex(page.audit_action.findData('登记公告草稿'))
    assert page.audit_table.rowCount() == 1
    audit_file = tmp_path / 'audit.csv'
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *_: (str(audit_file), ''))
    page.export_audit()
    with audit_file.open(encoding='utf-8-sig', newline='') as stream:
        exported = list(csv.DictReader(stream))
    assert [row['动作'] for row in exported] == ['登记公告草稿']


def test_task_center_inline_review_and_incident_deep_link(desktop):
    from skyguard.diagnostics import check_task_cycle

    window, _ = desktop
    service = window.context.service
    service.generate('obs-a', '事件协同')
    service.confirm(0)
    check_task_cycle(window)


def test_empty_states_center_in_available_area(desktop):
    from skyguard.ui.widgets.common import EmptyState
    window, app = desktop
    window.resize(1440,900)
    for name in ('my_plans','flight_logs','tasks'):
        window.switch_page(name)
        app.processEvents()
        page = window.current_page
        state, = [widget for widget in page.findChildren(EmptyState) if widget.isVisible()]
        viewport = state.parentWidget()
        delta = state.mapTo(viewport,state.rect().center()) - viewport.rect().center()
        assert abs(delta.x()) <= 12 and abs(delta.y()) <= 12
        content = state.layout().itemAt(0).widget()
        assert content.width() <= 420
        assert (content.geometry().center() - state.rect().center()).manhattanLength() <= 4


def test_disabled_notice_watcher_makes_no_requests(desktop, monkeypatch):
    from datetime import datetime, timedelta
    from skyguard.aerial import CHINA
    from skyguard.flight_rules import FlightQuery
    window, _ = desktop
    context = window.context
    now = datetime.now(CHINA)
    from skyguard.flight_route import FlightRoute, Waypoint
    route = FlightRoute((Waypoint(112.95, 28.18, 70, 60),), 5, (112.95, 28.18), now, '轻型')
    query = FlightQuery((),'route',75,now,route.end,70,'轻型',demo=False,route=route)
    context.civic.save_plan('changsha','待检查计划',query,'待核对')
    context.civic.watch_settings(0)
    context.schedule_notices()
    monkeypatch.setattr(context,'run_flight_lookup',lambda *_: pytest.fail('关闭提醒时发起了请求'))
    context.poll_notices()
    assert not context.watch_timer.isActive()


@pytest.mark.parametrize('failure',['timeout','format'])
def test_material_falls_back_to_local_template(desktop, monkeypatch, failure):
    from datetime import datetime, timedelta
    import httpx
    from skyguard.aerial import CHINA
    from skyguard.filing_material import DECLARATION, facts_for
    from skyguard.flight_model import FlightModel, FlightModelConfig
    from skyguard.flight_rules import FlightQuery
    from skyguard.ui.widgets import flight_journey
    from PySide6.QtWidgets import QLabel

    window, _ = desktop
    context = window.context
    context.flight_model_config = FlightModelConfig('https://example.test/v1','fake','test-key')
    now = datetime.now(CHINA)
    query = FlightQuery(((112.95,28.18),),'circle',75,now,now+timedelta(hours=1),70,'轻型',demo=False)
    def send(request):
        if failure=='timeout':
            raise httpx.ReadTimeout('mock',request=request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    monkeypatch.setattr(flight_journey,'FlightModel',
                        lambda config: FlightModel(config, httpx.MockTransport(send)))
    def run(work, success, failed):
        try:
            success(work())
        except (ValueError,httpx.RequestError):
            failed()
    monkeypatch.setattr(context,'run_flight_lookup',run)
    panel = flight_journey.MaterialCard(context,lambda purpose: facts_for('长沙',query,'待核对',purpose))
    panel.generate()
    copy = next(button for button in panel.findChildren(flight_journey.ActionButton) if button.text()=='复制全文')
    text = [item.text() for item in panel.findChildren(QLabel)]
    assert '模板生成，未经 AI 润色' in text
    copy.click()
    assert flight_journey.QApplication.clipboard().text().endswith(DECLARATION)
    panel.deleteLater()


def test_route_draft_city_switch_and_coordinate_edit_preserve_details(desktop, monkeypatch):
    window, _ = desktop
    query = window.flight_query
    editor = query.route_editor
    query.set_takeoff((112.94,28.17))
    query.map_point(28.17, 112.94)
    query.map_point(28.18, 112.95)
    editor.height.setValue(90)
    editor.hover.setValue(40)
    editor.change_waypoint()
    monkeypatch.setattr('skyguard.ui.pages.flight_query.QInputDialog.getMultiLineText',
                        lambda *_: ('112.9401,28.17\n112.95,28.18', True))
    query.coordinates()
    original = editor.snapshot()
    assert [(w.altitude_m, w.hover_s) for w in editor.waypoints] == [(70,0),(90,40)]
    assert editor.waypoints[0].lon == 112.9401
    query._prepared = {'check'}
    query.city.setCurrentIndex(query.city.findData('shanghai'))
    assert not query.points and not query._prepared
    query.city.setCurrentIndex(query.city.findData('changsha'))
    assert editor.snapshot() == original


def test_finder_discards_in_flight_response_after_preference_edit(desktop, monkeypatch):
    window, _ = desktop
    query = window.flight_query
    finder = query.finder
    callbacks = []
    monkeypatch.setattr(window.context, 'run_aerial_lookup', lambda fn, done, fail: callbacks.append(done))
    finder.text.setText('湘江日落')
    finder.search()
    done, = callbacks
    finder.text.setText('公园夜景')
    done(())
    assert finder.message.isHidden()
    assert query.result_stack.currentWidget() is query.recommend_empty
    assert finder.search_button.isEnabled()


def test_default_recommendation_and_shared_mission_input(desktop, monkeypatch):
    window, _ = desktop
    query = window.flight_query
    finder = query.finder
    jobs = []
    monkeypatch.setattr(window.context, 'run_aerial_lookup', lambda fn, done, fail: jobs.append((fn, done)))
    assert finder.text is query.mission_brief
    assert all(not finder.topics[tag].isHidden() for tag in ('江河', '湖面', '城市天际线', '公园绿地', '日落', '夜景'))
    finder.search()
    run, done = jobs.pop()
    topics, proposals = run()
    assert not topics and 1 <= len(proposals) <= 3
    assert len({proposal.scene.point for proposal in proposals}) == len(proposals)
    done((topics, proposals))
    assert finder.proposals == proposals
    assert window.flight_query.result_stack.currentWidget() is query.preview_panel
    query.mission_kind.setCurrentIndex(1)
    assert query.result_stack.currentWidget() is query.recommend_empty
    assert not query.mission_time.isHidden()


def test_planb_poi_response_switch_preview_adopt_and_save(desktop, monkeypatch):
    import json
    from datetime import datetime

    from skyguard.aerial import CHINA
    from skyguard.flight_route import FlightRoute
    from skyguard.ui.map_view import MapView
    from skyguard.ui.widgets.common import SegmentedControl

    window, app = desktop
    page, map_view = window.flight_query, window.map_view
    finder = page.finder
    jobs, requests = [], []
    monkeypatch.setattr(window.context, 'run_aerial_lookup', lambda action, done, fail: jobs.append((action, done)))
    monkeypatch.setattr(MapView, 'is_ready', property(lambda self: True))
    monkeypatch.setattr(map_view, 'lookup_city_place', lambda *args: requests.append(args))
    finder.now = lambda: datetime(2026, 11, 23, 13, tzinfo=CHINA)
    finder.text.setText('新港艺术馆')
    finder.search()
    revision, city, keyword = requests[-1]
    assert city.startswith('长沙') and keyword == '新港艺术馆' and not jobs
    payload = json.dumps([{'name': keyword, 'location': [112.90, 28.19], 'type': '艺术馆'}])
    map_view.place_resolved.emit(revision - 1, payload)
    assert not jobs
    map_view.place_resolved.emit(revision, payload)
    run, done = jobs.pop(0)
    done(run())
    assert page.active_proposal.source_kind == 'poi'
    initial = page.active_proposal.query.route
    toggle = page.preview_panel.findChild(SegmentedControl)
    assert toggle.group.button(2).text() == '快速打卡'
    toggle.group.button(2).click()
    selected = page.active_proposal.query.route
    assert selected != initial and page.active_proposal.style_key == 'quick'
    assert page.map_payload()['routes'][0]['waypoints'] == [[w.lon, w.lat, w.altitude_m] for w in selected.waypoints]
    page.open_photo_preview()
    shot = page.mission(selected)['shots'][0]
    page.photo_preview.seek.setValue(round(shot['seconds'] * 10) + 5)
    assert '俯角' in page.photo_preview.detail.text() and '低空定场推进' in page.photo_preview.detail.text()
    page.adopt_proposal(finder.proposals[0], page.active_proposal, '')
    assert page.confirm_takeoff and page.query().route.waypoints == selected.waypoints
    page.save_route_draft()
    saved = window.context.civic.data['plans'][0]
    assert FlightRoute.parse(saved['query']['route']).waypoints == selected.waypoints
    assert saved['mission']['shots'][0]['pitch'] is not None

    finder.text.setText('离线示例艺术馆')
    finder.search()
    map_view.place_resolved.emit(requests[-1][0], '[]')
    run, done = jobs.pop()
    done(run())
    app.processEvents()
    assert finder.proposals and all(plan.windows[0].source_kind != 'poi' for plan in finder.proposals)


def test_civic_training_layout_and_replay(desktop):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QCheckBox, QLabel, QTabWidget
    from skyguard.training_city_cases import CIVIC_CASES

    window, _ = desktop
    window.open_workspace('situation')
    page = window.situation
    panel = page.training
    session = panel.session
    assert page.left_stack.isHidden()
    assert page.columns.count() == 3 and page.columns.sizes()[0] == 0
    assert not hasattr(panel, 'event_card')
    assert panel.right.isVisible() and panel.stream.isVisible()
    assert not panel.right.findChildren(QTabWidget)
    assert not panel.toolbar.findChildren(QCheckBox)
    assert [panel.scenes.itemText(i) for i in range(panel.scenes.count())] == list(CIVIC_CASES)
    session.seek_civic(34)
    text = '\n'.join(label.text() for label in panel.right.findChildren(QLabel))
    text += '\n'.join(panel.stream.item(i).data(Qt.AccessibleTextRole) for i in range(panel.stream.count()))
    assert not any(word in text for word in ('A00', 'A11', '权重', 'CPA', '编队', '观景人群区 · L'))
    session.seek_civic(120)
    assert panel.timeline.play.text() == '重播'
    panel.timeline.play.click()
    assert session.clock_running and session.now == session.cursor == 0
    panel.close()
    assert not page.left_stack.isHidden() and panel.timeline.isHidden()


def test_wizard_navigation_clicks_and_confirmation(desktop):
    from dataclasses import replace

    from PySide6.QtWidgets import QLabel, QPushButton

    from skyguard.amap_adapter import display_payload
    from skyguard.sandbox import SandboxStrategy
    from test_sandbox import exercise, person

    window, app = desktop
    window.switch_role('duty')
    window.nav_buttons['sandbox'].click()
    page = window.sandbox
    assert window.current_page is page and page.uses_map
    assert window.nav_buttons['sandbox'].text() == '自由沙盘'
    assert set(page.cities) >= {'changsha', 'guangzhou', 'shanghai', 'shenzhen', 'hangzhou'}
    page.load_example()
    page._reset(exercise(staff=(person('guard'),), strategy=SandboxStrategy(mode='confirm')))
    for i, button in enumerate(page.step_buttons):
        button.click()
        assert page.step == i and button.isChecked()
        app.processEvents()
        payload = display_payload(page.map_payload())
        assert ('sandbox_edit' in payload) == (i < 3)
    assert payload['staff_markers'][0]['role'] == 'guard'
    page.cursor.setValue(12)
    app.processEvents()
    action = next(button for button in page.scroll.findChildren(QPushButton)
                  if button.text() == '同意执行' and button.isVisible())
    action.click()
    assert page.simulation.evaluate().resolved_count == 1
    labels = ' '.join(widget.text() for widget in page.scroll.findChildren(QLabel))
    assert all(term not in labels for term in ('A00', 'A11', '权重 35%', 'CPA'))
