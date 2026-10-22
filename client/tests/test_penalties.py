"""Penalty settings: values, dedupe, persistence, panel, and the lap payload built from them."""
import json
import unittest
from pathlib import Path
from lupa import LuaRuntime
from test_network import harness, config, drive_lap

ROOT = Path(__file__).resolve().parents[1]


def penalties_module():
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute("package.path = '%s/?.lua;' .. package.path" % ROOT.as_posix())
    return lua, lua.eval("(require('penalties'))")


class PenaltyMath(unittest.TestCase):
    def setUp(self):
        self.lua, self.m = penalties_module()
        self.t = lambda d: self.lua.table_from(d)

    def apply(self, cfg, counts, times=None):
        t = self.t
        return self.m.apply(cfg, t(counts), t({'off': t(times.get('off', [])), 'slalom': t(times.get('slalom', []))}) if times else None)

    def test_defaults_are_fsae(self):
        r = self.apply(self.m.defaults(), {'cone': 3, 'off_course': 1, 'slalom': 2})
        self.assertEqual(r.penalty_ms, 3 * 2000 + 20000 + 2 * 20000)

    def test_switched_off_adds_zero_but_count_is_kept_by_caller(self):
        cfg = self.m.defaults(); cfg.cone.on = False
        r = self.apply(cfg, {'cone': 5, 'off_course': 0, 'slalom': 0})
        self.assertEqual((r.penalty_ms, r.applied.cone), (0, 0))

    def test_seconds_are_edited_and_clamped(self):
        cfg = self.m.defaults(); cfg.cone.s = 3.5; cfg.off_course.s = 999; cfg.slalom.s = -4
        self.assertEqual(self.apply(cfg, {'cone': 2, 'off_course': 1, 'slalom': 1}).penalty_ms, 2 * 3500 + 60000 + 0)

    def test_same_incident_off_track_and_slalom_is_charged_once(self):
        r = self.apply(self.m.defaults(), {'cone': 0, 'off_course': 1, 'slalom': 1}, {'off': [100.0], 'slalom': [101.5]})
        self.assertEqual((r.penalty_ms, r.deduped), (20000, 1))

    def test_distant_incidents_are_both_charged(self):
        r = self.apply(self.m.defaults(), {'cone': 0, 'off_course': 1, 'slalom': 1}, {'off': [100.0], 'slalom': [160.0]})
        self.assertEqual((r.penalty_ms, r.deduped), (40000, 0))

    def test_no_dedupe_when_slalom_penalty_is_off(self):
        cfg = self.m.defaults(); cfg.slalom.on = False
        r = self.apply(cfg, {'cone': 0, 'off_course': 1, 'slalom': 1}, {'off': [100.0], 'slalom': [101.0]})
        self.assertEqual((r.penalty_ms, r.deduped), (20000, 0))

    def test_garbage_settings_fall_back_to_defaults(self):
        s = self.m.sanitize(self.t({'cone': self.t({'on': 'yes', 's': 'x'}), 'off_course': 7}))
        self.assertEqual((s.cone.on, s.cone.s, s.off_course.s), (True, 2, 20))


class Persistence(unittest.TestCase):
    PATH = '/state/penalty_config.json'

    def make(self, disk):
        """`disk` is a Python dict shared across 'restarts' (a fresh Lua runtime each time)."""
        lua, m = penalties_module()
        disk = {} if disk is None else disk
        def save(p, d): disk[p] = d; return True
        wrap = lua.eval('function(f) return function(...) return f(...) end end')  # real Lua functions, as in CSP
        io = lua.table_from({'save': wrap(save), 'load': wrap(lambda p: disk.get(p)), 'createDir': wrap(lambda p: True)})
        js = lua.table_from({'encode': wrap(lambda v: json.dumps(self.py(v))), 'decode': wrap(lambda t: self.lua_t(lua, json.loads(t)))})
        return lua, m, m.new(lua.table_from({'dir': '/state', 'io': io, 'json': js})), disk

    def py(self, o):
        if hasattr(o, 'items'): return {str(k): self.py(v) for k, v in o.items()}
        return o

    def lua_t(self, lua, o):
        return lua.table_from({k: self.lua_t(lua, v) for k, v in o.items()}) if isinstance(o, dict) else o

    def restart(self, files):
        lua, m, store, _ = self.make(files)
        return store

    def test_edit_is_saved_and_survives_restart(self):
        lua, m, a, files = self.make(None)
        a.set(a, 'cone', 's', 4); a.set(a, 'slalom', 'on', False)
        a.flush(a, 1.0)
        b = self.make(files)[2]
        self.assertEqual((b.cfg.cone.s, b.cfg.slalom.on, b.state), (4, False, 'ok'))

    def test_reset_restores_fsae_and_persists(self):
        lua, m, a, files = self.make(None)
        a.set(a, 'cone', 's', 9); a.flush(a, 1.0); a.reset(a)
        self.assertEqual(self.make(files)[2].cfg.cone.s, 2)

    def test_corrupt_file_falls_back_to_defaults(self):
        lua, m, a, files = self.make(None)
        files[self.PATH] = '{nope'
        c = self.make(files)[2]
        self.assertEqual((c.cfg.cone.s, c.state), (2, 'default'))

    def test_save_is_debounced(self):
        lua, m, a, files = self.make(None)
        a.set(a, 'cone', 's', 5); a.flush(a, 0.1)
        self.assertNotIn(self.PATH, files)
        a.flush(a, 0.6)
        self.assertIn(self.PATH, files)

    def test_unwritable_storage_is_reported_not_hidden(self):
        lua, m = penalties_module()
        io = lua.execute("return {save=function() return false end, load=function() return nil end}")
        wrap = lua.eval('function(f) return function(...) return f(...) end end')
        js = lua.table_from({'encode': wrap(lambda v: json.dumps(self.py(v))), 'decode': wrap(lambda t: self.lua_t(lua, json.loads(t)))})
        st = m.new(lua.table_from({'dir': '/state', 'io': io, 'json': js}))
        self.assertFalse(st.save(st))
        self.assertEqual(st.state, 'unsaved')


