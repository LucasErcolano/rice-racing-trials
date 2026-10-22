"""Production app opt-in wiring exercised by real Lua, no simulator required."""
import json
import unittest
from test_network import harness, config, drive_lap
from test_app_hud import STATION, capture_ui, lines


def app(disk=None, enabled=True, native_missing=False):
    def pre(lua):
        if native_missing:
            lua.execute("package.preload['native_laps']=function() error('backup unavailable') end")
        lua.execute(STATION % (", protocol_v2=%s, track_config='FSAEJ_Endurance'" % str(enabled).lower()))
        lua.execute("""
          CAR={lapCount=0, previousLapTimeMs=60000, isLastLapValid=true, position={x=0,z=1}}
          ac.getCar=function() return CAR end
          ac.onSessionStart=function(cb) SESSION_START=cb end
        """)
    result=harness(disk, pre)
    lua, _, _, requests, reply, _=result
    lua.globals().script.update(.1)
    reply(0, {'station_id':'ST01','session_id':'S1','pilot_id':'P1'})
    lua.globals().script.update(.1)
    return result


class AppField(unittest.TestCase):
    def test_opt_in_native_posts_without_route_and_preserves_local_backup(self):
        lua, disk, _, requests, _, _=app()
        lua.execute('CAR.lapCount=1')
        lua.globals().script.update(.1)
        native=[r for r in requests if r[1].endswith('/api/v2/native-laps')]
        self.assertEqual(len(native),1)
        self.assertEqual(json.loads(native[0][3])['raw_ms'],60000)
        self.assertFalse(any(r[1].endswith('/api/laps') for r in requests))
        self.assertTrue(any('native_laps.' in k for k in disk))
        capture_ui(lua)
        lua.globals().script.windowPenalties(.1)
        self.assertTrue(any('AC native laps: 1 saved' in t for t in lines(lua)))

    def test_off_keeps_v1_and_does_not_create_v2_state(self):
        lua, disk, position, requests, _, cv=app(enabled=False)
        lua.execute('ac.getCar=function() return {position={x=0,z=POS or 1}} end')
        lua.globals().script.configure(config(cv))
        for z in (1,-1,-4,-6,1,-1,-4,-6,1,-1):
            lua.globals().POS=z
            lua.globals().script.update(.1)
        self.assertTrue(any(r[1].endswith('/api/laps') for r in requests))
        self.assertFalse(any('/api/v2/' in r[1] for r in requests))
        self.assertFalse(any('field_' in k for k in disk))

    def test_route_produces_v2_penalties_preserving_local_times_and_no_v1(self):
        lua, _, _, requests, _, cv=app()
        script=lua.globals().script
        script.configure(config(cv))
        capture_ui(lua)
        for i,z in enumerate((1,-1,-4,-6,1,-1,-4,-6,1,-1)):
            lua.execute('CAR.position.z=%s' % z)
            if i==5: lua.execute('CAR.lapCount=1')
            if i==9: lua.execute('CAR.lapCount=2; CAR.previousLapTimeMs=400')
            script.update(.1)
        penalty=[r for r in requests if r[1].endswith('/api/v2/penalty-laps')]
        self.assertEqual(len(penalty),1)
        body=json.loads(penalty[0][3])
        self.assertEqual(body['raw_ms'],400)
        self.assertEqual(body['events'],[])
        self.assertFalse(body['evidence']['calibration_verified'])
        self.assertFalse(body['evidence']['detector_ok'])
        self.assertEqual(body['settings']['cone_penalty_ms'],2000)
        self.assertIs(body['native_valid'],True)
        self.assertFalse(any(r[1].endswith('/api/laps') for r in requests))
        script.windowTimes(.1)
        self.assertTrue(any(t.startswith('ULTIMA  0:00.') for t in lines(lua)),lines(lua))

    def test_detector_events_settings_and_bounded_telemetry_are_exportable(self):
        from test_network import AutoPenalties
        lua, _, _, requests, _, cv=app()
        script=lua.globals().script
        script.configure(config(cv))
        script.configureCones(AutoPenalties().cfg(cv,ref_line_cones=['REF']))
        lua.execute('CAR.look={x=0,z=-1}')
        for i,z in enumerate((1,-1,-4,-6,1,-1,-4,-6,1,-1)):
            lua.execute('CAR.position.z=%s' % z)
            if i==5: lua.execute('CAR.lapCount=1')
            if i==9: lua.execute('CAR.lapCount=2; CAR.previousLapTimeMs=400')
            script.update(.1)
        body=json.loads(next(r for r in requests if r[1].endswith('/api/v2/penalty-laps'))[3])
        events={e['cone_id']:e for e in body['events'] if e['type']=='cone_contact'}
        self.assertTrue(events['HIT']['penalizable'])
        self.assertFalse(events['REF']['penalizable'])
        self.assertEqual(body['settings']['off_course_penalty_ms'],20000)
        self.assertGreater(len(body['evidence']['telemetry']['samples']),0)
        self.assertLessEqual(len(body['evidence']['telemetry']['samples']),250)
        exported=json.loads(script.exportFieldEvidence())
        self.assertIn(body['lap_id'], json.dumps(exported))
        self.assertNotIn('local-only', json.dumps(exported))

    def test_session_callback_rebaselines_v2_without_inventing_native_lap(self):
        lua, _, _, requests, _, _=app()
        lua.globals().SESSION_START()
        lua.execute('CAR.lapCount=2')
        lua.globals().script.update(.1)
        self.assertFalse(any(r[1].endswith('/api/v2/native-laps') for r in requests))

    def test_session_native_rebaseline_survives_each_throwing_reset(self):
        for fault in ('route', 'cone', 'limits', 'slalom'):
            with self.subTest(fault=fault):
                lua, _, _, requests, _, cv = app()
                script = lua.globals().script
                script.configure(config(cv))
                lua.execute("""
                  RESET_SEEN={}
                  local d=require('detectors')
                  local function machine(name)
                    return {reset=function()
                      RESET_SEEN[name]=(RESET_SEEN[name] or 0)+1
                      if RESET_FAULT==name then error('reset failed: '..name) end
                    end}
                  end
                  d.cone_boxes=function() return machine('cone') end
                  d.track_limits=function() return machine('limits') end
                  d.slalom_runs=function() return machine('slalom') end
                """)
                script.configureCones(cv({}))
                lua.execute("""
                  RESET_SEEN={}
                  local c=require('core'); OLD_ROUTE=c.route_machine
                  c.route_machine=function(...)
                    if RESET_FAULT=='route' then error('route reset failed') end
                    return OLD_ROUTE(...)
                  end
                """)
                lua.globals().RESET_FAULT = fault
                # Real CSP invokes the registered callback independently of update.
                ok, _ = lua.eval('function() local ok, err=pcall(SESSION_START); return ok, tostring(err) end')()
                self.assertTrue(ok, 'session callback leaked a reset exception')
                lua.execute("require('core').route_machine=OLD_ROUTE; RESET_FAULT=nil; CAR.lapCount=2")
                script.update(.1)
                self.assertFalse(any(r[1].endswith('/api/v2/native-laps') for r in requests))
                if fault != 'route':
                    for name in ('cone', 'limits', 'slalom'):
                        self.assertEqual(lua.globals().RESET_SEEN[name], 1)
                self.assertEqual(len([r for r in requests if r[0] == 'GET']), 2)

    def test_replay_is_excluded_without_calibration_and_queues_keep_retrying(self):
        for calibrated in (False, True):
            with self.subTest(calibrated=calibrated):
                lua, _, _, requests, reply, cv = app()
                script = lua.globals().script
                if calibrated:
                    script.configure(config(cv))
                lua.execute('CAR.lapCount=1')
                script.update(.1)
                first = next(i for i, r in enumerate(requests) if r[1].endswith('/api/v2/native-laps'))
                reply(first, {}, err='offline')
                lua.execute('SIM={isReplayActive=true}; ac.getSim=function() return SIM end')
                for count in range(2, 63):
                    lua.execute('CAR.lapCount=%d' % count)
                    script.update(.1)
                native = [r for r in requests if r[1].endswith('/api/v2/native-laps')]
                self.assertEqual(len(native), 2, 'durable queue must retry during replay')
                self.assertEqual(native[0][3], native[1][3])
                state = json.loads(script.exportFieldEvidence())
                self.assertEqual(len(state['native']['records']), 1, 'replay fabricated native records')
                # Exit can jump forward; its first counter is only a baseline.
                lua.execute('SIM.isReplayActive=false; CAR.lapCount=90')
                script.update(.1)
                self.assertEqual(len(json.loads(script.exportFieldEvidence())['native']['records']), 1)
                lua.execute('CAR.lapCount=91; CAR.previousLapTimeMs=62000')
                script.update(.1)
                state = json.loads(script.exportFieldEvidence())
                self.assertEqual(len(state['native']['queue']), 2)
                self.assertEqual(state['native']['records'][-1]['raw_ms'], 62000)

    def test_replay_with_no_car_continues_both_durable_channels(self):
        lua, _, _, requests, reply, cv = app()
        script = lua.globals().script
        script.configure(config(cv))
        for i, z in enumerate((1, -1, -4, -6, 1, -1, -4, -6, 1, -1)):
            lua.execute('CAR.position.z=%s' % z)
            if i == 5:
                lua.execute('CAR.lapCount=1')
            if i == 9:
                lua.execute('CAR.lapCount=2; CAR.previousLapTimeMs=400')
            script.update(.1)
        initial = {}
        for channel, suffix in (('native', 'native-laps'), ('penalty', 'penalty-laps')):
            index = next(i for i, r in enumerate(requests) if r[1].endswith('/api/v2/' + suffix))
            initial[channel] = requests[index][3]
            reply(index, {}, err='offline')
        lua.execute('ac.getCar=function() return nil end; ac.getSim=function() return {isReplayActive=true} end')
        for _ in range(60):
            script.update(.1)
        for channel, suffix in (('native', 'native-laps'), ('penalty', 'penalty-laps')):
            sent = [r[3] for r in requests if r[1].endswith('/api/v2/' + suffix)]
            self.assertEqual(sent, [initial[channel], initial[channel]])

    def test_v2_capture_does_not_depend_on_legacy_backup_module(self):
        lua, _, _, requests, _, _=app(native_missing=True)
        lua.execute('CAR.lapCount=1')
        lua.globals().script.update(.1)
        self.assertEqual(len([r for r in requests if r[1].endswith('/api/v2/native-laps')]),1)


if __name__=='__main__': unittest.main()
