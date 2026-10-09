"""Database layer: SQLite (local/dev) or PostgreSQL/Supabase (DATABASE_URL).

The rest of the code base speaks SQLite-flavoured SQL through a sqlite3-like API
(conn.execute(sql, params) -> cursor, rows addressable by name and index,
`with conn() as c:` commits and closes).  In PostgreSQL mode this module
translates that dialect on the fly and isolates every tenant in its own schema;
in SQLite mode every tenant gets its own database file.
"""

import contextvars
import functools
import os
import re
import sqlite3
import threading
from decimal import Decimal

DATABASE_URL = os.getenv('DATABASE_URL') or os.getenv('SUPABASE_DB_URL') or ''
IS_PG = DATABASE_URL.startswith(('postgres://', 'postgresql://'))

tenant_var = contextvars.ContextVar('faxclip_tenant', default='main')
_paths = {'db': None, 'data': None}
_pool = None
_pool_lock = threading.Lock()
_ensured = set()
_schema_hook = []  # callables(conn) that create the tenant schema objects


def configure(db_path, data_dir):
    _paths['db'], _paths['data'] = db_path, data_dir


def on_new_tenant(fn):
    _schema_hook.append(fn)
    return fn


def current_tenant():
    return tenant_var.get()


def valid_tenant(t):
    return isinstance(t, str) and re.fullmatch(r'main|u[0-9]{1,20}', t) is not None


def schema_name(tenant):
    if not valid_tenant(tenant):
        raise ValueError('bad tenant')
    return 't_' + tenant


class use_tenant:
    """Context manager / decorator helper: run code for a given tenant."""

    def __init__(self, tenant):
        if not valid_tenant(tenant) and tenant != 'core':
            raise ValueError('bad tenant')
        self.tenant, self.token = tenant, None

    def __enter__(self):
        self.token = tenant_var.set(self.tenant)
        return self

    def __exit__(self, *a):
        tenant_var.reset(self.token)


def run_in_thread(target, *args, **kwargs):
    """threading.Thread that keeps the caller's tenant (contextvars are not inherited)."""
    ctx = contextvars.copy_context()
    t = threading.Thread(target=lambda: ctx.run(target, *args, **kwargs), daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------- SQLite ----
class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def sqlite_path(tenant):
    if tenant == 'main':
        return _paths['db']
    if tenant == 'core':
        return os.path.join(_paths['data'], 'core.db')
    d = os.path.join(_paths['data'], 'tenants')
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, schema_name(tenant) + '.db')


def _sqlite_connect(tenant):
    path = sqlite_path(tenant)
    fresh = tenant not in ('main', 'core') and tenant not in _ensured
    c = sqlite3.connect(path, timeout=30, factory=ClosingConnection)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA journal_mode=WAL')
    if fresh:
        _ensure_tenant(c, tenant)
    return c


# ------------------------------------------------------------ PostgreSQL ----
class Row:
    """sqlite3.Row look-alike: r['col'], r[0], dict(r), r.keys(), iteration."""

    __slots__ = ('_k', '_v', '_i')

    def __init__(self, keys, values, index):
        self._k, self._v, self._i = keys, values, index

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._v[key]
        return self._v[self._i[key]]

    def keys(self):
        return list(self._k)

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)

    def __eq__(self, other):
        return tuple(self) == tuple(other)

    def __hash__(self):
        return hash(tuple(self._v))

    def __repr__(self):
        return 'Row(%r)' % dict(zip(self._k, self._v))


def _row_factory(cursor):
    desc = cursor.description
    if not desc:
        return lambda values: values
    keys = tuple(d.name for d in desc)
    index = {k: i for i, k in enumerate(keys)}
    return lambda values: Row(keys, values, index)


