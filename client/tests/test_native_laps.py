"""Native AC lap capture: lapCount changes become confirmed local drafts (never sent to the server)."""
import json
import unittest
from pathlib import Path
from lupa import LuaRuntime
from test_network import harness, config, drive_lap

ROOT = Path(__file__).resolve().parents[1]


class Native(unittest.TestCase):
    def runtime(self, disk, fail_save=False):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.execute("package.path = '%s/?.lua;' .. package.path" % ROOT.as_posix())
        wrap = lua.eval('function(f) return function(...) return f(...) end end')
        def save(p, d):
            if fail_save: return False
            disk[p] = d; return True
        def to_lua(o):
            if isinstance(o, dict): return lua.table_from({k: to_lua(v) for k, v in o.items()})
            if isinstance(o, list): return lua.table_from([to_lua(v) for v in o])
            return o
        def to_py(o):
            if hasattr(o, 'items'):
                ks = list(o.keys())
                if not ks: return []
                if all(isinstance(k, int) for k in ks): return [to_py(o[k]) for k in range(1, len(ks) + 1)]
                return {str(k): to_py(v) for k, v in o.items()}
            return o
        io = lua.table_from({'save': wrap(save), 'load': wrap(lambda p, f=None: disk.get(p, f)),
                             'createDir': wrap(lambda p: True)})
        js = lua.table_from({'encode': wrap(lambda v: json.dumps(to_py(v), separators=(',', ':'))),
                             'decode': wrap(lambda t: to_lua(json.loads(t)))})
        jm = lua.eval("(require('journal'))")
        journal = jm.new(lua.table_from({'dir': '/state', 'name': 'native_laps'}), lua.table_from({'io': io, 'json': js}))
        nm = lua.eval("(require('native_laps'))")
        events = []
        ctx = lua.eval("function() return {station_id='ST1', session_id='S1', pilot_id='P1'} end")
        o = nm.new(lua.table_from({'journal': journal, 'nonce': 'nn', 'context': ctx,
                                   'on_event': wrap(lambda k, t: events.append(k))}))
        return lua, o, events

    def car(self, lua, lap_count, ms=None, valid=True):
        t = {'lapCount': lap_count}
        if ms is not None: t['previousLapTimeMs'] = ms
        if valid is not None: t['isLastLapValid'] = valid
        return lua.table_from(t)

    def test_lapcount_change_creates_confirmed_record(self):
        disk = {}
        lua, o, ev = self.runtime(disk)
        o.observe(o, self.car(lua, 0), 0.1)             # baseline, not a lap
        self.assertEqual(o.stats(o).count, 0)
        o.observe(o, self.car(lua, 1, 61234, True), 0.1)
        d = o.last(o)
        self.assertEqual((d.time_ms, d.valid, d.lap_count, d.session_id), (61234, True, 1, 'S1'))
        self.assertEqual(o.stats(o).state, 'ok')
        self.assertTrue(any(k.endswith('native_laps.a.json') or k.endswith('native_laps.b.json') for k in disk))
        self.assertIn('native_lap', ev)

    def test_same_count_does_not_duplicate(self):
        lua, o, _ = self.runtime({})
        for _ in range(5): o.observe(o, self.car(lua, 2, 60000), 0.1)
        o.observe(o, self.car(lua, 3, 59000), 0.1)
        for _ in range(5): o.observe(o, self.car(lua, 3, 59000), 0.1)
        self.assertEqual(o.stats(o).count, 1)     # first reading = baseline (2), only 2 -> 3 is a lap

    def test_survives_restart_without_duplicating(self):
        disk = {}
        lua, o, _ = self.runtime(disk)
        o.observe(o, self.car(lua, 0), 0.1); o.observe(o, self.car(lua, 1, 61000), 0.1)
        lua2, o2, _ = self.runtime(disk)           # AC / app restart
        self.assertEqual(o2.stats(o2).count, 1)
        o2.observe(o2, self.car(lua2, 1, 61000), 0.1)   # same counter after reload: baseline only
        self.assertEqual(o2.stats(o2).count, 1)
        o2.observe(o2, self.car(lua2, 2, 62000), 0.1)
        self.assertEqual(o2.stats(o2).count, 2)

    def test_counter_reset_is_reference_not_lap(self):
        lua, o, _ = self.runtime({})
        o.observe(o, self.car(lua, 0), 0.1); o.observe(o, self.car(lua, 3, 60000), 0.1)
        n = o.stats(o).count
        o.session_start(o)
        o.observe(o, self.car(lua, 0, 60000), 0.1)      # session restart: counter back to 0
        self.assertEqual(o.stats(o).count, n)
        o.observe(o, self.car(lua, 1, 58000), 0.1)
        self.assertEqual(o.stats(o).count, n + 1)
        self.assertEqual(o.last(o).time_ms, 58000)

    def test_counter_drop_without_session_event_is_also_reference(self):
        lua, o, _ = self.runtime({})
        o.observe(o, self.car(lua, 4), 0.1); o.observe(o, self.car(lua, 5, 60000), 0.1)
        o.observe(o, self.car(lua, 0, 60000), 0.1)
        self.assertEqual(o.stats(o).count, 1)

    def test_failed_write_stays_in_memory_and_retries(self):
        disk = {}
        lua, o, _ = self.runtime(disk, fail_save=True)
        o.observe(o, self.car(lua, 0), 0.1); o.observe(o, self.car(lua, 1, 60000), 0.1)
        self.assertEqual((o.stats(o).count, o.stats(o).state), (1, 'unsaved'))

    def test_invalid_and_missing_values_are_recorded_honestly(self):
        lua, o, _ = self.runtime({})
        o.observe(o, self.car(lua, 0), 0.1)
        o.observe(o, self.car(lua, 1, 60500, False), 0.1)
        self.assertIs(o.last(o).valid, False)
        o.observe(o, self.car(lua, 2, None, None), 0.1)
        self.assertIsNone(o.last(o).time_ms)

    def test_missing_lapcount_reports_unavailable(self):
        lua, o, _ = self.runtime({})
        o.observe(o, lua.table_from({'speedKmh': 10}), 0.1)
        s = o.stats(o)
        self.assertEqual((s.count, s.lapcount_available), (0, False))

    def test_reconciled_with_route_lap_once(self):
        lua, o, ev = self.runtime({})
        o.observe(o, self.car(lua, 0), 0.1); o.observe(o, self.car(lua, 1, 61000), 0.1)
        o.note_route_lap(o, 'L1', 60400, 1)
        self.assertEqual(o.last(o).rt_lap_id, 'L1')
        self.assertEqual(o.stats(o).unmatched, 0)
        r = o.result_for(o, 'L1')
        self.assertEqual((r.state, r.raw_ms, r.lap_count), ('confirmed', 61000, 1))
        o.note_route_lap(o, 'L2', 60500, 1)       # same counter already used: nothing to pair
        self.assertEqual(o.stats(o).count, 1)
        self.assertEqual(o.result_for(o, 'L2').state, 'pending')

    def test_identity_pairs_the_n14_divergence_that_2s_tolerance_rejected(self):
        # Real VM lap n-14: AC 385014 ms, route 379292 ms (5722 ms of render stalls).
        lua, o, ev = self.runtime({})
        o.observe(o, self.car(lua, 13), 0.1); o.observe(o, self.car(lua, 14, 385014), 0.1)
        o.note_route_lap(o, 'L14', 379292, 14)
        self.assertEqual((o.last(o).rt_lap_id, o.last(o).rt_divergence_ms), ('L14', 5722))
        self.assertIn('native_reconciled', ev)

    def test_far_or_unidentified_times_are_not_reconciled(self):
        lua, o, ev = self.runtime({})
        o.observe(o, self.car(lua, 0), 0.1); o.observe(o, self.car(lua, 1, 61000), 0.1)
        o.note_route_lap(o, 'L0', 61000, None)    # no native phase: never paired, even if equal
        self.assertEqual(o.stats(o).unmatched, 1)
        self.assertEqual(o.result_for(o, 'L0').state, 'unavailable')
        o.note_route_lap(o, 'L1', 61000, 2)       # another counter: identity wins over time
        self.assertEqual(o.stats(o).unmatched, 1)
        o.note_route_lap(o, 'L9', 30000, 1)       # same counter, 31 s apart: conflict, not pair
        self.assertEqual(o.stats(o).unmatched, 1)
        self.assertEqual(o.last(o).rt_conflict, 'L9')
        self.assertIn('native_conflict', ev)


