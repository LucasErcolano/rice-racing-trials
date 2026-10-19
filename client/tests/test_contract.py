import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

class Contract(unittest.TestCase):
    def test_fail_closed_and_official_sdk_entrypoints(self):
        app = (ROOT / 'RiceTrials.lua').read_text()
        manifest = (ROOT / 'manifest.ini').read_text()
        for token in ('function script.update(dt)', 'function script.windowMain(dt)', 'ac.getCar(0)', 'CALIBRATION_REQUIRED', 'UNVERIFIED', 'FSAEJ_Endurance'):
            self.assertIn(token, app)
        self.assertIn('FUNCTION_MAIN = windowMain', manifest)
        self.assertNotIn('RICE_FSAE_2027_AUTOCROSS_V1', app)

    def test_geometry_is_not_hardcoded(self):
        app = (ROOT / 'RiceTrials.lua').read_text()
        self.assertNotIn('course_definition', app)
        self.assertIn('calibration, route, previous', app)
        self.assertIn('not validGate(config.route.start)', app)
        self.assertIn('function core.crossed', (ROOT / 'core.lua').read_text())
