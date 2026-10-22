"""Native-style overlay model tests with explicit doubles (not a CSP runtime test).

The renderer is exercised through a fake `ui` table with only the CSP APIs
confirmed in docs/csp-api-reference.md and ext/acc-lua-sdk, so a missing API
degrades instead of raising.
"""
import unittest
from pathlib import Path
from lupa import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


class OverlayModel(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().package.path = str(ROOT / '?.lua') + ';' + self.lua.globals().package.path
        self.m = self.lua.eval("(require('native_overlay'))")

    def tab(self, value):
        return self.lua.table_from(value, recursive=True) if value is not None else None

    def model(self, state=None, config=None):
        m = self.m.timing(self.tab(state) if state is not None else None,
                          self.tab(config) if config is not None else None)
        rows = [(m.rows[i].label, m.rows[i].text, m.rows[i].color[1]) for i in range(1, len(m.rows) + 1)]
        return m, rows

    def find(self, rows, label):
        hits = [r for r in rows if r[0] == label]
        self.assertTrue(hits, f'no row {label!r} in {rows}')
        return hits[0]

    # --- time formatting -------------------------------------------------
    def test_time_format_matches_ac(self):
        f = self.m.format_time
        self.assertEqual(f(84520), '1:24.520')
        self.assertEqual(f(0), '0:00.000')
        self.assertEqual(f(60000), '1:00.000')
        self.assertIsNone(f(None))
        self.assertIsNone(f(-1))
        self.assertIsNone(f(84520.5))     # not an integer -> no fake precision
        self.assertIsNone(f(360000000))

    def test_signed_format(self):
        self.assertEqual(self.m.format_signed(-0.83), '-0.83')
        self.assertEqual(self.m.format_signed(3.2), '+3.20')
        self.assertIsNone(self.m.format_signed(None))
        self.assertIsNone(self.m.format_signed(150))

    # --- timing rows -----------------------------------------------------
    def test_empty_state_means_uncalibrated_route(self):
        # An empty state has no phase: the native overlay says so instead of
        # inventing a "--" lap that would look like a timed lap.
        _, rows = self.model({})
        self.assertEqual(self.find(rows, 'TIMING')[1], 'NO CALIBRATED')

    def test_waiting_phase_shows_placeholders_never_zero(self):
        _, rows = self.model({'phase': 'waiting'})
        self.assertEqual(self.find(rows, 'CURRENT')[1], '--:--.---')
        self.assertEqual(self.find(rows, 'LAST')[1], '--')
        self.assertEqual(self.find(rows, 'BEST')[1], '--')

    def test_nil_state_does_not_raise(self):
        _, rows = self.model(None)
        self.assertTrue(rows)

    def test_uncalibrated_route_says_so(self):
        _, rows = self.model({'phase': None, 'current_ms': 84520})
        self.assertEqual(self.find(rows, 'TIMING')[1], 'NO CALIBRATED')

    def test_outlap_shows_no_current_time(self):
        _, rows = self.model({'phase': 'outlap', 'lap_number': 1, 'last_ms': 90000, 'best_ms': 88000})
        self.assertEqual(self.find(rows, 'CURRENT')[1], 'OUTLAP')
        self.assertEqual(self.find(rows, 'LAST')[1], '1:30.000')
        self.assertEqual(self.find(rows, 'BEST')[1], '1:28.000')

    def test_timed_lap_with_penalty_line(self):
        _, rows = self.model({'phase': 'timed', 'lap_number': 3, 'current_ms': 88520,
                              'penalty_ms': 4000, 'last_ms': 90000, 'best_ms': 88000})
        self.assertEqual(self.find(rows, 'LAP')[1], '03')
        self.assertEqual(self.find(rows, 'CURRENT')[1], '1:28.520')
        self.assertEqual(self.find(rows, 'PENALTY')[1], '+4.000')

    def test_penalty_pending_is_not_zero(self):
        _, rows = self.model({'phase': 'timed', 'current_ms': 84520, 'penalty_ms': 0,
                              'penalty_pending': True})
        self.assertEqual(self.find(rows, 'PENALTY')[1], 'NO EVIDENCE')

    def test_no_penalty_line_when_clean(self):
        _, rows = self.model({'phase': 'timed', 'current_ms': 84520, 'penalty_ms': 0})
        self.assertFalse([r for r in rows if r[0] == 'PENALTY'])

    def test_official_valid_beats_local_best(self):
        _, rows = self.model({'phase': 'timed', 'current_ms': 84520, 'best_ms': 87000,
                              'official_ms': 86000, 'official_status': 'VALID'})
        self.assertEqual(self.find(rows, 'BEST')[1], '1:26.000')

    def test_pending_review_official_is_not_shown_as_best(self):
        _, rows = self.model({'phase': 'timed', 'current_ms': 84520, 'best_ms': 87000,
                              'official_ms': 86000, 'official_status': 'PENDING_REVIEW'})
        self.assertEqual(self.find(rows, 'BEST')[1], '1:27.000')

    def test_layout_config_clamped(self):
        lay = self.m.layout(self.tab({'scale': 99, 'x': -5, 'alpha': 7}))
        self.assertEqual(lay.scale, 3)
        self.assertEqual(lay.x, 0)
        self.assertEqual(lay.alpha, 1)
        self.assertEqual(self.m.layout(None).scale, 1)

    def test_model_is_pure(self):
        state = {'phase': 'timed', 'current_ms': 84520, 'penalty_ms': 4000}
        a = self.m.timing(self.tab(state), None)
        b = self.m.timing(self.tab(state), None)
        self.assertEqual(len(a.rows), len(b.rows))
        self.assertEqual(a.layout.scale, 1)


class DeltaModel(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().package.path = str(ROOT / '?.lua') + ';' + self.lua.globals().package.path
        self.m = self.lua.eval("(require('native_overlay'))")

    def delta(self, ms, opts=None, config=None):
        o = self.lua.table_from(opts, recursive=True) if opts is not None else None
        return self.m.delta(ms, o, None)

    def test_no_reference_means_dashes(self):
        d = self.delta(-500, {'has_reference': False})
        self.assertEqual(d.text, '--')
        self.assertEqual(d.frac, 0)
        self.assertFalse(d.has_reference)

    def test_faster_is_negative_and_green(self):
        d = self.delta(-830, {'has_reference': True})
        self.assertEqual(d.text, '-0.83')
        self.assertEqual(d.direction, 'faster')
        self.assertEqual(d.frac, -0.415)
        self.assertEqual(d.color[1], 0.30)   # palette.good

    def test_slower_is_positive_and_red(self):
        d = self.delta(1200, {'has_reference': True})
        self.assertEqual(d.text, '+1.20')
        self.assertEqual(d.direction, 'slower')
        self.assertEqual(d.color[1], 0.95)   # palette.bad

    def test_frac_saturates_at_full_scale(self):
        d = self.delta(9000, {'has_reference': True, 'full_scale_ms': 2000})
        self.assertEqual(d.frac, 1)

    def test_missing_value_is_pending_not_zero(self):
        d = self.delta(None, {'has_reference': True, 'pending': True})
        self.assertEqual(d.text, 'PENDING')
        self.assertTrue(d.pending)
        self.assertEqual(d.frac, 0)

    def test_penalty_kept_visible_with_amber(self):
        d = self.delta(4000, {'has_reference': True, 'pending': True})
        self.assertEqual(d.text, '+4.00')
        self.assertTrue(d.pending)
        self.assertEqual(d.color[1], 1.0)    # palette.pending (amber), not hidden


class Renderer(unittest.TestCase):
    """A fake ui with only the confirmed CSP APIs; nothing may raise.

    Same double convention as test_hud.py: Python callables are userdata in Lua,
    so every double is wrapped in a real Lua function.
    """

    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().package.path = str(ROOT / '?.lua') + ';' + self.lua.globals().package.path
        self.calls = []
        self.lua.execute('''
          vec2 = function(x, y) return {x=x, y=y} end
          rgbm = function(r, g, b, m) return {r=r, g=g, b=b, m=m} end
        ''')
        self.wrap = self.lua.eval('function(f) return function(...) return f(...) end end')
        self.renderer = self.lua.eval("(require('native_render'))")
        self.overlay = self.lua.eval("(require('native_overlay'))")

    def ui(self, names, boom=()):
        def rec(name):
            return self.wrap(lambda *a: self.calls.append((name,) + a))
        funcs = {n: rec(n) for n in names}
        if 'windowSize' in names:
            funcs['windowSize'] = self.wrap(
                lambda: (self.calls.append(('windowSize',)), self.lua.eval('{x=500, y=300}'))[1])
        if 'measureDWriteText' in names:
            funcs['measureDWriteText'] = self.wrap(lambda t, s: self.lua.eval('{x=0}'))
        if 'calculateACTextSize' in names:
            funcs['calculateACTextSize'] = self.wrap(lambda t, l: self.lua.eval('{x=0}'))
        for name in boom:
            funcs[name] = self.wrap(lambda *a: (_ for _ in ()).throw(RuntimeError('ui broken')))
        self.lua.globals().ui = self.lua.table_from(funcs)

    def tab(self, v):
        return self.lua.table_from(v, recursive=True) if v is not None else None

    def timing_model(self):
        return self.overlay.timing(self.tab({'phase': 'timed', 'current_ms': 84520,
                                             'penalty_ms': 4000, 'last_ms': 90000,
                                             'best_ms': 88000}), None)

    def delta_model(self):
        return self.overlay.delta(-830, self.tab({'has_reference': True}), None)

    def test_timing_draws_and_balances_ac_font(self):
        self.ui(['pushACFont', 'popACFont', 'acText', 'drawRectFilled', 'windowSize',
                 'setCursor', 'calculateACTextSize'])
        self.assertTrue(self.renderer.draw(self.timing_model(), None))
        names = [c[0] for c in self.calls]
        self.assertEqual(names[0], 'pushACFont')
        self.assertEqual(names[-1], 'popACFont')
        self.assertEqual(names.count('pushACFont'), names.count('popACFont'))
        self.assertIn('drawRectFilled', names)
        self.assertIn('acText', names)

    def test_delta_draws_centre_tick_and_readout(self):
        self.ui(['pushACFont', 'popACFont', 'acText', 'drawRectFilled', 'windowSize',
                 'setCursor', 'calculateACTextSize'])
        self.assertTrue(self.renderer.draw(self.delta_model(), None))
        self.assertIn('drawRectFilled', [c[0] for c in self.calls])

    def test_delta_without_reference_still_draws(self):
        self.ui(['pushACFont', 'popACFont', 'acText', 'drawRectFilled', 'windowSize',
                 'setCursor', 'calculateACTextSize'])
        model = self.overlay.delta(None, self.tab({'has_reference': False}), None)
        self.assertTrue(self.renderer.draw(model, None))

    def test_no_ui_never_raises(self):
        self.lua.globals().ui = None
        self.assertFalse(self.renderer.draw(self.timing_model(), None))

    def test_partial_ui_degrades_to_text(self):
        self.ui(['textColored', 'setCursor'])
        self.assertTrue(self.renderer.draw(self.timing_model(), None))
        names = [c[0] for c in self.calls]
        self.assertEqual(set(names), {'setCursor', 'textColored'})
        self.assertNotIn('acText', names)

    def test_dwrite_fallback_when_ac_font_missing(self):
        self.ui(['pushDWriteFont', 'popDWriteFont', 'dwriteText', 'drawRectFilled', 'windowSize',
                 'setCursor', 'measureDWriteText'])
        self.assertTrue(self.renderer.draw(self.timing_model(), None))
        names = [c[0] for c in self.calls]
        self.assertEqual(names[0], 'pushDWriteFont')
        self.assertEqual(names[-1], 'popDWriteFont')
        self.assertEqual(names.count('pushDWriteFont'), names.count('popDWriteFont'))

    def test_failing_ui_calls_never_raise_and_restore_font(self):
        self.ui(['pushACFont', 'popACFont', 'acText', 'drawRectFilled', 'windowSize',
                 'setCursor', 'calculateACTextSize'],
                boom=('acText', 'drawRectFilled', 'setCursor', 'calculateACTextSize', 'windowSize'))
        # Each ui call is individually guarded, so a failing frame degrades to
        # whatever was drawn before the failure instead of killing the overlay.
        self.assertIn(self.renderer.draw(self.timing_model(), None), (True, False))
        self.assertEqual([c[0] for c in self.calls], ['pushACFont', 'popACFont'])  # stack balanced

    def test_unknown_kind_returns_false(self):
        self.ui(['pushACFont', 'popACFont', 'acText'])
        self.assertFalse(self.renderer.draw(self.tab({'kind': 'nope', 'layout': {}}), None))

    def test_font_push_failure_degrades_without_raising(self):
        self.ui(['pushACFont', 'popACFont', 'acText', 'drawRectFilled', 'windowSize',
                 'setCursor', 'calculateACTextSize'], boom=('pushACFont',))
        # push fails: no pop may be attempted on a font that was never pushed.
        self.assertIn(self.renderer.draw(self.timing_model(), None), (True, False))
        self.assertNotIn('popACFont', [c[0] for c in self.calls])


if __name__ == '__main__':
    unittest.main()
