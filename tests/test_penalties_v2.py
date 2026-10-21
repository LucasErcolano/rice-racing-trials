import unittest

from server import penalties_v2, scoring


def config():
    return {
        'schema': 'rice-penalties-v2',
        'track': 'synthetic-test',
        'calibration': {'status': 'calibrated', 'survey_id': 'test-survey-1'},
        'boundaries': {
            'calibrated': True,
            'outer': [[-100, -10], [100, -10], [100, 10], [-100, 10]],
            'inner': [],
        },
        'route': {'closed': False, 'direction': 1, 'points': [[0, 0], [100, 0]]},
        'vehicle': {
            'length_m': 2.0,
            'width_m': 1.0,
            'wheel_offsets': [
                {'id': 'FL', 'forward_m': 0.8, 'right_m': -0.4},
                {'id': 'FR', 'forward_m': 0.8, 'right_m': 0.4},
                {'id': 'RL', 'forward_m': -0.8, 'right_m': -0.4},
                {'id': 'RR', 'forward_m': -0.8, 'right_m': 0.4},
            ],
        },
        'cones': {'calibrated': True, 'items': [{'id': 'C1', 'x': 5.0, 'z': 0.0, 'radius_m': 0.1}]},
        'slaloms': {'calibrated': True, 'sections': []},
    }


def trajectory(samples, lap_id='L1'):
    return {
        'protocol': 'rice-trajectory-v2',
        'lap': {'station_id': 'ST01', 'session_id': 'S1', 'lap_id': lap_id, 'raw_ms': 10000},
        'samples': samples,
    }


def sample(t_ms, x, z=0.0, fx=1.0, fz=0.0, vx=10.0, vz=0.0):
    return {
        't_ms': t_ms,
        'position': {'x': x, 'z': z},
        'forward': {'x': fx, 'z': fz},
        'velocity': {'x': vx, 'z': vz},
    }


class SweptConeTests(unittest.TestCase):
    def test_high_speed_interframe_contact_is_counted_once_per_cone(self):
        trace = trajectory([sample(0, 0), sample(1000, 10), sample(1100, 10)])
        result = penalties_v2.analyze_trajectory(trace, config())

        cones = [event for event in result['events'] if event['type'] == 'cone_contact']
        self.assertEqual([event['cone_id'] for event in cones], ['C1'])
        self.assertGreaterEqual(cones[0]['timestamp_ms'], 0)
        self.assertLessEqual(cones[0]['timestamp_ms'], 1000)
        self.assertEqual(cones[0]['lap_id'], 'L1')
        self.assertEqual(result['penalty_counts']['touched_cones'], 1)
        self.assertEqual(result['penalty_ms'], 2000)


class ConeConfigurationTests(unittest.TestCase):
    def test_uncalibrated_cones_require_review_without_penalty(self):
        cfg = config()
        cfg['cones']['calibrated'] = False
        result = penalties_v2.analyze_trajectory(trajectory([sample(0, 0), sample(100, 10)]), cfg)
        self.assertEqual(result['penalty_counts']['touched_cones'], 0)
        self.assertEqual(result['penalty_ms'], 0)
        self.assertTrue(result['review_required'])
        self.assertIn('cone_geometry_uncalibrated', result['review_reasons'])

    def test_direct_csp_collision_event_maps_to_calibrated_cone(self):
        cfg = config()
        trace = trajectory([sample(0, 0, 4), sample(1000, 10, 4)])
        trace['contact_events'] = [{'type': 'cone_contact', 'source': 'csp_collision', 'cone_id': 'C1',
                                   't_ms': 500, 'position': {'x': 5, 'z': 0}, 'contact_id': 'contact-1'}]
        result = penalties_v2.analyze_trajectory(trace, cfg)
        cones = [event for event in result['events'] if event['type'] == 'cone_contact']
        self.assertEqual(len(cones), 1)
        self.assertEqual(cones[0]['cone_id'], 'C1')
        self.assertEqual(cones[0]['evidence']['method'], 'direct_csp_collision_event')
        self.assertEqual(result['penalty_ms'], 2000)

    def test_close_pass_outside_vehicle_cone_clearance_is_not_contact(self):
        cfg = config()
        trace = trajectory([sample(0, 0, 0.8), sample(1000, 10, 0.8)])
        result = penalties_v2.analyze_trajectory(trace, cfg)
        self.assertFalse(any(event['type'] == 'cone_contact' for event in result['events']))
        self.assertEqual(result['penalty_counts']['touched_cones'], 0)

    def test_cone_penalty_can_be_disabled_without_losing_contact_evidence(self):
        result = penalties_v2.analyze_trajectory(
            trajectory([sample(0, 0), sample(1000, 10)]), config(), {'enable_cones': False})
        event = next(event for event in result['events'] if event['type'] == 'cone_contact')
        self.assertFalse(event['penalizable'])
        self.assertEqual(result['penalty_counts']['touched_cones'], 1)
        self.assertEqual(result['penalty_ms'], 0)


