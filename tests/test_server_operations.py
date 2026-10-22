"""HTTP-level operational tests: auth, idempotency, ordering, concurrency, restart, export, backup/restore, admin, penalties."""
import contextlib
import csv
import io
import json
import sqlite3
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from server import app, scoring
from server.app import Store, make_handler

ADMIN = 'admin-secret'
STATIONS = {f'ST0{i}': f'station-{i}-secret' for i in range(1, 5)}


class Harness(unittest.TestCase):
    armed = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / 'trials.sqlite'
        self.start()

    def start(self):
        self.store = Store(self.db, scoring_armed=self.armed)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, ADMIN, STATIONS))
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        self.worker.join()

    def tearDown(self):
        self.stop()
        self.tmp.cleanup()

    def call(self, method, path, data=None, token=None, raw=None):
        conn = HTTPConnection('127.0.0.1', self.http.server_port, timeout=20)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        conn.request(method, path, body, headers)
        response = conn.getresponse()
        payload, code, kind = response.read(), response.status, response.getheader('Content-Type') or ''
        conn.close()
        return code, json.loads(payload) if kind.startswith('application/json') else payload.decode()

    def admin(self, path, data):
        code, body = self.call('POST', path, data, ADMIN)
        self.assertEqual(code, 200, body)
        return body

    def seed(self, session='S1', pilots=('P1', 'P2', 'P3', 'P4')):
        self.admin('/api/admin/sessions', {'session_id': session, 'name': 'Session ' + session, 'active': True})
        for pilot in pilots:
            self.admin('/api/admin/pilots', {'pilot_id': pilot, 'name': 'Driver ' + pilot})
        for index, pilot in enumerate(pilots, 1):
            self.admin('/api/admin/assignments', {'station_id': f'ST0{index}', 'session_id': session, 'pilot_id': pilot})

    def lap(self, station='ST01', pilot='P1', lap_id='L1', session='S1', **extra):
        data = dict(protocol_version=1, station_id=station, session_id=session, pilot_id=pilot, lap_id=lap_id,
                    raw_ms=60000, events={'doo': 0, 'oc': 0}, status='valid', event='autocross')
        data.update(extra)
        return data

    def submit(self, data, expect=200):
        code, body = self.call('POST', '/api/laps', data, STATIONS[data['station_id']])
        self.assertEqual(code, expect, body)
        return body


