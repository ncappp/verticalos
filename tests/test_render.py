import unittest, io, json
from datetime import date, timedelta
from unittest.mock import patch
from test_system import client, request, device, conn
import render

MP4 = b'\x00\x00\x00\x18ftypmp42' + b'\x00' * 64


def recipe(**k):
    b = {
        'name': 'R',
        'source_type': 'local',
        'source_url': 'FaxClip/Склейка',
        'scenes': [{'folder': 'intro', 'max_sec': 2}, {'folder': 'main'}],
        **k,
    }
    return request('/recipes', 'POST', b)


class RenderTests(unittest.TestCase):
    def setUp(self):
        with conn() as c:
            for t in ('ws_recipes', 'ws_recipe_texts', 'ws_render_jobs', 'ws_sources'):
                c.execute('delete from ' + t)

    def test_auth(self):
        for p in ('/api/recipes', '/api/render-jobs', '/api/sources'):
            self.assertEqual(client.get(p).status_code, 401)
        d, h = device()
        self.assertEqual(client.post('/api/bridge/render/claim', json={}).status_code, 401)

    def test_validation(self):
        self.assertEqual(recipe(name='').status_code, 400)
        self.assertEqual(recipe(scenes=[]).status_code, 400)
        self.assertEqual(recipe(scenes=[{'folder': '../etc'}]).status_code, 400)
        self.assertEqual(recipe(source_url='/etc').status_code, 400)
        self.assertEqual(recipe(source_type='yandex', source_url='https://evil.com/d/x').status_code, 400)
        self.assertEqual(
            recipe(source_type='yandex', source_url='https://disk.yandex.ru/d/AbCd1234').status_code, 200
        )
        self.assertEqual(recipe(auto_publish=True).status_code, 400)
        r = recipe(
            text_style={'size': 999, 'color': 'red', 'font': 'Comic'}, plate={'opacity': 500}
        ).get_json()
        self.assertEqual(
            (
                r['text_style']['size'],
                r['text_style']['color'],
                r['text_style']['font'],
                r['plate']['opacity'],
            ),
            (160, '#FFFFFF', 'Arial Bold', 100),
        )

    def test_texts_sequential_and_upload(self):
        rid = recipe().get_json()['id']
        self.assertEqual(
            request(
                f'/recipes/{rid}/texts',
                'PUT',
                {'rows': [['текст', 'заголовок', 'описание'], ['A', 'tA', 'dA'], ['B']]},
            ).get_json()['total'],
            2,
        )
        csvdata = 'Текст;Заголовок;Описание\nC;tC;dC\nD;;\n'.encode()
        r = client.post(
            f'/api/recipes/{rid}/texts',
            data={'file': (io.BytesIO(csvdata), 't.csv'), 'replace': '1'},
            headers=__import__('test_system').owner,
            content_type='multipart/form-data',
        )
        self.assertEqual(r.get_json()['total'], 2, r.get_json())
        d, h = device()
        self.assertEqual(len(request(f'/recipes/{rid}/run', 'POST', {'count': 3}).get_json()['jobs']), 3)
        texts = []
        for _ in range(3):
            j = request('/bridge/render/claim', 'POST', {}, h).get_json()['job']
            texts.append(j['payload']['text'])
            self.assertEqual(
                request(f"/bridge/render/{j['id']}/fail", 'POST', {'error': 'x'}, h).status_code, 200
            )
        self.assertEqual(texts, ['C', 'D', 'C'])
        request(f'/recipes/{rid}/texts/reset', 'POST', {})
        with conn() as c:
            self.assertEqual(c.execute('select text_pos from ws_recipes where id=?', (rid,)).fetchone()[0], 0)

    def test_render_result_autopublish_and_quota(self):
        d, h = device()
        a = request(
            '/accounts', 'POST', {'platform': 'TikTok', 'username': '@redmaagi', 'device_id': d['id']}
        ).get_json()
        rid = recipe(
            auto_publish=True, rights_confirmed=True, period_hours=1, enabled=True, caption_template='#тег'
        ).get_json()['id']
        self.assertEqual(
            request(
                '/sources',
                'POST',
                {'account_id': a['id'], 'recipe_id': rid, 'start_per_day': 1, 'target_per_day': 2},
            ).status_code,
            200,
        )
        self.assertEqual(
            request('/sources', 'POST', {'account_id': a['id'], 'recipe_id': rid}).status_code, 400
        )
        with conn() as c:
            c.execute('update ws_recipes set next_run_at=0 where id=?', (rid,))
        j = request(
            '/bridge/render/claim', 'POST', {'version': 'RENDER_AGENT_V1', 'ffmpeg': True}, h
        ).get_json()['job']
        self.assertEqual(j['kind'], 'render')
        self.assertEqual(j['payload']['video'], {'w': 1080, 'h': 1920, 'fps': 30})
        self.assertEqual(
            client.post(
                f"/api/bridge/render/{j['id']}/result",
                data={'file': (io.BytesIO(b'notmp4' * 10), 'v.mp4')},
                headers=h,
                content_type='multipart/form-data',
            ).status_code,
            400,
        )
        r = client.post(
            f"/api/bridge/render/{j['id']}/result",
            data={
                'file': (io.BytesIO(MP4), 'v.mp4'),
                'meta': json.dumps({'files': ['intro/a.mp4'], 'duration': 5}),
            },
            headers=h,
            content_type='multipart/form-data',
        )
        self.assertEqual(r.status_code, 200, r.get_json())
        x = r.get_json()
        self.assertTrue(x['post_id'])
        p = request('/posts/' + x['post_id']).get_json()
        self.assertEqual(p['caption'], '#тег')
        self.assertTrue(p['targets'][0]['publication_id'])
        jobs = request('/render-jobs').get_json()
        self.assertTrue(jobs['agent_online'])
        self.assertEqual(jobs['items'][0]['status'], 'done')
        src = request('/sources').get_json()[0]
        self.assertEqual((src['today'], src['quota']), (1, 1))
        with conn() as c:
            c.execute('update ws_recipes set next_run_at=0 where id=?', (rid,))
        self.assertIsNone(
            request('/bridge/render/claim', 'POST', {}, h).get_json()['job']
        )  # quota for day 0 reached

    def test_ramp_and_lease(self):
        t = date(2026, 1, 10)
        s = dict(created_at='2026-01-10T00:00:00+00:00', start_per_day=1, target_per_day=5, ramp_days=4)
        self.assertEqual([render.day_quota(s, t + timedelta(days=i)) for i in range(6)], [1, 2, 3, 4, 5, 5])
        d, h = device()
        rid = recipe().get_json()['id']
        request(f'/recipes/{rid}/run', 'POST', {'count': 1})
        j = request('/bridge/render/claim', 'POST', {}, h).get_json()['job']
        with conn() as c:
            c.execute('update ws_render_jobs set lease_until=0 where id=?', (j['id'],))
        j2 = request('/bridge/render/claim', 'POST', {}, h).get_json()['job']
        self.assertEqual(j2['id'], j['id'])
        self.assertEqual(request(f"/render-jobs/{j['id']}/cancel", 'POST', {}).status_code, 409)

    def test_scan_local_and_yandex(self):
        d, h = device()
        rid = recipe(audio={'music_folder': 'music'}).get_json()['id']
        self.assertTrue(request(f'/recipes/{rid}/scan', 'POST', {}).get_json()['pending'])
        j = request('/bridge/render/claim', 'POST', {}, h).get_json()['job']
        self.assertEqual(j['kind'], 'scan')
        request(
            f"/bridge/render/{j['id']}/scan-result",
            'POST',
            {'ok': True, 'items': [{'path': 'intro', 'videos': 3, 'audio': 0}]},
            h,
        )
        self.assertTrue(request('/recipes/' + rid).get_json()['scan']['ok'])
        y = recipe(source_type='yandex', source_url='https://disk.yandex.ru/d/AbCd1234').get_json()['id']
        with patch(
            'render.yandex_list',
            side_effect=lambda u, p='/', timeout=20: (
                [{'type': 'dir', 'name': 'intro'}] if p == '/' else [{'type': 'file', 'name': 'a.mp4'}]
            ),
        ):
            s = request(f'/recipes/{y}/scan', 'POST', {}).get_json()
        self.assertTrue(s['ok'], s)

    def test_install_command(self):
        r = request('/render-install-command').get_json()
        self.assertIn('render-install.py', r['command'])
        body = client.get('/render-install.py').data
        import hashlib

        self.assertEqual(hashlib.sha256(body).hexdigest(), r['sha256'])


if __name__ == '__main__':
    unittest.main()
