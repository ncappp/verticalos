"""Owner-only queue cleanup and environment-assisted cold-start enrollment restore."""

import base64, hashlib, json, os, re, time, uuid
from flask import request, jsonify, g

CONFIRM = 'DELETE_ALL_ATTEMPTS'
COLUMNS = {
    'devices': [
        'id',
        'name',
        'model',
        'connection',
        'status',
        'battery',
        'token_hash',
        'last_seen',
        'created_at',
    ],
    'accounts': ['id', 'platform', 'username', 'niche', 'audience', 'status', 'device_id', 'created_at'],
    'tasks': [
        'id',
        'title',
        'target',
        'done',
        'unit',
        'deadline',
        'created_at',
        'period',
        'period_start',
        'period_end',
    ],
    'ui_account_media_guard': ['platform', 'username', 'sha256', 'publication_id'],
    'ui_media_guard': ['device_id', 'username', 'sha256', 'publication_id'],
}


def apply_bootstrap(conn, now):
    raw = os.getenv('FAXCLIP_BOOTSTRAP_B64', '').strip()
    if not raw:
        return
    if len(raw) > 2 * 1024 * 1024:
        raise RuntimeError('FaxClip bootstrap exceeds size limit')
    try:
        data = json.loads(base64.b64decode(raw, validate=True))
        if data.get('format') != 'FAXCLIP_ENROLLMENT_BACKUP_V1':
            raise ValueError()
        tables = data['tables']
        if not isinstance(tables, dict) or set(tables) - set(COLUMNS):
            raise ValueError()
        for table, rows in tables.items():
            if not isinstance(rows, list) or len(rows) > 10000:
                raise ValueError()
            for row in rows:
                if not isinstance(row, dict) or set(row) - set(COLUMNS[table]):
                    raise ValueError()
                if table == 'devices' and (
                    not re.fullmatch(r'[a-f0-9-]{36}', row.get('id', ''))
                    or not re.fullmatch(r'[a-f0-9]{64}', row.get('token_hash', ''))
                ):
                    raise ValueError()
                if table.startswith('ui_') and not re.fullmatch(r'[a-f0-9]{64}', row.get('sha256', '')):
                    raise ValueError()
    except Exception:
        raise RuntimeError('Invalid FaxClip bootstrap; restore refused') from None
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with conn() as c:
        c.execute(
            'CREATE TABLE IF NOT EXISTS faxclip_bootstrap_receipts(digest TEXT PRIMARY KEY,applied_at TEXT)'
        )
        c.execute('BEGIN IMMEDIATE')
        if c.execute('select 1 from faxclip_bootstrap_receipts where digest=?', (digest,)).fetchone():
            return
        # Never merge a snapshot into a live database or resurrect previously removed profiles.
        if (
            c.execute('select 1 from devices limit 1').fetchone()
            or c.execute('select 1 from accounts limit 1').fetchone()
        ):
            return
        for table in COLUMNS:
            for original in tables.get(table, []):
                row = dict(original)
                if table == 'devices':
                    row.update(status='PENDING', last_seen=None, battery=0)
                cols = [x for x in COLUMNS[table] if x in row]
                quoted = ','.join('"' + x + '"' for x in cols)
                marks = ','.join('?' for _ in cols)
                c.execute(
                    'INSERT INTO "' + table + '" (' + quoted + ') VALUES (' + marks + ')',
                    [row[x] for x in cols],
                )
        c.execute('insert into faxclip_bootstrap_receipts values(?,?)', (digest, now()))
    print('FaxClip: enrollment backup restored; old publication queue was not restored.', flush=True)