class AuthAndProtocol(Harness):
    def test_invalid_authentication(self):
        self.seed()
        self.assertEqual(self.call('POST', '/api/laps', self.lap())[0], 401)
        self.assertEqual(self.call('POST', '/api/laps', self.lap(), 'bogus')[0], 401)
        self.assertEqual(self.call('POST', '/api/laps', self.lap(), STATIONS['ST02'])[0], 403)
        self.assertEqual(self.call('POST', '/api/laps', self.lap(), ADMIN)[0], 403)
        self.assertEqual(self.call('GET', '/api/admin/export.json')[0], 401)
        self.assertEqual(self.call('GET', '/api/admin/export.json', token='nope')[0], 401)
        self.assertEqual(self.call('GET', '/api/admin/export.json', token=STATIONS['ST01'])[0], 403)
        self.assertEqual(self.call('POST', '/api/admin/sessions', {'session_id': 'X', 'name': 'x'}, STATIONS['ST01'])[0], 403)
        self.assertEqual(self.call('GET', '/api/station/assignment', token='nope')[0], 401)
        errors = self.call('GET', '/api/admin/sync-errors', token=ADMIN)[1]['sync_errors']
        self.assertTrue(any(e['station_id'] == 'ST01' and e['http_status'] == 401 for e in errors))

    def test_incompatible_configuration_is_rejected_and_logged(self):
        self.seed()
        for changes, cause in (({'protocol_version': 2}, 'unsupported protocol_version'), ({'rules_version': 2}, 'unsupported rules_version'),
                               ({'lap_number': -1}, 'invalid lap_number'), ({'detector': 'x'}, 'invalid detector'),
                               ({'detector': {'blob': 'x' * 5000}}, 'detector too large'), ({'calibration_version': [1]}, 'invalid calibration_version')):
            code, body = self.call('POST', '/api/laps', self.lap(**changes), STATIONS['ST01'])
            self.assertEqual((code, body['error']), (400, cause))
        code, body = self.call('POST', '/api/station/heartbeat', {'protocol_version': 2, 'station_id': 'ST01'}, STATIONS['ST01'])
        self.assertEqual(code, 400)
        errors = self.call('GET', '/api/admin/sync-errors?station_id=ST01', token=ADMIN)[1]['sync_errors']
        self.assertEqual(len(errors), 7)
        self.assertEqual(errors[0]['path'], '/api/station/heartbeat')
        self.assertEqual({e['lap_id'] for e in errors[1:]}, {'L1'})

    def test_sync_errors_are_bounded(self):
        original = app.SYNC_ERROR_LIMIT
        app.SYNC_ERROR_LIMIT = 5
        try:
            for _ in range(12):
                self.store.record_sync_error('ST01', '/api/laps', 400, 'x')
        finally:
            app.SYNC_ERROR_LIMIT = original
        self.assertEqual(len(self.store.sync_errors()), 5)

    def test_station_final_time_is_stored_as_sent_when_well_formed(self):
        self.seed()
        lap = self.lap(event='endurance', status='PENDING_REVIEW', scoring_version=1, events={'cone': 1}, corrected_ms=60000 + 5500)
        result = self.submit(lap)
        self.assertEqual((result['status'], result['corrected_ms']), ('PENDING_REVIEW', 65500))
        stored = self.call('GET', '/api/admin/laps?session_id=S1', token=ADMIN)[1]['laps'][0]
        self.assertEqual(stored['corrected_ms'], 65500)
        self.assertEqual(self.call('GET', '/api/leaderboard')[1]['ranking'], {})  # still never ranked while unarmed

    def test_malformed_station_final_time_is_not_stored(self):
        self.seed()
        for i, bad in enumerate((1, -5, 1.5, '65000', 60000 + 10 ** 8, None)):
            lap = self.lap(lap_id='B%d' % i, event='endurance', status='PENDING_REVIEW', scoring_version=1, events={}, corrected_ms=bad)
            self.assertIsNone(self.submit(lap)['corrected_ms'], bad)

    def test_optional_client_metadata_is_stored_and_exported(self):
        self.seed()
        extra = dict(lap_number=4, rules_version=1, calibration_version='cal-7', client_timestamp='2026-10-19T10:00:00Z',
                     detector={'gates': 12, 'missed': 0, 'confidence': 0.98}, sync={'queued': 2, 'attempt': 1}, corrected_ms=1)
        result = self.submit(self.lap(**extra))
        self.assertEqual(result['corrected_ms'], 60000, 'client corrected_ms must be ignored')
        lap = self.call('GET', '/api/admin/laps?session_id=S1', token=ADMIN)[1]['laps'][0]
        self.assertEqual((lap['lap_number'], lap['calibration_version'], lap['detector']['gates'], lap['sync']['queued']), (4, 'cal-7', 12, 2))
        self.assertEqual(lap['client_corrected_ms_ignored'], 1)
        stations = {s['station_id']: s for s in self.call('GET', '/api/admin/stations', token=ADMIN)[1]['stations']}
        self.assertEqual(stations['ST01']['detector']['confidence'], 0.98)
        self.assertEqual(set(stations), set(STATIONS))


