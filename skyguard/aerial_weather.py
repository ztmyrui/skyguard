"""Read local JWT credentials and adapt two hourly forecast sources."""
import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from .system_clock import now as system_now, wall_now
import json
from math import isfinite
import re
from threading import Lock
from time import monotonic

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import httpx

from .aerial import CHINA, Forecast, WeatherHour
from .amap_adapter import _dpapi


UPPER_SOURCE = 'https://api.open-meteo.com/v1/forecast'
WIND_HEIGHTS = (10, 80, 120, 180)
AIRCRAFT_PROFILES = {
    '': (None, ''),
    'DJI Mini 4 Pro': (8.0, 'https://www.dji.com/mini-4-pro/specs'),
    'DJI Air 3S': (9.0, 'https://www.dji.com/support/product/air-3s'),
}


def _encoded(document):
    return base64.urlsafe_b64encode(document).rstrip(b'=')


def _jwt(data, key):
    def encoded_json(fields):
        return _encoded(json.dumps(fields, separators=(',', ':')).encode())

    issued = int(wall_now().timestamp()) - 30
    head = encoded_json({'alg': 'EdDSA', 'kid': data['kid']})
    body = encoded_json({'iss': data['iss'], 'sub': data['sub'], 'iat': issued, 'exp': issued + 600})
    content = head + b'.' + body
    return (content + b'.' + _encoded(key.sign(content))).decode('ascii')


def _number(value, lower=0, upper=float('inf')):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) and lower <= value <= upper else None


def _measure(container, allowed):
    if not isinstance(container, dict) or container.get('unit') not in allowed:
        return None
    number = _number(container.get('value'))
    return None if number is None else number * allowed[container['unit']]


def _temperature(value):
    if isinstance(value, dict):
        value = value.get('value') if value.get('unit') in ('°C', 'C', 'celsius', 'degC') else None
    return _number(value, -100, 70)


def qweather_hours(document):
    speeds = {'m/s': 1, 'km/h': 1 / 3.6}
    hours = {}
    for row in document.get('hours', []):
        try:
            time = datetime.fromisoformat(row['forecastTime'].replace('Z', '+00:00'))
            if time.utcoffset() is None:
                continue
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
        time = time.astimezone(CHINA)
        condition = row.get('condition') or {}
        rain = row.get('precipitation') or {}
        text = str(condition.get('text', ''))[:20]
        code = str(condition.get('code', ''))
        hours[time] = WeatherHour(time, text, _measure((row.get('wind') or {}).get('speed'), speeds),
                                  _measure(row.get('windGust'), speeds), _number(rain.get('probability'), 0, 1),
                                  _measure(rain.get('amount'), {'mm': 1}),
                                  _measure(row.get('visibility'), {'m': .001, 'km': 1}),
                                  _number(row.get('cloudCover'), 0, 1),
                                  code in {'302', '303', '304'} or '雷' in text,
                                  temperature=_temperature(row.get('temperature')),
                                  wind_from_deg=_number(((row.get('wind') or {}).get('direction') or {}).get('degree'),0,360))
    return hours


class WeatherLookup:
    def __init__(self, root, transport=None):
        self.directory = root / 'qweather'
        self.transport = transport
        self._cache = {}
        self._lock = Lock()

    def configured(self):
        return all((self.directory / filename).is_file() for filename in
                   ('jwt-configuration.dpapi', 'ed25519-private.dpapi'))

    def _credential(self):
        config = json.loads(_dpapi((self.directory / 'jwt-configuration.dpapi').read_bytes(), False))
        if not re.fullmatch(r'[a-z0-9.-]+\.qweatherapi\.com', config['host']):
            raise ValueError('和风天气域名无效')
        key = serialization.load_der_private_key(
            _dpapi((self.directory / 'ed25519-private.dpapi').read_bytes(), False), None,
        )
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError('天气签名凭据无效')
        return config, key

    def _get(self, url, *, params, headers=None):
        with httpx.Client(transport=self.transport, timeout=httpx.Timeout(6, connect=3.0)) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()

    def _surface(self, point):
        if not self.configured():
            return {}, '和风天气尚未配置'
        try:
            identity, key = self._credential()
            lon, lat = point
            payload = self._get(f'https://{identity["host"]}/weather/v1/hourly/{lat:.2f}/{lon:.2f}',
                                params={'hours': 240, 'localTime': 'true', 'lang': 'zh'},
                                headers={'Authorization': f'Bearer {_jwt(identity, key)}'})
            return qweather_hours(payload), ''
        except httpx.HTTPStatusError as error:
            return {}, f'和风天气请求未完成（HTTP {error.response.status_code}）'
        except (httpx.HTTPError, OSError, KeyError, ValueError, TypeError, RuntimeError):
            return {}, '和风天气暂不可用，请重试'

    def _upper(self, point):
        lon, lat = point
        fields = [f'wind_speed_{height}m' for height in WIND_HEIGHTS] + ['wind_gusts_10m']
        try:
            dataset = self._get(UPPER_SOURCE, params={
                'latitude': f'{lat:.5f}', 'longitude': f'{lon:.5f}', 'hourly': ','.join(fields),
                'timezone': 'GMT', 'wind_speed_unit': 'ms', 'forecast_days': 11,
            })['hourly']
            result = {}
            gusts = dataset.get('wind_gusts_10m', ())
            for i, timestamp in enumerate(dataset['time']):
                time = datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc).astimezone(CHINA)
                levels = []
                for height, field in zip(WIND_HEIGHTS, fields):
                    values = dataset.get(field, ())
                    value = _number(values[i]) if i < len(values) else None
                    if value is not None:
                        levels.append((height, value))
                # The provider timestamps the preceding interval's maximum at its end.
                gust = _number(gusts[i + 1]) if i + 1 < len(gusts) else None
                result[time] = tuple(levels), gust
            return result, ''
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return {}, '计划高度风速来源暂不可用'

    def forecast(self, point):
        if self.transport is None and not self.configured():
            return Forecast((), system_now(CHINA), '和风天气尚未配置')
        cache_key = tuple(round(value, 5) for value in point)
        with self._lock:
            entry = self._cache.get(cache_key)
            if entry and monotonic() - entry[0] <= 900:
                return entry[1]
        with ThreadPoolExecutor(max_workers=2) as workers:
            surface = workers.submit(self._surface, point)
            upper = workers.submit(self._upper, point)
            hours, error = surface.result()
            levels, upper_error = upper.result()
        merged = []
        for time, hour in sorted(hours.items()):
            wind, gust = levels.get(time, ((), None))
            merged.append(replace(hour, levels=wind, surface_gust_10m=gust))
        forecast = Forecast(tuple(merged), system_now(CHINA), error, upper_error)
        if merged and not upper_error:
            with self._lock:
                if len(self._cache) >= 12:
                    self._cache.pop(next(iter(self._cache)))
                self._cache[cache_key] = monotonic(), forecast
        return forecast
