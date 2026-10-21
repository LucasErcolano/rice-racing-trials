"""Lua client tests with explicit CSP API doubles (not a CSP runtime test)."""
import json
import unittest
from pathlib import Path
from lupa import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


def harness(storage=None, pre_exec=None):
    lua = LuaRuntime(unpack_returned_tuples=True)
    storage = {} if storage is None else storage
    def to_lua(obj):
        if isinstance(obj, dict):
            return lua.table_from({k: to_lua(v) for k, v in obj.items()})
        if isinstance(obj, list):
            return lua.table_from([to_lua(v) for v in obj])
        return obj
    def to_python(obj):
        if hasattr(obj, 'items'):
            keys = list(obj.keys())
            if not keys: return []  # rxi-style JSON Lua may encode empty table as array
            if keys and all(isinstance(k, int) for k in keys):
                return [to_python(obj[k]) for k in range(1, len(keys) + 1)]
            return {str(k): to_python(v) for k, v in obj.items()}
        return obj
    lua.globals().JSON = lua.table_from({'parse': lambda s: to_lua(json.loads(s)), 'stringify': lambda o: json.dumps(to_python(o), separators=(',', ':'))})
    class Storage:
        def __getitem__(self, key): return storage.get(key)
        def __setitem__(self, key, value):
            if value is None: storage.pop(key, None)  # assigning nil deletes the key
            else: storage[key] = value
    position = {'x': 0, 'z': 1}
    lua.globals().ac = lua.table_from({'storage': Storage(), 'getCar': lambda n: to_lua({'position': position})})
    lua.globals().ui = lua.table_from({'text': lambda s: None})
    class Files:  # io double over the same dict, so a "restart" sees the same disk
        def save(self, p, d): storage['file:' + p] = d; return True
        def load(self, p, fallback=None): return storage.get('file:' + p, fallback)
        def exists(self, p): return 'file:' + p in storage
        def delete(self, p): return storage.pop('file:' + p, None) is not None
    lua.globals().PYFS = Files()
    lua.execute("""
      io = {save=function(p, d) return PYFS.save(p, d) end, load=function(p, f) return PYFS.load(p, f) end,
            fileExists=function(p) return PYFS.exists(p) end, deleteFile=function(p) return PYFS.delete(p) end,
            createDir=function() return true end}
      ac.FolderID = {Root='root'}
      ac.getFolder = function() return 'C:/AC' end
    """)
    lua.globals().script = lua.table_from({})
    requests = []
    def request(method):
        def send(url, headers, *args):
            body, callback = args if method == 'POST' else (None, args[0])
            requests.append((method, url, to_python(headers), body, callback))
        return send
    lua.globals().web = lua.table_from({'get': request('GET'), 'post': request('POST')})
    lua.globals().package.path = str(ROOT / '?.lua') + ';' + lua.globals().package.path
    if pre_exec: pre_exec(lua)
    lua.execute((ROOT / 'RiceTrials.lua').read_text())
    def reply(index, data, code=200, err=None):
        requests[index][4](err, to_lua({'status': code, 'body': json.dumps(data)}))
    return lua, storage, position, requests, reply, to_lua


STATE_DIR = 'C:/AC/apps/lua/RiceTrials/state'


def persisted(storage):
    """Client state from the newest valid journal slot (what a restarted VM would load)."""
    best = None
    for slot in 'ab':
        raw = storage.get('file:%s/queue.%s.json' % (STATE_DIR, slot))
        if raw:
            env = json.loads(raw)
            if best is None or env['seq'] > best['seq']: best = env
    return json.loads(best['items'])[0] if best else None


def config(to_lua):
    gate = lambda z: {'a': {'x': -2, 'z': z}, 'b': {'x': 2, 'z': z}, 'direction': -1}
    return to_lua({'verified': True, 'event': 'endurance', 'asset_hash': 'a' * 64,
                   'track_id': 'FSAEJ_Endurance', 'route': {'start': gate(0), 'checkpoints': [gate(-5)]}})