class LapDelivery(Harness):
    def test_identical_duplicate_is_idempotent_and_different_payload_conflicts(self):
        self.seed()
        first = self.submit(self.lap(lap_number=1))
        again = self.submit(self.lap(lap_number=1))
        self.assertEqual((first['duplicate'], again['duplicate'], again['corrected_ms']), (False, True, 60000))
        code, body = self.call('POST', '/api/laps', self.lap(lap_number=1, raw_ms=59000), STATIONS['ST01'])
        self.assertEqual((code, body['error']), (409, 'lap_id already used with different payload'))
        self.assertEqual(len(self.store.laps('S1')), 1)

    def test_out_of_order_delivery_is_accepted(self):
        self.seed()
        self.submit(self.lap(lap_id='L3', lap_number=3, raw_ms=58000))
        self.submit(self.lap(lap_id='L2', lap_number=2, raw_ms=61000))
        self.submit(self.lap(lap_id='L1', lap_number=1, raw_ms=62000))
        laps = self.call('GET', '/api/admin/laps?session_id=S1', token=ADMIN)[1]['laps']
        self.assertEqual(sorted(l['lap_number'] for l in laps), [1, 2, 3])
        board = self.call('GET', '/api/leaderboard?session_id=S1')[1]
        self.assertEqual(board['ranking']['autocross'][0]['lap_id'], 'L3')
        self.assertEqual(board['ranking']['autocross'][0]['valid_laps'], 3)

    def test_simultaneous_simulators(self):
        self.seed()
        failures = []

        def station(index):
            sid, pilot = f'ST0{index}', f'P{index}'
            try:
                for n in range(1, 16):
                    data = self.lap(station=sid, pilot=pilot, lap_id=f'{sid}-L{n}', lap_number=n, raw_ms=60000 + index * 100 + n)
                    code, body = self.call('POST', '/api/laps', data, STATIONS[sid])
                    if code != 200:
                        failures.append((sid, n, code, body))
                    if n % 5 == 0:
                        self.call('POST', '/api/laps', data, STATIONS[sid])  # retry: must be idempotent
                        self.call('POST', '/api/station/heartbeat', {'protocol_version': 1, 'station_id': sid}, STATIONS[sid])
            except Exception as error:  # pragma: no cover
                failures.append((sid, repr(error)))

        threads = [threading.Thread(target=station, args=(i,)) for i in range(1, 5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(failures, [])
        self.assertEqual(len(self.store.laps('S1')), 60)
        ranking = self.call('GET', '/api/leaderboard')[1]['ranking']['autocross']
        self.assertEqual([r['pilot_id'] for r in ranking], ['P1', 'P2', 'P3', 'P4'])
        stations = self.call('GET', '/api/admin/stations', token=ADMIN)[1]['stations']
        self.assertTrue(all(s['online'] and not s['offline'] for s in stations))


class Persistence(Harness):
    def test_restart_keeps_data_and_migrations_are_idempotent(self):
        self.seed()
        self.submit(self.lap(lap_number=1))
        self.stop()
        for _ in range(3):
            app.migrate(self.db)
        self.start()
        with contextlib.closing(sqlite3.connect(self.db)) as db, db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], app.SCHEMA_VERSION)
            self.assertEqual(db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.submit(self.lap(lap_number=1))['duplicate'], True)
        self.assertEqual(self.call('GET', '/api/leaderboard')[1]['session_id'], 'S1')
        self.assertEqual(len(self.store.laps('S1')), 1)

    def test_legacy_unversioned_database_is_migrated(self):
        self.stop()
        legacy = Path(self.tmp.name) / 'legacy.sqlite'
        with contextlib.closing(sqlite3.connect(legacy)) as db, db:
            db.executescript('''
                CREATE TABLE sessions (session_id TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, station_id TEXT NOT NULL, lap_id TEXT NOT NULL, session_id TEXT NOT NULL, reviewer TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL, before_status TEXT NOT NULL, after_status TEXT NOT NULL, created_utc TEXT NOT NULL);
                INSERT INTO sessions VALUES ('OLD', 'Legacy');''')
        db_path, self.db = self.db, legacy
        self.start()
        self.assertEqual(self.store.sessions()[0]['session_id'], 'OLD')
        self.assertFalse(self.store.sessions()[0]['active'])
        self.db = db_path

    def test_backup_and_restore(self):
        self.seed()
        self.submit(self.lap(lap_id='before-backup'))
        backup = Path(self.admin('/api/admin/backup', {})['path'])
        self.assertTrue(backup.exists())
        self.submit(self.lap(lap_id='after-backup'))
        self.stop()
        bad = Path(self.tmp.name) / 'bad.sqlite'
        bad.write_bytes(b'not a database' * 100)
        quiet = lambda argv: self._quiet(argv)
        self.assertEqual(quiet(['--db', str(self.db), '--restore', str(bad)]), 2)
        empty = Path(self.tmp.name) / 'empty.sqlite'
        sqlite3.connect(empty).close()
        with self.assertRaises(RuntimeError):
            app.restore(self.db, empty)
        self.assertEqual(quiet(['--db', str(self.db), '--restore', str(backup)]), 0)
        pre = sorted((self.db.parent / 'backups').glob('*pre-restore*.sqlite'))
        self.assertEqual(len(pre), 1)
        self.assertEqual(app.check_integrity(pre[0])['integrity'], 'ok')
        self.start()
        self.assertEqual([l['lap_id'] for l in self.store.laps('S1')], ['before-backup'])
        self.assertEqual(quiet(['--db', str(self.db), '--check']), 0)

    @staticmethod
    def _quiet(argv):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return app.main(argv)


