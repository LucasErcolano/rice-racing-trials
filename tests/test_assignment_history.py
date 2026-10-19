"""Offline laps must retain server-authenticated assignment identity across reassignment."""
import tempfile
import unittest
from pathlib import Path
from server.app import Store, Problem


class AssignmentHistory(unittest.TestCase):
    def test_uncommissioned_server_does_not_rank_any_event(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'trials.sqlite')
            store.register('sessions', 'session_id', {'session_id': 's1', 'name': 'First'})
            store.register('pilots', 'pilot_id', {'pilot_id': 'p1', 'name': 'First'})
            store.assign({'station_id': 'ST01', 'session_id': 's1', 'pilot_id': 'p1'}, {'ST01': 'secret'})
            for event, events, status in (
                ('autocross', {'doo': 0, 'oc': 0}, 'valid'),
                ('skidpad', {'doo': 0, 'oc': 0, 'right_ms': 60000, 'left_ms': 61000}, 'valid'),
                ('endurance', {'cone': 0, 'off_course': 0, 'slalom': []}, 'VALID'),
            ):
                result = store.submit({'protocol_version': 1, 'station_id': 'ST01',
                    'lap_id': 'uncommissioned-' + event, 'session_id': 's1', 'pilot_id': 'p1',
                    'event': event, 'raw_ms': 70000, 'scoring_version': 1,
                    'events': events, 'status': status, 'detector_ok': True}, 'ST01')
                self.assertNotIn(result['status'], ('valid', 'VALID'))
                self.assertIsNone(result['corrected_ms'])
            self.assertEqual(store.leaderboard('s1')['leaders'], [])

    def test_reassigned_offline_pending_lap_reaches_review_not_ranking(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'trials.sqlite')
            stations = {'ST01': 'secret'}
            store.register('sessions', 'session_id', {'session_id': 's1', 'name': 'First'})
            store.register('pilots', 'pilot_id', {'pilot_id': 'p1', 'name': 'First'})
            store.register('pilots', 'pilot_id', {'pilot_id': 'p2', 'name': 'Second'})
            first = store.assign({'station_id': 'ST01', 'session_id': 's1', 'pilot_id': 'p1'}, stations)
            self.assertIsInstance(first['assignment_revision'], int)
            store.assign({'station_id': 'ST01', 'session_id': 's1', 'pilot_id': 'p2'}, stations)
            payload = {'protocol_version': 1, 'station_id': 'ST01', 'lap_id': 'offline-1',
                       'session_id': 's1', 'pilot_id': 'p1', 'event': 'endurance',
                       'raw_ms': 70000, 'scoring_version': 1, 'events': {},
                       'status': 'PENDING_REVIEW', 'assignment_revision': first['assignment_revision']}
            self.assertEqual(store.submit(payload, 'ST01')['status'], 'PENDING_REVIEW')
            self.assertEqual(store.leaderboard('s1')['leaders'], [])
            self.assertEqual(len(store.laps('s1')), 1)

    def test_unknown_revision_cannot_masquerade_as_old_assignment(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(Path(folder) / 'trials.sqlite')
            store.register('sessions', 'session_id', {'session_id': 's1', 'name': 'First'})
            store.register('pilots', 'pilot_id', {'pilot_id': 'p1', 'name': 'First'})
            store.assign({'station_id': 'ST01', 'session_id': 's1', 'pilot_id': 'p1'}, {'ST01': 'secret'})
            payload = {'protocol_version': 1, 'station_id': 'ST01', 'lap_id': 'forged',
                       'session_id': 's1', 'pilot_id': 'p1', 'event': 'endurance',
                       'raw_ms': 70000, 'scoring_version': 1, 'events': {},
                       'status': 'PENDING_REVIEW', 'assignment_revision': 999999}
            with self.assertRaises(Problem) as raised:
                store.submit(payload, 'ST01')
            self.assertEqual(raised.exception.code, 409)


if __name__ == '__main__': unittest.main()
