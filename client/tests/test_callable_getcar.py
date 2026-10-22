"""CSP 0.2.11 exposes ac.getCar as a callable that is NOT a plain Lua function
(type(ac.getCar) ~= 'function'). Observed in game: the route detector (which calls
ac.getCar(0) directly) saw cones, while the native channel, the V1 native backup and the
diagnostics samples never saw a car. Every getCar consumer must accept any callable."""
import json
import unittest
from test_app_field_v2 import app


CALLABLE = """
  local real=ac.getCar
  ac.getCar=setmetatable({}, {__call=function(_, i) return real(i) end})
  assert(type(ac.getCar)=='table')
"""


class CallableGetCar(unittest.TestCase):
    def test_native_lap_is_posted_when_getcar_is_a_callable_table(self):
        lua, disk, _, requests, _, _ = app()
        lua.execute(CALLABLE)
        lua.globals().script.update(.1)
        lua.execute('CAR.lapCount=1')
        lua.globals().script.update(.1)
        native = [r for r in requests if r[1].endswith('/api/v2/native-laps')]
        self.assertEqual(len(native), 1)
        self.assertEqual(json.loads(native[0][3])['raw_ms'], 60000)
        self.assertTrue(any('native_laps.' in k for k in disk))
        self.assertTrue(any('field_native_v2' in k for k in disk))


if __name__ == '__main__':
    unittest.main()
