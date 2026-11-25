"""Replay the public workflow reported in the November 23 recording."""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event
from time import monotonic

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_desktop import button
from skyguard.aerial import CHINA, Forecast
from skyguard.official_notices import Scan
from skyguard.diagnostics import connect_test_map
from skyguard.ui.shell import MainWindow


def wait_for(check, seconds):
    end = monotonic() + seconds
    while monotonic() < end:
        if check():
            return
        QTest.qWait(25)
    assert check()


def map_zoom(window):
    values = []
    window.map_view.run_js("typeof map !== 'undefined' && map ? map.getZoom() : null", values.append)
    wait_for(lambda: bool(values), 2)
    return values[0]


def replay(output):
    app = QApplication([])
    window = MainWindow(output/'profile')
    connect_test_map(window)
    window.resize(1280,800)
    window.show()
    allow_services = Event()
    now = datetime(2026,11,23,0,20,tzinfo=CHINA)

    def notices(city):
        allow_services.wait(12)
        return Scan((), at=now)

    window.context.notice_lookup.search = notices
    window.context.weather_lookup.forecast = lambda point: Forecast((),now)
    public = window.flight_query
    finder = public.finder
    try:
        wait_for(lambda: window.map_view.is_ready, 22)
        public.city.setCurrentIndex(public.city.findData('hangzhou'))
        finder.now = lambda: now
        finder.topics['商圈夜景'].setChecked(True)
        begin = monotonic()
        finder.search_button.click()
        wait_for(lambda: public.preview_panel.isVisible(), 2)
        preview_ms = round((monotonic() - begin) * 1000)
        assert not allow_services.is_set()
        assert public.active_proposal.query.start.hour in range(16,23)
        assert public.active_proposal.query.route
        view = public.active_proposal.query.route
        wait_for(lambda: map_zoom(window) >= 17, 12)
        button(public.preview_panel,'预演航线').click()
        assert not public.photo_preview.isHidden()
        public.photo_preview.play.click()
        wait_for(lambda: public.photo_preview.seek.value() > 0, 2)
        button(public.preview_panel,'采用这条航线').click()
        assert public.query().route == view
        assert public.editor_step == 2
        assert not public.photo_preview.isVisible()
        assert public.confirm_button.isVisible()
        assert public.route_editor.draft.isVisible()
        wait_for(lambda: map_zoom(window) >= 17, 12)
        public.confirm_button.click()
        assert public.check_button.isVisible() and not public.confirm_takeoff
        assert public.query().route.takeoff == view.takeoff
        public.route_editor.draft.click()
        assert public.route_editor.draft.text() == '草稿已保存'
        original_id = public.editing
        assert any(row['id']==original_id and row['draft'] for row in window.context.civic.data['plans'])
        allow_services.set()
        public.check_button.click()
        wait_for(lambda: any(row['id']==original_id and not row['draft']
                             for row in window.context.civic.data['plans']), 6)
        window.switch_page('my_plans')
        assert any(card.plan['id']==original_id for card in window.my_plans.cards)
        return {'passed':True,'live_map':True,'local_result_ms':preview_ms,
                'selected_hour':view.start.hour,'draft_to_saved_plan':True}
    finally:
        allow_services.set()
        window.close()


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    result = replay(args.output)
    (args.output/'录屏流程验收.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))
