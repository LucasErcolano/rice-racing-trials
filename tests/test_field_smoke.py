"""Exercise the same process/HTTP smoke tool used for the frozen Windows server."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FieldSmokeTests(unittest.TestCase):
    def test_source_server_through_real_subprocess_http_and_restart(self):
        tool = ROOT / 'tools/field_smoke.py'
        self.assertTrue(tool.is_file(), 'field process smoke tool missing')
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'field.json'
            proc = subprocess.run([sys.executable, str(tool), '--output', str(output)],
                                  cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=45)
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertTrue(report['passed'])
            self.assertFalse(report['official'])
            for check in ('native_only', 'penalty_only', 'both_orders', 'duplicate', 'conflict',
                          'restart', 'export', 'official_ranking_empty', 'field_ui', 'heavy_ingestion_disabled'):
                self.assertIn(check, report['checks'])