class InApp(unittest.TestCase):
    def test_app_records_native_lap_and_never_posts_it(self):
        storage = {}
        def pre(lua):
            lua.execute("NATIVE = {lapCount=0, previousLapTimeMs=0, isLastLapValid=true, position={x=0,z=1}}")
        lua, storage, position, requests, reply, to_lua = harness(storage, pre)
        lua.execute("ac.getCar = function() NATIVE.position = {x=0, z=POSZ or 1}; return NATIVE end")
        script = lua.globals().script
        script.connect('http://127.0.0.1:8080', 'station-1', 'secret')
        script.configure(config(to_lua))
        reply(0, {'station_id': 'station-1', 'session_id': 's1', 'pilot_id': 'p1'})
        script.update(.1)
        lua.execute("NATIVE.lapCount = 1; NATIVE.previousLapTimeMs = 63210")
        script.update(.1)
        self.assertTrue(any('native_laps' in k for k in storage))
        n_posts = [r for r in requests if r[0] == 'POST' and r[1].endswith('/api/laps')]
        self.assertEqual(n_posts, [])
        # restart the whole app on the same disk: the draft is still there
        lua2, *_ = harness(storage, pre)
        lua2.execute("NATIVE.lapCount = 1")
        texts = []
        lua2.globals().ui = lua2.table_from({'text': lambda s: texts.append(s)})
        lua2.execute("ac.getCar = function() return NATIVE end")
        lua2.globals().script.windowPenalties(.1)
        self.assertTrue(any('AC native laps: 1 saved' in t for t in texts), texts)


if __name__ == '__main__':
    unittest.main()
