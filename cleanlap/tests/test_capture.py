import unittest,math


class CaptureTests(unittest.TestCase):
    def test_missing_wheels_never_certifies_dimensions(self):
        from cleanlap.capture import analyze
        self.assertEqual(analyze([{'live':True}])['sampled_envelope'],None)
        self.assertFalse(analyze([])['full_wheel_envelope_verified'])

    def test_observed_wheel_envelope_is_not_full_lock_certificate(self):
        from cleanlap.capture import analyze
        def v(x,y,z):return dict(x=x,y=y,z=z)
        wheels=[dict(index=i,position=v(x,0,z),look=v(0,0,1),up=v(0,1,0),radius=.2,tyre_width=.12)
                for i,(x,z) in enumerate([(-.6,.9),(.6,.9),(-.6,-.7),(.6,-.7)])]
        frame=dict(live=True,speed=0.,wheel_capture_status='OBSERVED_NOT_COMMISSIONED',
                   wheel_capture=dict(body=dict(position=v(0,0,0),look=v(0,0,1),side=v(1,0,0),up=v(0,1,0)),wheels=wheels))
        report=analyze([frame])
        self.assertAlmostEqual(report['wheelbase_median_m'],1.6)
        self.assertAlmostEqual(report['sampled_envelope']['half_width'],.66)
        self.assertAlmostEqual(report['sampled_envelope']['front_overhang'],1.1)
        self.assertAlmostEqual(report['sampled_envelope']['rear_overhang'],.9)
        self.assertFalse(report['full_wheel_envelope_verified'])
        del wheels[0]['tyre_width']
        self.assertIsNone(analyze([frame])['sampled_envelope'])
