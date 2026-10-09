"""Multi-tenancy, admin panel, plan limits, background jobs, monitoring and ban-protection preview."""

import json
import time
import unittest
from unittest.mock import patch

from test_system import client, request, values, key, hmac, hashlib, urlencode, owner  # noqa: F401
from app import conn
import db as dbmod
import jobs
import tenancy


def init_for(uid, username='user'):
    v = {'auth_date': str(int(time.time())), 'user': json.dumps({'id': uid, 'username': username})}
    v['hash'] = hmac.new(
        key, '\n'.join(f'{k}={v[k]}' for k in sorted(v)).encode(), hashlib.sha256
    ).hexdigest()
    return {'X-Telegram-Init-Data': urlencode(v)}


def as_user(uid):
    h = init_for(uid)
    return lambda path, method='GET', body=None: request(path, method, body, h)


class PlatformTests(unittest.TestCase):
    def setUp(self):
        tenancy.set_setting('registration', {'mode': 'open', 'default_plan': 'free'})
        tenancy.set_setting('maintenance', {'on': False, 'message': ''})
        tenancy.set_setting('plans', tenancy.DEFAULT_PLANS)

    def test_tenants_are_isolated(self):
        a, b = as_user(700001), as_user(700002)
        r = a('/accounts', 'POST', {'platform': 'TikTok', 'username': '@tenant_a'})
        self.assertEqual(r.status_code, 200, r.get_json())
        names_b = [x['username'] for x in b('/accounts').get_json()]
        names_a = [x['username'] for x in a('/accounts').get_json()]
        names_owner = [x['username'] for x in request('/accounts').get_json()]
        self.assertIn('@tenant_a', names_a)
        self.assertNotIn('@tenant_a', names_b)
        self.assertNotIn('@tenant_a', names_owner)
        s = a('/session').get_json()
        self.assertEqual(s['tenant'], 'u700001')
        self.assertFalse(s['is_admin'])
        self.assertTrue(request('/session').get_json()['is_admin'])

    def test_bridge_and_links_route_to_owner_tenant(self):
        a = as_user(700003)
        d = a('/devices', 'POST', {'name': 'A phone'}).get_json()
        h = {'X-Device-ID': d['id'], 'Authorization': 'Bearer ' + d['device_token']}
        self.assertEqual(
            client.post('/api/bridge/heartbeat', json={'battery': 50}, headers=h).status_code, 200
        )
        devs = {x['id']: x for x in a('/devices').get_json()}
        self.assertEqual(devs[d['id']]['battery'], 50)
        self.assertNotIn(d['id'], [x['id'] for x in request('/devices').get_json()])
        link = a('/links', 'POST', {'target_url': 'https://site.ru/a', 'placement': 'shapka'}).get_json()
        code = link['link']['code'] if 'link' in link else link['code']
        r = client.get('/l/' + code)
        self.assertEqual(r.status_code, 302)
        self.assertIn('site.ru', r.headers['Location'])

    def test_admin_only_and_user_management(self):
        u = as_user(700004)
        u('/session')
        self.assertEqual(u('/admin/overview').status_code, 403)
        ov = request('/admin/overview').get_json()
        self.assertGreaterEqual(ov['users']['total'], 1)
        users = request('/admin/users?q=700004').get_json()['users']
        self.assertEqual(users[0]['tg_id'], '700004')
        self.assertEqual(
            request('/admin/users/700004', 'PATCH', {'blocked': True, 'block_reason': 'спам'}).status_code,
            200,
        )
        r = u('/accounts')
        self.assertEqual(r.status_code, 403)
        self.assertIn('спам', r.get_json()['error'])
        request('/admin/users/700004', 'PATCH', {'blocked': False})
        self.assertEqual(u('/accounts').status_code, 200)
        self.assertEqual(request('/admin/users/8784706094', 'PATCH', {'blocked': True}).status_code, 400)
        self.assertTrue(request('/admin/audit').get_json()['items'])
        st = request('/admin/settings')
        self.assertEqual(st.status_code, 200)
        self.assertIn('free', st.get_json()['plans'])
        for tab in ('/admin/jobs', '/admin/errors?status=all', '/admin/overview'):
            self.assertEqual(request(tab).status_code, 200, tab)
        self.assertEqual(
            request('/admin/broadcast', 'POST', {'text': 'hello all', 'dry_run': True}).status_code, 200
        )

    def test_limits_features_registration_maintenance(self):
        u = as_user(700005)
        u('/session')
        request(
            '/admin/users/700005', 'PATCH', {'limits': {'max_accounts': 1}, 'features': {'warmup': False}}
        )
        self.assertEqual(u('/accounts', 'POST', {'platform': 'TikTok', 'username': '@one'}).status_code, 200)
        r = u('/accounts', 'POST', {'platform': 'TikTok', 'username': '@two'})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(u('/warmup/settings').status_code, 403)
        self.assertFalse(u('/session').get_json()['features']['warmup'])
        request('/admin/settings/registration', 'PUT', {'mode': 'closed'})
        self.assertEqual(as_user(700006)('/accounts').status_code, 403)
        self.assertEqual(u('/accounts').status_code, 200)  # existing users keep access
        request('/admin/settings/maintenance', 'PUT', {'on': True, 'message': 'Техработы'})
        tenancy._cache.clear()
        self.assertEqual(u('/accounts').status_code, 503)
        self.assertEqual(request('/accounts').status_code, 200)  # admin still works
        request('/admin/settings/announcement', 'PUT', {'active': True, 'text': 'Привет'})
        tenancy._cache.clear()
        self.assertEqual(request('/session').get_json()['announcement']['text'], 'Привет')

    def test_jobs_retry_then_dead_and_monitoring(self):
        calls = []

        @jobs.handler('t_flaky')
        def flaky(p):
            calls.append(dbmod.current_tenant())
            raise RuntimeError('boom')

        jid = jobs.enqueue('t_flaky', {}, tenant='u700007', max_attempts=2)
        for _ in range(40):
            with tenancy.core() as c:
                c.execute("update core_jobs set run_at=0 where id=? and status='queued'", (jid,))
                st = c.execute('select status from core_jobs where id=?', (jid,)).fetchone()['status']
            if st == 'dead':
                break
            jobs.run_one()
            time.sleep(0.02)
        with tenancy.core() as c:
            st = dict(c.execute('select status,attempts from core_jobs where id=?', (jid,)).fetchone())
        self.assertEqual(st['status'], 'dead')
        self.assertIn('u700007', calls)
        errs = request('/admin/errors').get_json()['errors']
        self.assertTrue(any('t_flaky' in e['title'] for e in errs))
        self.assertEqual(request('/admin/jobs/' + jid + '/retry', 'POST', {}).status_code, 200)

    def test_unhandled_error_is_recorded(self):
        import app as appmod

        def _boom():
            raise ValueError('kaboom')

        with patch.dict(appmod.app.view_functions, {'me': _boom}):
            r = request('/me')
        self.assertEqual(r.status_code, 500)
        self.assertIn('error_id', r.get_json())
        ids = [e['id'] for e in request('/admin/errors').get_json()['errors']]
        self.assertIn(r.get_json()['error_id'], ids)
        self.assertEqual(
            request('/client-error', 'POST', {'message': 'TypeError: x', 'page': 'posts'}).status_code, 200
        )

    def test_antiban_preview_is_read_only(self):
        u = as_user(700008)
        acc = u('/accounts', 'POST', {'platform': 'TikTok', 'username': '@fresh'}).get_json()
        tables = ['accounts', 'publications', 'ws_tasks', 'ws_alerts', 'ws_settings']

        def snapshot():
            with dbmod.use_tenant('u700008'), conn() as c:
                return {t: c.execute(f'select count(*) from {t}').fetchone()[0] for t in tables}

        before = snapshot()
        r = u('/antiban/preview?rules=' + json.dumps({'min_warm_sessions': 3}))
        self.assertEqual(r.status_code, 200, r.get_json())
        d = r.get_json()
        self.assertEqual(d['mode'], 'test')
        a = [x for x in d['accounts'] if x['account_id'] == acc['id']][0]
        self.assertEqual(a['stage'], 'young')
        self.assertTrue(any('прогре' in f['text'].lower() for f in a['findings']))
        self.assertEqual(snapshot(), before)
        with dbmod.use_tenant('u700008'), conn() as c:
            from antiban import _readonly

            _readonly(c)
            with self.assertRaises(Exception):
                c.execute('delete from accounts')

    def test_dialect_translation(self):
        self.assertEqual(
            dbmod.translate('insert or ignore into a values(?)')[0],
            'insert into a values(%s) on conflict do nothing',
        )
        self.assertIn('least(', dbmod.translate('update t set d=min(target,d+1)')[0])
        self.assertIn("'50%%'", dbmod.translate("select * from t where x like '50%' and y=?")[0])


if __name__ == '__main__':
    unittest.main()


class MigrationTests(unittest.TestCase):
    @unittest.skipUnless(dbmod.IS_PG, 'needs PostgreSQL')
    def test_sqlite_roundtrip(self):
        import os
        import tempfile
        import migrate

        fd, path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        migrate.export_pg_to_sqlite(path)
        import sqlite3

        n = sqlite3.connect(path).execute('select count(*) from accounts').fetchone()[0]
        with conn() as c:
            self.assertEqual(n, c.execute('select count(*) from accounts').fetchone()[0])
        stats = migrate.import_sqlite_into_pg(path, tenant='u700099')
        self.assertEqual(stats['accounts'][0], n)
        with dbmod.use_tenant('u700099'), conn() as c:
            self.assertEqual(c.execute('select count(*) from accounts').fetchone()[0], n)
