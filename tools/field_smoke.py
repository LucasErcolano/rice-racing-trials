#!/usr/bin/env python3
"""Real process/HTTP OR smoke, usable against source or a frozen Windows executable.

All data and credentials are ephemeral fixtures. Does not run AC or validate CSP.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def run(server_exe=None):
    checks = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    admin, station = secrets.token_hex(24), secrets.token_hex(24)
    env = dict(os.environ, RICE_ADMIN_TOKEN=admin,
               RICE_STATION_TOKENS=json.dumps({'FIELD01': station}))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = 'http://127.0.0.1:' + str(port)

    def call(method, path, data=None, token=None, expected=200) -> Any:
        request = urllib.request.Request(url + path,
            data=json.dumps(data).encode() if data is not None else None, method=method,
            headers={'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})})
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body, status = response.read().decode(), response.code
            assert status == expected, f'{method} {path}: {status}, expected {expected}'
            return json.loads(body) if 'application/json' in response.headers.get('Content-Type', '') else body

    def payload(channel, lap):
        data: dict[str, Any] = dict(protocol='rice-lap-or-v2', channel=channel, message_id=lap + '-' + channel,
                    station_id='FIELD01', session_id='FIELD', pilot_id='PILOT', track_config='FSAEJ_Endurance',
                    lap_id=lap, raw_ms=60000, native_valid=True, rules_version=1)
        if channel == 'penalty':
            data.update(events=[dict(type='cone_contact', cone_id='C1', penalizable=True)],
                        settings=dict(enable_cones=True, enable_off_track=True, enable_slalom=True,
                                      cone_penalty_ms=2000, off_course_penalty_ms=20000, slalom_penalty_ms=20000),
                        evidence=dict(source='rice-client-v2', track_config='FSAEJ_Endurance',
                                      detector_ok=True, calibration_verified=True,
                                      detector={'version': 'smoke-fixture'}, telemetry=[]))
        return data

    def send(channel, lap, data=None, expected=200):
        return call('POST', '/api/v2/' + channel + '-laps', data or payload(channel, lap), station, expected)

    with tempfile.TemporaryDirectory(prefix='rice-field-smoke-') as tmp:
        command = [str(Path(server_exe).resolve())] if server_exe else [sys.executable, '-m', 'server.app']
        command += ['--db', str(Path(tmp) / 'field.sqlite'), '--host', '127.0.0.1', '--port', str(port),
                    '--enable-penalties-v2', '--field-v2-only']
        proc = None
        log_path = Path(tmp) / 'server.log'
        with log_path.open('ab') as log:
            def start():
                child = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise AssertionError('server exited: ' + log_path.read_text(encoding='utf-8', errors='replace'))
                    try:
                        call('GET', '/tv')
                        return child
                    except (OSError, urllib.error.URLError):
                        time.sleep(.1)
                child.terminate(); child.wait(timeout=10)
                raise AssertionError('server readiness timeout: ' + log_path.read_text(encoding='utf-8', errors='replace'))

            def stop(child):
                if child and child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill(); child.wait(timeout=10)

            try:
                proc = start()
                for endpoint, data in [('sessions', dict(session_id='FIELD', name='Field smoke', active=True)),
                                       ('pilots', dict(pilot_id='PILOT', name='Fixture')),
                                       ('assignments', dict(station_id='FIELD01', session_id='FIELD', pilot_id='PILOT'))]:
                    call('POST', '/api/admin/' + endpoint, data, admin)
                assert 'PROVISIONAL' in call('GET', '/v2/field')
                checks.append('field_ui')
                call('POST', '/api/v2/laps', {'station_id': 'FIELD01'}, station, 404)
                checks.append('heavy_ingestion_disabled')
                native = send('native', 'NATIVE')
                assert native['status'] == 'PENALTIES_PENDING' and native['raw_ms'] == 60000
                assert native['penalty_ms'] is None and native['corrected_ms'] is None
                checks.append('native_only')
                penalty = send('penalty', 'PENALTY')
                assert penalty['penalty_received'] and not penalty['native_received'] and not penalty['official']
                checks.append('penalty_only')
                verified = call('POST', '/api/admin/v2/field-review',
                    dict(station_id='FIELD01', session_id='FIELD', lap_id='PENALTY', approved=True,
                         reviewer='smoke', reason='Fixture review exercises administrator attestation only'), admin)
                assert verified['status'] == 'VERIFIED_PROVISIONAL' and verified['evidence_verified']
                assert verified['preview_ms'] == 62000 and not verified['official']
                checks.append('audited_manual_verification')
                row = {}
                for lap, order in [('NP', ('native', 'penalty')), ('PN', ('penalty', 'native'))]:
                    for channel in order:
                        row = send(channel, lap)
                    assert row['native_received'] and row['penalty_received']
                    assert row['corrected_preview_ms'] == 62000 and row['final_ms'] is None
                checks.append('both_orders')
                assert send('native', 'NATIVE')['duplicate']
                checks.append('duplicate')
                bad = payload('penalty', 'NATIVE'); bad['raw_ms'] += 1
                send('penalty', 'NATIVE', bad, 409)
                checks.append('conflict')
                rows = call('GET', '/api/admin/v2/field-laps', token=admin)['laps']
                assert len(rows) == 4
                export = call('GET', '/api/admin/v2/field-export.json', token=admin)
                assert export['provisional'] and len(export['laps']) == 4
                assert all(not row['official'] and row['corrected_ms'] is None for row in export['laps'])
                checks.append('export')
                assert call('GET', '/api/leaderboard?session_id=FIELD')['ranking'] == {}
                checks.append('official_ranking_empty')
                stop(proc); proc = start()
                restored = call('GET', '/api/admin/v2/field-laps', token=admin)['laps']
                assert restored == rows
                assert send('native', 'NATIVE')['duplicate']
                checks.append('restart')
            finally:
                stop(proc)
    return dict(passed=True, official=False, runtime='frozen-exe' if server_exe else 'source',
                platform=sys.platform, checks=checks,
                note='Process, HTTP, SQLite and packaging validation only. No AC/CSP runtime validation.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-exe', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = run(args.server_exe)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
