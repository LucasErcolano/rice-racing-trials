#!/usr/bin/env python3
"""Windows Final Validation Kit (Python stdlib only).

Commands:
  inventory   read-only installed-asset inventory (never certifies a live AC/CSP run)
  checklist   write the operator checklist template (JSON) to fill in during the in-game test
  report      diagnostics JSONL (client) + server export JSON + checklist -> Markdown/HTML report

Backward compatible: ``validation.py --assetto-root <dir>`` runs ``inventory``.
Synthetic inputs (any line/document with ``"synthetic": true``) are labelled as such and can
never produce a verified result.
"""
import argparse
import hashlib
import html
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAR_ID = 'madformulateam_mft02'
TRACK_ID = 'FSAEJ_Endurance'
MAX_INPUT = 300_000_000
REQUIRED_APIS = ('ac.getCar', 'ac.getSim', 'io.save', 'web.post', 'JSON.parse')
REQUIRED_CAR_FIELDS = ('car.position', 'car.lapCount')
CONE_FIELDS = ('car.collidedWith', 'car.collisionDepth', 'car.wheelsOutside')

CHECKLIST = [
    ('preflight', 'Installer preflight: install.ps1 -Preflight without errors (AC, CSP, MFT02, FSAEJ_Endurance).'),
    ('open_ac', 'Open AC with the MFT02 on FSAEJ_Endurance, Practice session.'),
    ('hud_loads', 'The Rice Trials HUD appears (or opens from the apps bar) with no errors in the Lua console.'),
    ('diagnostics_on', 'Diagnostics enabled (diagnostics=true) and apps/lua/RiceTrials/diagnostics/rice_diagnostics.jsonl is created.'),
    ('outlap_2_laps', 'Out lap + 2 full laps: note the native Best Lap and the times shown by the HUD.'),
    ('cone_touch', 'Touch a cone at low speed: note the approximate time and whether the HUD records it.'),
    ('off_track', 'Go off-track with 4 wheels: note whether the lap is invalidated (isLapValid / HUD).'),
    ('teleport_restart', 'Teleport with Restart session (or return to pits): the HUD discards the lap in progress.'),
    ('network_down', 'Disconnect the network (cable or Wi-Fi) during a lap: the HUD shows offline and queues.'),
    ('network_up', 'Reconnect: the queue is sent and the lap appears on /tv.'),
    ('dummy_candidate', 'Apply the candidate Dummy.kn5 (Apply-DummyPatch.ps1 -Apply), repeat out lap + 1 lap, note whether '
                        'the ~18 s lap disappears, revert (-Revert) and confirm the original hash.'),
    ('mark_spoiled', 'Try ac.markLapAsSpoiled in Practice (Lua console or test app): note whether the lap is invalidated.'),
]
STATUSES = ('pending', 'ok', 'fail', 'n/a')
# Checklists filled with the earlier Spanish status values stay readable.
STATUS_ALIASES = {'pendiente': 'pending', 'falla': 'fail', 'no aplica': 'n/a'}


# ------------------------------------------------------------------ inventory
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


# ------------------------------------------------------------------ inputs
def read_jsonl(path):
    path = Path(path)
    if path.stat().st_size > MAX_INPUT:
        raise ValueError(f'{path} too large')
    records, bad = [], 0
    for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(item, dict):
            records.append(item)
        else:
            bad += 1
    return records, bad


def checklist_template():
    return {'version': 'validation-checklist-v1', 'operator': '', 'station_id': '', 'date': '',
            'ac_version': '', 'csp_version': '',
            'items': [{'id': i, 'text': t, 'status': 'pending', 'notes': ''} for i, t in CHECKLIST]}


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# ------------------------------------------------------------------ analysis
class Report:
    def __init__(self):
        self.verified, self.pending, self.blocking, self.warnings, self.calibration = [], [], [], [], []
        self.facts = {}
        self.synthetic = False


