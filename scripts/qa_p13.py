"""Verify public/duty separation and fixed P13 city cases through the desktop UI."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import os
import sys
import time
import traceback

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from unittest.mock import patch

from qa_desktop import button, capture, map_painted
from skyguard.official_notices import Scan
from skyguard.diagnostics import connect_test_map
from skyguard.ui.shell import MainWindow


def steps(window, shots, results):
    context=window.context
    window.resize(1440,900)
    yield lambda:window.map_view.is_ready
    assert not window.map_view.grid_check.isChecked()
    before=deepcopy(context.civic.data)
    window.switch_role('duty')
    assert window.current_page is window.situation
    panel=window.situation.training
    assert panel.enabled and not window.nav_buttons['tasks'].isVisible()
    model=context.training
    assert model.scenario['city']=='changsha'
    yield
    before_load=model.now
    model.clock.stop()
    panel.timeline.play.click()
    model.play_speed=4
    model.tick()
    assert model.now==before_load+4
    panel.timeline.play.click()
    assert not model.clock_running
    for name,city,second in (('长沙 · 湘江风光带','changsha',88),
                             ('广州 · 海心沙（平时）','guangzhou',50),
                             ('广州 · 海心沙（活动）','guangzhou',50),
                             ('上海 · 徐汇滨江','shanghai',59)):
        index=panel.scenes.findText(name)
        assert index>=0
        panel.scenes.setCurrentIndex(index)
        panel.scenes.activated.emit(index)
        assert model.scenario['city']==city
        assert window.situation.map_payload()['training_bounds']
        model.now=second
        model.return_now()
        snapshot=[(row['id'],row['attention'].level,row['attention'].score,row['quality'])
                  for row in model.objects()]
        panel.timeline.slider.setValue(10)
        panel.timeline.slider.setValue(second)
        assert snapshot==[(row['id'],row['attention'].level,row['attention'].score,row['quality'])
                           for row in model.objects()]
        assert panel._visible_rows==tuple(row[0] for row in snapshot)
        selected='T01' if city=='shanghai' else snapshot[0][0]
        panel.select_object(selected)
        panel.right.setCurrentIndex(0)
        for theme in ('klein','night'):
            window.set_theme(theme)
            yield lambda:map_painted(window)
            yield
            capture(window,shots,f'演练_{name}',theme)
        results[name]=snapshot
    assert results['广州 · 海心沙（活动）'][0][1] > results['广州 · 海心沙（平时）'][0][1]
    assert any(row[-1]=='观测中断' for row in results['上海 · 徐汇滨江'])
    model.load_example('长沙 · 湘江风光带')
    model.now=88
    model.return_now()
    panel.select_object('T02')
    panel.right.setCurrentWidget(panel.workflow.card)
    with patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
        button(panel.workflow.card,'安排观察岗核验').click()
    assert any(row['kind']=='dispatch' for row in model.actions)
    model.close_event('轻微偏离已纠正')
    assert model.is_closed('T02')
    panel.right.setCurrentWidget(panel.workflow.review)
    yield
    capture(window,shots,'演练_处置与复盘',window.theme_name)
    assert context.civic.data==before
    window.resize(1280,800)
    yield
    assert window.size().toTuple()==(1280,800) and panel.timeline.slider.isVisible()
    window.switch_role('public')
    assert window.current_page is window.flight_query and context.public_city=='changsha'
    assert context.civic.data==before
    results.update(independent_data=True,deterministic_sort=True,disposition_and_review=True,passed=True)


def run(output,shots):
    output.mkdir(parents=True,exist_ok=True)
    shots.mkdir(parents=True,exist_ok=True)
    for key in ('SKYGUARD_AMAP_KEY','SKYGUARD_AMAP_SECURITY_CODE'):
        os.environ.pop(key,None)
    app=QApplication([])
    window=MainWindow(output/'state')
    window.context.notice_lookup.search=lambda _:Scan()
    connect_test_map(window)
    window.show()
    results={}
    flow=steps(window,shots,results)
    waiting=None
    started=time.monotonic()

    def finish(code,error=None):
        if error:
            results.update(passed=False,error=error)
        (output/'P13验收.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
        window.close()
        app.exit(code)

    def step():
        nonlocal waiting
        try:
            if time.monotonic()-started>60:
                raise TimeoutError('P13 桌面验收超时')
            if waiting is None or waiting():
                waiting=next(flow)
        except StopIteration:
            finish(0)
            return
        except Exception as error:
            traceback.print_exc()
            finish(1,str(error) or type(error).__name__)
            return
        QTimer.singleShot(260,step)
    QTimer.singleShot(0,step)
    return app.exec()


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('output',type=Path)
    cli.add_argument('--shots',type=Path,required=True)
    args=cli.parse_args()
    sys.exit(run(args.output,args.shots))
