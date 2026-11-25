"""Verify both catalog and POI plans over the installed AMap connection."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_desktop import button, map_painted
from qa_recorded_hangzhou import wait_for
from skyguard.aerial import CHINA, Forecast, WeatherHour, touched_hours
from skyguard.diagnostics import connect_test_map
from skyguard.official_notices import Scan
from skyguard.ui.shell import MainWindow
from skyguard.ui.widgets.common import SegmentedControl


def run(output):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    window = MainWindow(output / 'profile')
    public, map_view = window.flight_query, window.map_view
    connect_test_map(window)
    window.resize(1440, 900)
    window.show()
    now = datetime(2026, 11, 23, 13, tzinfo=CHINA)
    public.finder.now = lambda: now
    weather = Forecast(tuple(WeatherHour(t, '晴', 2, 3, .03, 0, 12, .15)
                             for t in touched_hours(now, now + timedelta(days=7))), now)
    window.context.weather_lookup.forecast = lambda _: weather
    window.context.notice_lookup.search = lambda _: Scan((), at=now)
    result = {'passed': False, 'weather_fixture': True, 'notices_fixture': True}

    def markers_rendered(expected):
        labels = []
        map_view.run_js('JSON.stringify({labels: Array.from(document.querySelectorAll(".photo-shot"), item => item.textContent),'
                        ' running, waiting: !!pending, drawn: routeFocus, key: payload?.routes?.[0]?.key})', labels.append)
        QTest.qWait(100)
        if labels and labels[-1]:
            result['map_markers'] = json.loads(labels[-1])
        return any(expected in text for text in result.get('map_markers', {}).get('labels', ()))

    try:
        wait_for(lambda: map_view.is_ready and map_painted(window), 25)
        public.finder.text.setText('梅溪湖日落大片')
        public.finder.search_button.click()
        def catalog_with_variants():
            return next(((proposal, item) for proposal in public.finder.proposals for item in proposal.windows
                         if item.score is not None and len(item.variants) == 3), None)

        wait_for(catalog_with_variants, 35)
        public.preview_proposal(*catalog_with_variants())
        QTest.qWait(100)
        original = public.active_proposal.query.route
        body = public.preview_panel.body
        control = next(widget for i in range(body.count()) if isinstance(widget := body.itemAt(i).widget(), SegmentedControl))
        control.group.button(1).click()
        assert public.active_proposal.style_key == 'panoramic' and public.active_proposal.query.route != original
        assert public.map_payload()['routes'][0]['waypoints'] != [[w.lon, w.lat, w.altitude_m] for w in original.waypoints]
        public.open_photo_preview()
        first_shot = public.mission(public.active_proposal.query.route)['shots'][0]
        public.photo_preview.seek.setValue(round(first_shot['seconds']*10)+5)
        assert '俯角' in public.photo_preview.detail.text()
        wait_for(lambda: markers_rendered('场地全景'), 18)
        assert window.grab().save(str(output / '01-梅溪湖-全景预演.png'))
        result['catalog_variants'] = True

        window.resize(1280, 800)
        responses = []
        map_view.place_resolved.connect(lambda revision, payload: responses.append((revision, json.loads(payload))))
        for place in ('谢子龙影像艺术馆', '洋湖湿地公园'):
            public.finder.text.setText(place)
            public.finder.search_button.click()
            try:
                wait_for(lambda: public.active_proposal is not None and public.active_proposal.source_kind == 'poi'
                         and public.active_proposal.query.route is not None and public.active_proposal.score is not None, 14)
            except AssertionError:
                result.setdefault('attempts', []).append({'place': place, 'revision': public.finder.token,
                    'message': public.finder.message.text(), 'awaiting_place': public.finder._pending_place is not None,
                    'active_source': public.active_proposal.source_kind if public.active_proposal else None,
                    'responses': [(revision, [row['name'] for row in rows]) for revision, rows in responses],
                    'proposals': [{'name': plan.scene.name, 'source': plan.windows[0].source_kind,
                                   'route': bool(plan.windows[0].query.route)} for plan in public.finder.proposals]})
                continue
            result['poi'] = public._preview_subject
            result['poi_variants'] = [item.key for item in public.active_proposal.variants]
            wait_for(lambda: markers_rendered('细节推进'), 25)
            assert window.grab().save(str(output / '02-高德地标推演.png'))
            break
        result['poi_response_counts'] = [len(rows) for _, rows in responses]
        assert 'poi' in result, '高德地标未返回可展示的航线'
        result['passed'] = True
    finally:
        (output / '方案B桌面验收.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        window.close()


if __name__ == '__main__':
    run(Path(sys.argv[1]))