def analyse_diagnostics(records, bad, report, source):
    kinds = {}
    for record in records:
        kinds[str(record.get('kind'))] = kinds.get(str(record.get('kind')), 0) + 1
        if record.get('synthetic') is True:
            report.synthetic = True
    report.facts[f'diagnostics {source}'] = {'lines': len(records), 'invalid_lines': bad, 'types': kinds}
    if bad:
        report.warnings.append(f'{source}: {bad} lines are not valid JSON (possibly cut off when AC closed).')
    starts = [r for r in records if r.get('kind') == 'session_start']
    if not starts:
        report.blocking.append(f'{source}: no session_start: diagnostics did not start or the file is not from the client.')
        return
    start = starts[-1]
    report.facts['csp_version'] = start.get('csp_version')
    report.facts['csp_code'] = start.get('csp_code')
    report.verified.append(f'Lua client loaded on CSP {start.get("csp_version")} and wrote diagnostics ({source}).')
    car, track = str(start.get('car_id') or ''), str(start.get('track_id') or '')
    if car != CAR_ID:
        report.blocking.append(f'Car in session {car!r}: expected {CAR_ID}.')
    else:
        report.verified.append(f'Car {CAR_ID} confirmed by ac.getCarID(0).')
    if TRACK_ID.lower() not in track.lower():
        report.blocking.append(f'Track in session {track!r}: expected {TRACK_ID}.')
    else:
        report.verified.append(f'Track {track} confirmed by ac.getTrackFullID.')
    api = start.get('api') if isinstance(start.get('api'), dict) else {}
    for name in REQUIRED_APIS:
        if api.get(name) is not True:
            report.blocking.append(f'Required API missing: {name}.')
    missing_optional = sorted(k for k, v in api.items() if v is not True and k not in REQUIRED_APIS)
    if missing_optional:
        report.warnings.append('Optional APIs missing: ' + ', '.join(missing_optional) + '.')
    fields = {}
    for record in records:
        if record.get('kind') == 'car_fields' and isinstance(record.get('available'), dict):
            fields = record['available']
    for name in REQUIRED_CAR_FIELDS:
        if fields and fields.get(name) is not True:
            report.blocking.append(f'Car field missing: {name}.')
    absent_cone = [n for n in CONE_FIELDS if fields and fields.get(n) is not True]
    if absent_cone:
        report.warnings.append('Contact fields missing (limited cone detection): ' + ', '.join(absent_cone) + '.')

    samples = [r for r in records if r.get('kind') == 'sample' and isinstance(r.get('pos'), dict)]
    report.facts['position_samples'] = len(samples)
    if samples:
        report.verified.append(f'Position sampling active ({len(samples)} samples).')
    else:
        report.pending.append('No position samples: the driven path cannot be analysed.')
    jumps = 0
    for previous, current in zip(samples, samples[1:]):
        p, q = previous['pos'], current['pos']
        if all(_finite(v) for v in (p.get('x'), p.get('z'), q.get('x'), q.get('z'), previous.get('t'), current.get('t'))):
            if math.hypot(q['x'] - p['x'], q['z'] - p['z']) > 40 and current['t'] - previous['t'] < 2:
                jumps += 1
    report.facts['position_jumps'] = jumps
    if jumps:
        report.verified.append(f'Teleport/restart observed in the trace ({jumps} jumps > 40 m in < 2 s).')
    else:
        report.pending.append('No teleport/restart observed in the trace.')

    laps = [r for r in records if r.get('kind') == 'lap']
    report.facts['native_laps'] = [{'lapCount': r.get('lapCount'), 'previousLapTimeMs': r.get('previousLapTimeMs'),
                                        'isLastLapValid': r.get('isLastLapValid')} for r in laps]
    completed = [r for r in laps if _finite(r.get('previousLapTimeMs')) and r['previousLapTimeMs'] > 0]
    if len(completed) >= 3:
        report.verified.append(f'Native lap counter advanced {len(completed)} times (outlap + 2 laps).')
    else:
        report.pending.append(f'Expected outlap + 2 laps: only {len(completed)} native laps completed.')
    if completed and completed[0]['previousLapTimeMs'] < 30000:
        report.calibration.append(
            f'First native lap of {completed[0]["previousLapTimeMs"] / 1000:.3f} s: confirms the short out lap '
            '(spawn 19.291 m from the line). Evaluate the candidate Dummy.kn5 or discard it via client state.')

    contacts = [r for r in records if r.get('kind') == 'collision' and _finite(r.get('collisionDepth'))
                and r['collisionDepth'] > 0]
    if contacts:
        report.verified.append(f'Contacts recorded by diagnostics: {len(contacts)} (cannot tell a cone from a wall).')
    else:
        report.pending.append('No contact recorded: repeat the cone touch.')
    outside = [r for r in records if (r.get('kind') == 'collision' and _finite(r.get('wheelsOutside'))
                                      and r['wheelsOutside'] >= 4) or
               (r.get('kind') == 'sample' and r.get('isLapValid') is False)]
    if outside:
        report.verified.append('Off-track observed (wheelsOutside >= 4 or isLapValid=false).')
    else:
        report.pending.append('No off-track observed.')

    states = [r.get('state') for r in records if r.get('kind') == 'connection']
    online = [s.get('online') for s in states if isinstance(s, dict) and 'online' in s]
    went_down = any(a is True and b is False for a, b in zip(online, online[1:]))
    came_back = any(a is False and b is True for a, b in zip(online, online[1:]))
    if went_down and came_back:
        report.verified.append('Network drop and reconnection observed in the client connection state.')
    else:
        report.pending.append('Network drop/reconnection not observed in diagnostics.')
    errors = [r.get('message') for r in records if r.get('kind') == 'error']
    if errors:
        report.warnings.append(f'{len(errors)} client errors; first: {str(errors[0])[:160]}')
    spoiled = [r for r in records if r.get('kind') == 'spoiled_test']
    if spoiled:
        result = spoiled[-1]
        report.facts['markLapAsSpoiled'] = {k: result.get(k) for k in ('called', 'ok', 'isLapValid_after', 'session')}


