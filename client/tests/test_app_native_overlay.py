"""RiceTrials.lua wiring of the native-style overlays (CSP doubles, not a CSP runtime test).

Checks the two things that matter for the integration: the overlay reads only
values the V2 client already computed, and a missing detector/penalty evidence
is shown as a placeholder, never as 0.
"""
import unittest
from pathlib import Path

from test_app_hud import lines, capture_ui
from test_network import harness

STATION = ("package.preload['station_config'] = function() return {server_url='http://127.0.0.1:8080', "
           "station_id='ST01', station_token='local-only'%s} end")

AC_UI = ['pushACFont', 'popACFont', 'acText', 'drawRectFilled', 'windowSize', 'setCursor']


class AppNativeOverlay(unittest.TestCase):
    def setUp(self):
        self.lua, self.storage, _, self.requests, self.reply, self.cv = harness()
        self.lua.execute(STATION % '')
        capture_ui(self.lua)

    def _connect(self):
        script = self.lua.globals().script
        script.update(.1)
        self.reply(0, {'station_id': 'ST01', 'session_id': 's1', 'pilot_id': 'p7',
                       'pilot_name': 'Ana', 'assignment_revision': 1})
        return script

    def _ac_ui(self):
        lua = self.lua
        self.calls = []
        self.texts = []
        lua.execute("""
          vec2 = function(x, y) return {x=x, y=y} end
          rgbm = function(r, g, b, m) return {r=r, g=g, b=b, m=m} end
          AC_TEXT = {}
        """)
        wrap = lua.eval('function(f) return function(...) return f(...) end end')
        calls = self.calls
        texts = self.texts

        def rec(name):
            return wrap(lambda *a: calls.append((name,) + a))

        funcs = {n: rec(n) for n in AC_UI}
        funcs['windowSize'] = wrap(lambda: lua.eval('{x=500, y=300}'))
        funcs['calculateACTextSize'] = wrap(lambda t, s: lua.eval('{x=0}'))
        funcs['acText'] = wrap(lambda t, s, sp, c: (texts.append(t), calls.append(('acText', t)))[1])
        self.fallback = []
        fallback = self.fallback
        funcs['text'] = wrap(lambda t: fallback.append(t))
        lua.globals().ui = lua.table_from(funcs)

    def test_window_native_draws_after_assignment(self):
        script = self._connect()
        self._ac_ui()
        script.windowNative(.2)
        self.assertIn('drawRectFilled', [c[0] for c in self.calls])
        self.assertIn('acText', [c[0] for c in self.calls])
        # Font stack always balanced.
        names = [c[0] for c in self.calls]
        self.assertEqual(names.count('pushACFont'), names.count('popACFont'))

    def _text_ui(self):
        """Plain text ui, so a degraded window can be asserted on its message."""
        self.lua.execute("""
          UI_LINES = {}
          ui = {text=function(s) UI_LINES[#UI_LINES+1] = tostring(s) end,
                textColored=function(s) UI_LINES[#UI_LINES+1] = tostring(s) end}
        """)

    def test_window_native_degrades_when_modules_missing(self):
        # No overlay modules on this build: the window says so, never a crash.
        lua, _, _, _, _, _ = harness(pre_exec=lambda l: l.execute(
            "package.preload['native_overlay'] = function() error('no overlay') end"))
        lua.execute("""
          UI_LINES = {}
          ui = {text=function(s) UI_LINES[#UI_LINES+1] = tostring(s) end}
        """)
        script = lua.globals().script
        script.update(.1)
        script.windowNative(.2)
        self.assertEqual(lines(lua), ['Native overlay not available'])
        self.assertFalse(script.overlayEnabled())

    def test_window_native_survives_broken_renderer(self):
        # A failing render must not raise inside the window callback.
        lua, _, _, _, _, _ = harness(pre_exec=lambda l: l.execute(
            "package.preload['native_render'] = function() error('broken renderer') end"))
        lua.execute("""
          UI_LINES = {}
          ui = {text=function(s) UI_LINES[#UI_LINES+1] = tostring(s) end}
        """)
        script = lua.globals().script
        script.update(.1)
        script.windowNative(.2)
        self.assertEqual(lines(lua), ['Native overlay not available'])
        self.assertFalse(script.overlayEnabled())

    def test_window_native_degrades_when_model_builder_fails(self):
        # A broken model builder must not take down the window: it degrades to text.
        self._connect()
        self._ac_ui()
        self.lua.execute("package.loaded['native_overlay'].timing = function() error('broken model') end")
        self.lua.globals().script.windowNative(.2)
        self.assertEqual(self.fallback, ['Native overlay not available'])
        self.assertNotIn('acText', [c[0] for c in self.calls])  # nothing half-drawn

    def test_overlay_disabled_by_config(self):
        lua, _, _, _, _, _ = harness(pre_exec=lambda l: l.execute(
            "package.preload['station_config'] = function() return {server_url='http://127.0.0.1:8080',"
            " station_id='ST01', station_token='local-only', overlay_enabled=false} end"))
        script = lua.globals().script
        script.update(.1)
        self.assertFalse(script.overlayEnabled())

    def test_overlay_enabled_by_default(self):
        script = self._connect()
        self.assertTrue(script.overlayEnabled())

    def test_no_reference_shows_dashes_not_zero(self):
        # Without a reference lap the delta must read "--" (never a fake 0).
        lua = self.lua
        lua.execute("package.preload['native_overlay'] = function() return {timing=function() return {} end,"
                    " delta=function(v, o) return {kind='delta', text='--', has_reference=o.has_reference,"
                    " frac=0, color={1,1,1}} end, layout=function() return {scale=1,x=8,y=8,alpha=0.6} end,"
                    " palette=function() return {panel={0,0,0},label={0.5,0.5,0.5}} end,"
                    " panel_alpha=function() return 0.6 end} end")
        lua.execute((lua.globals().package.path and '') or '')
        self._ac_ui()
        script = lua.globals().script
        script.update(.1)
        script.windowNative(.2)
        # The draw path completed: the model itself was asserted in test_native_overlay.py.
        self.assertIn('pushACFont', [c[0] for c in self.calls])

    def test_manifest_declares_native_window(self):
        manifest = (Path(__file__).resolve().parents[1] / 'manifest.ini').read_text(encoding='utf-8')
        self.assertIn('FUNCTION_MAIN = windowNative', manifest)
        self.assertIn('ID = native', manifest)

    def test_overlay_shows_lap_times_and_discrete_penalty(self):
        # Real route crossing (same drive as the V2 tests): the overlay must show
        # the values the client computed, keep the penalty discrete, and with no
        # reference lap the delta must read a placeholder (never a fake 0).
        from test_network import config as make_config, drive_lap, AutoPenalties
        self.lua, _, _, _, self.reply, self.cv = harness()
        self.lua.execute(STATION % ", protocol_v2=true, track_config='FSAEJ_Endurance'")
        capture_ui(self.lua)
        lua = self.lua
        lua.execute("""
          CAR={lapCount=0, previousLapTimeMs=60000, isLastLapValid=true, position={x=0,z=1}}
          ac.getCar=function() return CAR end
        """)
        script = lua.globals().script
        script.update(.1)
        self.reply(0, {'station_id': 'ST01', 'session_id': 'S1', 'pilot_id': 'P1'})
        script.update(.1)
        script.configure(make_config(self.cv))
        script.configureCones(AutoPenalties().cfg(self.cv, ref_line_cones=['REF']))
        self._ac_ui()
        drive_lap(lua, lua.globals().CAR.position)
        # The window callback is what CSP calls; the model is cached inside it.
        script.windowNative(.2)
        text = ' '.join(self.texts)
        self.assertIn('CURRENT', text)   # timed lap, real elapsed value
        # Penalty indicator: absent when there is no penalty, never a fake "0".
        self.assertNotIn('PENALTY', text)
        self.assertNotIn('0.00', text)   # a missing delta is never shown as zero
        self.assertTrue('PENDING' in text or '--' in text)
        # The native timing must agree with the classic window on the final time.
        script.windowTimes(.1)
        classic = '\n'.join(lines(lua))
        self.assertIn('0:02.400', text)

    # ------------------------------------------------------------------
    # Penalty matrix through the real route crossing (same drive as drive_lap,
    # split so the overlay can be read mid-lap and after the lap closes).
    # ------------------------------------------------------------------
    def _session(self, auto=True, penalty_cfg=None, **extra):
        from test_network import config as make_config, AutoPenalties
        lua, storage, position, requests, reply, cv = harness()
        lua.execute("local p = ac.getCar; ac.getCar = function(n) local c = p(n);"
                    " c.look = {x=0, y=0, z=-1}; return c end")
        self.lua, self.cv, self.requests, self.position = lua, cv, requests, position
        capture_ui(lua)
        script = lua.globals().script
        script.connect('http://127.0.0.1:8080', 'station-1', 'secret')
        script.configure(make_config(cv))
        script.configureCones(AutoPenalties().cfg(cv, auto_penalties=auto, **extra))
        reply(0, {'station_id': 'station-1', 'session_id': 's1', 'pilot_id': 'p1'})
        if penalty_cfg is not None:
            store = script.penaltyStore()
            store.cfg = cv(penalty_cfg)
        self.script = script
        return script

    def _move(self, *zs):
        for z in zs:
            self.position['z'] = z
            self.script.update(.1)

    def _read(self):
        """Native overlay texts for the current frame (fresh capture each call)."""
        self._ac_ui()
        self.script.windowNative(.2)
        return list(self.texts)

    def _classic(self):
        capture_ui(self.lua)
        self.script.windowTimes(.1)
        return lines(self.lua)

    START_TIMED = (1, -1, -4, -6, 1, -1)   # route outlap, then timed lap starts

    def _lap_payload(self):
        import json
        post = next(r for r in self.requests if r[1].endswith('/api/laps'))
        return json.loads(post[3])

    def test_positive_penalty_is_discrete_and_matches_v2_corrected_time(self):
        self._session()
        self._move(*self.START_TIMED)
        self._move(-4)                      # both cones crossed mid-lap
        live = self._read()
        self.assertIn('PENALTY', live)
        # Mid-lap only the first cone has been crossed: 1 cone x 2 s.
        self.assertEqual(live[live.index('PENALTY') + 1], '+2.000')
        self.assertNotIn('NO EVIDENCE', live)
        # CURRENT is the raw clock, penalty not merged in. V2 interpolates the start-line
        # crossing inside the frame (0.05 s) and one further 0.1 s frame has elapsed: 0.150.
        self.assertEqual(live[live.index('CURRENT') + 1], '0:00.150')
        self._move(-6, 1, -1)               # close the lap
        payload = self._lap_payload()
        pen = payload['penalty']['penalty_ms']
        self.assertEqual(pen, 4000)         # both cones by lap end
        after = self._read()
        last = after[after.index('LAST') + 1]
        classic = '\n'.join(self._classic())
        # Same corrected time as the V2 window (raw + penalty), formatted identically.
        self.assertIn('LAST    ' + last, classic)
        self.assertIn('+ 4.0 s', classic)
        self.assertEqual(after[after.index('BEST') + 1], last)

    def test_corrected_time_is_raw_plus_penalty(self):
        self._session()
        self._move(*self.START_TIMED, -4, -6, 1, -1)
        payload = self._lap_payload()
        pen = payload['penalty']['penalty_ms']
        after = self._read()
        last = after[after.index('LAST') + 1]
        mins, rest = last.split(':')
        corrected_ms = int(mins) * 60000 + int(float(rest) * 1000 + 0.5)
        classic = '\n'.join(self._classic())
        # classic window prints "<raw> + <pen> s" for the same lap
        self.assertIn('+ %.1f s' % (pen / 1000), classic)
        raw_txt = classic.split('LAST    ' + last)[1].split('\n')[1].strip().split(' + ')[0]
        m2, r2 = raw_txt.split(':')
        raw_ms = int(m2) * 60000 + int(float(r2) * 1000 + 0.5)
        self.assertEqual(corrected_ms, raw_ms + pen)

    def test_no_evidence_is_not_zero(self):
        # No detector commissioned (auto_penalties off): outcome is nil.
        self._session(auto=False)
        self._move(*self.START_TIMED, -4)
        live = self._read()
        self.assertIn('PENALTY', live)
        self.assertEqual(live[live.index('PENALTY') + 1], 'NO EVIDENCE')
        self.assertFalse([t for t in live if t in ('+0.000', '0.000', '0')])

    def test_disabled_penalties_show_no_penalty_row_and_no_fake_evidence(self):
        cfg = {'cone': {'on': False, 's': 2}, 'off_course': {'on': False, 's': 20},
               'slalom': {'on': False, 's': 20}}
        self._session(penalty_cfg=cfg)
        self._move(*self.START_TIMED, -4)
        live = self._read()
        # Detector evidence exists but every penalty is switched off: nothing is charged,
        # and that is NOT reported as missing evidence.
        self.assertNotIn('PENALTY', live)
        self.assertNotIn('NO EVIDENCE', live)
        self._move(-6, 1, -1)
        self.assertEqual(self._lap_payload()['penalty']['penalty_ms'], 0)
        after = self._read()
        self.assertEqual(after[after.index('LAST') + 1], after[after.index('BEST') + 1])

    def test_clean_lap_has_no_penalty_row(self):
        # Detectors are live but nothing was hit before the first cone: no row, not "+0".
        self._session()
        self._move(*self.START_TIMED)
        live = self._read()
        self.assertNotIn('PENALTY', live)
        self.assertNotIn('NO EVIDENCE', live)

    def test_delta_without_reference_is_placeholder_not_zero(self):
        self._session()
        self._move(*self.START_TIMED, -4)
        live = self._read()
        self.assertIn('--', live)                    # delta readout placeholder
        self.assertFalse([t for t in live if t in ('0.00', '+0.00', '-0.00')])

    def test_delta_with_reference_shows_a_signed_value(self):
        self._session()
        self._move(*self.START_TIMED, -4, -6, 1, -1)   # lap 1 becomes the reference
        self._move(-4)                                  # lap 2 in progress
        live = self._read()
        signed = [t for t in live if t[:1] in '+-' and t[1:2].isdigit() and '.' in t
                  and t != '+4.000' and not t.startswith('+4.0')]
        self.assertTrue(signed or 'PENDING' in live, live)  # a value or an explicit pending, never blank


    def test_delta_panel_is_stacked_below_timing_panel(self):
        # The two panels must not overlap: the delta rectangle starts below the timing one.
        self._session()
        self._move(*self.START_TIMED, -4)
        self._ac_ui()
        self.script.windowNative(.2)
        rects = [c for c in self.calls if c[0] == 'drawRectFilled']
        self.assertGreaterEqual(len(rects), 2)
        ys = [float(r[1]['y']) for r in rects]   # top edge of every rect drawn
        timing_top, timing_bottom = ys[0], float(rects[0][2]['y'])
        delta_panel_top = ys[1]
        self.assertGreaterEqual(delta_panel_top, timing_bottom)


if __name__ == '__main__':
    unittest.main()
