"""Exercise the P12 desktop route, clock, recording and receipt workflow."""
import argparse
from datetime import timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from time import monotonic
import traceback
from unittest.mock import patch

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QInputDialog
from PySide6.QtGui import QPainter

from qa_desktop import button, capture, map_painted
from skyguard.demo_dataset import DATABASES
from skyguard.flight_route import destination
from skyguard.official_notices import Scan
from skyguard.diagnostics import connect_test_map
from skyguard.ui.shell import MainWindow
from skyguard.ui.widgets.clock_panel import ClockPanel

HOME = (112.946, 28.174)


def capture_popup(window, popup, shots, name, theme):
    pix = window.grab()
    assert pix.deviceIndependentSize().toSize().toTuple() == (1440,900)
    child = popup.grab()
    pos = window.mapFromGlobal(popup.mapToGlobal(popup.rect().topLeft()))
    if pos.x() < 0 or pos.y() < 0 or pos.x() + popup.width() > window.width():
        pos = window.rect().center() - popup.rect().center()
    painter = QPainter(pix)
    painter.drawPixmap(pos,child)
    painter.end()
    assert pix.scaled(1440,900,Qt.IgnoreAspectRatio,Qt.SmoothTransformation).save(str(shots/f'{name}_{theme}.png'))


def enter_route(window, report, shots):
    page = window.flight_query
    window.map_view.query_mode.group.button(0).click()
    window.map_view.bridge.clicked.emit(HOME[1],HOME[0])
    assert page.result and page.checked_query.form == 'circle'
    assert button(page.result_panel, '从这里画航线').isEnabled()
    report['临时查一个点'] = 2

    window.map_view.query_mode.group.button(1).click()
    window.map_view.bridge.clicked.emit(HOME[1],HOME[0])
    for metres in (40,120,220):
        lon,lat = destination(HOME,90,metres)
        window.map_view.bridge.clicked.emit(lat,lon)
    assert page.route_editor.takeoff and len(page.route_editor.waypoints)==3
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield
        page.route_editor.popup(1)
        yield
        capture_popup(window,page.route_editor.popup_widget,shots,'航线编辑_航点浮窗',theme)
        page.route_editor.popup_widget.close()
    page.step_bar.group.button(2).click()
    assert page.time_card.isVisible() and page.route_editor.isHidden()
    page.check_button.click()
    yield lambda: bool(window.context.civic.data['plans'])
    assert page.checked_query.route.window_start
    report['画航线并保存'] = 7


