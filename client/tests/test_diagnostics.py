"""Diagnostics recorder tests with a dict-backed io double (not a CSP runtime test)."""
import json
import unittest
from pathlib import Path
from lupa import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]
DIR = 'C:/AC/apps/lua/RiceTrials/diagnostics'
CUR = DIR + '/rice_diagnostics.jsonl'
OLD = DIR + '/rice_diagnostics.1.jsonl'


class FakeIO:
    """Filesystem as a dict. io.move refuses an existing destination, like
    Windows MoveFileEx without REPLACE_EXISTING (CSP behavior undocumented)."""
    def __init__(self, files=None, fail=()):
        self.files = {} if files is None else files
        self.fail = set(fail)
        self.calls = []

    def _check(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise RuntimeError(name + ' failed')

    def save(self, p, data):
        self._check('save'); self.files[p] = data; return True
    def load(self, p, fallback=None):
        self._check('load'); return self.files.get(p, fallback)
    def fileExists(self, p):
        self._check('fileExists'); return p in self.files
    def createDir(self, p):
        self._check('createDir'); return True
    def deleteFile(self, p):
        self._check('deleteFile'); return self.files.pop(p, None) is not None
    def move(self, a, b):
        self._check('move')
        if b in self.files or a not in self.files: return False
        self.files[b] = self.files.pop(a); return True


class Diagnostics(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.diag = self.lua.execute((ROOT / 'diagnostics.lua').read_text())
        # Python callables are userdata in Lua; real CSP APIs are Lua functions,
        # and the module checks type(f) == 'function'. Wrap doubles in closures.
        self.wrap = self.lua.eval('function(f) return function(...) return f(...) end end')
        self.env = self.lua.table_from({
            'ac': self.funcs({
                'getPatchVersion': lambda: '0.2.11', 'getPatchVersionCode': lambda: 3425,
                'getCarID': lambda i: 'madformulateam_mft02', 'getTrackFullID': lambda sep: 'FSAEJ_Endurance',
                'getCar': lambda i: None}),
            'io': self.funcs({'save': lambda *a: True}),
        })
        self.car = {'position': {'x': 1.5, 'y': 0.25, 'z': -3}, 'look': {'x': 0, 'y': 0, 'z': 1},
                    'lapTimeMs': 1000, 'lapCount': 0, 'isLapValid': True, 'splinePosition': 0.1,
                    'collidedWith': -1, 'collisionDepth': 0, 'wheelsOutside': 0, 'speedKmh': 50}

    def tab(self, v):
        return self.lua.table_from(v, recursive=True)

    def funcs(self, mapping):
        return self.lua.table_from({k: self.wrap(f) for k, f in mapping.items()})

    def io(self, fs):
        names = ('save', 'load', 'fileExists', 'createDir', 'deleteFile', 'move')
        return self.funcs({n: (lambda n=n: lambda *a: getattr(fs, n)(*a))() for n in names})

    def make(self, fs=None, **config):
        cfg = {'diagnostics': True, 'diagnostics_dir': DIR}
        cfg.update(config)
        deps = {'env': self.env}
        if fs is not None:
            deps['io'] = self.io(fs)
        return self.diag.new(self.tab(cfg), self.lua.table_from(deps))

    def lines(self, text):
        return [json.loads(l) for l in text.split('\n') if l]

    def run_frames(self, d, frames, dt=1/60, car=None, extra=None):
        car = self.tab(car or self.car)
        for _ in range(frames):
            d.update(d, dt, car, None, extra)

    def test_disabled_by_default_does_nothing(self):
        fs = FakeIO()
        d = self.diag.new(self.tab({}), self.lua.table_from({'io': self.io(fs)}))
        d.update(d, 1, self.tab(self.car), None, None)
        self.assertFalse(d.flush(d))
        self.assertFalse(d.stats(d).enabled)
        self.assertEqual(fs.calls, [])

    def test_session_header_and_every_line_is_valid_json(self):
        fs = FakeIO()
        d = self.make(fs)
        self.run_frames(d, 60)
        d.event(d, 'connection', self.tab({'message': 'quote " backslash \\ newline \n tab \t ctrl \x01'}))
        self.assertTrue(d.flush(d))
        rows = self.lines(fs.files[CUR])
        self.assertEqual(rows[0]['kind'], 'session_start')
        self.assertEqual(rows[0]['csp_version'], '0.2.11')
        self.assertEqual(rows[0]['csp_code'], 3425)
        self.assertEqual(rows[0]['car_id'], 'madformulateam_mft02')
        self.assertEqual(rows[0]['track_id'], 'FSAEJ_Endurance')
        self.assertIs(rows[0]['api']['ac.getPatchVersion'], True)
        self.assertIs(rows[0]['api']['web.post'], False)
        self.assertIs(rows[0]['api']['io.save'], True)
        self.assertTrue(all('t' in r and 'kind' in r for r in rows))
        self.assertTrue(fs.files[CUR].endswith('\n'))
        self.assertEqual(rows[-1]['message'], 'quote " backslash \\ newline \n tab \t ctrl \x01')
        sample = next(r for r in rows if r['kind'] == 'sample')
        self.assertEqual(sample['pos'], {'x': 1.5, 'y': 0.25, 'z': -3})
        self.assertEqual(sample['look'], {'x': 0, 'y': 0, 'z': 1})
        for key in ('lapTimeMs', 'lapCount', 'isLapValid', 'splinePosition'):
            self.assertIn(key, sample)
        self.assertTrue(fs.files[CUR].startswith('{"t":'))

    def test_samples_at_2hz(self):
        fs = FakeIO()
        d = self.make(fs)
        self.run_frames(d, 600)  # 10 s at 60 fps
        d.flush(d)
        samples = [r for r in self.lines(fs.files[CUR]) if r['kind'] == 'sample']
        self.assertIn(len(samples), (20, 21))
        times = [s['t'] for s in samples]
        gaps = [round(b - a, 3) for a, b in zip(times, times[1:])]
        self.assertTrue(all(0.49 <= g <= 0.52 for g in gaps), gaps)

    def test_collision_signals_only_on_change(self):
        fs = FakeIO()
        d = self.make(fs)
        car = dict(self.car)
        self.run_frames(d, 30, car=car)
        car.update(collidedWith=0, collisionDepth=0.02)
        self.run_frames(d, 30, car=car)
        car.update(wheelsOutside=2)
        self.run_frames(d, 30, car=car)
        d.flush(d)
        coll = [r for r in self.lines(fs.files[CUR]) if r['kind'] == 'collision']
        self.assertEqual([(c['collidedWith'], c['wheelsOutside']) for c in coll], [(-1, 0), (0, 0), (0, 2)])

    def test_periodic_flush_not_every_frame(self):
        fs = FakeIO()
        d = self.make(fs, diagnostics_flush_s=5)
        self.run_frames(d, 290)  # 4.83 s
        self.assertEqual(fs.calls.count('save'), 0)
        self.run_frames(d, 20)   # crosses 5 s
        self.assertEqual(fs.calls.count('save'), 1)
        self.run_frames(d, 290)
        self.assertEqual(fs.calls.count('save'), 1)
        self.run_frames(d, 20)
        self.assertEqual(fs.calls.count('save'), 2)
        # Second save rewrites the whole file: first-flush lines are preserved.
        rows = self.lines(fs.files[CUR])
        self.assertEqual(rows[0]['kind'], 'session_start')
        self.assertEqual(d.stats(d).lines_written, len(rows))

    def test_buffer_cap_drops_oldest_and_counts(self):
        d = self.make(FakeIO(fail={'save'}), diagnostics_buffer_max=10)
        for i in range(25):
            d.event(d, 'error', self.tab({'n': i}))
        st = d.stats(d)
        self.assertEqual(st.buffered, 10)
        self.assertEqual(st.dropped, 15)
        fs = FakeIO()
        d = self.make(fs, diagnostics_buffer_max=10)
        for i in range(25):
            d.event(d, 'error', self.tab({'n': i}))
        d.flush(d)
        self.assertEqual([r['n'] for r in self.lines(fs.files[CUR])], list(range(15, 25)))

    def test_rotation_by_size_keeps_two_files_bounded(self):
        fs = FakeIO()
        d = self.make(fs, diagnostics_max_bytes=4096, diagnostics_flush_s=1)
        for _ in range(6000):  # 100 s of frames, ~1 flush/s
            self.run_frames(d, 1, extra={'errors': ['x' * 40]})
        d.flush(d)
        st = d.stats(d)
        self.assertGreater(st.rotations, 1)
        self.assertEqual(set(fs.files), {CUR, OLD})
        for p in (CUR, OLD):
            self.assertLessEqual(len(fs.files[p]), 4096)
            self.lines(fs.files[p])  # every line parses
        last_old = self.lines(fs.files[OLD])[-1]['t']
        first_cur = self.lines(fs.files[CUR])[0]['t']
        self.assertLessEqual(last_old, first_cur)
        self.assertEqual(st.io_errors, 0)
        self.assertEqual(st.file_bytes, len(fs.files[CUR]))

    def test_previous_run_file_rotated_not_overwritten(self):
        fs = FakeIO({CUR: '{"t":1,"kind":"old"}\n', OLD: '{"t":0,"kind":"older"}\n'})
        d = self.make(fs)
        d.event(d, 'x', None)
        self.assertTrue(d.flush(d))
        self.assertEqual(self.lines(fs.files[OLD]), [{'t': 1, 'kind': 'old'}])
        self.assertEqual(self.lines(fs.files[CUR])[-1]['kind'], 'x')
        self.assertLess(fs.calls.index('deleteFile'), fs.calls.index('move'))

    def test_io_absent_never_raises_and_counts(self):
        self.lua.globals().io = None
        d = self.diag.new(self.tab({'diagnostics': True, 'diagnostics_dir': DIR}), self.lua.table_from({'env': self.env}))
        self.run_frames(d, 700)
        st = d.stats(d)
        self.assertGreater(st.io_errors, 0)
        self.assertGreater(st.buffered, 0)

    def test_io_failing_never_raises_and_retries_later(self):
        fs = FakeIO(fail={'save', 'createDir', 'fileExists'})
        d = self.make(fs, diagnostics_flush_s=1)
        self.run_frames(d, 120)
        st = d.stats(d)
        self.assertGreaterEqual(st.io_errors, 2)
        self.assertEqual(st.lines_written, 0)
        fs.fail.clear()
        self.assertTrue(d.flush(d))
        rows = self.lines(fs.files[CUR])
        self.assertEqual(rows[0]['kind'], 'session_start')  # nothing lost while buffer had room

    def test_save_returning_false_is_counted(self):
        fs = FakeIO()
        fs.save = lambda p, data: False
        d = self.make(fs)
        d.event(d, 'x', None)
        self.assertFalse(d.flush(d))
        self.assertEqual(d.stats(d).io_errors, 1)

    def test_missing_car_and_bad_dt_are_tolerated(self):
        fs = FakeIO()
        d = self.make(fs)
        for dt in (None, float('nan'), -1, 1e9, 0.5):
            d.update(d, dt, None, None, None)
        d.flush(d)
        kinds = [r['kind'] for r in self.lines(fs.files[CUR])]
        self.assertEqual(kinds.count('car_missing'), 1)

    def test_detector_and_connection_logged_on_change_at_sample_rate(self):
        fs = FakeIO()
        d = self.make(fs)
        extra = self.tab({'detector': {'off_course': 'unknown'}, 'connection': {'online': True, 'pending': 0}})
        car = self.tab(self.car)
        for _ in range(120):
            d.update(d, 1/60, car, None, extra)
        extra2 = self.tab({'detector': {'off_course': 'unknown'}, 'connection': {'online': False, 'pending': 1}})
        for _ in range(120):
            d.update(d, 1/60, car, None, extra2)
        d.flush(d)
        rows = self.lines(fs.files[CUR])
        self.assertEqual(len([r for r in rows if r['kind'] == 'detector']), 1)
        conns = [r['state'] for r in rows if r['kind'] == 'connection']
        self.assertEqual(conns, [{'online': True, 'pending': 0}, {'online': False, 'pending': 1}])

    def test_injected_clock_and_fallback_dir(self):
        fs = FakeIO()
        self.env.ac.FolderID = self.lua.table_from({'Root': 1})
        self.env.ac.getFolder = self.wrap(lambda fid: 'C:/AC' if fid == 1 else None)
        d = self.diag.new(self.tab({'diagnostics': True}), self.lua.table_from({'io': self.io(fs), 'env': self.env, 'clock': self.wrap(lambda: 123.5)}))
        d.event(d, 'x', None)
        d.flush(d)
        self.assertEqual(self.lines(fs.files[CUR])[-1]['t'], 123.5)

    def test_cdata_like_car_raising_on_unknown_field(self):
        def index(obj, key):
            if key in ('position', 'lapCount'):
                return self.tab({'x': 1, 'y': 2, 'z': 3}) if key == 'position' else 4
            raise RuntimeError('no field ' + key)
        car = self.lua.execute('local f = ...; return setmetatable({}, {__index = function(t, k) return f(t, k) end})', index)
        fs = FakeIO()
        d = self.make(fs)
        for _ in range(60):
            d.update(d, 1/60, car, None, None)
        d.flush(d)
        rows = self.lines(fs.files[CUR])
        fields = next(r for r in rows if r['kind'] == 'car_fields')['available']
        self.assertIs(fields['car.position'], True)
        self.assertIs(fields['car.collisionDepth'], False)
        self.assertEqual(next(r for r in rows if r['kind'] == 'sample')['pos'], {'x': 1, 'y': 2, 'z': 3})


class LuaJITSmoke(unittest.TestCase):
    """CSP runs LuaJIT 2.1 (Lua 5.1 semantics); the default lupa runtime may be 5.4."""
    def test_encode_and_rotation_under_luajit(self):
        try:
            from lupa import luajit21
        except ImportError:
            self.skipTest('lupa.luajit21 unavailable')
        lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        self.assertIn('LuaJIT', lua.eval('jit.version'))
        diag = lua.execute((ROOT / 'diagnostics.lua').read_text())
        files = lua.execute('''
          local files = {}
          io = {save=function(p, d) files[p] = d; return true end, fileExists=function(p) return files[p] ~= nil end,
            load=function(p, f) return files[p] or f end, createDir=function() return true end,
            deleteFile=function(p) files[p] = nil; return true end,
            move=function(a, b) if files[b] or not files[a] then return false end; files[b] = files[a]; files[a] = nil; return true end}
          return files''')
        d = diag.new(lua.table_from({'diagnostics': True, 'diagnostics_dir': 'D', 'diagnostics_max_bytes': 4096,
                                     'diagnostics_flush_s': 1}), lua.table_from({'env': lua.table_from({})}))
        car = lua.table_from({'position': lua.table_from({'x': 1.25, 'y': 0, 'z': 2 ** 40}), 'lapCount': 3}, recursive=True)
        errs = lua.table_from({'errors': lua.table_from(['e' * 40])}, recursive=True)
        for _ in range(3000):
            d.update(d, 1/60, car, None, errs)
        d.flush(d)
        self.assertGreater(d.stats(d).rotations, 0)
        self.assertEqual(d.stats(d).io_errors, 0)
        for p in ('D/rice_diagnostics.jsonl', 'D/rice_diagnostics.1.jsonl'):
            text = files[p]
            self.assertLessEqual(len(text), 4096)
            rows = [json.loads(l) for l in text.split('\n') if l]
            self.assertTrue(rows)
        sample = next(r for r in rows if r['kind'] == 'sample')
        self.assertEqual(sample['pos'], {'x': 1.25, 'y': 0, 'z': 2 ** 40})


if __name__ == '__main__': unittest.main()
