"""Ban protection — TEST MODE (dry run).

Reads accounts, the publication queue and warm-up activity and shows what protection *would* do:
which posts it would delay or hold, which accounts it would pause and why. It never writes:
the connection is switched to read-only (SQLite `query_only`, PostgreSQL `READ ONLY` transaction).
"""

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import jsonify, request

import db as dbmod

DONE = ('UI_CONFIRMED', 'PUBLISHED')
FAILED = ('FAILED', 'NEEDS_REVIEW', 'ERROR')
UPCOMING = ('QUEUED', 'SCHEDULED')
STAGES = {'young': 'Новый', 'warming': 'Прогревается', 'hot': 'Прогретый'}

DEFAULT_RULES = {
    'posts_per_day': {'young': 1, 'warming': 2, 'hot': 4},
    'likes_per_day': {'young': 30, 'warming': 80, 'hot': 150},
    'follows_per_day': {'young': 0, 'warming': 10, 'hot': 25},
    'min_gap_minutes': 120,
    'quiet_from': 1,
    'quiet_to': 7,
    'min_warm_sessions': 3,
    'pause_after_captcha_hours': 24,
    'max_failures_24h': 3,
    'max_accounts_per_device': 3,
    'device_stagger_minutes': 10,
    'require_proxy': True,
}


def merge_rules(raw):
    r = json.loads(json.dumps(DEFAULT_RULES))
    if not isinstance(raw, dict):
        return r

    def num(v, lo, hi, d):
        try:
            return max(lo, min(hi, int(v)))
        except (TypeError, ValueError):
            return d

    for k in ('posts_per_day', 'likes_per_day', 'follows_per_day'):
        if isinstance(raw.get(k), dict):
            for st in STAGES:
                r[k][st] = num(raw[k].get(st), 0, 1000, r[k][st])
    for k, lo, hi in (
        ('min_gap_minutes', 0, 1440),
        ('quiet_from', 0, 23),
        ('quiet_to', 0, 23),
        ('min_warm_sessions', 0, 100),
        ('pause_after_captcha_hours', 0, 240),
        ('max_failures_24h', 1, 50),
        ('max_accounts_per_device', 1, 50),
        ('device_stagger_minutes', 0, 240),
    ):
        if k in raw:
            r[k] = num(raw[k], lo, hi, r[k])
    if 'require_proxy' in raw:
        r['require_proxy'] = bool(raw['require_proxy'])
    return r


def _dt(v):
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _readonly(c):
    if dbmod.IS_PG:
        c.execute('set transaction read only')
    else:
        c.execute('pragma query_only=ON')
    return c


