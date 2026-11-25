"""Exercise the public camera plan, rehearsal, media review and export."""
import json
import sys
from unittest.mock import patch
from pathlib import Path
from datetime import datetime, timedelta

from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_desktop import map_painted
from skyguard.aerial import CHINA
from skyguard.flight_check import Verdict
from skyguard.flight_rules import FlightQuery
from skyguard.flight_route import FlightRoute, Waypoint
from skyguard.photo_mission import mission_for
from skyguard.diagnostics import connect_test_map
from skyguard.ui.shell import MainWindow
from skyguard.ui.widgets.task_card import write_task_card

output = Path(sys.argv[1])
output.mkdir(parents=True, exist_ok=True)
app = QApplication([])
window = MainWindow(output/'profile')
connect_test_map(window)
window.resize(1440,900)
window.show()
for index in range(280):
    if window.map_view.is_ready:
        break
    QTest.qWait(100)
assert window.map_view.is_ready

public = window.flight_query
now = datetime(2026, 11, 23, 16, tzinfo=CHINA)
takeoff = (112.949, 28.180)
path = (Waypoint(112.9495,28.1802,38,6,270), Waypoint(112.9505,28.1803,41,6,310),
        Waypoint(*takeoff,38,0,280))
route = FlightRoute(path, 3, takeoff, now, '轻型')
query = FlightQuery((),'route',25,route.start,route.end,0,route.category,demo=False,route=route)
mission = mission_for(route,'repeat','滨江项目','7 天')
civic = window.context.civic
identifier = civic.save_plan('changsha','滨江项目 · 固定机位',query,'空域边界待核对',
                             verdict=Verdict.CHECK.value,mission=mission)
def plan():
    return next(row for row in civic.data['plans'] if row['id']==identifier)

public.load_plan(plan(),recheck=False)
assert public.mission_kind.currentData() == 'repeat'
public.open_photo_preview()
QTest.qWait(300)
assert not public.photo_preview.isHidden()
assert public.photo_preview.height() > 80
assert not window.map_view.geometry().intersects(public.photo_preview.rect().translated(
    public.photo_preview.mapTo(window.stack, public.photo_preview.rect().topLeft())))
preview_title = public.photo_preview.detail.text()
public.photo_preview._jump(1)
assert public.photo_preview.detail.text() != preview_title
for theme in ('klein','night'):
    window.set_theme(theme)
    for attempt in range(60):
        QTest.qWait(100)
        if map_painted(window, theme):
            break
    assert map_painted(window, theme)
    completed = []
    for _ in range(80):
        public.sync_photo_frame()
        window.map_view.run_js('typeof photoDot !== "undefined" && !!photoDot', completed.append)
        QTest.qWait(200)
        if completed and completed[-1]:
            break
    assert completed and completed[-1]
    assert window.grab().save(str(output/f'航线预演_{theme}.png'))
public.close_photo_preview()

window.switch_page('my_plans')
window.my_plans.open_results(plan())
results = window.my_plans.results
assert results.mission['kind']=='repeat' and not results.retake_full.isHidden()
media = output/'DJI_20261123_160005.png'
image = QImage(640,360,QImage.Format_RGB32)
image.fill(QColor('#315e89'))
assert image.save(str(media))
assert results.import_files([str(media)]) == 1
assert len(plan()['mission']['materials']) == 1
results.shots.setCurrentRow(0)
results.link.setCurrentIndex(1)
results._associate(1)
assert plan()['mission']['materials'][0]['shot'] == 'repeat-1'
results.shots.setCurrentRow(2)
assert '\u5df2\u62cd' in results.progress.text()
for theme in ('klein','night'):
    window.set_theme(theme)
    QTest.qWait(300)
    assert window.grab().save(str(output/f'实拍成果_{theme}.png'))
write_task_card(output/'任务卡.png',plan(),'长沙')
assert QImage(str(output/'任务卡.png')).width()==1000
results.shots.setCurrentRow(0)
copied = results.folder / plan()['mission']['materials'][0]['file']
assert copied.is_file()
with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
    results.remove_file.click()
assert not plan()['mission']['materials'] and not copied.exists() and media.is_file()
results.shots.setCurrentRow(2)
results._supplement()
assert window.current_page is public and public.query().route
assert public.editing is None and len(public.query().route.waypoints)==2
assert public.previous_mission['previous_plan']==identifier
assert public.previous_mission['materials']==[]
window.resize(1280,800)
QTest.qWait(200)
assert window.size().toTuple()==(1280,800)
report={'passed': True, 'map_rehearsal': True, 'media_persistent': True,
        'png_task_card': True, 'material_copy_removed': True, 'supplement_route': True, 'themes': ['klein','night']}
(output/'流程验收.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
window.close()