class LapPayload(unittest.TestCase):
    def lap(self, edit=None, **extra):
        lua, storage, position, requests, reply, to_lua = harness()
        lua.execute("local p = ac.getCar; ac.getCar = function(n) local c = p(n); c.look = {x=0, y=0, z=-1}; return c end")
        script = lua.globals().script
        script.connect('http://127.0.0.1:8080', 'station-1', 'secret')
        script.configure(config(to_lua))
        cfg = {'version': 'cone-boxes-v1', 'half_width_m': 0.705, 'half_length_m': 1.48, 'shrink_m': 0.15,
               'cone_radius_m': 0.125, 'min_depth_m': 0.03, 'auto_penalties': True,
               'cones': [{'id': 'HIT', 'x': 0.0, 'z': -2.0}],
               'limits': {'outer': [[-100, -100], [100, -100], [100, 100], [-100, 100]],
                          'inner': [[500, 500], [501, 500], [501, 501], [500, 501]]}}
        script.configureCones(to_lua(cfg))
        if edit: edit(lua)
        reply(0, {'station_id': 'station-1', 'session_id': 's1', 'pilot_id': 'p1'})
        drive_lap(lua, position)
        post = next(r for r in requests if r[1].endswith('/api/laps'))
        return json.loads(post[3])

    def test_payload_carries_final_time_and_the_settings_used(self):
        p = self.lap()
        self.assertEqual(p['events']['cone'], 1)
        self.assertEqual(p['corrected_ms'], p['raw_ms'] + 2000)
        self.assertEqual(p['penalty']['penalty_ms'], 2000)
        self.assertEqual(p['penalty']['config']['cone']['s'], 2)
        self.assertEqual(p['status'], 'PENDING_REVIEW')

    def test_switched_off_cone_is_still_reported_but_adds_nothing(self):
        p = self.lap(edit=lambda lua: lua.execute("script.penaltyStore().cfg.cone.on = false"))
        self.assertEqual(p['events']['cone'], 1)
        self.assertEqual(p['corrected_ms'], p['raw_ms'])
        self.assertEqual(p['penalty']['applied']['cone'], 0)

    def test_edited_seconds_apply_to_the_next_lap(self):
        p = self.lap(edit=lambda lua: lua.execute("script.penaltyStore().cfg.cone.s = 5"))
        self.assertEqual(p['corrected_ms'], p['raw_ms'] + 5000)
        self.assertEqual(p['penalty']['config']['cone']['s'], 5)


class Panel(unittest.TestCase):
    def test_panel_draws_and_buttons_edit_settings(self):
        lua, m = penalties_module()
        calls = []
        pressed = {'name': None}
        ui = lua.table_from({
            'text': lambda s: calls.append(('text', s)),
            'button': lambda label: label == pressed['name'] or (calls.append(('button', label)) and False),
            'sameLine': lambda *a: None,
            'slider': lambda *a: (a[1], False),
        })
        store = m.new(lua.table_from({}))
        copied = []
        ac = lua.table_from({'setClipboardText': lambda s: copied.append(s)})
        self.assertTrue(m.draw(store, ui, ac, 0.016))
        labels = [c[1] for c in calls if c[0] == 'button']
        self.assertTrue(any('Conos' in l for l in labels) and any('Off-track' in l for l in labels) and any('Slalom omitido' in l for l in labels))
        pressed['name'] = '[x] Conos##on_cone'
        m.draw(store, ui, ac, 0.016)
        self.assertFalse(store.cfg.cone.on)
        pressed['name'] = 'Restaurar FSAE##reset'
        m.draw(store, ui, ac, 0.016)
        self.assertTrue(store.cfg.cone.on)

    def test_panel_without_ui_is_safe(self):
        lua, m = penalties_module()
        self.assertFalse(m.draw(m.new(lua.table_from({})), None, None, 0.1))


if __name__ == '__main__':
    unittest.main()
