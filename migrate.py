"""SQLite <-> PostgreSQL (Supabase) data transfer.

* First start on Supabase: the owner's data is imported from the latest Telegram backup
  (or from the local SQLite file) — nothing has to be copied by hand.
* While running on Supabase the owner's workspace is still exported to a SQLite snapshot and
  pinned in Telegram, so the existing backup/restore path keeps working.
"""

import logging
import os
import sqlite3
import tempfile

import db as dbmod
import tenancy

log = logging.getLogger('faxclip.migrate')
FIRST = ('devices', 'clips', 'accounts', 'publications')


def _pg_tables(c):
    return {
        r[0]
        for r in c.execute(
            "select table_name from information_schema.tables where table_schema=current_schema() and table_type='BASE TABLE'"
        ).fetchall()
    }


def _pg_cols(c, table):
    return [
        r[0]
        for r in c.execute(
            'select column_name from information_schema.columns where table_schema=current_schema() and table_name=? '
            'order by ordinal_position',
            (table,),
        ).fetchall()
    ]


def import_sqlite_into_pg(path, tenant='main'):
    src = sqlite3.connect(path)
    src.row_factory = sqlite3.Row
    names = [
        r[0]
        for r in src.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")
    ]
    order = [t for t in FIRST if t in names] + sorted(t for t in names if t not in FIRST)
    stats = {}
    with dbmod.connect(tenant) as c:
        have = _pg_tables(c)
        for t in order:
            if t not in have:
                continue
            pg_cols = set(_pg_cols(c, t))
            src_cols = [r[1] for r in src.execute(f'pragma table_info({t})')]
            cols = [x for x in src_cols if x in pg_cols]
            if not cols:
                continue
            ok = skipped = 0
            sql = f'insert into {t}({",".join(cols)}) values({",".join("?" * len(cols))}) on conflict do nothing'
            for row in src.execute(f'select {",".join(cols)} from {t}'):
                try:
                    c.execute(sql, tuple(row))
                    ok += 1
                except sqlite3.Error:
                    skipped += 1
            stats[t] = (ok, skipped)
    src.close()
    return stats


def export_pg_to_sqlite(path, tenant='main'):
    from app import create_schema

    dst = sqlite3.connect(path)
    dst.row_factory = sqlite3.Row
    create_schema(dst)
    dst.commit()
    dst.execute('pragma foreign_keys=OFF')
    with dbmod.connect(tenant) as c:
        for t in sorted(_pg_tables(c)):
            dst_cols = {r[1] for r in dst.execute(f'pragma table_info({t})')}
            cols = [x for x in _pg_cols(c, t) if x in dst_cols]
            if not cols:
                continue
            dst.execute(f'delete from {t}')
            rows = [tuple(r) for r in c.execute(f'select {",".join(cols)} from {t}').fetchall()]
            dst.executemany(
                f'insert or ignore into {t}({",".join(cols)}) values({",".join("?" * len(cols))})', rows
            )
    dst.commit()
    dst.close()


def _main_is_empty():
    with dbmod.connect('main') as c:
        return not any(
            c.execute(f'select 1 from {t} limit 1').fetchone()
            for t in ('devices', 'accounts', 'publications')
        )


def bootstrap_postgres(local_db):
    """Import the owner's existing data into an empty Supabase database exactly once."""
    if not dbmod.IS_PG or os.getenv('FAXCLIP_IMPORT_SQLITE', '1') == '0':
        return None
    if tenancy.get_setting('migrated_from_sqlite') or not _main_is_empty():
        return None
    import tg_backup

    source = None
    if os.path.exists(local_db) and tg_backup._has_state(local_db):
        source = local_db
    else:
        fd, tmp = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(tmp)
        if tg_backup.restore_if_empty(tmp):
            source = tmp
    if not source:
        tenancy.set_setting('migrated_from_sqlite', {'source': None})
        return None
    stats = import_sqlite_into_pg(source)
    tenancy.set_setting(
        'migrated_from_sqlite', {'source': 'telegram' if source != local_db else 'file', 'tables': stats}
    )
    print('FaxClip: imported SQLite data into PostgreSQL:', stats, flush=True)
    return stats


def backup_pg_to_telegram(tenant):
    if tenant != 'main' or not dbmod.IS_PG:
        return
    import tg_backup

    if not tg_backup.enabled():
        return
    fd, tmp = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        export_pg_to_sqlite(tmp)
        tg_backup.backup_now(tmp)
    finally:
        for suffix in ('', '-wal', '-shm'):
            if os.path.exists(tmp + suffix):
                os.unlink(tmp + suffix)