class AdminOperations(Harness):
    def test_sessions_active_and_reassignment_history(self):
        self.seed('S1', pilots=('P1', 'P2'))
        self.admin('/api/admin/sessions', {'session_id': 'S2', 'name': 'Tarde'})
        self.assertEqual(self.call('GET', '/api/sessions')[1]['active_session'], 'S1')
        self.admin('/api/admin/sessions/active', {'session_id': 'S2'})
        sessions = self.call('GET', '/api/admin/sessions', token=ADMIN)[1]
        self.assertEqual((sessions['active_session'], [s['active'] for s in sessions['sessions']]), ('S2', [False, True]))
        self.assertEqual(self.call('POST', '/api/admin/sessions/active', {'session_id': 'NOPE'}, ADMIN)[0], 404)
        self.admin('/api/admin/assignments', {'station_id': 'ST01', 'session_id': 'S2', 'pilot_id': 'P2'})
        data = self.call('GET', '/api/admin/assignments?station_id=ST01', token=ADMIN)[1]
        self.assertEqual([h['reassignment'] for h in data['history']], [False, True])
        self.assertEqual(data['history'][1]['previous_pilot_id'], 'P1')
        self.assertEqual(self.call('GET', '/api/leaderboard')[1]['session_id'], 'S2')
        diag = self.call('GET', '/api/admin/diagnostics', token=ADMIN)[1]
        self.assertEqual((diag['active_session'], diag['schema_version'], diag['journal_mode']), ('S2', app.SCHEMA_VERSION, 'wal'))
        self.assertTrue(all(s['offline'] for s in diag['stations']))

    def test_export_csv_and_json(self):
        self.seed()
        self.submit(self.lap(lap_number=1, detector={'ok': True}, client_timestamp=1760000000.5))
        self.submit(self.lap(station='ST02', pilot='P2', lap_id='-cmd', events={'doo': 1, 'oc': 0}))
        self.admin('/api/admin/penalties', {'station_id': 'ST01', 'lap_id': 'L1', 'events': {'doo': 2}, 'reviewer': 'chief', 'reason': 'video'})
        export = self.call('GET', '/api/admin/export.json', token=ADMIN)[1]
        for key in ('sessions', 'pilots', 'laps', 'reviews', 'assignments', 'assignment_history', 'calibrations', 'sync_errors'):
            self.assertIn(key, export)
        self.assertEqual((len(export['laps']), len(export['reviews']), export['rules_version']), (2, 1, scoring.RULES_VERSION))
        code, text = self.call('GET', '/api/admin/export.csv?table=laps&session_id=S1', token=ADMIN)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(code, 200)
        self.assertEqual(rows[0]['detector'], '{"ok":true}')
        self.assertEqual({r['lap_id'] for r in rows}, {'L1', "'-cmd"})  # formula injection neutralized
        for table in app.EXPORT_TABLES:
            self.assertEqual(self.call('GET', f'/api/admin/export.csv?table={table}', token=ADMIN)[0], 200)
        self.assertEqual(self.call('GET', '/api/admin/export.csv?table=secrets', token=ADMIN)[0], 400)
        reviews = list(csv.DictReader(io.StringIO(self.call('GET', '/api/admin/reviews.csv?session_id=S1', token=ADMIN)[1])))
        self.assertEqual((reviews[0]['kind'], reviews[0]['after_corrected_ms']), ('penalties', '64000'))

    def test_penalty_review_recalculates_and_is_audited(self):
        self.seed()
        self.submit(self.lap(events={'doo': 0, 'oc': 0}))
        result = self.admin('/api/admin/penalties', {'station_id': 'ST01', 'lap_id': 'L1', 'events': {'doo': 1, 'oc': 1}, 'reviewer': 'chief', 'reason': 'camera 2'})
        self.assertEqual((result['corrected_ms'], result['events']), (60000 + 2000 + 20000, {'doo': 1, 'oc': 1}))
        review = self.call('GET', '/api/admin/reviews?session_id=S1', token=ADMIN)[1]['reviews'][0]
        self.assertEqual((review['kind'], review['before_corrected_ms'], review['after_corrected_ms'], review['reviewer']), ('penalties', 60000, 82000, 'chief'))
        self.assertEqual(json.loads(review['before_events']), {'doo': 0, 'oc': 0})
        self.assertEqual(self.call('GET', '/api/leaderboard')[1]['ranking']['autocross'][0]['best_corrected_ms'], 82000)
        for bad in ({'doo': -1}, {'raw_ms': 1}, {'right_ms': 5}, {}, {'doo': 1.5}):
            payload = {'station_id': 'ST01', 'lap_id': 'L1', 'events': bad, 'reviewer': 'j', 'reason': 'r'}
            self.assertEqual(self.call('POST', '/api/admin/penalties', payload, ADMIN)[0], 400, bad)
        self.assertEqual(self.call('POST', '/api/admin/penalties', {'station_id': 'ST01', 'lap_id': 'L1', 'events': {'doo': 0}, 'reviewer': 'j', 'reason': ''}, ADMIN)[0], 400)
        self.assertEqual(self.call('POST', '/api/admin/penalties', {'station_id': 'ST01', 'lap_id': 'nope', 'events': {'doo': 0}, 'reviewer': 'j', 'reason': 'r'}, ADMIN)[0], 404)

    def test_endurance_penalty_review_then_approval(self):
        self.seed()
        self.admin('/api/admin/calibrations', {'station_id': 'ST01', 'track_config': 'trk', 'reference': 'acta-1', 'operator': 'insp'})
        lap = self.lap(event='endurance', status='VALID', scoring_version=1, events={'cone': 0, 'off_course': 0, 'slalom': []},
                       track_config='trk', calibration_verified=False, detector_ok=True)
        self.assertEqual(self.submit(lap)['status'], 'PENDING_REVIEW')
        result = self.admin('/api/admin/penalties', {'station_id': 'ST01', 'lap_id': 'L1', 'events': {'cone': 2, 'slalom': ['S1']}, 'reviewer': 'j', 'reason': 'video'})
        self.assertEqual((result['status'], result['corrected_ms'], result['corrected_preview_ms']), ('PENDING_REVIEW', None, 60000 + 4000 + 20000))
        self.assertEqual(self.call('GET', '/api/leaderboard')[1]['ranking'], {})


