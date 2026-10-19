import unittest
from pathlib import Path
from lupa import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]

class AppRoute(unittest.TestCase):
    def test_config_rejects_unsurveyed_route(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().script = lua.table_from({})
        lua.globals().ac = lua.table_from({})
        lua.globals().ui = lua.table_from({})
        lua.globals().package.path = str(ROOT / '?.lua') + ';' + lua.globals().package.path
        lua.execute((ROOT / 'RiceTrials.lua').read_text())
        point = lambda x, z: lua.table_from({'x': x, 'z': z})
        gate = lua.table_from({'a': point(-2, 0), 'b': point(2, 0)})
        config = lua.table_from({'verified': True, 'event': 'autocross', 'asset_hash': 'a'*64,
                                 'track_id': 'FSAEJ_Endurance', 'gate': gate, 'direction': -1})
        self.assertFalse(lua.globals().script.configure(config))

    def test_app_exposes_pending_review_not_automatic_scoring(self):
        app = (ROOT / 'RiceTrials.lua').read_text()
        self.assertIn('PENDING_REVIEW', app)
        self.assertNotIn('web.post(endpoint', app)
