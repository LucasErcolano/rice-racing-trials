"""Geometry regressions and reproducible, non-proprietary workload benchmark."""
import copy
import itertools
import math
from pathlib import Path
import unittest
from typing import Any

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

from server import penalties_v2
from tests.test_penalties_v2 import config, sample, trajectory


def calibrated_config():
    cfg = config()
    cfg['route']['calibrated'] = True
    return cfg


def excursion_config():
    cfg = calibrated_config()
    cfg['cones']['items'] = []
    cfg['boundaries']['outer'] = [[-100, -5], [100, -5], [100, 5], [-100, 5]]
    cfg['slaloms']['sections'] = [{
        'id': 'S1', 'start_s_m': 4, 'end_s_m': 16,
        'gates': [{'id': 'G1', 'a': [10, -5], 'b': [10, 5], 'direction': -1}],
    }]
    return cfg


def excursion_trace():
    return trajectory([sample(0, 0), sample(500, 5, 8), sample(1000, 10, 8),
                       sample(1500, 15, 8), sample(2000, 20, 8),
                       sample(2500, 25), sample(3000, 30)])


class CalibrationGateTests(unittest.TestCase):
    def test_each_detector_section_requires_boolean_calibration_even_when_empty(self):
        reasons = {'cones': 'cone_geometry_uncalibrated',
                   'boundaries': 'track_boundaries_uncalibrated',
                   'slaloms': 'slalom_geometry_uncalibrated'}
        for section, reason in reasons.items():
            for flag in (False, None, 1, 'true'):
                with self.subTest(section=section, flag=flag):
                    cfg = calibrated_config()
                    cfg['cones']['items'] = []
                    if flag is None:
                        del cfg[section]['calibrated']
                    else:
                        cfg[section]['calibrated'] = flag
                    result = penalties_v2.analyze_trajectory(
                        trajectory([sample(0, 0), sample(100, 1)]), cfg)
                    self.assertIn(reason, result['review_reasons'])
                    self.assertTrue(result['review_required'])
                    self.assertEqual(result['penalty_ms'], 0)

    def test_route_requires_explicit_boolean_calibration_before_progress_scoring(self):
        for flag in (False, None, 1, 'true'):
            with self.subTest(flag=flag):
                cfg = excursion_config()
                if flag is None:
                    del cfg['route']['calibrated']
                else:
                    cfg['route']['calibrated'] = flag
                result = penalties_v2.analyze_trajectory(excursion_trace(), cfg)
                self.assertIn('route_geometry_uncalibrated', result['review_reasons'])
                self.assertEqual(result['penalty_ms'], 0)
                self.assertFalse(any(e['type'] in ('off_course', 'slalom_violation')
                                     for e in result['events']))