class OffTrackTests(unittest.TestCase):
    def test_all_four_wheels_out_and_forward_reentry_counts_once(self):
        cfg = config()
        cfg['cones']['items'] = []
        cfg['boundaries']['outer'] = [[-100, -5], [100, -5], [100, 5], [-100, 5]]
        samples = [sample(0, 10), sample(100, 11), sample(200, 12), sample(300, 13, 8),
                   sample(400, 14, 8), sample(500, 15, 8), sample(600, 16), sample(700, 17)]
        result = penalties_v2.analyze_trajectory(
            trajectory(samples), cfg, {'off_track_hold_ms': 100, 'max_gap_ms': 200})
        events = [event for event in result['events'] if event['type'] == 'off_course']
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]['penalizable'])
        self.assertLess(events[0]['s_exit_m'], events[0]['s_reentry_m'])
        self.assertEqual(result['penalty_counts']['off_course_events'], 1)
        self.assertEqual(result['penalty_ms'], 20000)

    def test_closed_loop_wraparound_reentry_is_forward_progress(self):
        cfg = config()
        cfg['cones']['items'] = []
        cfg['boundaries']['outer'] = [[-1, -1], [11, -1], [11, 11], [-1, 11]]
        cfg['route'] = {'closed': True, 'direction': 1, 'points': [[0, 0], [10, 0], [10, 10], [0, 10]]}
        samples = [sample(0, 0, 1), sample(100, -3, 0), sample(250, -4, 0), sample(400, 3, 0), sample(500, 4, 0)]
        result = penalties_v2.analyze_trajectory(
            trajectory(samples), cfg, {'off_track_hold_ms': 100, 'max_gap_ms': 500, 'progress_epsilon_m': 0.1})
        events = [event for event in result['events'] if event['type'] == 'off_course']
        self.assertEqual(len(events), 1)
        self.assertGreater(events[0]['s_reentry_m'], events[0]['s_exit_m'])
        self.assertTrue(events[0]['penalizable'])
        self.assertEqual(result['penalty_ms'], 20000)

    def test_reentry_at_or_before_exit_is_recorded_but_not_penalized(self):
        cfg = config()
        cfg['cones']['items'] = []
        cfg['boundaries']['outer'] = [[-100, -5], [100, -5], [100, 5], [-100, 5]]
        samples = [sample(0, 10), sample(100, 11), sample(200, 12), sample(300, 13, 8),
                   sample(400, 12, 8), sample(500, 10), sample(600, 9)]
        result = penalties_v2.analyze_trajectory(
            trajectory(samples), cfg, {'off_track_hold_ms': 100, 'max_gap_ms': 200})
        events = [event for event in result['events'] if event['type'] == 'off_course']
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]['penalizable'])
        self.assertEqual(result['penalty_counts']['off_course_events'], 1)
        self.assertEqual(result['penalty_ms'], 0)


