"""Optional proprietary-geometry regression. Never downloaded or bundled by tests."""
import json
import os
from pathlib import Path
import unittest

SOURCE=Path(os.environ.get('CLEANLAP_GEOMETRY','/nonexistent'))

@unittest.skipUnless(SOURCE.is_file(),'set CLEANLAP_GEOMETRY to locally extracted proprietary geometry')
class RealGeometryTests(unittest.TestCase):
    def test_all_candidates_retained_and_collision_reduction_is_real(self):
        from cleanlap.geometry import ingest
        from cleanlap.planning import plan, validate_path
        w=ingest(json.loads(SOURCE.read_text(encoding='utf-8')),[0,1])
        self.assertEqual(len(w.cones),758)
        baseline=validate_path(w,w.reference)
        self.assertGreater(len(baseline['cone_contacts']),0)
        p=plan(w)
        self.assertEqual(p['checks']['cone_contacts'],[])
        self.assertEqual(p['checks']['offtrack_segments'],[])
        self.assertEqual(p['checks']['cone_margin_conflicts'],[])
        self.assertLessEqual(p['checks']['max_curvature'],__import__('math').tan(.60)/1.6)
        self.assertTrue(p['slalom_audit']['candidate_sequence_ok'])
        self.assertTrue(p['geometric_clear'],p['violations'])
        self.assertEqual(p['violations'],[])
        from cleanlap.control import simulate
        run=simulate(w,p,laps=3,lateral_offset=.25,heading_offset=.04,
                     steering_gain=.95,acceleration_gain=.85,max_seconds=900)
        self.assertTrue(run['clean_model_lap'],{k:v for k,v in run.items() if k!='telemetry'})
        self.assertEqual(run['laps_completed'],3)
        self.assertEqual(run['cone_margin_conflicts'],[])
        self.assertEqual(run['offtrack_samples'],0)
        self.assertTrue(all(lap['slalom']['candidate_sequence_ok'] for lap in run['lap_reports']))
        self.assertFalse(p['authorized'])
        self.assertIn('slalom_unresolved',p['unresolved'])
