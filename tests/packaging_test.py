"""Packaging/commissioning contract; no game asset fixtures are redistributed."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]


def module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


class ValidationTests(unittest.TestCase):
    def test_inventory_fails_closed_and_records_installed_hashes(self):
        validation = module(ROOT / 'tools/validation.py', 'validation')
        with tempfile.TemporaryDirectory() as tmp:
            ac = Path(tmp)
            (ac / 'apps/lua').mkdir(parents=True)
            (ac / 'dwrite.dll').write_bytes(b'placeholder')
            for track in ('FSAEJ_Endurance', 'FSAEJ_Skidpad'):
                target = ac / 'content/tracks' / track
                target.mkdir(parents=True)
                (target / (track + '.kn5')).write_bytes(b'fake track')
                (target / 'Physics.kn5').write_bytes(b'wrong physics')
                (target / 'Dummy.kn5').write_bytes(b'wrong dummy')
            car = ac / 'content/cars/madformulateam_mft02'
            car.mkdir(parents=True)
            (car / 'data.acd').write_bytes(b'fake car')
            result = validation.inspect(ac, ROOT / 'track/calibration/timing-landmarks.json')
            self.assertFalse(result['ready'])
            self.assertEqual(result['car']['id'], 'madformulateam_mft02')
            self.assertEqual(len(result['car']['data_acd_sha256']), 64)
            self.assertFalse(result['tracks']['FSAEJ_Skidpad']['landmark_hashes_match'])
            self.assertEqual(result['csp']['status'], 'candidate-only')
            self.assertEqual(result['status'], 'commissioning-required')

    def test_inventory_missing_root_does_not_create_directory(self):
        validation = module(ROOT / 'tools/validation.py', 'validation_missing')
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / 'missing'
            with self.assertRaises(FileNotFoundError):
                validation.inspect(missing, ROOT / 'track/calibration/timing-landmarks.json')
            self.assertFalse(missing.exists())


class WindowsScriptContractTests(unittest.TestCase):
    def test_installer_supports_explicit_backup_without_destroying_existing_app(self):
        text = (ROOT / 'packaging/install.ps1').read_text()
        self.assertIn('$BackupDirectory', text)
        self.assertIn('Move-Item -LiteralPath $target', text)
        self.assertIn('Restore', text)

    def test_installer_provisions_local_station_config_without_cli_secret(self):
        text = (ROOT / 'packaging/install.ps1').read_text()
        self.assertIn('station_config.lua', text)
        self.assertIn('Read-Host', text)
        self.assertIn('-AsSecureString', text)
        self.assertIn('Set-Acl', text)
        self.assertIn('function ConvertTo-LuaByteString', text)
        self.assertIn("'return {server_url='", text)
        self.assertIn("', station_token='", text)
        self.assertIn("', station_id='", text)
        self.assertNotIn('[string]$StationSecret', text)

    def test_server_launcher_uses_protected_credential_file_and_clears_environment(self):
        text = (ROOT / 'packaging/start-server.ps1').read_text()
        self.assertIn('Import-Clixml', text)
        self.assertIn('RICE_STATION_TOKENS', text)
        self.assertIn('finally', text)
        self.assertIn('Remove-Item Env:RICE_ADMIN_TOKEN', text)

    def test_windows_builder_stages_validation_and_scripts(self):
        text = (ROOT / 'packaging/build.ps1').read_text()
        self.assertIn('validation.py', text)
        self.assertIn('start-server.ps1', text)
        self.assertIn('*.lua', text)


class BuildTests(unittest.TestCase):
    def test_dynamic_source_package_includes_safe_modules_but_no_secrets_or_assets(self):
        build = module(ROOT / 'packaging/build-linux.py', 'build_linux')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'
            for directory in ('client', 'server', 'leaderboard', 'tests', 'packaging', 'tools', 'docs', 'assets/source'):
                (root / directory).mkdir(parents=True)
            for filename, contents in {
                'client/RiceTrials.lua': 'app', 'client/core.lua': 'core', 'client/extra.lua': 'extra',
                'client/manifest.ini': 'ini', 'client/station_config.lua': 'LOCAL_SECRET_DO_NOT_SHIP', 'server/app.py': 'app', 'server/service.py': 'module',
                'leaderboard/tv.html': 'tv', 'leaderboard/tv.js': 'js', 'leaderboard/admin.html': 'admin', 'leaderboard/admin.js': 'js',
                'packaging/install.ps1': 'install', 'packaging/uninstall.ps1': 'uninstall',
                'packaging/start-server.ps1': 'start', 'packaging/configure-secrets.ps1': 'configure',
                'tools/validation.py': 'kit', 'tests/packaging_test.py': 'test',
                'docs/windows-validation.md': 'validation', 'docs/operator-guide.md': 'guide',
                'README.md': 'readme', 'track-analysis.md': 'analysis',
                'assets/source/proprietary.rar': 'never', 'server/token.json': 'secret',
                'server/trials.sqlite': 'database', 'client/__pycache__/oops.py': 'cache',
            }.items():
                path = root / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
            (root / 'track/calibration').mkdir(parents=True)
            (root / 'track/calibration/timing-landmarks.json').write_text('{}')
            build.ROOT = root
            output = Path(tmp) / 'out'
            build.build(output, 'test')
            with ZipFile(output / 'RiceRacing_Trials_Client.zip') as archive:
                self.assertIn('RiceTrials/extra.lua', archive.namelist())
                self.assertNotIn('RiceTrials/station_config.lua', archive.namelist())
                self.assertIn('tools/validation.py', archive.namelist())
            with ZipFile(output / 'RiceRacing_Trials_Server.zip') as archive:
                self.assertIn('RiceTrialsServer/server/service.py', archive.namelist())
                self.assertIn('start-server.ps1', archive.namelist())
            with ZipFile(output / 'RiceRacing_Trials_Complete.zip') as archive:
                names = archive.namelist()
                self.assertEqual(len(names), len(set(names)))
                self.assertIn('source/tools/validation.py', names)
                self.assertIn('source/tests/packaging_test.py', names)
                self.assertNotIn('source/client/station_config.lua', names)
                self.assertNotIn('source/server/token.json', names)
                self.assertFalse(any('assets/' in n or '__pycache__' in n or n.endswith('.sqlite') for n in names))