class ConeContactGeometryTests(unittest.TestCase):
    def test_reference_line_cones_keep_contact_evidence_but_never_add_penalty(self):
        cfg = calibrated_config()
        cfg['cones']['ref_line_cones'] = ['C1']
        for z in (0, 4):
            with self.subTest(z=z):
                trace = trajectory([sample(0, 0, z), sample(1000, 10, z)])
                trace['contact_events'] = [
                    {'type': 'cone_contact', 'source': 'csp_collision', 'cone_id': 'C1',
                     't_ms': 500, 'position': {'x': 5, 'z': 0}, 'contact_id': 'direct-1'}]
                result = penalties_v2.analyze_trajectory(trace, cfg)
                contacts = [e for e in result['events'] if e['type'] == 'cone_contact']
                self.assertEqual(len(contacts), 1)
                self.assertEqual(result['penalty_ms'], 0)
                self.assertFalse(contacts[0]['penalizable'])
                self.assertEqual(contacts[0]['evidence']['exclusion'], 'reference_line_cone')

    def test_v1_auto_cone_cap_retains_all_evidence_and_requires_review(self):
        cfg = calibrated_config()
        cfg['cones']['items'] = [{'id': f'C{i:02}', 'x': 5, 'z': 0, 'radius_m': 0.125}
                                 for i in range(27)]
        cfg['cones']['ref_line_cones'] = ['C00']
        result = penalties_v2.analyze_trajectory(
            trajectory([sample(0, 0), sample(1000, 10)]), cfg)
        self.assertEqual(result['penalty_counts']['touched_cones'], 27)
        self.assertEqual(result['penalty_ms'], 25 * 2000)
        self.assertIn('cone_auto_cap_exceeded', result['review_reasons'])
        capped = [e for e in result['events'] if e['evidence'].get('exclusion') == 'automatic_cone_cap']
        self.assertEqual([e['cone_id'] for e in capped], ['C26'])

    def test_v1_shrink_and_minimum_depth_reject_edge_grazes(self):
        for z, expected in ((0.78, 0), (0.6501, 0), (0.6499, 1)):
            with self.subTest(z=z):
                cfg = calibrated_config()
                cfg['vehicle'].update(length_m=2.96, width_m=1.41)
                cfg['cones']['items'] = [{'id': 'C1', 'x': 5, 'z': z, 'radius_m': 0.125}]
                result = penalties_v2.analyze_trajectory(
                    trajectory([sample(0, 0), sample(1000, 10)]), cfg)
                self.assertEqual(result['penalty_counts']['touched_cones'], expected)
                self.assertEqual(result['penalty_ms'], 2000 * expected)
                if expected:
                    evidence = result['events'][0]['evidence']
                    self.assertEqual(evidence['shrink_m'], 0.15)
                    self.assertEqual(evidence['min_depth_m'], 0.03)
                    self.assertGreaterEqual(evidence['penetration_m'], 0.03)


def realistic_workload(sample_count=250, cone_count=100, route_count=500):
    """60 s closed circuit, 100 m radius, 4 m asphalt half-width; no assets."""
    cfg = calibrated_config()
    def ring(radius, count):
        return [[radius * math.cos(2 * math.pi * i / count),
                 radius * math.sin(2 * math.pi * i / count)] for i in range(count)]
    cfg['route'].update(points=ring(100, route_count), closed=True)
    cfg['boundaries'].update(outer=ring(104, route_count), inner=ring(96, route_count))
    cfg['cones']['items'] = [{'id': f'C{i}', 'x': x, 'z': z, 'radius_m': 0.125}
                             for i, (x, z) in enumerate(ring(104, cone_count))]
    frames = []
    for i in range(sample_count):
        angle = 2 * math.pi * i / (sample_count - 1)
        frames.append(sample(round(60000 * i / (sample_count - 1)),
                             100 * math.cos(angle), 100 * math.sin(angle),
                             -math.sin(angle), math.cos(angle),
                             -10.472 * math.sin(angle), 10.472 * math.cos(angle)))
    trace = trajectory(frames)
    trace['lap']['raw_ms'] = 60000
    return trace, cfg


class AnalysisWorkLimitTests(unittest.TestCase):
    def test_interpolation_alone_is_bounded_even_without_cones_or_route(self):
        cfg = calibrated_config()
        cfg['cones']['items'] = []
        cfg['route']['points'] = []
        cfg['boundaries'].update(calibrated=False, outer=[], inner=[])
        cfg['slaloms']['calibrated'] = False
        trace = trajectory([sample(i * 1000, i * 40) for i in range(2001)])
        trace['lap']['raw_ms'] = 2000000
        with self.assertRaisesRegex(penalties_v2.TrajectoryError, 'analysis work budget exceeded'):
            penalties_v2.analyze_trajectory(trace, cfg)

    def test_realistic_workload_is_rejected_before_expensive_geometry(self):
        trace, cfg = realistic_workload()
        with self.assertRaisesRegex(penalties_v2.TrajectoryError, 'analysis work budget exceeded') as caught:
            penalties_v2.analyze_trajectory(trace, cfg)
        self.assertEqual(type(caught.exception).__name__, 'AnalysisLimitError')


