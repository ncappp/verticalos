"""Device deletion, agents (tasks + analytics + study keywords), banners, owner-only admin."""

import io, json, os, unittest, uuid
from unittest.mock import patch
from test_system import client, request, device, conn, owner
import tenancy

PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 64


class DeviceDeleteTests(unittest.TestCase):
    def test_delete_unbinds_and_revokes(self):
        d, h = device()
        a = request(
            '/accounts', 'POST', {'platform': 'TikTok', 'username': '@del', 'device_id': d['id']}
        ).get_json()
        self.assertEqual(request('/devices/' + d['id'], 'DELETE').status_code, 200)
        self.assertNotIn(d['id'], [x['id'] for x in request('/devices').get_json()])
        with conn() as c:
            self.assertIsNone(
                c.execute('select device_id from accounts where id=?', (a['id'],)).fetchone()['device_id']
            )
        # the old phone token no longer works
        self.assertEqual(client.post('/api/bridge/render/claim', json={}, headers=h).status_code, 401)
        self.assertEqual(request('/devices/' + d['id'], 'DELETE').status_code, 404)

    def test_running_job_needs_force(self):
        d, _ = device()
        with conn() as c:
            c.execute(
                "insert into ws_tasks(id,device_id,status,created_at) values(?,?,'running',datetime('now'))",
                (str(uuid.uuid4()), d['id']),
            )
        r = request('/devices/' + d['id'], 'DELETE')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(request('/devices/' + d['id'] + '?force=1', 'DELETE').status_code, 200)


class AgentTests(unittest.TestCase):
    def test_tasks_stats_keywords(self):
        d, _ = device()
        p = request(
            '/personas',
            'POST',
            {'name': 'Ден', 'device_id': d['id'], 'interests': [{'tag': 'авто', 'weight': 5}]},
        )
        self.assertEqual(p.status_code, 200, p.get_json())
        pid = p.get_json()['id']
        self.assertEqual(request(f'/agents/{pid}/tasks', 'POST', {'kind': 'study_topic'}).status_code, 400)
        self.assertEqual(
            request(f'/agents/{pid}/tasks', 'POST', {'kind': 'likes', 'target': 'x'}).status_code, 400
        )
        self.assertEqual(
            request(
                f'/agents/{pid}/tasks', 'POST', {'kind': 'study_account', 'value': 'mrbeast'}
            ).status_code,
            200,
        )
        t = request(f'/agents/{pid}/tasks', 'POST', {'kind': 'likes', 'target': 10}).get_json()['id']
        with conn() as c:
            c.execute(
                "insert into ws_tasks(id,persona_id,status,actions,created_at) values(?,?,'done',?,?)",
                (
                    str(uuid.uuid4()),
                    pid,
                    json.dumps({'likes': 4, 'videos': 9, 'seconds': 120}),
                    '2999-01-01T00:00:00',
                ),
            )
            import agents

            self.assertEqual(agents.study_keywords(c, pid), ['@mrbeast', 'авто'])
        o = request(f'/agents/{pid}/overview').get_json()
        self.assertEqual((o['totals']['likes'], o['totals']['videos'], o['totals']['minutes']), (4, 9, 2))
        self.assertEqual([x['progress'] for x in o['tasks'] if x['id'] == t], [4])
        self.assertEqual(request(f'/agents/{pid}/tasks/{t}', 'PATCH', {'status': 'paused'}).status_code, 200)
        self.assertEqual(request(f'/agents/{pid}/tasks/{t}', 'DELETE').status_code, 200)
        self.assertEqual(request('/personas/' + pid, 'DELETE').status_code, 200)
        with conn() as c:
            self.assertEqual(
                c.execute('select count(*) from ws_agent_tasks where persona_id=?', (pid,)).fetchone()[0], 0
            )
        self.assertEqual(request(f'/agents/{pid}/overview').status_code, 404)


