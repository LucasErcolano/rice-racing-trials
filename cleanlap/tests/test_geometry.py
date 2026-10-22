import copy
import math
import unittest


def fixture():
    # Synthetic annular road, never represented as FSAEJ geometry.
    circle = lambda r: [[r*math.cos(i*math.tau/120), r*math.sin(i*math.tau/120)] for i in range(121)]
    return {'schema':'track-geometry-v1','track':'synthetic', 'source_sha256':{'fixture':'0'*64},
            'ai_path':circle(20)[:-1], 'limits':{'polylines':[circle(25),circle(15)]},
            'cones':{'clusters':[]},'slalom':{'status':'unresolved','gates':[]},
            'timing_gate':{'a':{'x':15,'z':0},'b':{'x':25,'z':0},'direction':1}}


class GeometryTests(unittest.TestCase):
    def test_ingestion_keeps_unknowns_fail_closed(self):
        from cleanlap.geometry import ingest
        world = ingest(fixture())
        self.assertAlmostEqual(world.road.area, math.pi*(625-225), delta=2)
        self.assertFalse(world.ready)
        self.assertIn('slalom_unresolved', world.unresolved)
        self.assertTrue(world.road.contains(__import__('shapely').Point(20,0)))

    def test_geometry_identity_also_binds_checkpoint_configuration(self):
        from cleanlap.geometry import ingest
        g=fixture();a=ingest(g);b=ingest(g,checkpoints=[g['timing_gate']])
        self.assertNotEqual(a.digest,b.digest)

    def test_nonfinite_and_open_boundaries_are_rejected(self):
        from cleanlap.geometry import ingest
        g=fixture(); g['ai_path'][0][0]=float('nan')
        with self.assertRaises(ValueError): ingest(g)
        g=fixture(); g['limits']['polylines'][0]=g['limits']['polylines'][0][:-8]
        with self.assertRaises(ValueError): ingest(g)

if __name__=='__main__': unittest.main()
