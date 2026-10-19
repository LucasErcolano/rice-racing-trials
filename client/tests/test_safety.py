import unittest
from pathlib import Path
from lupa import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]

class Safety(unittest.TestCase):
    def test_single_gate_cannot_prove_full_loop(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        core = lua.execute((ROOT / 'core.lua').read_text())
        self.assertFalse(core.full_loop_proven(None))
        self.assertFalse(core.full_loop_proven(lua.table_from({'crossings': 4, 'elapsed': 90})))

    def test_app_never_posts_unverified_split(self):
        app = (ROOT / 'RiceTrials.lua').read_text()
        self.assertNotIn('web.post(endpoint', app)
        self.assertIn('ROUTE_UNVERIFIED', app)
        self.assertIn('PENDING_REVIEW', app)
