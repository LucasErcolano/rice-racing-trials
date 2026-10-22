"""Real Lua producer + real journal tests (CSP API doubles, not game validation)."""
import json
import unittest
from test_network import harness


def runtime(disk=None):
    lua, disk, _, _, _, to_lua = harness(disk)
    lua.execute("""
      local jm = require('journal')
      function fieldJournal(name)
        return jm.new({dir='C:/AC/apps/lua/RiceTrials/state', name=name}, {
          io=io, json={encode=function(v) return JSON.stringify(v) end,
          decode=function(v) return JSON.parse(v) end}, legacy_key='unused-'..name})
      end
      FIELD_CONTEXT={station_id='ST01', session_id='S1', pilot_id='P1', track_config='FSAEJ_Endurance'}
      FIELD_POSTS={}
      FIELD=require('field_v2').new({journal=fieldJournal, nonce='boot',
        context=function() return FIELD_CONTEXT end,
        encode=function(v) return JSON.stringify(v) end,
        decode=function(v) return JSON.parse(v) end,
        post=function(channel, payload, cb)
          FIELD_POSTS[#FIELD_POSTS+1]={channel=channel,payload=payload,cb=cb}
        end,
        native_evidence=function(record)
          if record.raw_ms==217853 and record.lap_count==2 then return true end
        end})
    """)
    return lua, disk, lua.globals().FIELD, to_lua


def posts(lua, channel=None):
    out = []
    for i in range(1, len(lua.globals().FIELD_POSTS) + 1):
        p = lua.globals().FIELD_POSTS[i]
        if channel is None or p.channel == channel:
            out.append((json.loads(p.payload), p))
    return out


