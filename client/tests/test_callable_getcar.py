"""CSP 0.2.11 exposes ac.getCar as a callable that is NOT a plain Lua function.

acc-lua-sdk common/ac_state.lua defines it as
    ac.getCar = setmetatable({ordered=..., leaderboard=..., serverSlot=...}, {__call=function(_, index) ... end})
so type(ac.getCar) == 'table'. Observed in game (rice-win11, CSP 0.2.11 build 3465): the route
detector (which calls ac.getCar(0) directly) saw cones, while the V2 native channel, the V1 native
backup and the diagnostics samples never saw a car (diagnostics logged car_missing, no sample).
Every getCar consumer must accept any callable."""
import json
import unittest
from test_app_field_v2 import app
from test_app_hud import STATION
from test_network import harness

# Same shape as the SDK: a table with helper methods and a __call metamethod.
CSP_SHAPE = """
  local real=ac.getCar
  ac.getCar=setmetatable({
    ordered=function(i) return real(i) end,
    leaderboard=function(i) return real(i) end,
    serverSlot=function(i) return real(i) end,
  }, {__call=function(_, i) return real(tonumber(i) or 0) end})
  assert(type(ac.getCar)=='table')
"""


class CallableGetCar(unittest.TestCase):
    def test_native_lap_is_posted_and_backed_up_when_getcar_is_csp_callable_table(self):
        lua, disk, _, requests, _, _ = app()
        lua.execute(CSP_SHAPE)
        lua.globals().script.update(.1)
        lua.execute('CAR.lapCount=1')
        lua.globals().script.update(.1)
        native = [r for r in requests if r[1].endswith('/api/v2/native-laps')]
        self.assertEqual(len(native), 1)
        body = json.loads(native[0][3])
        self.assertEqual(body['raw_ms'], 60000)
        self.assertIs(body['native_valid'], True)
        self.assertTrue(any('native_laps.' in k for k in disk), 'V1 native backup journal')
        self.assertTrue(any('field_native_v2' in k for k in disk), 'V2 native journal')

    def test_diagnostics_sample_the_car_when_getcar_is_csp_callable_table(self):
        def pre(lua):
            lua.execute(STATION % ', diagnostics=true, diagnostics_flush_s=1')
            lua.execute("""
              CAR={lapCount=0, previousLapTimeMs=0, position={x=1.5,y=0,z=2}, look={x=0,y=0,z=1}}
              ac.getCar=function() return CAR end
            """)
            lua.execute(CSP_SHAPE)
        lua, storage, _, _, _, _ = harness(None, pre)
        for _ in range(30):
            lua.globals().script.update(.1)
        files = [k for k in storage if 'diagnostics' in k and k.endswith('.jsonl')]
        self.assertTrue(files, sorted(storage))
        kinds = [json.loads(l)['kind'] for f in files for l in storage[f].splitlines() if l.strip()]
        self.assertIn('sample', kinds)
        self.assertNotIn('car_missing', kinds)

    def test_getcar_raising_or_absent_never_breaks_update(self):
        lua, disk, _, requests, _, _ = app()
        lua.execute("ac.getCar=setmetatable({}, {__call=function() error('cdata gone') end})")
        lua.globals().script.update(.1)
        lua.execute('ac.getCar=nil')
        lua.globals().script.update(.1)
        self.assertFalse([r for r in requests if r[1].endswith('/api/v2/native-laps')])


class LuaJitCompiles(unittest.TestCase):
    """CSP runs LuaJIT 2.1 (max 60 upvalues per function); lupa's default Lua 5.4 does not catch it."""
    def test_every_client_module_compiles_under_luajit(self):
        try:
            from lupa import luajit21
        except ImportError:
            self.skipTest('lupa.luajit21 not available')
        from pathlib import Path
        lua = luajit21.LuaRuntime()
        compile_ = lua.eval('function(src, name) local f, err = loadstring(src, name); return err end')
        for path in sorted(Path(__file__).resolve().parents[1].glob('*.lua')):
            self.assertIsNone(compile_(path.read_text(encoding='utf-8'), '@' + path.name), path.name)


if __name__ == '__main__':
    unittest.main()
