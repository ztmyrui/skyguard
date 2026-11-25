"""Application services and background work shared by desktop pages."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from ..system_clock import clock, now as system_now
from ..demo_dataset import create_demo, load_demo, save_demo
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice, QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QMessageBox

from ..aerial import CHINA
from ..plan_watch import due_cities
from ..amap_adapter import CredentialStore, MapCredentials, MapServer
from ..aerial_scenery import ScenicLookup
from ..aerial_weather import WeatherLookup
from ..application import Service
from ..civic import CivicService
from ..flight_model import FlightModelConfig, FlightModelStore
from ..infrastructure import SQLiteRepository
from ..official_notices import OfficialNoticeLookup
from ..workflow import Workflow
from .app import asset_path, data_path


class Worker(QThread):
    """Run an operation on a background thread."""

    result = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        """Deliver the result or the operation's error."""
        try:
            self.result.emit(self.fn())
        except Exception as error:
            self.failed.emit(str(error))


class AppContext(QObject):
    """Own service adapters, credentials and shared desktop session state."""

    busy_changed = Signal(bool)
    selection_changed = Signal(str)
    status = Signal(str, int)
    observation_requested = Signal()
    import_requested = Signal()
    export_requested = Signal()
    civic_changed = Signal()
    notice_reminders = Signal(int)
    clock_changed = Signal()
    dataset_changed = Signal()

    def __init__(self, root=None, parent=None):
        super().__init__(parent)
        self.root = Path(root) if root is not None else data_path()
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_city = 'changsha'
        self.clock = clock
        self.clock.restore(None)
        self.demo_time = None
        self.demo_wind = None
        self.data_root = self.root
        self._open_services()
        self.notice_lookup = OfficialNoticeLookup()
        self.weather_lookup = WeatherLookup(self.root)
        self.scenic_lookup = ScenicLookup()
        from .widgets.workflow_session import WorkflowSession
        self.training = WorkflowSession(self)
        from .widgets.recorded_flight import RecordedFlight
        self.recorded = RecordedFlight(self, resume=False)
        self.flight_model_store = FlightModelStore(self.root)
        try:
            self.flight_model_config = self.flight_model_store.load()
        except (OSError, ValueError, TypeError, RuntimeError):
            self.flight_model_config = FlightModelConfig()
        self.map_store = CredentialStore(self.root)
        try:
            self.map_credentials = self.map_store.load()
        except Exception:
            self.map_credentials = MapCredentials()
        source = QFile(':/qtwebchannel/qwebchannel.js')
        source.open(QIODevice.ReadOnly)
        channel_script = bytes(source.readAll())
        source.close()
        self.map_server = MapServer(asset_path(), self.map_credentials, channel_script)
        self._worker = None
        self.flight_workers = set()
        self.aerial_workers = set()
        self._watch_busy = False
        self._watch_revision = 0
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self.poll_notices)
        self.schedule_notices()
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(250)
        self.clock_timer.timeout.connect(self.notify_clock)
        self.clock_timer.start()
        self._last_clock = None

    def _open_services(self):
        self.civic = CivicService(SQLiteRepository(self.data_root / 'civic.sqlite3'))
        self.service = Service(SQLiteRepository(self.data_root / 'business.sqlite3'),
                               Workflow(self.data_root / 'checkpoints.sqlite3'), self.civic)
        self.selected = next((view['id'] for view in self.service.views()), '')

    def _check_switch(self):
        if self.busy or self.flight_workers or self.aerial_workers or self.training.state or self.recorded.state:
            raise ValueError('请先完成当前飞行或后台查询')

    def _switch_data(self):
        self.public_city = 'changsha'
        self._open_services()
        self.training.open_dataset()
        self.schedule_notices()
        self.dataset_changed.emit()
        self.notify_clock(force=True)

    def use_demo(self, enabled):
        if self.clock.demo == enabled:
            return
        self._check_switch()
        if enabled:
            self.data_root = create_demo(self.root)
            self.clock.restore(self.demo_time or system_now().replace(microsecond=0))
        else:
            self.demo_time = system_now()
            self.data_root = self.root
            self.clock.restore(None)
        self._switch_data()

    def save_demo_archive(self, path):
        if not self.clock.demo:
            raise ValueError('请先切换到演示时间')
        if self.busy or self.flight_workers or self.aerial_workers:
            raise ValueError('请等待后台查询结束')
        save_demo(self.data_root, path, system_now())

    def load_demo_archive(self, path):
        self._check_switch()
        self.data_root, when = load_demo(self.root, path)
        self.clock.restore(when)
        self._switch_data()

    def notify_clock(self, force=False):
        instant = system_now().replace(microsecond=0)
        if force or instant != self._last_clock:
            self._last_clock = instant
            self.clock_changed.emit()

    def forecast_for(self, point, start, end, *, use_demo=None):
        from ..aerial import Forecast, WeatherHour, touched_hours
        from ..system_clock import wall_now
        is_demo = self.clock.demo if use_demo is None else use_demo
        bounds = tuple(touched_hours(start, end))
        if not is_demo:
            return self.weather_lookup.forecast(point)
        direction, speed = self.demo_wind or (0, 3)
        live = (self.weather_lookup.forecast(point) if self.demo_wind is None
                and bounds and bounds[-1] >= wall_now().replace(minute=0,second=0,microsecond=0)
                and self.weather_lookup.configured() else None)
        hours = {row.time:row for row in live.hours} if live else {}
        for hour in bounds:
            if hour not in hours or hour < wall_now().replace(minute=0,second=0,microsecond=0):
                hours[hour] = WeatherHour(hour, '演示天气 · 晴', speed, speed, 0, 0,
                                         15, .08, False, ((10,speed),(80,speed),(120,speed)),
                                         speed, wind_from_deg=direction)
        return Forecast(tuple(hours[hour] for hour in sorted(hours)), system_now())

    def set_speed(self, speed):
        if speed > 10 and self.recorded.state and self.recorded.control == '手动':
            raise ValueError('手动飞行最高 10×')
        self.clock.set_speed(speed)

    def cap_manual_speed(self):
        if self.recorded.state and self.recorded.control == '手动' and self.clock.speed > 10:
            self.clock.set_speed(10)

    def next_event(self):
        from ..flight_rules import instant
        times = [instant(p['query']['start']) for p in self.civic.data['plans']
                 if not p.get('cancelled') and not p.get('started_at') and p['query'].get('route')]
        times.extend(hint.at for hint in self.training.outlook)
        return self.clock.next_event(times)

    def schedule_notices(self):
        self._watch_revision += 1
        interval = self.civic.data['watch_minutes']
        if interval:
            self.watch_timer.start(interval * 60_000)
        else:
            self.watch_timer.stop()

    def poll_notices(self):
        now = system_now(CHINA)
        if self._watch_busy or not self.civic.data['watch_minutes']:
            return
        cities = sorted(due_cities(self.civic.data['plans'], now))
        if not cities:
            return
        self._watch_busy = True
        revision = self._watch_revision

        def search():
            with ThreadPoolExecutor(max_workers=min(5, len(cities))) as workers:
                return dict(zip(cities, workers.map(self.notice_lookup.search, cities)))

        def done(scans):
            self._watch_busy = False
            if not scans or revision != self._watch_revision:
                return
            count = self.civic.check_plan_notices(scans, system_now(CHINA))
            if count:
                self.status.emit(f'{count} 条飞行计划提醒', 6000)
                self.notice_reminders.emit(count)
                self.civic_changed.emit()

        self.run_flight_lookup(search, done, lambda: done({}))

    @property
    def busy(self):
        """Return whether a duty computation is in progress."""
        return self._worker is not None

    def selected_view(self, include_closed=False):
        """Return the current observation in the requested lifecycle scope."""
        return next((view for view in self.service.views(include_closed=include_closed, measure_impact=False)
                     if view['id'] == self.selected), None)

    def select(self, event_id):
        """Share the selected event with the active duty page."""
        self.selected = event_id
        self.selection_changed.emit(event_id)

    def safe(self, fn):
        """Run a UI action and show its error in the standard warning dialog."""
        try:
            return fn()
        except Exception as error:
            QMessageBox.warning(self.parent(), '操作未完成', str(error))
            return None

    def run_worker(self, fn, done):
        """Run a duty computation and publish the session busy state."""
        if self.busy:
            return
        worker = Worker(fn, self)
        self._worker = worker
        self.busy_changed.emit(True)
        self.status.emit('正在后台计算，可继续查看地图与已有数据…', 0)
        worker.result.connect(done, Qt.QueuedConnection)
        worker.failed.connect(self._worker_failed, Qt.QueuedConnection)
        worker.finished.connect(self._worker_finished, Qt.QueuedConnection)
        worker.start()

    def _worker_failed(self, message):
        QMessageBox.warning(self.parent(), '计算未完成', message)

    def _worker_finished(self):
        worker = self._worker
        self._worker = None
        worker.deleteLater()
        self.busy_changed.emit(False)
        self.status.emit('计算完成，结果已保存。', 8000)

    def run_flight_lookup(self, action, success, failure):
        """Run a flight lookup, preserving the limit of three concurrent requests."""
        self._lookup(self.flight_workers, 3, action, success, failure)

    def run_aerial_lookup(self, action, success, failure):
        self._lookup(self.aerial_workers, 7, action, success, failure)

    def _lookup(self, workers, limit, action, success, failure):
        if len(workers) >= limit:
            failure()
            return
        worker = Worker(action, self)
        workers.add(worker)

        def finished():
            workers.discard(worker)
            worker.deleteLater()

        worker.result.connect(success, Qt.QueuedConnection)
        worker.failed.connect(lambda _: failure(), Qt.QueuedConnection)
        worker.finished.connect(finished, Qt.QueuedConnection)
        worker.start()
