"""Convert the published EPSG:4326 rectangular cells without simplification."""

from pathlib import Path
from struct import unpack_from
import sys
from zipfile import ZipFile

import numpy as np


def read_bounds(path):
    with ZipFile(path) as bundle:
        files = {Path(name).suffix.lower(): name for name in bundle.namelist()}
        projection = bundle.read(files['.prj'])
        if b'"EPSG","4326"' not in projection:
            raise ValueError('Expected the published WGS84 grid.')
        shape = bundle.read(files['.shp'])
    offset = 100
    rows = []
    while offset < len(shape):
        _, length = unpack_from('>ii', shape, offset)
        record = offset + 8
        kind, west, south, east, north, parts, vertices = unpack_from('<i4d2i', shape, record)
        if (kind, parts, vertices) != (5, 1, 5):
            raise ValueError('Unexpected shape in the published grid.')
        corners = {unpack_from('<2d', shape, record + 48 + 16 * i) for i in range(vertices)}
        if corners != {(x, y) for x in (west, east) for y in (south, north)}:
            raise ValueError('Grid conversion cannot use a bounding box for this cell.')
        rows.append((west, south, east, north))
        offset = record + length * 2
    if offset != len(shape) or not rows:
        raise ValueError('Incomplete shapefile.')
    return np.asarray(rows)


if __name__ == '__main__':
    np.savez_compressed(sys.argv[2], bounds=read_bounds(sys.argv[1]))
