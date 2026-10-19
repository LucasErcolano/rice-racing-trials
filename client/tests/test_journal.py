"""Journal tests: real Lua (lupa, LuaJIT 2.1 like CSP) over a dict-backed io double.

Each "restart" is a brand new LuaRuntime sharing only the Python dict that plays the
filesystem. This verifies the Lua logic, not CSP's io implementation on Windows.
"""
import json
import unittest
from pathlib import Path


try:
    from lupa.luajit21 import LuaRuntime, lua_type
except ImportError:  # pragma: no cover
    from lupa import LuaRuntime, lua_type

ROOT = Path(__file__).resolve().parents[1]
SRC = (ROOT / 'journal.lua').read_text(encoding='utf-8')
DIR = 'C:/AC/apps/lua/RiceTrials/state'
A, B = DIR + '/queue.a.json', DIR + '/queue.b.json'
LEGACY = 'rice_trials_pending_review_v1'
DEGRADED = 'rice_trials_journal_v2'


class FakeIO:
    """Filesystem as a dict. hooks let a test corrupt what save() writes or load() returns."""
    def __init__(self, files):
        self.files = files
        self.save_hook = None   # fn(path, data) -> data to store, or raise
        self.load_hook = None   # fn(path, data) -> data to return
        self.save_result = True
        self.calls = []

    def save(self, p, data):
        self.calls.append(('save', p))
        if self.save_hook:
            data = self.save_hook(p, data)
        if data is not None:
            self.files[p] = data
        return self.save_result

    def load(self, p, fallback=None):
        self.calls.append(('load', p))
        data = self.files.get(p, fallback)
        if self.load_hook and data is not None:
            data = self.load_hook(p, data)
        return data

    def fileExists(self, p):
        return p in self.files

    def createDir(self, p):
        return True

    def deleteFile(self, p):
        return self.files.pop(p, None) is not None


class Storage(dict):
    """ac.storage double: assigning nil (None) deletes the key."""


class NoFallbackStorage(Storage):
    """Storage that refuses the degraded journal key (fallback unavailable)."""
    def __setitem__(self, k, v):
        if k == DEGRADED:
            raise RuntimeError('storage write refused')
        super().__setitem__(k, v)


def lua_to_py(v):
    if lua_type(v) == 'table':
        keys = list(v.keys())
        if all(isinstance(k, (int, float)) and k == int(k) for k in keys) and \
                sorted(int(k) for k in keys) == list(range(1, len(keys) + 1)):
            return [lua_to_py(v[i]) for i in range(1, len(keys) + 1)]
        return {str(k): lua_to_py(v[k]) for k in keys}
    if isinstance(v, float) and v.is_integer() and abs(v) < 2 ** 53:
        return int(v)
    return v


