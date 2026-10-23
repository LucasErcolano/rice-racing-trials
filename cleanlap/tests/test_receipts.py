"""Read-only V2 receipts and geometry diagnostics, never official scoring."""
import json
import math
import unittest
from cleanlap.tests.test_geometry import fixture


class ReceiptTests(unittest.TestCase):
    def test_missing_evidence_is_not_zero_and_identity_is_exact(self):
        from cleanlap.diagnostics import receipt
        from server import field_v2
        identity=dict(station_id='ST01',session_id='S1',pilot_id='P1',track_config='synthetic',lap_id='L1')
        native=dict(protocol='rice-lap-or-v2',channel='native',message_id='N1',raw_ms=10000,
                    rules_version=1,native_valid=True,assignment_revision=1,**identity)
        penalty=dict(native,channel='penalty',message_id='E1',events=[],settings={
            'enable_cones':True,'enable_off_track':True,'enable_slalom':True,
            'cone_penalty_ms':2000,'off_course_penalty_ms':20000,'slalom_penalty_ms':20000},
            evidence=dict(source='rice-client-v2',detector_ok=True,calibration_verified=True,
                          track_config='synthetic',detector={'coverage':'fixture'},review_required=False))
        # Test definitions are validated by the actual schema, not a copied field list.
        field_v2.validate(penalty,'ST01','penalty')
        row=dict(identity,raw_ms=10000,native_received=True,penalty_received=False,
                 native_payload=native,penalty_payload=None,conflicts=[])
        self.assertFalse(receipt(row,identity)['complete'])
        row.update(penalty_received=True,penalty_payload=penalty)
        self.assertFalse(receipt(row,identity)['complete'])  # station flags are not commissioning
        import hashlib
        row.update(evidence_verified=True,verification=dict(approved=True,
            penalty_sha256=hashlib.sha256(field_v2.compact(penalty).encode()).hexdigest()))
        self.assertTrue(receipt(row,identity)['complete'])
        row['raw_ms']=True
        self.assertFalse(receipt(row,identity)['complete'])
        row['raw_ms']=10000
        penalty['lap_id']='OTHER'
        self.assertFalse(receipt(row,identity)['complete'])
        penalty['lap_id']='L1';penalty['evidence']['detector']={}
        self.assertFalse(receipt(row,identity)['complete'])

    def test_receipt_rejects_boolean_identity_and_millisecond_values(self):
        from cleanlap.diagnostics import receipt
        bad={'candidate_id':True,'attempt_id':'a','assignment_revision':0,'lap_id':'l','lap_seq':1}
        self.assertIn('invalid_expected_identity',receipt({},bad)['reasons'])

    def test_automatic_diagnostics_are_model_evidence_not_native_receipts(self):
        from cleanlap.diagnostics import diagnose
        from cleanlap.geometry import ingest
        world=ingest(fixture())
        rows=[dict(x=20.,z=i*.1,heading=math.pi/2,time=i*.02,speed=1.) for i in range(3)]
        report=diagnose(world,rows)
        self.assertEqual(report['evidence'],'GEOMETRIC_DIAGNOSTIC_NOT_OFFICIAL')
        self.assertEqual(report['candidate_contacts'],[])
        self.assertEqual(report['offtrack_samples'],0)
        self.assertIsNone(report['native_lap_ms'])
        self.assertFalse(report['checkpoints_complete'])


if __name__=='__main__': unittest.main()
