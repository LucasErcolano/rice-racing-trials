"""Track analysis tool tests on a minimal SYNTHETIC track (no proprietary assets needed)."""
import hashlib
import json
import math
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from track import dummy_patch, geometry, kn5, render, route_export, telemetry  # noqa: E402

TRACK = 'SYNTH_Track'
RADIUS = 30.0


def ai_bytes(count=72):
    points = [(RADIUS * math.cos(2 * math.pi * i / count), 0.0, RADIUS * math.sin(2 * math.pi * i / count))
              for i in range(count)]
    out = [struct.pack('<4i', 7, count, 0, 0)]
    for i, (x, y, z) in enumerate(points):
        out.append(struct.pack('<4fi', x, y, z, i * 2.6, i))
    return b''.join(out)


def make_track(folder):
    """Square asphalt pad, grass mesh, circular AI line (counter-clockwise in XZ), timing dummies, 2 cone clusters."""
    folder = Path(folder)
    (folder / 'data').mkdir(parents=True)
    (folder / 'ai').mkdir()
    (folder / 'data/surfaces.ini').write_text(
        '[SURFACE_0]\nKEY=ASPHALT\nIS_VALID_TRACK=1\n\n[SURFACE_1]\nKEY=GRASS\nIS_VALID_TRACK=0\n', encoding='utf-8')
    (folder / 'ai/fast_lane.ai').write_bytes(ai_bytes())
    dummies = kn5.synthetic_transform('Dummy', children=[
        kn5.synthetic_transform('AC_TIME_0_L', (25.0, 0.5, 0.0)),
        kn5.synthetic_transform('AC_TIME_0_R', (35.0, 0.5, 0.0)),
        kn5.synthetic_transform('AC_START_0', (30.0, 1.0, -15.0)),
        kn5.synthetic_transform('AC_HOTLAP_START_0', (30.0, 1.0, -40.0)),
        kn5.synthetic_transform('AC_PIT_0', (20.0, 1.0, -20.0)),
    ])
    (folder / 'Dummy.kn5').write_bytes(kn5.synthetic_kn5(dummies))
    pad = kn5.synthetic_mesh('1ASPHALT_pad', [(-50, 0, -50), (50, 0, -50), (50, 0, 50), (-50, 0, 50)],
                             triangles=[0, 1, 2, 0, 2, 3])
    grass = kn5.synthetic_mesh('1GRASS_out', [(60, 0, 0), (70, 0, 0), (60, 0, 10)])
    (folder / 'Physics.kn5').write_bytes(kn5.synthetic_kn5(kn5.synthetic_transform('Physics', children=[pad, grass])))
    cones = kn5.synthetic_mesh('PYLON_merged', [(0, 0, 0), (0.2, 0.5, 0), (0, 0, 0.2),
                                                (5, 0, 5), (5.2, 0.5, 5), (5, 0, 5.2)],
                               triangles=[0, 1, 2, 3, 4, 5], material_id=1)
    road = kn5.synthetic_mesh('ROAD_visual', [(-50, 0, -50), (50, 0, -50), (50, 0, 50)])
    (folder / f'{TRACK}.kn5').write_bytes(kn5.synthetic_kn5(kn5.synthetic_transform('Visual', children=[cones, road]),
                                                            materials=('road', 'cone_orange')))
    return folder


class KN5Tests(unittest.TestCase):
    def test_synthetic_roundtrip_v5_and_v6(self):
        for version in (5, 6):
            root = kn5.synthetic_transform('R', (1, 2, 3), [kn5.synthetic_mesh('m', [(0, 0, 0), (1, 0, 0), (0, 0, 1)])])
            data = kn5.synthetic_kn5(root, version=version)
            model = kn5.parse(data)
            self.assertEqual(model.version, version)
            self.assertEqual(kn5.write(model), data)
            names = [n.name for n, _, _ in model.walk()]
            self.assertEqual(names, ['R', 'm'])

    def test_truncated_and_bad_magic_rejected(self):
        data = kn5.synthetic_kn5(kn5.synthetic_transform('R'))
        with self.assertRaises(kn5.KN5Error):
            kn5.parse(data[:-3])
        with self.assertRaises(kn5.KN5Error):
            kn5.parse(b'xx' + data[2:])

    def test_world_transform_applied_to_children(self):
        root = kn5.synthetic_transform('P', (10, 0, -5), [kn5.synthetic_transform('AC_TIME_0_L', (1, 0, 1))])
        marks = geometry.landmarks(kn5.parse(kn5.synthetic_kn5(root)))
        self.assertEqual((marks['AC_TIME_0_L']['x'], marks['AC_TIME_0_L']['z']), (11.0, -4.0))

    def test_real_dummy_roundtrip_when_assets_present(self):
        archive = ROOT / 'assets/source/FSAEJ_Endurance.zip'
        if not archive.is_file():
            self.skipTest('proprietary assets not present')
        data = zipfile.ZipFile(archive).read('FSAEJ_Endurance/Dummy.kn5')
        self.assertEqual(kn5.write(kn5.parse(data)), data)


class GeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.folder = make_track(Path(cls.tmp.name) / TRACK)
        cls.doc = geometry.extract(cls.folder, TRACK, mesh_points='ROAD')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_landmarks_and_gate_direction_from_ai(self):
        gate = self.doc['timing_gate']
        self.assertEqual(set(self.doc['landmarks']), set(geometry.TIMING_NAMES))
        self.assertEqual(gate['spawn_to_line_midpoint_m'], 15.0)
        # AI runs counter-clockwise: at (30, 0) it moves towards +z; spawn at z=-15 is before the line.
        self.assertIn('fast_lane.ai', gate['direction_source'])
        self.assertTrue(gate['spawn_before_line'])
        before, after = {'t': 0, 'x': 30, 'z': -1}, {'t': 0.1, 'x': 30, 'z': 1}
        self.assertIsNotNone(telemetry.crossing(before, after, gate))
        self.assertIsNone(telemetry.crossing(after, before, gate))

    def test_limits_from_valid_surfaces_only(self):
        limits = self.doc['limits']
        self.assertIn('IS_VALID_TRACK', limits['status'])
        self.assertEqual(len(limits['polylines']), 1)
        xs = [p[0] for p in limits['polylines'][0]]
        self.assertEqual((min(xs), max(xs)), (-50, 50))  # grass (x 60..70) excluded
        self.assertTrue(self.doc['surfaces']['ASPHALT']['valid_track'])
        self.assertEqual(self.doc['surfaces']['GRASS']['meshes'], 1)

    def test_limits_unavailable_without_valid_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_track(Path(tmp) / TRACK)
            (folder / 'data/surfaces.ini').write_text('[SURFACE_0]\nKEY=ASPHALT\nIS_VALID_TRACK=0\n', encoding='utf-8')
            doc = geometry.extract(folder, TRACK)
        self.assertEqual(doc['limits']['status'], 'no disponible')

    def test_cones_are_unconfirmed_clusters(self):
        cones = self.doc['cones']
        self.assertEqual(cones['status'], 'candidato no confirmado')
        self.assertEqual(len(cones['clusters']), 2)
        self.assertTrue(all(c['status'] == 'candidato no confirmado' for c in cones['clusters']))

    def test_slalom_empty_with_warning_and_loaded_from_file(self):
        self.assertEqual(self.doc['slalom']['gates'], [])
        self.assertTrue(self.doc['slalom']['warning'])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'slalom.json'
            path.write_text(json.dumps({'version': 'slalom-gates-v1', 'track': TRACK, 'survey_id': 'S1',
                                        'gates': [{'a': {'x': 0, 'z': 0}, 'b': {'x': 1, 'z': 0}, 'label': 'G1'}]}))
            loaded = geometry.load_slalom(path, TRACK)
            self.assertEqual(len(loaded['gates']), 1)
            self.assertIsNone(loaded['warning'])
            path.write_text(json.dumps({'version': 'slalom-gates-v1', 'track': 'OTHER', 'gates': []}))
            with self.assertRaises(ValueError):
                geometry.load_slalom(path, TRACK)

    def test_zip_source_matches_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / f'{TRACK}.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                for path in self.folder.rglob('*'):
                    if path.is_file():
                        z.write(path, f'{TRACK}/{path.relative_to(self.folder).as_posix()}')
            doc = geometry.extract(archive, TRACK, mesh_points='ROAD')
        self.assertEqual(doc['landmarks'], self.doc['landmarks'])
        self.assertEqual(doc['source_sha256'], self.doc['source_sha256'])

    def test_render_is_self_contained(self):
        samples = [{'t': i * 0.1, 'x': 30 * math.cos(i / 10), 'z': 30 * math.sin(i / 10), 'y': 0} for i in range(80)]
        page = render.page(self.doc, telemetry.decimate(samples), telemetry.analyse(samples, self.doc['timing_gate']))
        self.assertIn('<svg', page)
        self.assertIn('candidato no confirmado', page)
        for forbidden in ('<script', 'http://', 'https://', '@import', 'src='):
            self.assertNotIn(forbidden, page.replace('http://www.w3.org/2000/svg', ''))


