"""Native HUD timing: route clock on AC physics time, same-lap identity, LAST/BEST/delta.

Regression of the real VM lap n-14 (session RC2F-VM-01, field.sqlite pulled 2026-10-22):
AC previousLapTimeMs 385014, route raw 379292 (dt accumulator lost 5722 ms to render
stalls), 29 cones + 2 slalom gates = +90 s. Expected corrected time: 385014 + 90000 = 475014
(7:55.014) in every window and at /v2/field; never 379292 + 90000 = 469292 (7:49.292).
"""
import json
import sys
import unittest
from pathlib import Path
from lupa import LuaRuntime
from test_network import harness, config
from test_app_hud import STATION, capture_ui, lines

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

N14_NATIVE, N14_ROUTE, N14_PENALTY = 385014, 379292, 90000


def module(name):
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute("package.path = '%s/?.lua;' .. package.path" % ROOT.as_posix())
    return lua, lua.eval("(require('%s'))" % name)


class LapClock(unittest.TestCase):
    def setUp(self):
        self.lua, self.m = module('lap_clock')

    def car(self, lc, lt, plt=None):
        t = {'lapCount': lc, 'lapTimeMs': lt}
        if plt is not None: t['previousLapTimeMs'] = plt
        return self.lua.table_from(t)

    def test_render_stall_follows_ac_physics_not_frame_dt(self):
        c = self.m.new()
        total = 0.0
        lt = 0
        c.step(c, 0.016, self.car(0, lt))                      # baseline frame: dt
        for i in range(100):
            stall = i == 50
            lt += 28000 if stall else 16                     # a 28 s stall (seen in diag1.jsonl)
            dt = 0.0333 if stall else 0.016                   # CSP hands a small dt after the stall
            s, src = c.step(c, dt, self.car(0, lt))
            total += s
            self.assertEqual(src, 'native')
        self.assertAlmostEqual(total * 1000, 28000 + 99 * 16, delta=0.5)
        e = c.since(c, self.lua.table_from({}))
        self.assertEqual(e.stall_frames, 1)
        self.assertLess(e.dt_minus_clock_ms, -27000)

    def test_native_line_crossing_uses_previous_lap_time(self):
        c = self.m.new()
        c.step(c, 0.016, self.car(4, 384990))
        s, src = c.step(c, 0.016, self.car(5, 10, plt=385014))
        self.assertEqual(src, 'native')
        self.assertAlmostEqual(s * 1000, 24 + 10, delta=0.01)

    def test_fallbacks_reset_jump_frozen_missing(self):
        c = self.m.new()
        c.step(c, 0.016, self.car(3, 5000))
        self.assertEqual(c.step(c, 0.02, self.car(0, 0))[1], 'dt')            # counter reset
        self.assertEqual(c.step(c, 0.02, self.car(0, 70000))[1], 'dt')        # implausible jump
        self.assertEqual(c.step(c, 0.02, self.lua.table_from({}))[1], 'dt')   # field missing
        c2 = self.m.new()
        c2.step(c2, 0.02, self.car(0, 0))
        srcs = [c2.step(c2, 0.02, self.car(0, 0))[1] for _ in range(30)]       # CSP never fills it
        self.assertEqual(srcs[-1], 'dt')
        c3 = self.m.new()
        c3.step(c3, 0.02, self.car(0, 100)); c3.step(c3, 0.02, self.car(0, 120))
        s, src = c3.step(c3, 0.02, self.car(0, 120))                           # frozen physics frame
        self.assertEqual((s, src), (0, 'native'))

    def test_reset_rebaselines(self):
        c = self.m.new()
        c.step(c, 0.016, self.car(0, 1000))
        c.reset(c)
        self.assertEqual(c.step(c, 0.016, self.car(0, 9000))[1], 'dt')


