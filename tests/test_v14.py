import base64, io, os, shutil, tempfile, unittest, uuid
from pathlib import Path
from unittest.mock import patch
from test_system import app, client, request, device, new_job, owner, conn, UPLOAD
from pc_bridge import Bridge
from adb_control import ScreenError
from accessibility_publisher import AccessibilityPublisher, VERIFY_OK

PNG = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII='
)
MEDIA = b'\x00\x00\x00\x18ftypisom' + b'\x00' * 80
URL = 'https://www.tiktok.com/@redmaagi/video/1234567890123456789'


class IntegrationSafetyTests(unittest.TestCase):
    def test_upload_caption_roundtrip(self):
        r = client.post(
            '/api/media/upload', headers=owner, data={'file': (io.BytesIO(MEDIA), '../../private.mp4')}
        )
        self.assertEqual(r.status_code, 200)
        name = r.get_json()['filename']
        self.assertRegex(name, r'^[a-f0-9]{32}\.mp4$')
        caption = 'Описание точно\n#тест 🟢'
        cid = request(
            '/clips', 'POST', {'source_file': name, 'title': 'Видео', 'caption': caption}
        ).get_json()['id']
        self.assertEqual(next(x for x in request('/clips').get_json() if x['id'] == cid)['caption'], caption)

    def test_invalid_upload_rejected(self):
        r = client.post(
            '/api/media/upload', headers=owner, data={'file': (io.BytesIO(b'notvideo'), 'bad.mp4')}
        )
        self.assertEqual(r.status_code, 400)

    def test_rights_and_confirmation_are_boolean(self):
        d, h = device()
        pub, body, key = new_job(d)
        for field in ('confirmed', 'rights_confirmed'):
            body[field] = 'true'
            self.assertEqual(
                request(
                    '/publications', 'POST', body, {**owner, 'Idempotency-Key': uuid.uuid4().hex}
                ).status_code,
                400,
            )
            body[field] = True

    def test_same_media_never_requeued(self):
        d, h = device()
        pub, body, key = new_job(d)
        self.assertEqual(
            request(
                '/publications', 'POST', body, {**owner, 'Idempotency-Key': uuid.uuid4().hex}
            ).status_code,
            409,
        )

    def test_empty_or_control_caption_rejected(self):
        d, h = device()
        pub, body, key = new_job(d)
        for caption in ('', '\x01bad'):
            body['caption'] = caption
            self.assertEqual(
                request(
                    '/publications', 'POST', body, {**owner, 'Idempotency-Key': uuid.uuid4().hex}
                ).status_code,
                400,
            )

    def test_wrong_url_is_not_confirmation(self):
        d, h = device()
        pub, _, _ = new_job(d)
        job = request('/bridge/claim', 'POST', {}, h).get_json()['job']
        leased = {**h, 'X-Job-Lease': job['lease']}
        for phase in ('PUBLISH_STARTED', 'SUBMITTED', 'UI_CONFIRMED'):
            request('/bridge/jobs/' + job['id'] + '/phase', 'POST', {'phase': phase}, leased)
        client.post('/api/bridge/jobs/' + job['id'] + '/evidence', data=PNG, headers=leased)
        proof = dict(
            post_url='https://evil.example/',
            caption=job['payload']['caption'],
            sha256=job['payload']['sha256'],
            verification=VERIFY_OK,
        )
        self.assertEqual(
            request('/bridge/jobs/' + job['id'] + '/complete', 'POST', proof, leased).status_code, 409
        )

    def test_uncertainty_pauses_device(self):
        d, h = device()
        pub, _, _ = new_job(d)
        job = request('/bridge/claim', 'POST', {}, h).get_json()['job']
        leased = {**h, 'X-Job-Lease': job['lease']}
        request('/bridge/jobs/' + job['id'] + '/fail', 'POST', {'code': 'SUBMITTED_UNVERIFIED'}, leased)
        with conn() as c:
            c.execute(
                "insert into ui_jobs(id,device_id,publication_id,account_id,status,payload,available,phase,created_at) values(?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, d['id'], uuid.uuid4().hex, 'fake', 'QUEUED', '{}', 0, 'NEW', 'future'),
            )
        self.assertTrue(request('/bridge/claim', 'POST', {}, h).get_json()['paused'])

    def test_local_login_is_loopback_and_origin_scoped(self):
        with patch.dict(os.environ, {'FAXCLIP_LOCAL_TOKEN': 'RANDOM_TEST_SECRET', 'ALLOW_DEV_AUTH': '0'}):
            c = app.test_client()
            self.assertEqual(c.get('/api/accounts').status_code, 401)
            self.assertEqual(
                c.post(
                    '/api/local-session',
                    json={'token': 'RANDOM_TEST_SECRET'},
                    headers={'Origin': 'https://evil.example'},
                ).status_code,
                403,
            )
            self.assertEqual(c.post('/api/local-session', json={'token': 'wrong'}).status_code, 401)
            self.assertEqual(
                c.post('/api/local-session', json={'token': 'RANDOM_TEST_SECRET'}).status_code, 200
            )
            self.assertEqual(c.get('/api/accounts').status_code, 200)
            self.assertEqual(c.get('/api/accounts', base_url='http://evil.example').status_code, 403)
            self.assertEqual(
                c.get('/api/accounts', environ_overrides={'REMOTE_ADDR': '203.0.113.1'}).status_code, 403
            )