def analyse_export(data, report):
    if data.get('synthetic') is True:
        report.synthetic = True
    laps = data.get('laps') or []
    statuses = {}
    for lap in laps:
        statuses[str(lap.get('status'))] = statuses.get(str(lap.get('status')), 0) + 1
    report.facts['server'] = {'schema_version': data.get('schema_version'), 'rules_version': data.get('rules_version'),
                                'scoring_armed': data.get('scoring_armed'), 'laps': len(laps),
                                'statuses': statuses, 'sync_errors': len(data.get('sync_errors') or [])}
    if laps:
        report.verified.append(f'The server received {len(laps)} laps (export).')
    else:
        report.pending.append('The server export contains no laps.')
    if data.get('sync_errors'):
        report.warnings.append(f'{len(data["sync_errors"])} sync errors on the server.')
    if not data.get('scoring_armed'):
        report.calibration.append('Scoring not armed on the server (expected until calibration is approved).')
    calibrations = [c for c in data.get('calibrations') or [] if c.get('active')]
    if not calibrations:
        report.calibration.append('No active calibration per station: the route is still not approved.')


def analyse_checklist(data, report):
    if data.get('version') != 'validation-checklist-v1':
        raise ValueError('checklist must be validation-checklist-v1')
    if data.get('synthetic') is True:
        report.synthetic = True
    report.facts['operator'] = data.get('operator') or '(no name)'
    known = dict(CHECKLIST)
    seen = set()
    for item in data.get('items', []):
        item_id, status = item.get('id'), item.get('status')
        status = STATUS_ALIASES.get(status, status) if isinstance(status, str) else status
        if item_id not in known or status not in STATUSES:
            report.warnings.append(f'Invalid checklist item: {item_id!r} / {status!r}.')
            continue
        seen.add(item_id)
        text = f'[{item_id}] {known[item_id]}' + (f' Note: {item["notes"]}' if item.get('notes') else '')
        if status == 'ok':
            report.verified.append('Operator: ' + text)
        elif status == 'fail':
            report.blocking.append('Operator: ' + text)
        elif status == 'pending':
            report.pending.append('Operator: ' + text)
    for item_id in sorted(set(known) - seen):
        report.pending.append(f'Operator: [{item_id}] not recorded.')


def build(diagnostics=(), export=None, checklist=None, geometry=None):
    report = Report()
    sources = {}
    for path in diagnostics:
        records, bad = read_jsonl(path)
        sources[str(path)] = digest(Path(path))
        analyse_diagnostics(records, bad, report, Path(path).name)
        if geometry:
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from track import telemetry
            document = json.loads(Path(geometry).read_text(encoding='utf-8'))
            try:
                samples, _ = telemetry.load(path)
                report.facts[f'native line crossings {Path(path).name}'] = telemetry.analyse(
                    samples, document.get('timing_gate'))
            except ValueError as exc:
                report.warnings.append(f'Telemetry not analysable: {exc}')
    if not diagnostics:
        report.pending.append('No client diagnostics JSONL provided.')
    if export:
        sources[str(export)] = digest(Path(export))
        analyse_export(json.loads(Path(export).read_text(encoding='utf-8')), report)
    else:
        report.pending.append('No server export JSON provided (/api/admin/export.json).')
    if checklist:
        sources[str(checklist)] = digest(Path(checklist))
        analyse_checklist(json.loads(Path(checklist).read_text(encoding='utf-8')), report)
    else:
        report.pending.append('No operator checklist provided.')
    report.calibration.append('Route checkpoints: route_config.lua keeps verified=false until explicit approval.')
    report.calibration.append('Slalom: no calibration file unless the operator loads one (track/tool.py --slalom).')
    if report.synthetic:
        report.warnings.insert(0, 'SYNTHETIC DATA: this report certifies no feature in Assetto Corsa.')
    status = 'BLOCKED' if report.blocking else ('INCOMPLETE' if report.pending else 'READY FOR REVIEW')
    if report.synthetic:
        status = 'SYNTHETIC (not valid)'
    return {'status': status, 'synthetic': report.synthetic, 'verified': report.verified, 'pending': report.pending,
            'blocking': report.blocking, 'warnings': report.warnings, 'calibration': report.calibration,
            'facts': report.facts, 'inputs_sha256': sources}


