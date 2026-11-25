"""Read common camera metadata without loading whole photographs or videos."""
from datetime import datetime
from pathlib import Path
import re
import struct

from .flight_check import CHINA


def _tags(data, start, endian):
    def unpack(spec, offset):
        return struct.unpack_from(endian + spec, data, offset)

    count, = unpack('H', start)
    output = {}
    for index in range(min(count, 256)):
        here = start + 2 + index * 12
        tag, kind, length, position = unpack('HHII', here)
        sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8}
        size = sizes.get(kind, 0) * length
        if not 0 < size <= 1024:
            continue
        offset = here + 8 if size <= 4 else position
        if not 0 <= offset <= len(data)-size:
            continue
        if kind == 2:
            output[tag] = data[offset:offset+size].split(b'\0', 1)[0].decode('ascii', 'ignore')
        elif kind == 4 and length == 1:
            output[tag] = unpack('I', offset)[0]
        elif kind == 5 and length == 3:
            values = unpack('IIIIII', offset)
            if all(values[1::2]):
                output[tag] = sum(n / d / (60**order) for order, (n, d) in enumerate(zip(values[::2], values[1::2])))
    return output


def _jpeg_tags(path):
    with path.open('rb') as file:
        if file.read(2) != b'\xff\xd8':
            return {}, {}
        for _ in range(80):
            marker = file.read(2)
            if len(marker) != 2 or marker[0] != 255 or marker[1] in (218, 217):
                break
            size, = struct.unpack('>H', file.read(2))
            if size < 2:
                break
            if marker[1] != 225:
                file.seek(size - 2, 1)
                continue
            payload = file.read(size - 2)
            if not payload.startswith(b'Exif\x00\x00'):
                continue
            data = payload[6:]
            endian = '<' if data[:2] == b'II' else '>' if data[:2] == b'MM' else None
            if not endian:
                break
            base = _tags(data, struct.unpack_from(endian + 'I', data, 4)[0], endian)
            photo = _tags(data, base[0x8769], endian) if 0x8769 in base else {}
            gps = _tags(data, base[0x8825], endian) if 0x8825 in base else {}
            return photo or base, gps
    return {}, {}


def media_metadata(path):
    path = Path(path)
    try:
        tags, gps = _jpeg_tags(path) if path.suffix.lower() in ('.jpg', '.jpeg') else ({}, {})
    except (OSError, struct.error, TypeError):
        tags, gps = {}, {}
    point = None
    if gps.get(1) in ('N', 'S') and gps.get(3) in ('E', 'W') and isinstance(gps.get(2), (int, float)) and isinstance(gps.get(4), (int, float)):
        lat = gps[2] * (-1 if gps[1] == 'S' else 1)
        lon = gps[4] * (-1 if gps[3] == 'W' else 1)
        if abs(lat) <= 90 and abs(lon) <= 180:
            point = [lon, lat]
    time = tags.get(0x9003) or tags.get(0x132)
    result = None
    try:
        if time:
            captured = datetime.strptime(time, '%Y:%m:%d %H:%M:%S')
            zone = tags.get(0x9011)
            if zone:
                try:
                    captured = datetime.fromisoformat(captured.isoformat() + zone)
                except ValueError:
                    pass
            result = captured if captured.tzinfo else captured.replace(tzinfo=CHINA)
        elif match := re.search(r'(?<!\d)(20\d{6})[_-]?(\d{6})(?!\d)', path.stem):
            result = datetime.strptime(''.join(match.groups()), '%Y%m%d%H%M%S').replace(tzinfo=CHINA)
    except (ValueError, TypeError):
        pass
    return {'time': result.isoformat() if result else '', 'point': point}
