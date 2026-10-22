"""Real HTTP contract tests for independent native/penalty field channels."""
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from server.app import Store, make_handler


class FieldV2HttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'field.sqlite'
        self.store = Store(self.path, penalties_v2_enabled=True)
        self.start()
        for endpoint, data in [('sessions', {'session_id': 'S1', 'name': 'Session'}),
                               ('pilots', {'pilot_id': 'P1', 'name': 'Pilot'}),
                               ('assignments', {'station_id': 'ST01', 'session_id': 'S1', 'pilot_id': 'P1'})]:
            self.assertEqual(self.call('POST', '/api/admin/' + endpoint, data, 'admin')[0], 200)

    def start(self):
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
            self.store, 'admin', {'ST01': 'station', 'ST02': 'other'}))
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()

    def stop(self):
        self.http.shutdown()
        self.http.server_close()
        self.worker.join()

    def tearDown(self):
        self.stop()
        self.tmp.cleanup()

    def call(self, method, path, data=None, token: str | None = 'station') -> tuple[int, Any]:
        conn = HTTPConnection('127.0.0.1', self.http.server_port, timeout=5)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        conn.request(method, path, json.dumps(data) if data is not None else None, headers)
        response = conn.getresponse()
        body, code = response.read().decode(), response.status
        kind = response.getheader('Content-Type') or ''
        conn.close()
        return code, json.loads(body) if kind.startswith('application/json') else body

    def payload(self, channel='native', lap='L1') -> dict[str, Any]:
        value: dict[str, Any] = dict(protocol='rice-lap-or-v2', message_id=lap + '-' + channel,
                     station_id='ST01', session_id='S1', pilot_id='P1',
                     track_config='FSAEJ_Endurance', lap_id=lap, raw_ms=60000,
                     rules_version=1, channel=channel, native_valid=True)
        if channel == 'penalty':
            value.update(events=[{'type': 'cone_contact', 'cone_id': 'C1', 'penalizable': True}],
                         settings=dict(enable_cones=True, enable_off_track=True, enable_slalom=True,
                                       cone_penalty_ms=2000, off_course_penalty_ms=20000,
                                       slalom_penalty_ms=20000),
                         evidence=dict(source='rice-client-v2', detector_ok=True,
                                       calibration_verified=True, track_config='FSAEJ_Endurance',
                                       detector={'version': 'test'}))
        return value

    def send_channel(self, channel='native', data=None):
        return self.call('POST', '/api/v2/' + channel + '-laps', data or self.payload(channel))

    def rows(self):
        code, value = self.call('GET', '/api/admin/v2/field-laps', token='admin')
        self.assertEqual(code, 200)
        return value['laps']

    def test_both_orders_reconcile_exact_identity_and_recompute_provisional_preview(self):
        for lap, order in [('L1', ('native', 'penalty')), ('L2', ('penalty', 'native'))]:
            result = {}
            for channel in order:
                code, result = self.send_channel(channel, self.payload(channel, lap))
                self.assertEqual(code, 200)
            self.assertEqual(result['status'], 'PENDING_REVIEW')
            self.assertEqual(result['corrected_preview_ms'], 62000)
            self.assertEqual(result['penalty_ms'], 2000)
            self.assertIsNone(result['final_ms'])
            self.assertIsNone(result['corrected_ms'])
            self.assertFalse(result['official'])
            self.assertFalse(result['evidence_verified'])
            self.assertTrue(result['native_received'])
            self.assertTrue(result['penalty_received'])
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.store.leaderboard('S1')['ranking'], {})

    def test_malformed_penalty_shapes_rejected_without_losing_native(self):
        self.assertEqual(self.send_channel()[0], 200)
        for field, value in [('events', {}), ('events', [{'type': 'unknown'}]),
                             ('events', [{'type': 'cone_contact', 'penalizable': True}]),
                             ('events', [{'type': 'cone_contact', 'cone_id': 'C1', 'penalizable': 'yes'}]),
                             ('settings', {}), ('evidence', []),
                             ('settings', dict(self.payload('penalty')['settings'], enable_cones=1))]:
            with self.subTest(field=field, value=value):
                payload = self.payload('penalty')
                payload[field] = value
                self.assertEqual(self.send_channel('penalty', payload)[0], 400)
        self.assertEqual(len(self.rows()), 1)
        self.assertFalse(self.rows()[0]['penalty_received'])

    def test_incomplete_or_spoofed_evidence_allows_only_unverified_math_preview(self):
        for index, mutation in enumerate([{}, {'source': 'other'}, {'detector_ok': False},
                                          {'calibration_verified': False}, {'track_config': 'other'},
                                          {'detector': {}}, {'detector_ok': 'true'}]):
            lap = 'E' + str(index)
            self.send_channel('native', self.payload('native', lap))
            payload = self.payload('penalty', lap)
            if not mutation:
                payload['evidence'] = {}
            else:
                payload['evidence'].update(mutation)
            payload['verified'] = True
            code, result = self.send_channel('penalty', payload)
            self.assertEqual(code, 200)
            self.assertEqual(result['status'], 'PENDING_REVIEW')
            self.assertEqual(result['corrected_preview_ms'], 62000)
            self.assertEqual(result['preview_ms'], 62000)
            self.assertIsNone(result['corrected_ms'])
            self.assertIsNone(result['final_ms'])
            self.assertFalse(result['official'])
            self.assertFalse(result['evidence_verified'])
            self.assertTrue(result['review_reasons'])

    def test_track_config_evidence_ignores_only_letter_case(self):
        # AC reports the folder name (fsaej_endurance on the VM); calibration files say FSAEJ_Endurance.
        # Windows folder names are case-insensitive, so only case may differ; any other value stays flagged.
        for lap, row_track, evidence_track, flagged in (('T1', 'fsaej_endurance', 'FSAEJ_Endurance', False),
                                                        ('T2', 'fsaej_endurance', 'fsaej_skidpad', True),
                                                        ('T3', 'fsaej_endurance', None, True)):
            payload = self.payload('penalty', lap)
            payload['track_config'] = row_track
            payload['evidence']['track_config'] = evidence_track
            code, result = self.send_channel('penalty', payload)
            self.assertEqual(code, 200)
            self.assertEqual('evidence.track_config' in result['review_reasons'], flagged, lap)
            self.assertFalse(result['evidence_verified'])
            self.assertFalse(result['official'])

    def test_penalty_only_uncalibrated_has_math_preview_without_native_confirmation(self):
        payload = self.payload('penalty')
        payload['evidence']['calibration_verified'] = False
        code, result = self.send_channel('penalty', payload)
        self.assertEqual(code, 200)
        self.assertEqual(result['status'], 'NATIVE_PENDING')
        self.assertEqual(result['corrected_preview_ms'], 62000)
        self.assertEqual(result['penalty_ms'], 2000)
        self.assertIn('evidence.calibration_verified', result['review_reasons'])
        self.assertFalse(result['official'])
        self.assertFalse(result['evidence_verified'])
        self.assertIsNone(result['final_ms'])
        self.assertIsNone(result['corrected_ms'])

    def test_conflicting_raw_identity_and_altered_retry_are_retained_for_review(self):
        for index, mutation in enumerate([{'raw_ms': 61000}, {'pilot_id': 'P2'},
                                          {'track_config': 'OtherTrack'}]):
            lap = 'C' + str(index)
            self.assertEqual(self.send_channel('native', self.payload('native', lap))[0], 200)
            payload = self.payload('penalty', lap)
            payload.update(mutation)
            code, _ = self.send_channel('penalty', payload)
            self.assertEqual(code, 409)
            row = next(row for row in self.rows() if row['lap_id'] == lap)
            self.assertEqual(row['status'], 'PENDING_REVIEW')
            self.assertEqual(row['raw_ms'], 60000)
            self.assertFalse(row['penalty_received'])
            self.assertEqual(row['conflicts'][0]['payload'], payload)
            self.assertIsNone(row['corrected_preview_ms'])
        self.send_channel('penalty', self.payload('penalty', 'C0'))
        payload = self.payload('native', 'C0')
        payload.update(lap_id='OtherLap', raw_ms=62000)
        self.assertEqual(self.send_channel('native', payload)[0], 409)
        rows = self.rows()
        self.assertEqual(len(rows), 3)
        row = next(row for row in rows if row['lap_id'] == 'C0')
        self.assertEqual(row['native_payload'], self.payload('native', 'C0'))
        self.assertEqual(len(row['conflicts']), 2)
        self.assertEqual(self.send_channel('native', self.payload('native', 'C0'))[0], 200)
        self.assertIsNone(self.rows()[0]['corrected_preview_ms'])
        self.stop()
        self.store = Store(self.path, penalties_v2_enabled=True)
        self.start()
        self.assertEqual(len(self.rows()[0]['conflicts']), 2)

    def test_provisional_exports_keep_original_telemetry_and_unknown_values(self):
        self.send_channel()
        payload = self.payload('penalty')
        payload['evidence']['telemetry'] = [{'t_ms': 0, 'x': 1, 'z': 2}]
        self.assertEqual(self.send_channel('penalty', payload)[0], 200)
        self.send_channel('native', self.payload('native', 'OnlyNative'))
        code, exported = self.call('GET', '/api/admin/v2/field-export.json?session_id=S1', token='admin')
        self.assertEqual(code, 200)
        self.assertTrue(exported['provisional'])
        self.assertFalse(exported['official'])
        self.assertEqual(exported['laps'][0].get('preview_ms'), 62000)
        self.assertEqual(exported['laps'][0].get('evidence'), payload['evidence'])
        self.assertEqual(exported['laps'][0]['penalty_payload']['evidence']['telemetry'],
                         payload['evidence']['telemetry'])
        self.assertIsNone(exported['laps'][1]['penalty_ms'])
        code, csv_body = self.call('GET', '/api/admin/v2/field-export.csv?session_id=S1', token='admin')
        self.assertEqual(code, 200)
        import csv
        import io
        rows = list(csv.DictReader(io.StringIO(csv_body)))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['penalty_ms'], '')
        self.assertEqual(rows[0]['corrected_preview_ms'], '62000')
        self.assertEqual(json.loads(rows[0]['penalty_payload']), payload)
        self.assertEqual(self.call('GET', '/api/admin/v2/field-laps?session_id=Other', token='admin')[1]['laps'], [])

    def test_field_only_mode_rejects_heavy_trajectory_before_analysis_but_accepts_native(self):
        self.store.field_v2_only = True
        code, result = self.call('POST', '/api/v2/laps', {'station_id': 'ST01'})
        self.assertEqual(code, 404)
        self.assertIn('field-only', result['error'])
        self.assertEqual(self.send_channel()[0], 200)

    def test_field_ui_static_routes_are_additive(self):
        from server.app import STATIC
        self.assertEqual(STATIC.get('/v2/field'), 'field-v2.html')
        self.assertEqual(STATIC.get('/field-v2.js'), 'field-v2.js')
        self.assertEqual(STATIC['/admin'], 'admin.html')
        self.assertEqual(STATIC['/tv'], 'tv.html')
        self.assertEqual(self.call('GET', '/v2/field', token=None)[0], 200)
        self.assertEqual(self.call('GET', '/field-v2.js', token=None)[0], 200)

    def test_native_validity_is_native_channel_evidence_not_required_on_penalty(self):
        for index, validity in enumerate([True, False, None]):
            lap = 'V' + str(index)
            native = self.payload('native', lap)
            if validity is None:
                native.pop('native_valid')
            else:
                native['native_valid'] = validity
            self.assertEqual(self.send_channel('native', native)[0], 200)
            penalty = self.payload('penalty', lap)
            penalty.pop('native_valid')
            code, result = self.send_channel('penalty', penalty)
            self.assertEqual(code, 200)
            self.assertEqual(result['corrected_preview_ms'], 62000)
            if validity is not True:
                self.assertIn('native_validity_unconfirmed', result['review_reasons'])

    def test_altered_retry_with_malformed_payload_still_marks_original_for_review(self):
        self.send_channel()
        self.send_channel('penalty')
        malformed = self.payload()
        malformed['raw_ms'] = -1
        self.assertEqual(self.send_channel('native', malformed)[0], 409)
        row = self.rows()[0]
        self.assertEqual(row['conflicts'][0]['payload'], malformed)
        self.assertIsNone(row['corrected_preview_ms'])

    def test_authentication_opt_in_and_admin_isolation(self):
        for channel in ('native', 'penalty'):
            path = '/api/v2/' + channel + '-laps'
            for token, expected in [(None, 401), ('wrong', 401), ('other', 403), ('admin', 403)]:
                self.assertEqual(self.call('POST', path, self.payload(channel), token)[0], expected)
            self.store.penalties_v2_enabled = False
            self.assertEqual(self.send_channel(channel)[0], 404)
            self.store.penalties_v2_enabled = True
        for path in ('field-laps', 'field-export.json', 'field-export.csv'):
            self.assertEqual(self.call('GET', '/api/admin/v2/' + path, token=None)[0], 401)
            self.assertEqual(self.call('GET', '/api/admin/v2/' + path)[0], 403)
        self.assertEqual(self.rows(), [])

    def test_channel_identity_cannot_be_replaced_with_a_new_message_id(self):
        native = self.payload()
        self.assertEqual(self.send_channel('native', native)[0], 200)
        replacement = dict(native, message_id='different-message')
        self.assertEqual(self.send_channel('native', replacement)[0], 409)
        code, retry = self.send_channel('native', native)
        self.assertEqual(code, 200)
        self.assertTrue(retry['duplicate'])
        self.assertEqual(retry['native_payload'], native)
        self.assertEqual(len(self.rows()), 1)

    def test_penalty_first_bad_raw_never_blocks_the_independent_native_record(self):
        penalty = self.payload('penalty')
        penalty['raw_ms'] = 61000
        self.assertEqual(self.send_channel('penalty', penalty)[0], 200)
        code, result = self.send_channel()
        self.assertEqual(code, 200)
        self.assertTrue(result['native_received'])
        self.assertEqual(result['raw_ms'], 60000)
        self.assertEqual(result['native_payload'], self.payload())
        self.assertIsNone(result['corrected_preview_ms'])
        self.assertEqual(result['conflicts'][0]['payload'], penalty)
        self.assertTrue(self.send_channel()[1]['duplicate'])

    def test_historical_assignment_upload_preserves_native_and_keeps_review_required(self):
        self.assertEqual(self.call('POST', '/api/admin/pilots', {'pilot_id': 'P2', 'name': 'Second'}, 'admin')[0], 200)
        self.assertEqual(self.call('POST', '/api/admin/assignments',
                                  {'station_id': 'ST01', 'session_id': 'S1', 'pilot_id': 'P2'}, 'admin')[0], 200)
        self.assertEqual(self.send_channel()[0], 200)
        code, result = self.send_channel('penalty')
        self.assertEqual(code, 200)
        self.assertTrue(result['native_received'])
        self.assertEqual(result['pilot_id'], 'P1')
        self.assertIn('historical_assignment', result['review_reasons'])
        self.assertEqual(result['corrected_preview_ms'], 62000)
        unknown = self.payload('native', 'Unknown')
        unknown['pilot_id'] = 'Unassigned'
        self.assertEqual(self.send_channel('native', unknown)[0], 409)
        self.assertEqual(len(self.rows()), 1)

    def test_field_tables_have_a_new_schema_version_and_survive_backup_restore(self):
        with self.store.connect() as db:
            self.assertGreaterEqual(db.execute('PRAGMA user_version').fetchone()[0], 4)
        self.send_channel()
        self.send_channel('penalty')
        backup = self.store.backup()
        from server.app import restore
        restored_path = Path(self.tmp.name) / 'restored.sqlite'
        restore(restored_path, backup)
        from server import field_v2
        restored_store = Store(restored_path, penalties_v2_enabled=True)
        self.assertEqual(field_v2.list_laps(restored_store), self.rows())

    def test_native_channel_persists_unknown_penalties_across_server_restart(self):
        code, accepted = self.send_channel()
        self.assertEqual(code, 200)
        self.assertEqual(accepted['status'], 'PENALTIES_PENDING')
        for field in ('penalty_ms', 'final_ms', 'corrected_ms', 'corrected_preview_ms'):
            self.assertIsNone(accepted[field])
        self.stop()
        self.store = Store(self.path, penalties_v2_enabled=True)
        self.start()
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['native_received'])
        self.assertFalse(rows[0]['penalty_received'])
        self.assertEqual(rows[0]['native_payload'], self.payload())
        self.assertTrue(self.send_channel()[1]['duplicate'])


if __name__ == '__main__':
    unittest.main()