class FieldProducer(unittest.TestCase):
    def test_old_captured_contextless_native_draft_migrates_to_unassigned(self):
        lua, disk, _, cv = runtime()
        lua.execute("""
          local j=fieldJournal('field_native_v2')
          j:save({{version=1,nonce='boot',seq=2,epoch=1,ref=2,queue={},records={{
            id='boot-n-2',lap_count=2,epoch=1,raw_ms=217853,capture_state='captured'}}}})
        """)
        lua2, _, field, _ = runtime(disk)
        field.pump(field)
        payload = posts(lua2, 'native')[0][0]
        self.assertEqual(payload['lap_id'], 'boot-n-2')
        self.assertEqual(payload['raw_ms'], 217853)
        self.assertEqual(payload['session_id'], 'UNASSIGNED')
        self.assertEqual(payload['pilot_id'], 'UNASSIGNED')
        self.assertEqual(payload['assignment_state'], 'UNASSIGNED')
        self.assertTrue(payload['native_valid'])
        self.assertNotIn('native_valid_available', payload)

    def test_native_capture_without_assignment_is_queued_as_unassigned(self):
        lua, disk, o, cv = runtime()
        lua.execute("FIELD_CONTEXT.session_id=nil; FIELD_CONTEXT.pilot_id=nil")
        o.observe(o, cv({'lapCount': 0}), .1)
        o.observe(o, cv({'lapCount': 1, 'previousLapTimeMs': 217853, 'isLastLapValid': True}), .1)
        o.pump(o)
        payload = posts(lua, 'native')[0][0]
        self.assertEqual(payload['session_id'], 'UNASSIGNED')
        self.assertEqual(payload['pilot_id'], 'UNASSIGNED')
        self.assertEqual(payload['raw_ms'], 217853)
        self.assertEqual(payload['assignment_state'], 'UNASSIGNED')
        self.assertTrue(payload['native_valid'])

    def test_native_capture_is_durable_and_retry_bytes_survive_restart(self):
        lua, disk, o, cv = runtime()
        o.observe(o, cv({'lapCount': 0}), .1)
        o.observe(o, cv({'lapCount': 1, 'previousLapTimeMs': 61000, 'isLastLapValid': False}), .1)
        o.pump(o)
        payload, sent = posts(lua, 'native')[0]
        self.assertEqual(payload['protocol'], 'rice-lap-or-v2')
        self.assertEqual(payload['raw_ms'], 61000)
        self.assertIs(payload['native_valid'], False)
        self.assertNotIn('events', payload)
        lua2, _, o2, _ = runtime(disk)
        o2.pump(o2)
        self.assertEqual(posts(lua2, 'native')[0][1].payload, sent.payload)
        self.assertEqual(o2.stats(o2).native_pending, 1)

    def test_exact_counter_identity_reconciles_both_orders_across_restart(self):
        for native_first in (True, False):
            with self.subTest(native_first=native_first):
                lua, disk, o, cv = runtime()
                o.observe(o, cv({'lapCount': 3}), .1)
                token = o.begin_lap(o)
                snapshot = cv({'events': [], 'settings': {}, 'evidence': {'source': 'rice-client-v2'}})
                if native_first:
                    o.observe(o, cv({'lapCount': 4, 'previousLapTimeMs': 60000}), .1)
                self.assertTrue(o.finish_lap(o, token, 59999, snapshot))
                lua2, _, o2, cv2 = runtime(disk)
                if not native_first:
                    o2.observe(o2, cv2({'lapCount': 4, 'previousLapTimeMs': 60000}), .1)
                o2.pump(o2)
                native = posts(lua2, 'native')[0][0]
                penalty = posts(lua2, 'penalty')[0][0]
                self.assertEqual(native['lap_id'], penalty['lap_id'])
                self.assertEqual(penalty['raw_ms'], 60000)
                self.assertEqual(penalty['evidence']['route_raw_ms'], 59999)
                self.assertNotEqual(native['message_id'], penalty['message_id'])

    def test_reset_token_never_pairs_with_similar_native_time(self):
        lua, _, o, cv = runtime()
        o.observe(o, cv({'lapCount': 3}), .1)
        token = o.begin_lap(o)
        o.observe(o, cv({'lapCount': 0}), .1)
        o.observe(o, cv({'lapCount': 1, 'previousLapTimeMs': 60000}), .1)
        self.assertTrue(o.finish_lap(o, token, 60000, cv({'events': [], 'settings': {}, 'evidence': {}})))
        o.pump(o)
        native = posts(lua, 'native')[0][0]
        penalty = posts(lua, 'penalty')[0][0]
        self.assertNotEqual(native['lap_id'], penalty['lap_id'])
        self.assertIn('identity_ambiguous', penalty['evidence']['review_reasons'])

    def test_delivery_ack_requires_exact_channel_identity_and_is_independent(self):
        lua, disk, o, cv = runtime()
        o.observe(o, cv({'lapCount': 0}), .1)
        token = o.begin_lap(o)
        o.observe(o, cv({'lapCount': 1, 'previousLapTimeMs': 61000}), .1)
        o.finish_lap(o, token, 60999, cv({'events': [], 'settings': {}, 'evidence': {}}))
        o.pump(o)
        native, np = posts(lua, 'native')[0]
        penalty, pp = posts(lua, 'penalty')[0]
        np.cb(cv({'message_id': native['message_id']}))  # not a receipt
        self.assertEqual(o.stats(o).native_pending, 1)
        pp.cb(cv({'penalty_received': True, 'penalty_payload': penalty}))
        self.assertEqual(o.stats(o).penalty_pending, 0)
        lua2, _, o2, _ = runtime(disk)
        o2.pump(o2)
        self.assertEqual(len(posts(lua2, 'native')), 1)
        self.assertEqual(posts(lua2, 'penalty'), [])

    def test_penalty_only_without_native_counter_is_durable_manual_review(self):
        lua, disk, o, cv = runtime()
        token = o.begin_lap(o)
        self.assertIsInstance(token, str)
        self.assertTrue(o.finish_lap(o, token, 60000, cv({'events': [], 'settings': {}, 'evidence': {}})))
        lua2, _, o2, _ = runtime(disk)
        o2.pump(o2)
        payload = posts(lua2, 'penalty')[0][0]
        self.assertEqual(payload['raw_ms'], 60000)
        self.assertFalse(payload['evidence']['detector_ok'])
        self.assertIn('identity_ambiguous', payload['evidence']['review_reasons'])
        self.assertEqual(posts(lua2, 'native'), [])

    def test_stalled_transport_times_out_and_retries_identical_without_frame_spam(self):
        lua, _, o, cv=runtime()
        o.observe(o,cv({'lapCount':0}),.1)
        o.observe(o,cv({'lapCount':1,'previousLapTimeMs':60000}),.1)
        o.pump(o)
        for _ in range(10): o.observe(o,cv({'lapCount':1}),.1); o.pump(o)
        self.assertEqual(len(posts(lua,'native')),1)
        for _ in range(160): o.observe(o,cv({'lapCount':1}),.1); o.pump(o)
        self.assertEqual(len(posts(lua,'native')),2)
        self.assertEqual(posts(lua,'native')[0][1].payload,posts(lua,'native')[1][1].payload)
        old=posts(lua,'native')[0]
        old[1].cb(cv({'native_received':True,'native_payload':old[0]}))
        self.assertEqual(o.stats(o).native_pending,1)  # stale callback cannot dequeue newer attempt

    def test_missing_native_finish_releases_penalty_for_manual_review(self):
        lua, _, o, cv=runtime()
        o.observe(o,cv({'lapCount':0}),.1)
        token=o.begin_lap(o)
        o.finish_lap(o,token,60000,cv({'events':[],'settings':{},'evidence':{}}))
        for _ in range(60): o.observe(o,cv({'lapCount':0}),.1); o.pump(o)
        body=posts(lua,'penalty')[0][0]
        self.assertNotEqual(body['lap_id'],token)
        self.assertIn('native_capture_missing',body['evidence']['review_reasons'])
        o.observe(o,cv({'lapCount':1,'previousLapTimeMs':60000}),.1)
        o.pump(o)
        self.assertNotEqual(body['lap_id'],posts(lua,'native')[0][0]['lap_id'])

    def test_duplicate_ack_cannot_remove_next_native_message(self):
        lua, _, o, cv=runtime()
        for count in range(3):
            o.observe(o,cv({'lapCount':count,'previousLapTimeMs':60000}),.1)
        o.pump(o)
        body,request=posts(lua,'native')[0]
        receipt=cv({'native_received':True,'native_payload':body})
        request.cb(receipt)
        request.cb(receipt)
        self.assertEqual(o.stats(o).native_pending,1)

    def test_delayed_native_time_retries_same_identity_after_restart(self):
        for invalid in (None, 0, -1, '60000', 60000.5, 3600001):
            with self.subTest(invalid=invalid):
                lua, disk, o, cv = runtime()
                o.observe(o, cv({'lapCount': 0}), .1)
                token = o.begin_lap(o)
                o.observe(o, cv({'lapCount': 1, 'previousLapTimeMs': invalid}), .1)
                o.finish_lap(o, token, 59999, cv({'events': [], 'settings': {}, 'evidence': {}}))
                state = json.loads(o.export(o))
                self.assertEqual(state['native']['records'][0]['id'], token)
                self.assertEqual(state['native']['records'][0].get('capture_state'), 'awaiting_previous_lap_time')
                self.assertEqual(state['native']['queue'], [])
                lua2, _, o2, cv2 = runtime(disk)
                o2.observe(o2, cv2({'lapCount': 1, 'previousLapTimeMs': 60000,
                                    'isLastLapValid': True}), .1)
                o2.pump(o2)
                native = posts(lua2, 'native')
                self.assertEqual(len(native), 1, 'delayed native completion was lost')
                self.assertEqual(native[0][0]['lap_id'], token)
                self.assertEqual(native[0][0]['raw_ms'], 60000)
                self.assertEqual(posts(lua2, 'penalty')[0][0]['lap_id'], token)
                for _ in range(3):
                    o2.observe(o2, cv2({'lapCount': 1, 'previousLapTimeMs': 60000}), .1)
                self.assertEqual(o2.stats(o2).native_pending, 1)

    def test_superseded_missing_time_and_counter_gaps_preserve_evidence(self):
        lua, disk, o, cv = runtime()
        o.observe(o, cv({'lapCount': 0}), .1)
        token = o.begin_lap(o)
        o.observe(o, cv({'lapCount': 1, 'previousLapTimeMs': 0}), .1)
        o.finish_lap(o, token, 59999, cv({'events': [], 'settings': {},
                                         'evidence': {'review_reasons': ['existing_reason']}}))
        # Counter 3's time must never fill counter 1 (or fabricate counter 2).
        o.observe(o, cv({'lapCount': 3, 'previousLapTimeMs': 70000}), .1)
        lua2, _, o2, cv2 = runtime(disk)
        for _ in range(60):
            o2.observe(o2, cv2({'lapCount': 3, 'previousLapTimeMs': 70000}), .1)
        o2.pump(o2)
        state = json.loads(o2.export(o2))
        missing = next(r for r in state['native']['records'] if r['id'] == token)
        self.assertEqual(missing.get('capture_state'), 'native_time_unavailable')
        self.assertEqual(missing['superseded_by_lap_count'], 3)
        self.assertNotIn('raw_ms', missing)
        self.assertEqual(state['native']['gaps'], [{
            'epoch': 0, 'from_lap_count': 2, 'to_lap_count': 2, 'reason': 'native_counter_gap'}])
        native = posts(lua2, 'native')[0][0]
        self.assertEqual(native['raw_ms'], 70000)
        self.assertNotEqual(native['lap_id'], token)
        self.assertEqual(o2.stats(o2).native_pending, 1)
        penalty = posts(lua2, 'penalty')[0][0]
        self.assertNotEqual(penalty['lap_id'], token)
        self.assertEqual(penalty['evidence']['candidate_lap_id'], token)
        self.assertIn('existing_reason', penalty['evidence']['review_reasons'])
        self.assertIn('native_time_unavailable', penalty['evidence']['review_reasons'])
        self.assertEqual(penalty['evidence']['native_capture']['superseded_by_lap_count'], 3)

    def test_pending_native_time_is_closed_at_each_epoch_boundary(self):
        for boundary in ('session_start', 'counter_reset', 'replay'):
            with self.subTest(boundary=boundary):
                lua, disk, o, cv = runtime()
                o.observe(o, cv({'lapCount': 0}), .1)
                o.observe(o, cv({'lapCount': 1, 'previousLapTimeMs': 0}), .1)
                before = json.loads(o.export(o))['native']['records'][0]
                if boundary == 'session_start':
                    o.session_start(o)
                elif boundary == 'counter_reset':
                    o.observe(o, cv({'lapCount': 0, 'previousLapTimeMs': 61000}), .1)
                else:
                    o.observe(o, cv({'lapCount': 90, 'previousLapTimeMs': 61000}), .1, True)
                lua2, _, o2, cv2 = runtime(disk)
                o2.observe(o2, cv2({'lapCount': 0, 'previousLapTimeMs': 62000}), .1)
                o2.observe(o2, cv2({'lapCount': 1, 'previousLapTimeMs': 63000}), .1)
                state = json.loads(o2.export(o2))
                closed = state['native']['records'][0]
                self.assertEqual(closed.get('capture_state'), 'native_capture_interrupted')
                self.assertEqual(closed.get('interruption_reason'), boundary)
                self.assertNotIn('raw_ms', closed)
                self.assertEqual(len(state['native']['queue']), 1)
                self.assertNotEqual(state['native']['records'][-1]['id'], before['id'])
                self.assertEqual(state['native']['records'][-1]['raw_ms'], 63000)

    def test_late_route_finish_keeps_counter_gap_diagnostics(self):
        lua, disk, o, cv = runtime()
        o.observe(o, cv({'lapCount': 0}), .1)
        token = o.begin_lap(o)  # reserved counter 1 is skipped entirely
        o.observe(o, cv({'lapCount': 1000000, 'previousLapTimeMs': 60000}), .1)
        o.finish_lap(o, token, 59999, cv({'events': [], 'settings': {},
                                         'evidence': {'review_reasons': ['existing_reason']}}))
        lua2, _, o2, _ = runtime(disk)
        o2.pump(o2)
        state = json.loads(o2.export(o2))
        self.assertEqual(len(state['native']['records']), 2)  # range, not a million fabricated laps
        self.assertEqual(state['native']['gaps'][0]['to_lap_count'], 999999)
        penalty = posts(lua2, 'penalty')[0][0]
        self.assertIn('identity_ambiguous', penalty['evidence']['review_reasons'])
        self.assertIn('existing_reason', penalty['evidence']['review_reasons'])
        self.assertIn('native_counter_gap', penalty['evidence']['review_reasons'])
        self.assertEqual(penalty['evidence']['native_capture']['lap_id'], token)
        self.assertEqual(penalty['evidence']['native_capture']['state'], 'native_counter_gap')
        self.assertEqual(posts(lua2, 'native')[0][0]['raw_ms'], 60000)
        self.assertNotEqual(posts(lua2, 'native')[0][0]['lap_id'], token)

    def test_native_without_route_reserves_delayed_completion_durably(self):
        lua, disk, o, cv = runtime()
        o.observe(o, cv({'lapCount': 4}), .1)
        # No route begin_lap reservation: native capture owns its identity.
        o.observe(o, cv({'lapCount': 5, 'previousLapTimeMs': 0}), .1)
        reserved = json.loads(o.export(o))['native']['records'][0]['id']
        lua2, _, o2, cv2 = runtime(disk)
        o2.observe(o2, cv2({'lapCount': 5, 'previousLapTimeMs': 61000}), .1)
        o2.pump(o2)
        self.assertEqual(posts(lua2, 'native')[0][0]['lap_id'], reserved)
        self.assertEqual(posts(lua2, 'native')[0][0]['raw_ms'], 61000)
        self.assertEqual(posts(lua2, 'penalty'), [])

    def test_replay_boundary_persists_and_cannot_reuse_live_reservation(self):
        lua, disk, o, cv = runtime()
        o.observe(o, cv({'lapCount': 0}), .1)
        token = o.begin_lap(o)
        o.observe(o, cv({'lapCount': 7, 'previousLapTimeMs': 60000}), .1, True)
        lua2, _, o2, cv2 = runtime(disk)
        o2.observe(o2, cv2({'lapCount': 10, 'previousLapTimeMs': 60000}), .1, True)
        o2.observe(o2, cv2({'lapCount': 20, 'previousLapTimeMs': 60000}), .1, False)
        self.assertEqual(o2.stats(o2).native_pending, 0)
        o2.observe(o2, cv2({'lapCount': 21, 'previousLapTimeMs': 61000}), .1, False)
        o2.finish_lap(o2, token, 61000, cv2({'events': [], 'settings': {}, 'evidence': {}}))
        o2.pump(o2)
        self.assertNotEqual(posts(lua2, 'native')[0][0]['lap_id'], token)
        self.assertNotEqual(posts(lua2, 'native')[0][0]['lap_id'], posts(lua2, 'penalty')[0][0]['lap_id'])
        self.assertIn('identity_ambiguous', posts(lua2, 'penalty')[0][0]['evidence']['review_reasons'])

    def test_explicit_session_restart_invalidates_counter_reservation(self):
        lua, disk, o, cv=runtime()
        o.observe(o,cv({'lapCount':0}),.1)
        token=o.begin_lap(o)
        self.assertIsNotNone(o.session_start)
        o.session_start(o)
        o.observe(o,cv({'lapCount':0}),.1)
        o.observe(o,cv({'lapCount':1,'previousLapTimeMs':60000}),.1)
        o.finish_lap(o,token,60000,cv({'events':[],'settings':{},'evidence':{}}))
        o.pump(o)
        self.assertNotEqual(posts(lua,'native')[0][0]['lap_id'],posts(lua,'penalty')[0][0]['lap_id'])


if __name__ == '__main__':
    unittest.main()
