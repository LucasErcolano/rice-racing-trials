import unittest

class GateTests(unittest.TestCase):
    def test_first_start_is_not_a_full_lap_and_checkpoint_order_is_required(self):
        from cleanlap.validation import GateTracker
        tracker=GateTracker([-1,-1],100)
        self.assertEqual(tracker.observe(0,-1,0,0)['phase'],'start')
        self.assertEqual(tracker.laps,0)
        self.assertFalse(tracker.observe(0,-1,1,99)['ordered'])
        self.assertEqual(tracker.laps,0)
        self.assertTrue(tracker.observe(1,-1,2,70)['ordered'])
        self.assertEqual(tracker.observe(0,-1,3,100)['phase'],'finish')
        self.assertEqual(tracker.laps,1)

    def test_line_oscillation_does_not_complete_a_lap(self):
        from cleanlap.validation import GateTracker
        tracker=GateTracker([1],100)
        tracker.observe(0,1,0,0)
        self.assertFalse(tracker.observe(0,1,1,1)['ordered'])
        self.assertEqual(tracker.laps,0)