class FakeADB:
    def __init__(self):
        self.serial = 'MODEL_ONLY'
        self.executable = 'MODEL_ONLY_ADB'
        self.sent = False
        self.clicks = 0
        self.caption = None
        self.state = 'IDLE'
        self.commands = []

    def ready(self):
        pass

    def run(self, *args, **kw):
        if args == ('exec-out', 'screencap', '-p'):
            return PNG
        raise AssertionError('Unexpected raw command')

    def shell(self, *args, **kw):
        self.commands.append(args)
        assert 'uiautomator' not in args and 'input' not in args
        if args[:2] == ('dumpsys', 'package'):
            return 'versionName=44.6.4\n'
        if args[:2] == ('content', 'call'):
            method = args[args.index('--method') + 1]
            if method == 'finish_import':
                return 'status=IMPORTED, uri=content://media/external_primary/video/media/101'
            if method == 'configure_job':
                self.caption = base64.b64decode(
                    next(x.split(':', 2)[2] for x in args if x.startswith('caption64:s:'))
                ).decode()
                return 'status=JOB_CONFIGURED'
            raise AssertionError('Retry authorization or unknown provider call')
        if args[:2] == ('am', 'start'):
            return 'Status: ok'
        if args[:2] == ('am', 'broadcast'):
            cmd = args[args.index('cmd') + 1]
            states = {
                'status': 'SERVICE_CONNECTED_V14',
                'verification_job': 'VERIFICATION_JOB_BOUND',
                'verification_candidate': 'PROFILE_CANDIDATE_OPENED',
                'verification_share': 'VERIFICATION_SHARE_OPENED',
                'verification_copy_link': 'COPY_LINK_ACTION_ACCEPTED',
                'verification_reopened': 'MATCHING_POST_REOPENED_BY_URL',
            }
            if cmd == 'verification_inspect':
                state = (
                    'PROFILE_MATCHING_CAPTION_FOUND' if self.sent else 'POST_AUTHOR_OR_CAPTION_NOT_MATCHED'
                )
            elif cmd == 'verification_link_result':
                self.link_calls = getattr(self, 'link_calls', 0) + 1
                state = (
                    ('FRESH_TIKTOK_LINK_CAPTURED;url=' + URL)
                    if self.sent and self.link_calls >= 3
                    else 'BASELINE_CAPTURED;url='
                )
            elif cmd == 'tiktok_open_videos_start':
                self.state = 'VIDEO_TAB_SELECTED'
                state = 'ROUTE_STARTED'
            elif cmd == 'tiktok_share_video_start':
                self.state = 'SHARE_VIDEO_ACTION_ACCEPTED'
                state = 'SHARE_ROUTE_STARTED'
            elif cmd == 'tiktok_editor_next_start':
                self.state = 'EDITOR_NEXT_ACTION_ACCEPTED'
                state = 'EDITOR_NEXT_ROUTE_STARTED'
            elif cmd == 'tiktok_submission_start':
                self.clicks += 1
                self.sent = True
                self.state = 'PUBLICATION_SUBMITTED_UNVERIFIED'
                state = 'SUBMISSION_ROUTE_STARTED'
            elif cmd == 'tiktok_route_state':
                state = self.state
            else:
                state = states[cmd]
            return 'Broadcast completed: data="' + state + '"'
        raise AssertionError('Unexpected shell command ' + str(args))


