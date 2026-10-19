import unittest
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from lupa import LuaRuntime

ROOT = Path(__file__).resolve().parents[2]


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.detectors = self.lua.execute((ROOT / 'client/detectors.lua').read_text())

    def tab(self, value):
        return self.lua.table_from(value, recursive=True)

    def test_off_course_edge_deduplicates_and_requires_return(self):
        detector = self.detectors.off_course(self.tab({'min_out': 2, 'source': 'ac_physics_numberOfTyresOut'}))
        statuses = [detector.observe(detector, self.tab({'numberOfTyresOut': n}))['status'] for n in [0, 2, 3, 1, 2]]
        self.assertEqual(statuses, ['on_course', 'off_course', 'off_course', 'on_course', 'off_course'])
        self.assertEqual(detector.count, 2)
        self.assertEqual(detector.confidence, 'physics_proxy_uncertified')

    def test_off_course_missing_or_malformed_data_fails_closed(self):
        detector = self.detectors.off_course(self.tab({'min_out': 2, 'source': 'ac_physics_numberOfTyresOut'}))
        self.assertEqual(detector.observe(detector, self.tab({'numberOfTyresOut': 2}))['status'], 'off_course')
        self.assertEqual(detector.observe(detector, self.tab({}))['status'], 'unknown')
        self.assertEqual(detector.observe(detector, self.tab({'numberOfTyresOut': 2.5}))['status'], 'unknown')
        self.assertEqual(detector.observe(detector, self.tab({'numberOfTyresOut': 2}))['status'], 'off_course')
        self.assertEqual(detector.count, 1)
        self.assertFalse(detector.official)

    def test_off_course_rejects_unrecognised_source(self):
        self.assertIsNone(self.detectors.off_course(self.tab({'min_out': 2, 'source': 'guessed'})))

    def test_slalom_requires_surveyed_approved_gates_and_direction(self):
        candidate = self.tab({'version': 'slalom-gates-v1', 'status': 'candidate', 'survey_id': 'survey-1', 'asset_sha256': 'abc', 'gates': [
            {'id': 'a', 'a': {'x': -2, 'z': 0}, 'b': {'x': 2, 'z': 0}, 'pylon': {'x': 0, 'z': 0}, 'clearance_m': 0.25, 'direction': -1, 'side': 'left'},
            {'id': 'b', 'a': {'x': -2, 'z': -10}, 'b': {'x': 2, 'z': -10}, 'pylon': {'x': 0, 'z': -10}, 'clearance_m': 0.25, 'direction': -1, 'side': 'right'}]})
        self.assertIsNone(self.detectors.slalom(candidate))
        candidate['status'] = 'surveyed'
        machine = self.detectors.slalom(candidate)
        self.assertIsNotNone(machine)
        self.assertEqual(machine.observe(machine, self.tab({'x': 1, 'z': 1}), self.tab({'x': 1, 'z': -1}))['status'], 'wrong_side')
        self.assertEqual(machine.observe(machine, self.tab({'x': -1, 'z': 1}), self.tab({'x': -1, 'z': -1}))['status'], 'progress')
        self.assertEqual(machine.observe(machine, self.tab({'x': -1, 'z': -1}), self.tab({'x': -1, 'z': -2}))['status'], 'pending')
        self.assertEqual(machine.next_gate, 2)
        self.assertEqual(machine.observe(machine, self.tab({'x': 1, 'z': -9}), self.tab({'x': 1, 'z': -11}))['status'], 'sequence_observed')
        self.assertFalse(machine.official)

    def test_slalom_wrong_order_or_jump_cannot_complete(self):
        gates = self.tab({'version': 'slalom-gates-v1', 'status': 'surveyed', 'survey_id': 'survey-1', 'asset_sha256': 'abc', 'gates': [
            {'id': 'a', 'a': {'x': -2, 'z': 0}, 'b': {'x': 2, 'z': 0}, 'pylon': {'x': 0, 'z': 0}, 'clearance_m': 0.25, 'direction': -1, 'side': 'left'},
            {'id': 'b', 'a': {'x': -2, 'z': -10}, 'b': {'x': 2, 'z': -10}, 'pylon': {'x': 0, 'z': -10}, 'clearance_m': 0.25, 'direction': -1, 'side': 'right'}]})
        machine = self.detectors.slalom(gates)
        self.assertEqual(machine.observe(machine, self.tab({'x': 1, 'z': -9}), self.tab({'x': 1, 'z': -11}))['status'], 'out_of_order')
        self.assertEqual(machine.observe(machine, self.tab({'x': -2, 'z': 1}), self.tab({'x': 2, 'z': -11}))['status'], 'ambiguous')
        self.assertEqual(machine.next_gate, 1)

    def test_cones_require_distinct_adjudicated_contact_not_proximity(self):
        detector = self.detectors.cones()
        self.assertEqual(detector.observe(detector, self.tab({'cone_id': 'p1', 'distance': 0}))['status'], 'unknown')
        self.assertEqual(detector.observe(detector, self.tab({'cone_id': 'p1', 'contact': True, 'source': 'collision'}))['status'], 'review')
        self.assertEqual(detector.count, 0)
        adjudication = self.tab({'cone_id': 'p1', 'contact': True, 'source': 'operator_adjudication', 'decision': 'hit', 'reviewer': 'marshal-1', 'evidence_id': 'video-1'})
        self.assertEqual(detector.observe(detector, adjudication)['status'], 'adjudicated')
        self.assertEqual(detector.observe(detector, adjudication)['status'], 'duplicate')
        self.assertEqual(detector.count, 1)
        self.assertFalse(detector.official)

    def test_cone_missing_identifiers_never_counts(self):
        detector = self.detectors.cones()
        self.assertEqual(detector.observe(detector, self.tab({'source': 'operator_adjudication', 'decision': 'hit'}))['status'], 'unknown')
        self.assertEqual(detector.count, 0)


class ArchiveInspectionTests(unittest.TestCase):
    def test_inspection_emits_real_ai_evidence_but_no_certified_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(ROOT / 'track/inspect.py'),
                                     '--archive', str(ROOT / 'assets/source/FSAEJ_Endurance.zip'),
                                     '--output', tmp], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            candidate = json.loads((Path(tmp) / 'FSAEJ_Endurance.candidate-v1.json').read_text())
            self.assertEqual(candidate['schema'], 'ai-path-candidate-v1')
            self.assertEqual(candidate['confidence'], 'asset-derived-unsurveyed')
            self.assertEqual(candidate['point_count'], 608)
            self.assertEqual(candidate['surface_validity']['PYLON'], False)
            self.assertEqual(candidate['surface_validity']['ASPHALT'], True)
            self.assertNotIn('certified_gates', candidate)
            self.assertTrue((Path(tmp) / 'FSAEJ_Endurance.candidate-v1.svg').read_text().startswith('<svg'))
            survey = json.loads((Path(tmp) / 'FSAEJ_Endurance.survey-template-v1.json').read_text())
            self.assertEqual(survey['status'], 'candidate')
            self.assertEqual(survey['gates'], [])
            self.assertEqual(survey['asset_sha256'], candidate['member_sha256']['Physics.kn5'])

    def test_inspection_refuses_wrong_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'bad.zip'
            archive.write_bytes(b'not a ZIP')
            result = subprocess.run([sys.executable, str(ROOT / 'track/inspect.py'),
                                     '--archive', str(archive), '--output', tmp], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(list(Path(tmp).glob('*.candidate-v1.json')))


if __name__ == '__main__':
    unittest.main()
