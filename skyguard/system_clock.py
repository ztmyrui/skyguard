"""Application time and the controllable, forward-only desktop demonstration."""
from datetime import datetime, timedelta, timezone
from time import monotonic
CHINA = timezone(timedelta(hours=8), 'CST')
SPEEDS = (1, 10, 60, 600)


def wall_now(tz=CHINA):
    """External protocols use machine time, including authentication tokens."""
    return datetime.now(tz)


class SystemClock:
    def __init__(self, *, real=wall_now, counter=monotonic):
        self._real = real
        self._counter = counter
        self._anchor = counter()
        self._at = None
        self.playing = False
        self.speed = 1

    @property
    def demo(self):
        return self._at is not None

    def now(self, tz=CHINA):
        if not self.demo:
            return self._real(tz)
        elapsed = self._counter() - self._anchor if self.playing else 0
        return (self._at + timedelta(seconds=max(0, elapsed) * self.speed)).astimezone(tz)

    def restore(self, when):
        """Load a dataset snapshot or switch clock mode."""
        if when is not None and when.utcoffset() is None:
            raise ValueError('请选择带时区的演示时间')
        self.playing = False
        self._at = when
        self._anchor = self._counter()

    def jump_to(self, when):
        current = self.now()
        if not self.demo or when < current:
            raise ValueError('请选择当前演示时间之后的时刻')
        self._at = when
        self._anchor = self._counter()
        return when

    def advance(self, seconds):
        current = self.now()
        if not self.demo or seconds < 0:
            raise ValueError('演示时间只能向前推进')
        self._at = current + timedelta(seconds=seconds)
        self._anchor = self._counter()
        return self._at

    def play(self, playing):
        if self.demo:
            self._at = self.now()
            self._anchor = self._counter()
            self.playing = bool(playing)

    def set_speed(self, speed):
        if speed not in SPEEDS:
            raise ValueError('请选择有效倍速')
        if self.demo:
            self._at = self.now()
            self._anchor = self._counter()
        self.speed = speed

    def next_event(self, dates):
        current = self.now()
        return min((event for event in dates if event > current), default=None)


clock = SystemClock()


def now(tz=CHINA):
    return clock.now(tz)
