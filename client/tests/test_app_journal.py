"""RiceTrials.lua on top of journal.lua: migration and fail-closed corruption (CSP doubles)."""
import json
import unittest

from test_network import STATE_DIR, config, drive_lap, harness, persisted

LEGACY = 'rice_trials_pending_review_v1'


class AppJournal(unittest.TestCase):
    def queued(self):
        lua, storage, position, requests, reply, to_lua = harness()
        script = lua.globals().script
        script.connect('http://127.0.0.1:8080', 'station-1', 'secret')
        script.configure(config(to_lua))
        reply(0, {'station_id': 'station-1', 'session_id': 's1', 'pilot_id': 'p1', 'assignment_revision': 1})
        drive_lap(lua, position)
        return storage, [r for r in requests if r[1].endswith('/api/laps')]

    def test_legacy_storage_queue_is_migrated_then_removed(self):
        storage, posts = self.queued()
        state = persisted(storage)
        legacy = {k: v for k, v in storage.items() if not k.startswith('file:')}
        legacy[LEGACY] = json.dumps(state)
        lua, after, _, requests, _, _ = harness(legacy)
        self.assertNotIn(LEGACY, after)
        self.assertEqual(persisted(after)['entries'], state['entries'])
        self.assertTrue(lua.globals().script.connect('http://127.0.0.1:8080', 'station-1', 'secret'))
        lua.globals().script.retryPending()
        resent = [r for r in requests if r[1].endswith('/api/laps')]
        self.assertEqual(resent[0][3], posts[0][3])  # byte-identical resend after migration

    def test_both_slots_corrupt_blocks_sending_and_keeps_files(self):
        storage, _ = self.queued()
        for slot in 'ab':
            key = 'file:%s/queue.%s.json' % (STATE_DIR, slot)
            if key in storage: storage[key] = storage[key][:-7]
        before = dict(storage)
        lua, after, _, requests, _, _ = harness(storage)
        self.assertFalse(lua.globals().script.connect('http://127.0.0.1:8080', 'station-1', 'secret'))
        self.assertFalse(requests)
        for k, v in before.items():
            self.assertEqual(after[k], v)  # nothing overwritten or deleted
        self.assertTrue(any('.quarantine.' in k for k in after))


if __name__ == '__main__':
    unittest.main()
