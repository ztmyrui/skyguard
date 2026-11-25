"""Desktop walkthrough for the public planner on the installed map provider."""
import json
import sys
from datetime import datetime,timedelta
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qa_recorded_hangzhou import wait_for
from qa_desktop import button
from skyguard.aerial import CHINA,Forecast,WeatherHour,touched_hours
from skyguard.diagnostics import connect_test_map
from skyguard.flight_route import timeline
from skyguard.official_notices import Scan
from skyguard.ui.shell import MainWindow

output=Path(sys.argv[1])
output.mkdir(parents=True,exist_ok=True)
app=QApplication([])
window=MainWindow(output/'profile')
public=window.flight_query
connect_test_map(window)
window.resize(1440,900)
window.show()
now=datetime(2026,11,23,13,tzinfo=CHINA)
public.finder.now=lambda:now
window.context.weather_lookup.forecast=lambda _:Forecast(tuple(
    WeatherHour(t,'晴',2,3,.03,0,12,.15) for t in touched_hours(now,now+timedelta(days=4))),now)
window.context.notice_lookup.search=lambda city: Scan((),at=now)
try:
    wait_for(lambda: window.map_view.is_ready,25)
    assert public.finder.search_button.isVisible() and public.parameter_panel.isHidden()
    public.finder.text.setText('梅溪湖湖面日落')
    public.finder.search_button.click()
    wait_for(lambda: public.preview_panel.isVisible() and public.active_proposal.score is not None,12)
    route=public.active_proposal.query.route
    assert route and route.complete_flight
    assert timeline(route)[0].phase=='起飞爬升' and timeline(route)[-1].phase=='降落'
    assert all('梅溪湖' in item.scene.name for item in public.finder.proposals)
    data=public.map_payload()['routes'][0]
    assert data['shots'] and any(item['phase']=='返航' for item in data['itinerary'])
    first=data['shots'][0]
    public.focus_shot(first)
    assert public.photo_preview.isVisible() and public.shot_highlight==first['id']
    def photo_overlay():
        completed=[]
        window.map_view.run_js(
            """JSON.stringify({visible:!!photoDot?.isConnected&&photoDot.lastElementChild instanceof SVGElement && photoDot.lastElementChild.getBoundingClientRect().width>0,
                camera:photoRay?.hidden===false, error:photoError, frame:!!photoFrame,
                x:photoDot?.offsetLeft,y:photoDot?.offsetTop})""",completed.append)
        QTest.qWait(150)
        return json.loads(completed[-1]) if completed and completed[-1] else {}
    try:
        wait_for(lambda: (state:=photo_overlay()).get('visible') and
                 state.get('camera') and state.get('x',0)>0 and state.get('y',0)>0,12)
    except AssertionError:
        window.grab().save(str(output/'预演诊断.png'))
        print('预演状态',photo_overlay())
        raise
    for theme in ('klein','night'):
        window.set_theme(theme)
        QTest.qWait(500)
        assert public.photo_preview.isVisible()
        assert window.grab().save(str(output/f'推荐预演_{theme}.png'))
    button(public.preview_panel,'采用这条航线').click()
    public.confirm_button.click()
    assert public.parameter_panel.isVisible() and public.query().route.complete_flight
    public.set_time(now+timedelta(days=1))
    public.capture_subject=None
    public.score_button.click()
    wait_for(lambda: public.aerial.assessment is not None,8)
    assert public.aerial.quality.weather is not None and public.aerial.quality.framing is None
    button(public.aerial,'在地图上标出主体').click()
    location=(112.8957,28.1874)
    # Convert and submit a point through the actual AMap editor queue.
    received=[]
    window.map_view.bridge.route_batch.connect(lambda *args: received.append(args[1:]))
    window.map_view.run_js(f'SkyMapCore.convertBatch([{json.dumps(location)}],convert,cache).then(([p])=>routeEdit("add",-1,{{lng:p[0],lat:p[1]}}))')
    try:
        wait_for(lambda: public.aerial.score is not None,20)
    except AssertionError:
        debug=[]
        window.map_view.run_js("""JSON.stringify({subject:payload?.subject_pick,editing:payload?.route_editing,
            scope:editor.scope,busy:editor.busy,failed:editor.failed,queue:editor.queue.length})""",debug.append)
        QTest.qWait(200)
        print('地图主体选择',debug,received,public.subject_pick,public.capture_subject,
              public._preview_point,public.aerial.quality,window.map_view.note.text())
        window.grab().save(str(output/'评分诊断.png'))
        raise
    assert public.capture_subject and not public.subject_pick
    assert abs(public.capture_subject[0]-location[0])<.000003
    assert abs(public.capture_subject[1]-location[1])<.000003
    assert received and [row[0] for row in json.loads(received[-1][-1])]==['subject']
    window.resize(1280,800)
    QTest.qWait(250)
    assert window.size().toTuple()==(1280,800)
    assert window.grab().save(str(output/'自画评分.png'))
    result={'passed':True,'live_map':True,'complete_route':True,'shared_scoring':True,
            'map_aircraft_and_camera':True,'forecast_fixture':True}
    (output/'公众航拍验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))
finally:
    window.close()
