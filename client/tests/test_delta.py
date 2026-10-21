"""Delta bar: current lap vs best FINAL lap (raw + penalties)."""
import unittest
from pathlib import Path
from lupa import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


class Delta(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute("package.path = '%s/?.lua;' .. package.path" % ROOT)
        self.m = self.lua.eval("(require('delta'))")

    def lap(self, d, total_ms, start=0.0, penalty_at=None, penalty_ms=0, steps=200, spline=True, length_m=3000.0):
        """Drive one lap of fixed length at constant pace. Returns the last delta."""
        d.begin(d, start if spline else None)
        last = None
        for i in range(1, steps + 1):
            f = i / steps
            val = total_ms * f + (penalty_ms if penalty_at is not None and f >= penalty_at else 0)
            sp = (start + f * 0.9995) % 1.0 if spline else None   # the line itself is crossed after the lap
            last = d.feed(d, sp, length_m / steps, val)
        return last

    def test_no_reference_means_no_delta(self):
        d = self.m.new()
        self.assertIsNone(self.lap(d, 60000))
        self.assertTrue(d.finish(d, 60000))
        self.assertTrue(d.has_reference(d))

    def test_faster_lap_is_negative_slower_is_positive(self):
        d = self.m.new(); self.lap(d, 60000); d.finish(d, 60000)
        fast = self.lap(d, 58000)
        self.assertAlmostEqual(fast, -2000, delta=80)
        d.finish(d, 58000)
        slow = self.lap(d, 61000)
        self.assertAlmostEqual(slow, 3000, delta=80)

    def test_penalties_count_in_the_delta_and_in_the_reference(self):
        d = self.m.new(); self.lap(d, 60000); d.finish(d, 60000)
        # same raw pace but +20 s penalty at half lap: delta jumps to ~+20 s
        r = self.lap(d, 60000, penalty_at=0.5, penalty_ms=20000)
        self.assertAlmostEqual(r, 20000, delta=100)
        # a penalised lap with a worse final time must NOT replace the reference
        self.assertFalse(d.finish(d, 80000))
        # a clean but slower raw lap that beats the penalised one is still worse than the reference
        self.assertFalse(d.finish(d, 61000))

    def test_better_final_time_replaces_reference_even_if_raw_is_slower(self):
        d = self.m.new(); self.lap(d, 60000, penalty_at=0.5, penalty_ms=20000); d.finish(d, 80000)
        self.lap(d, 70000); self.assertTrue(d.finish(d, 70000))

    def test_start_line_offset_in_spline_is_handled(self):
        d = self.m.new(); self.lap(d, 60000, start=0.87); d.finish(d, 60000)   # line crosses 1.0 -> 0.0
        r = self.lap(d, 59000, start=0.87)
        self.assertAlmostEqual(r, -1000, delta=80)

    def test_forward_jump_invalidates_the_lap(self):
        d = self.m.new(); self.lap(d, 60000); d.finish(d, 60000)
        d.begin(d, 0.0)
        d.feed(d, 0.02, 1.0, 1000)
        d.feed(d, 0.6, 1.0, 2000)           # teleport / shortcut
        self.assertFalse(d.finish(d, 1000))  # not an acceptable reference
        self.assertIsNone(d.feed(d, 0.7, 1.0, 3000))

    def test_abort_never_becomes_reference(self):
        d = self.m.new(); self.lap(d, 60000); d.finish(d, 60000)
        d.begin(d, 0.0); d.feed(d, 0.3, 10, 18000); d.abort(d)
        self.assertFalse(d.finish(d, 5000))

    def test_incomplete_spline_lap_is_not_a_reference(self):
        d = self.m.new()
        d.begin(d, 0.0)
        for i in range(1, 60): d.feed(d, i / 200.0, 5, i * 300)
        self.assertFalse(d.finish(d, 18000))
        self.assertFalse(d.has_reference(d))

    def test_distance_mode_without_spline(self):
        d = self.m.new()
        self.lap(d, 60000, spline=False); self.assertTrue(d.finish(d, 60000))  # builds the trace by metres
        r = self.lap(d, 59000, spline=False)
        self.assertAlmostEqual(r, -1000, delta=300)
        self.assertEqual(d.source(d), 'distance')

    def test_bad_values_do_not_raise(self):
        d = self.m.new()
        self.assertIsNone(d.feed(d, 0.1, 1, 100))              # no lap started
        d.begin(d, 0.0)
        self.assertIsNone(d.feed(d, float('nan'), 1, 100))
        self.assertIsNone(d.feed(d, 0.1, 1, float('nan')))
        self.assertFalse(d.finish(d, float('nan')))

    def test_format_and_bar(self):
        self.assertEqual(self.m.format(-830), '-0.83')
        self.assertEqual(self.m.format(3200), '+3.20')
        self.assertEqual(self.m.format(None), '--')
        b = self.m.bar(-1000, 2000); self.assertEqual((b.frac, b.level), (-0.5, 'ok'))
        b = self.m.bar(9000, 2000); self.assertEqual((b.frac, b.level), (1, 'error'))
        b = self.m.bar(None, 2000); self.assertEqual(b.frac, 0)


if __name__ == '__main__':
    unittest.main()
