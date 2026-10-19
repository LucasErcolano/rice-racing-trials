#!/usr/bin/env python3
"""Create auditable source-only deployment ZIPs on Linux; never claims a Windows binary."""
import argparse
from pathlib import Path
import re
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ('RiceTrials.lua', 'core.lua', 'scoring.lua', 'detectors.lua', 'journal.lua', 'hud.lua', 'diagnostics.lua', 'manifest.ini')
LEADERBOARD = ('admin.html', 'admin.js', 'tv.html', 'tv.js')
SAFE_SOURCE = {
    'client': {'.lua', '.ini'}, 'server': {'.py'}, 'leaderboard': {'.html', '.js', '.css'},
    'tests': {'.py'}, 'rules': {'.json'}, 'packaging': {'.py', '.ps1', '.yml'}, 'tools': {'.py'},
}


def safe_files(section, extensions):
    folder = ROOT / section
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.rglob('*')) if p.is_file() and not p.is_symlink()
            and p.suffix.lower() in extensions and p.name.lower() != 'station_config.lua'
            and not any(part.startswith('.') or part in ('__pycache__', 'node_modules', 'dist')
                        for part in p.relative_to(ROOT).parts)]


def build(output: Path, version: str):
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
    entries['server'] += [(ROOT / 'docs/operator-guide.md', 'RiceTrialsServer/OPERATOR_GUIDE.md')] + [
        (ROOT / 'packaging' / name, name) for name in ('start-server.ps1', 'configure-secrets.ps1')]
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


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    build(args.output, args.version)
