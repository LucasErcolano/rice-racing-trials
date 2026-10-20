"""End-to-end scenarios: UNMODIFIED production client (LuaJIT 2.1) + production server (real HTTP)
inside a MOCK CSP/AC environment (sim/cspmock.py). Passing here is NOT CSP validation.

Run: python -m unittest discover -s sim -p 'test_*.py' -v
Optional real-geometry run: RICE_FSAEJ_GEOMETRY=<FSAEJ_Endurance.geometry.json> (git-ignored, derived
from proprietary assets) adds the FSAEJ AI-line scenarios.
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cspmock as M  # noqa: E402
import harness as H  # noqa: E402

FRAME_MS = 1000 / 60


class Base(unittest.TestCase):
    def make(self, **kw):
        h = H.Harness(**kw)
        self.addCleanup(h.close)
        return h

    def assertNoLuaErrors(self, h):
        self.assertEqual(h.errors, [], 'Lua errors raised inside update/windowMain')


class CleanDriving(Base):
    def test_laps_reach_server_with_accurate_time(self):
        h = self.make()
        h.laps(4).settle()
        laps = h.server_laps()
        truth = h.driver.truth_laps_ms()
        # outlap is never timed: 4 start crossings after the spawn -> truth has >= 3 laps
        self.assertGreaterEqual(len(laps), 2)
        for i, lap in enumerate(laps):
            self.assertEqual(lap['status'], 'PENDING_REVIEW')
            self.assertEqual(lap['pilot_id'], 'P1')
            self.assertEqual(lap['lap_number'], i + 1)
            # lap i+1 timed by the client corresponds to truth lap i+1 (truth lap 0 is the outlap)
            self.assertLess(abs(lap['raw_ms'] - truth[i + 1]), 3, f'lap {i}: {lap["raw_ms"]} vs {truth[i + 1]:.1f}')
        self.assertEqual(h.status(), 'PENDING_REVIEW / SERVER_ACKNOWLEDGED (NOT SCORED)')
        self.assertNoLuaErrors(h)

    def test_frame_rate_jitter_and_low_fps(self):
        for fps, jitter in ((30, 0.4), (144, 0.2)):
            with self.subTest(fps=fps):
                h = self.make(fps=fps, seed=fps)
                h.laps(3, jitter=jitter).settle()
                laps, truth = h.server_laps(), h.driver.truth_laps_ms()
                self.assertGreaterEqual(len(laps), 1)
                for i, lap in enumerate(laps):
                    self.assertLess(abs(lap['raw_ms'] - truth[i + 1]), 3)
                self.assertNoLuaErrors(h)


class GameStates(Base):
    def test_replay_never_produces_laps(self):
        h = self.make()
        h.laps(3).settle()
        before = len(h.server_laps())
        h.run(2.2 * h.lap_time_s(), sim={'isReplayActive': True})
        h.settle()
        self.assertEqual(len(h.server_laps()), before, 'laps recorded while a replay was playing')
        self.assertNoLuaErrors(h)

    def test_main_menu_never_produces_laps(self):
        h = self.make()
        h.run(2.2 * h.lap_time_s(), sim={'isInMainMenu': True})
        h.settle()
        self.assertEqual(h.server_laps(), [])

    def test_pause_with_dt_ticking_does_not_inflate_lap(self):
        """If CSP keeps calling update with dt > 0 while paused, the lap must not count the pause."""
        h = self.make()
        h.laps(1.5)
        h.run(20, move=False, sim={'isPaused': True})
        h.laps(3).settle()
        truth = h.driver.truth_laps_ms()
        for lap in h.server_laps():
            self.assertLess(lap['raw_ms'], max(truth) + 20, 'pause time leaked into a lap')

    def test_pause_with_zero_dt_invalidates_current_lap(self):
        h = self.make()
        h.laps(1.6)
        for _ in range(60):
            h.rt.frame(0.0, h.driver.car_state(), sim={'isPaused': True})
        self.assertIn('OUTLAP_RESET', h.status())


class Resets(Base):
    def test_reset_counter_teleport_restarts_outlap(self):
        h = self.make()
        h.laps(1.5)
        h.driver.teleport(h._start_s() - 15.0)  # "back to pits": resetCounter += 1
        h.run(0.2)
        self.assertIn('RESET_COUNTER', h.status())
        h.laps(3).settle()
        for lap in h.server_laps():
            self.assertGreater(lap['raw_ms'], 0.9 * h.lap_time_s() * 1000)

    def test_teleport_without_reset_counter_field(self):
        """Older CSP struct without resetCounter: reading it raises on FFI cdata."""
        h = self.make(car_fields=M.CAR_FIELDS_LEGACY)
        h.laps(1.5)
        h.driver.teleport(h._start_s() + 3.0, count_reset=False)  # jump across the start line
        h.laps(2.5).settle()
        truth = h.driver.truth_laps_ms()
        for lap in h.server_laps():
            self.assertGreater(lap['raw_ms'], 0.9 * h.lap_time_s() * 1000, 'teleport produced a short lap')
        self.assertNoLuaErrors(h)

    def test_car_jumped_event_resets(self):
        h = self.make()
        h.laps(1.5)
        self.assertEqual(h.rt.car_jumped(), 1)
        self.assertIn('CAR_JUMPED', h.status())

    def test_session_restart_refreshes_assignment(self):
        h = self.make()
        h.laps(1.5)
        h.assign('P2')
        self.assertEqual(h.rt.session_start(0, True), 1)
        h.laps(3).settle()
        pilots = {lap['pilot_id'] for lap in h.server_laps()}
        self.assertEqual(pilots, {'P2'})

    def test_reverse_driving_never_times(self):
        h = self.make()
        h.driver.reverse = True
        h.laps(3).settle()
        self.assertEqual(h.server_laps(), [])

    def test_shortcut_skipping_checkpoint_not_timed(self):
        h = self.make(checkpoints=4)
        h.laps(1.2)
        laps_before = len(h.server_laps())
        # jump forward past checkpoint 2 without a reset event (worst case: track cut)
        h.driver.teleport(h.driver.s + 0.4 * h.track.length, count_reset=False)
        h.laps(0.7).settle()
        self.assertEqual(len(h.server_laps()), laps_before)


class Network(Base):
    def test_server_down_then_recovers_without_loss_or_duplicates(self):
        h = self.make()
        h.go_offline_after_assignment()
        h.laps(4)
        self.assertEqual(h.server_laps(), [])
        hud = '\n'.join(h.hud())
        self.assertIn('Servidor: OFFLINE', hud)
        h.net.down = False
        h.run(31).settle(10)  # heartbeat/assignment refresh after 30 s, retries every 5 s
        laps = h.server_laps()
        self.assertGreaterEqual(len(laps), 2)
        self.assertEqual(len({l['lap_id'] for l in laps}), len(laps))
        self.assertIn('Cola pendiente: 0', '\n'.join(h.hud()))

    def test_lost_ack_is_retried_as_identical_duplicate(self):
        h = self.make()
        h.net.inject('drop_reply', '/api/laps', count=1)
        h.laps(3).settle(12)
        laps = h.server_laps()
        self.assertGreaterEqual(len(laps), 1)
        self.assertEqual(len({l['lap_id'] for l in laps}), len(laps))
        self.assertIn('Cola pendiente: 0', '\n'.join(h.hud()))

    def test_http500_and_captive_portal_are_not_acks(self):
        h = self.make()
        h.net.inject('http500', '/api/laps', count=2)
        h.net.inject('garbage', '/api/laps', count=2)
        h.laps(3).settle(30)
        self.assertGreaterEqual(len(h.server_laps()), 1)
        self.assertIn('Cola pendiente: 0', '\n'.join(h.hud()))

    def test_requests_in_flight_are_bounded(self):
        """The client keeps at most one request per channel (assignment, heartbeat, lap) in
        flight, even with slow replies and a backlog. CSP's own concurrency limit is unknown."""
        h = self.make(latency_frames=120)
        h.go_offline_after_assignment()
        h.laps(4)
        h.net.down = False
        h.run(31).settle(15)
        self.assertLessEqual(h.net.max_in_flight, 3)
        self.assertEqual(len({l['lap_id'] for l in h.server_laps()}), len(h.server_laps()))

    def test_wrong_token_is_rejected_and_nothing_scored(self):
        h = self.make()
        M.write_station_config(h.disk, 'ST01', h.server.url, 'MOCK-wrong-token', diagnostics=True)
        h.launch()
        h.laps(3).settle()
        self.assertEqual(h.server_laps(), [])
        self.assertIn('ASSIGNMENT_UNAVAILABLE', ' '.join(h.hud()) + ' ' + str(h.status()))


