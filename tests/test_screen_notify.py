import unittest
from test_system import client, request, device, new_job, conn


class ScreenTests(unittest.TestCase):
    def setUp(self):
        with conn() as c:
            c.execute('delete from ui_jobs')
            c.execute('delete from publications')
            c.execute('delete from ws_alerts')

    def test_screen_flow(self):
        d, h = device()
        did = d['id']
        self.assertEqual(client.get(f'/api/devices/{did}/screen').status_code, 401)
        self.assertFalse(request('/bridge/screen/poll', headers=h).get_json()['active'])
        self.assertEqual(request(f'/devices/{did}/screen/open', 'POST', {}).status_code, 200)
        self.assertEqual(
            request(
                f'/devices/{did}/screen/command', 'POST', {'action': 'tap', 'x': 2, 'y': 0.5}
            ).status_code,
            400,
        )
        self.assertEqual(
            request(f'/devices/{did}/screen/command', 'POST', {'action': 'key', 'key': 'rm'}).status_code, 400
        )
        self.assertEqual(
            request(
                f'/devices/{did}/screen/command', 'POST', {'action': 'tap', 'x': 0.5, 'y': 0.5}
            ).status_code,
            200,
        )
        p = request('/bridge/screen/poll', headers=h).get_json()
        self.assertTrue(p['active'])
        self.assertEqual(p['commands'][0]['action'], 'tap')
        self.assertEqual(request('/bridge/screen/poll', headers=h).get_json()['commands'], [])
        r = client.post(
            '/api/bridge/screen/frame',
            data=b'\xff\xd8JPEGDATA',
            headers={**h, 'Content-Type': 'image/jpeg', 'X-Screen-Width': '1080', 'X-Screen-Height': '2400'},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(client.post('/api/bridge/screen/frame', data=b'PNG', headers=h).status_code, 400)
        st = request(f'/devices/{did}/screen').get_json()
        self.assertTrue(st['agent_online'])
        self.assertEqual(st['frame_seq'], 1)
        self.assertEqual(st['width'], 1080)
        from test_system import owner

        self.assertEqual(
            client.get(f'/api/devices/{did}/screen.jpg', headers=owner).data, b'\xff\xd8JPEGDATA'
        )
        request(
            '/bridge/screen/result',
            'POST',
            {
                'id': p['commands'][0]['id'],
                'ok': True,
                'message': 'Нажатие',
                'action': 'tap',
                'elements': [{'text': 'OK', 'clickable': True}],
            },
            h,
        )
        st = request(f'/devices/{did}/screen').get_json()
        self.assertEqual(st['results'][-1]['message'], 'Нажатие')
        self.assertEqual(st['elements'][0]['index'], 1)
        self.assertEqual(client.get('/api/bridge/screen/poll').status_code, 401)

    def test_input_blocked_while_publishing(self):
        d, h = device()
        new_job(d)
        request('/bridge/claim', 'POST', {}, h)
        r = request(f"/devices/{d['id']}/screen/command", 'POST', {'action': 'tap', 'x': 0.1, 'y': 0.1})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(
            request(f"/devices/{d['id']}/screen/command", 'POST', {'action': 'wake'}).status_code, 200
        )


class NotifyTests(unittest.TestCase):
    def setUp(self):
        with conn() as c:
            c.execute('delete from ui_jobs')
            c.execute('delete from publications')
            c.execute('delete from ws_alerts')

    def test_alerts_from_events(self):
        d, h = device()
        request('/bridge/heartbeat', 'POST', {'battery': 50}, h)
        pub, body, key = new_job(d)
        job = request('/bridge/claim', 'POST', {}, h).get_json()['job']
        request(
            '/bridge/jobs/' + job['id'] + '/fail',
            'POST',
            {'code': 'DUPLICATE_VIDEO_ALREADY_POSTED'},
            {**h, 'X-Job-Lease': job['lease']},
        )
        request('/bridge/disconnect', 'POST', {}, h)
        a = request('/alerts').get_json()
        types = [x['event_type'] for x in a['items']]
        for t in ('device_online', 'publication_queued', 'publication_review', 'device_offline'):
            self.assertIn(t, types)
        self.assertEqual(a['unread'], 4)
        rev = [x for x in a['items'] if x['event_type'] == 'publication_review'][0]
        self.assertIn('уже есть в профиле', rev['message'])
        request('/alerts/' + rev['id'] + '/read', 'POST', {})
        self.assertEqual(request('/alerts?unread=1').get_json()['total'], 4)
        self.assertEqual(len(request('/alerts?unread=1').get_json()['items']), 3)
        request('/alerts/read-all', 'POST', {})
        self.assertEqual(request('/alerts').get_json()['unread'], 0)
        self.assertTrue(len(request('/activity').get_json()) >= 4)
        self.assertEqual(
            request('/alerts/settings', 'PUT', {'telegram': False, 'levels': ['error', 'bad']}).get_json()[
                'levels'
            ],
            ['error'],
        )
        self.assertFalse(request('/alerts').get_json()['settings']['telegram'])
        self.assertEqual(request('/alerts', 'DELETE').status_code, 200)
        self.assertEqual(request('/alerts').get_json()['total'], 0)