class LapSync(unittest.TestCase):
    def setUp(self):
        self.lua, self.m = module('lap_sync')

    def car(self, lc, lt):
        return self.lua.table_from({'lapCount': lc, 'lapTimeMs': lt})

    def native(self, **kw):
        return self.lua.table_from(kw)

    def test_n14_confirms_with_5722_ms_divergence(self):
        ph = self.m.phase(self.car(13, 30), 25)        # AC line 5 ms before the route line
        self.assertTrue(ph.synced)
        l = self.m.lap(N14_ROUTE, self.lua.table_from({'penalty_ms': N14_PENALTY}), ph, None)
        self.assertEqual((l.state, l.expected_lap_count, l.corrected_ms), ('pending', 14, N14_ROUTE + N14_PENALTY))
        self.assertTrue(self.m.apply(l, self.native(state='confirmed', raw_ms=N14_NATIVE, lap_count=14), 0.1))
        self.assertEqual((l.state, l.raw_ms, l.route_raw_ms), ('confirmed', N14_NATIVE, N14_ROUTE))
        self.assertEqual(l.divergence_ms, 5722)
        self.assertEqual(l.corrected_ms, 475014)
        self.assertEqual(l.penalty_ms, N14_PENALTY)        # penalties never folded into raw

    def test_identity_not_proximity(self):
        ph = self.m.phase(self.car(13, 0), 0)
        # A native time almost equal to the route time but of ANOTHER lap is a mismatch.
        l = self.m.lap(60000, None, ph, None)
        self.m.apply(l, self.native(state='confirmed', raw_ms=60001, lap_count=15), 0.1)
        self.assertEqual((l.state, l.reason, l.corrected_ms), ('mismatch', 'native_lap_count_mismatch', 60000))
        # Same counter but an implausible divergence is a contradiction, not a pairing.
        l2 = self.m.lap(60000, None, ph, None)
        self.m.apply(l2, self.native(state='confirmed', raw_ms=95000, lap_count=14), 0.1)
        self.assertEqual((l2.state, l2.reason), ('mismatch', 'route_native_divergence'))

    def test_unsynced_lines_never_pair(self):
        ph = self.m.phase(self.car(13, 42000), 10)     # AC line 42 s away: other line / outlap
        self.assertFalse(ph.synced)
        l = self.m.lap(60000, None, ph, None)
        self.assertEqual((l.state, l.reason), ('unmatched', 'native_line_phase_unsynced'))
        self.assertFalse(self.m.apply(l, self.native(state='confirmed', raw_ms=60000, lap_count=14), 0.1))
        self.assertEqual(self.m.lap(60000, None, None, None).reason, 'native_phase_unavailable')

    def test_late_native_counter_resyncs_once_inside_window(self):
        ph = self.m.phase(self.car(13, 61000), 5)       # route crossed first, AC counter not yet
        self.assertFalse(ph.synced)
        self.assertIsNone(self.m.resync(ph, self.car(13, 61030), 30))
        new = self.m.resync(ph, self.car(14, 8), 48)    # AC crossed 40 ms after the route line
        self.assertTrue(new.synced and new.late)
        self.assertEqual((new.lap_count, new.offset_ms), (14, -40))
        ph2 = self.m.phase(self.car(13, 61000), 5)
        self.assertIsNone(self.m.resync(ph2, self.car(13, 62600), 1600))
        self.assertTrue(ph2.closed)
        self.assertIsNone(self.m.resync(ph2, self.car(14, 5), 1700))

    def test_pending_times_out_then_late_native_still_confirms(self):
        ph = self.m.phase(self.car(0, 0), 0)
        l = self.m.lap(60000, None, ph, None)
        for _ in range(50): self.m.apply(l, self.native(state='pending'), 0.11)
        self.assertEqual((l.state, l.reason), ('unmatched', 'native_capture_missing'))
        self.assertTrue(self.m.apply(l, self.native(state='confirmed', raw_ms=60100, lap_count=1), 0.1))
        self.assertEqual(l.state, 'confirmed')
        l2 = self.m.lap(60000, None, ph, None)
        self.m.apply(l2, self.native(state='ambiguous'), 0.1)
        self.assertEqual(l2.state, 'unmatched')
        self.assertFalse(self.m.apply(l2, self.native(state='confirmed', raw_ms=60000, lap_count=1), 0.1))


