"""Agents (formerly «Персоны»): a persona that performs concrete tasks.

* Tasks: measurable goals (watch minutes, likes, follows, searches) and «study» items — topics,
  hashtags or accounts the owner wants the agent to learn. Study items and interests are added to
  the search keywords of every warm-up session of the agent's accounts (see warmup claim).
* Analytics: everything is computed from real warm-up sessions (ws_tasks.actions written by the
  Mac warm agent) and real publications of the agent's accounts. Nothing is invented.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from flask import jsonify, request

KINDS = {
    'study_topic': 'Изучать тему',
    'study_account': 'Изучать аккаунт',
    'watch_minutes': 'Смотреть ролики (минут)',
    'likes': 'Ставить лайки',
    'follows': 'Подписываться',
    'searches': 'Искать по темам',
}
STUDY = {'study_topic', 'study_account'}
METRIC = {'watch_minutes': 'minutes', 'likes': 'likes', 'follows': 'follows', 'searches': 'searches'}


def study_keywords(c, persona_id, limit=20):
    """Keywords an agent should search for: active study items first, then interests."""
    if not persona_id:
        return []
    out = []
    for r in c.execute(
        "select kind,value from ws_agent_tasks where persona_id=? and status='active' and kind in ('study_topic','study_account') order by created_at",
        (persona_id,),
    ).fetchall():
        v = (r['value'] or '').strip()
        if r['kind'] == 'study_account' and v and not v.startswith('@'):
            v = '@' + v
        if v:
            out.append(v)
    for r in c.execute(
        'select tag from ws_persona_interests where persona_id=? order by weight desc', (persona_id,)
    ).fetchall():
        if r['tag']:
            out.append(r['tag'].strip())
    seen, res = set(), []
    for k in out:
        if k.lower() not in seen:
            seen.add(k.lower())
            res.append(k)
    return res[:limit]


def _since(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _sessions(c, pid, since_iso):
    rows = c.execute(
        'select t.status,t.actions,t.duration,t.account_id,t.plan_date,t.finished_at,t.created_at,a.username,a.platform '
        'from ws_tasks t left join accounts a on a.id=t.account_id where t.persona_id=? and t.created_at>=?',
        (pid, since_iso),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d['actions'] = json.loads(d['actions'] or '{}')
        except ValueError:
            d['actions'] = {}
        out.append(d)
    return out


def _totals(sessions):
    t = {
        'done': 0,
        'failed': 0,
        'missed': 0,
        'scheduled': 0,
        'minutes': 0,
        'videos': 0,
        'likes': 0,
        'follows': 0,
        'searches': 0,
    }
    for s in sessions:
        st = s['status']
        if st in t:
            t[st] += 1
        a = s['actions'] or {}
        t['minutes'] += round((a.get('seconds') or 0) / 60)
        for k in ('videos', 'likes', 'follows', 'searches'):
            t[k] += int(a.get(k) or 0)
    return t


def register_agents(app, conn, now, audit):
    def err(m, code=400):
        return jsonify(error=m), code

    def persona(c, pid):
        return c.execute('select id,name from ws_personas where id=?', (pid,)).fetchone()

    def task_rows(c, pid):
        rows = [
            dict(r)
            for r in c.execute(
                'select * from ws_agent_tasks where persona_id=? order by created_at', (pid,)
            ).fetchall()
        ]
        for r in rows:
            r['label'] = KINDS.get(r['kind'], r['kind'])
            r['progress'] = None
            if r['kind'] in METRIC:
                today = datetime.now(timezone.utc).date().isoformat()
                since = today if r['period'] == 'day' else r['created_at']
                tot = _totals([s for s in _sessions(c, pid, since) if s['status'] == 'done'])
                r['progress'] = tot[METRIC[r['kind']]]
            elif r['kind'] in STUDY:
                kw = (r['value'] or '').lower().lstrip('@')
                n = 0
                for s in _sessions(c, pid, r['created_at']):
                    if s['status'] == 'done' and kw and (s['actions'] or {}).get('searches'):
                        n += 1
                r['progress'] = n
        return rows

    @app.get('/api/agents/<pid>/overview')
    def agent_overview(pid):
        c = conn()
        p = persona(c, pid)
        if not p:
            return err('Агент не найден', 404)
        try:
            days = max(1, min(90, int(request.args.get('days', 30))))
        except ValueError:
            days = 30
        sessions = _sessions(c, pid, _since(days))
        by_day = {}
        for s in sessions:
            day = (s['plan_date'] or s['created_at'] or '')[:10]
            d = by_day.setdefault(day, {'day': day, 'sessions': 0, 'minutes': 0, 'videos': 0, 'likes': 0})
            if s['status'] == 'done':
                d['sessions'] += 1
                a = s['actions'] or {}
                d['minutes'] += round((a.get('seconds') or 0) / 60)
                d['videos'] += int(a.get('videos') or 0)
                d['likes'] += int(a.get('likes') or 0)
        accounts = {}
        for r in c.execute(
            'select a.id,a.username,a.platform from accounts a join ws_account_profiles p on p.account_id=a.id where p.persona_id=?',
            (pid,),
        ).fetchall():
            accounts[r['id']] = {
                'username': r['username'],
                'platform': r['platform'],
                'sessions': 0,
                'videos': 0,
                'likes': 0,
                'published': 0,
            }
        for s in sessions:
            a = accounts.get(s['account_id'])
            if a and s['status'] == 'done':
                a['sessions'] += 1
                a['videos'] += int((s['actions'] or {}).get('videos') or 0)
                a['likes'] += int((s['actions'] or {}).get('likes') or 0)
        if accounts:
            marks = ','.join('?' * len(accounts))
            for r in c.execute(
                f"select account_id,count(*) n from publications where status='PUBLISHED' and account_id in ({marks}) and created_at>=? group by account_id",
                (*accounts.keys(), _since(days)),
            ).fetchall():
                accounts[r['account_id']]['published'] = r['n']
        return jsonify(
            id=pid,
            name=p['name'],
            days=days,
            totals=_totals(sessions),
            by_day=sorted(by_day.values(), key=lambda x: x['day']),
            accounts=list(accounts.values()),
            tasks=task_rows(c, pid),
            keywords=study_keywords(c, pid),
            kinds=KINDS,
        )

    @app.post('/api/agents/<pid>/tasks')
    def agent_task_add(pid):
        c = conn()
        if not persona(c, pid):
            return err('Агент не найден', 404)
        x = request.get_json(silent=True) or {}
        kind = x.get('kind')
        if kind not in KINDS:
            return err('Неизвестный тип задачи')
        value = str(x.get('value') or '').strip()[:80]
        if kind in STUDY and not value:
            return err('Укажите, что изучать: тему, хэштег или @аккаунт')
        target = None
        if kind in METRIC:
            try:
                target = int(x.get('target'))
            except (TypeError, ValueError):
                return err('Укажите цель числом')
            if not 1 <= target <= 100000:
                return err('Цель должна быть от 1 до 100000')
        period = 'day' if x.get('period') == 'day' else 'total'
        if (
            c.execute(
                "select count(*) from ws_agent_tasks where persona_id=? and status!='done'", (pid,)
            ).fetchone()[0]
            >= 30
        ):
            return err('У агента уже 30 задач — завершите или удалите лишние')
        tid = str(uuid.uuid4())
        c.execute(
            'insert into ws_agent_tasks(id,persona_id,kind,value,target,period,status,created_at) values(?,?,?,?,?,?,?,?)',
            (tid, pid, kind, value or None, target, period, 'active', now()),
        )
        audit(c, 'create', 'agent_task', tid, {'persona': pid, 'kind': kind})
        c.commit()
        return jsonify(ok=True, id=tid)

    @app.route('/api/agents/<pid>/tasks/<tid>', methods=['PATCH', 'DELETE'])
    def agent_task_edit(pid, tid):
        c = conn()
        if not c.execute('select 1 from ws_agent_tasks where id=? and persona_id=?', (tid, pid)).fetchone():
            return err('Задача не найдена', 404)
        if request.method == 'DELETE':
            c.execute('delete from ws_agent_tasks where id=?', (tid,))
            audit(c, 'delete', 'agent_task', tid)
        else:
            st = (request.get_json(silent=True) or {}).get('status')
            if st not in ('active', 'paused', 'done'):
                return err('Неверный статус')
            c.execute('update ws_agent_tasks set status=? where id=?', (st, tid))
            audit(c, 'update', 'agent_task', tid, {'status': st})
        c.commit()
        return jsonify(ok=True)