class Persistence(Base):
    def test_game_restart_with_offline_queue_delivers_after_restart(self):
        h = self.make()
        h.go_offline_after_assignment()
        h.laps(4)
        pending_files = h.journal_files()
        self.assertIn('queue.a.json', pending_files)
        h.launch()  # game closed and reopened, network back
        h.net.down = False
        h.driver.teleport(h._start_s() - 15.0)
        h.run(2).settle(10)
        laps = h.server_laps()
        self.assertGreaterEqual(len(laps), 2)
        self.assertEqual(sorted(l['lap_number'] for l in laps), list(range(1, len(laps) + 1)))

    def test_torn_write_never_loses_acknowledged_state(self):
        h = self.make()
        h.go_offline_after_assignment()
        h.laps(3)
        h.disk.inject('save', 'torn', 'queue', count=1)
        h.laps(1.2)
        h.launch()
        h.net.down = False
        h.run(1).settle(10)
        status = h.status()
        self.assertNotIn('CORRUPT', status)
        self.assertGreaterEqual(len(h.server_laps()), 1)

    def test_disk_full_degrades_and_says_so(self):
        h = self.make()
        h.disk.inject('save', 'enospc', 'queue', count=-1)
        h.laps(3).settle()
        hud = '\n'.join(h.hud())
        self.assertRegex(hud, 'Persistencia (DEGRADADA|NO DISPONIBLE)')

    def test_corrupted_journal_blocks_with_manual_recovery(self):
        h = self.make()
        h.go_offline_after_assignment()
        h.laps(3)
        for name in ('queue.a.json', 'queue.b.json'):
            p = h.disk.ac_root / 'apps/lua/RiceTrials/state' / name
            if p.exists():
                p.write_text(p.read_text()[:-20] + 'X' * 20)
        h.launch()
        self.assertIn('QUEUE_CORRUPT', h.status())


