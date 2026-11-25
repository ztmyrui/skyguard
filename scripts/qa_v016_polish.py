"""Capture the v0.16.0 polish on a real AMap desktop."""
import argparse
from pathlib import Path
import sys
import time
import traceback

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QTableWidget

from qa_desktop import map_painted
from skyguard.diagnostics import connect_test_map
from skyguard.sandbox import WEATHERS
from skyguard.ui.shell import MainWindow
from skyguard.ui.widgets.help_dialog import HelpDialog


def run(shots, work):
    app = QApplication([])
    window = MainWindow(work / 'isolated-data')
    connect_test_map(window)
    window.set_theme('klein')
    window.resize(1440, 900)
    window.show()
    view = window.map_view
    sandbox = window.sandbox
    guide = None

    def capture(widget, name, directory=shots):
        pixmap = widget.grab()
        size = pixmap.deviceIndependentSize().toSize()
        assert pixmap.scaled(size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).save(str(directory / f'{name}.png'))
        print(name, flush=True)

    def checks():
        nonlocal guide
        yield lambda: view.is_ready and map_painted(window)
        assert view.configure_button.isHidden() and view.note.isHidden()
        capture(window, '01_public_flight_clean')
        window.switch_role('duty')
        view.legend_button.click()
        panel = window.situation.training
        panel.scenes.setCurrentIndex(2)
        panel.session.seek_civic(35)
        yield "ready && payload?.city==='shanghai' && !!sceneWarp && !calibrating"
        assert view.legend.isVisible() and panel.stream.count() > 0
        capture(window, '02_civic_situation_legend')
        window.set_theme('night')
        yield lambda: map_painted(window, 'night')
        capture(window, 'night_civic_legend', work)
        window.set_theme('klein')

        window.nav_buttons['sandbox'].click()
        sandbox.load_example()
        sandbox._update_config(weather=WEATHERS[2])
        sandbox.set_step(2)
        guard_index = next(i for i, staff in enumerate(sandbox.config.staff) if staff.role == 'guard')
        sandbox._select_staff(guard_index)
        guard = sandbox.config.staff[guard_index]
        view.focus_point(guard.point, 16.6)
        yield "ready && payload?.sandbox_edit?.step===2 && overlays.some(item=>item.getRadius?.()===127.5) && !running"
        capture(window, '03_sandbox_step3_coverage')

        sandbox._update_config(staff=(sandbox.config.staff[0],))
        sandbox._strategy(name='基础值守', allow_reassign=False, pre_alert=False)
        sandbox.save_snapshot()
        sandbox._suggest()
        sandbox._strategy(name='协同值守', allow_reassign=True, pre_alert=True)
        sandbox.save_snapshot()
        sandbox.set_step(3)
        sandbox._pause()
        sandbox.cursor.setValue(28)
        sandbox._show_review(True)
        assert len(sandbox.results) == 2
        yield "ready && payload?.training_scene==='sandbox-run' && !!sceneWarp && !calibrating"
        assert sandbox.scroll.widget().findChild(QTableWidget, 'sandboxComparison').isVisible()
        capture(window, '04_sandbox_step4_auto_compare')
        window.set_theme('night')
        window.resize(1280, 800)
        yield lambda: map_painted(window, 'night')
        assert window.size().toTuple() == (1280, 800)
        capture(window, 'night_sandbox_compare_1280', work)

        window.set_theme('klein')
        window.resize(1440, 900)
        window.switch_page('settings')
        yield lambda: window.settings.isVisible()
        capture(window, '05_settings_clean')
        guide = HelpDialog(window, 3)
        guide.show()
        yield guide.isVisible
        capture(guide, '06_help_guide')
        window.set_theme('night')
        yield guide.isVisible
        capture(guide, 'night_help_guide', work)
        guide.accept()

    steps = checks()
    started = time.monotonic()
    done = False

    def finish(code):
        nonlocal done
        if not done:
            done = True
            window.close()
            app.exit(code)

    def next_step():
        if done:
            return
        try:
            condition = next(steps)
        except StopIteration:
            finish(0)
            return
        except Exception:
            traceback.print_exc()
            finish(1)
            return

        def poll():
            if done:
                return
            if time.monotonic() - started > 110:
                print(f'Timed out waiting for {condition}', flush=True)
                finish(1)
            elif isinstance(condition, str):
                view.run_js(condition, check_result)
            else:
                check_result(condition())

        def check_result(ok):
            QTimer.singleShot(550 if ok else 290, next_step if ok else poll)

        QTimer.singleShot(500, poll)

    QTimer.singleShot(0, next_step)
    QTimer.singleShot(115000, lambda: finish(1))
    return app.exec()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('shots', type=Path)
    parser.add_argument('work', type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    args.shots.mkdir(parents=True, exist_ok=True)
    args.work.mkdir(parents=True, exist_ok=True)
    raise SystemExit(run(args.shots, args.work))
