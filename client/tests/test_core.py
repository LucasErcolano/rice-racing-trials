import unittest
from pathlib import Path
from lupa import LuaRuntime

CORE = Path(__file__).resolve().parents[1] / 'core.lua'

class GeometryAndScoring(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.core = self.lua.execute(CORE.read_text())
        self.pt = lambda x, z: self.lua.table_from({'x': x, 'z': z})
        self.line = self.lua.table_from({'a': self.pt(-2, 0), 'b': self.pt(2, 0)})

    def test_forward_segment_only(self):
        self.assertTrue(self.core.crossed(self.pt(0, 1), self.pt(0, -1), self.line, -1))
        self.assertFalse(self.core.crossed(self.pt(3, 1), self.pt(3, -1), self.line, -1))
        self.assertFalse(self.core.crossed(self.pt(0, -1), self.pt(0, 1), self.line, -1))
        self.assertFalse(self.core.crossed(None, self.pt(0, -1), self.line, -1))

    def test_corrected_times_and_invalid_inputs(self):
        self.assertEqual(self.core.score('autocross', 60, 2, 1, None, None), 84)
        self.assertEqual(self.core.score('skidpad', None, 4, 0, 7, 9), 8.5)
        self.assertIsNone(self.core.score('skidpad', None, 0, 1, 7, 9))
        self.assertIsNone(self.core.score('autocross', -1, 0, 0, None, None))
