"""Multi-tenant registry: Telegram users, tenant routing, plans/limits, global settings."""

import json
import os
import time
from datetime import datetime, timezone

import db as dbmod

CORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS core_users(
  tg_id TEXT PRIMARY KEY, tenant TEXT UNIQUE NOT NULL, username TEXT, first_name TEXT, last_name TEXT,
  language TEXT, created_at TEXT NOT NULL, last_seen TEXT, blocked INTEGER DEFAULT 0, block_reason TEXT,
  plan TEXT DEFAULT 'free', limits TEXT DEFAULT '{}', features TEXT DEFAULT '{}', note TEXT,
  requests INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS core_registry(
  kind TEXT NOT NULL, key TEXT NOT NULL, tenant TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(kind, key)
);
CREATE TABLE IF NOT EXISTS core_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS core_admin_audit(
  id TEXT PRIMARY KEY, admin_id TEXT, action TEXT NOT NULL, target TEXT, payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS core_errors(
  id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL, detail TEXT, path TEXT, tenant TEXT,
  count INTEGER DEFAULT 1, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, last_alert REAL DEFAULT 0,
  resolved INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS core_jobs(
  id TEXT PRIMARY KEY, tenant TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 5,
  run_at REAL NOT NULL, locked_until REAL, dedupe TEXT, last_error TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS core_jobs_due ON core_jobs(status, run_at);
CREATE INDEX IF NOT EXISTS core_jobs_dedupe ON core_jobs(dedupe, status);
"""

FEATURES = {
    'assembly': 'Склейка',
    'warmup': 'Прогрев',
    'analytics': 'Аналитика',
    'links': 'Ссылки',
    'screen': 'Экран телефона',
    'antiban': 'Защита от банов',
}
FEATURE_PATHS = [
    (
        'assembly',
        ('/api/recipes', '/api/render-jobs', '/api/sources', '/api/render-install-command', '/api/banners'),
    ),
    ('warmup', ('/api/warmup', '/api/scenarios', '/api/warm-install-command')),
    (
        'analytics',
        ('/api/analytics/v2', '/api/analytics/sync', '/api/analytics/publications', '/api/analytics/manual'),
    ),
    ('links', ('/api/links',)),
    ('antiban', ('/api/antiban',)),
]
LIMITS = {
    'max_accounts': 'Аккаунтов',
    'max_devices': 'Устройств',
    'max_posts_per_day': 'Публикаций в день',
    'max_recipes': 'Рецептов склейки',
    'max_upload_mb': 'Размер видео, МБ',
}
DEFAULT_PLANS = {
    'free': {
        'name': 'Бесплатный',
        'limits': {
            'max_accounts': 3,
            'max_devices': 1,
            'max_posts_per_day': 20,
            'max_recipes': 3,
            'max_upload_mb': 300,
        },
        'features': {k: True for k in FEATURES},
    },
    'pro': {
        'name': 'Pro',
        'limits': {
            'max_accounts': 30,
            'max_devices': 10,
            'max_posts_per_day': 300,
            'max_recipes': 50,
            'max_upload_mb': 1000,
        },
        'features': {k: True for k in FEATURES},
    },
    'unlimited': {
        'name': 'Без ограничений',
        'limits': {k: 0 for k in LIMITS},
        'features': {k: True for k in FEATURES},
    },
}
DEFAULT_SETTINGS = {
    'registration': {'mode': 'open', 'default_plan': 'free'},
    'maintenance': {'on': False, 'message': 'Идут технические работы. Попробуйте через несколько минут.'},
    'announcement': {'active': False, 'text': '', 'level': 'info'},
    'plans': DEFAULT_PLANS,
}

_cache = {}


def now():
    return datetime.now(timezone.utc).isoformat()


def core():
    return dbmod.connect('core')


def init_core():
    c = core()
    try:
        c.executescript(CORE_SCHEMA)
    finally:
        c.close()
    dbmod.mark_ready('core')
    _cache.clear()


def owner_id():
    raw = os.getenv('FAXCLIP_OWNER_ID') or os.getenv('TELEGRAM_ALLOWED_USER_IDS', '')
    first = raw.replace(' ', '').split(',')[0]
    return first if first.isdigit() else ''


def admin_ids():
    """The admin panel belongs to the owner only. FAXCLIP_ADMIN_IDS can narrow access but never adds
    other people: any id there that is not the owner is ignored."""
    owner = owner_id()
    if not owner:
        return set()
    raw = os.getenv('FAXCLIP_ADMIN_IDS', '')
    listed = {x for x in raw.replace(' ', '').split(',') if x.isdigit()}
    return {owner} if not listed or owner in listed else set()


def tenant_for(tg_id):
    tg_id = str(tg_id)
    return 'main' if tg_id == owner_id() or tg_id == '0' else 'u' + tg_id


def chat_for_tenant(tenant):
    if tenant == 'main':
        return owner_id()
    return tenant[1:] if tenant.startswith('u') else ''


# ------------------------------------------------------------- settings ----
def get_setting(key):
    hit = _cache.get(('s', key))
    if hit and hit[0] > time.time():
        return hit[1]
    with core() as c:
        r = c.execute('select value from core_settings where key=?', (key,)).fetchone()
    val = DEFAULT_SETTINGS.get(key)
    if r:
        try:
            stored = json.loads(r['value'])
            val = {**val, **stored} if isinstance(val, dict) and isinstance(stored, dict) else stored
        except ValueError:
            pass
    _cache[('s', key)] = (time.time() + 15, val)
    return val


def set_setting(key, value):
    with core() as c:
        c.execute(
            'insert into core_settings(key,value) values(?,?) on conflict(key) do update set value=excluded.value',
            (key, json.dumps(value, ensure_ascii=False)),
        )
    _cache.pop(('s', key), None)


# ------------------------------------------------------------- registry ----
def register(kind, key, tenant=None):
    tenant = tenant or dbmod.current_tenant()
    with core() as c:
        c.execute(
            'insert into core_registry(kind,key,tenant,created_at) values(?,?,?,?) '
            'on conflict(kind,key) do update set tenant=excluded.tenant',
            (kind, key, tenant, now()),
        )
    _cache[('r', kind, key)] = (time.time() + 300, tenant)


def unregister(kind, key):
    with core() as c:
        c.execute('delete from core_registry where kind=? and key=?', (kind, key))
    _cache.pop(('r', kind, key), None)


def lookup(kind, key, default='main'):
    if not key:
        return default
    hit = _cache.get(('r', kind, key))
    if hit and hit[0] > time.time():
        return hit[1]
    with core() as c:
        r = c.execute('select tenant from core_registry where kind=? and key=?', (kind, key)).fetchone()
    t = r['tenant'] if r else default
    if r:
        _cache[('r', kind, key)] = (time.time() + 300, t)
    return t


def tenants():
    with core() as c:
        rows = c.execute('select tenant from core_users where blocked=0').fetchall()
    out = ['main'] + [r['tenant'] for r in rows if r['tenant'] != 'main']
    return [t for t in out if dbmod.valid_tenant(t)]


# ---------------------------------------------------------------- users ----
class AccessDenied(Exception):
    def __init__(self, message, status=403):
        super().__init__(message)
        self.status = status


def _user_row(tg_id):
    with core() as c:
        r = c.execute('select * from core_users where tg_id=?', (str(tg_id),)).fetchone()
    return dict(r) if r else None


def authorize(user):
    """Map a validated Telegram user to (tenant, profile). Creates the account on first visit."""
    tg_id = str(int(user['id']))
    row = _user_row(tg_id)
    is_admin = tg_id in admin_ids()
    if not row:
        reg = get_setting('registration')
        allow = {x for x in os.getenv('TELEGRAM_ALLOWED_USER_IDS', '').replace(' ', '').split(',') if x}
        if reg.get('mode') == 'closed' and not is_admin and tg_id not in allow:
            raise AccessDenied('Регистрация новых пользователей сейчас закрыта.')
        plan = 'unlimited' if tenant_for(tg_id) == 'main' or is_admin else reg.get('default_plan', 'free')
        with core() as c:
            c.execute(
                'insert into core_users(tg_id,tenant,username,first_name,last_name,language,created_at,last_seen,plan) '
                'values(?,?,?,?,?,?,?,?,?) on conflict(tg_id) do nothing',
                (
                    tg_id,
                    tenant_for(tg_id),
                    (user.get('username') or '')[:64],
                    (user.get('first_name') or '')[:64],
                    (user.get('last_name') or '')[:64],
                    (user.get('language_code') or '')[:8],
                    now(),
                    now(),
                    plan,
                ),
            )
        row = _user_row(tg_id)
    if row['blocked'] and not is_admin:
        raise AccessDenied(
            'Доступ заблокирован администратором.'
            + (' ' + row['block_reason'] if row['block_reason'] else '')
        )
    m = get_setting('maintenance')
    if m.get('on') and not is_admin:
        raise AccessDenied(m.get('message') or 'Технические работы', 503)
    key = ('seen', tg_id)
    if _cache.get(key, 0) < time.time():
        _cache[key] = time.time() + 120
        with core() as c:
            c.execute(
                'update core_users set last_seen=?,requests=requests+1,username=coalesce(nullif(?,\'\'),username) where tg_id=?',
                (now(), (user.get('username') or '')[:64], tg_id),
            )
    return row['tenant'], row


def effective(row):
    plans = get_setting('plans') or DEFAULT_PLANS
    plan = plans.get(row.get('plan') or 'free') or plans.get('free') or DEFAULT_PLANS['free']

    def _j(v):
        try:
            return json.loads(v or '{}')
        except ValueError:
            return {}

    limits = {**{k: 0 for k in LIMITS}, **plan.get('limits', {}), **_j(row.get('limits'))}
    features = {**{k: True for k in FEATURES}, **plan.get('features', {}), **_j(row.get('features'))}
    return {
        'plan': row.get('plan') or 'free',
        'plan_name': plan.get('name', row.get('plan')),
        'limits': limits,
        'features': features,
    }


def feature_for_path(path):
    import re

    if re.match(r'^/api/devices/[^/]+/screen', path):
        return 'screen'
    for feat, prefixes in FEATURE_PATHS:
        if any(path == p or path.startswith(p + '/') or path.startswith(p + '?') for p in prefixes):
            return feat
    return None


def admin_audit(admin_id, action, target=None, payload=None):
    import uuid

    with core() as c:
        c.execute(
            'insert into core_admin_audit values(?,?,?,?,?,?)',
            (
                str(uuid.uuid4()),
                str(admin_id),
                action,
                target,
                json.dumps(payload or {}, ensure_ascii=False),
                now(),
            ),
        )