def canon(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class VM:
    """One Lua VM = one app/AC session."""
    def __init__(self, files, storage=None, io=True, fs=None):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.mod = self.lua.execute(SRC)
        self.wrap = self.lua.eval('function(f) return function(...) return f(...) end end')
        self.fs = fs or FakeIO(files)
        deps = {
            'json': self.lua.table_from({
                'encode': self.wrap(lambda t: canon(lua_to_py(t))),
                'decode': self.wrap(lambda s: self.py_to_lua(json.loads(s))),
            }),
            'legacy_key': LEGACY,
        }
        if io:
            names = ('save', 'load', 'fileExists', 'createDir', 'deleteFile')
            deps['io'] = self.lua.table_from(
                {n: self.wrap((lambda n=n: lambda *a: getattr(self.fs, n)(*a))()) for n in names})
        else:
            deps['io_disabled'] = True
        if storage is not None:
            deps['storage'] = self.storage_proxy(storage)
        self.j = self.mod.new(self.lua.table_from({'dir': DIR, 'name': 'queue'}),
                              self.lua.table_from(deps))

    def storage_proxy(self, storage):
        mk = self.lua.eval('''function(get, set)
          return setmetatable({}, {__index=function(_, k) return get(k) end,
                                   __newindex=function(_, k, v) set(k, v) end}) end''')

        def setter(k, v):
            if v is None:
                storage.pop(k, None)
            else:
                storage[k] = v
        return mk(self.wrap(lambda k: storage.get(k)), self.wrap(setter))

    def py_to_lua(self, v):
        if isinstance(v, (list, dict)):
            return self.lua.table_from(v, recursive=True)
        return v

    def items(self, py):
        return self.lua.table_from(py, recursive=True)

    def load(self):
        items, state = self.j.load(self.j)
        return lua_to_py(items) if len(items) else [], state

    def save(self, py):
        r = self.j.save(self.j, self.items(py))
        return r if isinstance(r, tuple) else (r, None)

    def state(self):
        return lua_to_py(self.j.state(self.j))

    def can_send(self):
        return self.j.can_send(self.j)


def seq_of(raw):
    return json.loads(raw)['seq']


class Journal(unittest.TestCase):
    def setUp(self):
        self.files = {}

    def test_round_trip_across_restart(self):
        vm = VM(self.files)
        self.assertEqual(vm.load(), ([], 'empty'))
        ok, err = vm.save([{'id': 'L1', 'raw_ms': 61234}, {'id': 'L2', 'raw_ms': 59999}])
        self.assertTrue(ok, err)
        env = json.loads(self.files[A])
        self.assertEqual(env['v'], 1)
        self.assertEqual(env['seq'], 1)
        self.assertIsInstance(env['items'], str)
        items, state = VM(self.files).load()
        self.assertEqual(state, 'ok')
        self.assertEqual(items, [{'id': 'L1', 'raw_ms': 61234}, {'id': 'L2', 'raw_ms': 59999}])

    def test_empty_queue_round_trip(self):
        vm = VM(self.files)
        vm.load()
        self.assertTrue(vm.save([])[0])
        self.assertEqual(VM(self.files).load(), ([], 'ok'))

    def test_slots_alternate_and_seq_grows(self):
        vm = VM(self.files)
        vm.load()
        seen = []
        for i in range(1, 6):
            self.assertTrue(vm.save([{'n': i}])[0])
            st = vm.state()
            seen.append((st['slot'], st['seq']))
        self.assertEqual(seen, [('a', 1), ('b', 2), ('a', 3), ('b', 4), ('a', 5)])
        self.assertEqual((seq_of(self.files[A]), seq_of(self.files[B])), (5, 4))
        # after a restart the next write goes to the older slot with seq 6
        vm2 = VM(self.files)
        self.assertEqual(vm2.load(), ([{'n': 5}], 'ok'))
        self.assertTrue(vm2.save([{'n': 6}])[0])
        self.assertEqual(seq_of(self.files[B]), 6)
        self.assertEqual(seq_of(self.files[A]), 5)

    def test_crash_before_write_keeps_previous(self):
        vm = VM(self.files)
        vm.load()
        vm.save([{'n': 1}])
        vm.save([{'n': 2}])
        snapshot = dict(self.files)
        # "crash": the VM dies before io.save of n=3 runs; dict unchanged
        del vm
        self.assertEqual(self.files, snapshot)
        self.assertEqual(VM(self.files).load(), ([{'n': 2}], 'ok'))

    def test_crash_mid_write_truncated_new_slot(self):
        vm = VM(self.files)
        vm.load()
        vm.save([{'n': 1}])            # slot a, seq 1
        vm.save([{'n': 2}])            # slot b, seq 2
        old_b = self.files[B]
        vm.fs.save_hook = lambda p, d: d[:len(d) // 2]   # partial write of slot a
        ok, _ = vm.save([{'n': 3}])
        self.assertFalse(ok)
        self.assertEqual(self.files[B], old_b)
        vm2 = VM(self.files)
        self.assertEqual(vm2.load(), ([{'n': 2}], 'ok'))
        self.assertIn('slot a', vm2.state()['last_error'])
        self.assertTrue(vm2.can_send())
        # the next save overwrites the broken slot, not the good one
        self.assertTrue(vm2.save([{'n': 3}])[0])
        self.assertEqual(self.files[B], old_b)
        self.assertEqual(seq_of(self.files[A]), 3)

    def test_readback_mismatch_returns_false_and_previous_survives(self):
        vm = VM(self.files)
        vm.load()
        vm.save([{'n': 1}])
        old_a = self.files[A]
        vm.fs.load_hook = lambda p, d: d.replace(':2}', ':9}') if p == B else d
        ok, err = vm.save([{'n': 2}])
        self.assertFalse(ok)
        self.assertIn('readback', err)
        self.assertEqual(self.files[A], old_a)       # previous slot never touched
        # a later save in the same VM must not clobber the confirmed slot a
        vm.fs.load_hook = None
        self.assertTrue(vm.save([{'n': 3}])[0])
        self.assertEqual(self.files[A], old_a)
        self.assertEqual(VM(self.files).load(), ([{'n': 3}], 'ok'))

    def test_unconfirmed_but_intact_write_may_surface_after_restart(self):
        # false means "not confirmed", not "not written": if the bytes on disk are
        # actually valid, the next load may return them (documented behavior).
        vm = VM(self.files)
        vm.load()
        vm.save([{'n': 1}])
        vm.fs.load_hook = lambda p, d: d.replace(':2}', ':9}') if p == B else d
        self.assertFalse(vm.save([{'n': 2}])[0])
        self.assertEqual(VM(self.files).load(), ([{'n': 2}], 'ok'))

    def test_io_save_returning_false_is_failure(self):
        vm = VM(self.files)
        vm.load()
        vm.save([{'n': 1}])
        vm.fs.save_result = False
        vm.fs.save_hook = lambda p, d: None
        ok, err = vm.save([{'n': 2}])
        self.assertFalse(ok)
        self.assertIn('io.save returned false', err)
        self.assertEqual(VM(self.files).load(), ([{'n': 1}], 'ok'))

    def test_checksum_detects_tampered_items(self):
        vm = VM(self.files)
        vm.load()
        vm.save([{'n': 1}])
        vm.save([{'n': 2}])
        env = json.loads(self.files[B])
        env['items'] = env['items'].replace('2', '7')   # valid JSON, wrong checksum
        self.files[B] = json.dumps(env)
        items, state = VM(self.files).load()
        self.assertEqual((items, state), ([{'n': 1}], 'ok'))

    def test_one_corrupt_slot(self):
        vm = VM(self.files)
        vm.load()
        vm.save([{'n': 1}])
        self.files[B] = '{garbage'
        vm2 = VM(self.files)
        self.assertEqual(vm2.load(), ([{'n': 1}], 'ok'))
        self.assertIsNone(vm2.state().get('quarantined'))
        self.assertTrue(vm2.can_send())

    def test_both_corrupt_is_fail_closed_with_quarantine(self):
        self.files[A] = '{"v":1,"seq":3,"checksum":"00000000","items":"[]"}'
        self.files[B] = 'not json \u00e9'
        storage = Storage({LEGACY: '[{"id":"old"}]'})
        vm = VM(self.files, storage=storage)
        items, state = vm.load()
        self.assertEqual((items, state), ([], 'corrupt'))
        st = vm.state()
        self.assertEqual(st['state'], 'corrupt')
        self.assertFalse(vm.can_send())
        q1, q2 = DIR + '/queue.quarantine.1.json', DIR + '/queue.quarantine.2.json'
        self.assertEqual(st['quarantined'], q1)
        self.assertEqual(self.files[q1], self.files[A])
        self.assertEqual(self.files[q2], self.files[B])
        # originals untouched, legacy not migrated over it, saves refused
        self.assertIn(LEGACY, storage)
        ok, err = vm.save([{'n': 1}])
        self.assertFalse(ok)
        self.assertIn('corrupt', err)
        self.assertEqual(self.files[B], 'not json \u00e9')
        # a restart does not overwrite existing quarantine files
        vm2 = VM(self.files, storage=storage)
        self.assertEqual(vm2.load()[1], 'corrupt')
        self.assertEqual(vm2.state()['quarantined'], DIR + '/queue.quarantine.3.json')

    def test_migration_deletes_legacy_only_after_confirmed_save(self):
        legacy = [{'id': 'L1', 'payload': '{"raw_ms":1}'}, {'id': 'L2', 'payload': '{"raw_ms":2}'}]
        storage = Storage({LEGACY: json.dumps(legacy)})
        vm = VM(self.files, storage=storage)
        items, state = vm.load()
        self.assertEqual((items, state), (legacy, 'migrated'))
        self.assertNotIn(LEGACY, storage)
        self.assertTrue(vm.can_send())
        self.assertEqual(VM(self.files, storage=storage).load(), (legacy, 'ok'))

    def test_migration_accepts_current_riceTrials_envelope(self):
        entries = [{'id': 'x1', 'payload': '{}'}]
        storage = Storage({LEGACY: json.dumps({'version': 1, 'nonce': 'n', 'next_id': 2,
                                               'entries': entries})})
        vm = VM(self.files, storage=storage)
        self.assertEqual(vm.load(), (entries, 'migrated'))
        self.assertEqual(lua_to_py(vm.j.legacy_meta)['next_id'], 2)

    def test_migration_keeps_legacy_when_save_fails(self):
        legacy = [{'id': 'L1'}]
        raw = json.dumps(legacy)
        fs = FakeIO(self.files)
        fs.save_hook = lambda p, d: d[:10]          # every write is truncated
        storage = NoFallbackStorage({LEGACY: raw})
        # no usable fallback either: nothing confirmed -> legacy kept
        vm = VM(self.files, storage=storage, fs=fs)
        items, state = vm.load()
        self.assertEqual(storage.get(LEGACY), raw)
        self.assertEqual(state, 'degraded')
        self.assertEqual(items, legacy)
        self.assertIn('migration save failed', vm.state()['last_error'])

    def test_migration_failure_with_storage_does_not_delete_legacy(self):
        raw = json.dumps([{'id': 'L1'}])
        fs = FakeIO(self.files)
        fs.save_hook = lambda p, d: d[:10]
        storage = Storage({LEGACY: raw})
        vm = VM(self.files, storage=storage, fs=fs)
        items, state = vm.load()
        self.assertEqual(storage.get(LEGACY), raw)
        self.assertEqual(state, 'degraded')

    def test_io_absent_is_degraded_storage(self):
        storage = Storage()
        vm = VM(self.files, storage=storage, io=False)
        self.assertEqual(vm.load(), ([], 'degraded'))
        self.assertTrue(vm.save([{'n': 1}])[0])
        self.assertEqual(vm.state()['state'], 'degraded')
        self.assertIn(DEGRADED, storage)
        self.assertEqual(self.files, {})
        self.assertEqual(VM(self.files, storage=storage, io=False).load(), ([{'n': 1}], 'degraded'))

    def test_io_save_always_failing_falls_back_to_storage(self):
        storage = Storage()
        fs = FakeIO(self.files)
        fs.save_result = False
        fs.save_hook = lambda p, d: None
        vm = VM(self.files, storage=storage, fs=fs)
        vm.load()
        self.assertTrue(vm.save([{'n': 1}])[0])
        self.assertEqual(vm.state()['state'], 'degraded')
        self.assertEqual(VM(self.files, storage=storage).load(), ([{'n': 1}], 'degraded'))
        # io recovers: a newer file write wins over the storage copy
        vm2 = VM(self.files, storage=storage)
        vm2.load()
        self.assertTrue(vm2.save([{'n': 2}])[0])
        self.assertEqual(VM(self.files, storage=storage).load(), ([{'n': 2}], 'ok'))

    def test_io_absent_without_storage_fails(self):
        vm = VM(self.files, io=False)
        self.assertEqual(vm.load(), ([], 'degraded'))
        self.assertFalse(vm.save([{'n': 1}])[0])

    def test_fnv1a_vectors_bit_and_pure(self):
        vectors = {'': '811c9dc5', 'a': 'e40c292c', 'foobar': 'bf9cf968'}
        vm = VM(self.files)
        self.assertEqual(vm.mod.FNV_IMPL, 'bit')
        for s, h in vectors.items():
            self.assertEqual(vm.mod.fnv1a(s), h, s)
            self.assertEqual(vm.mod.fnv1a_pure(s), h, s)
        # both implementations agree on arbitrary bytes, including UTF-8 and 0xff
        for s in ['\u00e9\u65e5\u672c\U0001F600', 'x' * 1000, '\x00\x7f']:
            self.assertEqual(vm.mod.fnv1a(s), vm.mod.fnv1a_pure(s))

    def test_fnv1a_pure_on_plain_lua(self):
        vectors = {'': '811c9dc5', 'a': 'e40c292c', 'foobar': 'bf9cf968'}
        import importlib
        for name in ('lupa.lua51', 'lupa.lua54'):
            try:
                rt = importlib.import_module(name).LuaRuntime()
            except ImportError:  # pragma: no cover
                continue
            mod = rt.execute(SRC)
            self.assertEqual(mod.FNV_IMPL, 'pure', name)
            for s, h in vectors.items():
                self.assertEqual(mod.fnv1a(s), h, (name, s))

    def test_200_items_unicode_and_large_numbers_byte_identical(self):
        items = []
        for i in range(200):
            items.append({
                'id': 'lap-%03d' % i,
                'driver': ['Jos\u00e9 \u00d1u\u00f1ez', '\u7530\u4e2d \u592a\u90ce', 'Zo\u00eb \U0001F3CE',
                           'quote " back \\ nl \n tab \t'][i % 4],
                'raw_ms': 2 ** 53 - 1 - i,
                'neg': -(2 ** 40) + i,
                'ratio': i / 7,
                'flags': [True, False, None][i % 3],
                'nested': {'cones': [i, i + 1], 'note': '\u00bfv\u00e1lido?'},
            })
        vm = VM(self.files)
        vm.load()
        lua_items = vm.items(items)
        # JSON of each item exactly as the Lua side holds it at save time
        # (Lua drops nil fields; an integral double such as 0.0 is the Lua number 0)
        saved_json = [canon(lua_to_py(lua_items[i + 1])) for i in range(200)]
        self.assertTrue(vm.j.save(vm.j, lua_items))
        got, state = VM(self.files).load()
        self.assertEqual(state, 'ok')
        self.assertEqual(len(got), 200)
        self.assertEqual([canon(g) for g in got], saved_json)
        self.assertEqual(got[0]['raw_ms'], 2 ** 53 - 1)
        self.assertEqual(got[3]['driver'], 'quote " back \\ nl \n tab \t')
        self.assertEqual(got[2]['driver'], 'Zo\u00eb \U0001F3CE')


if __name__ == '__main__':
    unittest.main()
