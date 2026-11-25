"""Limit airspace overview geometry to the current map viewport."""
from functools import lru_cache
from math import isfinite

import numpy as np

from .official_airspace import hunan_grid

MAX_RECTS = 480


@lru_cache(maxsize=64)
def visible_grid(bounds, zoom):
    if len(bounds) != 4 or not all(isfinite(value) for value in (*bounds, zoom)):
        return ()
    west, south, east, north = bounds
    if west >= east or south >= north:
        return ()
    cells = hunan_grid()
    indices = np.nonzero((cells[:, 0] < east) & (cells[:, 2] > west)
                         & (cells[:, 1] < north) & (cells[:, 3] > south))[0]
    stride = max(1, (len(indices) + MAX_RECTS - 1) // MAX_RECTS)
    # Overview zooms show a sample of actual cells, with no filled gaps.
    if zoom < 12:
        stride = max(stride, round(12 - zoom))
    return tuple(tuple(float(value) for value in row) for row in cells[indices[::stride]])