def _adapt(adapters):
    from psycopg.adapt import Dumper, Loader

    class UnknownDumper(Dumper):
        oid = 0  # let the server infer the type from context, like SQLite

        def dump(self, obj):
            if isinstance(obj, bool):
                return b'1' if obj else b'0'
            if isinstance(obj, float):
                return repr(obj).encode()
            return str(obj).encode()

    class NumericLoader(Loader):
        def load(self, data):
            v = Decimal(bytes(data).decode())
            return int(v) if v == v.to_integral_value() else float(v)

    for t in (int, float, bool, Decimal):
        adapters.register_dumper(t, UnknownDumper)
    adapters.register_loader('numeric', NumericLoader)


def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                from psycopg_pool import ConnectionPool

                def configure_conn(c):
                    _adapt(c.adapters)
                    c.row_factory = _row_factory
                    c.prepare_threshold = None  # safe with Supabase/pgBouncer poolers
                    c.autocommit = True
                    c.execute('set statement_timeout = 30000')
                    c.autocommit = False

                _pool = ConnectionPool(
                    DATABASE_URL,
                    min_size=int(os.getenv('DB_POOL_MIN', '1')),
                    max_size=int(os.getenv('DB_POOL_MAX', '8')),
                    configure=configure_conn,
                    kwargs={'connect_timeout': 15, 'client_encoding': 'utf8'},
                    timeout=30,
                    open=True,
                    name='faxclip',
                )
                import atexit

                atexit.register(lambda: _pool.close(timeout=2))
    return _pool


_IGNORE = re.compile(r'^\s*insert\s+or\s+ignore\s+into\s', re.I)
_REPLACE = re.compile(r'^\s*insert\s+or\s+replace\s+into\s+([a-z_][a-z0-9_]*)\s*(\(([^)]*)\))?', re.I)
_PRAGMA_TI = re.compile(r'^\s*pragma\s+table_info\s*\(\s*([a-z_][a-z0-9_]*)\s*\)\s*;?\s*$', re.I)
_PRAGMA = re.compile(r'^\s*pragma\s', re.I)
_BEGIN_IMM = re.compile(r'^\s*begin(\s+immediate|\s+exclusive)?(\s+transaction)?\s*;?\s*$', re.I)
_DDL = re.compile(r'^\s*(create\s+table|alter\s+table)', re.I)
_WRITE = re.compile(r'^\s*(insert|update|delete|create|alter|drop|replace)\b', re.I)


def _split_code(sql):
    """Yield (is_code, text) chunks so that string literals are never rewritten."""
    out, i, n, start = [], 0, len(sql), 0
    while i < n:
        ch = sql[i]
        if ch in ("'", '"'):
            if i > start:
                out.append((True, sql[start:i]))
            j = i + 1
            while j < n:
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            out.append((False, sql[i : j + 1]))
            i = start = j + 1
            continue
        i += 1
    if start < n:
        out.append((True, sql[start:]))
    return out


def _rewrite_code(text, ddl):
    text = re.sub(r'\bdatetime\(\s*([^()]+?)\s*\)', r'(\1)::timestamptz', text, flags=re.I)
    text = re.sub(r'\bmin\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)', r'least(\1, \2)', text, flags=re.I)
    text = re.sub(r'\bmax\(\s*([^(),]+?)\s*,\s*([^(),]+?)\s*\)', r'greatest(\1, \2)', text, flags=re.I)
    text = re.sub(r'\blimit\s+-1\b', 'limit all', text, flags=re.I)
    if ddl:
        text = re.sub(r'\bINTEGER\b', 'BIGINT', text, flags=re.I)
        text = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', text, flags=re.I)
        text = re.sub(r'\bBLOB\b', 'BYTEA', text, flags=re.I)
    return text.replace('%', '%%').replace('?', '%s')


