"""Persistent state for Render Free: snapshot SQLite into the owner's Telegram chat
(as a pinned document) and restore it automatically after a restart wipes the disk.
Only the database is stored (devices, token hashes, accounts, tasks, history, duplicate guard).
Videos are not stored."""

import gzip, hashlib, io, os, re, sqlite3, tempfile, threading, time
import requests

CAPTION = 'FaxClip backup — не удаляйте и не открепляйте. Нужен для автоматического восстановления.'
_lock = threading.Lock()
_state = {'hash': None, 'message_id': None, 'started': False, 'last': 0}


def _token():
    t = os.getenv('TELEGRAM_BOT_TOKEN', '')
    return t if re.fullmatch(r'\d{5,15}:[A-Za-z0-9_-]{30,}', t) else ''


def _owner():
    raw = os.getenv('TELEGRAM_ALLOWED_USER_IDS', '').replace(' ', '').split(',')
    return raw[0] if raw and re.fullmatch(r'\d{3,20}', raw[0]) else ''


def enabled():
    return (
        os.getenv('FAXCLIP_TG_BACKUP', '1') != '0'
        and not os.getenv('FAXCLIP_LOCAL_TOKEN')
        and bool(_token() and _owner())
    )


def _api(method, **kw):
    r = requests.post(f'https://api.telegram.org/bot{_token()}/{method}', timeout=60, **kw)
    data = r.json()
    if not data.get('ok'):
        raise RuntimeError(method + ': ' + str(data.get('description', ''))[:120])
    return data['result']


def _has_state(path):
    if not os.path.exists(path):
        return False
    try:
        c = sqlite3.connect(path)
        try:
            return bool(
                c.execute('select 1 from devices limit 1').fetchone()
                or c.execute('select 1 from accounts limit 1').fetchone()
            )
        finally:
            c.close()
    except sqlite3.Error:
        return False


def _valid_db(raw):
    if not raw.startswith(b'SQLite format 3\x00'):
        return False
    fd, tmp = tempfile.mkstemp(suffix='.db')
    os.write(fd, raw)
    os.close(fd)
    try:
        c = sqlite3.connect(tmp)
        try:
            return c.execute('pragma integrity_check').fetchone()[0] == 'ok' and bool(
                c.execute("select 1 from sqlite_master where name='devices'").fetchone()
            )
        finally:
            c.close()
    except sqlite3.Error:
        return False
    finally:
        os.unlink(tmp)


def restore_if_empty(path):
    """Called before schema creation. Never overwrites a database that already has devices/accounts."""
    if not enabled() or _has_state(path):
        return False
    try:
        chat = _api('getChat', data={'chat_id': _owner()})
        msg = chat.get('pinned_message') or {}
        doc = msg.get('document') or {}
        if not str(doc.get('file_name', '')).startswith('faxclip-backup'):
            print('FaxClip: no Telegram backup to restore', flush=True)
            return False
        f = _api('getFile', data={'file_id': doc['file_id']})
        r = requests.get(f'https://api.telegram.org/file/bot{_token()}/{f["file_path"]}', timeout=120)
        r.raise_for_status()
        raw = gzip.decompress(r.content)
        if not _valid_db(raw):
            print('FaxClip: Telegram backup invalid; not restored', flush=True)
            return False
        for suffix in ('-wal', '-shm'):
            if os.path.exists(path + suffix):
                os.unlink(path + suffix)
        tmp = path + '.restore'
        open(tmp, 'wb').write(raw)
        os.replace(tmp, path)
        _state['message_id'] = msg.get('message_id')
        _state['hash'] = hashlib.sha256(raw).hexdigest()
        print('FaxClip: database restored from Telegram backup', flush=True)
        return True
    except Exception as e:
        print('FaxClip: Telegram restore skipped:', type(e).__name__, str(e)[:120], flush=True)
        return False


def _snapshot(path):
    src = sqlite3.connect(path, timeout=30)
    fd, tmp = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    try:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
        return open(tmp, 'rb').read()
    finally:
        src.close()
        os.unlink(tmp)


def backup_now(path, force=False):
    if not enabled() or not _has_state(path):
        return False
    with _lock:
        raw = _snapshot(path)
        h = hashlib.sha256(raw).hexdigest()
        if h == _state['hash'] and not force:
            return False
        body = gzip.compress(raw)
        if len(body) > 45 * 1024 * 1024:
            print('FaxClip: backup too large for Telegram', flush=True)
            return False
        name = 'faxclip-backup-' + time.strftime('%Y%m%d-%H%M%S') + '.db.gz'
        msg = _api(
            'sendDocument',
            data={'chat_id': _owner(), 'caption': CAPTION, 'disable_notification': 'true'},
            files={'document': (name, io.BytesIO(body), 'application/gzip')},
        )
        old = _state['message_id']
        try:
            _api(
                'pinChatMessage',
                data={'chat_id': _owner(), 'message_id': msg['message_id'], 'disable_notification': 'true'},
            )
        except Exception as e:
            print('FaxClip: pin failed:', str(e)[:120], flush=True)
            return False
        if old and old != msg['message_id']:
            try:
                _api('deleteMessage', data={'chat_id': _owner(), 'message_id': old})
            except Exception:
                pass
        _state.update(hash=h, message_id=msg['message_id'], last=time.time())
        return True


def start(path, interval=60):
    if not enabled() or _state['started']:
        return
    _state['started'] = True
    if not _state['message_id']:
        try:
            _state['message_id'] = (
                _api('getChat', data={'chat_id': _owner()}).get('pinned_message') or {}
            ).get('message_id')
        except Exception:
            pass

    def loop():
        time.sleep(20)
        while True:
            try:
                backup_now(path)
            except Exception as e:
                print('FaxClip: Telegram backup failed:', type(e).__name__, str(e)[:120], flush=True)
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name='faxclip-tg-backup').start()