class SlalomTests(unittest.TestCase):
    def _config(self):
        cfg = config()
        cfg['cones']['items'] = []
        cfg['slaloms'] = {'calibrated': True, 'sections': [{
            'id': 'S1', 'start_s_m': 4.0, 'end_s_m': 16.0,
            'gates': [
                {'id': 'G1', 'a': [5.0, -5.0], 'b': [5.0, 5.0], 'direction': -1},
                {'id': 'G2', 'a': [10.0, -5.0], 'b': [10.0, 5.0], 'direction': -1},
                {'id': 'G3', 'a': [15.0, -5.0], 'b': [15.0, 5.0], 'direction': -1},
            ],
        }]}
        return cfg

    def test_missing_several_gates_is_one_slalom_and_one_penalty(self):
        trace = trajectory([sample(0, 0, 8), sample(1000, 20, 8)])
        result = penalties_v2.analyze_trajectory(trace, self._config())
        missed = [event for event in result['events'] if event['type'] == 'slalom_violation']
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0]['slalom_id'], 'S1')
        self.assertEqual(missed[0]['reasons']['missing_gates'], ['G1', 'G2', 'G3'])
        self.assertEqual(result['penalty_counts']['missed_slaloms'], 1)
        self.assertEqual(result['penalty_counts']['off_course_events'], 1)
        self.assertEqual(result['penalty_ms'], 20000)

    def test_correct_ordered_gate_traversal_is_clean(self):
        trace = trajectory([sample(0, 0), sample(1000, 20)])
        result = penalties_v2.analyze_trajectory(trace, self._config())
        crossed = [event for event in result['events'] if event['type'] == 'slalom_gate_crossing']
        self.assertEqual(len(crossed), 3)
        self.assertEqual([event['gate_id'] for event in crossed], ['G1', 'G2', 'G3'])
        self.assertEqual([event for event in result['events'] if event['type'] == 'slalom_violation'], [])
        self.assertEqual(result['penalty_counts']['missed_slaloms'], 0)

    def test_off_course_and_missed_slalom_share_one_penalty_incident(self):
        cfg = self._config()
        cfg['boundaries']['outer'] = [[-100, -5], [100, -5], [100, 5], [-100, 5]]
        samples = [sample(0, 0, 0), sample(500, 5, 8), sample(1000, 10, 8),
                   sample(1500, 15, 8), sample(2000, 20, 8), sample(2500, 25, 0), sample(3000, 30, 0)]
        result = penalties_v2.analyze_trajectory(trajectory(samples), cfg)
        incidents = {event['incident_id'] for event in result['events']
                     if event['type'] in ('off_course', 'slalom_violation') and event['penalty_ms'] > 0}
        self.assertEqual(len(incidents), 1)
        self.assertEqual(result['penalty_counts']['off_course_events'], 1)
        self.assertEqual(result['penalty_ms'], 20000)
        missed = next(event for event in result['events'] if event['type'] == 'slalom_violation')
        self.assertFalse(missed['penalizable'])
        self.assertTrue(missed['evidence']['deduplicated_with'])

    def test_partial_slalom_section_is_reviewed_not_penalized(self):
        trace = trajectory([sample(0, 8), sample(300, 12)])
        result = penalties_v2.analyze_trajectory(trace, self._config())
        self.assertEqual(result['penalty_counts']['missed_slaloms'], 0)
        self.assertTrue(any(reason == 'slalom_section_incomplete:S1' for reason in result['review_reasons']))
        self.assertEqual([event for event in result['events'] if event['type'] == 'slalom_violation'], [])

    def test_wrong_gate_direction_is_one_reviewable_slalom_event(self):
        cfg = self._config()
        cfg['slaloms']['sections'][0]['gates'][0]['direction'] = 1
        result = penalties_v2.analyze_trajectory(trajectory([sample(0, 0), sample(1000, 20)]), cfg)
        missed = [event for event in result['events'] if event['type'] == 'slalom_violation']
        self.assertEqual(len(missed), 1)
        self.assertIn('wrong_direction', missed[0]['reasons'])
        self.assertEqual(result['penalty_counts']['missed_slaloms'], 1)

class V2ScoringTests(unittest.TestCase):
    def test_corrected_ms_v2_counts_unique_penalizable_incidents_from_events(self):
        events = [
            {'type': 'cone_contact', 'cone_id': 'C1', 'penalizable': True},
            {'type': 'cone_contact', 'cone_id': 'C2', 'penalizable': True},
            {'type': 'off_course', 'incident_id': 'I1', 'penalizable': True},
            {'type': 'slalom_violation', 'incident_id': 'I1', 'penalizable': False},
            {'type': 'slalom_violation', 'incident_id': 'I2', 'penalizable': True},
        ]
        settings = {'enable_cones': True, 'enable_off_track': True, 'enable_slalom': True,
                    'cone_penalty_ms': 2000, 'off_course_penalty_ms': 20000, 'slalom_penalty_ms': 5000}
        self.assertEqual(scoring.corrected_ms_v2(60000, events, settings), 89000)

    def test_corrected_ms_v2_toggles_do_not_change_evidence_events(self):
        events = [{'type': 'cone_contact', 'cone_id': 'C1', 'penalizable': True}]
        settings = {'enable_cones': False, 'enable_off_track': True, 'enable_slalom': True,
                    'cone_penalty_ms': 2000, 'off_course_penalty_ms': 20000, 'slalom_penalty_ms': 20000}
        self.assertEqual(scoring.corrected_ms_v2(60000, events, settings), 60000)


if __name__ == '__main__':
    unittest.main()