@functools.lru_cache(maxsize=4096)
def translate(sql):
    """Return (pg_sql, kind) where kind in {'sql','noop','upsert'}."""
    s = sql.strip().rstrip(';')
    if _PRAGMA_TI.match(s):
        t = _PRAGMA_TI.match(s).group(1).lower()
        return (
            "select column_name as name, data_type as type from information_schema.columns "
            f"where table_schema=current_schema() and table_name='{t}' order by ordinal_position",
            'sql',
        )
    if _PRAGMA.match(s):
        return 'select 1', 'noop'
    if _BEGIN_IMM.match(s):
        return 'select pg_advisory_xact_lock(hashtext(current_schema()))', 'sql'
    kind = 'sql'
    if _IGNORE.match(s):
        s = _IGNORE.sub('insert into ', s, count=1) + ' on conflict do nothing'
    elif _REPLACE.match(s):
        kind = 'upsert'
        s = re.sub(r'^\s*insert\s+or\s+replace\s+into\s', 'insert into ', s, count=1, flags=re.I)
    s = re.sub(
        r"datetime\(\s*'now'\s*,\s*'(-?\d+) (minutes?|hours?|days?|seconds?)'\s*\)",
        lambda m: f"(now() + interval '{m.group(1)} {m.group(2)}')",
        s,
        flags=re.I,
    )
    s = re.sub(r"datetime\(\s*'now'\s*\)", 'now()', s, flags=re.I)
    ddl = bool(_DDL.match(s))
    return ''.join(
        _rewrite_code(t, ddl) if code else t.replace('%', '%%') for code, t in _split_code(s)
    ), kind


_upsert_cache = {}


def _upsert_suffix(raw, sql):
    m = _REPLACE.match(sql.strip())
    table = m.group(1).lower()
    if table not in _upsert_cache:
        cols = [
            r[0]
            for r in raw.execute(
                "select column_name from information_schema.columns where table_schema=current_schema() "
                "and table_name=%s order by ordinal_position",
                (table,),
            ).fetchall()
        ]
        pk = [
            r[0]
            for r in raw.execute(
                "select a.attname from pg_index i join pg_attribute a on a.attrelid=i.indrelid and "
                "a.attnum=any(i.indkey) where i.indrelid=(current_schema()||'.'||%s)::regclass and "
                "i.indisprimary",
                (table,),
            ).fetchall()
        ]
        _upsert_cache[table] = (cols, pk)
    cols, pk = _upsert_cache[table]
    used = [c.strip().lower() for c in m.group(3).split(',')] if m.group(3) else cols
    upd = [c for c in used if c not in pk]
    if not pk:
        return ''
    action = 'do update set ' + ','.join(f'{c}=excluded.{c}' for c in upd) if upd else 'do nothing'
    return f" on conflict ({','.join(pk)}) {action}"


_cols_cache = {}
_DO_UPDATE = re.compile(r'\bdo\s+update\s+set\b', re.I)
_INS_TABLE = re.compile(r'^\s*insert\s+into\s+([a-z_][a-z0-9_]*)', re.I)


def _table_cols(raw, table):
    if table not in _cols_cache:
        _cols_cache[table] = {
            r[0]
            for r in raw.execute(
                "select column_name from information_schema.columns where table_schema=current_schema() "
                "and table_name=%s",
                (table,),
            ).fetchall()
        }
    return _cols_cache[table]


def _qualify_conflict(raw, q):
    """PostgreSQL needs target-table columns qualified on the right side of DO UPDATE SET."""
    m, t = _DO_UPDATE.search(q), _INS_TABLE.match(q)
    if not m or not t:
        return q
    table = t.group(1).lower()
    cols = _table_cols(raw, table)
    head, tail = q[: m.end()], q[m.end() :]
    out = []
    for code, text in _split_code(tail):
        if not code:
            out.append(text)
            continue

        def rep(mm):
            word, start = mm.group(1), mm.start()
            before = text[:start].rstrip()
            after = text[mm.end() :].lstrip()
            if word.lower() not in cols or before.endswith('.') or after.startswith('.'):
                return word
            if (
                after.startswith('=')
                and not after.startswith('==')
                and (before == '' or before.endswith(',') or before.lower().endswith('set'))
            ):
                return word
            return f'{table}.{word}'

        out.append(re.sub(r'\b([A-Za-z_][A-Za-z0-9_]*)\b', rep, text))
    return head + ''.join(out)