class TelemetryTests(unittest.TestCase):
    gate = {'a': {'x': 25, 'z': 0}, 'b': {'x': 35, 'z': 0}, 'direction': 1}

    def write(self, name, text):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / name
        path.write_text(text, encoding='utf-8')
        return path

    def test_diagnostics_jsonl_format(self):
        # SYNTHETIC lines in the client/diagnostics.lua format.
        lines = [{'t': 0, 'kind': 'session_start', 'synthetic': True}]
        lines += [{'t': i * 0.5, 'kind': 'sample', 'pos': {'x': 30, 'y': 0, 'z': -5 + i}} for i in range(12)]
        lines.append({'t': 9, 'kind': 'collision', 'collisionDepth': 0.1})
        path = self.write('rice_diagnostics.jsonl', '\n'.join(json.dumps(x) for x in lines) + '\nnot json\n')
        samples, stats = telemetry.load(path)
        self.assertEqual(stats['format'], 'jsonl')
        self.assertEqual(len(samples), 12)
        self.assertEqual(stats['skipped_lines'], 3)
        result = telemetry.analyse(samples, self.gate)
        self.assertEqual(result['gate_crossings_t'], [2.5])

    def test_csv_and_teleport_gap_ignored(self):
        rows = ['t,x,y,z', '0,30,0,-1', '0.1,30,0,1', '5,30,0,-1', '20,30,0,1']
        samples, stats = telemetry.load(self.write('trace.csv', '\n'.join(rows) + '\n'))
        self.assertEqual(stats['format'], 'csv')
        self.assertEqual(telemetry.crossings(samples, self.gate), [0.05])

    def test_rejects_unusable(self):
        with self.assertRaises(ValueError):
            telemetry.load(self.write('bad.csv', 'a,b\n1,2\n'))
        with self.assertRaises(ValueError):
            telemetry.load(self.write('one.jsonl', '{"t":0,"pos":{"x":1,"z":2}}\n'))


class RouteExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cls.doc = geometry.extract(make_track(Path(tmp) / TRACK), TRACK)
        cls.checkpoints = route_export.checkpoints_from_ai(cls.doc['ai_path'], 3, 8.0, skip_m=10)

    def test_default_is_unverified(self):
        lua, summary = route_export.build(self.doc, self.checkpoints)
        self.assertFalse(summary['verified'])
        self.assertIn('verified = false,', lua)
        self.assertIn('approved_by = nil,', lua)
        self.assertIn(self.doc['source_sha256']['Physics.kn5'], lua)

    def test_approval_requires_operator_name(self):
        with self.assertRaises(ValueError):
            route_export.build(self.doc, self.checkpoints, approve=True)
        with self.assertRaises(ValueError):
            route_export.build(self.doc, self.checkpoints, approve=True, operator='   ')
        lua, summary = route_export.build(self.doc, self.checkpoints, approve=True, operator='Ana "Tester"')
        self.assertTrue(summary['verified'])
        self.assertIn('verified = true,', lua)

    def test_rejects_invalid_gates(self):
        bad = [{'a': {'x': 0, 'z': 0}, 'b': {'x': 0, 'z': 0}, 'direction': 1}]
        with self.assertRaises(ValueError):
            route_export.build(self.doc, bad)
        with self.assertRaises(ValueError):
            route_export.build(self.doc, [{'a': {'x': 0, 'z': 0}, 'b': {'x': 1, 'z': 0}, 'direction': 0}])

    def test_ai_candidates_are_crossed_in_driving_order(self):
        samples = [{'t': i * 0.05, 'x': RADIUS * math.cos(i / 200), 'z': RADIUS * math.sin(i / 200)}
                   for i in range(int(2 * math.pi * 200) + 5)]
        report = telemetry.analyse(samples, self.doc['timing_gate'], self.checkpoints)
        times = [c['crossings_t'] for c in report['checkpoints']]
        self.assertTrue(all(len(t) == 1 for t in times), times)
        self.assertEqual([t[0] for t in times], sorted(t[0] for t in times))

    def test_exported_route_drives_real_client_state_machine(self):
        try:
            import lupa
        except ImportError:
            self.skipTest('lupa not installed')
        lua_text, _ = route_export.build(self.doc, self.checkpoints, approve=True, operator='Test')
        runtime = lupa.LuaRuntime()
        core = runtime.execute((ROOT / 'client/core.lua').read_text(encoding='utf-8'))
        config = runtime.execute(lua_text)
        machine = core['route_machine'](config['route'])
        self.assertIsNotNone(machine)
        steps = int(2.2 * 2 * math.pi * 100)  # outlap start on the spawn side, then 2 laps, counter-clockwise
        start_angle = math.atan2(-15.0, 30.0)
        events, previous = [], None
        for i in range(steps):
            angle = start_angle + i / 100
            current = runtime.table_from({'x': RADIUS * math.cos(angle), 'z': RADIUS * math.sin(angle)})
            if previous is not None:
                event = machine.observe(machine, previous, current)
                if isinstance(event, tuple):  # start-line events also return the in-frame fraction
                    event, fraction = event
                    self.assertTrue(0 <= fraction <= 1)
                if event:
                    events.append(event)
            previous = current
        self.assertEqual(events, ['OUTLAP'] + ['CHECKPOINT'] * 3 + ['OUTLAP_COMPLETE'] + ['CHECKPOINT'] * 3 + ['COMPLETE'])

    def test_lua_output_loads_in_lua_when_available(self):
        try:
            import lupa
        except ImportError:
            self.skipTest('lupa not installed')
        lua, _ = route_export.build(self.doc, self.checkpoints, approve=True, operator='Ana "x" \\ y')
        runtime = lupa.LuaRuntime()
        table = runtime.execute(lua)
        self.assertTrue(table['verified'])
        self.assertEqual(table['approved_by'], 'Ana "x" \\ y')
        self.assertEqual(len(table['route']['checkpoints']), 3)
        self.assertEqual(table['route']['start']['direction'], self.doc['timing_gate']['direction'])