SECTIONS = (('verified', 'Verified features'), ('pending', 'Pending'), ('blocking', 'Blocking errors'),
            ('warnings', 'Warnings'), ('calibration', 'Calibration requirements'))


def markdown(result):
    lines = ['# Windows validation report (Rice Racing Driver Trials)', '',
             f'Status: **{result["status"]}**', '']
    if result['synthetic']:
        lines += ['> SYNTHETIC DATA: generated from test files, not from a real session.', '']
    for key, title in SECTIONS:
        lines += [f'## {title}', '']
        lines += [f'- {item}' for item in result[key]] or ['- (none)']
        lines.append('')
    lines += ['## Data', '', '```json', json.dumps(result['facts'], indent=1, ensure_ascii=False), '```', '',
              '## Inputs (SHA-256)', '']
    lines += [f'- `{k}`: `{v}`' for k, v in result['inputs_sha256'].items()]
    return '\n'.join(lines) + '\n'


def html_page(result):
    body = [f'<h1>Windows validation report</h1><p>Status: <b>{html.escape(result["status"])}</b></p>']
    if result['synthetic']:
        body.append('<p style="color:#b00"><b>SYNTHETIC DATA</b>: certifies nothing.</p>')
    for key, title in SECTIONS:
        items = ''.join(f'<li>{html.escape(i)}</li>' for i in result[key]) or '<li>(none)</li>'
        body.append(f'<h2>{title}</h2><ul class="{key}">{items}</ul>')
    body.append('<h2>Data</h2><pre>' + html.escape(json.dumps(result['facts'], indent=1, ensure_ascii=False)) + '</pre>')
    return ('<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Rice Trials validation</title>'
            '<style>body{font:14px sans-serif;margin:20px}.blocking li{color:#b00}.verified li{color:#060}'
            '.warnings li{color:#a60}</style></head><body>' + ''.join(body) + '</body></html>\n')


# ------------------------------------------------------------------ CLI
def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].startswith('--') and argv[0] not in ('--help', '-h'):
        argv.insert(0, 'inventory')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('inventory')
    p.add_argument('--assetto-root', type=Path, required=True)
    p.add_argument('--landmarks', type=Path, default=ROOT / 'track/calibration/timing-landmarks.json')
    p.add_argument('--output', type=Path, help='Explicit report path; absent means stdout only')
    p = sub.add_parser('checklist')
    p.add_argument('--output', type=Path, required=True)
    p = sub.add_parser('report')
    p.add_argument('--diagnostics', type=Path, nargs='*', default=[])
    p.add_argument('--server-export', type=Path)
    p.add_argument('--checklist', type=Path)
    p.add_argument('--geometry', type=Path, help='track geometry JSON (track/tool.py extract) for line crossings')
    p.add_argument('--output', type=Path, required=True, help='Markdown path; an .html twin is written alongside')
    args = parser.parse_args(argv)
    if args.command == 'inventory':
        payload = json.dumps(inspect(args.assetto_root, args.landmarks), indent=2, sort_keys=True) + '\n'
        if args.output:
            args.output.write_text(payload, encoding='utf-8')
        else:
            print(payload, end='')
    elif args.command == 'checklist':
        if args.output.exists():
            parser.exit(2, f'error: {args.output} already exists\n')
        args.output.write_text(json.dumps(checklist_template(), indent=1, ensure_ascii=False) + '\n', encoding='utf-8')
        print(args.output)
    else:
        result = build(args.diagnostics, args.server_export, args.checklist, args.geometry)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown(result), encoding='utf-8')
        args.output.with_suffix('.html').write_text(html_page(result), encoding='utf-8')
        print(f'{result["status"]}: {args.output}')
        return 1 if result['blocking'] and not result['synthetic'] else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