class BannerTests(unittest.TestCase):
    def upload(self, data, name='Лого'):
        return client.post(
            '/api/banners',
            data={'file': (io.BytesIO(data), 'b.png'), 'name': name},
            headers=owner,
            content_type='multipart/form-data',
        )

    def test_banner_flow(self):
        self.assertEqual(self.upload(b'not an image').status_code, 400)
        bid = self.upload(PNG).get_json()['id']
        f = client.get(f'/api/banners/{bid}/file', headers=owner)
        self.assertEqual((f.status_code, f.data, f.mimetype), (200, PNG, 'image/png'))
        r = request(
            '/recipes',
            'POST',
            {
                'name': 'B',
                'source_type': 'local',
                'source_url': 'FaxClip',
                'scenes': [{'folder': 'main'}],
                'banners': [
                    {'banner_id': bid, 'position': 'bottom-right', 'width_pct': 500, 'opacity': 70},
                    {'banner_id': 'nope'},
                ],
            },
        ).get_json()
        self.assertEqual(len(r['banners']), 1)
        self.assertEqual((r['banners'][0]['width_pct'], r['banners'][0]['position']), (100, 'bottom-right'))
        self.assertEqual(request('/banners').get_json()[0]['recipes'], 1)
        d, h = device()
        self.assertEqual(client.get(f'/api/bridge/banners/{bid}', headers=h).data, PNG)
        self.assertEqual(client.get(f'/api/bridge/banners/{bid}').status_code, 401)
        self.assertEqual(request('/banners/' + bid, 'DELETE').status_code, 200)
        self.assertEqual(request('/recipes/' + r['id']).get_json()['banners'], [])


class AdminOwnerOnlyTests(unittest.TestCase):
    def test_only_owner(self):
        with patch.dict(os.environ, {'FAXCLIP_ADMIN_IDS': '8784706094,111'}):
            self.assertEqual(tenancy.admin_ids(), {'8784706094'})
        with patch.dict(os.environ, {'FAXCLIP_ADMIN_IDS': '111'}):
            self.assertEqual(tenancy.admin_ids(), set())
        with patch.dict(os.environ, {'FAXCLIP_ADMIN_IDS': ''}):
            self.assertEqual(tenancy.admin_ids(), {'8784706094'})


if __name__ == '__main__':
    unittest.main()


class AntibanLiveTests(unittest.TestCase):
    def setUp(self):
        with conn() as c:
            c.execute("delete from ws_settings where key='antiban'")

    def tearDown(self):
        with conn() as c:
            c.execute("delete from ws_settings where key='antiban'")

    def test_switch_and_delay(self):
        from test_system import new_job

        self.assertFalse(request('/antiban/settings').get_json()['enabled'])
        s = request(
            '/antiban/settings', 'PUT', {'enabled': True, 'rules': {'quiet_from': 0, 'quiet_to': 0}}
        ).get_json()
        self.assertTrue(s['enabled'])
        self.assertEqual(request('/antiban/preview').get_json()['mode'], 'live')
        d, h = device()
        new_job(d)
        with conn() as c:
            pid = c.execute(
                "select publication_id from ui_jobs where device_id=? and status='QUEUED'", (d['id'],)
            ).fetchone()[0]
            aid = c.execute('select account_id from publications where id=?', (pid,)).fetchone()[0]
            # the account already posted 5 minutes ago -> min gap 120 min must postpone the next post
            c.execute(
                "insert into publications values(?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    None,
                    aid,
                    'TikTok',
                    'PUBLISHED',
                    None,
                    __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),
                    None,
                    None,
                    '2020-01-01T00:00:00+00:00',
                ),
            )
        r = request('/bridge/claim', 'POST', {}, h).get_json()
        self.assertIsNone(r['job'])
        self.assertIn('Защита от банов', r['antiban'])
        with conn() as c:
            self.assertIn(
                'Защита от банов',
                c.execute('select error from publications where id=?', (pid,)).fetchone()[0],
            )
        self.assertEqual(request('/antiban/log').get_json()[0]['decision'], 'delay')
        request('/antiban/settings', 'PUT', {'enabled': False})
        with conn() as c:
            c.execute('update ui_jobs set available=0 where publication_id=?', (pid,))
        self.assertIsNotNone(request('/bridge/claim', 'POST', {}, h).get_json()['job'])
        with conn() as c:
            self.assertIsNone(c.execute('select error from publications where id=?', (pid,)).fetchone()[0])

    def test_dashboard_views_are_real(self):
        with conn() as c:
            c.execute(
                "insert into ws_video_metrics(id,publication_id,account_id,url,views,likes,comments,shares,saves,source,created_at) values(?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), 'p-dash', None, 'u', 1234, 5, 1, 0, 0, 'test', '2026-01-01T00:00:00'),
            )
        self.assertGreaterEqual(request('/dashboard').get_json()['views'], 1234)