class QueueBridgeModelTests(unittest.TestCase):
    def test_upload_queue_bridge_publish_verify_and_save_url(self):
        d, h = device()
        pub, body, key = new_job(d)
        job = request('/bridge/claim', 'POST', {}, h).get_json()['job']
        leased = {**h, 'X-Job-Lease': job['lease']}
        b = Bridge(
            'http://127.0.0.1:9999',
            {
                'serial': 'MODEL_ONLY',
                'device_id': d['id'],
                'device_token': d['device_token'],
                'mode': 'ACCESSIBILITY_PUBLISH',
            },
            tempfile.mkdtemp(),
            True,
        )
        b.adb = FakeADB()
        b.download = lambda job: self.download_copy(b, job, pub)

        def call(path, body=None, job=None, method='POST', binary=None):
            r = client.open(
                '/api/bridge' + path,
                method=method,
                headers=leased,
                json=body if binary is None else None,
                data=binary,
                content_type='image/png' if binary is not None else None,
            )
            if not 200 <= r.status_code < 300:
                raise ScreenError('Server rejected ' + path + ' ' + str(r.get_json()))
            return r.get_json()

        b.call = call
        with (
            patch('accessibility_publisher.time.sleep', lambda _: None),
            patch('accessibility_publisher.subprocess.run', return_value=type('R', (), {'returncode': 0})()),
        ):
            b.execute(job)
        self.assertEqual(b.adb.clicks, 1)
        self.assertEqual(b.adb.caption, body['caption'])
        row = next(x for x in request('/publications').get_json() if x['id'] == pub['id'])
        self.assertEqual(row['status'], 'UI_CONFIRMED')
        self.assertEqual(row['external_id'], URL)
        self.assertIsNone(request('/bridge/claim', 'POST', {}, h).get_json()['job'])

    def download_copy(self, b, job, pub):
        with conn() as c:
            name = c.execute(
                'select c.source_file from clips c join publications p on p.clip_id=c.id where p.id=?',
                (pub['id'],),
            ).fetchone()['source_file']
        p = b.work / (job['id'] + '.mp4')
        shutil.copy(Path(UPLOAD) / name, p)
        return p

    def test_hash_mismatch_stops_before_import(self):
        b = type('B', (), {'adb': FakeADB(), 'check_lease': lambda self: None})()
        p = AccessibilityPublisher(b, {'id': str(uuid.uuid4()), 'payload': {'sha256': '0' * 64, 'bytes': 1}})
        with tempfile.NamedTemporaryFile() as f:
            f.write(b'changed')
            f.flush()
            with self.assertRaisesRegex(ScreenError, 'SOURCE_HASH'):
                p.import_and_configure(f.name)
        self.assertEqual(b.adb.commands, [])

    def test_non_tiktok_redirect_rejected(self):
        with self.assertRaises(ScreenError):
            AccessibilityPublisher.canonical('https://evil.example/')


if __name__ == '__main__':
    unittest.main()