def analyze(c, rules, now=None):
    now = now or datetime.now(timezone.utc)
    day_ago, two_days = now - timedelta(days=1), now - timedelta(days=2)
    q = lambda sql, *a: [dict(r) for r in c.execute(sql, a).fetchall()]  # noqa: E731
    accounts = q('select id,platform,username,status,device_id,created_at from accounts order by created_at')
    profiles = {
        r['account_id']: r
        for r in q('select account_id,work_mode,successful_sessions from ws_account_profiles')
    }
    devices = {r['id']: r for r in q('select id,name,status from devices')}
    tzs = {r['device_id']: r['timezone'] for r in q('select device_id,timezone from ws_device_profiles')}
    proxies = {
        r['device_id']: r
        for r in q(
            'select device_id,status,last_check_ok,last_check_egress_ip from ws_proxies where device_id is not null'
        )
    }
    pubs = q(
        'select id,account_id,clip_id,status,scheduled_at,published_at,error,created_at from publications'
    )
    tasks = q('select account_id,status,plan_date,actions,finished_at,created_at from ws_tasks')
    captions = {
        r['clip_id']: (r['caption'] or '').strip() for r in q('select clip_id,caption from clip_captions')
    }
    guard = q('select username,sha256 from ui_account_media_guard')

    per_device = {}
    for a in accounts:
        per_device.setdefault(a['device_id'], []).append(a)
    sha_users = {}
    for gk in guard:
        sha_users.setdefault(gk['sha256'], set()).add(gk['username'])
    dup_by_user = {}
    for sha, users in sha_users.items():
        if len(users) > 1:
            for u in users:
                dup_by_user[u] = max(dup_by_user.get(u, 0), len(users))
    cap_accounts = {}
    for p in pubs:
        cap = captions.get(p['clip_id'])
        if cap and len(cap) > 15 and (_dt(p['created_at']) or now) > now - timedelta(days=7):
            cap_accounts.setdefault(cap, set()).add(p['account_id'])

    out_accounts, queue, device_warnings = [], [], []
    for did, accs in per_device.items():
        if did and len(accs) > rules['max_accounts_per_device']:
            device_warnings.append(
                {
                    'device': (devices.get(did) or {}).get('name') or did,
                    'text': f'{len(accs)} аккаунтов на одном телефоне (рекомендуем не больше {rules["max_accounts_per_device"]}).',
                }
            )
    ips = {}
    for did, p in proxies.items():
        if p.get('last_check_egress_ip'):
            ips.setdefault(p['last_check_egress_ip'], []).append(did)
    for ip, dids in ips.items():
        if len(dids) > 1:
            device_warnings.append(
                {
                    'device': ', '.join((devices.get(d) or {}).get('name') or d for d in dids),
                    'text': f'Разные телефоны выходят в интернет с одного IP {ip}.',
                }
            )

    state = {}
    for a in accounts:
        aid = a['id']
        prof = profiles.get(aid) or {}
        added = _dt(a['created_at']) or now
        age = max(0, (now - added).days)
        stage = (
            prof.get('work_mode')
            if prof.get('work_mode') in STAGES
            else ('young' if age < 7 else 'warming' if age < 30 else 'hot')
        )
        mine = [p for p in pubs if p['account_id'] == aid]
        done = sorted(
            [_dt(p['published_at']) for p in mine if p['status'] in DONE and _dt(p['published_at'])]
        )
        posts_24h = sum(1 for d in done if d > day_ago)
        fails_24h = sum(1 for p in mine if p['status'] in FAILED and (_dt(p['created_at']) or now) > day_ago)
        my_tasks = [t for t in tasks if t['account_id'] == aid]
        today = now.date().isoformat()
        likes = follows = 0
        for t in my_tasks:
            if t['plan_date'] == today and t['status'] in ('done', 'running'):
                try:
                    acts = json.loads(t['actions'] or '{}')
                except ValueError:
                    acts = {}
                likes += int(acts.get('likes') or 0)
                follows += int(acts.get('follows') or 0)
        warm_done = max(
            int(prof.get('successful_sessions') or 0), sum(1 for t in my_tasks if t['status'] == 'done')
        )
        captcha = [
            _dt(t['finished_at'] or t['created_at'])
            for t in my_tasks
            if t['status'] == 'human_intervention'
            and (_dt(t['finished_at'] or t['created_at']) or now) > two_days
        ]
        pause_until = None
        if captcha and rules['pause_after_captcha_hours']:
            pause_until = max(captcha) + timedelta(hours=rules['pause_after_captcha_hours'])
            if pause_until <= now:
                pause_until = None
        tz = tzs.get(a['device_id']) or 'Europe/Moscow'
        try:
            zone = ZoneInfo(tz)
        except Exception:
            zone, tz = ZoneInfo('Europe/Moscow'), 'Europe/Moscow'

        findings, score = [], 0

        def add(level, text, action, points):
            nonlocal score
            findings.append({'level': level, 'text': text, 'action': action})
            score += points

        lim_posts = rules['posts_per_day'][stage]
        if posts_24h > lim_posts:
            add(
                'high',
                f'{posts_24h} публикаций за 24 ч при лимите {lim_posts} для стадии «{STAGES[stage]}».',
                'Задержала бы следующие публикации до конца суточного окна.',
                30,
            )
        elif posts_24h == lim_posts and lim_posts:
            add(
                'medium',
                f'Суточный лимит публикаций исчерпан ({lim_posts}).',
                'Следующий пост перенесла бы на завтра.',
                10,
            )
        if likes > rules['likes_per_day'][stage]:
            add(
                'high',
                f'{likes} лайков сегодня при лимите {rules["likes_per_day"][stage]}.',
                'Остановила бы лайки в прогреве до завтра.',
                20,
            )
        if follows > rules['follows_per_day'][stage]:
            add(
                'high',
                f'{follows} подписок сегодня при лимите {rules["follows_per_day"][stage]}.',
                'Остановила бы подписки до завтра.',
                20,
            )
        if pause_until:
            add(
                'high',
                f'Капча/блокировка экрана {len(captcha)} раз за 48 ч.',
                f'Поставила бы аккаунт на паузу до {pause_until.astimezone(zone):%d.%m %H:%M}.',
                35,
            )
        if fails_24h >= rules['max_failures_24h']:
            add(
                'high',
                f'{fails_24h} неудачных публикаций за 24 ч.',
                'Остановила бы публикации и попросила проверить аккаунт вручную.',
                25,
            )
        if warm_done < rules['min_warm_sessions'] and not done:
            add(
                'medium',
                f'Прогрето {warm_done} из {rules["min_warm_sessions"]} сессий перед первым постом.',
                'Придержала бы первую публикацию до окончания прогрева.',
                15,
            )
        if dup_by_user.get(a['username']):
            add(
                'medium',
                f'Одно и то же видео опубликовано на {dup_by_user[a["username"]]} аккаунтах.',
                'Предупредила бы: TikTok снижает охват копий. Лучше уникализировать (склейка).',
                10,
            )
        same_cap = [s for s in cap_accounts.values() if aid in s and len(s) > 1]
        if same_cap:
            add(
                'low',
                f'Одинаковое описание на {max(len(s) for s in same_cap)} аккаунтах за 7 дней.',
                'Предупредила бы о повторе описания.',
                5,
            )
        if rules['require_proxy'] and a['device_id'] and a['device_id'] not in proxies:
            add('low', 'Для телефона не настроен прокси.', 'Только предупреждение (прокси — этап 5).', 5)
        p = proxies.get(a['device_id'])
        if p and p.get('last_check_ok') == 0:
            add(
                'medium',
                'Последняя проверка прокси не прошла.',
                'Не запускала бы задачи на телефоне, пока прокси не заработает.',
                15,
            )
        if a['device_id'] and len(per_device.get(a['device_id'], [])) > rules['max_accounts_per_device']:
            add('low', 'На телефоне слишком много аккаунтов.', 'Только предупреждение.', 5)
        score = min(100, score)
        level = 'high' if score >= 40 else 'medium' if score >= 15 else 'low'
        state[aid] = {
            'stage': stage,
            'zone': zone,
            'tz': tz,
            'done': list(done),
            'pause_until': pause_until,
            'blocked': fails_24h >= rules['max_failures_24h'],
            'needs_warm': warm_done < rules['min_warm_sessions'] and not done,
            'limit': lim_posts,
        }
        out_accounts.append(
            {
                'account_id': aid,
                'username': a['username'],
                'platform': a['platform'],
                'device': (devices.get(a['device_id']) or {}).get('name'),
                'stage': stage,
                'stage_name': STAGES[stage],
                'age_days': age,
                'timezone': tz,
                'posts_24h': posts_24h,
                'posts_limit': lim_posts,
                'likes_today': likes,
                'likes_limit': rules['likes_per_day'][stage],
                'follows_today': follows,
                'follows_limit': rules['follows_per_day'][stage],
                'warm_sessions': warm_done,
                'failures_24h': fails_24h,
                'last_post_at': done[-1].isoformat() if done else None,
                'score': score,
                'level': level,
                'findings': findings,
            }
        )

    horizon = now + timedelta(hours=48)
    upcoming = [p for p in pubs if p['status'] in UPCOMING and (_dt(p['scheduled_at']) or now) <= horizon]
    upcoming.sort(key=lambda p: _dt(p['scheduled_at']) or now)
    device_last = {}
    names = {a['id']: a for a in accounts}
    for p in upcoming:
        st = state.get(p['account_id'])
        acc = names.get(p['account_id']) or {}
        planned = max(_dt(p['scheduled_at']) or now, now)
        if not st:
            continue
        reasons, when, decision = [], planned, 'allow'
        if st['blocked']:
            decision = 'block'
            reasons.append('Много неудачных публикаций за сутки — нужна ручная проверка аккаунта.')
        if st['needs_warm']:
            decision = 'block'
            reasons.append('Аккаунт ещё не прогрет перед первой публикацией.')
        if decision != 'block':
            if st['pause_until'] and when < st['pause_until']:
                when = st['pause_until']
                reasons.append('Пауза после капчи.')
            if st['done'] and rules['min_gap_minutes']:
                nxt = st['done'][-1] + timedelta(minutes=rules['min_gap_minutes'])
                if when < nxt:
                    when = nxt
                    reasons.append(f'Интервал между постами меньше {rules["min_gap_minutes"]} мин.')
            window = [d for d in st['done'] if d > when - timedelta(days=1)]
            if st['limit'] and len(window) >= st['limit']:
                when = max(when, sorted(window)[-st['limit']] + timedelta(days=1))
                reasons.append(f'Суточный лимит {st["limit"]} для стадии «{STAGES[st["stage"]]}».')
            dev = acc.get('device_id')
            if dev and rules['device_stagger_minutes'] and dev in device_last:
                nxt = device_last[dev] + timedelta(minutes=rules['device_stagger_minutes'])
                if when < nxt:
                    when = nxt
                    reasons.append(
                        f'Другой аккаунт на этом телефоне публикует меньше чем за {rules["device_stagger_minutes"]} мин.'
                    )
            local = when.astimezone(st['zone'])
            qf, qt = rules['quiet_from'], rules['quiet_to']
            in_quiet = (
                (qf <= local.hour < qt)
                if qf < qt
                else (local.hour >= qf or local.hour < qt)
                if qf != qt
                else False
            )
            if in_quiet:
                local = local.replace(hour=qt, minute=0, second=0, microsecond=0)
                if local.astimezone(timezone.utc) < when:
                    local += timedelta(days=1)
                when = local.astimezone(timezone.utc)
                reasons.append(f'Ночные часы {qf:02d}:00–{qt:02d}:00 по времени телефона.')
            if when > planned + timedelta(minutes=1):
                decision = 'delay'
            st['done'].append(when)
            st['done'].sort()
            if dev:
                device_last[dev] = when
        queue.append(
            {
                'publication_id': p['id'],
                'username': acc.get('username'),
                'planned_at': planned.isoformat(),
                'decision': decision,
                'new_time': when.isoformat() if decision == 'delay' else None,
                'local_time': when.astimezone(st['zone']).strftime('%d.%m %H:%M')
                if decision == 'delay'
                else None,
                'reasons': reasons,
            }
        )

    out_accounts.sort(key=lambda x: -x['score'])
    return {
        'mode': 'test',
        'generated_at': now.isoformat(),
        'rules': rules,
        'summary': {
            'accounts': len(out_accounts),
            'high': sum(1 for a in out_accounts if a['level'] == 'high'),
            'medium': sum(1 for a in out_accounts if a['level'] == 'medium'),
            'low': sum(1 for a in out_accounts if a['level'] == 'low'),
            'queue': len(queue),
            'would_delay': sum(1 for x in queue if x['decision'] == 'delay'),
            'would_block': sum(1 for x in queue if x['decision'] == 'block'),
            'warnings': sum(len(a['findings']) for a in out_accounts) + len(device_warnings),
        },
        'accounts': out_accounts,
        'queue': queue,
        'device_warnings': device_warnings,
    }


def register_antiban(app, conn):
    @app.get('/api/antiban/preview')
    def antiban_preview():
        try:
            raw = json.loads(request.args.get('rules') or '{}')
        except ValueError:
            raw = {}
        rules = merge_rules(raw)
        c = _readonly(conn())
        try:
            data = analyze(c, rules)
        finally:
            c.rollback()
        data['defaults'] = DEFAULT_RULES
        data['stage_names'] = STAGES
        return jsonify(data)
