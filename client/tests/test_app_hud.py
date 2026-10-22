"""RiceTrials.lua wiring of hud.lua and diagnostics.lua (CSP API doubles, not a CSP runtime test)."""
import json
import unittest

from test_network import harness

STATION = ("package.preload['station_config'] = function() return {server_url='http://127.0.0.1:8080', "
           "station_id='ST01', station_token='local-only'%s} end")


def capture_ui(lua):
    lua.execute("""
      UI_LINES = {}
      ui = {text=function(s) UI_LINES[#UI_LINES+1] = tostring(s) end,
            textColored=function(s) UI_LINES[#UI_LINES+1] = tostring(s) end}
    """)


def lines(lua):
    t = lua.globals().UI_LINES
    return [t[i] for i in range(1, len(t) + 1)]


class AppHud(unittest.TestCase):
    def test_window_draws_hud_model_with_assignment_and_sync(self):
        lua, storage, _, requests, reply, _ = harness()
        lua.execute(STATION % '')
        capture_ui(lua)
        script = lua.globals().script
        script.update(.1)
        reply(0, {'station_id': 'ST01', 'session_id': 's1', 'pilot_id': 'p7', 'pilot_name': 'Ana',
                  'assignment_revision': 1})
        script.windowMain(.3)
        text = '\n'.join(lines(lua))
        self.assertIn('Ana', text)
        self.assertNotIn('Driver: none assigned', text)
        self.assertNotIn('Rice Driver Trials: NOT VALIDATED', text)  # HUD, not the plain fallback

    def test_window_falls_back_to_plain_text_when_hud_breaks(self):
        lua, _, _, _, _, _ = harness(pre_exec=lambda l: l.execute(
            "package.preload['hud'] = function() error('broken hud') end"))
        capture_ui(lua)
        lua.globals().script.windowMain(.3)
        text = '\n'.join(lines(lua))
        self.assertIn('Rice Driver Trials: NOT VALIDATED on CSP/Windows', text)
        self.assertIn('Queue persistence:', text)

    def test_diagnostics_off_by_default_writes_nothing(self):
        lua, storage, _, _, _, _ = harness()
        lua.execute(STATION % '')
        for _ in range(200): lua.globals().script.update(.1)
        self.assertFalse([k for k in storage if 'diagnostics' in k])

    def test_diagnostics_enabled_writes_bounded_jsonl(self):
        lua, storage, _, _, _, _ = harness()
        lua.execute(STATION % ', diagnostics=true, diagnostics_flush_s=1')
        for _ in range(40): lua.globals().script.update(.1)
        files = [k for k in storage if 'diagnostics' in k and k.endswith('.jsonl')]
        self.assertTrue(files, sorted(storage))
        first = json.loads(storage[files[0]].splitlines()[0])
        self.assertEqual(first['kind'], 'session_start')


if __name__ == '__main__':
    unittest.main()
