import unittest
from pathlib import Path
from lupa import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]

class RouteQueue(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.core = self.lua.execute((ROOT / 'core.lua').read_text())
        self.p = lambda z: self.lua.table_from({'x': 0, 'z': z})
        self.gate = self.lua.table_from({'a': self.lua.table_from({'x': -2, 'z': 0}), 'b': self.lua.table_from({'x': 2, 'z': 0}), 'direction': -1})
        self.checkpoint = self.lua.table_from({'a': self.lua.table_from({'x': -2, 'z': -5}), 'b': self.lua.table_from({'x': 2, 'z': -5}), 'direction': -1})
        self.route = self.lua.table_from({'start': self.gate, 'checkpoints': self.lua.table_from([self.checkpoint])})

    def test_outlap_then_ordered_checkpoint_then_finish(self):
        machine = self.core.route_machine(self.route)
        self.assertEqual(machine.observe(machine, self.p(1), self.p(-1)), ('OUTLAP', 0.5))
        self.assertIsNone(machine.observe(machine, self.p(1), self.p(-1)))
        self.assertEqual(machine.observe(machine, self.p(-4), self.p(-6)), 'CHECKPOINT')
        self.assertEqual(machine.observe(machine, self.p(1), self.p(-1)), ('OUTLAP_COMPLETE', 0.5))
        self.assertEqual(machine.observe(machine, self.p(-4), self.p(-6)), 'CHECKPOINT')
        self.assertEqual(machine.observe(machine, self.p(3), self.p(-1)), ('COMPLETE', 0.75))

    def test_reverse_and_missing_checkpoint_cannot_finish(self):
        machine = self.core.route_machine(self.route)
        self.assertIsNone(machine.observe(machine, self.p(-1), self.p(1)))
        self.assertEqual(machine.observe(machine, self.p(1), self.p(-1))[0], 'OUTLAP')
        self.assertIsNone(machine.observe(machine, self.p(1), self.p(-1)))

    def test_queue_retains_entry_until_ack_and_rejects_wrong_ack(self):
        queue = self.core.pending_queue()
        self.assertTrue(queue.add(queue, 'lap-1', 'immutable-payload'))
        self.assertFalse(queue.add(queue, 'lap-1', 'changed'))
        self.assertEqual(queue.peek(queue).payload, 'immutable-payload')
        self.assertFalse(queue.ack(queue, 'lap-other'))
        self.assertEqual(queue.peek(queue).id, 'lap-1')
        self.assertTrue(queue.ack(queue, 'lap-1'))
        self.assertIsNone(queue.peek(queue))

    def test_assignment_requires_server_identity_and_matching_session(self):
        self.assertFalse(self.core.assignment_matches(None, 's', 'p', 'station'))
        assignment = self.lua.table_from({'station_id': 'station', 'session_id': 's', 'pilot_id': 'p'})
        self.assertTrue(self.core.assignment_matches(assignment, 's', 'p', 'station'))
        self.assertFalse(self.core.assignment_matches(assignment, 'other', 'p', 'station'))