class DetectionEvidenceTests(unittest.TestCase):
    def test_independent_penalty_toggles_preserve_detection_and_incident_dedupe(self):
        cfg = excursion_config()
        cfg['cones']['items'] = [{'id': 'C1', 'x': 10, 'z': 8, 'radius_m': 0.125}]
        baseline = penalties_v2.analyze_trajectory(excursion_trace(), cfg)
        baseline_detection = [(e['type'], e.get('cone_id'), e['timestamp_ms'])
                              for e in baseline['events']]
        self.assertEqual(baseline['penalty_ms'], 22000)
        for cones, off, slalom in itertools.product((False, True), repeat=3):
            with self.subTest(cones=cones, off=off, slalom=slalom):
                result = penalties_v2.analyze_trajectory(excursion_trace(), cfg,
                    {'enable_cones': cones, 'enable_off_track': off, 'enable_slalom': slalom})
                self.assertEqual([(e['type'], e.get('cone_id'), e['timestamp_ms'])
                                  for e in result['events']], baseline_detection)
                self.assertEqual(result['penalty_ms'], (2000 if cones else 0) +
                                 (20000 if off or slalom else 0))
                self.assertEqual(result['penalty_counts']['touched_cones'], 1)
                off_event = next(e for e in result['events'] if e['type'] == 'off_course')
                slalom_event = next(e for e in result['events'] if e['type'] == 'slalom_violation')
                if off and slalom:
                    self.assertEqual(slalom_event['incident_id'], off_event['incident_id'])
                    self.assertFalse(slalom_event['penalizable'])

    def test_direct_and_swept_contact_retries_count_one_cone(self):
        cfg = calibrated_config()
        trace = trajectory([sample(0, 0), sample(1000, 10), sample(1500, 5)])
        contact = {'type': 'cone_contact', 'source': 'csp_collision', 'cone_id': 'C1',
                   't_ms': 500, 'position': {'x': 5, 'z': 0}, 'contact_id': 'contact-1'}
        trace['contact_events'] = [contact, copy.deepcopy(contact)]
        result = penalties_v2.analyze_trajectory(trace, cfg)
        self.assertEqual(result['penalty_counts']['touched_cones'], 1)
        self.assertEqual(result['penalty_ms'], 2000)
        event = next(e for e in result['events'] if e['type'] == 'cone_contact')
        self.assertEqual(event['evidence']['direct_collision_event']['contact_id'], 'contact-1')

    @unittest.skipIf(LuaRuntime is None, 'lupa is required for unmodified V1 Lua parity')
    def test_contact_thresholds_match_unmodified_v1_lua_in_two_headings(self):
        assert LuaRuntime is not None
        lua = LuaRuntime(unpack_returned_tuples=True)
        detectors: Any = lua.execute((Path(__file__).resolve().parents[1] /
                                 'client/detectors.lua').read_text(encoding='utf-8'))
        for lateral, turn in itertools.product((0, 0.6499, 0.6501, 0.78), (False, True)):
            with self.subTest(lateral=lateral, turn=turn):
                cfg = calibrated_config()
                cfg['vehicle'].update(length_m=2.96, width_m=1.41)
                x, z = (-lateral, 5) if turn else (5, lateral)
                cfg['cones']['items'] = [{'id': 'C1', 'x': x, 'z': z, 'radius_m': 0.125}]
                v1_config = {'version': 'cone-boxes-v1', 'half_width_m': 0.705,
                             'half_length_m': 1.48, 'shrink_m': 0.15,
                             'cone_radius_m': 0.125, 'min_depth_m': 0.03,
                             'cones': [{'id': 'C1', 'x': x, 'z': z}]}
                machine = detectors.cone_boxes(lua.table_from(v1_config, recursive=True))
                a = {'x': 0, 'z': 0, 'lx': 0 if turn else 1, 'lz': 1 if turn else 0}
                b = dict(a, x=0 if turn else 10, z=10 if turn else 0)
                v1 = machine.observe(machine, lua.table_from(a), lua.table_from(b))
                trace = trajectory([sample(0, a['x'], a['z'], a['lx'], a['lz']),
                                    sample(1000, b['x'], b['z'], b['lx'], b['lz'])])
                v2 = penalties_v2.analyze_trajectory(trace, cfg)
                self.assertEqual(v2['penalty_counts']['touched_cones'], v1['count'])


if __name__ == '__main__':
    unittest.main()
