"""Durable background jobs: table core_jobs, leases, retries with backoff, periodic scheduler.

Runs embedded in the web process by default (FAXCLIP_WORKER=embedded, works on Render Free)
or as a separate process: `python worker.py` with FAXCLIP_WORKER=off on the web service.
"""

import json
import logging
import os
import threading
import time
import traceback
import uuid

import db as dbmod
import tenancy

log = logging.getLogger('faxclip.jobs')
HANDLERS = {}
PERIODIC = []  # (name, every_seconds, fn(tenant) -> None) evaluated per tenant
_state = {'started': False, 'heartbeat': 0.0, 'processed': 0, 'failed': 0, 'mode': None}
LEASE = 300
_wake = threading.Event()


def handler(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn

    return deco


def periodic(name, every):
    def deco(fn):
        PERIODIC.append((name, every, fn))
        return fn

    return deco


def _now_iso():
    return tenancy.now()


def enqueue(kind, payload=None, tenant=None, delay=0, max_attempts=5, dedupe=None):
    """Queue a job. With `dedupe`, an identical queued/running job is not duplicated."""
    tenant = tenant or dbmod.current_tenant()
    with tenancy.core() as c:
        if (
            dedupe
            and c.execute(
                "select 1 from core_jobs where dedupe=? and tenant=? and status in ('queued','running')",
                (dedupe, tenant),
            ).fetchone()
        ):
            return None
        jid = str(uuid.uuid4())
        c.execute(
            'insert into core_jobs(id,tenant,kind,payload,status,attempts,max_attempts,run_at,dedupe,created_at,updated_at) '
            "values(?,?,?,?,'queued',0,?,?,?,?,?)",
            (
                jid,
                tenant,
                kind,
                json.dumps(payload or {}, ensure_ascii=False),
                max_attempts,
                time.time() + delay,
                dedupe,
                _now_iso(),
                _now_iso(),
            ),
        )
    _wake.set()
    return jid


def active(kind, tenant=None):
    tenant = tenant or dbmod.current_tenant()
    with tenancy.core() as c:
        return bool(
            c.execute(
                "select 1 from core_jobs where kind=? and tenant=? and status in ('queued','running')",
                (kind, tenant),
            ).fetchone()
        )


def _claim():
    t = time.time()
    with tenancy.core() as c:
        if dbmod.IS_PG:
            r = c.execute(
                "update core_jobs set status='running',locked_until=?,attempts=attempts+1,updated_at=? where id=("
                "select id from core_jobs where (status='queued' and run_at<=?) or (status='running' and locked_until<?) "
                'order by run_at limit 1 for update skip locked) returning *',
                (t + LEASE, _now_iso(), t, t),
            ).fetchone()
        else:
            c.execute('BEGIN IMMEDIATE')
            r = c.execute(
                "select id from core_jobs where (status='queued' and run_at<=?) or (status='running' and locked_until<?) "
                'order by run_at limit 1',
                (t, t),
            ).fetchone()
            if r:
                c.execute(
                    "update core_jobs set status='running',locked_until=?,attempts=attempts+1,updated_at=? where id=?",
                    (t + LEASE, _now_iso(), r['id']),
                )
                r = c.execute('select * from core_jobs where id=?', (r['id'],)).fetchone()
        return dict(r) if r else None


def _finish(job, error=None):
    with tenancy.core() as c:
        if error is None:
            c.execute(
                "update core_jobs set status='done',locked_until=null,last_error=null,updated_at=? where id=?",
                (_now_iso(), job['id']),
            )
            return 'done'
        if job['attempts'] >= job['max_attempts']:
            c.execute(
                "update core_jobs set status='dead',locked_until=null,last_error=?,updated_at=? where id=?",
                (error[:4000], _now_iso(), job['id']),
            )
            return 'dead'
        backoff = min(3600, 30 * 2 ** max(0, job['attempts'] - 1))
        c.execute(
            "update core_jobs set status='queued',locked_until=null,run_at=?,last_error=?,updated_at=? where id=?",
            (time.time() + backoff, error[:4000], _now_iso(), job['id']),
        )
        return 'retry'


def run_one():
    job = _claim()
    if not job:
        return False
    fn = HANDLERS.get(job['kind'])
    try:
        if not fn:
            raise RuntimeError('Нет обработчика для задачи ' + job['kind'])
        with dbmod.use_tenant(job['tenant']):
            fn(json.loads(job['payload'] or '{}'))
        _finish(job)
        _state['processed'] += 1
    except Exception as exc:
        _state['failed'] += 1
        detail = traceback.format_exc()
        status = _finish(job, f'{type(exc).__name__}: {exc}\n{detail}')
        log.warning('job %s %s failed (%s): %s', job['kind'], job['id'], status, exc)
        if status == 'dead':
            try:
                import monitoring

                monitoring.record(
                    'job', f"Задача «{job['kind']}» не выполнилась", detail, job['kind'], job['tenant']
                )
            except Exception:
                pass
    return True


def schedule_periodic():
    """Enqueue periodic jobs for every tenant whose interval elapsed (idempotent via dedupe keys)."""
    t = time.time()
    for name, every, _fn in PERIODIC:
        slot = int(t // every)
        for tenant in tenancy.tenants():
            enqueue('periodic', {'name': name}, tenant=tenant, max_attempts=2, dedupe=f'{name}:{slot}')


@handler('periodic')
def _run_periodic(payload):
    for name, _every, fn in PERIODIC:
        if name == payload.get('name'):
            fn(dbmod.current_tenant())


def cleanup():
    cutoff = time.time() - 7 * 86400
    with tenancy.core() as c:
        c.execute("delete from core_jobs where status in ('done','cancelled') and run_at<?", (cutoff,))
        c.execute(
            "delete from core_jobs where status='done' and kind='periodic' and run_at<?",
            (time.time() - 86400,),
        )


def loop(stop=None):
    last_sched = last_clean = 0
    while not (stop and stop.is_set()):
        _state['heartbeat'] = time.time()
        try:
            if time.time() - last_sched > 60:
                last_sched = time.time()
                schedule_periodic()
            if time.time() - last_clean > 3600:
                last_clean = time.time()
                cleanup()
            busy = run_one()
        except Exception:
            log.exception('job loop error')
            busy = False
        if not busy:
            _wake.wait(2)
            _wake.clear()


def start_embedded():
    mode = os.getenv('FAXCLIP_WORKER', 'embedded')
    _state['mode'] = mode
    if mode != 'embedded' or _state['started']:
        return
    _state['started'] = True
    threading.Thread(target=loop, daemon=True, name='faxclip-jobs').start()


def status():
    with tenancy.core() as c:
        counts = {
            r['status']: r['n'] for r in c.execute('select status,count(*) n from core_jobs group by status')
        }
    return {
        'mode': _state['mode'] or os.getenv('FAXCLIP_WORKER', 'embedded'),
        'heartbeat_age': round(time.time() - _state['heartbeat'], 1) if _state['heartbeat'] else None,
        'processed': _state['processed'],
        'failed': _state['failed'],
        'counts': counts,
    }


@handler('tg_send')
def _tg_send(payload):
    import requests

    import re

    token = os.getenv('TELEGRAM_BOT_TOKEN', '')
    if not re.fullmatch(r'\d{5,15}:[A-Za-z0-9_-]{30,}', token) or not payload.get('chat_id'):
        return
    r = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        data={
            'chat_id': payload['chat_id'],
            'text': payload.get('text', '')[:4000],
            'disable_web_page_preview': 'true',
        },
        timeout=15,
    )
    if r.status_code == 403:
        return  # the user blocked the bot: retrying will not help
    r.raise_for_status()
