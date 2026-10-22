"""Administrative field verification is audited, opt-in, and never official scoring."""
import unittest
from typing import Any
from tests import test_field_v2_api as api


class FieldReviewTests(api.FieldV2HttpTests):
    # Avoid re-running the inherited HTTP contract suite in this module.
    __unittest_skip__ = False

    def review(self, approved: Any = True, token: str | None = 'admin', **changes):
        body = dict(station_id='ST01', session_id='S1', lap_id='L1', approved=approved,
                    reviewer='operator', reason='Observed and reviewed captured lap evidence')
        body.update(changes)
        return self.call('POST', '/api/admin/v2/field-review', body, token)

    def test_reviewed_penalty_only_shows_corrected_without_entering_official_rank(self):
        payload = self.payload('penalty')
        payload['evidence']['calibration_verified'] = False
        self.send_channel('penalty', payload)
        code, row = self.review()
        self.assertEqual(code, 200)
        self.assertEqual(row['status'], 'VERIFIED_PROVISIONAL')
        self.assertTrue(row['evidence_verified'])
        self.assertEqual(row['preview_ms'], 62000)
        self.assertIsNone(row['corrected_ms'])
        self.assertFalse(row['official'])
        self.assertEqual(row['verification']['reviewer'], 'operator')
        self.assertEqual(self.call('GET', '/api/leaderboard?session_id=S1', token=None)[1]['ranking'], {})
        self.stop()
        self.store = api.Store(self.path, penalties_v2_enabled=True)
        self.start()
        self.assertTrue(self.rows()[0]['evidence_verified'])
        self.assertEqual(self.review(False)[1]['status'], 'NATIVE_PENDING')
        self.assertFalse(self.rows()[0]['evidence_verified'])
        history = self.call('GET', '/api/admin/v2/field-reviews?station_id=ST01&session_id=S1&lap_id=L1', token='admin')[1]['reviews']
        self.assertEqual([r['approved'] for r in history], [True, False])
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM field_v2_reviews').fetchone()[0], 2)

    def test_review_requires_admin_reason_and_penalty_evidence(self):
        self.send_channel()
        self.assertEqual(self.review(token=None)[0], 401)
        self.assertEqual(self.review(token='station')[0], 403)
        self.assertEqual(self.review(reason='')[0], 400)
        self.assertEqual(self.review(approved='true')[0], 400)
        self.assertEqual(self.review()[0], 409)
        self.assertEqual(self.review(lap_id='missing')[0], 404)
        self.store.penalties_v2_enabled = False
        self.assertEqual(self.review()[0], 404)

    def test_conflict_cannot_be_approved_and_invalidates_previous_verification(self):
        self.send_channel('penalty')
        self.assertEqual(self.review()[0], 200)
        changed = self.payload('native')
        changed['raw_ms'] += 1
        self.send_channel('native', changed)
        self.assertFalse(self.rows()[0]['evidence_verified'])
        self.assertIsNone(self.rows()[0]['preview_ms'])
        self.assertEqual(self.review()[0], 409)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(FieldReviewTests(name) for name in FieldReviewTests.__dict__
                              if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main()
