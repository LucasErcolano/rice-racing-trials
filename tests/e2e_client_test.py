"""Real HTTP bridge from LuaRuntime callback API to Python SQLite server and leaderboard."""
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'client' / 'tests'))
sys.path.insert(0, str(ROOT))
from test_network import harness, config, drive_lap, persisted
from server.app import Store, make_handler


class EndToEnd(unittest.TestCase):
    def test_lua_http_sqlite_duplicate_retry_and_leaderboard(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'laps.sqlite')
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(store, 'admin-key', {'station-1': 'station-key'}))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = 'http://127.0.0.1:' + str(server.server_port)
            def http(method, url, headers, body=None):
                req = urllib.request.Request(url, data=body.encode() if body is not None else None,
                    headers=headers, method=method)
                try:
                    with urllib.request.urlopen(req, timeout=3) as response:
                        return response.status, response.read().decode()
                except urllib.error.HTTPError as error:
                    return error.code, error.read().decode()
            def admin(path, payload):
                code, response = http('POST', base + path,
                    {'Authorization': 'Bearer admin-key', 'Content-Type': 'application/json'}, json.dumps(payload))
                self.assertEqual(code, 200, response)
            try:
                admin('/api/admin/sessions', {'session_id': 'session-1', 'name': 'Practice'})
                admin('/api/admin/pilots', {'pilot_id': 'pilot-1', 'name': 'Pilot'})
                admin('/api/admin/assignments', {'station_id': 'station-1', 'session_id': 'session-1', 'pilot_id': 'pilot-1'})
                lua, storage, position, requests, reply, to_lua = harness()
                bridge = []
                lose_first_ack = [True]
                def invoke(method, convert):
                    def request(url, headers, *args):
                        body, callback = args if method == 'POST' else (None, args[0])
                        def native(value):
                            if hasattr(value, 'items'): return {str(k): native(v) for k, v in value.items()}
                            return value
                        code, response = http(method, url, native(headers), body)
                        bridge.append((method, url, code, body))
                        if url.endswith('/api/laps') and lose_first_ack[0]:
                            lose_first_ack[0] = False
                            callback('simulated lost response after commit', None)
                        else:
                            callback(None, convert({'status': code, 'body': response}))
                    return request
                lua.globals().web = lua.table_from({'get': invoke('GET', to_lua), 'post': invoke('POST', to_lua)})
                script = lua.globals().script
                self.assertTrue(script.connect(base, 'station-1', 'station-key'))
                self.assertTrue(script.configure(config(to_lua)))
                drive_lap(lua, position)
                self.assertEqual(len(store.laps('session-1')), 1)
                saved = persisted(storage)
                self.assertEqual(len(saved['entries']), 1)
                lap_id = saved['entries'][0]['id']
                assigned_revision = store.assignment('station-1')['assignment_revision']
                self.assertEqual(json.loads(saved['entries'][0]['payload'])['assignment_revision'], assigned_revision)
                # Reassign while the first acknowledgment was lost; retry must keep the old identity.
                admin('/api/admin/pilots', {'pilot_id': 'pilot-2', 'name': 'Next'})
                admin('/api/admin/assignments', {'station_id': 'station-1', 'session_id': 'session-1', 'pilot_id': 'pilot-2'})
                # Restart the Lua VM with persisted storage; resend exactly the same payload.
                lua2, _, _, _, _, to_lua2 = harness(storage)
                lua2.globals().web = lua2.table_from({'get': invoke('GET', to_lua2), 'post': invoke('POST', to_lua2)})
                self.assertTrue(lua2.globals().script.connect(base, 'station-1', 'station-key'))
                self.assertEqual(len(store.laps('session-1')), 1)
                self.assertEqual(store.laps('session-1')[0]['lap_id'], lap_id)
                self.assertEqual(len(persisted(storage)['entries']), 0)
                laps = [item for item in bridge if item[1].endswith('/api/laps')]
                self.assertEqual(len(laps), 2)
                self.assertEqual(laps[0][3], laps[1][3])
                self.assertTrue(any('/api/station/heartbeat' in item[1] for item in bridge))
                code, body = http('GET', base + '/api/leaderboard?session_id=session-1', {})
                self.assertEqual(code, 200)
                board = json.loads(body)
                self.assertEqual(board['leaders'], [])  # provisional, never scored
                self.assertEqual(board['laps'][0]['status'], 'PENDING_REVIEW')
                self.assertIsNone(board['laps'][0]['corrected_ms'])
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=3)

if __name__ == '__main__': unittest.main()
