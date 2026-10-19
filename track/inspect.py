#!/usr/bin/env python3
"""Inspect original FSAEJ ZIP assets; export an unsurveyed AI-line candidate.

No inference of cone positions, official route width, direction, or scoring gates.
"""
import argparse
import configparser
import hashlib
import json
import math
import struct
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape


def inspect(archive: Path):
    track = archive.stem
    if track not in ('FSAEJ_Endurance', 'FSAEJ_Skidpad'):
        raise ValueError('unsupported track archive name')
    with zipfile.ZipFile(archive) as z:
        required = [f'{track}/ai/fast_lane.ai', f'{track}/data/surfaces.ini',
                    f'{track}/Physics.kn5', f'{track}/Dummy.kn5']
        for member in required:
            if z.getinfo(member).file_size > 200_000_000:
                raise ValueError('unexpected oversized archive member')
            if z.open(member).read(1) is None:
                raise ValueError('unreadable archive member')
        ai = z.read(required[0])
        if len(ai) < 16:
            raise ValueError('truncated AI line')
        version, count, _, _ = struct.unpack_from('<4i', ai)
        if version != 7 or not 2 <= count <= 100000 or len(ai) < 16 + count * 20:
            raise ValueError('unknown AI line layout')
        raw = [struct.unpack_from('<4fi', ai, 16 + i*20) for i in range(count)]
        if any(p[4] != i or not all(math.isfinite(c) for c in p[:4]) or
               (i and p[3] <= raw[i-1][3]) for i, p in enumerate(raw)):
            raise ValueError('invalid AI points')
        settings = configparser.ConfigParser()
        settings.read_string(z.read(required[1]).decode('utf-8-sig'))
        surfaces = {settings[s]['KEY']: settings[s].getboolean('IS_VALID_TRACK')
                    for s in settings.sections() if s.startswith('SURFACE_')}
        hashes = {}
        for member in required:
            h = hashlib.sha256()
            with z.open(member) as stream:
                for block in iter(lambda: stream.read(1024*1024), b''):
                    h.update(block)
            hashes[Path(member).name] = h.hexdigest()
    return {
        'schema': 'ai-path-candidate-v1', 'track': track,
        'confidence': 'asset-derived-unsurveyed', 'scoring_enabled': False,
        'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
        'member_sha256': hashes, 'ai_version': version, 'point_count': count,
        'ai_length_m_approx': round(raw[-1][3], 3),
        'surface_validity': surfaces,
        'coordinates': 'Assetto Corsa world x,z in meters; AI path, not boundary',
        'ai_path_xz_m': [[round(p[0], 4), round(p[2], 4)] for p in raw],
        'limitations': ['No surveyed cones or lane boundaries',
                        'AI driving line is not a certified course centerline',
                        'No calibrated gate direction or slalom pattern']
    }


def diagram(data):
    points = data['ai_path_xz_m']
    x0, x1 = min(p[0] for p in points), max(p[0] for p in points)
    z0, z1 = min(p[1] for p in points), max(p[1] for p in points)
    scale = min(760/max(x1-x0, 1), 500/max(z1-z0, 1))
    coords = ' '.join(f'{20+(x-x0)*scale:.2f},{520-(z-z0)*scale:.2f}' for x, z in points)
    title = escape(data['track'])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 560">\n'
            f'<rect width="800" height="560" fill="#12202a"/>\n'
            f'<text x="20" y="25" fill="white">{title}: AI path only / NOT surveyed</text>\n'
            f'<polyline points="{coords}" fill="none" stroke="#40dfb0" stroke-width="1.5"/>\n'
            f'<circle cx="{20+(points[0][0]-x0)*scale:.2f}" '
            f'cy="{520-(points[0][1]-z0)*scale:.2f}" r="4" fill="orange"/>\n'
            f'<text x="20" y="550" fill="white">Coordinates: AC x,z meters; no cones, limits or official gates</text>\n'
            f'</svg>\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        data = inspect(args.archive)
        args.output.mkdir(parents=True, exist_ok=True)
        stem = data['track'] + '.candidate-v1'
        (args.output / (stem + '.json')).write_text(json.dumps(data, indent=2) + '\n')
        (args.output / (stem + '.svg')).write_text(diagram(data))
        survey = {
            'version': 'slalom-gates-v1', 'status': 'candidate',
            'track': data['track'], 'asset_sha256': data['member_sha256']['Physics.kn5'],
            'survey_id': None, 'gates': [],
            'note': 'Operator must survey and validate every pylon/gate in the matching rig; do not activate from AI path.'
        }
        (args.output / (data['track'] + '.survey-template-v1.json')).write_text(
            json.dumps(survey, indent=2) + '\n')
        print(f'{stem}: {data["point_count"]} AI samples, {data["ai_length_m_approx"]} m approximate, NOT certified')
    except (OSError, ValueError, KeyError, struct.error, zipfile.BadZipFile) as exc:
        parser.exit(2, f'inspection failed: {exc}\n')


if __name__ == '__main__':
    main()
