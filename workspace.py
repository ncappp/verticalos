"""Workspace modules modelled on QUICON (no sales): onboarding, proxies, personas,
extended account and device profiles. Uses side tables so the original schema
(and rollback to the previous build) stays intact."""

import json, socket, time, uuid, base64
from datetime import date
from flask import request, jsonify

SLOTS = ("morning", "afternoon", "evening", "night")
GENDERS = ("male", "female")
PERSONA_STATUS = ("active", "blocked", "suspended", "archived")
PROXY_STATUS = ("draft", "active", "error", "archived")
LOGIN_METHODS = ("username", "email", "phone_number")
WORK_MODES = ("young", "warming", "hot", "dormant")
ACCOUNT_STATUS = ("draft", "pending", "registered", "login", "logout", "blocked", "archived")
PERSONALITIES = ("creative", "empathetic", "analytical", "energetic", "calm", "humorous", "expert")
MIN_AGE = 18

SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_settings(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS ws_proxies(
  id TEXT PRIMARY KEY,proxy_ip TEXT NOT NULL,proxy_port INTEGER NOT NULL,proxy_username TEXT,proxy_password TEXT,
  device_id TEXT UNIQUE,status TEXT DEFAULT 'draft',last_check_at TEXT,last_check_ok INTEGER,
  last_check_egress_ip TEXT,last_check_latency_ms INTEGER,last_check_message TEXT,created_at TEXT NOT NULL,updated_at TEXT);
CREATE TABLE IF NOT EXISTS ws_personas(
  id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT,phone TEXT,gender TEXT,date_of_birth TEXT,country TEXT,language TEXT,
  city TEXT,device_id TEXT UNIQUE,personality TEXT,status TEXT DEFAULT 'active',notes TEXT,denied_keywords TEXT,
  target_by_niche INTEGER DEFAULT 0,activity_level REAL DEFAULT 1,social_confidence REAL DEFAULT 1,risk_tolerance REAL DEFAULT 1,
  consistency REAL DEFAULT 1,max_sessions_per_account INTEGER DEFAULT 1,session_duration_avg INTEGER DEFAULT 60,
  duration_variance INTEGER DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT);
CREATE TABLE IF NOT EXISTS ws_persona_interests(id TEXT PRIMARY KEY,persona_id TEXT NOT NULL,tag TEXT NOT NULL,weight INTEGER DEFAULT 3);
CREATE TABLE IF NOT EXISTS ws_persona_times(id TEXT PRIMARY KEY,persona_id TEXT NOT NULL,time_slot TEXT NOT NULL,weight INTEGER DEFAULT 3);
CREATE TABLE IF NOT EXISTS ws_account_profiles(
  account_id TEXT PRIMARY KEY,persona_id TEXT,login_method TEXT DEFAULT 'username',work_mode TEXT DEFAULT 'young',
  status TEXT DEFAULT 'login',notes TEXT,search_keywords TEXT,channel_name TEXT,successful_sessions INTEGER DEFAULT 0,updated_at TEXT);
CREATE TABLE IF NOT EXISTS ws_device_profiles(device_id TEXT PRIMARY KEY,locale TEXT,timezone TEXT,updated_at TEXT);
CREATE TABLE IF NOT EXISTS ws_agent_tasks(id TEXT PRIMARY KEY,persona_id TEXT NOT NULL,kind TEXT NOT NULL,value TEXT,target INTEGER,period TEXT DEFAULT 'total',status TEXT DEFAULT 'active',created_at TEXT NOT NULL);
"""


class Bad(Exception):
    pass


def _w(v, default=3):
    try:
        v = round(float(v))
    except (TypeError, ValueError):
        v = default
    return max(1, min(5, int(v)))


def _s(x, k, maxlen=2000):
    v = x.get(k)
    if v is None:
        return None
    v = str(v).strip()
    return v[:maxlen] or None


def _age(dob):
    d = date.fromisoformat(dob)
    t = date.today()
    return t.year - d.year - ((t.month, t.day) < (d.month, d.day))


def proxy_check(ip, port, user=None, password=None, timeout=8):
    """HTTP CONNECT through the upstream proxy to an IP echo service; returns (ok,egress_ip,latency_ms,message)."""
    t0 = time.monotonic()
    try:
        s = socket.create_connection((ip, int(port)), timeout=timeout)
    except OSError as e:
        return False, None, None, f"Прокси не отвечает: {e.__class__.__name__}"
    try:
        s.settimeout(timeout)
        auth = ""
        if user:
            auth = (
                "Proxy-Authorization: Basic "
                + base64.b64encode(f"{user}:{password or ''}".encode()).decode()
                + "\r\n"
            )
        s.sendall(
            f"GET http://api.ipify.org/ HTTP/1.1\r\nHost: api.ipify.org\r\n{auth}Connection: close\r\n\r\n".encode()
        )
        data = b""
        while len(data) < 65536:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        ms = int((time.monotonic() - t0) * 1000)
        head, _, body = data.partition(b"\r\n\r\n")
        line = head.split(b"\r\n", 1)[0].decode("latin1", "replace")
        if b" 407" in head[:20]:
            return False, None, ms, "Неверный логин или пароль прокси"
        if b" 200" not in head[:20]:
            return False, None, ms, f"Прокси ответил: {line[:80]}"
        ip_out = body.strip().decode("latin1", "replace")[:64]
        return True, ip_out, ms, "Прокси работает"
    except OSError as e:
        return False, None, None, f"Ошибка соединения: {e.__class__.__name__}"
    finally:
        s.close()


def register_workspace(app, conn, now, audit):
    def err(msg, code=400):
        return jsonify(error=msg), code

    # ---------- onboarding ----------
    def counts(c):
        q = lambda sql: c.execute(sql).fetchone()[0]
        return dict(
            proxies=q("select count(*) from ws_proxies where status!='archived'"),
            devices=q("select count(*) from devices where status!='REVOKED'"),
            personas=q("select count(*) from ws_personas where status!='archived'"),
            accounts=q("select count(*) from accounts"),
        )

    @app.get('/api/onboarding')
    def onboarding_state():
        c = conn()
        k = counts(c)
        r = c.execute("select value from ws_settings where key='onboarding'").fetchone()
        val = json.loads(r[0]) if r else {}
        return jsonify(done=bool(val.get('done')), proxies_skipped=bool(val.get('proxies_skipped')), counts=k)

    @app.post('/api/onboarding')
    def onboarding_update():
        x = request.json or {}
        c = conn()
        r = c.execute("select value from ws_settings where key='onboarding'").fetchone()
        val = json.loads(r[0]) if r else {}
        if x.get('skip_proxies'):
            val['proxies_skipped'] = True
        if x.get('finish'):
            k = counts(c)
            if k['devices'] < 1 or k['personas'] < 1 or k['accounts'] < 1:
                return err('Нужно хотя бы одно устройство, одна персона и один аккаунт')
            val['done'] = True
            val['finished_at'] = now()
        if x.get('restart'):
            val = {}
        c.execute("insert or replace into ws_settings(key,value) values('onboarding',?)", (json.dumps(val),))
        c.commit()
        return jsonify(ok=True, **val)

    # ---------- proxies ----------
    def proxy_row(r):
        d = dict(r)
        d['has_password'] = bool(d.pop('proxy_password', None))
        return d

    def proxy_payload(x, c, pid=None):
        ip = _s(x, 'proxy_ip', 255)
        if not ip:
            raise Bad('Укажите адрес прокси')
        if any(ch.isspace() for ch in ip) or ':' in ip and ip.count(':') < 2:
            raise Bad('Укажите только адрес, без порта — порт задаётся отдельным полем')
        if ip.split('.')[0] in ('10', '127', '0') or ip.startswith('192.168.') or ip == 'localhost':
            raise Bad('Адрес должен быть публичным')
        try:
            port = int(x.get('proxy_port'))
        except (TypeError, ValueError):
            raise Bad('Укажите порт')
        if not 1 <= port <= 65535:
            raise Bad('Порт должен быть от 1 до 65535')
        did = x.get('device_id') or None
        if did:
            if not c.execute("select 1 from devices where id=?", (did,)).fetchone():
                raise Bad('Устройство не найдено')
            busy = c.execute(
                "select id from ws_proxies where device_id=? and id!=? and status!='archived'",
                (did, pid or ''),
            ).fetchone()
            if busy:
                raise Bad('Одно устройство — один прокси, один прокси — одно устройство')
        return ip, port, _s(x, 'proxy_username', 255), x.get('proxy_password'), did

    @app.route('/api/proxies', methods=['GET', 'POST'])
    def proxies():
        c = conn()
        if request.method == 'POST':
            x = request.json or {}
            try:
                ip, port, u, p, did = proxy_payload(x, c)
            except Bad as e:
                return err(str(e))
            pid = str(uuid.uuid4())
            c.execute(
                "insert into ws_proxies(id,proxy_ip,proxy_port,proxy_username,proxy_password,device_id,status,created_at,updated_at) values(?,?,?,?,?,?,?,?,?)",
                (pid, ip, port, u, p or None, did, 'draft', now(), now()),
            )
            audit(c, 'create', 'proxy', pid, {'ip': ip})
            c.commit()
            return jsonify(id=pid)
        rows = c.execute(
            "select p.*,d.name device_name from ws_proxies p left join devices d on d.id=p.device_id where p.status!='archived' or ?=1 order by p.created_at desc",
            (1 if request.args.get('archived') else 0,),
        ).fetchall()
        return jsonify([proxy_row(r) for r in rows])

    @app.route('/api/proxies/<pid>', methods=['PATCH', 'DELETE'])
    def proxy_one(pid):
        c = conn()
        r = c.execute("select * from ws_proxies where id=?", (pid,)).fetchone()
        if not r:
            return err('Прокси не найден', 404)
        if request.method == 'DELETE':
            c.execute("delete from ws_proxies where id=?", (pid,))
            audit(c, 'delete', 'proxy', pid)
            c.commit()
            return jsonify(ok=True)
        x = {**dict(r), **(request.json or {})}
        if 'proxy_password' not in (request.json or {}):
            x['proxy_password'] = r['proxy_password']
        try:
            ip, port, u, p, did = proxy_payload(x, c, pid)
        except Bad as e:
            return err(str(e))
        status = x.get('status') if x.get('status') in PROXY_STATUS else r['status']
        c.execute(
            "update ws_proxies set proxy_ip=?,proxy_port=?,proxy_username=?,proxy_password=?,device_id=?,status=?,updated_at=? where id=?",
            (ip, port, u, p or None, did if status != 'archived' else None, status, now(), pid),
        )
        audit(c, 'update', 'proxy', pid)
        c.commit()
        return jsonify(ok=True)

    @app.post('/api/proxies/<pid>/check')
    def proxy_check_route(pid):
        c = conn()
        r = c.execute("select * from ws_proxies where id=?", (pid,)).fetchone()
        if not r:
            return err('Прокси не найден', 404)
        ok, egress, ms, msg = proxy_check(
            r['proxy_ip'], r['proxy_port'], r['proxy_username'], r['proxy_password']
        )
        c.execute(
            "update ws_proxies set last_check_at=?,last_check_ok=?,last_check_egress_ip=?,last_check_latency_ms=?,last_check_message=?,status=?,updated_at=? where id=?",
            (now(), int(ok), egress, ms, msg, 'active' if ok else 'error', now(), pid),
        )
        c.commit()
        return jsonify(ok=ok, egress_ip=egress, latency_ms=ms, message=msg)

    # ---------- personas ----------
    def persona_full(c, r):
        d = dict(r)
        d['interests'] = [
            dict(i)
            for i in c.execute(
                "select tag,weight from ws_persona_interests where persona_id=? order by weight desc",
                (r['id'],),
            )
        ]
        d['preferred_times'] = [
            dict(i)
            for i in c.execute("select time_slot,weight from ws_persona_times where persona_id=?", (r['id'],))
        ]
        d['accounts'] = c.execute(
            "select count(*) from ws_account_profiles where persona_id=?", (r['id'],)
        ).fetchone()[0]
        dn = (
            c.execute("select name from devices where id=?", (r['device_id'],)).fetchone()
            if r['device_id']
            else None
        )
        d['device_name'] = dn[0] if dn else None
        return d

    def persona_payload(x, c, pid=None):
        name = _s(x, 'name', 120)
        if not name:
            raise Bad('Укажите имя персоны')
        did = x.get('device_id')
        if not did:
            raise Bad('Пожалуйста, выберите устройство.')
        if not c.execute("select 1 from devices where id=? and status!='REVOKED'", (did,)).fetchone():
            raise Bad('Устройство не найдено')
        if c.execute(
            "select 1 from ws_personas where device_id=? and id!=? and status!='archived'", (did, pid or '')
        ).fetchone():
            raise Bad('На этом устройстве уже есть агент. Разрешён один агент на одно устройство')
        dob = _s(x, 'date_of_birth', 10)
        if dob:
            try:
                age = _age(dob)
            except ValueError:
                raise Bad('Неверная дата рождения')
            if age < MIN_AGE:
                raise Bad(f'Пользователь должен быть не моложе {MIN_AGE} лет')
        gender = x.get('gender') if x.get('gender') in GENDERS else None
        status = x.get('status') if x.get('status') in PERSONA_STATUS else 'active'
        interests = []
        for i in (x.get('interests') or [])[:5]:
            tag = str((i or {}).get('tag', '')).strip()[:60]
            if tag:
                interests.append((tag, _w(i.get('weight'))))
        times = []
        for t in x.get('preferred_times') or []:
            if (t or {}).get('time_slot') in SLOTS and t['time_slot'] not in [s for s, _ in times]:
                times.append((t['time_slot'], _w(t.get('weight'))))
        f = lambda k, d, lo, hi: max(lo, min(hi, float(x.get(k) if x.get(k) not in (None, '') else d)))
        vals = dict(
            name=name,
            email=_s(x, 'email', 200),
            phone=_s(x, 'phone', 40),
            gender=gender,
            date_of_birth=dob,
            country=_s(x, 'country', 80),
            language=_s(x, 'language', 80),
            city=_s(x, 'city', 80),
            device_id=did,
            personality=_s(x, 'personality', 40),
            status=status,
            notes=_s(x, 'notes', 4000),
            denied_keywords=_s(x, 'denied_keywords', 2000),
            target_by_niche=int(bool(x.get('target_by_niche'))),
            activity_level=f('activity_level', 1, 0, 1),
            social_confidence=f('social_confidence', 1, 0, 1),
            risk_tolerance=f('risk_tolerance', 1, 0, 1),
            consistency=f('consistency', 1, 0, 1),
            max_sessions_per_account=int(f('max_sessions_per_account', 1, 1, 10)),
            session_duration_avg=int(f('session_duration_avg', 60, 5, 240)),
            duration_variance=int(f('duration_variance', 0, 0, 120)),
        )
        return vals, interests, times

    def save_children(c, pid, interests, times):
        c.execute("delete from ws_persona_interests where persona_id=?", (pid,))
        c.execute("delete from ws_persona_times where persona_id=?", (pid,))
        for tag, w in interests:
            c.execute("insert into ws_persona_interests values(?,?,?,?)", (str(uuid.uuid4()), pid, tag, w))
        for s, w in times:
            c.execute("insert into ws_persona_times values(?,?,?,?)", (str(uuid.uuid4()), pid, s, w))

    @app.route('/api/personas', methods=['GET', 'POST'])
    def personas():
        c = conn()
        if request.method == 'POST':
            try:
                vals, interests, times = persona_payload(request.json or {}, c)
            except Bad as e:
                return err(str(e))
            pid = str(uuid.uuid4())
            vals.update(id=pid, created_at=now(), updated_at=now())
            c.execute(
                f"insert into ws_personas({','.join(vals)}) values({','.join('?' * len(vals))})",
                tuple(vals.values()),
            )
            save_children(c, pid, interests, times)
            audit(c, 'create', 'persona', pid, {'name': vals['name']})
            c.commit()
            return jsonify(id=pid)
        rows = c.execute("select * from ws_personas order by created_at desc").fetchall()
        return jsonify([persona_full(c, r) for r in rows])

    @app.route('/api/personas/<pid>', methods=['GET', 'PATCH', 'DELETE'])
    def persona_one(pid):
        c = conn()
        r = c.execute("select * from ws_personas where id=?", (pid,)).fetchone()
        if not r:
            return err('Агент не найден', 404)
        if request.method == 'GET':
            return jsonify(persona_full(c, r))
        if request.method == 'DELETE':
            c.execute("update ws_account_profiles set persona_id=null where persona_id=?", (pid,))
            c.execute("delete from ws_persona_interests where persona_id=?", (pid,))
            c.execute("delete from ws_persona_times where persona_id=?", (pid,))
            c.execute("delete from ws_agent_tasks where persona_id=?", (pid,))
            c.execute("delete from ws_personas where id=?", (pid,))
            audit(c, 'delete', 'persona', pid)
            c.commit()
            return jsonify(ok=True)
        x = {**persona_full(c, r), **(request.json or {})}
        try:
            vals, interests, times = persona_payload(x, c, pid)
        except Bad as e:
            return err(str(e))
        vals['updated_at'] = now()
        c.execute(
            f"update ws_personas set {','.join(k + '=?' for k in vals)} where id=?", (*vals.values(), pid)
        )
        save_children(c, pid, interests, times)
        audit(c, 'update', 'persona', pid)
        c.commit()
        return jsonify(ok=True)

    # ---------- account profiles ----------
    @app.get('/api/account-profiles')
    def account_profiles():
        c = conn()
        rows = c.execute("""select a.id,a.platform,a.username,a.status account_status,a.device_id,d.name device_name,p.persona_id,
          s.name persona_name,p.login_method,p.work_mode,p.status,p.notes,p.search_keywords,p.channel_name,p.successful_sessions
          from accounts a left join devices d on d.id=a.device_id left join ws_account_profiles p on p.account_id=a.id
          left join ws_personas s on s.id=p.persona_id order by a.created_at desc""").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d['login_method'] = d['login_method'] or 'username'
            d['work_mode'] = d['work_mode'] or 'young'
            d['status'] = d['status'] or 'login'
            out.append(d)
        return jsonify(out)

    @app.put('/api/account-profiles/<aid>')
    def account_profile_set(aid):
        c = conn()
        if not c.execute("select 1 from accounts where id=?", (aid,)).fetchone():
            return err('Аккаунт не найден', 404)
        x = request.json or {}
        pid = x.get('persona_id') or None
        if pid and not c.execute("select 1 from ws_personas where id=?", (pid,)).fetchone():
            return err('Агент не найден')
        kw = _s(x, 'search_keywords', 2000)
        if not kw:
            return err('Укажите ключевые слова для поиска через запятую')
        c.execute(
            """insert into ws_account_profiles(account_id,persona_id,login_method,work_mode,status,notes,search_keywords,channel_name,updated_at)
          values(?,?,?,?,?,?,?,?,?) on conflict(account_id) do update set persona_id=excluded.persona_id,login_method=excluded.login_method,
          work_mode=excluded.work_mode,status=excluded.status,notes=excluded.notes,search_keywords=excluded.search_keywords,
          channel_name=excluded.channel_name,updated_at=excluded.updated_at""",
            (
                aid,
                pid,
                x.get('login_method') if x.get('login_method') in LOGIN_METHODS else 'username',
                x.get('work_mode') if x.get('work_mode') in WORK_MODES else 'young',
                x.get('status') if x.get('status') in ACCOUNT_STATUS else 'login',
                _s(x, 'notes', 4000),
                kw,
                _s(x, 'channel_name', 200),
                now(),
            ),
        )
        if pid:
            dev = c.execute("select device_id from ws_personas where id=?", (pid,)).fetchone()[0]
            cur = c.execute("select device_id from accounts where id=?", (aid,)).fetchone()[0]
            if dev and not cur:
                c.execute("update accounts set device_id=? where id=?", (dev, aid))
        audit(c, 'update', 'account_profile', aid)
        c.commit()
        return jsonify(ok=True)

    # ---------- device profiles ----------
    @app.get('/api/device-profiles')
    def device_profiles():
        c = conn()
        rows = c.execute("""select d.id,d.name,d.status,p.locale,p.timezone,x.id proxy_id,x.proxy_ip,x.proxy_port,x.status proxy_status,
          s.id persona_id,s.name persona_name from devices d left join ws_device_profiles p on p.device_id=d.id
          left join ws_proxies x on x.device_id=d.id and x.status!='archived' left join ws_personas s on s.device_id=d.id and s.status!='archived'
          where d.status!='REVOKED' order by d.created_at desc""").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.put('/api/device-profiles/<did>')
    def device_profile_set(did):
        c = conn()
        if not c.execute("select 1 from devices where id=?", (did,)).fetchone():
            return err('Устройство не найдено', 404)
        x = request.json or {}
        name = _s(x, 'name', 80)
        if name:
            c.execute("update devices set name=? where id=?", (name, did))
        c.execute(
            """insert into ws_device_profiles(device_id,locale,timezone,updated_at) values(?,?,?,?)
          on conflict(device_id) do update set locale=excluded.locale,timezone=excluded.timezone,updated_at=excluded.updated_at""",
            (did, _s(x, 'locale', 20), _s(x, 'timezone', 60), now()),
        )
        if 'proxy_id' in x:
            c.execute("update ws_proxies set device_id=null where device_id=?", (did,))
            if x['proxy_id']:
                if not c.execute(
                    "select 1 from ws_proxies where id=? and status!='archived'", (x['proxy_id'],)
                ).fetchone():
                    return err('Прокси не найден')
                c.execute("update ws_proxies set device_id=? where id=?", (did, x['proxy_id']))
        audit(c, 'update', 'device_profile', did)
        c.commit()
        return jsonify(ok=True)
