import json
import tempfile
import unittest
from pathlib import Path

from server import penalties_v2
from server.penalty_calibration import calibration_report, make_template


class PenaltyCalibrationTests(unittest.TestCase):
    def test_template_marks_every_track_detector_uncalibrated(self):
        template = make_template('FSAEJ_Endurance', 'survey-001', 2.0, 1.0)
        self.assertEqual(template['schema'], penalties_v2.CONFIG_SCHEMA)
        self.assertFalse(template['boundaries']['calibrated'])
        self.assertFalse(template['cones']['calibrated'])
        self.assertFalse(template['slaloms']['calibrated'])
        report = calibration_report(template)
        self.assertFalse(report['ready_for_automatic_scoring'])
        self.assertEqual(report['uncalibrated_sections'], ['boundaries', 'cones', 'route', 'slaloms'])

    def test_report_recognizes_synthetic_fully_calibrated_geometry(self):
        from tests.test_penalties_v2 import config
        report = calibration_report(config())
        self.assertTrue(report['structurally_valid'])
        self.assertTrue(report['ready_for_automatic_scoring'])
        self.assertEqual(report['uncalibrated_sections'], [])


if __name__ == '__main__':
    unittest.main()
