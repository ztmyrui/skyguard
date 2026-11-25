"""Deterministic sample radar and local constant-velocity estimation."""
from bisect import bisect_right
from functools import lru_cache
from math import atan2, cos, degrees, hypot, pi, radians, sin
from random import Random

import numpy as np


TRACKS = {'obs-a': (112.953, 28.1792, 250, 110, 91),
          'obs-c': (112.956, 28.1740, 170, 230, 37)}


def transition(elapsed, noise=0.45):
    identity = np.eye(2)
    zero = np.zeros((2, 2))
    step = np.block([[identity, elapsed * identity], [zero, identity]])
    process = noise * np.block([[elapsed**3 / 3 * identity, elapsed**2 / 2 * identity],
                                [elapsed**2 / 2 * identity, elapsed * identity]])
    return step, process


def forecast(state, covariance, elapsed):
    step, process = transition(elapsed)
    return step @ state, step @ covariance @ step.T + process


@lru_cache(maxsize=2)
def prepare_track(identifier):
    """Create noisy measured frames and filter history once, permitting arbitrary replay seeks."""
    longitude, latitude, span_x, span_y, seed = TRACKS[identifier]
    random = Random(seed)
    sigma = 4.0
    measurement = np.eye(2) * sigma**2
    select_position = np.eye(4)[:2]
    records = []
    state = covariance = None
    previous = -8
    for seconds in range(-8, 1801, 2):
        if 120 <= seconds < 156 and identifier == 'obs-a':
            continue
        measured = np.array([span_x * sin(seconds * 2*pi/350),
                             span_y * sin(seconds * 2*pi/440)])
        measured += [random.gauss(0, sigma), random.gauss(0, sigma)]
        if state is None or seconds - previous > 15:
            state = np.array([*measured, 0., 0.])
            covariance = np.diag([sigma**2, sigma**2, 16., 16.])
            sample_count = 1
        else:
            state, covariance = forecast(state, covariance, seconds - previous)
            residual = measured - select_position @ state
            innovation = select_position @ covariance @ select_position.T + measurement
            gain = np.linalg.solve(innovation, select_position @ covariance).T
            state = state + gain @ residual
            correction = np.eye(4) - gain @ select_position
            covariance = correction @ covariance @ correction.T + gain @ measurement @ gain.T
            sample_count += 1
        records.append((seconds, measured, state, covariance, sample_count))
        previous = seconds
    return longitude, latitude, [record[0] for record in records], records


def track_view(identifier, clock):
    longitude, latitude, times, records = prepare_track(identifier)
    east = 111195 * cos(radians(latitude))

    def point(position):
        return {'lon': longitude + float(position[0]) / east,
                'lat': latitude + float(position[1]) / 111195}

    index = bisect_right(times, clock) - 1
    if index < 0:
        return None
    seconds, measured, state, covariance, count = records[index]
    age = clock - seconds
    prediction = []
    if age <= 8 and count >= 3:
        for step in range(0, 31, 5):
            predicted, uncertainty = forecast(state, covariance, age + step)
            radius = float(np.sqrt(5.991 * np.linalg.eigvalsh(uncertainty[:2, :2])[-1]))
            if radius > 140:
                break
            prediction.append({**point(predicted), 'seconds': step, 'radius_m': round(radius, 1)})
    return {**point(measured), 'age': round(age), 'speed': round(hypot(*state[2:]), 1),
            'heading': round(degrees(atan2(*state[2:])) % 360),
            'accuracy_m': 10, 'prediction': prediction, 'observed_s': seconds,
            'history': [point(row[1]) for row in records[max(0,index-17):index+1]
                        if row[0] >= seconds - min(34, (count-1)*2)],
            'track_state': '跟踪中断' if age > 8 else '模拟雷达跟踪',
            'quality': '信息陈旧' if age > 8 else '较充分'}


def candidate_tracks(view, clock, distance):
    if view['lat'] is None or view['age'] > 90:
        return []
    observed = view.get('observed_s', clock - view['age'])
    candidates = []
    for identifier in TRACKS:
        track = track_view(identifier, observed)
        if not track or track['age'] > 8:
            continue
        separation = distance((track['lat'], track['lon']), (view['lat'], view['lon']))
        if separation <= view['accuracy_m'] + track['accuracy_m']:
            candidates.append({'id': identifier, 'distance_m': round(separation)})
    return sorted(candidates, key=lambda item: (item['distance_m'], item['id']))
