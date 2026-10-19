"""Read-only audit of AC timing and spawn dummies from operator-supplied KN5 archives.

KN5 v5/v6 node structure adapted from the handoff's tools/kn5.py. This tool
extracts evidence; it does not modify a proprietary track or certify lap logic.
"""
import argparse
import hashlib
import json
import math
import struct
from pathlib import Path
from zipfile import ZipFile


class Reader:
    def __init__(self, data):
        self.data = memoryview(data)
        self.pos = 0

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError('truncated or malformed KN5')
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def integer(self):
        return struct.unpack('<i', self.take(4))[0]

    def byte(self):
        return self.take(1)[0]

    def float_array(self, size):
        return struct.unpack('<' + 'f' * size, self.take(4 * size))

    def text(self):
        size = self.integer()
        if size > 1_000_000:
            raise ValueError('oversized KN5 string')
        return self.take(size).tobytes().decode('utf-8', errors='replace')


def multiply(a, b):
    return tuple(sum(a[4 * i + k] * b[4 * k + j] for k in range(4)) for i in range(4) for j in range(4))


IDENTITY = (1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1., 0., 0., 0., 0., 1.)


def markers_from_kn5(data):
    reader = Reader(data)
    if reader.take(6).tobytes() != b'sc6969':
        raise ValueError('invalid KN5 magic')
    version = reader.integer()
    if version not in (5, 6):
        raise ValueError('unsupported KN5 version')
    if version > 5:
        reader.integer()
    for _ in range(reader.integer()):
        reader.integer()
        reader.text()
        reader.take(reader.integer())
    for _ in range(reader.integer()):
        reader.text()
        reader.text()
        reader.take(2)
        reader.integer()
        for _ in range(reader.integer()):
            reader.text()
            reader.take(40)
        for _ in range(reader.integer()):
            reader.text()
            reader.integer()
            reader.text()
    markers = {}

    def node(parent):
        kind, name, children = reader.integer(), reader.text(), reader.integer()
        if children < 0 or children > 100_000:
            raise ValueError('malformed KN5 node count')
        reader.byte()
        if kind == 1:
            matrix = multiply(reader.float_array(16), parent)
            if name.startswith('AC_'):
                markers[name] = list(matrix[12:15])
        elif kind == 2:
            matrix = parent
            reader.take(3)
            count = reader.integer()
            reader.take(count * 44)
            reader.take(reader.integer() * 2)
            reader.take(4 + 4 + 8 + 16 + 1)
        else:
            raise ValueError('unsupported KN5 node kind')
        for _ in range(children):
            node(matrix)

    node(IDENTITY)
    return markers


def analyze_archive(path, track_id):
    with ZipFile(path) as archive:
        data = archive.read(f'{track_id}/Dummy.kn5')
    markers = markers_from_kn5(data)
    required = ('AC_START_0', 'AC_PIT_0', 'AC_TIME_0_L', 'AC_TIME_0_R')
    missing = sorted(set(required) - set(markers))
    if missing:
        raise ValueError(f'missing native timing markers: {missing}')
    line = (markers['AC_TIME_0_L'], markers['AC_TIME_0_R'])
    start = markers['AC_START_0']
    middle = [(a + b) / 2 for a, b in zip(*line)]
    return {'track_id': track_id, 'dummy_kn5_sha256': hashlib.sha256(data).hexdigest(),
            'markers_ac_xyz': markers,
            'grid_to_timing_midpoint_xz_m': math.hypot(start[0] - middle[0], start[2] - middle[2]),
            'native_fix_status': 'REQUIRES_GAME_VALIDATION'}


def classify_native_fix(result):
    if not isinstance(result, dict) or 'markers_ac_xyz' not in result:
        raise ValueError('no timing evidence')
    # Marker coordinates cannot establish game timing behavior or certify a patch.
    return 'REQUIRES_GAME_VALIDATION'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('track_id')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = analyze_archive(args.archive, args.track_id)
    text = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
