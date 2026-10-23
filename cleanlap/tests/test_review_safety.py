"""Review regressions: synthetic evidence only, no Windows access."""
import math
import unittest
from cleanlap.geometry import ingest
from cleanlap.planning import plan
from cleanlap.runtime import Runtime
from cleanlap.tests.test_geometry import fixture


class ReviewSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.world=ingest(fixture()); cls.plan=plan(cls.world)

    def test_nondefault_footprint_is_reported_and_changes_offtrack(self):
        from cleanlap.diagnostics import diagnose
        from cleanlap.planning import Vehicle
        from dataclasses import asdict
        row=dict(x=23.,z=0.,heading=math.pi/2)
        small=Vehicle(half_width=.5); wide=Vehicle(half_width=3.)
        narrow=diagnose(self.world,[row],small)
        broad=diagnose(self.world,[row],wide)
        self.assertLess(narrow['offtrack_samples'],broad['offtrack_samples'])
        self.assertEqual(broad['vehicle'],asdict(wide))
        self.assertTrue(diagnose(self.world,[row])['provisional_vehicle'])

    def traversal(self, change=None):
        from cleanlap.diagnostics import diagnose
        from cleanlap.planning import Vehicle
        rows=[]
        for i in range(126):
            angle=-.05+i*math.tau/120
            rows.append(dict(x=20*math.cos(angle),z=20*math.sin(angle),heading=angle+math.pi/2,
                             source_t=i*.02,seq=i,epoch='boot',car='test',track='synthetic',
                             protocol='cleanlap-1',live=True))
        if change: rows[60].update(change)
        return diagnose(self.world,rows,Vehicle())

    def test_default_vehicle_never_qualifies_complete_traversal(self):
        from cleanlap.diagnostics import diagnose
        rows=[]
        for i in range(126):
            angle=-.05+i*math.tau/120
            rows.append(dict(x=20*math.cos(angle),z=20*math.sin(angle),heading=angle+math.pi/2,
                             source_t=i*.02,seq=i,epoch='boot',car='test',track='synthetic',
                             protocol='cleanlap-1',live=True))
        self.assertFalse(diagnose(self.world,rows)['checkpoints_complete'])

    def test_live_continuous_traversal_completes(self):
        self.assertTrue(self.traversal()['checkpoints_complete'])

    def test_reset_cannot_stitch_traversal(self):
        self.assertFalse(self.traversal({'epoch':'reset'})['checkpoints_complete'])

    def test_car_switch_cannot_stitch_traversal(self):
        self.assertFalse(self.traversal({'car':'other'})['checkpoints_complete'])

    def test_track_switch_cannot_stitch_traversal(self):
        self.assertFalse(self.traversal({'track':'other'})['checkpoints_complete'])

    def test_sequence_regression_cannot_stitch_traversal(self):
        self.assertFalse(self.traversal({'seq':1})['checkpoints_complete'])

    def test_paused_cannot_stitch_traversal(self):
        self.assertFalse(self.traversal({'live':False})['checkpoints_complete'])

    def test_replay_cannot_stitch_traversal(self):
        self.assertFalse(self.traversal({'replay':True})['checkpoints_complete'])

    def test_bad_protocol_cannot_stitch_traversal(self):
        self.assertFalse(self.traversal({'protocol':'replay'})['checkpoints_complete'])

    def first_frame_change(self, change):
        class Pad:
            def __init__(self): self.sent=[]
            def send(self, command): self.sent.append(command)
            def release(self): pass
        a,b=self.plan['points'][:2]
        frame=dict(protocol='cleanlap-1',live=True,car='test',track='synthetic',epoch='admitted',
                   seq=2,source_t=1.02,gear=1,lap_count=0,lap_ms=0,speed=0.,
                   x=a[0],z=a[1],heading=math.atan2(b[1]-a[1],b[0]-a[0]))
        pad=Pad()
        runtime=Runtime(self.world,self.plan,pad,authorized=True,
                        commissioned_car='test',admitted_epoch='admitted')
        result=runtime.step(dict(frame,**change),now=1.02)
        self.assertEqual(result['mode'],'STOP')
        self.assertEqual(runtime.step(frame,now=1.04)['mode'],'STOP')
        self.assertEqual(pad.sent,[])

    def test_first_post_init_car_change_latches_before_send(self):
        self.first_frame_change({'car':'other'})

    def test_first_post_init_epoch_change_latches_before_send(self):
        self.first_frame_change({'epoch':'reset'})
