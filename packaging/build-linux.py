#!/usr/bin/env python3
"""Create auditable source-only deployment ZIPs on Linux; never claims a Windows binary."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ('RiceTrials.lua', 'core.lua', 'scoring.lua', 'detectors.lua', 'journal.lua', 'hud.lua', 'penalties.lua', 'delta.lua', 'native_overlay.lua', 'native_render.lua', 'native_laps.lua', 'lap_clock.lua', 'lap_sync.lua', 'field_v2.lua', 'diagnostics.lua', 'manifest.ini')
LEADERBOARD = ('admin.html', 'admin.js', 'tv.html', 'tv.js', 'field-v2.html', 'field-v2.js', 'field-v2.css')
SAFE_SOURCE = {
    'client': {'.lua', '.ini'}, 'server': {'.py'}, 'leaderboard': {'.html', '.js', '.css'},
    'tests': {'.py'}, 'rules': {'.json'}, 'packaging': {'.py', '.ps1', '.yml'}, 'tools': {'.py'},
    'track': {'.py', '.ps1', '.md'}, 'timing': {'.py'},
}
# Track Calibration Package: tools are our code; geometry/patches are DERIVED from the mod's KN5
# (proprietary). It is a separate internal ZIP, never merged into Complete, and never carries a KN5.
CALIBRATION_ZIP = 'RiceRacing_Track_Calibration.zip'
CALIBRATION_DERIVED = {'.json', '.html', '.svg', '.lua', '.candidate', '.txt', '.ps1'}
FORBIDDEN_IN_CALIBRATION = re.compile(r'\.(kn5|kn5\.original|ai|rar|zip|7z|acd|dds|png|jpg)$', re.IGNORECASE)
CALIBRATION_NOTICE = (
    'INTERNAL USE, DO NOT DISTRIBUTE. derived/ contains data DERIVED from the proprietary FSAEJ geometry '
    '(coordinates, edges, clusters, candidate Dummy.kn5). No original KN5 is included.\n'
    'Dummy patches are CANDIDATES NOT VERIFIED IN GAME. Exported routes have verified=false '
    'unless an operator explicitly approves them.\n')


def safe_files(section, extensions):
    folder = ROOT / section
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.rglob('*')) if p.is_file() and not p.is_symlink()
            and p.suffix.lower() in extensions and p.name.lower() != 'station_config.lua'
            and not any(part.startswith('.') or part in ('__pycache__', 'node_modules', 'dist')
                        for part in p.relative_to(ROOT).parts)]


def build(output: Path, version: str, derived=None):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', version):
        raise ValueError('invalid version')
    output.mkdir(parents=True, exist_ok=True)
    entries = {'client': [(p, f'RiceTrials/{p.name}') for p in safe_files('client', {'.lua', '.ini'}) if p.parent == ROOT / 'client'] +
               [(ROOT / 'packaging' / name, name) for name in ('install.ps1', 'uninstall.ps1')],
               'server': [(p, f'RiceTrialsServer/{p.relative_to(ROOT)}') for p in safe_files('server', {'.py'})] +
                         [(p, f'RiceTrialsServer/{p.relative_to(ROOT)}') for p in safe_files('leaderboard', {'.html', '.js', '.css'})]}
    documentation = [(p, f'docs/{p.name}') for p in sorted((ROOT / 'docs').glob('*.md'))]
    documentation += [(ROOT / 'README.md', 'README.md'), (ROOT / 'track-analysis.md', 'track-analysis.md')]
    entries['client'] += [(ROOT / 'docs/windows-validation.md', 'RiceTrials/WINDOWS_VALIDATION.md'),
                          (ROOT / 'tools/validation.py', 'tools/validation.py'),
                          (ROOT / 'track/calibration/timing-landmarks.json', 'track/calibration/timing-landmarks.json'),
                          (ROOT / 'rules/scoring-v1.json', 'rules/scoring-v1.json')]
    # Track configs (route + cones) for the published FSAEJ_Endurance mod; installed with install.ps1 -RouteConfig/-ConeConfig.
    entries['client'] += [(q, f'track-config/{q.name}') for q in sorted((ROOT / 'track/calibration/route').glob('*'))
                          if q.is_file() and q.suffix in ('.lua', '.json', '.md')]
    entries['server'] += [(ROOT / 'docs/operator-guide.md', 'RiceTrialsServer/OPERATOR_GUIDE.md')] + [
        (ROOT / 'packaging' / name, name) for name in ('start-server.ps1', 'configure-secrets.ps1', 'server-admin.ps1')]
    entries['complete'] = entries['client'] + entries['server'] + documentation + [
        (p, f'source/{p.relative_to(ROOT)}') for section, extensions in SAFE_SOURCE.items()
        for p in safe_files(section, extensions)]
    for section, required in (('client', CLIENT), ('leaderboard', LEADERBOARD), ('server', ('app.py', 'scoring.py'))):
        for name in required:
            if not (ROOT / section / name).is_file():
                raise FileNotFoundError(ROOT / section / name)
    names = {'client': 'RiceRacing_Trials_Client.zip', 'server': 'RiceRacing_Trials_Server.zip', 'complete': 'RiceRacing_Trials_Complete.zip'}
    for kind, files in entries.items():
        for source, _ in files:
            if not source.is_file():
                raise FileNotFoundError(source)
        target = output / names[kind]
        with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
            archive.writestr('SOURCE_ONLY_NOT_READY_FOR_OFFICIAL_TRIALS.txt',
                'Source-only Linux package. No Windows executable, approved FSAEJ KN5, MFT02, physical cone validation, or CSP rig validation included. Do not use for official trials.\n')
            for source, destination in files:
                archive.write(source, destination)
        print(target)
    build_calibration(output, derived)


def build_calibration(output: Path, derived=None):
    """Write RiceRacing_Track_Calibration.zip: tools + committed landmarks + (optional) derived outputs."""
    files = [(p, f'tools/{p.relative_to(ROOT)}') for section, extensions in
             (('track', {'.py', '.ps1', '.md'}), ('timing', {'.py'}), ('tools', {'.py'})) for p in safe_files(section, extensions)]
    files += [(p, f'calibration/{p.relative_to(ROOT / "track/calibration")}')
              for p in sorted((ROOT / 'track/calibration').rglob('*')) if p.is_file() and p.suffix in ('.json', '.svg')]
    files += [(p, f'calibration/{p.name}') for p in sorted((ROOT / 'timing').glob('*-native-evidence.json'))]
    files += [(ROOT / 'docs' / name, f'docs/{name}') for name in
              ('track-tool.md', 'native-timing.md', 'geometry-development.md', 'windows-validation.md')
              if (ROOT / 'docs' / name).is_file()]
    derived = Path(derived) if derived else None
    derived_count = 0
    if derived and derived.is_dir():
        for p in sorted(derived.rglob('*')):
            if p.is_file() and FORBIDDEN_IN_CALIBRATION.search(p.name):
                raise ValueError(f'proprietary/original file inside the derived directory: {p}')
            if p.is_file() and not p.is_symlink() and p.suffix.lower() in CALIBRATION_DERIVED:
                files.append((p, f'derived/{p.relative_to(derived).as_posix()}'))
                derived_count += 1
    for source, destination in files:
        data = source.read_bytes()
        if FORBIDDEN_IN_CALIBRATION.search(destination):
            raise ValueError(f'refusing proprietary payload in calibration package: {destination}')
        if data.startswith(b'sc6969'):
            # Only a Dummy.kn5 CANDIDATE next to its manifest, and never the original under another name.
            manifest = source.parent / 'manifest.json'
            if not destination.endswith('/Dummy.kn5.candidate') or not manifest.is_file():
                raise ValueError(f'KN5 payload outside a patch candidate: {destination}')
            info = json.loads(manifest.read_text(encoding='utf-8'))
            digest = hashlib.sha256(data).hexdigest()
            if digest != info.get('candidate_sha256') or digest == info.get('original_sha256'):
                raise ValueError(f'candidate hash mismatch or identical to the original: {destination}')
    target = output / CALIBRATION_ZIP
    with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
        archive.writestr('INTERNAL_DERIVED_DATA_DO_NOT_DISTRIBUTE.txt', CALIBRATION_NOTICE + (
            '' if derived_count else 'No derived data: generate it with "python -I track/tool.py extract ..." (see docs/track-tool.md).\n'))
        for source, destination in files:
            archive.write(source, destination)
    print(target, f'(derived files: {derived_count})')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    parser.add_argument('--track-calibration', type=Path, default=ROOT / 'dist' / 'track-calibration',
                        help='derived outputs of track/tool.py (default dist/track-calibration; skipped if absent)')
    args = parser.parse_args()
    build(args.output, args.version, args.track_calibration)