def _sqlite_error(exc):
    import psycopg

    msg = str(exc).strip().split('\n')[0]
    if isinstance(exc, psycopg.IntegrityError):
        e = sqlite3.IntegrityError(msg)
    else:
        e = sqlite3.OperationalError(msg)
    e.pg = exc
    return e


class PGConnection:
    def __init__(self, tenant):
        self.tenant = tenant
        self._pool = get_pool()
        self._raw = self._pool.getconn()
        self._closed = False
        self._sp = 0
        try:
            schema = 'core' if tenant == 'core' else schema_name(tenant)
            if tenant not in _ensured:
                self._raw.execute(f'create schema if not exists {schema}')
                self._raw.commit()
            self._raw.execute("select set_config('search_path', %s, false)", (schema + ',public',))
            self._raw.commit()
            if tenant not in _ensured and tenant not in ('core',):
                _ensure_tenant(self, tenant)
        except Exception:
            self.close()
            raise

    # sqlite3-compatible API --------------------------------------------
    row_factory = None

    @property
    def in_transaction(self):
        from psycopg.pq import TransactionStatus

        return self._raw.info.transaction_status == TransactionStatus.INTRANS

    def execute(self, sql, params=()):
        q, kind = translate(sql)
        if kind == 'noop':
            return self._raw.cursor()
        if kind == 'upsert':
            q = q + _upsert_suffix(self._raw, sql)
        elif 'update set' in q.lower():
            q = _qualify_conflict(self._raw, q)
        params = tuple(params) if params is not None else ()
        savepoint = self.in_transaction and _WRITE.match(sql) is not None
        cur = self._raw.cursor()
        if savepoint:
            self._sp += 1
            sp = f'sp{self._sp}'
            self._raw.execute('savepoint ' + sp)
        try:
            cur.execute(q, params)
        except Exception as exc:
            import psycopg

            if not isinstance(exc, psycopg.Error):
                raise
            if savepoint:
                self._raw.execute('rollback to savepoint ' + sp)
            elif not self.in_transaction or self._raw.info.transaction_status != 0:
                self._raw.rollback()
            raise _sqlite_error(exc) from exc
        if savepoint:
            self._raw.execute('release savepoint ' + sp)
        return cur

    def executemany(self, sql, seq):
        cur = None
        for p in seq:
            cur = self.execute(sql, p)
        return cur

    def executescript(self, script):
        for code_stmt in _split_statements(script):
            self.execute(code_stmt)
        self.commit()

    def commit(self):
        if not self._closed:
            self._raw.commit()

    def rollback(self):
        if not self._closed:
            self._raw.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            self._raw.rollback()
        except Exception:
            pass
        self._pool.putconn(self._raw)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *a):
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


def _split_statements(script):
    stmts, buf = [], []
    for code, text in _split_code(script):
        if not code:
            buf.append(text)
            continue
        parts = text.split(';')
        for i, p in enumerate(parts):
            buf.append(p)
            if i < len(parts) - 1:
                stmt = ''.join(buf).strip()
                if stmt:
                    stmts.append(stmt)
                buf = []
    tail = ''.join(buf).strip()
    if tail:
        stmts.append(tail)
    clean = [re.sub(r'^(\s*--[^\n]*\n)+', '', s).strip() for s in stmts]
    return [s for s in clean if s and not s.startswith('--')]


def _ensure_tenant(c, tenant):
    _ensured.add(tenant)
    if tenant in ('main', 'core'):
        return
    for fn in _schema_hook:
        fn(c)


def connect(tenant=None):
    tenant = tenant or tenant_var.get()
    if IS_PG:
        return PGConnection(tenant)
    return _sqlite_connect(tenant)


def mark_ready(tenant):
    _ensured.add(tenant)
