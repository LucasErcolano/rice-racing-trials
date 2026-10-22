import unittest
import math
import numpy as np
from test_geometry import fixture
from cleanlap.geometry import ingest


class PlanningTests(unittest.TestCase):
    def test_periodic_refinement_solves_synthetic_slalom_with_full_sweeps(self):
        from cleanlap.planning import plan, curvature, Vehicle
        g=fixture(); gates=[]
        for i,angle in enumerate([.4,.95,1.5]):
            x,z=20*math.cos(angle),20*math.sin(angle)
            g['cones']['clusters'].append({'x':x,'z':z,'footprint_m':.25})
            gates.append({'id':str(i),'x':x,'z':z,'tx':-math.sin(angle),'tz':math.cos(angle)})
        w=ingest(g);w.slaloms=[{'id':'synthetic','gates':gates,'confidence':1.}]
        p=plan(w)
        self.assertTrue(p['geometric_clear'],p['violations'])
        self.assertTrue(p['refinement_trials'])
        self.assertTrue(p['slalom_audit']['candidate_sequence_ok'])
        self.assertEqual(p['checks']['cone_margin_conflicts'],[])
        self.assertEqual(p['checks']['offtrack_segments'],[])
        self.assertFalse(p['authorized'])
        # Every edge, including the cyclic seam, respects the speed envelope.
        v=np.array(p['speeds']); points=np.array(p['points']); car=Vehicle()
        ds=np.linalg.norm(np.roll(points,-1,axis=0)-points,axis=1)
        self.assertTrue(np.all(v*v*abs(curvature(points))<=car.lateral_accel+1e-8))
        self.assertTrue(np.all(np.roll(v,-1)**2-v*v<=2*car.acceleration*ds+1e-8))
        self.assertTrue(np.all(v*v-np.roll(v,-1)**2<=2*car.braking*ds+1e-8))

    def test_default_body_encloses_host_source_collider(self):
        # Source collider aa44e268... has X ±.705000043 and Z [-1.516238263,1.451433617].
        # This is a conservative source-body bound, not the unverified wheel envelope.
        from cleanlap.planning import Vehicle
        self.assertGreaterEqual(Vehicle().half_length,1.516238263)
        self.assertGreaterEqual(Vehicle().half_width,.705000043)

    def test_planner_moves_off_ai_line_around_candidate_cone(self):
        from cleanlap.planning import plan, validate_path, Vehicle
        g=fixture(); g['cones']['clusters']=[{'x':20.,'z':0.,'footprint_m':0.25}]
        w=ingest(g); result=plan(w)
        self.assertTrue(result['geometric_clear'], result['violations'])
        self.assertFalse(result['authorized'])
        self.assertGreater(np.linalg.norm(np.asarray(result['points'])[0]-[20,0]),1.0)
        self.assertEqual(validate_path(w,result['points'],Vehicle())['cone_contacts'],[])
        self.assertLessEqual(max(result['speeds']),6)
        self.assertGreater(min(result['speeds']),0)

    def test_full_rectangle_fits_narrow_corridor_without_disc_overrestriction(self):
        from cleanlap.planning import plan
        g=fixture()
        for line,r in zip(g['limits']['polylines'],[21.4,18.6]):
            for point in line:
                angle=math.atan2(point[1],point[0]); point[:]=[r*math.cos(angle),r*math.sin(angle)]
        self.assertTrue(plan(ingest(g))['geometric_clear'])

    def test_collision_checked_smoothing_removes_lattice_corners(self):
        from cleanlap.planning import smooth_path, curvature, Vehicle
        w=ingest(fixture()); p=w.reference.copy()
        p[20]*=1.03
        q=smooth_path(w,p,Vehicle())
        self.assertLess(max(abs(curvature(q))),max(abs(curvature(p)))*.6)

    def test_slalom_requires_finite_forward_crossings_and_alternation(self):
        from cleanlap.validation import slalom_evidence
        gates=[{'id':str(i),'x':x,'z':0.,'tx':1.,'tz':0.} for i,x in enumerate([0.,5.,10.])]
        runs=[{'id':'synthetic','gates':gates,'confidence':1.}]
        good=[[-2,2],[2,2],[3,-2],[7,-2],[8,2],[12,2]]
        self.assertTrue(slalom_evidence(good,runs)['candidate_sequence_ok'])
        self.assertFalse(slalom_evidence([[-2,2],[12,2]],runs)['candidate_sequence_ok'])
        self.assertFalse(slalom_evidence([[-2,20],[12,20]],runs)['candidate_sequence_ok'])
        self.assertFalse(slalom_evidence(good[:3],runs)['candidate_sequence_ok'])

    def test_safety_margin_conflict_is_not_reported_as_physical_contact(self):
        from cleanlap.planning import validate_path
        g=fixture();g['cones']['clusters']=[{'x':21.,'z':0.,'footprint_m':.25}]
        w=ingest(g);r=validate_path(w,w.reference)
        self.assertEqual(r['cone_contacts'],[])
        self.assertEqual(r['cone_margin_conflicts'],[0])

    def test_turn_sweep_checks_rotating_corners_not_only_centres(self):
        from cleanlap.planning import validate_path
        g=fixture();g['cones']['clusters']=[{'x':1.53,'z':.65,'footprint_m':.01}]
        r=validate_path(ingest(g),[[-10,0],[0,0],[0,10],[-10,10]])
        self.assertEqual(r['cone_contacts'],[0])

    def test_impossible_corridor_does_not_return_a_drivable_plan(self):
        from cleanlap.planning import plan
        g=fixture(); g['cones']['clusters']=[{'x':20.,'z':0.,'footprint_m':18.}]
        r=plan(ingest(g))
        self.assertFalse(r['geometric_clear'])
        self.assertFalse(r['authorized'])

if __name__=='__main__': unittest.main()