def register_maintenance(app, conn, now, uploads):
    with conn() as c:
        c.execute(
            'CREATE TABLE IF NOT EXISTS ui_deleted_jobs(id TEXT PRIMARY KEY,device_id TEXT NOT NULL,deleted_at TEXT NOT NULL)'
        )

    def snapshot(c):
        rows = [dict(r) for r in c.execute('select id,status,lease_until,phase from ui_jobs order by id')]
        pubs = [r['id'] for r in c.execute('select id from publications order by id')]
        legacy = [dict(r) for r in c.execute('select id,status,locked_at from device_jobs order by id')]
        fingerprint = hashlib.sha256(json.dumps([rows, pubs, legacy], sort_keys=True).encode()).hexdigest()
        # Do not stop live phone actions by deleting their lease. Require a safe manager pause first.
        active = [
            x
            for x in rows
            if x['status'] in ('RUNNING', 'VERIFYING')
            and (x['lease_until'] is None or x['lease_until'] > time.time())
        ]
        active += [x for x in legacy if x['status'] == 'RUNNING']
        return dict(
            fingerprint=fingerprint, publications=len(pubs), jobs=len(rows) + len(legacy), active=len(active)
        )

    @app.get('/api/maintenance/attempts-preview')
    def attempts_preview():
        with conn() as c:
            return jsonify(snapshot(c))

    @app.post('/api/maintenance/clear-attempts')
    def clear_attempts():
        x = request.get_json(silent=True) or {}
        if x.get('confirmation') != CONFIRM:
            return jsonify(error='Подтверждение удаления отсутствует'), 400
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            state = snapshot(c)
            if state['active']:
                return jsonify(
                    error='Телефон выполняет операцию. Сначала безопасно остановите менеджер; после завершения операции и истечения аренды повторите очистку.'
                ), 409
            if x.get('fingerprint') != state['fingerprint']:
                return jsonify(error='Очередь изменилась. Откройте подтверждение очистки заново.'), 409
            evidence = [
                r['evidence'] for r in c.execute('select evidence from ui_jobs where evidence is not null')
            ]
            c.execute('insert or ignore into ui_deleted_jobs select id,device_id,? from ui_jobs', (now(),))
            # Keep duplicate guards and consumed idempotency keys. Deleting history is not permission to repost.
            c.execute('delete from ui_jobs')
            c.execute('delete from device_jobs')
            c.execute('delete from publications')
            c.execute(
                'insert into audit_log values(?,?,?,?,?,?)',
                (
                    str(uuid.uuid4()),
                    'clear_attempts',
                    'queue',
                    None,
                    json.dumps(
                        {
                            'publications': state['publications'],
                            'jobs': state['jobs'],
                            'owner_id': g.telegram_user['id'],
                        }
                    ),
                    now(),
                ),
            )
        for name in evidence:
            if isinstance(name, str) and re.fullmatch(r'[a-f0-9-]{36}\.png', name):
                try:
                    os.unlink(os.path.join(os.path.dirname(uploads), 'evidence', name))
                except OSError:
                    pass
        return jsonify(
            ok=True, deleted_publications=state['publications'], deleted_jobs=state['jobs'], queue_empty=True
        )

    @app.get('/api/bridge/queue-state')
    def bridge_queue_state():
        with conn() as c:
            pending = c.execute(
                "select count(*) n from ui_jobs where device_id=? and status in ('QUEUED','RUNNING','VERIFYING','NEEDS_REVIEW')",
                (g.bridge_device['id'],),
            ).fetchone()['n']
            legacy = c.execute(
                "select count(*) n from device_jobs where device_id=? and status in ('QUEUED','RUNNING')",
                (g.bridge_device['id'],),
            ).fetchone()['n']
        return jsonify(queue_empty=pending + legacy == 0, pending_jobs=pending + legacy)

    @app.get('/api/maintenance/enrollment-backup')
    def enrollment_backup():
        with conn() as c:
            c.execute('BEGIN')
            tables = {
                table: [
                    dict(row)
                    for row in c.execute(
                        'select ' + ','.join('"' + col + '"' for col in columns) + ' from "' + table + '"'
                    )
                ]
                for table, columns in COLUMNS.items()
            }
        # Contains verifier hashes, never raw device tokens. Still treat as a private file.
        return jsonify(format='FAXCLIP_ENROLLMENT_BACKUP_V1', created_at=now(), tables=tables)
