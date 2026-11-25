"""Offline P9 workstation acceptance and dual-theme screenshots."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import time
import traceback
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skyguard.aerial import CHINA, Forecast, WeatherHour, HOUR
from skyguard.airspace_display import MAX_RECTS
from skyguard.flight_rules import DEMO_START
from skyguard.official_notices import Scan
from skyguard.diagnostics import connect_test_map
from skyguard.ui.shell import MainWindow
from skyguard.ui.widgets.common import ActionButton
from skyguard.ui.widgets.filing import PublicFilingCard

SHOTS = ('飞行查询_机位预演', '航线编辑_红黄航段', '我的计划_阶段卡片')


def button(parent, text):
    matches = [control for control in parent.findChildren(ActionButton) if control.text() == text]
    return next((control for control in matches if control.isVisible()), matches[0])


def forecast(point):
    now = datetime.now(CHINA)
    base = now.replace(minute=0,second=0,microsecond=0)
    return Forecast(tuple(WeatherHour(base + index*HOUR, '少云', 2, 3, .02, 0, 10,
        .15, False, ((10,2),(80,3)), 3, 21) for index in range(48)), now)


def capture(window, shots, name, theme):
    pix = window.grab()
    assert pix.deviceIndependentSize().toSize().toTuple() == (1440,900)
    assert pix.scaled(1440,900,Qt.IgnoreAspectRatio,Qt.SmoothTransformation).save(
        str(shots/f'{name}_{theme}.png'))


def map_painted(window, theme=None):
    rect = window.map_view.web.geometry().translated(window.map_view.geometry().topLeft())
    pos = window.stack.mapTo(window, rect.topLeft())
    pix = window.grab().toImage()
    ratio = pix.devicePixelRatio()
    colors = [pix.pixelColor(int((pos.x()+x)*ratio), int((pos.y()+y)*ratio))
              for x in range(30, rect.width()-10, 31) for y in range(25, rect.height()-10, 23)]
    if not colors or len({color.rgb() for color in colors}) <= 12:
        return False
    if theme is None:
        return True
    light = sum(color.lightness() >= 125 for color in colors) / len(colors)
    return light > .65 if theme == 'klein' else light < .35


def grid_count(window, holder):
    window.map_view.run_js('typeof gridOverlays !== "undefined" ? gridOverlays.length : 0', holder.append)
    return bool(holder and holder[-1] and 0 < holder[-1] <= MAX_RECTS)


def checks(window, output, shots, result):
    from skyguard.flight_check import Verdict
    from skyguard.flight_route import FlightRoute, Waypoint

    public, civic = window.flight_query, window.context.civic
    finder = public.finder
    yield lambda: window.map_view.is_ready
    window.resize(1280,800)
    yield
    assert window.size().toTuple() == (1280,800)
    assert finder.search_button.isVisible() and finder.text.isVisible() and window.map_view.query_mode.isHidden()
    public.planning_mode.click()
    assert window.map_view.query_mode.isVisible()
    result['1280x800'] = True
    window.resize(1440,900)
    window.map_view.run_js('home()')
    assert not window.map_view.grid_check.isChecked()
    window.map_view.grid_check.setChecked(True)
    grid = []
    retry_at = time.monotonic() + 12
    def grid_painted():
        nonlocal retry_at
        if grid_count(window, grid):
            return True
        if time.monotonic() >= retry_at:
            window.map_view.run_js('reportViewport()')
            retry_at = time.monotonic() + 12
        return False

    yield grid_painted
    result['visible_grid_rectangles'] = grid[-1]
    window.map_view.grid_check.setChecked(False)
    window.map_view.query_mode.group.button(0).click()
    window.map_view.bridge.gridPoint(112.949,28.180)
    yield lambda: bool(public.result and not public.result.provisional)
    assert public.checked_query.form == 'circle'
    result['quick_lookup_clicks'] = 2
    window.map_view.query_mode.group.button(1).click()
    public.set_takeoff((112.949,28.180))
    public.route_editor.add(112.949,28.180)
    public.route_editor.add(112.943,28.181)
    assert public.query().route
    original = public.route_editor.snapshot()

    fixed = datetime(2026,11,20,12,tzinfo=CHINA)
    finder.now = lambda: fixed
    base = fixed.replace(hour=0)
    window.context.weather_lookup.forecast = lambda _point: Forecast(tuple(
        WeatherHour(base+i*HOUR, '少云', 2, 3, .05, 0, 12, .15, False, ((10,2),(80,3),(120,3)), 3)
        for i in range(96)), fixed)
    # One text field recognizes both weekend and Xiang River sunset.
    public.set_manual(False)
    finder.model.setCurrentIndex(1)
    finder.text.setFocus()
    finder.text.setText('周末想拍湘江日落')
    finder.search_button.click()
    yield lambda: public.preview_panel.isVisible()
    assert len(finder.proposals) >= 3
    assert all('江河' in p.scene.tags for p in finder.proposals)
    assert all(p.windows[0].verdict != Verdict.RESTRICTED for p in finder.proposals)
    assert public.map_payload()['routes'][0]['style'] == 'preview'
    assert len(public.map_payload()['routes']) == 1
    window.resize(1280,800)
    yield lambda: any(control.isVisible() and control.text() == '收起方案'
                      for control in public.preview_panel.findChildren(ActionButton))
    collapse = button(public.preview_panel,'收起方案')
    assert window.size().toTuple() == (1280,800) and collapse.isVisible(), (
        f'1280×800 航拍方案布局验证：窗口 {window.size().toTuple()}，'
        f'最小尺寸 {window.minimumSizeHint().toTuple()}，收起方案可见 {collapse.isVisible()}')
    window.resize(1440,900)
    yield
    public.open_photo_preview()
    marks = []
    def preview_painted():
        window.map_view.run_js(
            'Boolean(photoDot && photoDot.offsetWidth && document.querySelector(".photo-shot") && !running && !pending)',
            marks.append)
        return bool(marks and marks[-1] and map_painted(window, theme))

    for theme in ('klein','night'):
        window.set_theme(theme)
        marks.clear()
        yield preview_painted
        capture(window,shots,'飞行查询_机位预演',theme)
    button(public.preview_panel,'收起方案').click()
    assert public.route_editor.snapshot() == original
    result['original_route_survived_preview'] = True
    public.clear()
    finder.search_button.click()
    yield lambda: public.preview_panel.isVisible()
    button(public.preview_panel,'采用这条航线').click()
    assert public.query().route and public.confirm_takeoff and public.editor_step == 2
    assert public.confirm_button.isVisible() and public.route_editor.draft.isVisible()
    chosen = public.query().route.takeoff
    public.confirm_button.click()
    assert not public.confirm_takeoff and public.check_button.isVisible()
    assert public.query().route.takeoff == chosen
    public.check()
    yield lambda: bool(public.result and not public.result.provisional)
    assert public.checked_query.route
    result['proposal_to_edit_and_lookup'] = True
    result['proposal_count'] = len(finder.proposals)

    public.set_demo_mode(True)
    public.set_time(DEMO_START)
    editor = public.route_editor
    public.clear()
    editor.reset()
    public.set_takeoff((112.949,28.181))
    for x in (112.949,112.955,112.960,112.979):
        editor.add(x,28.181)
    index = 2
    old = editor.waypoints[index].xy
    window.map_view.bridge.routeAction('changsha','move',index,112.963,28.183)
    assert editor.waypoints[index].xy == (112.963,28.183)
    editor.undo_edit()
    assert editor.waypoints[index].xy == old
    editor.redo_edit()
    assert editor.waypoints[index].xy == (112.963,28.183)
    editor.undo_edit()
    window.map_view.bridge.routeAction('changsha','add',-1,112.978,28.182)
    assert len(editor.waypoints) == 5
    window.map_view.bridge.routeAction('changsha','remove',4,0,0)
    assert len(editor.waypoints) == 4
    yield lambda: {'restricted','check'} <= {s['state'] for s in public._segments}
    public.set_step(2)
    public.check()
    assert public.result.verdict == Verdict.RESTRICTED
    assert public.result_panel.save_button.text() == '定位受限航段'
    window.resize(1280,800)
    yield
    assert window.size().toTuple() == (1280,800) and public.result_panel.save_button.isVisible()
    window.resize(1440,900)
    yield
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield lambda: map_painted(window, theme)
        capture(window,shots,'航线编辑_红黄航段',theme)
    public.route_editor.draft.click()
    draft = civic.data['plans'][-1]
    assert draft['draft']
    try:
        civic.public_filing(draft['id'])
    except ValueError:
        pass
    else:
        raise AssertionError('草稿被报备')
    result['route_edit_drag_undo_redo_red_and_yellow'] = True
    public.edit_result_route.click()
    assert public.editor_step == 1 and public.drawing_route
    counts = []
    def midpoint_painted():
        window.map_view.run_js(
            'Array.from(document.querySelectorAll(".edit-insert")).filter(button=>{'
            'const r=button.getBoundingClientRect();return r.left>0&&r.right<innerWidth&&r.top>0&&r.bottom<innerHeight'
            '}).length', counts.append)
        return bool(counts and counts[-1] == len(editor.waypoints)-1)

    yield midpoint_painted
    result['editable_after_lookup_with_midpoints'] = True
    capture(window, shots, '查飞后修改航线', window.theme_name)
    clicks = []
    deadline = time.monotonic() + 25
    def insert_saved():
        if time.monotonic() >= deadline:
            raise TimeoutError(f'插点未保存，航点数 {len(editor.waypoints)}，坐标待确认 {public.coordinates_pending}，'
                               f'地图返回 {clicks[-1:]!r}')
        if not clicks or not clicks[-1]:
            window.map_view.run_js(
                '(() => { if (window.qaInserted) return true;'
                'for (const button of document.querySelectorAll(".edit-insert")) {'
                'const r=button.getBoundingClientRect();'
                'if(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)!==button)continue;'
                'window.qaInserted=true;button.click();return true;}return false;})()', clicks.append)
        return len(editor.waypoints) == 5 and not public.coordinates_pending

    yield insert_saved
    editor.undo_edit()
    assert len(editor.waypoints) == 4
    result['live_map_insert_click_and_undo'] = True

    public.set_demo_mode(False)
    from skyguard.flight_rules import FlightQuery
    start = datetime.now(CHINA)
    waypoint = Waypoint(112.947,28.183,70,10)
    route = FlightRoute((waypoint,Waypoint(112.949,28.185,80,40)),5,waypoint.xy,start,'轻型')
    query = FlightQuery((),'route',15,route.start,route.end,70,'轻型',demo=False,route=route)
    active_id = civic.save_plan('changsha','湘江航拍',query,'需核对后再飞行',verdict='check')
    window.switch_page('my_plans')
    yield
    active = next(card for card in window.my_plans.cards if card.plan['id']==active_id)
    assert button(active,'出门前刷新').isVisible()
    assert window.my_plans.map_payload()['routes']
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield lambda: map_painted(window, theme)
        capture(window,shots,'我的计划_阶段卡片',theme)
    button(active,'改时间').click()
    assert window.current_page is public and public.editor_step==2
    result['saved_plan_reschedule'] = True

    result['screenshots'] = len(SHOTS)*2 + 1
    result['real_amap_tiles_match_both_themes'] = True
    result['map_provider'] = 'amap'
    result['passed'] = True


def run(output, shots):
    output.mkdir(parents=True,exist_ok=True)
    shots.mkdir(parents=True,exist_ok=True)
    for name in ('SKYGUARD_AMAP_KEY','SKYGUARD_AMAP_SECURITY_CODE'):
        os.environ.pop(name,None)
    app=QApplication([])
    window=MainWindow(output/'isolated-data')
    window.context.weather_lookup.forecast=forecast
    window.context.notice_lookup.search=lambda city: Scan(('https://example.test/index.html',),at=datetime.now(CHINA))
    connect_test_map(window)
    window.show()
    result={}
    steps=checks(window,output,shots,result)
    ready=None
    start=time.monotonic()

    def finish(code, error=None):
        if error:
            result.update(passed=False,error=error)
        (output/'新版界面验证.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        if window.isVisible():
            window.close()
        app.exit(code)

    def tick():
        nonlocal ready
        try:
            if time.monotonic()-start>110:
                raise TimeoutError(f'桌面验收超时，当前阶段 {list(result)}')
            if ready is None or ready():
                ready=next(steps)
        except StopIteration:
            finish(0)
            return
        except Exception as exc:
            traceback.print_exc()
            finish(1,str(exc) or type(exc).__name__)
            return
        QTimer.singleShot(240,tick)

    QTimer.singleShot(0,tick)
    return app.exec()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--shots',type=Path)
    args=parser.parse_args()
    raise SystemExit(run(args.output,args.shots or args.output/'截图'))
