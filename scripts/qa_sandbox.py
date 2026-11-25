"""Capture the sandbox on a configured online AMap desktop."""
import argparse
from dataclasses import replace
from pathlib import Path
import sys
import time
import traceback

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qa_desktop import map_painted
from skyguard.diagnostics import connect_test_map
from skyguard.sandbox import SandboxStaff, offset
from skyguard.ui.shell import MainWindow


def run(shots, work):
    app = QApplication([])
    window = MainWindow(work / 'isolated-data')
    connect_test_map(window)
    window.resize(1440, 900)
    window.show()
    window.switch_page('sandbox')
    page = window.sandbox
    page.load_example()
    started = time.monotonic()
    finished = False

    def capture(name, popup=None):
        pixmap = window.grab()
        if popup:
            assert popup.isVisible()
            painter = QPainter(pixmap)
            painter.drawPixmap(window.mapFromGlobal(popup.mapToGlobal(QPoint())), popup.grab())
            painter.end()
        assert pixmap.deviceIndependentSize().toSize().toTuple() == (1440, 900)
        assert map_painted(window)
        assert pixmap.scaled(1440, 900, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).save(str(shots / f'{name}.png'))
        print(name, flush=True)

    def steps():
        yield "ready && overlays.length === 5 && payload?.sandbox_edit?.step === 0"
        search = next(widget for widget in page.scroll.widget().findChildren(QLineEdit) if widget.accessibleName() == '地标名称')
        search.setText('湖南大学')
        page._edit_search(search.text())
        page._locate(search.text())
        yield "ready && !!sandboxPin && Math.abs(map.getZoom()-15.5)<.1"
        page._locate('', nearby=True)
        yield "true"
        while page.search_status == '正在搜索…':
            yield "true"
        page._edit_site(template='park')
        page._render_step()
        combo = next(widget for widget in page.scroll.widget().findChildren(QComboBox) if widget.count() == 3)
        page.scroll.ensureWidgetVisible(combo, 5, 30)
        combo.showPopup()
        yield "true"
        capture('01_step1_search_sites_clean_combo', popup=combo.view().window())
        combo.hidePopup()
        page.set_step(1)
        page._select_drone(1)
        page._center_scene()
        yield "ready && overlays.length >= 18 && payload?.sandbox_edit?.step === 1"
        capture('02_step2_varied_drones_triggers')
        page.set_step(2)
        page._update_config(staff=(SandboxStaff('P01', '巡查员 1', 'foot', *offset(page.config.sites[0].point, 125, -105)),),
                            strategy=replace(page.config.strategy, name='步行巡查', allow_reassign=False, pre_alert=False))
        page.save_snapshot()
        page._suggest()
        page._strategy(name='协同值守', allow_reassign=True, pre_alert=True)
        page.save_snapshot()
        yield "ready && overlays.length >= 8 && payload?.sandbox_edit?.step === 2"
        print(f'道路缓存可用路径数 {sum(bool(path) for path in page.road_cache.values())}', flush=True)
        page._set_tool(('staff', 'vehicle'))
        page.scroll.verticalScrollBar().setValue(0)
        yield "true"
        capture('03_step3_role_tools_snapshots')
        page.set_step(3)
        assert page.speed.currentText() == '2×' and page.timer.isActive()
        page.cursor.setValue(19)
        page._inject('airdrop')
        page._inject('blocked')
        yield "ready && !calibrating && !!sceneWarp && payload?.staff_markers?.length >= 3 && !!payload?.aircraft?.length"
        capture('04_step4_live_light_c')
        page._solidify()
        page._show_review(True)
        page._compare()
        assert all(row.avg_arrival_s is not None and row.avg_arrival_s > 0 for row in page.results)
        window.resize(1280, 800)
        yield "true"
        assert page.scroll.width() >= 340
        assert page.step_buttons[-1].isVisible()
        window.resize(1440, 900)
        yield "true"
        capture('05_step4_realistic_compare')

    pending = steps()

    def finish(code):
        nonlocal finished
        if not finished:
            finished = True
            window.close()
            app.exit(code)

    def advance():
        try:
            condition = next(pending)
        except StopIteration:
            finish(0)
        except Exception:
            traceback.print_exc()
            finish(1)
        else:
            def poll():
                if finished:
                    return
                if time.monotonic() - started > 125:
                    print(f'Timed out waiting for {condition}', flush=True)
                    print(f'Search status {page.search_status!r}, candidates {len(page.place_rows)}', flush=True)
                    window.map_view.run_js('JSON.stringify({ready,zoom:map?.getZoom(),pin:!!sandboxPin,overlays:overlays.length})',
                                           lambda state: (print(state, flush=True), finish(1)))
                    return
                window.map_view.run_js(condition, lambda ok: QTimer.singleShot(
                    600 if ok else 350, advance if ok else poll))
            QTimer.singleShot(850, poll)

    QTimer.singleShot(200, advance)
    QTimer.singleShot(130000, lambda: finish(1))
    return app.exec()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('shots', type=Path)
    parser.add_argument('work', type=Path)
    args = parser.parse_args()
    args.shots.mkdir(parents=True, exist_ok=True)
    raise SystemExit(run(args.shots, args.work))
