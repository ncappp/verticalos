import unittest
from unittest.mock import patch
from test_system import client, request, device, conn


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        with conn() as c:
            for t in (
                'ws_persona_interests',
                'ws_persona_times',
                'ws_account_profiles',
                'ws_personas',
                'ws_proxies',
                'ws_settings',
                'ws_device_profiles',
            ):
                c.execute('delete from ' + t)

    def test_auth_required(self):
        for p in (
            '/api/onboarding',
            '/api/proxies',
            '/api/personas',
            '/api/account-profiles',
            '/api/device-profiles',
        ):
            self.assertEqual(client.get(p).status_code, 401)

    def test_proxy_validation_and_one_to_one(self):
        d, _ = device()
        self.assertEqual(
            request('/proxies', 'POST', {'proxy_ip': '1.2.3.4:80', 'proxy_port': 80}).status_code, 400
        )
        self.assertEqual(
            request('/proxies', 'POST', {'proxy_ip': '192.168.1.1', 'proxy_port': 80}).status_code, 400
        )
        self.assertEqual(
            request('/proxies', 'POST', {'proxy_ip': '8.8.8.8', 'proxy_port': 99999}).status_code, 400
        )
        r = request(
            '/proxies',
            'POST',
            {
                'proxy_ip': '8.8.8.8',
                'proxy_port': 8080,
                'proxy_username': 'u',
                'proxy_password': 'secret',
                'device_id': d['id'],
            },
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            request(
                '/proxies', 'POST', {'proxy_ip': '8.8.4.4', 'proxy_port': 8080, 'device_id': d['id']}
            ).status_code,
            400,
        )
        lst = request('/proxies').get_json()
        self.assertNotIn('proxy_password', lst[0])
        self.assertTrue(lst[0]['has_password'])
        pid = r.get_json()['id']
        self.assertEqual(request('/proxies/' + pid, 'PATCH', {'proxy_port': 3128}).status_code, 200)
        with conn() as c:
            self.assertEqual(c.execute('select proxy_password from ws_proxies').fetchone()[0], 'secret')
        with patch('workspace.proxy_check', return_value=(True, '9.9.9.9', 120, 'Прокси работает')):
            x = request('/proxies/' + pid + '/check', 'POST', {}).get_json()
        self.assertTrue(x['ok'])
        self.assertEqual(request('/proxies').get_json()[0]['status'], 'active')

    def test_persona_rules(self):
        d, _ = device()
        self.assertEqual(request('/personas', 'POST', {'name': '101 Ден'}).status_code, 400)
        self.assertEqual(
            request(
                '/personas', 'POST', {'name': '101 Ден', 'device_id': d['id'], 'date_of_birth': '2015-01-01'}
            ).status_code,
            400,
        )
        r = request(
            '/personas',
            'POST',
            {
                'name': '101 Ден',
                'device_id': d['id'],
                'gender': 'male',
                'interests': [{'tag': f't{i}', 'weight': 9} for i in range(8)],
                'preferred_times': [{'time_slot': 'morning', 'weight': 0}, {'time_slot': 'bad'}],
            },
        )
        self.assertEqual(r.status_code, 200, r.get_json())
        pid = r.get_json()['id']
        p = request('/personas/' + pid).get_json()
        self.assertEqual(len(p['interests']), 5)
        self.assertTrue(all(i['weight'] == 5 for i in p['interests']))
        self.assertEqual(p['preferred_times'], [{'time_slot': 'morning', 'weight': 1}])
        self.assertEqual(
            request('/personas', 'POST', {'name': 'Второй', 'device_id': d['id']}).status_code, 400
        )
        self.assertEqual(request('/personas/' + pid, 'PATCH', {'city': 'Москва'}).status_code, 200)
        self.assertEqual(request('/personas/' + pid).get_json()['city'], 'Москва')

    def test_account_profile_and_onboarding(self):
        d, _ = device()
        st = request('/onboarding').get_json()
        self.assertFalse(st['done'])
        self.assertEqual(request('/onboarding', 'POST', {'finish': True}).status_code, 400)
        pid = request('/personas', 'POST', {'name': 'P', 'device_id': d['id']}).get_json()['id']
        a = request('/accounts', 'POST', {'platform': 'TikTok', 'username': '@x'}).get_json()['id']
        self.assertEqual(request('/account-profiles/' + a, 'PUT', {'persona_id': pid}).status_code, 400)
        self.assertEqual(
            request(
                '/account-profiles/' + a,
                'PUT',
                {'persona_id': pid, 'search_keywords': 'авто, ремонт', 'work_mode': 'warming'},
            ).status_code,
            200,
        )
        row = [x for x in request('/account-profiles').get_json() if x['id'] == a][0]
        self.assertEqual(row['persona_name'], 'P')
        self.assertEqual(row['work_mode'], 'warming')
        self.assertEqual(row['device_id'], d['id'])
        self.assertEqual(request('/onboarding', 'POST', {'skip_proxies': True}).status_code, 200)
        self.assertEqual(request('/onboarding', 'POST', {'finish': True}).status_code, 200)
        st = request('/onboarding').get_json()
        self.assertTrue(st['done'])
        self.assertTrue(st['proxies_skipped'])

    def test_device_profile(self):
        d, _ = device()
        pr = request('/proxies', 'POST', {'proxy_ip': '8.8.8.8', 'proxy_port': 1080}).get_json()['id']
        self.assertEqual(
            request(
                '/device-profiles/' + d['id'],
                'PUT',
                {'name': '101', 'locale': 'ru-RU', 'timezone': 'Europe/Moscow', 'proxy_id': pr},
            ).status_code,
            200,
        )
        row = [x for x in request('/device-profiles').get_json() if x['id'] == d['id']][0]
        self.assertEqual((row['name'], row['timezone'], row['proxy_id']), ('101', 'Europe/Moscow', pr))
