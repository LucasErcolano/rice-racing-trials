import json, os, shutil, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import rr99_audit  # noqa: E402

IDS = rr99_audit.IDS


def w(root, rel, data):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data if isinstance(data, bytes) else data.encode())


def ui(name):
    return json.dumps({'name': name, 'brand': 'X', 'version': '1.42', 'parent': 'madformulateam_mft02', 'tags': []})


class Fixture(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t, True)
        self.o, self.f, self.p = (os.path.join(self.t, n) for n in ('orig', 'full', 'patch'))
        for cid in IDS:
            base = f'content/cars/{cid}'
            for root in (self.o, self.f):
                w(root, f'{base}/data.acd', 'acd-' + cid)
                w(root, f'{base}/madformulateam_mft02.kn5', 'kn5')
                w(root, f'{base}/collider.kn5', 'col')
            w(self.o, f'{base}/ui/ui_car.json', ui('MFT02'))
            w(self.o, f'{base}/ui/badge.png', 'old-badge')
            w(self.o, f'{base}/skins/2022_base/skin.dds', 'old-skin')
            for rel, data in ((f'{base}/ui/ui_car.json', ui('RR99')), (f'{base}/ui/badge.png', 'rr-badge'),
                              (f'{base}/skins/rr99/skin.dds', 'rr-skin'), (f'{base}/skins/rr99/ui_skin.json', '{}')):
                w(self.f, rel, data)
                w(self.p, rel, data)
        w(self.p, 'Install-RR99.ps1', '# installer')
        w(self.p, 'LEEME_RR99.txt', 'readme')

    def run_audit(self, *extra):
        return rr99_audit.main(['--orig', self.o, '--full', self.f, '--patch', self.p, *extra])

    def zip_of(self, name, files):
        import zipfile
        path = os.path.join(self.t, name)
        with zipfile.ZipFile(path, 'w') as z:
            for n in files:
                z.writestr(n, 'x')
        return path


class AuditTests(Fixture):
    def test_clean_patch_passes(self):
        self.assertEqual(self.run_audit(), 0)

    def test_physics_change_fails(self):
        w(self.f, f'content/cars/{IDS[0]}/data.acd', 'tampered')
        self.assertEqual(self.run_audit(), 1)

    def test_patch_differing_from_full_fails(self):
        w(self.p, f'content/cars/{IDS[1]}/skins/rr99/skin.dds', 'different')
        self.assertEqual(self.run_audit(), 1)

    def test_renamed_car_id_fails(self):
        shutil.copytree(os.path.join(self.p, 'content/cars', IDS[0]), os.path.join(self.p, 'content/cars/rr99_car'))
        self.assertEqual(self.run_audit(), 1)

    def test_kn5_in_patch_fails(self):
        w(self.p, f'content/cars/{IDS[2]}/madformulateam_mft02.kn5', 'kn5')
        w(self.f, f'content/cars/{IDS[2]}/madformulateam_mft02.kn5', 'kn5')
        self.assertEqual(self.run_audit(), 1)

    def test_ui_schema_drift_fails(self):
        for root in (self.f, self.p):
            w(root, f'content/cars/{IDS[0]}/ui/ui_car.json', json.dumps({'name': 'RR99', 'version': '2.0'}))
        self.assertEqual(self.run_audit(), 1)

    def test_overlap_with_client_zip_fails(self):
        z = self.zip_of('client.zip', ['RiceTrials/RiceTrials.lua', 'LEEME_RR99.txt'])
        self.assertEqual(self.run_audit('--client-zip', z), 1)

    def test_disjoint_client_server_zip_passes(self):
        c = self.zip_of('client.zip', ['RiceTrials/RiceTrials.lua', 'install.ps1'])
        s = self.zip_of('server.zip', ['RiceTrialsServer/app.exe'])
        self.assertEqual(self.run_audit('--client-zip', c, '--server-zip', s), 0)


if __name__ == '__main__':
    unittest.main()
