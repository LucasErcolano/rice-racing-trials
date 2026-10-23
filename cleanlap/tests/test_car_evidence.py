import unittest


class CarEvidenceTests(unittest.TestCase):
    def test_physics_values_have_provenance_and_envelope_unknown_is_null(self):
        from cleanlap.car_evidence import summarize
        data={'suspensions.ini':b'[BASIC]\nWHEELBASE=1.55\nCG_LOCATION=0.5\n[FRONT]\nTRACK=1.2\n[REAR]\nTRACK=1.1\n',
              'car.ini':b'[CONTROLS]\nSTEER_LOCK=200\nSTEER_RATIO=5\n',
              'tyres.ini':b'[FRONT]\nWIDTH=0.2\nRADIUS=0.23\n[REAR]\nWIDTH=0.2\nRADIUS=0.23\n'}
        report=summarize(data,'test','0'*64)
        self.assertEqual(report['wheelbase_m']['value'],1.55)
        self.assertIn('suspensions.ini',report['wheelbase_m']['source'])
        self.assertIsNone(report['verified_full_wheel_envelope'])
        self.assertIsNone(report['verified_road_wheel_limit_rad'])
        self.assertEqual(report['steer_ratio']['value'],5.)


if __name__=='__main__':unittest.main()
