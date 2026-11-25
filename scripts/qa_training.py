"""Offline P10 and P11 desktop scenarios on a new local data directory."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import time
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qa_desktop import capture, map_painted
from skyguard.aerial import CHINA
from skyguard.flight_route import destination, tracking
from skyguard.flight_rules import FlightQuery
from skyguard.training_tracks import HOME
from skyguard.diagnostics import connect_test_map
from skyguard.ui.shell import MainWindow


def map_visible(window):
    rect=window.map_view.web.rect()
    return window.map_view.is_ready and window.map_view.web.isVisible() and rect.width()>200 and rect.height()>200


def p10(window, shots, result):
    from skyguard.ui.widgets.common import ActionButton
    context=window.context
    context.training.clock.stop()
    window.resize(1440,900)
    yield lambda:window.map_view.is_ready
    now=datetime.now(CHINA)
    start=now-timedelta(seconds=min(2,now.second))
    path=tracking(HOME,90,750,70,start=start,category='轻型',speed=4)
    query=FlightQuery((),'route',20,start,path.end,70,'轻型',demo=False,route=path)
    plan_id=context.civic.save_plan('changsha','培训 · 河岸航线',query,'待核对',verdict='check')
    context.civic.public_filing(plan_id)
    context.civic.transition_plan(plan_id,'start',now)
    window.switch_page('my_plans')
    yield
    card=window.my_plans.cards[0]
    assert card.simulate.isVisible(), card.stage.accessibleName()
    card.simulate.click()
    sim=context.training
    assert sim.track['source']=='公众模拟飞行'
    sim.command('航线')
    sim.sim_speed=10
    sim.tick()
    sim.drag(*destination(HOME,0,300))
    sim.altitude(65)
    for _ in range(4):
        sim.tick()
    assert sim.now==50 and sim.state.height>0
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield lambda:map_visible(window)
        yield
        capture(window,shots,'飞手端_模拟飞行',theme)
    window.switch_role('duty')
    window.situation.open_training()
    session=window.situation.training
    session.right.setCurrentIndex(0)
    objects=sim.objects()
    assert objects and objects[0]['dev'] and objects[0]['dev']['level'] in ('明显','偏向敏感区','轻微')
    assert session.pilot.isVisible()
    before=sim.now
    sim.tick()
    assert sim.now>before
    result['飞手偏离与后台运行']=True
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield lambda:map_visible(window)
        yield
        capture(window,shots,'态势_实时研判',theme)
    sim.seek(30)
    assert sim.mode=='回看'
    key=sim.objects()
    sim.seek(5)
    sim.seek(30)
    assert sim.objects()==key
    window.resize(1280,800)
    yield
    assert session.timeline.slider.isVisible() and session.timeline.width()<=window.width()
    window.resize(1440,900)
    yield
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield lambda:map_visible(window)
        yield
        capture(window,shots,'态势_回看',theme)
    result['时间轴定位确定性']=True
    window.switch_page('settings')
    window.settings.management.setVisible(True)
    window.resources.tabs.setCurrentIndex(4)
    assert window.resources.zone_types.rowCount()==12
    for theme in ('klein','night'):
        window.set_theme(theme)
        yield
        capture(window,shots,'管理_区域类型',theme)
    result['双主题截图']=8
    result['passed']=True


def run(output,shots,phase):
    output.mkdir(parents=True,exist_ok=True)
    shots.mkdir(parents=True,exist_ok=True)
    for name in ('SKYGUARD_AMAP_KEY','SKYGUARD_AMAP_SECURITY_CODE'):
        os.environ.pop(name,None)
    app=QApplication([])
    window=MainWindow(output/'state')
    connect_test_map(window)
    window.show()
    if phase==11:
        from qa_workflow import p11
        steps=p11
    else:
        steps=p10
    result={}
    flow=steps(window,shots,result)
    wait=None
    started=time.monotonic()

    def finish(code,error=''):
        if error:
            result.update(passed=False,error=error)
        (output/'验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        window.close()
        app.exit(code)

    def resume():
        nonlocal wait
        try:
            if time.monotonic()-started>90:
                raise TimeoutError('阶段验收超时')
            if wait is None or wait():
                wait=next(flow)
        except StopIteration:
            finish(0)
            return
        except Exception as error:
            traceback.print_exc()
            finish(1,str(error) or type(error).__name__)
            return
        QTimer.singleShot(250,resume)
    QTimer.singleShot(0,resume)
    return app.exec()


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('output',type=Path)
    cli.add_argument('--shots',required=True,type=Path)
    cli.add_argument('--phase',type=int,choices=(10,11),default=10)
    args=cli.parse_args()
    raise SystemExit(run(args.output,args.shots,args.phase))
