"""Same fixtures through server/scoring.py and client/scoring.lua (real Lua via lupa)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import scoring  # noqa: E402

RULES = json.loads((ROOT / 'rules' / 'scoring-v1.json').read_text())


def lua_runtime():
    from lupa import LuaRuntime
    lua = LuaRuntime(unpack_returned_tuples=True)
    module = lua.execute((ROOT / 'client' / 'scoring.lua').read_text())

    def to_lua(obj):
        if isinstance(obj, dict):
            return lua.table_from({k: to_lua(v) for k, v in obj.items()})
        if isinstance(obj, list):
            return lua.table_from([to_lua(v) for v in obj])
        return obj
    return module, to_lua


class Parity(unittest.TestCase):
    def test_rules_file_matches_both_engines(self):
        module, _ = lua_runtime()
        self.assertEqual(RULES['rules_version'], scoring.RULES_VERSION)
        self.assertEqual(RULES['rules_version'], module.RULES_VERSION)
        self.assertEqual(RULES['penalties_ms'], scoring.PENALTIES)
        for event, table in RULES['penalties_ms'].items():
            for key, value in table.items():
                self.assertEqual(module.PENALTIES[event][key], value)

    def test_corrected_time_fixtures(self):
        module, to_lua = lua_runtime()
        for fx in RULES['fixtures']:
            with self.subTest(fx['name']):
                py = scoring.corrected_ms(fx['event'], fx['raw_ms'], fx['events'])
                lu = module.corrected_ms(fx['event'], fx['raw_ms'], to_lua(fx['events']))
                self.assertEqual(py, fx['corrected_ms'])
                self.assertEqual(lu, fx['corrected_ms'])
                if lu is not None:
                    self.assertEqual(lu % 1, 0)

    def test_status_fixtures(self):
        module, _ = lua_runtime()
        for fx in RULES['status_fixtures']:
            with self.subTest(fx['name']):
                self.assertEqual(scoring.resolve_status(fx['proposed'], fx['detector_ok']), fx['expected'])
                self.assertEqual(module.resolve_status(fx['proposed'], fx['detector_ok']), fx['expected'])


if __name__ == '__main__':
    unittest.main()
