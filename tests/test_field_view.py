"""Read-only provisional monitor wiring and injection-safe rendering."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FieldViewTests(unittest.TestCase):
    def test_monitor_labels_all_results_provisional_and_keeps_tokens_ephemeral(self):
        self.assertTrue((ROOT / 'leaderboard/field-v2.html').is_file(), 'provisional monitor missing')
        html = (ROOT / 'leaderboard/field-v2.html').read_text(encoding='utf-8')
        js = (ROOT / 'leaderboard/field-v2.js').read_text(encoding='utf-8')
        self.assertIn('PROVISIONAL', html)
        self.assertIn('field-v2.css', html)
        css = (ROOT / 'leaderboard/field-v2.css').read_text(encoding='utf-8')
        self.assertIn('.field-page', css)
        self.assertIn('font-size: 22px', css)
        self.assertIn('PENALTIES_PENDING', html)
        self.assertIn('type="password"', html)
        self.assertNotIn('localStorage', js)
        self.assertNotIn('sessionStorage', js)
        self.assertNotIn('innerHTML', js)
        self.assertIn('textContent', js)
        self.assertIn('/api/admin/v2/field-laps', js)
        self.assertIn('response.ok', js)
        self.assertIn('setInterval', js)

    @unittest.skipUnless(shutil.which('node'), 'Node not installed')
    def test_javascript_syntax(self):
        result = subprocess.run(['node', '--check', str(ROOT / 'leaderboard/field-v2.js')], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
