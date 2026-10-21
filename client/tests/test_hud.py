"""HUD model/render tests with explicit ui doubles (not a CSP runtime test)."""
import unittest
from pathlib import Path
from lupa import LuaRuntime

ROOT = Path(__file__).resolve().parents[1]


class HudModel(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().package.path = str(ROOT / '?.lua') + ';' + self.lua.globals().package.path
        self.hud = self.lua.execute((ROOT / 'hud.lua').read_text())

    def tab(self, value):
        return self.lua.table_from(value, recursive=True)

    def model(self, state=None, config=None):
        m = self.hud.model(self.tab(state) if state is not None else None,
                           self.tab(config) if config is not None else None)
        lines = [(m.lines[i].text, m.lines[i].level) for i in range(1, len(m.lines) + 1)]
        banner = (m.banner.text, m.banner.level) if m.banner else None
        return m, lines, banner

    def find(self, lines, prefix):
        hits = [l for l in lines if l[0].startswith(prefix)]
        self.assertTrue(hits, f'no line starting with {prefix!r} in {lines}')
        return hits[0]

    def base(self, **over):
        state = {'assignment': {'pilot_id': 'p1', 'pilot_name': 'Ana'}, 'event': 'endurance',
                 'phase': 'timed', 'lap': {'elapsed_ms': 70123, 'penalties': {}},
                 'sync': {'online': True, 'pending': 0, 'persistence': 'ok'}}
        state.update(over)
        return state

    def test_empty_or_nil_state_does_not_raise(self):
        for state in (None, {}):
            m, lines, banner = self.model(state)
            self.assertIn('PROVISIONAL', m.title)
            self.assertEqual(self.find(lines, 'Piloto')[1], 'warn')
            self.assertEqual(self.find(lines, 'Tiempo')[0], 'Tiempo: --')
            self.assertEqual(self.find(lines, 'Mejor corregida')[0], 'Mejor corregida: --')
            self.assertEqual(self.find(lines, 'Cola pendiente')[0], 'Cola pendiente: desconocida')

    def test_without_assignment(self):
        _, lines, banner = self.model({'phase': 'waiting', 'sync': {'online': True, 'pending': 0, 'persistence': 'ok'}})
        self.assertEqual(self.find(lines, 'Piloto'), ('Piloto: SIN ASIGNACION (no se puntua)', 'warn'))
        self.assertEqual(banner, ('SIN PILOTO ASIGNADO', 'warn'))

    def test_outlap_visible_and_untimed(self):
        _, lines, banner = self.model(self.base(phase='outlap'))
        self.assertEqual(self.find(lines, 'OUTLAP')[1], 'warn')
        self.assertEqual(self.find(lines, 'Tiempo')[0], 'Tiempo: --')
        self.assertFalse([l for l in lines if l[0].startswith('Proyectado')])
        self.assertEqual(banner, ('OUTLAP - no cuenta', 'info'))

    def test_lap_in_progress_without_evidence_never_shows_zero(self):
        _, lines, _ = self.model(self.base())
        self.assertEqual(self.find(lines, 'Tiempo')[0], 'Tiempo: 1:10.123')
        for label in ('Conos', 'Off-course', 'Slalom'):
            self.assertEqual(self.find(lines, label + ':'), (label + ': sin evidencia', 'warn'))
        projected = self.find(lines, 'Proyectado')
        self.assertTrue(projected[0].startswith('Proyectado: sin evidencia'))
        self.assertNotIn('1:10.123', projected[0])

    def test_partial_evidence_does_not_project(self):
        _, lines, _ = self.model(self.base(lap={'elapsed_ms': 70000, 'penalties': {'cone': 1}}))
        self.assertEqual(self.find(lines, 'Conos:')[0], 'Conos: 1 +0:02.000')
        self.assertEqual(self.find(lines, 'Proyectado')[0], 'Proyectado: sin evidencia (Off-course, Slalom)')

    def test_penalties_project_with_scoring_corrected_ms(self):
        state = self.base(lap={'elapsed_ms': 68500, 'penalties': {'cone': 3, 'off_course': 2, 'slalom': ['S1', 'S4']}})
        _, lines, _ = self.model(state)
        self.assertEqual(self.find(lines, 'Conos:')[0], 'Conos: 3 +0:06.000')
        self.assertEqual(self.find(lines, 'Off-course:')[0], 'Off-course: 2 +0:40.000')
        self.assertEqual(self.find(lines, 'Slalom:')[0], 'Slalom: 2 (S1,S4) +0:40.000')
        # Fixture "endurance combined" in rules/scoring-v1.json: 154500 ms.
        self.assertEqual(self.find(lines, 'Proyectado'), ('Proyectado PROVISIONAL: 2:34.500', 'info'))

    def test_station_configured_amounts_and_projection_are_shown(self):
        state = self.base(lap={'elapsed_ms': 68500, 'penalties': {'cone': 2, 'off_course': 1, 'slalom': ['S1']},
                               'penalty_amounts': {'cone': 3500, 'off_course': 0, 'slalom': 20000},
                               'projected_ms': 68500 + 2 * 3500 + 20000})
        _, lines, _ = self.model(state)
        self.assertEqual(self.find(lines, 'Conos:')[0], 'Conos: 2 +0:07.000')
        self.assertEqual(self.find(lines, 'Off-course:')[0], 'Off-course: 1 +0:00.000')
        self.assertEqual(self.find(lines, 'Proyectado'), ('Proyectado PROVISIONAL: 1:35.500', 'info'))

    def test_clean_lap_with_explicit_zero_evidence(self):
        state = self.base(lap={'elapsed_ms': 70123, 'penalties': {'cone': 0, 'off_course': 0, 'slalom': []}})
        _, lines, _ = self.model(state)
        self.assertEqual(self.find(lines, 'Conos:'), ('Conos: 0', 'ok'))
        self.assertEqual(self.find(lines, 'Proyectado')[0], 'Proyectado PROVISIONAL: 1:10.123')

    def test_invalid_penalty_is_error_not_projection(self):
        state = self.base(lap={'elapsed_ms': 70000, 'penalties': {'cone': -1, 'off_course': 0, 'slalom': []}})
        _, lines, _ = self.model(state)
        self.assertEqual(self.find(lines, 'Conos:')[1], 'error')
        self.assertEqual(self.find(lines, 'Proyectado'), ('Proyectado: no calculable', 'error'))

    def test_skidpad_not_projected(self):
        _, lines, _ = self.model(self.base(event='skidpad'))
        self.assertEqual(self.find(lines, 'Proyectado')[0], 'Proyectado: no disponible')

    def test_offline_with_queue(self):
        _, lines, banner = self.model(self.base(sync={'online': False, 'pending': 3, 'persistence': 'ok',
                                                      'last_ack': {'lap_id': 'ST01-n-4', 'age_s': 42.7}}))
        self.assertEqual(self.find(lines, 'Servidor'), ('Servidor: OFFLINE', 'warn'))
        self.assertEqual(self.find(lines, 'Cola pendiente'), ('Cola pendiente: 3', 'warn'))
        self.assertEqual(self.find(lines, 'Ultimo ack')[0], 'Ultimo ack: ST01-n-4 hace 42s')
        self.assertEqual(banner, ('OFFLINE: 3 vuelta(s) en cola local', 'warn'))

    def test_corrupt_persistence_is_error_banner(self):
        _, lines, banner = self.model(self.base(phase='outlap', sync={'online': True, 'pending': 0, 'persistence': 'corrupt'}))
        self.assertEqual(self.find(lines, 'Persistencia')[1], 'error')
        self.assertEqual(banner, ('PERSISTENCIA CORRUPTA - avisar al operador', 'error'))
        _, lines, banner = self.model(self.base(sync={'online': True, 'pending': 0, 'persistence': 'degraded'}))
        self.assertEqual(self.find(lines, 'Persistencia')[1], 'warn')
        self.assertEqual(banner, ('PERSISTENCIA DEGRADADA', 'warn'))

    def test_pending_review_vs_valid_last_and_best(self):
        _, lines, _ = self.model(self.base(last_official={'lap_id': 'a', 'status': 'PENDING_REVIEW', 'raw_ms': 71000, 'corrected_ms': 71000},
                                           best={'status': 'PENDING_REVIEW', 'corrected_ms': 71000}))
        last = self.find(lines, 'Ultima')
        self.assertEqual(last, ('Ultima: 1:11.000 bruto PENDING_REVIEW (no oficial)', 'warn'))
        self.assertEqual(self.find(lines, 'Mejor corregida')[0], 'Mejor corregida: --')
        _, lines, _ = self.model(self.base(last_official={'lap_id': 'b', 'status': 'VALID', 'raw_ms': 70000, 'corrected_ms': 72000},
                                           best={'status': 'VALID', 'corrected_ms': 72000}))
        self.assertEqual(self.find(lines, 'Ultima oficial'), ('Ultima oficial: 1:12.000 VALID', 'ok'))
        self.assertEqual(self.find(lines, 'Mejor corregida'), ('Mejor corregida: 1:12.000', 'ok'))

    def test_unknown_server_status_is_error(self):
        _, lines, _ = self.model(self.base(last_official={'status': 'GREAT', 'raw_ms': 1}))
        self.assertEqual(self.find(lines, 'Ultima oficial')[1], 'error')

    def test_detector_errors_bounded_and_banner(self):
        errs = [f'err{i}' for i in range(5)]
        _, lines, banner = self.model(self.base(detector_errors=errs), {'hud_max_errors': 2})
        shown = [l for l in lines if l[0].startswith('ERROR detector')]
        self.assertEqual(len(shown), 2)
        self.assertEqual(self.find(lines, '(+3'), ('(+3 errores mas)', 'error'))
        self.assertEqual(banner, ('ERROR DE DETECTOR', 'error'))

    def test_untrusted_text_is_sanitized(self):
        _, lines, _ = self.model(self.base(assignment={'pilot_id': 'p1', 'pilot_name': 'x\nA' * 30}))
        pilot = self.find(lines, 'Piloto')[0]
        self.assertNotIn('\n', pilot)
        self.assertLessEqual(len(pilot), len('Piloto: ') + 40)

    def test_layout_config_clamped(self):
        m, _, _ = self.model({}, {'hud_scale': 99, 'hud_x': -5, 'hud_y': 120})
        self.assertEqual((m.layout.scale, m.layout.x, m.layout.y), (3, 0, 120))
        m, _, _ = self.model({}, {'hud_scale': 'big'})
        self.assertEqual((m.layout.scale, m.layout.x, m.layout.y), (1, 8, 8))

    def test_cache_recomputes_at_interval_only(self):
        calls = self.lua.execute('''
          local hud = ...
          local n, real = 0, hud.model
          hud.model = function(s, c) n = n + 1; return real(s, c) end
          local cache = hud.cache(0.25)
          for i = 1, 60 do cache:update(1/60, {}, {}) end
          hud.model = real
          return n
        ''', self.hud)
        self.assertEqual(calls, 4)  # first frame + every 0.25 s over 1 s


class HudDraw(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().package.path = str(ROOT / '?.lua') + ';' + self.lua.globals().package.path
        self.hud = self.lua.execute((ROOT / 'hud.lua').read_text())
        self.lua.execute('''
          vec2 = function(x, y) return {x=x, y=y} end
          rgbm = function(r, g, b, m) return {r=r, g=g, b=b, m=m} end
        ''')
        self.calls = []
        # Python callables are userdata in Lua; hud.draw requires real functions.
        self.wrap = self.lua.eval('function(f) return function(...) return f(...) end end')

    def ui(self, names):
        def rec(name):
            return self.wrap(lambda *a: self.calls.append((name,) + a))
        funcs = {n: rec(n) for n in names}
        if 'windowSize' in names:
            funcs['windowSize'] = self.wrap(lambda: (self.calls.append(('windowSize',)), self.lua.eval('{x=500, y=300}'))[1])
        self.lua.globals().ui = self.lua.table_from(funcs)

    def model(self):
        return self.hud.model(self.lua.table_from({'phase': 'outlap'}, recursive=True),
                              self.lua.table_from({'hud_scale': 2, 'hud_x': 10, 'hud_y': 20}))

    def test_dwrite_path_balanced_and_scaled(self):
        self.ui(['pushDWriteFont', 'popDWriteFont', 'dwriteText', 'drawRectFilled', 'windowSize', 'setCursor', 'textColored'])
        self.assertTrue(self.hud.draw(self.model(), None))
        names = [c[0] for c in self.calls]
        self.assertEqual(names[0], 'pushDWriteFont')
        self.assertEqual(names[-1], 'popDWriteFont')
        self.assertEqual(names.count('pushDWriteFont'), names.count('popDWriteFont'))
        texts = [c for c in self.calls if c[0] == 'dwriteText']
        self.assertTrue(texts and all(c[2] == 30 for c in texts))  # 15 * hud_scale
        self.assertIn('PROVISIONAL', texts[0][1])
        self.assertNotIn('textColored', names)
        rect = next(c for c in self.calls if c[0] == 'drawRectFilled')
        self.assertEqual((rect[1].x, rect[1].y), (10, 20))
        self.assertLess(rect[3].m, 1)  # translucent dark background

    def test_fallback_to_text_colored_without_dwrite(self):
        self.ui(['textColored'])
        self.assertTrue(self.hud.draw(self.model(), None))
        names = {c[0] for c in self.calls}
        self.assertEqual(names, {'textColored'})

    def test_missing_ui_or_failing_ui_never_raises(self):
        self.lua.globals().ui = None
        self.assertFalse(self.hud.draw(self.model(), None))
        def boom(*a): raise RuntimeError('ui broken')
        self.lua.globals().ui = self.lua.table_from({'pushDWriteFont': self.wrap(lambda *a: None),
                                                    'popDWriteFont': self.wrap(lambda *a: self.calls.append(('pop',))),
                                                    'dwriteText': self.wrap(boom)})
        self.assertFalse(self.hud.draw(self.model(), None))
        self.assertEqual(self.calls, [('pop',)])  # font stack restored even on failure

    def test_model_is_pure(self):
        self.lua.globals().ui = None
        self.lua.globals().ac = None
        self.lua.globals().io = None
        m = self.hud.model(self.lua.table_from({'phase': 'timed', 'event': 'endurance'}, recursive=True), None)
        self.assertGreater(len(m.lines), 5)


class LuaJITSmoke(unittest.TestCase):
    """CSP runs LuaJIT 2.1 (Lua 5.1 semantics); the default lupa runtime may be 5.4."""
    def test_model_and_draw_under_luajit(self):
        try:
            from lupa import luajit21
        except ImportError:
            self.skipTest('lupa.luajit21 unavailable')
        lua = luajit21.LuaRuntime(unpack_returned_tuples=True)
        lua.globals().package.path = str(ROOT / '?.lua') + ';' + lua.globals().package.path
        hud = lua.execute((ROOT / 'hud.lua').read_text())
        state = lua.table_from({'phase': 'timed', 'event': 'endurance', 'assignment': {'pilot_id': 'p1'},
                                'lap': {'elapsed_ms': 68500, 'penalties': {'cone': 3, 'off_course': 2, 'slalom': ['S1', 'S4']}},
                                'sync': {'online': True, 'pending': 0, 'persistence': 'ok'}}, recursive=True)
        m = hud.model(state, None)
        texts = [m.lines[i].text for i in range(1, len(m.lines) + 1)]
        self.assertIn('Proyectado PROVISIONAL: 2:34.500', texts)
        drawn = lua.execute('''
          local hud, model = ...
          local n = 0
          vec2 = function(x, y) return {x=x, y=y} end
          rgbm = function(r, g, b, a) return {r, g, b, a} end
          ui = {pushDWriteFont=function() end, popDWriteFont=function() end,
            dwriteText=function() n = n + 1 end, drawRectFilled=function() end, setCursor=function() end}
          return hud.draw(model, nil), n''', hud, m)
        self.assertIsNone(m.banner)  # assigned, timed, online, persistence ok: nothing to raise
        self.assertEqual(drawn, (True, len(m.lines) + 1))  # title + lines


if __name__ == '__main__': unittest.main()
