import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from server.app import Store, make_handler
from tests.test_penalties_v2 import config, sample


class PenaltiesV2ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'trials.sqlite', scoring_armed=True,
                           penalties_v2_enabled=True)
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(
            self.store, 'admin-secret', {'ST01': 'station-secret'}))
        self.worker = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.worker.start()
        for path, value in [('/api/admin/sessions', {'session_id': 'S1', 'name': 'Morning'}),
                            ('/api/admin/pilots', {'pilot_id': 'P1', 'name': 'Pilot One'}),
                            ('/api/admin/assignments', {'station_id': 'ST01', 'session_id': 'S1', 'pilot_id': 'P1'})]:
            self.assertEqual(self.call('POST', path, value, 'admin-secret')[0], 200)

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.worker.join()
        self.tmp.cleanup()

    def call(self, method, path, data=None, token=None):
        connection = HTTPConnection('127.0.0.1', self.http.server_port)
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        connection.request(method, path, json.dumps(data).encode() if data is not None else None, headers)
        response = connection.getresponse()
        body, code = response.read(), response.status
        connection.close()
        return code, json.loads(body)

    def install_config(self):
        settings = {'enable_cones': True, 'enable_off_track': True, 'enable_slalom': True,
                    'cone_penalty_ms': 2000, 'off_course_penalty_ms': 20000, 'slalom_penalty_ms': 20000}
        config_doc = config()
        result = self.call('POST', '/api/admin/v2/config', {
            'station_id': 'ST01', 'track_config': 'synthetic-test', 'reference': 'survey-test-1',
            'config': config_doc, 'settings': settings}, 'admin-secret')
        self.assertEqual(result[0], 200)

    def payload(self, lap_id='L1'):
        return {'protocol_version': 2, 'station_id': 'ST01', 'session_id': 'S1', 'pilot_id': 'P1',
                'lap_id': lap_id, 'raw_ms': 60000, 'track_config': 'synthetic-test',
                'trace': {'protocol': 'rice-trajectory-v2',
                          'lap': {'station_id': 'ST01', 'session_id': 'S1', 'lap_id': lap_id, 'raw_ms': 60000},
                          'samples': [sample(0, 0), sample(1000, 10)]}}

    def test_v2_submission_persists_result_and_replay_is_deterministic(self):
        self.install_config()
        code, accepted = self.call('POST', '/api/v2/laps', self.payload(), 'station-secret')
        self.assertEqual(code, 200)
        self.assertEqual((accepted['status'], accepted['corrected_ms'], accepted['penalty_ms']),
                         ('VALID', 62000, 2000))
        self.assertFalse(accepted['duplicate'])
        self.assertEqual(self.call('POST', '/api/v2/laps', self.payload(), 'station-secret')[1]['duplicate'], True)
        code, replay = self.call('GET', '/api/admin/v2/replay?station_id=ST01&lap_id=L1', token='admin-secret')
        self.assertEqual(code, 200)
        self.assertEqual(replay['result']['final_ms'], 62000)
        self.assertTrue(replay['replay_matches'])
        self.assertEqual(replay['result'], accepted['analysis'])

    def test_v2_ingestion_is_disabled_by_default(self):
        self.store.penalties_v2_enabled = False
        self.assertEqual(self.call('POST', '/api/v2/laps', self.payload(), 'station-secret')[0], 404)
        self.assertEqual(self.call('POST', '/api/laps', {'protocol_version': 2}, 'station-secret')[0], 400)

    def test_v2_admin_penalty_adjustment_is_audited_and_does_not_promote_pending_lap(self):
        self.store.scoring_armed = False
        self.install_config()
        code, accepted = self.call('POST', '/api/v2/laps', self.payload(), 'station-secret')
        self.assertEqual(code, 200)
        self.assertEqual(accepted['status'], 'PENDING_REVIEW')
        self.assertIsNone(accepted['corrected_ms'])
        code, adjusted = self.call('POST', '/api/admin/v2/penalties', {
            'station_id': 'ST01', 'lap_id': 'L1',
            'counts': {'touched_cones': 2, 'off_course_events': 1, 'missed_slaloms': 0},
            'reviewer': 'marshal', 'reason': 'verified against review video'}, 'admin-secret')
        self.assertEqual(code, 200)
        self.assertEqual((adjusted['status'], adjusted['corrected_ms'], adjusted['corrected_preview_ms']),
                         ('PENDING_REVIEW', None, 84000))
        code, audits = self.call('GET', '/api/admin/v2/reviews?station_id=ST01&lap_id=L1', token='admin-secret')
        self.assertEqual(code, 200)
        self.assertEqual(len(audits['reviews']), 1)
        self.assertEqual(audits['reviews'][0]['counts'],
                         {'touched_cones': 2, 'off_course_events': 1, 'missed_slaloms': 0})
        self.assertEqual(audits['reviews'][0]['corrected_preview_ms'], 84000)

    def test_v2_config_and_replay_are_admin_only(self):
        settings = {'enable_cones': True, 'enable_off_track': True, 'enable_slalom': True,
                    'cone_penalty_ms': 2000, 'off_course_penalty_ms': 20000, 'slalom_penalty_ms': 20000}
        body = {'station_id': 'ST01', 'track_config': 'synthetic-test', 'reference': 'survey-test-1',
                'config': config(), 'settings': settings}
        self.assertEqual(self.call('POST', '/api/admin/v2/config', body)[0], 401)
        self.assertEqual(self.call('POST', '/api/admin/v2/config', body, 'station-secret')[0], 403)
        self.install_config()
        self.call('POST', '/api/v2/laps', self.payload(), 'station-secret')
        self.assertEqual(self.call('GET', '/api/admin/v2/replay?station_id=ST01&lap_id=L1')[0], 401)


if __name__ == '__main__':
    unittest.main()
