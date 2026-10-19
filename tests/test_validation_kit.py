"""Validation Kit report tests. ALL inputs here are SYNTHETIC (marked "synthetic": true where it matters)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

import validation  # noqa: E402

APIS = {name: True for name in ('ac.getCar', 'ac.getSim', 'ac.getPatchVersion', 'io.save', 'web.post', 'JSON.parse')}
FIELDS = {f'car.{k}': True for k in ('position', 'lapCount', 'collidedWith', 'collisionDepth', 'wheelsOutside')}


def session(synthetic=True, car='madformulateam_mft02', track='FSAEJ_Endurance', apis=APIS, fields=FIELDS):
    """Synthetic diagnostics in the client/diagnostics.lua line format."""
    lines = [{'t': 0, 'kind': 'session_start', 'format': 1, 'csp_version': '0.2.x-SINTETICO', 'csp_code': 1,
              'car_id': car, 'track_id': track, 'api': apis},
             {'t': 0, 'kind': 'car_fields', 'available': fields}]
    if synthetic:
        lines[0]['synthetic'] = True
    t = 0.0
    for i in range(40):
        t += 0.5
        lines.append({'t': t, 'kind': 'sample', 'pos': {'x': i * 2.0, 'y': 0, 'z': 0}, 'isLapValid': i < 30})
    lines.append({'t': t + 0.5, 'kind': 'sample', 'pos': {'x': 500.0, 'y': 0, 'z': 0}})  # teleport
    for n, ms in enumerate((18100, 95000, 93000), start=1):
        lines.append({'t': t + n, 'kind': 'lap', 'lapCount': n, 'previousLapTimeMs': ms, 'isLastLapValid': True})
    lines.append({'t': t + 5, 'kind': 'collision', 'collidedWith': 0, 'collisionDepth': 0.05, 'wheelsOutside': 0})
    for online in (True, False, True):
        lines.append({'t': t + 6, 'kind': 'connection', 'state': {'online': online, 'pending': 0}})
    return lines


def export(synthetic=True, laps=2):
    data = {'schema_version': 2, 'rules_version': 'test', 'scoring_armed': False, 'calibrations': [],
            'laps': [{'id': i, 'status': 'provisional'} for i in range(laps)], 'sync_errors': []}
    if synthetic:
        data['synthetic'] = True
    return data


class ValidationReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def jsonl(self, lines, extra=''):
        path = self.dir / 'rice_diagnostics.jsonl'
        path.write_text('\n'.join(json.dumps(x) for x in lines) + '\n' + extra, encoding='utf-8')
        return path

    def json(self, name, data):
        path = self.dir / name
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def test_synthetic_inputs_are_never_verified(self):
        result = validation.build([self.jsonl(session())], self.json('export.json', export()))
        self.assertTrue(result['synthetic'])
        self.assertEqual(result['status'], 'SINTÉTICO (no válido)')
        self.assertIn('DATOS SINTÉTICOS', result['warnings'][0])
        self.assertIn('DATOS SINTÉTICOS', validation.markdown(result))

    def test_sections_are_separated(self):
        result = validation.build([self.jsonl(session(synthetic=False))], self.json('e.json', export(synthetic=False)))
        text = '\n'.join(result['verified'])
        for fragment in ('madformulateam_mft02', 'Teleport', 'outlap + 2', 'reconexión', 'Salida de pista',
                         'Contactos', 'servidor recibió 2'):
            self.assertIn(fragment, text)
        self.assertEqual(result['blocking'], [])
        self.assertTrue(any('18.100 s' in c for c in result['calibration']))
        self.assertTrue(any('checklist' in p for p in result['pending']))
        self.assertEqual(result['status'], 'INCOMPLETO')

    def test_wrong_car_track_and_missing_api_block(self):
        apis = dict(APIS, **{'web.post': False})
        lines = session(synthetic=False, car='other_car', track='other_track', apis=apis,
                        fields=dict(FIELDS, **{'car.position': False, 'car.collisionDepth': False}))
        result = validation.build([self.jsonl(lines)])
        blocking = '\n'.join(result['blocking'])
        self.assertIn('other_car', blocking)
        self.assertIn('other_track', blocking)
        self.assertIn('web.post', blocking)
        self.assertIn('car.position', blocking)
        self.assertTrue(any('car.collisionDepth' in w for w in result['warnings']))
        self.assertEqual(result['status'], 'BLOQUEADO')

    def test_corrupt_lines_and_missing_session_start(self):
        result = validation.build([self.jsonl([{'t': 1, 'kind': 'sample', 'pos': {'x': 0, 'z': 0}}], '{broken\n[1]\n')])
        self.assertTrue(any('session_start' in b for b in result['blocking']))
        self.assertTrue(any('2 líneas' in w for w in result['warnings']))

    def test_checklist_statuses(self):
        checklist = validation.checklist_template()
        checklist['synthetic'] = True
        checklist['items'][0]['status'] = 'ok'
        checklist['items'][1]['status'] = 'falla'
        checklist['items'][2]['status'] = 'bogus'
        result = validation.build(checklist=self.json('c.json', checklist))
        self.assertTrue(any('[preflight]' in v for v in result['verified']))
        self.assertTrue(any('[open_ac]' in b for b in result['blocking']))
        self.assertTrue(any('bogus' in w for w in result['warnings']))
        self.assertTrue(any('[hud_loads] sin registrar' in p for p in result['pending']))
        ids = [i['id'] for i in validation.checklist_template()['items']]
        for required in ('preflight', 'open_ac', 'hud_loads', 'diagnostics_on', 'outlap_2_laps', 'cone_touch',
                         'off_track', 'teleport_restart', 'network_down', 'network_up', 'dummy_candidate',
                         'mark_spoiled'):
            self.assertIn(required, ids)

    def test_cli_writes_markdown_and_html(self):
        diag = self.jsonl(session())
        out = self.dir / 'out/report.md'
        run = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/validation.py'), 'report', '--diagnostics',
                              str(diag), '--server-export', str(self.json('e.json', export())), '--output', str(out)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('## Funciones verificadas', out.read_text(encoding='utf-8'))
        page = out.with_suffix('.html').read_text(encoding='utf-8')
        self.assertIn('DATOS SINTÉTICOS', page)
        self.assertNotIn('<script', page)
        checklist = self.dir / 'checklist.json'
        run = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/validation.py'), 'checklist', '--output',
                              str(checklist)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        run = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/validation.py'), 'checklist', '--output',
                              str(checklist)], capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 2, 'checklist must not overwrite operator notes')

    def test_inventory_backward_compatible_flag(self):
        root = self.dir / 'ac'
        (root / 'content/tracks').mkdir(parents=True)
        run = subprocess.run([sys.executable, '-I', str(ROOT / 'tools/validation.py'), '--assetto-root', str(root)],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(json.loads(run.stdout)['ready'])


if __name__ == '__main__':
    unittest.main()