def drive_lap(lua, position):
    def move(z):
        position['z'] = z
        lua.globals().script.update(.1)
    move(1)
    move(-1)  # outlap start
    move(-4)
    move(-6)
    move(1)   # teleport back: no fabricated crossing
    move(-1)  # outlap end, timed lap starts
    move(-4)
    move(-6)
    move(1)
    move(-1)  # finish


class Network(unittest.TestCase):
    def test_queue_requires_assignment_and_ack_then_retry_is_identical(self):
        lua, storage, position, requests, reply, to_lua = harness()
        script = lua.globals().script
        self.assertTrue(script.connect('http://127.0.0.1:8080', 'station-1', 'secret'))
        self.assertTrue(script.configure(config(to_lua)))
        drive_lap(lua, position)
        self.assertFalse(any(r[0] == 'POST' and r[1].endswith('/api/laps') for r in requests))
        reply(0, {'station_id': 'station-1', 'session_id': 'session-1', 'pilot_id': 'pilot-1'})
        drive_lap(lua, position)
        posts = [r for r in requests if r[1].endswith('/api/laps')]
        self.assertEqual(len(posts), 1)
        payload = json.loads(posts[0][3])
        self.assertEqual(payload['status'], 'PENDING_REVIEW')
        self.assertEqual(payload['events'], {})  # no invented cone count
        self.assertEqual((payload['session_id'], payload['pilot_id']), ('session-1', 'pilot-1'))
        posts[0][4]('offline', None)
        self.assertIn('entries', persisted(storage))
        script.retryPending()
        posts = [r for r in requests if r[1].endswith('/api/laps')]
        self.assertEqual(posts[0][3], posts[1][3])
        reply(len(requests)-1, {'lap_id': payload['lap_id'], 'status': 'PENDING_REVIEW'})
        self.assertEqual(len(persisted(storage)['entries']), 0)

    def test_corrupt_storage_blocks_configure_and_does_not_overwrite(self):
        storage = {'rice_trials_pending_review_v1': '{bad'}
        lua, _, _, requests, _, to_lua = harness(storage)
        self.assertFalse(lua.globals().script.configure(config(to_lua)))
        self.assertEqual(storage['rice_trials_pending_review_v1'], '{bad')
        self.assertFalse(requests)

    def test_ambiguous_ack_and_assignment_change_keep_frozen_payload(self):
        lua, storage, position, requests, reply, to_lua = harness()
        script = lua.globals().script
        script.connect('http://127.0.0.1:8080', 'station-1', 'secret')
        script.configure(config(to_lua))
        reply(0, {'station_id': 'station-1', 'session_id': 's1', 'pilot_id': 'p1'})
        drive_lap(lua, position)
        post = next(r for r in requests if r[1].endswith('/api/laps'))
        payload = json.loads(post[3])
        post[4](None, to_lua({'status': 200, 'body': json.dumps({'lap_id': 'other', 'status': 'PENDING_REVIEW'})}))
        self.assertEqual(len(persisted(storage)['entries']), 1)
        script.refreshAssignment()
        reply(len(requests)-1, {'station_id': 'station-1', 'session_id': 's2', 'pilot_id': 'p2'})
        script.retryPending()
        self.assertEqual(json.loads(requests[-1][3]), payload)

    def test_blank_storage_is_corruption_not_new_queue(self):
        storage = {'rice_trials_pending_review_v1': ''}
        lua, _, _, requests, _, to_lua = harness(storage)
        self.assertFalse(lua.globals().script.configure(config(to_lua)))
        self.assertFalse(lua.globals().script.connect('http://127.0.0.1:8080', 'station-1', 'secret'))
        self.assertEqual(storage['rice_trials_pending_review_v1'], '')
        self.assertFalse(requests)

    def test_persisted_queue_cannot_be_sent_from_other_station(self):
        lua, storage, position, requests, reply, to_lua = harness()
        script = lua.globals().script
        script.connect('http://127.0.0.1:8080', 'station-1', 'secret')
        script.configure(config(to_lua))
        reply(0, {'station_id': 'station-1', 'session_id': 's1', 'pilot_id': 'p1'})
        drive_lap(lua, position)
        self.assertEqual(len(persisted(storage)['entries']), 1)
        lua2, _, _, requests2, _, _ = harness(storage)
        self.assertFalse(lua2.globals().script.connect('http://127.0.0.1:8080', 'station-2', 'secret2'))
        self.assertFalse(requests2)

    def test_teleport_does_not_complete_outlap(self):
        lua, storage, position, requests, reply, to_lua = harness()
        script = lua.globals().script
        script.connect('http://127.0.0.1:8080', 'station-1', 'secret')
        script.configure(config(to_lua))
        reply(0, {'station_id': 'station-1', 'session_id': 's1', 'pilot_id': 'p1'})
        position['z'] = 1; script.update(.1)
        position['z'] = -1; script.update(.1)
        position['z'] = -500; script.update(.1)  # impossible displacement resets the outlap
        position['z'] = -4; script.update(.1)
        position['z'] = -6; script.update(.1)
        position['z'] = 1; script.update(.1)
        position['z'] = -1; script.update(.1)
        self.assertFalse(any(r[1].endswith('/api/laps') for r in requests))