class DeltaAdopt(unittest.TestCase):
    def setUp(self):
        self.lua, self.m = module('delta')

    def drive(self, d, total_ms, steps=200):
        d.begin(d, 0.0)
        last = None
        for i in range(1, steps + 1):
            f = i / steps
            last = d.feed(d, f * 0.9995, 15.0, total_ms * f)
        return last

    def test_route_trace_is_rescaled_to_ac_time_of_the_same_lap(self):
        d = self.m.new()
        self.drive(d, N14_ROUTE)
        trace = d.close(d, N14_ROUTE)
        self.assertTrue(d.adopt(d, trace, N14_NATIVE, N14_NATIVE + N14_PENALTY, True))
        self.assertEqual(d.reference_ms(d), N14_NATIVE)
        # Same driving pace on AC's time base: delta ~0, not -5.7 s, and no +90 s penalty jump.
        self.assertAlmostEqual(self.drive(d, N14_NATIVE), 0, delta=50)
        d.clear(d)
        self.assertFalse(d.has_reference(d))


class AppN14(unittest.TestCase):
    """Real RiceTrials.lua: the route lap is timed on AC's clock and confirmed by identity."""

    def app(self, v2=True, clock=True, cones=False):
        def pre(lua):
            lua.execute(STATION % (", protocol_v2=%s, track_config='FSAEJ_Endurance'" % str(v2).lower()))
            if not clock:   # v0.3.0 route clock (frame dt): reproduces the real 5.722 s drift
                lua.execute("package.preload['lap_clock']=function() error('disabled for test') end")
            lua.execute("""
              CAR={lapCount=12, lapTimeMs=0, previousLapTimeMs=61000, isLastLapValid=true,
                   position={x=0,z=1}, look={x=0,y=0,z=-1}}
              ac.getCar=function() return CAR end
            """)
        res = harness(None, pre)
        lua, _, _, requests, reply, cv = res
        script = lua.globals().script
        script.update(.1)
        reply(0, {'station_id': 'ST01', 'session_id': 'S1', 'pilot_id': 'P1', 'assignment_revision': 1})
        script.update(.1)
        script.configure(config(cv))
        if cones:
            from test_network import AutoPenalties
            script.configureCones(AutoPenalties().cfg(cv))
        capture_ui(lua)
        return lua, requests, script

    def stalled_lap(self, lua, script, route_ms, native_ms, lap_count=12, fin_extra=None):
        """Drive outlap + one timed lap. dt frames add up to route_ms, AC physics to native_ms.
        Lines at z=0 (start, crossed at frame midpoint) and checkpoint z=-5."""
        def frame(z, dt, lc, lt, plt=None):
            lua.execute('CAR.position.z=%r; CAR.lapCount=%d; CAR.lapTimeMs=%d' % (z, lc, lt))
            if plt is not None: lua.execute('CAR.previousLapTimeMs=%d' % plt)
            script.update(dt)
        lt = 20000
        for z in (1, -1, -4, -6, 1):          # outlap: start, checkpoint, back to before the line
            lt += 100; frame(z, .1, lap_count, lt)
        # Route line crossed at the middle of this 100 ms frame; AC's line in the same frame,
        # 50 ms before its end -> lapCount+1, lapTimeMs=50.
        frame(-1, .1, lap_count + 1, 50, lt + 50)
        lt = 50
        # Integer-ms frames (<= 1 s each, larger dt resets the route) summing to route_ms - 300:
        # 50 ms after the start crossing + these + 2 x 100 ms + 50 ms before the finish crossing.
        body = route_ms - 300
        n_mid = 600
        frames = [body // n_mid + (1 if i < body % n_mid else 0) for i in range(n_mid)]
        stall_total = native_ms - route_ms          # AC physics time the frame dt never saw
        for i, ms in enumerate(frames):
            z = -1.5 - 3.0 * i / n_mid               # -1.5 .. -4.5 (before the checkpoint)
            lt += ms + (stall_total if i == 40 else 0)
            frame(z, ms / 1000.0, lap_count + 1, lt)
        lt += 100; frame(-6, .1, lap_count + 1, lt)      # checkpoint
        lt += 100; frame(1, .1, lap_count + 1, lt)
        # Finish: route line at mid frame; AC line in the same frame.
        native_lap = lt + 50
        frame(-1, .1, lap_count + 2, 50, native_lap)
        return native_lap

    def test_n14_v2_last_best_overlay_and_payload_use_ac_time(self):
        lua, requests, script = self.app(v2=True)
        native = self.stalled_lap(lua, script, N14_ROUTE, N14_NATIVE)
        pen = [json.loads(r[3]) for r in requests if r[1].endswith('/api/v2/penalty-laps')]
        nat = [json.loads(r[3]) for r in requests if r[1].endswith('/api/v2/native-laps')]
        self.assertEqual(len(pen), 1)
        body = pen[0]
        # The route clock now follows AC physics time: no 5.7 s loss.
        self.assertLessEqual(abs(body['evidence']['route_raw_ms'] - native), 60)
        self.assertEqual(body['raw_ms'], native)
        # Same identity: the penalty lap IS the native slot of AC counter 14 (the lap that the
        # route timed), not the outlap's counter 13. (The native queue posts one at a time.)
        exported = json.loads(script.exportFieldEvidence())
        slot = next(r for r in exported['native']['records'] if r['id'] == body['lap_id'])
        self.assertEqual((slot['lap_count'], slot['raw_ms']), (14, native))
        self.assertEqual(body['evidence']['native_lap_count'], 14)
        self.assertTrue(nat)
        clock = body['evidence']['route_clock']
        self.assertGreaterEqual(clock['stall_frames'], 1)
        self.assertLess(clock['dt_minus_clock_ms'], -5000)            # what v0.3.0 would have lost
        self.assertTrue(body['evidence']['native_phase']['synced'])
        script.windowTimes(.1)
        times = '\n'.join(lines(lua))
        f = lambda ms: '%d:%02d.%03d' % (ms // 60000, ms // 1000 % 60, ms % 1000)
        self.assertIn('LAST    %s  AC' % f(native), times)
        self.assertIn('BEST    %s  AC' % f(native), times)

    def test_without_lap_time_ms_the_lap_stays_an_estimate(self):
        lua, requests, script = self.app(v2=False)
        # A CSP build without lapTimeMs: no native phase, so no identity and no pairing.
        lua.execute("""
          ac.getCar = function() return setmetatable({}, {__index=function(_, k)
            if k == 'lapTimeMs' then return nil end return CAR[k] end}) end
        """)
        self.stalled_lap(lua, script, N14_ROUTE, N14_NATIVE)
        capture_ui(lua)
        script.windowTimes(.1)
        times = '\n'.join(lines(lua))
        self.assertRegex(times, r'LAST    \d+:\d\d\.\d{3}  EST')
        self.assertIn('route est., no AC time', times)

    def f(self, ms):
        return '%d:%02d.%03d' % (ms // 60000, ms // 1000 % 60, ms % 1000)

    def test_n14_drifted_route_clock_v1_pairs_by_identity_beyond_old_2s_tolerance(self):
        # v0.3.0 dt clock: route raw drifts 5.722 s. v0.3.0 native_laps paired only within 2 s,
        # so the native draft stayed unmatched; now the AC counter identity pairs it.
        lua, requests, script = self.app(v2=False, clock=False, cones=True)
        native = self.stalled_lap(lua, script, N14_ROUTE, N14_NATIVE)
        v1 = json.loads(next(r for r in requests if r[1].endswith('/api/laps'))[3])
        self.assertAlmostEqual(v1['raw_ms'], N14_ROUTE, delta=60)          # V1 payload: route raw
        pen = v1['penalty']['penalty_ms']
        self.assertGreater(pen, 0)
        script.windowTimes(.1)
        times = '\n'.join(lines(lua))
        self.assertIn('LAST    %s  AC' % self.f(native + pen), times)       # AC time + penalties
        self.assertIn('%s + %.1f s  AC time' % (self.f(native), pen / 1000), times)
        self.assertNotIn(self.f(v1['raw_ms'] + pen), times)               # never the drifted sum

    def test_n14_drifted_route_clock_v2_windows_and_server_agree_on_ac_plus_penalties(self):
        from server.app import Store
        from server import field_v2 as server_field
        lua, requests, script = self.app(v2=True, clock=False, cones=True)
        native = self.stalled_lap(lua, script, N14_ROUTE, N14_NATIVE)
        for _ in range(3): script.update(.1)                               # let the queues pump
        pen = json.loads(next(r for r in requests if r[1].endswith('/api/v2/penalty-laps'))[3])
        self.assertAlmostEqual(pen['evidence']['route_raw_ms'], N14_ROUTE, delta=60)
        self.assertEqual(pen['raw_ms'], native)
        # Windows: Times (classic), native overlay and HUD all show AC time + penalties.
        script.windowTimes(.1)
        times = '\n'.join(lines(lua))
        exported = json.loads(script.exportFieldEvidence())
        slot = next(r for r in exported['native']['records'] if r['id'] == pen['lap_id'])
        self.assertEqual(slot['lap_count'], 14)
        # Server: the same lap through the real Field V2 ingestion.
        import tempfile, pathlib, threading
        from http.client import HTTPConnection
        from http.server import ThreadingHTTPServer
        from server.app import make_handler
        with tempfile.TemporaryDirectory() as d:
            store = Store(pathlib.Path(d) / 'field.sqlite', penalties_v2_enabled=True)
            http = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(store, 'admin', {'ST01': 'local-only'}))
            worker = threading.Thread(target=http.serve_forever, daemon=True); worker.start()
            try:
                for path, body in (('sessions', {'session_id': 'S1', 'name': 'S'}),
                                   ('pilots', {'pilot_id': 'P1', 'name': 'P'}),
                                   ('assignments', {'station_id': 'ST01', 'session_id': 'S1', 'pilot_id': 'P1'})):
                    c = HTTPConnection('127.0.0.1', http.server_port, timeout=3)
                    c.request('POST', '/api/admin/' + path, json.dumps(body),
                              {'Authorization': 'Bearer admin', 'Content-Type': 'application/json'})
                    self.assertEqual(c.getresponse().status, 200); c.close()
            finally:
                http.shutdown(); http.server_close()
            natives = [json.loads(r[3]) for r in requests if r[1].endswith('/api/v2/native-laps')]
            nat = next((b for b in natives if b['lap_id'] == pen['lap_id']), None)
            if nat is None:   # the native queue posts one at a time: take it from the journal
                q = [json.loads(i['payload']) for i in exported['native']['queue']]
                nat = next(b for b in q if b['lap_id'] == pen['lap_id'])
            server_field.receive(store, nat, 'ST01', 'native')
            server_field.receive(store, pen, 'ST01', 'penalty')
            laps = server_field.list_laps(store, 'S1')
            item = next(l for l in laps if l['lap_id'] == pen['lap_id'])
            server_preview = item['preview_ms']
        self.assertEqual(item['raw_ms'], native)
        self.assertIsNotNone(server_preview)
        self.assertGreater(server_preview, native)
        # One number everywhere: the server's AC time + penalties is what LAST/BEST show.
        self.assertIn('LAST    %s  AC' % self.f(server_preview), times)
        self.assertIn('BEST    %s  AC' % self.f(server_preview), times)
        self.assertIn('%s + %.1f s  AC time' % (self.f(native), (server_preview - native) / 1000), times)
        self.assertNotIn(self.f(pen['evidence']['route_raw_ms'] + server_preview - native), times)


if __name__ == '__main__':
    unittest.main()
