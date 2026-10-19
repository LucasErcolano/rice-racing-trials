"""A CSP app must initialize itself from locally provisioned station settings."""
import unittest
from test_network import harness, persisted


class Bootstrap(unittest.TestCase):
    def test_first_update_loads_provisioned_station_without_manual_calls(self):
        lua, storage, pos, requests, reply, to_lua = harness()
        lua.execute("package.preload['station_config'] = function() return {server_url='http://127.0.0.1:8080', station_id='ST01', station_token='local-only'} end")
        lua.globals().script.update(.1)
        self.assertTrue(any(r[1].endswith('/api/station/assignment') for r in requests))
        self.assertTrue(any(r[1].endswith('/api/station/heartbeat') for r in requests))
        self.assertIn('nonce', persisted(storage))

    def test_absent_station_config_fails_closed(self):
        lua, _, _, requests, _, _ = harness()
        lua.globals().script.update(.1)
        self.assertFalse(requests)


if __name__ == '__main__': unittest.main()