class DummyPatchTests(unittest.TestCase):
    def test_candidate_changes_only_target_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_track(Path(tmp) / TRACK)
            original = (folder / 'Dummy.kn5').read_bytes()
            candidate, manifest = dummy_patch.build(folder, TRACK)
            self.assertEqual((folder / 'Dummy.kn5').read_bytes(), original, 'original must never be modified')
        self.assertEqual(manifest['label'], 'CANDIDATO NO VERIFICADO EN JUEGO')
        self.assertEqual(manifest['original_sha256'], hashlib.sha256(original).hexdigest())
        self.assertTrue(all(manifest['verification_linux'].values()))
        self.assertEqual(len(candidate), len(original))
        change = manifest['changes'][0]
        self.assertEqual(change['node'], 'AC_START_0')
        self.assertEqual(change['new_side_of_line'], 'después de la línea (ya cruzada)')
        self.assertEqual(change['surface_under_new_position']['key'], 'ASPHALT')
        marks = geometry.landmarks(kn5.parse(candidate))
        self.assertEqual(marks['AC_TIME_0_L'], geometry.landmarks(kn5.parse(original))['AC_TIME_0_L'])
        self.assertAlmostEqual(math.hypot(marks['AC_START_0']['x'], marks['AC_START_0']['z']), RADIUS, delta=0.2)
        self.assertGreater(marks['AC_START_0']['z'], 0)

    def test_refuses_off_track_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_track(Path(tmp) / TRACK)
            (folder / 'data/surfaces.ini').write_text('[SURFACE_0]\nKEY=ASPHALT\nIS_VALID_TRACK=0\n')
            with self.assertRaisesRegex(ValueError, 'IS_VALID_TRACK'):
                dummy_patch.build(folder, TRACK)


class CliTests(unittest.TestCase):
    def test_end_to_end_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            folder = make_track(tmp / TRACK)
            run = lambda *a: subprocess.run([sys.executable, '-m', 'track.tool', *map(str, a)], cwd=ROOT,
                                            capture_output=True, text=True, timeout=120)
            out = tmp / 'out'
            r = run('extract', '--source', folder, '--track', TRACK, '--output', out / 'g.json')
            self.assertEqual(r.returncode, 0, r.stderr)
            r = run('export-route', '--geometry', out / 'g.json', '--ai-candidates', 3, '--output', out / 'route_config.lua')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('verified = false', (out / 'route_config.lua').read_text())
            r = run('export-route', '--geometry', out / 'g.json', '--ai-candidates', 3, '--approve',
                    '--output', out / 'r2.lua')
            self.assertEqual(r.returncode, 2)
            self.assertIn('operator', r.stderr)
            trace = tmp / 't.jsonl'
            trace.write_text('\n'.join(json.dumps({'t': i * 0.1, 'kind': 'sample', 'pos': {'x': 30, 'y': 0, 'z': -3 + i}})
                                       for i in range(8)))
            r = run('render', '--geometry', out / 'g.json', '--output', out / 'view.html', '--svg', out / 'view.svg',
                    '--telemetry', trace, '--route', out / 'route_config.json')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue((out / 'view.html').read_text().startswith('<!DOCTYPE html>'))
            self.assertTrue((out / 'view.svg').read_text().startswith('<svg'))
            r = run('dummy-patch', '--source', folder, '--track', TRACK, '--output', out / 'patch')
            self.assertEqual(r.returncode, 0, r.stderr)
            sums = (out / 'patch/SHA256SUMS.txt').read_text().split()
            self.assertEqual(sums[0], hashlib.sha256((folder / 'Dummy.kn5').read_bytes()).hexdigest())
            self.assertFalse((out / 'patch/Dummy.kn5.original').exists(), 'the proprietary original is never copied')
            self.assertTrue((out / 'patch/Apply-DummyPatch.ps1').is_file())
            r = run('dummy-patch', '--source', folder, '--track', TRACK, '--output', folder / 'inside')
            self.assertEqual(r.returncode, 2)


if __name__ == '__main__':
    unittest.main()
