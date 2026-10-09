"""Admin panel API (/api/admin/*): users, plans & limits, feature switches, registration,
maintenance mode, announcement, Telegram broadcast, error log, background jobs, audit."""

import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from flask import g, jsonify, request

import db as dbmod
import jobs
import tenancy


def _iso_ago(**kw):
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def tenant_stats(tenant):
    out = {'accounts': 0, 'devices': 0, 'devices_online': 0, 'posts_today': 0, 'publications': 0}
    try:
        with dbmod.connect(tenant) as c:
            out['accounts'] = c.execute('select count(*) from accounts').fetchone()[0]
            out['devices'] = c.execute('select count(*) from devices').fetchone()[0]
            out['devices_online'] = c.execute(
                "select count(*) from devices where status='ONLINE'"
            ).fetchone()[0]
            out['publications'] = c.execute('select count(*) from publications').fetchone()[0]
            out['posts_today'] = c.execute(
                'select count(*) from publications where created_at>=?', (tenancy.now()[:10],)
            ).fetchone()[0]
    except Exception as exc:
        out['error'] = str(exc)[:200]
    return out


def register_admin(app):
    @app.before_request
    def admin_guard():
        if request.path.startswith('/api/admin/') and not getattr(g, 'is_admin', False):
            return jsonify(error='Только для администратора'), 403

    def me():
        return str((getattr(g, 'telegram_user', None) or {}).get('id', ''))

    def body():
        x = request.get_json(silent=True)
        return x if isinstance(x, dict) else {}

    @app.get('/api/session')
    def session_info():
        prof = getattr(g, 'profile', None) or {'plan': 'unlimited'}
        eff = tenancy.effective(prof)
        if getattr(g, 'is_admin', False):
            eff['features'] = {k: True for k in tenancy.FEATURES}
        return jsonify(
            tenant=getattr(g, 'tenant', 'main'),
            is_admin=bool(getattr(g, 'is_admin', False)),
            plan=eff['plan'],
            plan_name=eff['plan_name'],
            limits=eff['limits'],
            limit_names=tenancy.LIMITS,
            features=eff['features'],
            announcement=tenancy.get_setting('announcement'),
            usage={k: v for k, v in tenant_stats(dbmod.current_tenant()).items() if k != 'error'},
        )

    # ------------------------------------------------------------ overview
    @app.get('/api/admin/overview')
    def admin_overview():
        with tenancy.core() as c:
            q = lambda sql, *a: c.execute(sql, a).fetchone()[0]  # noqa: E731
            users = {
                'total': q('select count(*) from core_users'),
                'new_24h': q('select count(*) from core_users where created_at>=?', _iso_ago(days=1)),
                'new_7d': q('select count(*) from core_users where created_at>=?', _iso_ago(days=7)),
                'active_24h': q('select count(*) from core_users where last_seen>=?', _iso_ago(days=1)),
                'active_7d': q('select count(*) from core_users where last_seen>=?', _iso_ago(days=7)),
                'blocked': q('select count(*) from core_users where blocked=1'),
            }
            plans = {
                r['plan']: r['n'] for r in c.execute('select plan,count(*) n from core_users group by plan')
            }
            errors = {
                'open': q('select count(*) from core_errors where resolved=0'),
                'last_24h': q('select count(*) from core_errors where last_seen>=?', _iso_ago(days=1)),
            }
        totals = {'accounts': 0, 'devices': 0, 'devices_online': 0, 'posts_today': 0}
        for t in tenancy.tenants()[:300]:
            s = tenant_stats(t)
            for k in totals:
                totals[k] += s.get(k, 0)
        return jsonify(
            users=users,
            plans=plans,
            errors=errors,
            totals=totals,
            jobs=jobs.status(),
            system={
                'database': 'Supabase / PostgreSQL' if dbmod.IS_PG else 'SQLite (локальный файл)',
                'sentry': bool(app.extensions.get('faxclip_sentry')),
                'telegram_bot': bool(os.getenv('TELEGRAM_BOT_TOKEN')),
                'registration': tenancy.get_setting('registration'),
                'maintenance': tenancy.get_setting('maintenance'),
            },
        )

    # --------------------------------------------------------------- users
    @app.get('/api/admin/users')
    def admin_users():
        qtext = (request.args.get('q') or '').strip().lower()[:64]
        flt = request.args.get('filter') or 'all'
        off = max(0, int(request.args.get('offset') or 0))
        where, args = [], []
        if qtext:
            where.append(
                "(lower(coalesce(username,'')) like ? or lower(coalesce(first_name,'')) like ? or tg_id like ?)"
            )
            args += [f'%{qtext}%'] * 3
        if flt == 'blocked':
            where.append('blocked=1')
        elif flt == 'active':
            where.append('last_seen>=?')
            args.append(_iso_ago(days=7))
        elif flt in ('free', 'pro', 'unlimited'):
            where.append('plan=?')
            args.append(flt)
        w = ('where ' + ' and '.join(where)) if where else ''
        with tenancy.core() as c:
            total = c.execute(f'select count(*) from core_users {w}', args).fetchone()[0]
            rows = [
                dict(r)
                for r in c.execute(
                    f'select * from core_users {w} order by last_seen desc limit 50 offset ?', args + [off]
                )
            ]
        admins = tenancy.admin_ids()
        for r in rows:
            r['stats'] = tenant_stats(r['tenant'])
            r['is_admin'] = r['tg_id'] in admins
            r['effective'] = tenancy.effective(r)
            r['limits'] = json.loads(r['limits'] or '{}')
            r['features'] = json.loads(r['features'] or '{}')
        return jsonify(users=rows, total=total, offset=off)

    @app.patch('/api/admin/users/<tg_id>')
    def admin_user_update(tg_id):
        if not re.fullmatch(r'\d{1,20}', tg_id):
            return jsonify(error='Неверный ID'), 400
        x = body()
        plans = tenancy.get_setting('plans') or {}
        sets, args = [], []
        if 'plan' in x:
            if x['plan'] not in plans:
                return jsonify(error='Нет такого тарифа'), 400
            sets.append('plan=?')
            args.append(x['plan'])
        if 'blocked' in x:
            if tg_id in tenancy.admin_ids() and x['blocked']:
                return jsonify(error='Нельзя заблокировать администратора'), 400
            sets += ['blocked=?', 'block_reason=?']
            args += [1 if x['blocked'] else 0, str(x.get('block_reason') or '')[:200]]
        if 'limits' in x and isinstance(x['limits'], dict):
            lim = {
                k: max(0, int(v))
                for k, v in x['limits'].items()
                if k in tenancy.LIMITS and v not in (None, '')
            }
            sets.append('limits=?')
            args.append(json.dumps(lim))
        if 'features' in x and isinstance(x['features'], dict):
            fe = {k: bool(v) for k, v in x['features'].items() if k in tenancy.FEATURES}
            sets.append('features=?')
            args.append(json.dumps(fe))
        if 'note' in x:
            sets.append('note=?')
            args.append(str(x['note'] or '')[:500])
        if not sets:
            return jsonify(error='Нечего менять'), 400
        with tenancy.core() as c:
            n = c.execute(f'update core_users set {",".join(sets)} where tg_id=?', args + [tg_id]).rowcount
        if not n:
            return jsonify(error='Пользователь не найден'), 404
        tenancy.admin_audit(me(), 'user.update', tg_id, x)
        return jsonify(ok=True)

    @app.post('/api/admin/users/<tg_id>/message')
    def admin_user_message(tg_id):
        text = str(body().get('text') or '').strip()[:3500]
        if not text or not re.fullmatch(r'\d{1,20}', tg_id):
            return jsonify(error='Введите текст'), 400
        jobs.enqueue('tg_send', {'chat_id': tg_id, 'text': text}, tenant='main', max_attempts=4)
        tenancy.admin_audit(me(), 'user.message', tg_id, {'text': text[:200]})
        return jsonify(ok=True)

    # ------------------------------------------------- plans and settings
    @app.get('/api/admin/settings')
    def admin_settings():
        out = {k: tenancy.get_setting(k) for k in ('registration', 'maintenance', 'announcement', 'plans')}
        out.update(feature_names=tenancy.FEATURES, limit_names=tenancy.LIMITS)
        return jsonify(out)

    @app.put('/api/admin/settings/<key>')
    def admin_settings_put(key):
        x = body()
        if key == 'registration':
            v = {
                'mode': 'closed' if x.get('mode') == 'closed' else 'open',
                'default_plan': x.get('default_plan')
                if x.get('default_plan') in (tenancy.get_setting('plans') or {})
                else 'free',
            }
        elif key == 'maintenance':
            v = {'on': bool(x.get('on')), 'message': str(x.get('message') or '')[:300]}
        elif key == 'announcement':
            v = {
                'active': bool(x.get('active')),
                'text': str(x.get('text') or '')[:500],
                'level': x.get('level') if x.get('level') in ('info', 'warning', 'success') else 'info',
            }
        elif key == 'plans':
            v = {}
            for pid, p in (x.get('plans') or {}).items():
                if not re.fullmatch(r'[a-z0-9_]{2,20}', pid) or not isinstance(p, dict):
                    return jsonify(error='Неверный код тарифа: ' + str(pid)), 400
                v[pid] = {
                    'name': str(p.get('name') or pid)[:40],
                    'limits': {k: max(0, int((p.get('limits') or {}).get(k) or 0)) for k in tenancy.LIMITS},
                    'features': {k: bool((p.get('features') or {}).get(k, True)) for k in tenancy.FEATURES},
                }
            if 'free' not in v:
                return jsonify(error='Тариф free обязателен (назначается новым пользователям)'), 400
        else:
            return jsonify(error='Неизвестная настройка'), 404
        tenancy.set_setting(key, v)
        tenancy.admin_audit(me(), 'settings.' + key, key, v)
        return jsonify(ok=True, value=v)

    @app.post('/api/admin/broadcast')
    def admin_broadcast():
        x = body()
        text = str(x.get('text') or '').strip()[:3500]
        if len(text) < 3:
            return jsonify(error='Введите текст рассылки'), 400
        only = x.get('audience') or 'all'
        with tenancy.core() as c:
            q = 'select tg_id from core_users where blocked=0'
            args = []
            if only == 'active':
                q += ' and last_seen>=?'
                args.append(_iso_ago(days=7))
            ids = [r['tg_id'] for r in c.execute(q, args)]
        if x.get('dry_run'):
            return jsonify(ok=True, recipients=len(ids))
        for i, tid in enumerate(ids):
            # ~20 messages/second keeps us under Telegram's broadcast limits
            jobs.enqueue(
                'tg_send', {'chat_id': tid, 'text': text}, tenant='main', delay=i * 0.05, max_attempts=4
            )
        tenancy.admin_audit(me(), 'broadcast', only, {'recipients': len(ids), 'text': text[:200]})
        return jsonify(ok=True, recipients=len(ids))

    # ------------------------------------------------------------- errors
    @app.get('/api/admin/errors')
    def admin_errors():
        st = request.args.get('status') or 'open'
        w = 'where resolved=0' if st == 'open' else ('where resolved=1' if st == 'resolved' else '')
        with tenancy.core() as c:
            rows = [
                dict(r) for r in c.execute(f'select * from core_errors {w} order by last_seen desc limit 100')
            ]
        return jsonify(errors=rows)

    @app.post('/api/admin/errors/<eid>/<action>')
    def admin_error_action(eid, action):
        with tenancy.core() as c:
            if action == 'resolve':
                c.execute('update core_errors set resolved=1 where id=?', (eid,))
            elif action == 'reopen':
                c.execute('update core_errors set resolved=0 where id=?', (eid,))
            elif action == 'delete':
                c.execute('delete from core_errors where id=?', (eid,))
            else:
                return jsonify(error='Неизвестное действие'), 400
        tenancy.admin_audit(me(), 'error.' + action, eid)
        return jsonify(ok=True)

    @app.post('/api/admin/errors/test')
    def admin_error_test():
        fp = __import__('monitoring').record(
            'server', 'Тестовая ошибка из админки', 'Проверка мониторинга', '/api/admin/errors/test'
        )
        return jsonify(ok=True, id=fp)

    # --------------------------------------------------------------- jobs
    @app.get('/api/admin/jobs')
    def admin_jobs():
        st = request.args.get('status') or ''
        w, a = ('where status=?', [st]) if st else ('', [])
        with tenancy.core() as c:
            rows = [
                dict(r)
                for r in c.execute(
                    f'select id,tenant,kind,status,attempts,max_attempts,run_at,last_error,created_at,updated_at '
                    f'from core_jobs {w} order by updated_at desc limit 100',
                    a,
                )
            ]
        for r in rows:
            r['last_error'] = (r['last_error'] or '').split('\n')[0][:300]
        return jsonify(jobs=rows, status=jobs.status())

    @app.post('/api/admin/jobs/<jid>/<action>')
    def admin_job_action(jid, action):
        with tenancy.core() as c:
            if action == 'retry':
                n = c.execute(
                    "update core_jobs set status='queued',attempts=0,run_at=?,updated_at=? where id=? and status in ('dead','failed','cancelled')",
                    (time.time(), tenancy.now(), jid),
                ).rowcount
            elif action == 'cancel':
                n = c.execute(
                    "update core_jobs set status='cancelled',updated_at=? where id=? and status='queued'",
                    (tenancy.now(), jid),
                ).rowcount
            else:
                return jsonify(error='Неизвестное действие'), 400
        if not n:
            return jsonify(error='Задачу нельзя изменить в текущем статусе'), 409
        tenancy.admin_audit(me(), 'job.' + action, jid)
        return jsonify(ok=True)

    @app.get('/api/admin/audit')
    def admin_audit_log():
        with tenancy.core() as c:
            rows = [
                dict(r)
                for r in c.execute('select * from core_admin_audit order by created_at desc limit 200')
            ]
        return jsonify(items=rows)
