"""Warm-up agent without AI (QUICON "Сценарии и задачи"): rule-based TikTok sessions on the phone
via the Mac (adb). Server plans sessions from persona time slots, hands them to the Mac warm agent,
keeps an event log and promotes account stages. Publications always have priority over warm-up."""

import json, random, time, uuid
from datetime import datetime, timedelta, timezone
from flask import request, jsonify, g

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_scenarios(id TEXT PRIMARY KEY,name TEXT NOT NULL,system INTEGER DEFAULT 0,network TEXT DEFAULT 'TikTok',
  account_mode TEXT DEFAULT 'any',max_duration INTEGER DEFAULT 15,watch_min INTEGER DEFAULT 5,watch_max INTEGER DEFAULT 20,
  like_pct INTEGER DEFAULT 10,follow_pct INTEGER DEFAULT 0,search_pct INTEGER DEFAULT 30,max_likes INTEGER DEFAULT 10,max_follows INTEGER DEFAULT 0,
  num_success INTEGER DEFAULT 0,num_fail INTEGER DEFAULT 0,total_usage INTEGER DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT);
CREATE TABLE IF NOT EXISTS ws_tasks(id TEXT PRIMARY KEY,persona_id TEXT,account_id TEXT,device_id TEXT,scenario_id TEXT,status TEXT DEFAULT 'scheduled',
  task_type TEXT DEFAULT 'session',time_slot TEXT,plan_date TEXT,planned_start REAL,planned_end REAL,duration INTEGER,actions TEXT,worker_notes TEXT,
  error TEXT,lease_until REAL,started_at TEXT,finished_at TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ws_task_events(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,event_type TEXT,message TEXT,details TEXT,created_at TEXT NOT NULL);
INSERT OR IGNORE INTO ws_scenarios(id,name,system,account_mode,max_duration,watch_min,watch_max,like_pct,follow_pct,search_pct,max_likes,max_follows,created_at) VALUES('sys-warm-1','Прогрев 1',1,'young',10,5,15,5,0,30,5,0,'2026-01-01T00:00:00+00:00');
INSERT OR IGNORE INTO ws_scenarios(id,name,system,account_mode,max_duration,watch_min,watch_max,like_pct,follow_pct,search_pct,max_likes,max_follows,created_at) VALUES('sys-warm-2','Прогрев 2',1,'warming',15,6,25,12,2,40,15,3,'2026-01-01T00:00:00+00:00');
INSERT OR IGNORE INTO ws_scenarios(id,name,system,account_mode,max_duration,watch_min,watch_max,like_pct,follow_pct,search_pct,max_likes,max_follows,created_at) VALUES('sys-warm-3','Прогрев 3',1,'hot',25,6,35,20,4,30,30,6,'2026-01-01T00:00:00+00:00');
"""
SYSTEM = [
    dict(
        id='sys-warm-1',
        name='Прогрев 1',
        account_mode='young',
        max_duration=10,
        watch_min=5,
        watch_max=15,
        like_pct=5,
        follow_pct=0,
        search_pct=30,
        max_likes=5,
        max_follows=0,
    ),
    dict(
        id='sys-warm-2',
        name='Прогрев 2',
        account_mode='warming',
        max_duration=15,
        watch_min=6,
        watch_max=25,
        like_pct=12,
        follow_pct=2,
        search_pct=40,
        max_likes=15,
        max_follows=3,
    ),
    dict(
        id='sys-warm-3',
        name='Прогрев 3',
        account_mode='hot',
        max_duration=25,
        watch_min=6,
        watch_max=35,
        like_pct=20,
        follow_pct=4,
        search_pct=30,
        max_likes=30,
        max_follows=6,
    ),
]
SLOTS = {'morning': (8, 12), 'afternoon': (12, 17), 'evening': (17, 22), 'night': (22, 24)}
STATUSES = ('scheduled', 'running', 'done', 'failed', 'cancelled', 'human_intervention', 'missed')
DELETABLE = ('scheduled', 'failed', 'cancelled', 'human_intervention', 'missed')
RESTARTABLE = ('cancelled', 'human_intervention', 'failed', 'missed')
MODES = ('any', 'young', 'warming', 'hot')
SETTINGS_DEF = dict(
    enabled=False, auto_stage_promotion=True, to_warming=5, to_hot=15, timezone='Europe/Moscow'
)
LEASE = 120


def _n(v, lo, hi, d):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return d


def tz(name):
    if ZoneInfo:
        try:
            return ZoneInfo(name or 'Europe/Moscow')
        except Exception:
            pass
    return timezone(timedelta(hours=3))


def register_warmup(app, conn, now, audit):
    def err(m, code=400):
        return jsonify(error=m), code

    def body():
        return request.get_json(silent=True) or {}

    def settings(c):
        r = c.execute("select value from ws_settings where key='warmup'").fetchone()
        try:
            v = json.loads(r[0]) if r else {}
        except ValueError:
            v = {}
        return {**SETTINGS_DEF, **v}

    def event(c, tid, kind, msg, details=None):
        c.execute(
            'insert into ws_task_events values(?,?,?,?,?,?)',
            (
                str(uuid.uuid4()),
                tid,
                kind,
                (msg or '')[:500],
                json.dumps(details or {}, ensure_ascii=False)[:2000],
                now(),
            ),
        )

    def eligible(c):
        return c.execute("""select a.id account_id,a.username,a.platform,a.device_id,p.work_mode,p.search_keywords,p.status pstatus,
          s.id persona_id,s.name persona,s.max_sessions_per_account,s.session_duration_avg,s.duration_variance,s.denied_keywords,dp.timezone
          from accounts a join ws_account_profiles p on p.account_id=a.id join ws_personas s on s.id=p.persona_id
          left join ws_device_profiles dp on dp.device_id=a.device_id join devices d on d.id=a.device_id
          where s.status='active' and s.device_id=a.device_id and d.status!='REVOKED' and lower(a.platform)='tiktok'
          and coalesce(p.status,'login') in ('login','registered') and coalesce(p.work_mode,'young')!='dormant'""").fetchall()

    def pick_scenario(c, mode):
        rows = c.execute(
            "select * from ws_scenarios where account_mode in (?, 'any') order by case when account_mode=? then 0 else 1 end,system,total_usage",
            (mode, mode),
        ).fetchall()
        return rows[0] if rows else None

    def plan(c, force_now=False):
        """Create today's sessions for every eligible account (once per local day)."""
        st = settings(c)
        made = 0
        for a in eligible(c):
            zone = tz(a['timezone'] or st['timezone'])
            loc = datetime.now(zone)
            day = loc.date().isoformat()
            if c.execute(
                "select 1 from ws_tasks where account_id=? and plan_date=? and task_type='session'",
                (a['account_id'], day),
            ).fetchone():
                continue
            times = c.execute(
                'select time_slot,weight from ws_persona_times where persona_id=?', (a['persona_id'],)
            ).fetchall()
            slots = {t['time_slot']: t['weight'] for t in times if t['time_slot'] in SLOTS} or {
                'afternoon': 3,
                'evening': 3,
            }
            sc = pick_scenario(c, a['work_mode'] or 'young')
            if not sc:
                continue
            n = _n(a['max_sessions_per_account'], 1, 6, 1)
            chosen = []
            mine = 0
            pool = dict(slots)
            for _ in range(n):
                if not pool:
                    pool = dict(slots)
                k = random.choices(list(pool), weights=list(pool.values()))[0]
                chosen.append(k)
                pool.pop(k, None)
            for slot in chosen:
                h0, h1 = SLOTS[slot]
                start = loc.replace(hour=h0, minute=0, second=0, microsecond=0)
                end = loc.replace(hour=h1 - 1, minute=59, second=0, microsecond=0)
                if end < loc:
                    continue  # slot already passed today
                start = max(start, loc + timedelta(minutes=2))
                t = start + timedelta(seconds=random.uniform(0, max(0, (end - start).total_seconds())))
                avg = _n(a['session_duration_avg'], 3, 240, 60)
                var = _n(a['duration_variance'], 0, 120, 0)
                dur = (
                    max(
                        3,
                        min(
                            sc['max_duration'],
                            avg + random.randint(-var, var) if var else min(avg, sc['max_duration']),
                        ),
                    )
                    * 60
                )
                c.execute(
                    'insert into ws_tasks(id,persona_id,account_id,device_id,scenario_id,status,task_type,time_slot,plan_date,planned_start,planned_end,duration,actions,created_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (
                        str(uuid.uuid4()),
                        a['persona_id'],
                        a['account_id'],
                        a['device_id'],
                        sc['id'],
                        'scheduled',
                        'session',
                        slot,
                        day,
                        t.timestamp(),
                        t.timestamp() + dur,
                        dur,
                        '{}',
                        now(),
                    ),
                )
                made += 1
                mine += 1
            if not mine:
                # mark the day as planned even if all slots passed, so we don't retry every poll
                c.execute(
                    'insert into ws_tasks(id,persona_id,account_id,device_id,scenario_id,status,task_type,plan_date,error,created_at) values(?,?,?,?,?,?,?,?,?,?)',
                    (
                        str(uuid.uuid4()),
                        a['persona_id'],
                        a['account_id'],
                        a['device_id'],
                        sc['id'],
                        'missed',
                        'session',
                        day,
                        'Слоты активности на сегодня уже прошли',
                        now(),
                    ),
                )
        return made

    def task_rows(c, where='1=1', args=()):
        rows = c.execute(
            f"""select t.*,a.username,a.platform,s.name persona,d.name device,sc.name scenario from ws_tasks t left join accounts a on a.id=t.account_id
          left join ws_personas s on s.id=t.persona_id left join devices d on d.id=t.device_id left join ws_scenarios sc on sc.id=t.scenario_id
          where {where} order by coalesce(t.planned_start,0) desc,t.created_at desc limit 300""",
            args,
        ).fetchall()
        out = []
        for r in rows:
            r = dict(r)
            try:
                r['actions'] = json.loads(r['actions'] or '{}')
            except ValueError:
                r['actions'] = {}
            out.append(r)
        return out

    def finish(c, t, status, error=None, counts=None, duration=None):
        c.execute(
            'update ws_tasks set status=?,error=?,actions=?,finished_at=?,lease_until=null,duration=coalesce(?,duration) where id=?',
            (
                status,
                error,
                json.dumps(counts or json.loads(t['actions'] or '{}'), ensure_ascii=False),
                now(),
                duration,
                t['id'],
            ),
        )
        c.execute(
            'update ws_scenarios set total_usage=total_usage+1,num_success=num_success+?,num_fail=num_fail+? where id=?',
            (1 if status == 'done' else 0, 0 if status == 'done' else 1, t['scenario_id']),
        )
        a = c.execute('select username from accounts where id=?', (t['account_id'],)).fetchone()
        who = a['username'] if a else ''
        if status == 'done':
            c.execute(
                'insert into ws_account_profiles(account_id,successful_sessions) values(?,1) on conflict(account_id) do update set successful_sessions=coalesce(successful_sessions,0)+1',
                (t['account_id'],),
            )
            st = settings(c)
            if st['auto_stage_promotion']:
                p = c.execute(
                    'select work_mode,successful_sessions from ws_account_profiles where account_id=?',
                    (t['account_id'],),
                ).fetchone()
                mode, n = (p['work_mode'] or 'young'), (p['successful_sessions'] or 0)
                new = (
                    'warming'
                    if mode == 'young' and n >= st['to_warming']
                    else 'hot'
                    if mode == 'warming' and n >= st['to_hot']
                    else None
                )
                if new:
                    c.execute(
                        'update ws_account_profiles set work_mode=? where account_id=?',
                        (new, t['account_id']),
                    )
                    event(
                        c,
                        t['id'],
                        'stage',
                        f"Аккаунт переведён на ступень «{'Прогрев' if new == 'warming' else 'Горячий'}»",
                    )
                    try:
                        from notify import emit

                        emit(
                            c,
                            'success',
                            'stage_promotion',
                            f"{who}: новая ступень прогрева",
                            'Горячий' if new == 'hot' else 'Прогрев',
                            'warmup',
                        )
                    except Exception:
                        pass
        elif status == 'human_intervention':
            try:
                from notify import emit

                emit(
                    c,
                    'warning',
                    'human_intervention',
                    f'Прогрев остановлен · {who}',
                    error or 'Нужно вмешательство человека',
                    'warmup',
                )
            except Exception:
                pass

    # ---------- owner API ----------
    @app.route('/api/warmup/settings', methods=['GET', 'PUT'])
    def warm_settings():
        with conn() as c:
            st = settings(c)
            if request.method == 'PUT':
                x = body()
                st = dict(
                    enabled=bool(x.get('enabled', st['enabled'])),
                    auto_stage_promotion=bool(x.get('auto_stage_promotion', st['auto_stage_promotion'])),
                    to_warming=_n(x.get('to_warming', st['to_warming']), 1, 200, 5),
                    to_hot=_n(x.get('to_hot', st['to_hot']), 1, 500, 15),
                    timezone=str(x.get('timezone', st['timezone']))[:60],
                )
                if st['to_hot'] <= st['to_warming']:
                    st['to_hot'] = st['to_warming'] + 1
                c.execute(
                    "insert into ws_settings values('warmup',?) on conflict(key) do update set value=excluded.value",
                    (json.dumps(st),),
                )
                if st['enabled']:
                    plan(c)
            seen = c.execute("select value from ws_settings where key='warm_agent_seen'").fetchone()
            elig = [dict(r) for r in eligible(c)]
        return jsonify(
            **st,
            agent_online=bool(seen and time.time() - float(seen[0]) < 120),
            eligible=[{k: e[k] for k in ('account_id', 'username', 'persona', 'work_mode')} for e in elig],
        )

    @app.get('/api/warmup/tasks')
    def warm_tasks():
        with conn() as c:
            if settings(c)['enabled']:
                plan(c)
            st = request.args.get('status')
            acc = request.args.get('account_id')
            w = ["t.status!='missed' or t.planned_start is not null"]
            a = []
            if st:
                w.append('t.status=?')
                a.append(st)
            if acc:
                w.append('t.account_id=?')
                a.append(acc)
            return jsonify(task_rows(c, ' and '.join('(' + x + ')' for x in w), a))

    @app.get('/api/warmup/tasks/<tid>')
    def warm_task(tid):
        with conn() as c:
            t = task_rows(c, 't.id=?', (tid,))
            if not t:
                return err('Задача не найдена', 404)
            t = t[0]
            t['events'] = [
                dict(e)
                for e in c.execute(
                    'select event_type,message,details,created_at from ws_task_events where task_id=? order by created_at',
                    (tid,),
                )
            ]
            return jsonify(t)

    @app.post('/api/warmup/run-now')
    def warm_run_now():
        x = body()
        with conn() as c:
            a = [e for e in eligible(c) if e['account_id'] == x.get('account_id')]
            if not a:
                return err(
                    'Аккаунт не готов к прогреву: нужен активный агент на том же телефоне, статус «В системе» и TikTok'
                )
            a = a[0]
            sc = c.execute(
                'select * from ws_scenarios where id=?', (x.get('scenario_id'),)
            ).fetchone() or pick_scenario(c, a['work_mode'] or 'young')
            if c.execute(
                "select 1 from ws_tasks where device_id=? and status='running'", (a['device_id'],)
            ).fetchone():
                return err('На этом телефоне уже идёт сессия', 409)
            minutes = _n(x.get('minutes'), 3, sc['max_duration'], min(10, sc['max_duration']))
            tid = str(uuid.uuid4())
            t = time.time()
            c.execute(
                'insert into ws_tasks(id,persona_id,account_id,device_id,scenario_id,status,task_type,time_slot,plan_date,planned_start,planned_end,duration,actions,worker_notes,created_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    tid,
                    a['persona_id'],
                    a['account_id'],
                    a['device_id'],
                    sc['id'],
                    'scheduled',
                    'session',
                    'manual',
                    None,
                    t,
                    t + minutes * 60,
                    minutes * 60,
                    '{}',
                    'Запущено вручную',
                    now(),
                ),
            )
            event(c, tid, 'created', 'Сессия запущена вручную')
            audit(c, 'create', 'warm_task', tid, {})
        return jsonify(id=tid)

    @app.post('/api/warmup/tasks/<tid>/<action>')
    def warm_task_action(tid, action):
        with conn() as c:
            t = c.execute('select * from ws_tasks where id=?', (tid,)).fetchone()
            if not t:
                return err('Задача не найдена', 404)
            if action == 'cancel':
                if t['status'] not in ('scheduled', 'running'):
                    return err('Отменить можно запланированную или идущую сессию', 409)
                c.execute("update ws_tasks set status='cancelled',finished_at=? where id=?", (now(), tid))
                event(c, tid, 'cancelled', 'Отменено владельцем')
                return jsonify(ok=True)
            if action == 'restart':
                if t['status'] not in RESTARTABLE:
                    return err(
                        'Перезапуск возможен из статусов: отменена, ошибка, нужен человек, пропущена', 409
                    )
                ts = time.time()
                d = t['duration'] or 600
                c.execute(
                    "update ws_tasks set status='scheduled',error=null,planned_start=?,planned_end=?,lease_until=null,started_at=null,finished_at=null where id=?",
                    (ts, ts + d, tid),
                )
                event(c, tid, 'restart', 'Перезапущено принудительно')
                return jsonify(ok=True)
            if action == 'delete':
                if t['status'] not in DELETABLE:
                    return err('Удалить можно только неактивную задачу', 409)
                c.execute('delete from ws_task_events where task_id=?', (tid,))
                c.execute('delete from ws_tasks where id=?', (tid,))
                return jsonify(ok=True)
        return err('Неизвестное действие', 404)

    def scen_payload(x, old=None):
        o = dict(old) if old else {}
        g_ = lambda k, d: x.get(k, o.get(k, d))
        name = str(g_('name', '')).strip()[:80]
        if not name:
            raise ValueError('Укажите название')
        mode = g_('account_mode', 'any')
        mode = mode if mode in MODES else 'any'
        wmin = _n(g_('watch_min', 5), 2, 120, 5)
        wmax = max(wmin, _n(g_('watch_max', 20), 2, 300, 20))
        return dict(
            name=name,
            account_mode=mode,
            max_duration=_n(g_('max_duration', 15), 3, 60, 15),
            watch_min=wmin,
            watch_max=wmax,
            like_pct=_n(g_('like_pct', 10), 0, 60, 10),
            follow_pct=_n(g_('follow_pct', 0), 0, 20, 0),
            search_pct=_n(g_('search_pct', 30), 0, 100, 30),
            max_likes=_n(g_('max_likes', 10), 0, 100, 10),
            max_follows=_n(g_('max_follows', 0), 0, 30, 0),
        )

    @app.route('/api/scenarios', methods=['GET', 'POST'])
    def scenarios():
        with conn() as c:
            if request.method == 'GET':
                return jsonify(
                    [dict(r) for r in c.execute('select * from ws_scenarios order by system desc,created_at')]
                )
            try:
                d = scen_payload(body())
            except ValueError as e:
                return err(str(e))
            sid = str(uuid.uuid4())
            c.execute(
                f"insert into ws_scenarios(id,{','.join(d)},created_at) values(?,{','.join('?' * len(d))},?)",
                (sid, *d.values(), now()),
            )
            audit(c, 'create', 'scenario', sid, {})
            return jsonify(id=sid)

    @app.route('/api/scenarios/<sid>', methods=['PATCH', 'DELETE'])
    def scenario_one(sid):
        with conn() as c:
            s = c.execute('select * from ws_scenarios where id=?', (sid,)).fetchone()
            if not s:
                return err('Сценарий не найден', 404)
            if s['system']:
                return err('Системный шаблон нельзя менять — создайте свой сценарий', 409)
            if request.method == 'DELETE':
                if c.execute(
                    "select 1 from ws_tasks where scenario_id=? and status in ('scheduled','running')", (sid,)
                ).fetchone():
                    return err('Сценарий используется в запланированных сессиях', 409)
                c.execute('delete from ws_scenarios where id=?', (sid,))
                return jsonify(ok=True)
            try:
                d = scen_payload(body(), s)
            except ValueError as e:
                return err(str(e))
            c.execute(
                f"update ws_scenarios set {','.join(k + '=?' for k in d)},updated_at=? where id=?",
                (*d.values(), now(), sid),
            )
            return jsonify(ok=True)

    # ---------- Mac warm agent ----------
    def publication_due(c, did):
        return c.execute(
            "select 1 from ui_jobs where device_id=? and (status in ('RUNNING','VERIFYING') or (status='QUEUED' and available<=?))",
            (did, time.time()),
        ).fetchone()

    @app.post('/api/bridge/warm/claim')
    def warm_claim():
        did = g.bridge_device['id']
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute(
                "insert into ws_settings values('warm_agent_seen',?) on conflict(key) do update set value=excluded.value",
                (str(time.time()),),
            )
            for t in c.execute(
                "select * from ws_tasks where status='running' and lease_until<?", (time.time(),)
            ).fetchall():
                event(c, t['id'], 'lost', 'Mac перестал отвечать')
                finish(c, t, 'failed', 'Mac перестал отвечать во время сессии')
            if not settings(c)['enabled']:
                return jsonify(task=None, reason='disabled')
            plan(c)
            for t in c.execute(
                "select id from ws_tasks where device_id=? and status='scheduled' and planned_start<?",
                (did, time.time() - 3 * 3600),
            ).fetchall():
                c.execute(
                    "update ws_tasks set status='missed',error='Телефон или Mac были недоступны в это время' where id=?",
                    (t['id'],),
                )
            if publication_due(c, did):
                return jsonify(task=None, reason='publication')
            if c.execute("select 1 from ws_tasks where device_id=? and status='running'", (did,)).fetchone():
                return jsonify(task=None, reason='busy')
            t = c.execute(
                "select * from ws_tasks where device_id=? and status='scheduled' and planned_start<=? order by planned_start limit 1",
                (did, time.time()),
            ).fetchone()
            if not t:
                return jsonify(task=None)
            sc = c.execute('select * from ws_scenarios where id=?', (t['scenario_id'],)).fetchone()
            ap = c.execute(
                'select a.username,p.search_keywords,s.denied_keywords from accounts a left join ws_account_profiles p on p.account_id=a.id left join ws_personas s on s.id=p.persona_id where a.id=?',
                (t['account_id'],),
            ).fetchone()
            if not sc or not ap:
                finish(c, t, 'failed', 'Сценарий или аккаунт удалены')
                return jsonify(task=None)
            guard = None
            try:
                import antiban

                guard = antiban.gate_warm(c, t['account_id'])
            except Exception as exc:  # protection must never stop warm-up by itself
                print('antiban warm gate error', type(exc).__name__, exc, flush=True)
            if guard and guard.get('skip'):
                finish(c, t, 'missed', 'Защита от банов: ' + guard['skip'])
                return jsonify(task=None, reason='antiban')
            dur = t['duration'] or 600
            c.execute(
                "update ws_tasks set status='running',started_at=?,lease_until=? where id=?",
                (now(), time.time() + LEASE, t['id']),
            )
            event(c, t['id'], 'started', 'Mac начал сессию')
            kw = [k.strip() for k in (ap['search_keywords'] or '').split(',') if k.strip()]
            # The agent also studies what the owner asked (study tasks) and its interests.
            from agents import study_keywords

            for k in study_keywords(c, t['persona_id']):
                if k.lower() not in {x.lower() for x in kw}:
                    kw.append(k)
            kw = kw[:20]
            deny = [k.strip().lower() for k in (ap['denied_keywords'] or '').split(',') if k.strip()][:30]
            return jsonify(
                task=dict(
                    id=t['id'],
                    username=ap['username'],
                    duration=dur,
                    keywords=kw,
                    denied=deny,
                    scenario={
                        k: sc[k]
                        for k in (
                            'name',
                            'watch_min',
                            'watch_max',
                            'like_pct',
                            'follow_pct',
                            'search_pct',
                            'max_likes',
                            'max_follows',
                        )
                    }
                    | (
                        {
                            'max_likes': min(sc['max_likes'], guard['likes_left']),
                            'max_follows': min(sc['max_follows'], guard['follows_left']),
                        }
                        if guard
                        else {}
                    ),
                )
            )

    def mine(c, tid):
        return c.execute(
            "select * from ws_tasks where id=? and device_id=?", (tid, g.bridge_device['id'])
        ).fetchone()

    @app.post('/api/bridge/warm/<tid>/events')
    def warm_events(tid):
        x = body()
        with conn() as c:
            t = mine(c, tid)
            if not t:
                return jsonify(error='not found'), 404
            if t['status'] != 'running':
                return jsonify(go=False, reason=t['status'])
            for e in (x.get('events') or [])[:30]:
                if isinstance(e, dict):
                    event(
                        c,
                        tid,
                        str(e.get('type', 'info'))[:30],
                        str(e.get('message', ''))[:300],
                        e.get('details') if isinstance(e.get('details'), dict) else None,
                    )
            counts = {
                k: _n(v, 0, 10000, 0)
                for k, v in (x.get('counts') or {}).items()
                if k in ('videos', 'likes', 'follows', 'searches', 'popups', 'seconds')
            }
            c.execute(
                'update ws_tasks set lease_until=?,actions=? where id=?',
                (time.time() + LEASE, json.dumps(counts), tid),
            )
            if publication_due(c, t['device_id']):
                event(c, tid, 'yield', 'Пауза: на телефоне ждёт публикация')
                return jsonify(go=False, reason='publication')
        return jsonify(go=True)

    @app.post('/api/bridge/warm/<tid>/finish')
    def warm_finish(tid):
        x = body()
        status = x.get('status')
        if status not in ('done', 'failed', 'human_intervention'):
            status = 'failed'
        with conn() as c:
            t = mine(c, tid)
            if not t:
                return jsonify(error='not found'), 404
            if t['status'] != 'running':
                return jsonify(ok=True, existing=True)
            counts = {
                k: _n(v, 0, 10000, 0)
                for k, v in (x.get('counts') or {}).items()
                if k in ('videos', 'likes', 'follows', 'searches', 'popups', 'seconds')
            }
            msg = str(x.get('error') or '')[:300] or None
            event(
                c,
                tid,
                'finished',
                {
                    'done': 'Сессия завершена',
                    'failed': 'Сессия прервана: ' + (msg or ''),
                    'human_intervention': 'Нужен человек: ' + (msg or ''),
                }[status],
                counts,
            )
            finish(
                c,
                t,
                status,
                msg,
                counts,
                _n(x.get('seconds'), 0, 7200, None) if x.get('seconds') is not None else None,
            )
        return jsonify(ok=True)

    # publications must not start while a warm-up session holds the phone
    orig = app.view_functions.get('bridge_claim')
    if orig:

        def claim_with_warmup():
            with conn() as c:
                busy = c.execute(
                    "select 1 from ws_tasks where device_id=? and status='running' and lease_until>?",
                    (g.bridge_device['id'], time.time()),
                ).fetchone()
            if busy:
                return jsonify(job=None, paused=False, busy='warmup')
            return orig()

        app.view_functions['bridge_claim'] = claim_with_warmup

    import hashlib, os, shlex
    from flask import send_from_directory

    @app.get('/warm-install.py')
    def warm_installer():
        return send_from_directory(
            os.path.join(app.root_path, 'bridge'), 'warm_install.py', mimetype='text/plain'
        )

    @app.get('/api/warm-install-command')
    def warm_install_command():
        with open(os.path.join(app.root_path, 'bridge', 'warm_install.py'), 'rb') as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        boot = (
            "import requests,hashlib; r=requests.get('https://verticalos-rxdl.onrender.com/warm-install.py',timeout=60); r.raise_for_status(); s=r.content; hashlib.sha256(s).hexdigest()=="
            + repr(sha)
            + " or __import__('sys').exit('Checksum mismatch'); exec(compile(s,'warm_install.py','exec'))"
        )
        return jsonify(
            command='"$HOME/Downloads/faxclip-telegram-bridge-v14/.venv/bin/python" -c ' + shlex.quote(boot),
            sha256=sha,
        )
