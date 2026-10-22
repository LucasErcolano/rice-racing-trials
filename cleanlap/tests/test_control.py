import math
import unittest
from test_geometry import fixture
from cleanlap.geometry import ingest
from cleanlap.planning import plan


class ControlTests(unittest.TestCase):
    def test_feedback_bicycle_completes_three_perturbed_laps(self):
        from cleanlap.control import simulate
        w=ingest(fixture()); p=plan(w)
        r=simulate(w,p,laps=3,lateral_offset=.25,heading_offset=.04)
        self.assertTrue(r['completed'],r['stop_reason'])
        self.assertEqual(r['laps_completed'],3)
        self.assertEqual(len(r['lap_reports']),3)
        self.assertTrue(all(lap['cone_contacts']==[] for lap in r['lap_reports']))
        self.assertLess(r['max_lateral_error_m'],.65)
        self.assertEqual(r['cone_contacts'],[])
        self.assertEqual(r['offtrack_samples'],0)
        self.assertEqual(sum(e['phase']=='finish' for e in r['gate_events']),3)
        self.assertTrue(all('target_speed' in row and 'heading_error' in row for row in r['telemetry']))

    def test_unsafe_diagnostic_is_explicit_and_never_authorized(self):
        from cleanlap.control import simulate
        w=ingest(fixture());p=plan(w);p['geometric_clear']=False;p['violations']=['test_unsafe']
        self.assertEqual(simulate(w,p,max_seconds=.1)['stop_reason'],'unsafe_plan')
        r=simulate(w,p,max_seconds=.1,diagnostic_unsafe=True)
        self.assertTrue(r['diagnostic_unsafe'])
        self.assertFalse(r['clean_model_lap'])
        self.assertNotEqual(r['stop_reason'],'unsafe_plan')

    def test_controlled_synthetic_single_cone_contact_is_deduplicated(self):
        from cleanlap.control import simulate
        g=fixture();g['cones']['clusters']=[{'x':20.,'z':0.,'footprint_m':.25}]
        w=ingest(g);p=plan(w);p['points']=w.reference.tolist();p['speeds']=[3.]*len(w.reference)
        p['geometric_clear']=False
        r=simulate(w,p,diagnostic_unsafe=True,max_seconds=90)
        self.assertTrue(r['completed'])
        self.assertEqual(r['cone_contacts'],[0])
        self.assertFalse(r['clean_model_lap'])

    def test_world_plan_identity_mismatch_is_rejected(self):
        from cleanlap.control import simulate
        w=ingest(fixture());p=plan(w);p['geometry_sha256']='wrong'
        with self.assertRaises(ValueError):simulate(w,p)

    def test_unknown_geometry_and_stale_state_brake(self):
        from cleanlap.control import Controller, State
        w=ingest(fixture()); p=plan(w)
        c=Controller(p)
        command=c.update(State(20,0,math.pi/2,0,0),now=0)
        self.assertEqual(command['brake'],1)
        self.assertEqual(command['mode'],'HOLD')
        c=Controller(p,simulation=True)
        self.assertEqual(c.update(State(20,0,math.pi/2,0,0),now=1)['mode'],'STOP')
        self.assertEqual(c.update(State(20,0,math.pi/2,0,1),now=1)['throttle'],0)

if __name__=='__main__':unittest.main()
