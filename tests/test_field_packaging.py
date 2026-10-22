"""Field-test packaging gates, independent of a Windows runtime."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FieldPackagingTests(unittest.TestCase):
    def test_windows_launcher_and_task_explicitly_opt_in_v2(self):
        launcher = (ROOT / 'packaging/start-server.ps1').read_text(encoding='utf-8')
        admin = (ROOT / 'packaging/server-admin.ps1').read_text(encoding='utf-8')
        self.assertIn('[switch]$EnablePenaltiesV2', launcher)
        self.assertIn("if ($EnablePenaltiesV2) { $serverArguments += '--enable-penalties-v2' }", launcher)
        self.assertIn("if ($EnablePenaltiesV2) { $serverArguments += '--field-v2-only' }", launcher)
        self.assertIn('[switch]$EnablePenaltiesV2', admin)
        self.assertIn("if ($EnablePenaltiesV2) { $arguments += ' -EnablePenaltiesV2' }", admin)

    def test_windows_build_carries_configs_docs_and_explicit_server_imports(self):
        text = (ROOT / 'packaging/build.ps1').read_text(encoding='utf-8')
        self.assertIn('track/calibration/route', text)
        self.assertIn('track-config', text)
        self.assertIn('v2-field-testing.md', text)
        for name in ('penalties_v2', 'penalty_calibration', 'field_v2'):
            self.assertIn('--hidden-import server.' + name, text)

    def test_both_builds_require_field_client_and_provisional_monitor(self):
        linux = (ROOT / 'packaging/build-linux.py').read_text(encoding='utf-8')
        windows = (ROOT / 'packaging/build.ps1').read_text(encoding='utf-8')
        for text in (linux, windows):
            for name in ('field_v2.lua', 'field-v2.html', 'field-v2.js'):
                self.assertIn(name, text)
        for name in ('velocity-integration.md', 'test-report.md'):
            self.assertIn(name, windows)

    def test_windows_ci_exercises_or_channels_in_frozen_executable(self):
        workflow = (ROOT / '.github/workflows/windows-package.yml').read_text(encoding='utf-8')
        self.assertIn('tools/field_smoke.py', workflow)
        self.assertIn('--server-exe', workflow)

    def test_install_explicitly_opt_in_v2_without_changing_v1_default(self):
        text = (ROOT / 'packaging/install.ps1').read_text(encoding='utf-8')
        self.assertIn('[switch]$ProtocolV2', text)
        self.assertIn("if ($ProtocolV2) { ', protocol_v2=true' }", text)


if __name__ == '__main__':
    unittest.main()
