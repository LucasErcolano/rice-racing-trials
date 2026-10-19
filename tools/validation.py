#!/usr/bin/env python3
"""Read-only installed-asset inventory; never certifies a live AC/CSP run."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAR_ID = 'madformulateam_mft02'


def digest(path):
    if not path.is_file() or path.is_symlink():
        return None
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def inspect(assetto_root, landmarks=ROOT / 'track/calibration/timing-landmarks.json'):
    root = Path(assetto_root)
    if not root.is_dir():
        raise FileNotFoundError(root)
    reference = json.loads(Path(landmarks).read_text(encoding='utf-8'))
    tracks = {}
    for track_id, expected in reference['tracks'].items():
        directory = root / 'content/tracks' / track_id
        files = {name: digest(directory / name) for name in ('Physics.kn5', 'Dummy.kn5')}
        tracks[track_id] = {
            'id': track_id, 'installed': directory.is_dir(),
            'root_models': sorted(p.name for p in directory.glob('*.kn5') if p.is_file() and not p.is_symlink()),
            'physics_kn5_sha256': files['Physics.kn5'], 'dummy_kn5_sha256': files['Dummy.kn5'],
            'landmark_hashes_match': files['Physics.kn5'] == expected['physics_kn5_sha256'] and
                                      files['Dummy.kn5'] == expected['dummy_kn5_sha256'],
        }
    car = root / 'content/cars' / CAR_ID
    data_acd = digest(car / 'data.acd')
    unpacked = digest(car / 'data/car.ini')
    csp = {'dwrite_dll_sha256': digest(root / 'dwrite.dll'),
           'lua_apps_folder': (root / 'apps/lua').is_dir(),
           'status': 'candidate-only' if (root / 'dwrite.dll').is_file() and (root / 'apps/lua').is_dir() else 'not-detected'}
    return {
        'status': 'commissioning-required', 'ready': False, 'assetto_root': str(root.resolve()),
        'csp': csp, 'tracks': tracks,
        'car': {'id': CAR_ID, 'installed': car.is_dir(), 'data_acd_sha256': data_acd,
                'data_car_ini_sha256': unpacked, 'has_physics_data': bool(data_acd or unpacked)},
        'required_live_checks': ['AC/CSP version and Lua load', 'car selection and track/layout identity',
                                 'calibrated X/Z directional gate and ordered checkpoints',
                                 'outlap and independent timing/video review', 'server authorization/read-back'],
        'warning': 'Matching files are offline evidence only; neither CSP runtime nor route/scoring is certified.'
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--assetto-root', type=Path, required=True)
    parser.add_argument('--landmarks', type=Path, default=ROOT / 'track/calibration/timing-landmarks.json')
    parser.add_argument('--output', type=Path, help='Explicit report path; absent means stdout only')
    args = parser.parse_args()
    payload = json.dumps(inspect(args.assetto_root, args.landmarks), indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.write_text(payload, encoding='utf-8')
    else:
        print(payload, end='')


if __name__ == '__main__':
    main()
