"""Reset/teleport/session-restart handling with CSP API doubles (not a CSP runtime test)."""
import json
import unittest

from test_network import config, harness


def install_events(lua):
    # Lua closures, so the client's type(fn) == 'function' guards see real functions.
    lua.execute("""
      RT_EVENTS = {}
      ac.onCarJumped = function(index, cb) RT_EVENTS.jump_index = index; RT_EVENTS.jump = cb end
      ac.onSessionStart = function(cb) RT_EVENTS.session = cb end
    """)


def setup(with_events=True):
    lua, storage, position, requests, reply, to_lua = harness(pre_exec=install_events if with_events else None)
    script = lua.globals().script
    script.connect('http://127.0.0.1:8080', 'station-1', 'secret')
    script.configure(config(to_lua))
    reply(0, {'station_id': 'station-1', 'session_id': 's1', 'pilot_id': 'p1', 'assignment_revision': 1})
    return lua, storage, position, requests, reply, script


def move(lua, position, z, dt=.1):
    position['z'] = z
    lua.globals().script.update(dt)


def laps(requests):
    return [json.loads(r[3]) for r in requests if r[1].endswith('/api/laps')]


def outlap(lua, position):
    for z in (1, -1, -4, -6, 1, -1):  # start, checkpoint, start again: outlap complete
        move(lua, position, z)


def timed_lap(lua, position):
    for z in (-4, -6, 1, -1):
        move(lua, position, z)


class Resets(unittest.TestCase):
    def test_spawn_before_line_outlap_is_never_a_lap(self):
        # AC_START_0 sits ~19.3 m before the timing line: the first ~18 s to the
        # line is an outlap, and the first full loop after it is still not timed.
        lua, _, position, requests, _, _ = setup()
        move(lua, position, 19.3)
        for _ in range(180):  # 18 s of driving toward the line
            move(lua, position, position['z'] - 0.1)
        move(lua, position, -1)
        self.assertEqual(laps(requests), [])
        for z in (-4, -6, 1, -1):
            move(lua, position, z)
        self.assertEqual(laps(requests), [], 'outlap completion must not emit a lap')
        timed_lap(lua, position)
        self.assertEqual(len(laps(requests)), 1)

    def test_payload_carries_lap_number_rules_and_client_timestamp(self):
        lua, _, position, requests, _, _ = setup()
        outlap(lua, position)
        timed_lap(lua, position)
        timed_lap(lua, position)
        sent = laps(requests)
        self.assertEqual(sent[0]['lap_number'], 1)
        self.assertEqual(sent[0]['rules_version'], 1)
        self.assertIsInstance(sent[0]['client_timestamp'], int)
        self.assertEqual(sent[0]['assignment_revision'], 1)
        self.assertEqual(sent[0]['status'], 'PENDING_REVIEW')
        self.assertIsNone(sent[0].get('calibration_version'))

    def test_car_jump_event_mid_lap_discards_lap(self):
        lua, _, position, requests, _, _ = setup()
        outlap(lua, position)
        move(lua, position, -4)
        lua.execute('RT_EVENTS.jump(0)')  # small in-place reset: no position jump
        for z in (-6, 1, -1):
            move(lua, position, z)
        self.assertEqual(laps(requests), [])
        self.assertEqual(lua.eval('RT_EVENTS.jump_index'), 0)

    def test_reset_counter_change_discards_lap(self):
        lua, _, position, requests, _, _ = setup(with_events=False)
        lua.execute("""
          RT_RESETS = 0
          local base = ac.getCar
          ac.getCar = function(i) local c = base(i); c.resetCounter = RT_RESETS; return c end
        """)
        outlap(lua, position)
        move(lua, position, -4)
        lua.execute('RT_RESETS = 1')
        for z in (-6, 1, -1):
            move(lua, position, z)
        self.assertEqual(laps(requests), [])
        timed_lap(lua, position)  # not enough: a reset puts the car back in outlap
        self.assertEqual(laps(requests), [])

    def test_session_restart_resets_route_and_refetches_assignment(self):
        lua, _, position, requests, _, _ = setup()
        outlap(lua, position)
        move(lua, position, -4)
        before = sum(1 for r in requests if r[1].endswith('/api/station/assignment'))
        lua.execute('RT_EVENTS.session(0, true)')
        move(lua, position, -6)
        after = sum(1 for r in requests if r[1].endswith('/api/station/assignment'))
        self.assertEqual(after, before + 1)
        for z in (1, -1):
            move(lua, position, z)
        self.assertEqual(laps(requests), [])

    def test_missing_event_apis_still_score_without_crash(self):
        lua, _, position, requests, _, _ = setup(with_events=False)
        outlap(lua, position)
        timed_lap(lua, position)
        self.assertEqual(len(laps(requests)), 1)

    def test_pit_reentry_teleport_does_not_finish(self):
        lua, _, position, requests, _, _ = setup()
        outlap(lua, position)
        move(lua, position, -4)
        move(lua, position, -300)  # back to pit: impossible displacement
        for z in (-6, 1, -1):
            move(lua, position, z)
        self.assertEqual(laps(requests), [])


if __name__ == '__main__':
    unittest.main()