def exercise(window, report, shots):
    context = window.context
    window.resize(1440,900)
    yield lambda: window.map_view.is_ready
    real = {name: sha256((context.root/name).read_bytes()).hexdigest()
            for name in DATABASES if (context.root/name).exists()}
    context.use_demo(True)
    current = context.clock.now()
    days = (5 - current.weekday()) % 7
    then = (current + timedelta(days=days)).replace(hour=17,minute=25,second=0,microsecond=0)
    if then < current:
        then += timedelta(days=7)
    context.clock.jump_to(then)
    context.notify_clock(force=True)
    window.flight_query.set_time(then)
    assert '演示' in window.system_time_button.text()
    for theme in ('klein','night'):
        window.set_theme(theme)
        panel = ClockPanel(context)
        panel.show()
        yield
        panel.move(window.mapToGlobal(window.rect().center()-panel.rect().center()))
        yield
        capture_popup(window,panel,shots,'顶栏时间面板',theme)
        panel.close()
        panel.deleteLater()
    yield from enter_route(window,report,shots)
    plan = context.civic.data['plans'][-1]
    identifier = plan['id']
    window.switch_page('my_plans')
    public = window.my_plans
    card = public.cards[0]
    button(card,'去报备').click()
    button(card.filing,'记入本机报备').click()
    assert len(context.civic.data['filings']) == 1
    report['计划到报备'] = 2
    button(card,'开始飞行').click()
    local = context.recorded
    assert local.state and local.plan['filing_id']
    assert not (context.training.scenario and context.training.scenario.get('tracks'))
    loc = destination(HOME,0,210)
    local.drag(*loc)
    context.clock.advance(45)
    context.notify_clock(force=True)
    assert local.report(local.plan)['attention'].level >= 2
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield
        capture(window,shots,'计划卡片_飞行中',theme)
        capture(window,shots,'鼠标飞行',theme)
    window.resize(1280,800)
    yield
    assert window.size().toTuple()==(1280,800) and public.pilot.isVisible()
    window.resize(1440,900)
    clock_text = window.system_time_button.text()
    window.switch_role('duty')
    duty = window.situation.recorded
    assert window.system_time_button.text()==clock_text
    assert not window.situation.training.enabled
    assert duty.visible_now() and duty.right.currentWidget() is duty.compare
    report['值班对象到比对'] = 1
    duty.right.setCurrentWidget(duty.actions)
    with patch.object(QInputDialog,'getItem',return_value=('已联系并确认',True)):
        button(duty.actions,'记录联系飞手').click()
    button(duty.actions,'发送本机提醒').click()
    window.switch_role('public')
    assert window.current_page is public
    card = public.cards[0]
    button(card.messages,'正在处理').click()
    assert all(item.text()!='正在处理' or not item.isVisible() for item in card.messages.findChildren(type(card.primary)))
    local.drag(*destination(HOME,90,220))
    context.clock.advance(32)
    context.notify_clock(force=True)
    button(card,'结束飞行').click()
    finished = context.recorded.find(identifier)
    assert finished['ended_at'] and len(finished['actual_track']) >= 70
    window.switch_role('duty')
    duty.show(identifier)
    duty.right.setCurrentWidget(duty.compare)
    duty.seek(30)
    before = duty.session.report(finished,duty.cursor)
    duty.seek(5)
    duty.seek(30)
    assert before == duty.session.report(finished,duty.cursor)
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield
        capture(window,shots,'值班端_比对',theme)
    duty.return_now()
    duty.session.verify(finished,'轻微偏离已纠正','完成 P12 流程验收')
    window.switch_role('public')
    window.switch_page('my_plans')
    assert '已核对' in public.cards[0].flight_summary.text()
    assert any(row['kind']=='reminder_reply' for row in finished['flight_actions'])
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield
        capture(window,shots,'飞手端_核对回执',theme)
    archive = context.root.parent / '演示验收.zip'
    context.save_demo_archive(archive)
    previous = context.clock.now()
    context.clock.advance(600)
    context.load_demo_archive(archive)
    assert context.clock.now()==previous and context.civic.plan_reviewed(identifier)
    for name, digest in real.items():
        assert sha256((context.root/name).read_bytes()).hexdigest()==digest
    assert len(list(shots.glob('*.png'))) >= 12
    report.update(双主题截图=12, 双端全流程=True, 演示存读档=True, 真实库哈希一致=True, passed=True)


def run(output, shots):
    output.mkdir(parents=True,exist_ok=True)
    shots.mkdir(parents=True,exist_ok=True)
    for name in ('SKYGUARD_AMAP_KEY','SKYGUARD_AMAP_SECURITY_CODE'):
        os.environ.pop(name,None)
    app = QApplication([])
    window = MainWindow(output/'state')
    window.context.notice_lookup.search = lambda _: Scan()
    connect_test_map(window)
    window.show()
    report = {}
    flow = exercise(window,report,shots)
    waiting = None
    started = monotonic()

    def finish(code, error=''):
        if error:
            report.update(passed=False,error=error)
        (output/'验收.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        window.close()
        app.exit(code)

    def step():
        nonlocal waiting
        try:
            if monotonic()-started>90:
                raise TimeoutError('P12 流程验收超时')
            if waiting is None or waiting():
                waiting = next(flow)
        except StopIteration:
            finish(0)
            return
        except Exception as exc:
            traceback.print_exc()
            finish(1, str(exc) or type(exc).__name__)
            return
        QTimer.singleShot(220,step)
    QTimer.singleShot(0,step)
    return app.exec()


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    parser.add_argument('--shots',type=Path,required=True)
    options = parser.parse_args()
    raise SystemExit(run(options.output,options.shots))
