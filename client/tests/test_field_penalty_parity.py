"""Field evidence arithmetic must equal the local HUD, including overlap dedupe."""
import itertools
import json
from pathlib import Path
import sys
import unittest
from typing import Any
from test_field_v2 import runtime
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.scoring import corrected_ms_v2


class FieldPenaltyParity(unittest.TestCase):
    def test_all_toggles_overlap_and_excluded_contacts_match_local_hud(self):
        lua, _, _, cv = runtime()
        module: Any = lua.eval("require('field_v2')")
        if isinstance(module, tuple):
            module = module[0]
        self.assertIsNotNone(module.penalty_events, 'field evidence needs HUD-equivalent event eligibility')
        apply: Any = lua.eval("require('penalties').apply")
        for cone, off, slalom in itertools.product((False, True), repeat=3):
            with self.subTest(cone=cone, off=off, slalom=slalom):
                cfg = {'cone': {'on': cone, 's': 2}, 'off_course': {'on': off, 's': 10},
                       'slalom': {'on': slalom, 's': 30}, 'dedupe_ms': 3000}
                events = module.penalty_events(cv(['C1', 'REF', 'C2']), cv({'REF': True}), 1,
                                              cv([1, 5]), cv(['G1']), cv({'G1': 1.1}), cv(cfg))
                globals_: Any = lua.globals()
                events = json.loads(globals_.JSON.stringify(events))
                settings = dict(enable_cones=cone, enable_off_track=off, enable_slalom=slalom,
                                cone_penalty_ms=2000, off_course_penalty_ms=10000, slalom_penalty_ms=30000)
                expected = apply(cv(cfg), cv({'cone': 1, 'off_course': 2, 'slalom': 1}),
                                 cv({'off': [1, 5], 'slalom': [1.1]})).penalty_ms
                self.assertEqual(corrected_ms_v2(60000, events, settings), 60000 + expected)
                self.assertEqual(len(events), 6, 'excluded and capped contacts remain evidence')