class Leaderboard(Harness):
    def test_only_valid_laps_ranked_by_corrected_with_deterministic_ties(self):
        self.seed()
        self.submit(self.lap(station='ST01', pilot='P1', lap_id='a', raw_ms=50000, events={'doo': 5, 'oc': 0}))   # 60000
        self.submit(self.lap(station='ST02', pilot='P2', lap_id='b', raw_ms=55000, events={'doo': 0, 'oc': 0}))   # 55000
        self.submit(self.lap(station='ST03', pilot='P3', lap_id='c', raw_ms=58000, events={'doo': 1, 'oc': 0}))   # 60000, higher raw
        self.submit(self.lap(station='ST04', pilot='P4', lap_id='d', raw_ms=40000, status='dnf'))
        self.submit(self.lap(station='ST04', pilot='P4', lap_id='e', raw_ms=41000, status='invalid'))
        board = self.call('GET', '/api/leaderboard')[1]
        ranking = board['ranking']['autocross']
        self.assertEqual([(r['rank'], r['pilot_id'], r['best_corrected_ms']) for r in ranking], [(1, 'P2', 55000), (2, 'P1', 60000), (3, 'P3', 60000)])
        self.assertEqual([r['gap_ms'] for r in ranking], [0, 5000, 5000])
        self.assertEqual((ranking[1]['penalty_ms'], ranking[1]['raw_ms']), (10000, 50000))
        self.assertTrue(board['scoring_armed'])


class UnarmedLeaderboard(Harness):
    armed = False

    def test_unarmed_server_never_ranks_or_approves(self):
        self.seed()
        self.assertEqual(self.submit(self.lap())['status'], 'PENDING_REVIEW')
        board = self.call('GET', '/api/leaderboard')[1]
        self.assertEqual((board['ranking'], board['leaders'], board['scoring_armed']), ({}, [], False))
        result = self.admin('/api/admin/penalties', {'station_id': 'ST01', 'lap_id': 'L1', 'events': {'doo': 1}, 'reviewer': 'j', 'reason': 'r'})
        self.assertEqual((result['corrected_ms'], result['official']), (None, False))
        code, body = self.call('POST', '/api/admin/reviews', {'station_id': 'ST01', 'lap_id': 'L1', 'decision': 'approve', 'reviewer': 'j', 'reason': 'r'}, ADMIN)
        self.assertEqual(code, 409)

    def test_production_default_is_unarmed(self):
        self.assertFalse(Store(Path(self.tmp.name) / 'default.sqlite').scoring_armed)

    def test_static_pages_use_external_assets_only(self):
        for page, assets in (('/tv', ('/tv.css', '/tv.js')), ('/admin', ('/admin.css', '/admin.js'))):
            code, html = self.call('GET', page)
            self.assertEqual(code, 200)
            self.assertNotIn('<style', html)
            self.assertNotIn('style=', html)
            self.assertNotRegex(html, r'<script>(?!</script>)')
            for asset in assets:
                self.assertIn(asset, html)
                self.assertEqual(self.call('GET', asset)[0], 200)


if __name__ == '__main__':
    unittest.main()