class ConeCandidates(unittest.TestCase):
    """Cone-box candidates ride in payload.detector for the reviewer; never in events/penalties."""
    def cones(self, to_lua):
        return to_lua({'version': 'cone-boxes-v1', 'half_width_m': 0.705, 'half_length_m': 1.48, 'shrink_m': 0.15,
                       'cone_radius_m': 0.125, 'min_depth_m': 0.03,
                       'cones': [{'id': 'HIT', 'x': 0.0, 'z': -2.0}, {'id': 'NEAR', 'x': 1.5, 'z': -2.0},
                                 {'id': 'GRAZE', 'x': 0.78, 'z': -3.0}]})

    def run_lap(self):
        lua, storage, position, requests, reply, to_lua = harness()
        lua.execute("local p = ac.getCar; ac.getCar = function(n) local c = p(n); c.look = {x=0, y=0, z=-1}; return c end")
        script = lua.globals().script
        script.connect('http://127.0.0.1:8080', 'station-1', 'secret')
        script.configure(config(to_lua))
        self.assertTrue(script.configureCones(self.cones(to_lua)))
        reply(0, {'station_id': 'station-1', 'session_id': 's1', 'pilot_id': 'p1'})
        drive_lap(lua, position)
        post = next(r for r in requests if r[1].endswith('/api/laps'))
        return json.loads(post[3])

    def test_candidate_reaches_server_payload_but_not_penalties(self):
        payload = self.run_lap()
        self.assertEqual(payload['status'], 'PENDING_REVIEW')
        self.assertEqual(payload['events'], {})
        det = payload['detector']
        self.assertEqual(det['cone_source'], 'geometric_candidate_uncertified')
        self.assertEqual(det['cone_candidates'], ['HIT'])  # outlap contact is dropped; near miss and shrunk graze are clear
        self.assertEqual(det['cone_candidate_count'], 1)

    def test_invalid_cone_config_is_rejected_and_laps_have_no_detector(self):
        lua, storage, position, requests, reply, to_lua = harness()
        script = lua.globals().script
        script.connect('http://127.0.0.1:8080', 'station-1', 'secret')
        script.configure(config(to_lua))
        self.assertFalse(script.configureCones(to_lua({'version': 'bad'})))
        reply(0, {'station_id': 'station-1', 'session_id': 's1', 'pilot_id': 'p1'})
        drive_lap(lua, position)
        payload = json.loads(next(r for r in requests if r[1].endswith('/api/laps'))[3])
        self.assertNotIn('detector', payload)

if __name__ == '__main__': unittest.main()
