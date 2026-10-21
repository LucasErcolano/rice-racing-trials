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

    def cone_cfg(self, **over):
        cfg = {'version': 'cone-boxes-v1', 'half_width_m': 0.705, 'half_length_m': 1.48, 'shrink_m': 0.15,
               'cone_radius_m': 0.125, 'min_depth_m': 0.03,
               'cones': [{'id': 'C1', 'x': 10.0, 'z': 0.0}, {'id': 'C2', 'x': 10.0, 'z': 2.0}]}
        cfg.update(over)
        return self.tab(cfg)

    def pose(self, x, z, lx=1.0, lz=0.0):
        return self.tab({'x': x, 'z': z, 'lx': lx, 'lz': lz})

    def test_cone_box_direct_hit_is_candidate_once_and_never_official(self):
        d = self.detectors.cone_boxes(self.cone_cfg())
        r = d.observe(d, self.pose(5, 0), self.pose(9, 0))
        self.assertEqual(r['status'], 'candidate')
        self.assertEqual(r['cones'][1], 'C1')
        again = d.observe(d, self.pose(9, 0), self.pose(12, 0))
        self.assertEqual(again['status'], 'clear')
        self.assertEqual(d.count, 1)
        self.assertFalse(d.official)

    def test_cone_box_sweep_catches_fast_frame_without_tunnelling(self):
        d = self.detectors.cone_boxes(self.cone_cfg())
        self.assertEqual(d.observe(d, self.pose(0, 0), self.pose(18, 0))['status'], 'candidate')

    def test_cone_box_near_miss_and_shrunk_graze_are_not_candidates(self):
        d = self.detectors.cone_boxes(self.cone_cfg())
        # cone 1.0 m beside the centreline: clear of a 0.705 m half-width car
        self.assertEqual(d.observe(d, self.pose(5, -1.0), self.pose(15, -1.0))['status'], 'clear')
        # edge graze: cone centre 0.78 m off axis penetrates the full box by 0.05 but not the shrunk one
        d2 = self.detectors.cone_boxes(self.cone_cfg(cones=self.tab([{'id': 'G', 'x': 10.0, 'z': 0.78}])))
        self.assertEqual(d2.observe(d2, self.pose(5, 0), self.pose(15, 0))['status'], 'clear')
        full = self.detectors.cone_boxes(self.cone_cfg(shrink_m=0.0, cones=self.tab([{'id': 'G', 'x': 10.0, 'z': 0.78}])))
        self.assertEqual(full.observe(full, self.pose(5, 0), self.pose(15, 0))['status'], 'candidate')

    def test_cone_box_respects_heading(self):
        d = self.detectors.cone_boxes(self.cone_cfg(cones=self.tab([{'id': 'S', 'x': 0.0, 'z': 1.0}])))
        # car at origin facing +x: cone 1.0 m to its side is clear; facing +z the same cone is ahead and hit
        self.assertEqual(d.observe(d, self.pose(0, 0), self.pose(0, 0))['status'], 'clear')
        d2 = self.detectors.cone_boxes(self.cone_cfg(cones=self.tab([{'id': 'S', 'x': 0.0, 'z': 1.0}])))
        self.assertEqual(d2.observe(d2, self.pose(0, 0, 0, 1), self.pose(0, 0, 0, 1))['status'], 'candidate')

    def test_cone_box_rejects_bad_config_and_bad_samples(self):
        D = self.detectors
        self.assertIsNone(D.cone_boxes(self.cone_cfg(version='x')))
        self.assertIsNone(D.cone_boxes(self.cone_cfg(shrink_m=0.8)))
        self.assertIsNone(D.cone_boxes(self.cone_cfg(cones=self.tab([]))))
        self.assertIsNone(D.cone_boxes(self.cone_cfg(cones=self.tab([{'id': 'A', 'x': 0, 'z': 0}, {'id': 'A', 'x': 1, 'z': 1}]))))
        d = D.cone_boxes(self.cone_cfg())
        self.assertEqual(d.observe(d, None, self.tab({'x': 1}))['status'], 'unknown')
        self.assertEqual(d.observe(d, None, self.pose(10, 0, 0, 0))['status'], 'unknown')

    def test_cone_box_teleport_does_not_sweep_across_the_track(self):
        d = self.detectors.cone_boxes(self.cone_cfg())
        self.assertEqual(d.observe(d, self.pose(-500, 0), self.pose(500, 0))['status'], 'clear')

    def test_cone_missing_identifiers_never_counts(self):
        detector = self.detectors.cones()
        self.assertEqual(detector.observe(detector, self.tab({'source': 'operator_adjudication', 'decision': 'hit'}))['status'], 'unknown')
        self.assertEqual(detector.count, 0)



    # ---- track limits / slalom runs -------------------------------------------------------
    def ring(self, **extra):
        # 100 m square ring, 10 m wide: outer [-50,50]^2, inner [-40,40]^2 (x, z)
        sq = lambda h: [[-h, -h], [h, -h], [h, h], [-h, h]]
        cfg = {'limits': {'outer': sq(50), 'inner': sq(40)}, 'off_margin_m': 0.3, 'off_hold_s': 0.15,
               'off_rearm_s': 0.5, 'wheel_half_track_m': 0.6, 'wheel_half_base_m': 0.85}
        cfg.update(extra)
        return self.detectors.track_limits(self.tab(cfg))

    def off(self, det, x, z, dt=0.1):
        return det.observe(det, self.tab({'x': x, 'z': z, 'lx': 0, 'lz': 1}), dt)

    def test_track_limits_on_asphalt_and_wheel_on_edge_is_not_off_course(self):
        det = self.ring()
        for x in (-45, -49.5, -40.2):  # centre on asphalt, and car box straddling the edge
            self.assertEqual(self.off(det, x, 0)['status'], 'on_course')
        # centre 1.0 m outside the outer edge: the inner wheels are still within margin of... no, all four out
        self.assertEqual(det.count, 0)

    def test_track_limits_needs_all_four_wheels_out_beyond_margin_and_dedupes(self):
        det = self.ring()
        self.assertEqual(self.off(det, 50.5, 0)['status'], 'on_course')   # wheels still near the edge (<= margin)
        self.assertEqual(det.count, 0)
        self.assertEqual(self.off(det, 52.5, 0)['status'], 'on_course')   # first frame out < hold (0.15 s)
        self.assertEqual(self.off(det, 52.5, 0)['status'], 'off_course')  # fully out for >= hold
        self.assertEqual(self.off(det, 53, 0)['status'], 'off_course')
        self.assertEqual(det.count, 1)                                    # one event per excursion
        for _ in range(6): self.off(det, 45, 0)                           # back on for > rearm
        self.off(det, 52.5, 0)
        self.assertEqual(self.off(det, 52.5, 0)['status'], 'off_course')
        self.assertEqual(det.count, 2)

    def test_track_limits_short_blip_and_bad_data_are_not_penalised(self):
        det = self.ring(off_hold_s=0.25)
        self.assertEqual(self.off(det, 52.5, 0, 0.1)['status'], 'on_course')
        self.assertEqual(self.off(det, 45, 0, 0.1)['status'], 'on_course')
        self.assertEqual(det.count, 0)
        self.assertEqual(det.observe(det, self.tab({'x': 52.5, 'z': 0}), 0.1)['status'], 'unknown')
        self.assertEqual(det.observe(det, self.tab({'x': 52.5, 'z': 0, 'lx': 0, 'lz': 1}), 5)['status'], 'unknown')
        self.assertEqual(det.count, 0)
        self.assertIsNone(self.detectors.track_limits(self.tab({'limits': {'outer': [[0, 0]], 'inner': []}})))

    def slalom_cfg(self):
        # 3 pylons along +x at x = 0,10,20; car travels +x (tangent 1,0). expected sides alternate.
        gates = [{'id': 'G%d' % (i + 1), 'x': 10.0 * i, 'z': 0.0, 'tx': 1.0, 'tz': 0.0,
                  'expected': (1, -1, 1)[i]} for i in range(3)]
        return self.tab({'slalom': {'half_m': 5.5, 'runs': [{'id': 'S1', 'gates': gates}]}})

    def weave(self, machine, sides, z_by_gate=None):
        for i, side in enumerate(sides):
            z = side * 1.5
            # lateral = (x-gx)*-tz + (z-gz)*tx = z for tx=1: side comes from the z offset
            machine.observe(machine, self.tab({'x': 10.0 * i - 1, 'z': z}), self.tab({'x': 10.0 * i + 1, 'z': z}))

    def test_slalom_correct_weave_has_no_violation(self):
        m = self.detectors.slalom_runs(self.slalom_cfg())
        self.weave(m, (1, -1, 1))
        r = m.finalize(m)
        self.assertEqual(len(r.violations), 0)
        self.assertEqual(len(r.incomplete), 0)

    def test_slalom_opposite_entry_phase_is_not_penalised(self):
        # Driver enters on the other side than the reference line but alternates correctly.
        m = self.detectors.slalom_runs(self.slalom_cfg())
        self.weave(m, (-1, 1, -1))
        r = m.finalize(m)
        self.assertEqual(len(r.violations), 0)

    def test_slalom_five_gate_run_inverted_is_zero_not_five(self):
        gates = [{'id': 'G%d' % (i + 1), 'x': 10.0 * i, 'z': 0.0, 'tx': 1.0, 'tz': 0.0,
                  'expected': (-1, 1, -1, 1, -1)[i]} for i in range(5)]
        m = self.detectors.slalom_runs(self.tab({'slalom': {'half_m': 5.5, 'runs': [{'id': 'S2', 'gates': gates}]}}))
        self.weave(m, (1, -1, 1, -1, 1))
        self.assertEqual(len(m.finalize(m).violations), 0)

    def test_slalom_one_break_costs_one_in_either_phase(self):
        gates = [{'id': 'G%d' % (i + 1), 'x': 10.0 * i, 'z': 0.0, 'tx': 1.0, 'tz': 0.0,
                  'expected': (1, -1, 1, -1, 1)[i]} for i in range(5)]
        m = self.detectors.slalom_runs(self.tab({'slalom': {'half_m': 5.5, 'runs': [{'id': 'S2', 'gates': gates}]}}))
        self.weave(m, (-1, -1, 1, -1, 1))   # opposite phase, but pylon 1 is passed on the wrong side
        r = m.finalize(m)
        self.assertEqual([r.violations[i] for i in range(1, len(r.violations) + 1)], ['G1'])

    def test_slalom_wrong_side_is_reported_per_gate(self):
        m = self.detectors.slalom_runs(self.slalom_cfg())
        self.weave(m, (1, 1, 1))   # missed the alternation at gate 2
        r = m.finalize(m)
        self.assertEqual([r.violations[i] for i in range(1, len(r.violations) + 1)], ['G2'])

    def test_slalom_skipped_gate_is_incomplete_not_penalised_and_resets(self):
        m = self.detectors.slalom_runs(self.slalom_cfg())
        for i in (0, 2):   # drove around gate 2 entirely (z far outside the half width)
            m.observe(m, self.tab({'x': 10.0 * i - 1, 'z': 1.5}), self.tab({'x': 10.0 * i + 1, 'z': 1.5}))
        r = m.finalize(m)
        self.assertEqual(len(r.violations), 0)
        self.assertEqual(r.incomplete[1], 'S1')
        m.reset(m)
        self.weave(m, (1, -1, 1))
        self.assertEqual(len(m.finalize(m).violations), 0)
        self.assertEqual(len(m.finalize(m).incomplete), 0)

    def test_slalom_config_validation(self):
        bad = self.tab({'slalom': {'runs': [{'id': 'S1', 'gates': [
            {'id': 'a', 'x': 0, 'z': 0, 'tx': 2, 'tz': 0, 'expected': 1}] * 3}]}})
        self.assertIsNone(self.detectors.slalom_runs(bad))
        self.assertIsNone(self.detectors.slalom_runs(self.tab({})))


