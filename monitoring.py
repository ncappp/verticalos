"""Error monitoring: own error log (core_errors) + optional Sentry + Telegram alerts to admins."""

import hashlib
import logging
import os
import time
import traceback

from flask import g, jsonify, request
from werkzeug.exceptions import HTTPException

import db as dbmod
import tenancy

log = logging.getLogger('faxclip')
ALERT_EVERY = 3600
_client_rate = {}


def init_sentry(app):
    dsn = os.getenv('SENTRY_DSN', '').strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.flask import FlaskIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[FlaskIntegration()],
            traces_sample_rate=float(os.getenv('SENTRY_TRACES', '0') or 0),
            send_default_pii=False,
            environment=os.getenv('FAXCLIP_ENV', 'production' if os.getenv('RENDER') else 'local'),
        )
        return True
    except Exception:
        log.exception('Sentry init failed')
        return False


def record(kind, title, detail='', path='', tenant=None, alert=True):
    """Store/aggregate an error by fingerprint; alert admins at most once per hour per fingerprint."""
    tenant = tenant or dbmod.current_tenant()
    first_frame = ''
    for line in reversed((detail or '').splitlines()):
        if line.strip().startswith('File "'):
            first_frame = line.strip()
            break
    fp = hashlib.sha256(f'{kind}|{title}|{first_frame}'.encode()).hexdigest()[:20]
    now = tenancy.now()
    try:
        with tenancy.core() as c:
            r = c.execute('select count,last_alert,resolved from core_errors where id=?', (fp,)).fetchone()
            if r:
                c.execute(
                    'update core_errors set count=count+1,last_seen=?,detail=?,path=?,tenant=?,resolved=0 where id=?',
                    (now, (detail or '')[:8000], (path or '')[:300], tenant, fp),
                )
                should_alert = alert and (r['resolved'] or time.time() - (r['last_alert'] or 0) > ALERT_EVERY)
            else:
                c.execute(
                    'insert into core_errors(id,kind,title,detail,path,tenant,count,first_seen,last_seen,last_alert,resolved) '
                    'values(?,?,?,?,?,?,1,?,?,0,0)',
                    (fp, kind, title[:300], (detail or '')[:8000], (path or '')[:300], tenant, now, now),
                )
                should_alert = alert
            if should_alert:
                c.execute('update core_errors set last_alert=? where id=?', (time.time(), fp))
        if should_alert:
            _alert_admins(f'⛔ FaxClip: {title}\n{kind} · {path or "-"} · {tenant}\nКод: {fp}')
    except Exception:
        log.exception('could not record error')
    return fp


def _alert_admins(text):
    if not os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('FAXCLIP_LOCAL_TOKEN'):
        return
    import jobs

    for admin in tenancy.admin_ids():
        jobs.enqueue('tg_send', {'chat_id': admin, 'text': text}, tenant='main', max_attempts=3)


def register_monitoring(app):
    sentry = init_sentry(app)
    app.extensions['faxclip_sentry'] = sentry

    @app.errorhandler(Exception)
    def on_error(exc):
        if isinstance(exc, HTTPException):
            return exc
        detail = traceback.format_exc()
        log.error('Unhandled error on %s %s\n%s', request.method, request.path, detail)
        fp = record(
            'server', f'{type(exc).__name__}: {str(exc)[:160]}', detail, f'{request.method} {request.path}'
        )
        if request.path.startswith('/api/') or request.path.startswith('/l/'):
            return jsonify(
                error='Внутренняя ошибка сервера. Отчёт уже отправлен администратору.', error_id=fp
            ), 500
        return 'Внутренняя ошибка сервера', 500

    @app.post('/api/client-error')
    def client_error():
        """Browser errors from the Mini App (window.onerror / unhandledrejection)."""
        t = getattr(g, 'tenant', 'main')
        window = [x for x in _client_rate.get(t, []) if x > time.time() - 600]
        if len(window) >= 20:
            return jsonify(ok=False, throttled=True), 429
        _client_rate[t] = window + [time.time()]
        x = request.get_json(silent=True) or {}
        msg = str(x.get('message') or 'Ошибка в браузере')[:200]
        detail = '\n'.join(
            str(x.get(k) or '')[:3000] for k in ('stack', 'source', 'page', 'user_agent') if x.get(k)
        )
        record('client', msg, detail, str(x.get('page') or '')[:200], alert=False)
        return jsonify(ok=True)


def watchdog(tenant):
    """Alert the user when a device or Mac agent silently stopped reporting (no explicit disconnect)."""
    import notify

    limit = int(os.getenv('FAXCLIP_SILENT_MINUTES', '10'))
    cutoff = time.time() - limit * 60
    from datetime import datetime

    with dbmod.connect() as c:
        rows = c.execute("select id,name,status,last_seen from devices where status='ONLINE'").fetchall()
        for d in rows:
            try:
                seen = datetime.fromisoformat(d['last_seen']).timestamp() if d['last_seen'] else 0
            except ValueError:
                continue
            key = f'silent:{d["id"]}:{d["last_seen"]}'
            if (
                seen
                and seen < cutoff
                and not c.execute(
                    "select 1 from ws_alerts where event_type='device_silent' and link=?", (key,)
                ).fetchone()
            ):
                notify.emit(
                    c,
                    'warning',
                    'device_silent',
                    f'{d["name"]}: нет связи больше {limit} минут',
                    'Устройство не присылает сигналы. Проверьте, что Mac не спит, USB-кабель подключён, а агент запущен.',
                    'devices',
                    key,
                )
