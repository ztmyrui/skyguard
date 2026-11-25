"""Capture the three civilian exercises over the live AMap desktop basemap."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_desktop import map_painted
from qa_recorded_hangzhou import wait_for
from skyguard.diagnostics import connect_test_map
from skyguard.training_city_cases import CIVIC_CASES
from skyguard.ui.shell import MainWindow


def run(output, profile):
    app = QApplication([])
    window = MainWindow(profile)
    window.resize(1440, 900)
    window.show()
    panel = window.situation.training
    session = panel.session
    session.clock.stop()
    result = {'passed': False, 'frames': {}}

    def js(expression):
        values = []
        window.map_view.run_js(expression, values.append)
        wait_for(lambda: bool(values), 5)
        return values[0]

    def capture(name, second, *, select='', zoom=None):
        panel.select_object(select)
        session.seek_civic(second)
        city = session.scenario['city']
        def ready():
            return (js(f"payload?.city === '{city}' && payload?.civic_replay && !!sceneWarp && !calibrating")
                    and map_painted(window))
        try:
            wait_for(ready, 35)
        except AssertionError:
            (output / 'work').mkdir(exist_ok=True)
            window.grab().save(str(output / 'work' / 'failure.png'))
            detail = js("JSON.stringify({city:payload?.city,scene:payload?.training_scene,warp:!!sceneWarp,calibrating})")
            raise RuntimeError(f'地图验收未就绪 {detail}') from None
        if zoom:
            js(f'map.setZoomAndCenter(17.6, sceneWarp({json.dumps(zoom)}), true); true')
            QTest.qWait(500)
            wait_for(lambda: map_painted(window), 20)
        QTest.qWait(600)
        assert not window.situation.left_stack.isVisible() and panel.right.isVisible()
        assert window.grab().save(str(output / f'{name}.png'))
        layers = session.map_layers()
        result['frames'][name] = {'city': city, 'second': second, 'active_sites': [
            area['name'] for area in layers['active_highlights']], 'staff': layers['staff_markers'][0]}

    try:
        connect_test_map(window)
        wait_for(lambda: window.map_view.is_ready, 45)
        window.open_workspace('situation')
        assert panel.scenes.count() == len(CIVIC_CASES)
        capture('01-changsha-calm', 0)
        capture('02-changsha-patrol', 33, select='T04')
        capture('03-changsha-lawn', 87, select='T03')
        panel.scenes.setCurrentIndex(1)
        capture('04-guangzhou-show', 10, select='T02')
        capture('05-guangzhou-school', 54, select='T04')
        capture('06-guangzhou-lawn', 85, select='T05')
        panel.scenes.setCurrentIndex(2)
        capture('07-shanghai-turn', 33, select='T04')
        capture('08-shanghai-shadow', 39, select='T02')
        capture('09-shanghai-lawn', 74, select='T05')
        panel.modes.buttons()[1].click()
        panel.restart()
        session.seek_civic(18)
        next(b for b in panel.focus_cards.findChildren(type(panel.modes.buttons()[0]))
             if b.text() == '同意执行').click()
        capture('10-shanghai-confirm', 26)
        next(b for b in panel.focus_cards.findChildren(type(panel.modes.buttons()[0]))
             if b.text() == '同意改派').click()
        window.resize(1280, 800)
        QTest.qWait(200)
        assert window.size().toTuple() == (1280, 800)
        capture('11-shanghai-confirmed-1280', 34)
        for index, name in enumerate(('changsha', 'guangzhou', 'shanghai')):
            panel.scenes.setCurrentIndex(index)
            point = session.civic_replay.config['sites']['lawn'][1]
            capture(f'{12 + index:02d}-{name}-lawn-detail', (98, 100, 96)[index], zoom=point)
        result['passed'] = True
    except Exception as exc:
        result['error'] = str(exc)
        raise
    finally:
        (output / '验收.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        window.close()
        app.processEvents()


if __name__ == '__main__':
    output = Path(sys.argv[1])
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='skyguard-situation-') as profile:
        run(output, Path(profile))
