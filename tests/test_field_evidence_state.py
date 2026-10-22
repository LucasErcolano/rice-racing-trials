"""Absent, incomplete, zero-incident and disabled penalty evidence must be distinguishable."""
import unittest
from typing import Any

from tests import test_field_v2_api as api


class FieldEvidenceStateTests(api.FieldV2HttpTests):
    def row(self, lap):
        return next(r for r in self.rows() if r['lap_id'] == lap)

    def penalty(self, lap, **changes) -> dict[str, Any]:
        data = self.payload('penalty', lap)
        data.update(changes)
        return data

    def test_native_only_is_absent_not_zero(self):
        self.assertEqual(self.send_channel('native', self.payload('native', 'A'))[0], 200)
        row = self.row('A')
        self.assertEqual(row['status'], 'PENALTIES_PENDING')
        self.assertEqual(row['penalty_evidence_state'], 'ABSENT')
        self.assertIsNone(row['penalty_ms'])
        self.assertIsNone(row['corrected_preview_ms'])
        self.assertEqual(row['disabled_penalties'], [])

    def test_verified_shape_with_no_events_is_zero_incidents(self):
        self.send_channel('native', self.payload('native', 'Z'))
        self.send_channel('penalty', self.penalty('Z', events=[]))
        row = self.row('Z')
        self.assertEqual(row['penalty_evidence_state'], 'ZERO_INCIDENTS')
        self.assertEqual(row['penalty_ms'], 0)
        self.assertFalse(row['official'])

    def test_incomplete_evidence_is_not_zero_incidents(self):
        data = self.penalty('I', events=[])
        data['evidence'] = dict(data['evidence'], detector_ok=False)
        self.send_channel('penalty', data)
        row = self.row('I')
        self.assertEqual(row['penalty_evidence_state'], 'INCOMPLETE')
        self.assertIn('evidence.detector_ok', row['review_reasons'])

    def test_disabled_penalty_keeps_evidence_and_is_reported(self):
        data = self.penalty('D')
        data['settings'] = dict(data['settings'], enable_cones=False)
        self.send_channel('native', self.payload('native', 'D'))
        self.send_channel('penalty', data)
        row = self.row('D')
        self.assertEqual(row['disabled_penalties'], ['cones'])
        self.assertEqual(row['penalty_ms'], 0)
        self.assertEqual(row['penalty_evidence_state'], 'PENALTIES_DISABLED')
        self.assertEqual(row['penalty_payload']['events'][0]['cone_id'], 'C1')

    def test_with_penalties_and_conflict_states(self):
        self.send_channel('native', self.payload('native', 'P'))
        self.send_channel('penalty', self.penalty('P'))
        self.assertEqual(self.row('P')['penalty_evidence_state'], 'PENALIZED')
        self.assertEqual(self.row('P')['penalty_ms'], 2000)
        self.send_channel('native', self.payload('native', 'X'))
        self.send_channel('penalty', self.penalty('X', raw_ms=61000))
        self.assertEqual(self.row('X')['penalty_evidence_state'], 'CONFLICT')
        self.assertEqual(self.row('X')['status'], 'PENDING_REVIEW')


if __name__ == '__main__':
    unittest.main()
