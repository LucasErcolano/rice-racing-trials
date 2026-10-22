import json
import tempfile
import unittest
from pathlib import Path
from test_geometry import fixture


class ArtifactTests(unittest.TestCase):
    def test_cli_writes_bound_non_actuating_artifacts(self):
        from cleanlap.__main__ import main
        with tempfile.TemporaryDirectory() as t:
            source=Path(t)/'geometry.json';source.write_text(json.dumps(fixture()),encoding='utf-8')
            out=Path(t)/'result'
            self.assertEqual(main(['--geometry',str(source),'--output',str(out),'--laps','1']),2)
            report=json.loads((out/'report.json').read_text(encoding='utf-8'))
            self.assertTrue(report['completed'])
            self.assertFalse(report['authorized'])
            self.assertTrue((out/'telemetry.csv').exists())
            self.assertGreater((out/'trajectory.png').stat().st_size,1000)
            self.assertTrue((out/'signals.png').exists())
            manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
            self.assertIn('scipy',manifest['versions'])
            self.assertIn('geometry_sha256',manifest)
            self.assertIn('code_sha256',manifest)
            self.assertIn('telemetry.csv',manifest['artifact_sha256'])

if __name__=='__main__':unittest.main()