@unittest.skipUnless((ROOT / 'assets/source/FSAEJ_Endurance.zip').is_file(),
                     'JSAE track archive is proprietary and gitignored (assets/source); not present in CI')
class ArchiveInspectionTests(unittest.TestCase):
    def test_inspection_emits_real_ai_evidence_but_no_certified_gates(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run([sys.executable, str(ROOT / 'track/inspect.py'),
                                     '--archive', str(ROOT / 'assets/source/FSAEJ_Endurance.zip'),
                                     '--output', tmp], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            candidate = json.loads((Path(tmp) / 'FSAEJ_Endurance.candidate-v1.json').read_text(encoding='utf-8'))
            self.assertEqual(candidate['schema'], 'ai-path-candidate-v1')
            self.assertEqual(candidate['confidence'], 'asset-derived-unsurveyed')
            self.assertEqual(candidate['point_count'], 608)
            self.assertEqual(candidate['surface_validity']['PYLON'], False)
            self.assertEqual(candidate['surface_validity']['ASPHALT'], True)
            self.assertNotIn('certified_gates', candidate)
            self.assertTrue((Path(tmp) / 'FSAEJ_Endurance.candidate-v1.svg').read_text(encoding='utf-8').startswith('<svg'))
            survey = json.loads((Path(tmp) / 'FSAEJ_Endurance.survey-template-v1.json').read_text(encoding='utf-8'))
            self.assertEqual(survey['status'], 'candidate')
            self.assertEqual(survey['gates'], [])
            self.assertEqual(survey['asset_sha256'], candidate['member_sha256']['Physics.kn5'])

    def test_inspection_refuses_wrong_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / 'bad.zip'
            archive.write_bytes(b'not a ZIP')
            result = subprocess.run([sys.executable, str(ROOT / 'track/inspect.py'),
                                     '--archive', str(archive), '--output', tmp], capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(list(Path(tmp).glob('*.candidate-v1.json')))


if __name__ == '__main__':
    unittest.main()
