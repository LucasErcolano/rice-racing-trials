import csv
import io
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from pathlib import Path
from http.server import ThreadingHTTPServer

from server.app import Store, make_handler


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'trials.sqlite', calibrated_configs={'test-only-calibration'}, scoring_armed=True)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store, 'admin-secret', {'ST01': 'station-secret', 'ST02': 'second-secret'}))
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.worker.join()
        self.tmp.cleanup()

    def call(self, method, path, data=None, token=None):
        conn = HTTPConnection('127.0.0.1', self.http.server_port)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        conn.request(method, path, json.dumps(data).encode() if data is not None else None, headers)
        response = conn.getresponse()
        body = response.read()
        code = response.status
        content_type = response.getheader('Content-Type')
        conn.close()
        return code, json.loads(body) if content_type.startswith('application/json') else body.decode()

    def setup_assignment(self):
        for path, value in [('/api/admin/sessions', {'session_id': 'S1', 'name': 'Morning'}),
                            ('/api/admin/pilots', {'pilot_id': 'P1', 'name': 'Pilot One'}),
                            ('/api/admin/assignments', {'station_id': 'ST01', 'session_id': 'S1', 'pilot_id': 'P1'})]:
            self.assertEqual(self.call('POST', path, value, 'admin-secret')[0], 200)

    def result(self, **overrides):
        data = dict(protocol_version=1, station_id='ST01', session_id='S1', pilot_id='P1', lap_id='L1', raw_ms=60000, events={'doo': 0, 'oc': 0}, status='valid', event='autocross')
        data.update(overrides)
        return data

    def test_malformed_content_length_is_client_error_not_server_error(self):
        conn = HTTPConnection('127.0.0.1', self.http.server_port)
        conn.putrequest('POST', '/api/laps')
        conn.putheader('Content-Length', 'not-a-number')
        conn.endheaders()
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        response.read()
        conn.close()

    def test_auth_assignment_and_idempotent_lap(self):
        self.setup_assignment()
        self.assertEqual(self.call('POST', '/api/laps', self.result())[0], 401)
        self.assertEqual(self.call('POST', '/api/laps', self.result(pilot_id='P2'), 'station-secret')[0], 409)
        self.assertEqual(self.call('POST', '/api/laps', self.result(), 'station-secret')[0], 200)
        self.assertEqual(self.call('POST', '/api/laps', self.result(), 'station-secret')[1]['duplicate'], True)
        self.assertEqual(self.call('POST', '/api/laps', self.result(raw_ms=1000), 'station-secret')[0], 409)
        self.assertEqual(len(self.call('GET', '/api/leaderboard?session_id=S1')[1]['laps']), 1)

    def test_scoring_validation_and_session_isolation(self):
        self.setup_assignment()
        for data in (self.result(raw_ms=-1), self.result(raw_ms=True), self.result(events={'doo': -1}), self.result(event='skidpad'), self.result(status='mystery'), self.result(protocol_version=2)):
            self.assertEqual(self.call('POST', '/api/laps', data, 'station-secret')[0], 400)
        self.assertEqual(self.call('POST', '/api/laps', self.result(events={'doo': 2, 'oc': 1}), 'station-secret')[1]['corrected_ms'], 84000)
        self.assertEqual(self.call('GET', '/api/leaderboard?session_id=S2')[1]['laps'], [])
        self.assertEqual(self.call('GET', '/api/leaderboard?session_id=S1')[1]['leaders'][0]['best_ms'], 84000)

    def test_skidpad_and_invalid_attempt(self):
        self.setup_assignment()
        data = self.result(event='skidpad', events={'right_ms': 62000, 'left_ms': 60000, 'doo': 2})
        self.assertEqual(self.call('POST', '/api/laps', data, 'station-secret')[1]['corrected_ms'], 61250)
        self.assertEqual(self.call('POST', '/api/laps', self.result(lap_id='L2', status='dnf'), 'station-secret')[1]['corrected_ms'], None)
        self.assertEqual(len(self.call('GET', '/api/leaderboard?session_id=S1')[1]['laps']), 2)
        self.assertEqual(self.call('GET', '/api/leaderboard?session_id=S1')[1]['leaders'][0]['best_ms'], 61250)

    def test_admin_and_tv_pages_expose_provisional_review_and_diagnostics(self):
        admin = self.call('GET', '/admin')[1]
        tv = self.call('GET', '/tv')[1]
        admin_js = self.call('GET', '/admin.js')[1]
        tv_js = self.call('GET', '/tv.js')[1]
        for control in ('calibrate', 'review', 'pending', 'audit'):
            self.assertIn('id="' + control + '"', admin)
        self.assertIn('/api/admin/reviews', admin_js)
        self.assertIn('/api/admin/calibrations', admin_js)
        self.assertIn('textContent', admin_js)
        self.assertIn('id="diagnostics"', tv)
        self.assertIn('id="recent-laps"', tv)
        self.assertIn('data.diagnostics', tv_js)

    def test_csv_review_export_requires_admin_and_keeps_audit(self):
        self.setup_assignment()
        self.call('POST', '/api/laps', self.endurance(), 'station-secret')
        self.call('POST', '/api/admin/reviews', {'station_id': 'ST01', 'lap_id': 'L1', 'decision': 'reject', 'reason': 'bad line', 'reviewer': 'chief'}, 'admin-secret')
        self.assertEqual(self.call('GET', '/api/admin/reviews.csv?session_id=S1')[0], 401)
        code, text = self.call('GET', '/api/admin/reviews.csv?session_id=S1', token='admin-secret')
        self.assertEqual(code, 200)
        rows = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual((rows[0]['lap_id'], rows[0]['reviewer'], rows[0]['after_status']), ('L1', 'chief', 'INVALID'))

    def test_csv_backup_and_pages(self):
        self.setup_assignment()
        self.call('POST', '/api/laps', self.result(), 'station-secret')
        code, text = self.call('GET', '/api/admin/export.csv?session_id=S1', token='admin-secret')
        self.assertEqual(code, 200)
        self.assertEqual(list(csv.DictReader(io.StringIO(text)))[0]['lap_id'], 'L1')
        self.assertEqual(self.call('GET', '/api/admin/export.csv')[0], 401)
        code, backup = self.call('POST', '/api/admin/backup', {}, 'admin-secret')
        self.assertEqual(code, 200)
        self.assertTrue(Path(backup['path']).exists())
        for page in ('/admin', '/tv'):
            self.assertEqual(self.call('GET', page)[0], 200)

    def endurance(self, **changes):
        data = self.result(event='endurance', status='VALID', events={'cone': 1, 'off_course': 1, 'slalom': ['A', 'B']},
                           track_config='test-only-calibration', scoring_version=1,
                           calibration_verified=True, detector_ok=True)
        data.update(changes)
        return data

    def certify(self):
        self.assertEqual(self.call('POST', '/api/admin/calibrations',
            {'station_id': 'ST01', 'track_config': 'test-only-calibration', 'reference': 'inspection-record', 'operator': 'race-director'}, 'admin-secret')[0], 200)

    def test_endurance_scoring_and_unranked_states(self):
        self.setup_assignment()
        self.certify()
        for index, status in enumerate(('VALID', 'INVALID', 'OUTLAP', 'PENDING_REVIEW', 'DETECTOR_ERROR')):
            code, response = self.call('POST', '/api/laps', self.endurance(lap_id=f'E{index}', status=status), 'station-secret')
            self.assertEqual(code, 200)
            self.assertEqual(response['corrected_ms'], 122000 if status == 'VALID' else None)
        board = self.call('GET', '/api/leaderboard?session_id=S1')[1]
        self.assertEqual(board['leaders'], [{'event': 'endurance', 'pilot_id': 'P1', 'pilot_name': 'Pilot One', 'best_ms': 122000}])

    def test_endurance_fails_closed_for_missing_calibration_and_detector(self):
        self.setup_assignment()
        for index, changes in enumerate(({'calibration_verified': False}, {'detector_ok': False},
                                         {'track_config': ''}, {'calibration_verified': None})):
            code, response = self.call('POST', '/api/laps', self.endurance(lap_id=f'F{index}', **changes), 'station-secret')
            self.assertEqual(code, 200)
            self.assertIsNone(response['corrected_ms'])
        self.assertEqual(self.call('GET', '/api/leaderboard?session_id=S1')[1]['leaders'], [])

    def test_untrusted_track_cannot_self_attest_calibration(self):
        self.setup_assignment()
        code, response = self.call('POST', '/api/laps', self.endurance(track_config='unverified-track'), 'station-secret')
        self.assertEqual(code, 200)
        self.assertEqual(response['status'], 'PENDING_REVIEW')
        self.assertIsNone(response['corrected_ms'])
        self.assertEqual(self.call('GET', '/api/leaderboard?session_id=S1')[1]['leaders'], [])

    def test_calibration_requires_admin_provenance_for_correct_station(self):
        self.setup_assignment()
        self.assertEqual(self.call('POST', '/api/laps', self.endurance(), 'station-secret')[1]['status'], 'PENDING_REVIEW')
        evidence = {'station_id': 'ST01', 'track_config': 'test-only-calibration', 'reference': 'inspection-2026-10-19', 'operator': 'race-director'}
        self.assertEqual(self.call('POST', '/api/admin/calibrations', evidence)[0], 401)
        code, certificate = self.call('POST', '/api/admin/calibrations', evidence, 'admin-secret')
        self.assertEqual(code, 200)
        self.assertEqual(certificate['reference'], evidence['reference'])
        code, lap = self.call('POST', '/api/laps', self.endurance(lap_id='certified'), 'station-secret')
        self.assertEqual((code, lap['status'], lap['corrected_ms']), (200, 'VALID', 122000))
        board = self.call('GET', '/api/leaderboard?session_id=S1')[1]
        self.assertEqual(board['laps'][-1]['calibration_reference'], evidence['reference'])
        self.assertEqual(self.call('POST', '/api/admin/calibrations', {**evidence, 'active': False}, 'admin-secret')[0], 200)
        self.assertEqual(self.call('POST', '/api/laps', self.endurance(lap_id='revoked'), 'station-secret')[1]['status'], 'PENDING_REVIEW')

    def test_review_requires_admin_and_records_immutable_audit(self):
        self.setup_assignment()
        self.assertEqual(self.call('POST', '/api/laps', self.endurance(), 'station-secret')[1]['status'], 'PENDING_REVIEW')
        review = {'station_id': 'ST01', 'lap_id': 'L1', 'decision': 'approve', 'reason': 'inspected footage', 'reviewer': 'chief'}
        self.assertEqual(self.call('POST', '/api/admin/reviews', review)[0], 401)
        self.assertEqual(self.call('POST', '/api/admin/reviews', review, 'station-secret')[0], 403)
        self.assertEqual(self.call('POST', '/api/admin/reviews', review, 'admin-secret')[0], 409)
        self.certify()
        code, accepted = self.call('POST', '/api/admin/reviews', review, 'admin-secret')
        self.assertEqual((code, accepted['status'], accepted['corrected_ms']), (200, 'VALID', 122000))
        self.assertEqual(self.call('GET', '/api/leaderboard?session_id=S1')[1]['leaders'][0]['best_ms'], 122000)
        self.assertEqual(self.call('POST', '/api/admin/reviews', {**review, 'decision': 'reject'}, 'admin-secret')[1]['status'], 'INVALID')
        code, audit = self.call('GET', '/api/admin/reviews?session_id=S1', token='admin-secret')
        self.assertEqual(code, 200)
        self.assertEqual([(r['before_status'], r['after_status']) for r in audit['reviews']], [('PENDING_REVIEW', 'VALID'), ('VALID', 'INVALID')])
        self.assertEqual(self.call('GET', '/api/admin/reviews?session_id=S1')[0], 401)
        self.assertEqual(self.call('GET', '/api/leaderboard?session_id=S1')[1]['leaders'], [])
        self.assertEqual(self.call('POST', '/api/laps', self.endurance(), 'station-secret')[1]['status'], 'INVALID')

    def test_station_assignment_heartbeat_and_reassignment(self):
        self.setup_assignment()
        self.assertEqual(self.call('GET', '/api/station/assignment', token='station-secret')[1]['pilot_id'], 'P1')
        self.assertEqual(self.call('POST', '/api/station/heartbeat', {'station_id': 'ST01', 'protocol_version': 1}, 'station-secret')[0], 200)
        self.assertEqual(self.call('GET', '/api/station/assignment', token='station-secret')[1]['online'], True)
        self.assertEqual(self.call('POST', '/api/admin/pilots', {'pilot_id': 'P2', 'name': 'Two'}, 'admin-secret')[0], 200)
        self.assertEqual(self.call('POST', '/api/admin/assignments', {'station_id': 'ST01', 'session_id': 'S1', 'pilot_id': 'P2'}, 'admin-secret')[0], 200)
        self.assertEqual(self.call('POST', '/api/laps', self.endurance(), 'station-secret')[0], 409)
        self.assertEqual(self.call('POST', '/api/laps', self.endurance(lap_id='L2', pilot_id='P2'), 'station-secret')[0], 200)
        self.assertEqual(self.call('POST', '/api/laps', self.endurance(lap_id='L2', pilot_id='P1'), 'station-secret')[0], 409)

    def test_stale_heartbeat_is_offline_but_preserves_last_seen(self):
        self.setup_assignment()
        self.call('POST', '/api/station/heartbeat', {'station_id': 'ST01', 'protocol_version': 1}, 'station-secret')
        old = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with self.store.connect() as db:
            db.execute('UPDATE heartbeats SET seen_utc=? WHERE station_id=?', (old, 'ST01'))
        assignment = self.call('GET', '/api/station/assignment', token='station-secret')[1]
        self.assertFalse(assignment['online'])
        self.assertEqual(assignment['last_seen_utc'], old)
        self.assertTrue(self.call('POST', '/api/station/heartbeat', {'station_id': 'ST01', 'protocol_version': 1}, 'station-secret')[1]['online'])

    def test_two_stations_isolated_idempotency_and_public_diagnostics(self):
        self.setup_assignment()
        self.assertEqual(self.call('POST', '/api/admin/assignments', {'station_id': 'ST02', 'session_id': 'S1', 'pilot_id': 'P1'}, 'admin-secret')[0], 200)
        self.assertEqual(self.call('GET', '/api/station/assignment', token='second-secret')[1]['station_id'], 'ST02')
        self.assertEqual(self.call('POST', '/api/laps', self.result(), 'second-secret')[0], 403)
        self.assertEqual(self.call('POST', '/api/laps', self.result(), 'station-secret')[0], 200)
        self.assertEqual(self.call('POST', '/api/laps', self.result(station_id='ST02', raw_ms=59000), 'second-secret')[0], 200)
        self.assertEqual(self.call('POST', '/api/laps', self.result(station_id='ST02', raw_ms=59000), 'second-secret')[1]['duplicate'], True)
        self.assertEqual(self.call('POST', '/api/laps', self.result(lap_id='E1', event='endurance', status='DETECTOR_ERROR', scoring_version=1, events={'cone': 0}), 'station-secret')[0], 200)
        board = self.call('GET', '/api/leaderboard?session_id=S1')[1]
        self.assertEqual(len(board['laps']), 3)
        self.assertEqual(board['leaders'][0]['best_ms'], 59000)
        self.assertEqual(board['leaders'][0]['pilot_name'], 'Pilot One')
        self.assertEqual(board['diagnostics']['status_counts'], {'valid': 2, 'DETECTOR_ERROR': 1})
        self.assertEqual({s['station_id'] for s in board['diagnostics']['stations']}, {'ST01', 'ST02'})

    def test_endurance_multiple_pilots_restart_and_duplicate(self):
        self.setup_assignment()
        self.certify()
        self.assertEqual(self.call('POST', '/api/laps', self.endurance(), 'station-secret')[0], 200)
        self.call('POST', '/api/admin/pilots', {'pilot_id': 'P2', 'name': 'Two'}, 'admin-secret')
        self.call('POST', '/api/admin/assignments', {'station_id': 'ST01', 'session_id': 'S1', 'pilot_id': 'P2'}, 'admin-secret')
        second = self.endurance(lap_id='L2', pilot_id='P2', raw_ms=50000, events={'cone': 0, 'off_course': 0, 'slalom': []})
        self.assertEqual(self.call('POST', '/api/laps', second, 'station-secret')[0], 200)
        self.assertTrue(self.call('POST', '/api/laps', second, 'station-secret')[1]['duplicate'])
        self.assertEqual([x['pilot_id'] for x in self.call('GET', '/api/leaderboard?session_id=S1')[1]['leaders']], ['P2', 'P1'])
        reopened = Store(self.store.path, calibrated_configs={'test-only-calibration'}, scoring_armed=True)
        self.assertEqual([x['pilot_id'] for x in reopened.leaderboard('S1')['leaders']], ['P2', 'P1'])
