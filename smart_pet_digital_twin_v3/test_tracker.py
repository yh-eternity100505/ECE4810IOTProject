"""Run: python -m unittest -v test_tracker.py (no hardware required)."""
import copy
import unittest
from tracker import Tracker, load_config
import server


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.t = Tracker(clock=lambda: self.now)

    def feed(self, values, count=3):
        for _ in range(count):
            result = self.t.update(values)
        return result

    def test_debounce_missing_and_clear(self):
        self.feed({1: 30}, 1)
        self.feed({}, 5)
        self.assertIsNone(self.t.zone)
        self.feed({1: 30}, 2)
        self.assertEqual(self.t.zone, 'A')
        self.feed({1: 100}, 2)
        self.assertTrue(self.t.active[1])
        self.feed({1: 100}, 1)
        self.now = 2
        self.assertIsNone(self.t.snapshot()['zone'])

    def test_stale_no_new_posts(self):
        self.feed({1: 30})
        self.now = 4.1
        state = self.t.snapshot()
        self.assertIsNone(state['zone'])
        self.assertFalse(state['sensors']['1']['active'])
        self.assertTrue(state['sensors']['1']['stale'])

    def test_stale_partial_batch(self):
        self.feed({1: 30})
        self.now = 5
        self.feed({5: 30})
        self.assertEqual(self.t.zone, 'C')

    def test_ambiguous_and_previous_zone(self):
        self.assertEqual(self.feed({1: 30, 3: 30})['status'], 'ambiguous')
        self.assertIsNone(self.t.zone)
        self.t = Tracker(clock=lambda: self.now)
        self.feed({1: 30})
        state = self.feed({1: 30, 3: 30})
        self.assertEqual(state['zone'], 'A')
        self.assertEqual(state['status'], 'ambiguous')

    def test_five_to_three_and_simulation_live_switch(self):
        for sensor, zone in [(1, 'A'), (2, 'A'), (3, 'B'), (4, 'B'), (5, 'C')]:
            self.assertEqual(self.t.simulate(sensor)['zone'], zone)
        self.assertIsNone(self.t.simulate(0)['zone'])
        self.t.simulate(1)
        self.feed({5: 30})
        self.assertEqual(self.t.zone, 'C')
        self.assertFalse(self.t.active[1])

    def test_simulation_holds_without_stale_badge(self):
        self.t.simulate(2)
        self.now = 100
        state = self.t.snapshot()
        self.assertEqual(state['zone'], 'A')
        self.assertFalse(state['sensors']['2']['stale'])

    def test_config_remap(self):
        cfg = copy.deepcopy(load_config())
        cfg['groups'] = {'A': [5], 'B': [1, 2], 'C': [3, 4]}
        self.assertEqual(Tracker(cfg).simulate(5)['zone'], 'A')


class APITests(unittest.TestCase):
    def setUp(self):
        self.old_tracker, self.old_token = server.tracker, server.TOKEN
        server.tracker, server.TOKEN = Tracker(), ''
        self.client = server.app.test_client()

    def tearDown(self):
        server.tracker, server.TOKEN = self.old_tracker, self.old_token

    def test_validation(self):
        for payload in [[], {'distances': {}}, {'distances': {'6': 20}}, {'distances': {'1': True}}, {'distances': {'1': '3'}}, {'distances': {'1': float('nan')}}, {'distances': {'1': -1}}, {'distances': {'1': 501}}]:
            self.assertEqual(self.client.post('/api/sensors', json=payload).status_code, 400)
        for payload in [[], {}, {'sensor': True}, {'sensor': 1.5}, {'sensor': 6}]:
            self.assertEqual(self.client.post('/api/simulate', json=payload).status_code, 400)
        self.assertEqual(self.client.post('/api/sensors', json={'distances': {'1': None}}).status_code, 200)

    def test_token(self):
        server.TOKEN = 'test'
        self.assertEqual(self.client.post('/api/simulate', json={'sensor': 1}).status_code, 401)
        self.assertEqual(self.client.post('/api/simulate', json={'sensor': 1}, headers={'X-Pet-Token': 'test'}).status_code, 200)

    def test_socket_and_state(self):
        sock = server.socketio.test_client(server.app)
        self.assertEqual(sock.get_received()[0]['name'], 'pet_update')
        state = self.client.post('/api/simulate', json={'sensor': 5}).get_json()
        self.assertEqual(state['zone'], 'C')
        self.assertEqual(sock.get_received()[-1]['args'][0]['zone'], 'C')
        self.assertEqual(len(self.client.get('/api/config').get_json()['sensors']), 5)
        sock.disconnect()


if __name__ == '__main__':
    unittest.main()
