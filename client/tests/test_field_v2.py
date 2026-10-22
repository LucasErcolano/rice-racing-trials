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
