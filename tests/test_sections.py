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