class Compatibility(Base):
    def test_missing_optional_apis_degrade_without_errors(self):
        h = self.make(hidden_apis=('ac.onCarJumped', 'ac.onSessionStart', 'ui.pushDWriteFont',
                                   'ui.dwriteText', 'io.move'))
        h.laps(3).settle()
        self.assertGreaterEqual(len(h.server_laps()), 1)
        self.assertNoLuaErrors(h)

    def test_no_io_falls_back_to_storage_and_reports_degraded(self):
        h = self.make(hidden_apis=('io.save', 'io.load', 'io.createDir', 'io.fileExists', 'io.deleteFile'))
        h.laps(3).settle()
        self.assertIn('Persistencia DEGRADADA', '\n'.join(h.hud()))
        self.assertGreaterEqual(len(h.server_laps()), 1)

    def test_diagnostics_file_is_valid_jsonl(self):
        h = self.make()
        h.laps(2).run(11)
        path = h.disk.ac_root / 'apps/lua/RiceTrials/diagnostics/rice_diagnostics.jsonl'
        lines = path.read_text(encoding='utf-8').splitlines()
        self.assertGreater(len(lines), 10)
        kinds = {json.loads(l)['kind'] for l in lines}
        self.assertTrue({'sample', 'connection'} <= kinds, kinds)


@unittest.skipUnless(os.environ.get(H.FSAEJ_GEOMETRY_ENV), 'set RICE_FSAEJ_GEOMETRY for real-track scenarios')
class FsaejGeometry(Base):
    def test_fsaej_ai_line_laps(self):
        geo = H.load_geometry('fsaej')
        h = self.make(geometry=geo, checkpoints=4)
        h.laps(4).settle()
        laps, truth = h.server_laps(), h.driver.truth_laps_ms()
        self.assertGreaterEqual(len(laps), 2)
        for i, lap in enumerate(laps):
            self.assertLess(abs(lap['raw_ms'] - truth[i + 1]), 3)
        self.assertNoLuaErrors(h)


if __name__ == '__main__':
    unittest.main()
