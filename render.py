"""Video assembly (QUICON "Склейка") + Sources (distribution and ramp-up).
Rendering runs on the owner's Mac (render agent, ffmpeg) because the free server can't render.
The server keeps recipes, texts, schedule, quotas and hands out render jobs to the Mac."""

import json, os, random, re, time, uuid, csv, io, hashlib, shlex
from datetime import datetime, timezone
from flask import request, jsonify, send_from_directory

SCHEMA = """
CREATE TABLE IF NOT EXISTS ws_recipes(id TEXT PRIMARY KEY,name TEXT NOT NULL,enabled INTEGER DEFAULT 0,source_type TEXT DEFAULT 'local',
  source_url TEXT,scenes TEXT,text_style TEXT,plate TEXT,audio TEXT,timing TEXT,text_mode TEXT DEFAULT 'sequential',text_pos INTEGER DEFAULT 0,
  period_hours INTEGER DEFAULT 0,next_run_at REAL,auto_publish INTEGER DEFAULT 0,rights_confirmed INTEGER DEFAULT 0,caption_template TEXT,
  scan TEXT,created_at TEXT NOT NULL,updated_at TEXT);
CREATE TABLE IF NOT EXISTS ws_recipe_texts(id TEXT PRIMARY KEY,recipe_id TEXT NOT NULL,idx INTEGER NOT NULL,text TEXT,title TEXT,description TEXT);
CREATE TABLE IF NOT EXISTS ws_render_jobs(id TEXT PRIMARY KEY,recipe_id TEXT,batch_id TEXT,kind TEXT DEFAULT 'render',status TEXT DEFAULT 'queued',
  payload TEXT,result TEXT,error TEXT,clip_id TEXT,account_id TEXT,post_id TEXT,lease_until REAL,attempts INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,started_at TEXT,finished_at TEXT);
CREATE TABLE IF NOT EXISTS ws_sources(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,recipe_id TEXT NOT NULL,weight INTEGER DEFAULT 1,active INTEGER DEFAULT 1,
  start_per_day INTEGER DEFAULT 1,target_per_day INTEGER DEFAULT 3,ramp_days INTEGER DEFAULT 7,created_at TEXT NOT NULL,UNIQUE(account_id,recipe_id));
"""
TEXT_DEF = dict(
    font='Arial Bold',
    size=64,
    color='#FFFFFF',
    stroke_color='#000000',
    stroke_width=3,
    line_spacing=1.15,
    max_lines=4,
    align='center',
)
PLATE_DEF = dict(
    enabled=True, color='#000000', opacity=55, position='center', offset=0, padding=36, radius=28
)
AUDIO_DEF = dict(original=True, original_volume=100, music_folder='', music_volume=35)
TIMING_DEF = dict(text_start=0, text_end=0, max_total=60)
FONTS = (
    'Arial Bold',
    'Arial',
    'Helvetica',
    'Impact',
    'Georgia',
    'Verdana Bold',
    'Trebuchet MS Bold',
    'Courier New Bold',
)
YANDEX = re.compile(
    r'https://(?:disk\.yandex\.(?:ru|com|kz|by|ua|com\.tr)|yadi\.sk)/(?:d|i)/[A-Za-z0-9_\-]{4,100}(?:/.*)?'
)
JOB_TTL = 20 * 60
MAX_ATTEMPTS = 3
VIDEO_EXT = ('.mp4', '.mov', '.m4v', '.webm', '.mkv')
AUDIO_EXT = ('.mp3', '.m4a', '.aac', '.wav', '.ogg')


class Bad(Exception):
    pass


def _hex(v, d):
    v = str(v or d).strip()
    return v.upper() if re.fullmatch(r'#[0-9A-Fa-f]{6}', v) else d


def _num(v, lo, hi, d, cast=int):
    try:
        return max(lo, min(hi, cast(v)))
    except (TypeError, ValueError):
        return d


def _folder(v):
    v = str(v or '').strip().strip('/')
    if len(v) > 300 or '..' in v.split('/') or any(ord(ch) < 32 for ch in v):
        raise Bad('Неверное имя папки: ' + v[:60])
    return v


def clean_text_style(x):
    x = {**TEXT_DEF, **(x or {})}
    return dict(
        font=x['font'] if x['font'] in FONTS else 'Arial Bold',
        size=_num(x['size'], 20, 160, 64),
        color=_hex(x['color'], '#FFFFFF'),
        stroke_color=_hex(x['stroke_color'], '#000000'),
        stroke_width=_num(x['stroke_width'], 0, 12, 3),
        line_spacing=_num(x['line_spacing'], 0.8, 2.5, 1.15, float),
        max_lines=_num(x['max_lines'], 1, 10, 4),
        align=x['align'] if x['align'] in ('left', 'center', 'right') else 'center',
    )


def clean_plate(x):
    x = {**PLATE_DEF, **(x or {})}
    return dict(
        enabled=bool(x['enabled']),
        color=_hex(x['color'], '#000000'),
        opacity=_num(x['opacity'], 0, 100, 55),
        position=x['position'] if x['position'] in ('top', 'center', 'bottom') else 'center',
        offset=_num(x['offset'], -40, 40, 0),
        padding=_num(x['padding'], 0, 120, 36),
        radius=_num(x['radius'], 0, 80, 28),
    )


def clean_audio(x):
    x = {**AUDIO_DEF, **(x or {})}
    return dict(
        original=bool(x['original']),
        original_volume=_num(x['original_volume'], 0, 200, 100),
        music_folder=_folder(x['music_folder']) if x['music_folder'] else '',
        music_volume=_num(x['music_volume'], 0, 200, 35),
    )


def clean_timing(x):
    x = {**TIMING_DEF, **(x or {})}
    return dict(
        text_start=_num(x['text_start'], 0, 600, 0, float),
        text_end=_num(x['text_end'], 0, 600, 0, float),
        max_total=_num(x['max_total'], 3, 600, 60, float),
    )


def clean_scenes(v):
    if not isinstance(v, list) or not v:
        raise Bad('Добавьте хотя бы одну сцену (папку с дублями)')
    if len(v) > 20:
        raise Bad('Не больше 20 сцен')
    out = []
    for s in v:
        s = s if isinstance(s, dict) else {'folder': s}
        f = _folder(s.get('folder'))
        if not f:
            raise Bad('У сцены не указана папка')
        out.append(dict(folder=f, max_sec=_num(s.get('max_sec'), 0, 120, 0, float)))
    return out


def yandex_list(url, path='/', timeout=20):
    import requests

    items = []
    offset = 0
    while True:
        r = requests.get(
            'https://cloud-api.yandex.net/v1/disk/public/resources',
            params={'public_key': url, 'path': path or '/', 'limit': 200, 'offset': offset},
            timeout=timeout,
        )
        if r.status_code == 404:
            raise Bad('Папка не найдена на Яндекс.Диске: ' + (path or '/'))
        if r.status_code >= 400:
            raise Bad('Яндекс.Диск не отдал папку (код %s). Проверьте, что ссылка публичная.' % r.status_code)
        e = r.json().get('_embedded') or {}
        batch = e.get('items') or []
        items += batch
        offset += len(batch)
        if len(batch) < 200 or offset >= e.get('total', 0) or offset > 2000:
            break
    return items


def scan_yandex(url, scenes, music):
    out = []
    ok = True
    root = yandex_list(url, '/')
    out.append(dict(path='/', folders=[i['name'] for i in root if i.get('type') == 'dir'][:60]))
    for f in [s['folder'] for s in scenes] + ([music] if music else []):
        try:
            items = yandex_list(url, '/' + f)
            v = sum(1 for i in items if i.get('type') == 'file' and i['name'].lower().endswith(VIDEO_EXT))
            a = sum(1 for i in items if i.get('type') == 'file' and i['name'].lower().endswith(AUDIO_EXT))
            out.append(dict(path=f, videos=v, audio=a))
            ok = ok and (v > 0 if f != music else a > 0)
        except Bad as e:
            out.append(dict(path=f, error=str(e)))
            ok = False
    return ok, out


def day_quota(src, today=None):
    """Ramp-up: start_per_day on day 0, linearly to target_per_day on day ramp_days."""
    today = today or datetime.now(timezone.utc).date()
    try:
        start = datetime.fromisoformat(src['created_at']).date()
    except (TypeError, ValueError):
        start = today
    d = max(0, (today - start).days)
    a, b, r = src['start_per_day'], src['target_per_day'], max(1, src['ramp_days'])
    return b if d >= r else round(a + (b - a) * d / r)


def register_render(app, conn, now, audit, uploads):
    def err(m, code=400):
        return jsonify(error=m), code

    def body():
        return request.get_json(silent=True) or {}

    def today_start():
        t = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        return t.isoformat()

    def recipe_out(c, r):
        r = dict(r)
        for k in ('scenes', 'text_style', 'plate', 'audio', 'timing', 'scan'):
            try:
                r[k] = json.loads(r[k]) if r[k] else None
            except ValueError:
                r[k] = None
        r['texts_count'] = c.execute(
            'select count(*) from ws_recipe_texts where recipe_id=?', (r['id'],)
        ).fetchone()[0]
        r['jobs'] = {
            k: v
            for k, v in c.execute(
                "select status,count(*) from ws_render_jobs where recipe_id=? and kind='render' group by status",
                (r['id'],),
            )
        }
        last = c.execute(
            "select status,error,finished_at from ws_render_jobs where recipe_id=? and kind='render' and finished_at is not null order by finished_at desc limit 1",
            (r['id'],),
        ).fetchone()
        r['last'] = dict(last) if last else None
        r['sources'] = c.execute(
            'select count(*) from ws_sources where recipe_id=? and active=1', (r['id'],)
        ).fetchone()[0]
        return r

    def recipe_payload(x, old=None):
        old = old or {}
        name = str(x.get('name', old.get('name')) or '').strip()[:100]
        if not name:
            raise Bad('Укажите название рецепта')
        st = x.get('source_type', old.get('source_type') or 'local')
        if st not in ('local', 'yandex'):
            raise Bad('Неверный источник')
        url = str(x.get('source_url', old.get('source_url')) or '').strip()
        if st == 'yandex' and not YANDEX.fullmatch(url):
            raise Bad('Нужна публичная ссылка на папку Яндекс.Диска: https://disk.yandex.ru/d/…')
        if st == 'local':
            url = url or 'FaxClip'
            if not re.fullmatch(r'[^\x00-\x1f]{1,200}', url) or url.startswith('/') or '..' in url.split('/'):
                raise Bad('Папка на Mac указывается относительно «Загрузки», например FaxClip/Склейка')
        period = _num(x.get('period_hours', old.get('period_hours', 0)), 0, 24, 0)
        auto = bool(x.get('auto_publish', old.get('auto_publish', 0)))
        rights = bool(x.get('rights_confirmed', old.get('rights_confirmed', 0)))
        if auto and not rights:
            raise Bad('Для автопубликации подтвердите права на видео и музыку')
        mode = x.get('text_mode', old.get('text_mode') or 'sequential')
        if mode not in ('sequential', 'random'):
            mode = 'sequential'
        cap = str(x.get('caption_template', old.get('caption_template')) or '')
        if len(cap) > 2200:
            raise Bad('Описание длиннее 2200 символов')
        j = lambda k, fn: json.dumps(fn(x[k] if k in x else old.get(k)), ensure_ascii=False)
        return dict(
            name=name,
            enabled=1 if x.get('enabled', old.get('enabled', 0)) else 0,
            source_type=st,
            source_url=url,
            scenes=json.dumps(
                clean_scenes(x['scenes'] if 'scenes' in x else old.get('scenes')), ensure_ascii=False
            ),
            text_style=j('text_style', clean_text_style),
            plate=j('plate', clean_plate),
            audio=j('audio', clean_audio),
            timing=j('timing', clean_timing),
            text_mode=mode,
            period_hours=period,
            auto_publish=1 if auto else 0,
            rights_confirmed=1 if rights else 0,
            caption_template=cap,
        )

    def pick_text(c, r):
        rows = c.execute(
            'select * from ws_recipe_texts where recipe_id=? order by idx', (r['id'],)
        ).fetchall()
        if not rows:
            return None
        if r['text_mode'] == 'random':
            row = random.choice(rows)
        else:
            pos = c.execute('select coalesce(text_pos,0) from ws_recipes where id=?', (r['id'],)).fetchone()[
                0
            ]
            row = rows[pos % len(rows)]
            c.execute('update ws_recipes set text_pos=coalesce(text_pos,0)+1 where id=?', (r['id'],))
        return dict(row)

    def sources_state(c, rid):
        out = []
        for s in c.execute(
            'select s.*,a.username,a.platform,a.device_id from ws_sources s join accounts a on a.id=s.account_id where s.recipe_id=? and s.active=1',
            (rid,),
        ):
            s = dict(s)
            s['quota'] = day_quota(s)
            s['today'] = c.execute(
                "select count(*) from ws_render_jobs where account_id=? and recipe_id=? and created_at>=? and status not in ('failed','canceled')",
                (s['account_id'], rid, today_start()),
            ).fetchone()[0]
            out.append(s)
        return out

    def choose_account(c, r, respect_quota=True):
        if not r['auto_publish']:
            return None
        cands = [s for s in sources_state(c, r['id']) if not respect_quota or s['today'] < s['quota']]
        if not cands:
            return None
        return random.choices(cands, weights=[max(1, s['weight']) for s in cands])[0]['account_id']

    def make_job(c, r, batch=None, account=None):
        r = dict(r)
        t = pick_text(c, r)
        payload = dict(
            recipe_id=r['id'],
            name=r['name'],
            source=dict(type=r['source_type'], url=r['source_url']),
            scenes=json.loads(r['scenes']),
            text=(t or {}).get('text') or '',
            text_style=json.loads(r['text_style']),
            plate=json.loads(r['plate']),
            audio=json.loads(r['audio']),
            timing=json.loads(r['timing']),
            video=dict(w=1080, h=1920, fps=30),
        )
        meta = dict(
            title=(t or {}).get('title') or r['name'],
            caption=(t or {}).get('description')
            or r['caption_template']
            or (t or {}).get('text')
            or r['name'],
        )
        jid = str(uuid.uuid4())
        c.execute(
            'insert into ws_render_jobs(id,recipe_id,batch_id,kind,status,payload,result,account_id,created_at) values(?,?,?,?,?,?,?,?,?)',
            (
                jid,
                r['id'],
                batch,
                'render',
                'queued',
                json.dumps(payload, ensure_ascii=False),
                json.dumps({'meta': meta}, ensure_ascii=False),
                account,
                now(),
            ),
        )
        return jid

    def schedule_due(c):
        t = time.time()
        for r in c.execute(
            'select * from ws_recipes where enabled=1 and period_hours>0 and (next_run_at is null or next_run_at<=?)',
            (t,),
        ).fetchall():
            c.execute(
                'update ws_recipes set next_run_at=? where id=?', (t + r['period_hours'] * 3600, r['id'])
            )
            if c.execute(
                "select 1 from ws_render_jobs where recipe_id=? and kind='render' and status in ('queued','running')",
                (r['id'],),
            ).fetchone():
                continue
            acc = choose_account(c, r)
            if r['auto_publish'] and not acc:
                continue  # all accounts reached today's quota
            make_job(c, r, account=acc)

    # ---------- recipes ----------
    @app.route('/api/recipes', methods=['GET', 'POST'])
    def recipes():
        with conn() as c:
            if request.method == 'GET':
                return jsonify(
                    [recipe_out(c, r) for r in c.execute('select * from ws_recipes order by created_at desc')]
                )
            try:
                d = recipe_payload(body())
            except Bad as e:
                return err(str(e))
            rid = str(uuid.uuid4())
            nxt = time.time() + 60 if d['period_hours'] else None
            c.execute(
                'insert into ws_recipes(id,name,enabled,source_type,source_url,scenes,text_style,plate,audio,timing,text_mode,period_hours,next_run_at,auto_publish,rights_confirmed,caption_template,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    rid,
                    d['name'],
                    d['enabled'],
                    d['source_type'],
                    d['source_url'],
                    d['scenes'],
                    d['text_style'],
                    d['plate'],
                    d['audio'],
                    d['timing'],
                    d['text_mode'],
                    d['period_hours'],
                    nxt,
                    d['auto_publish'],
                    d['rights_confirmed'],
                    d['caption_template'],
                    now(),
                    now(),
                ),
            )
            audit(c, 'create', 'recipe', rid, {})
            return jsonify(recipe_out(c, c.execute('select * from ws_recipes where id=?', (rid,)).fetchone()))

    @app.route('/api/recipes/<rid>', methods=['GET', 'PATCH', 'DELETE'])
    def recipe_one(rid):
        with conn() as c:
            r = c.execute('select * from ws_recipes where id=?', (rid,)).fetchone()
            if not r:
                return err('Рецепт не найден', 404)
            if request.method == 'GET':
                out = recipe_out(c, r)
                out['texts'] = [
                    dict(x)
                    for x in c.execute(
                        'select idx,text,title,description from ws_recipe_texts where recipe_id=? order by idx',
                        (rid,),
                    )
                ]
                return jsonify(out)
            if request.method == 'DELETE':
                if c.execute(
                    "select 1 from ws_render_jobs where recipe_id=? and status='running'", (rid,)
                ).fetchone():
                    return err('Сейчас идёт рендер этого рецепта — дождитесь окончания', 409)
                c.execute(
                    "update ws_render_jobs set status='canceled' where recipe_id=? and status='queued'",
                    (rid,),
                )
                for t in ('ws_recipe_texts', 'ws_sources'):
                    c.execute(f'delete from {t} where recipe_id=?', (rid,))
                c.execute('delete from ws_recipes where id=?', (rid,))
                audit(c, 'delete', 'recipe', rid, {})
                return jsonify(ok=True)
            old = recipe_out(c, r)
            try:
                d = recipe_payload(body(), old)
            except Bad as e:
                return err(str(e))
            nxt = r['next_run_at']
            if d['period_hours'] != r['period_hours'] or (d['enabled'] and not r['enabled']):
                nxt = time.time() + 60 if d['period_hours'] else None
            c.execute(
                'update ws_recipes set name=?,enabled=?,source_type=?,source_url=?,scenes=?,text_style=?,plate=?,audio=?,timing=?,text_mode=?,period_hours=?,next_run_at=?,auto_publish=?,rights_confirmed=?,caption_template=?,updated_at=? where id=?',
                (
                    d['name'],
                    d['enabled'],
                    d['source_type'],
                    d['source_url'],
                    d['scenes'],
                    d['text_style'],
                    d['plate'],
                    d['audio'],
                    d['timing'],
                    d['text_mode'],
                    d['period_hours'],
                    nxt,
                    d['auto_publish'],
                    d['rights_confirmed'],
                    d['caption_template'],
                    now(),
                    rid,
                ),
            )
            audit(c, 'update', 'recipe', rid, {})
            return jsonify(recipe_out(c, c.execute('select * from ws_recipes where id=?', (rid,)).fetchone()))

    def parse_rows(raw_rows):
        out = []
        for row in raw_rows:
            row = [str(v if v is not None else '').strip() for v in (row or [])][:3]
            if not row or not row[0]:
                continue
            while len(row) < 3:
                row.append('')
            if row[0].lower() in ('текст', 'text') and len(out) == 0:
                continue  # header
            if len(row[0]) > 500 or len(row[2]) > 2200:
                raise Bad('Слишком длинный текст в строке %d' % (len(out) + 1))
            out.append(row)
        if len(out) > 5000:
            raise Bad('Не больше 5000 строк')
        return out

    @app.route('/api/recipes/<rid>/texts', methods=['PUT', 'POST'])
    def recipe_texts(rid):
        with conn() as c:
            if not c.execute('select 1 from ws_recipes where id=?', (rid,)).fetchone():
                return err('Рецепт не найден', 404)
        try:
            if request.files.get('file'):
                f = request.files['file']
                name = (f.filename or '').lower()
                data = f.read(10 * 1024 * 1024 + 1)
                if len(data) > 10 * 1024 * 1024:
                    raise Bad('Файл больше 10 МБ')
                if name.endswith('.xlsx'):
                    try:
                        import openpyxl
                    except ImportError:
                        raise Bad('На сервере нет поддержки Excel — сохраните таблицу как CSV')
                    try:
                        ws = openpyxl.load_workbook(
                            io.BytesIO(data), read_only=True, data_only=True
                        ).worksheets[0]
                    except Exception:
                        raise Bad('Не удалось прочитать Excel-файл')
                    rows = parse_rows(ws.iter_rows(values_only=True))
                elif name.endswith(('.csv', '.txt')):
                    text = data.decode('utf-8-sig', errors='replace')
                    if name.endswith('.txt'):
                        rows = parse_rows([[l] for l in text.splitlines()])
                    else:
                        dialect = (
                            csv.Sniffer().sniff(text[:2000], delimiters=',;\t') if text.strip() else csv.excel
                        )
                        rows = parse_rows(csv.reader(io.StringIO(text), dialect))
                else:
                    raise Bad('Нужен файл Excel (.xlsx), CSV или TXT')
                replace = request.form.get('replace', '1') == '1'
            else:
                x = body()
                rows = parse_rows(x.get('rows') or [])
                replace = x.get('replace', True)
        except Bad as e:
            return err(str(e))
        with conn() as c:
            if replace:
                c.execute('delete from ws_recipe_texts where recipe_id=?', (rid,))
                c.execute('update ws_recipes set text_pos=0 where id=?', (rid,))
            base = c.execute(
                'select coalesce(max(idx),-1)+1 from ws_recipe_texts where recipe_id=?', (rid,)
            ).fetchone()[0]
            for i, (t, ti, de) in enumerate(rows):
                c.execute(
                    'insert into ws_recipe_texts values(?,?,?,?,?,?)',
                    (str(uuid.uuid4()), rid, base + i, t, ti, de),
                )
            n = c.execute('select count(*) from ws_recipe_texts where recipe_id=?', (rid,)).fetchone()[0]
        return jsonify(ok=True, added=len(rows), total=n)

    @app.post('/api/recipes/<rid>/texts/reset')
    def recipe_texts_reset(rid):
        with conn() as c:
            c.execute('update ws_recipes set text_pos=0 where id=?', (rid,))
        return jsonify(ok=True)

    @app.post('/api/recipes/<rid>/run')
    def recipe_run(rid):
        n = _num(body().get('count'), 1, 20, 1)
        with conn() as c:
            r = c.execute('select * from ws_recipes where id=?', (rid,)).fetchone()
            if not r:
                return err('Рецепт не найден', 404)
            batch = str(uuid.uuid4())
            ids = [make_job(c, r, batch, choose_account(c, r, respect_quota=False)) for _ in range(n)]
            audit(c, 'run', 'recipe', rid, {'count': n})
        return jsonify(ok=True, batch_id=batch, jobs=ids)

    @app.post('/api/recipes/<rid>/scan')
    def recipe_scan(rid):
        with conn() as c:
            r = c.execute('select * from ws_recipes where id=?', (rid,)).fetchone()
            if not r:
                return err('Рецепт не найден', 404)
            scenes = json.loads(r['scenes'])
            music = json.loads(r['audio']).get('music_folder')
            if r['source_type'] == 'local':
                c.execute(
                    "update ws_render_jobs set status='canceled' where recipe_id=? and kind='scan' and status='queued'",
                    (rid,),
                )
                c.execute(
                    'insert into ws_render_jobs(id,recipe_id,kind,status,payload,created_at) values(?,?,?,?,?,?)',
                    (
                        str(uuid.uuid4()),
                        rid,
                        'scan',
                        'queued',
                        json.dumps(
                            dict(
                                source=dict(type='local', url=r['source_url']),
                                scenes=scenes,
                                music_folder=music,
                            ),
                            ensure_ascii=False,
                        ),
                        now(),
                    ),
                )
                c.execute(
                    'update ws_recipes set scan=? where id=?',
                    (json.dumps(dict(pending=True, at=now()), ensure_ascii=False), rid),
                )
                return jsonify(pending=True)
        try:
            ok, res = scan_yandex(r['source_url'], scenes, music)
            scan = dict(ok=ok, items=res, at=now())
        except Bad as e:
            scan = dict(ok=False, error=str(e), at=now())
        except Exception:
            scan = dict(ok=False, error='Яндекс.Диск не ответил. Попробуйте позже.', at=now())
        with conn() as c:
            c.execute('update ws_recipes set scan=? where id=?', (json.dumps(scan, ensure_ascii=False), rid))
        return jsonify(scan)

    # ---------- render jobs ----------
    @app.get('/api/render-jobs')
    def render_jobs():
        q = "select j.id,j.recipe_id,j.batch_id,j.status,j.error,j.clip_id,j.account_id,j.post_id,j.attempts,j.created_at,j.started_at,j.finished_at,j.result,r.name recipe,a.username from ws_render_jobs j left join ws_recipes r on r.id=j.recipe_id left join accounts a on a.id=j.account_id where j.kind='render'"
        args = []
        if request.args.get('recipe_id'):
            q += ' and j.recipe_id=?'
            args.append(request.args['recipe_id'])
        if request.args.get('status'):
            q += ' and j.status=?'
            args.append(request.args['status'])
        with conn() as c:
            rows = []
            for r in c.execute(q + ' order by j.created_at desc limit 200', args):
                r = dict(r)
                try:
                    res = json.loads(r.pop('result') or '{}')
                except ValueError:
                    res = {}
                r['title'] = (res.get('meta') or {}).get('title')
                r['files'] = res.get('files')
                r['duration'] = res.get('duration')
                rows.append(r)
            seen = c.execute("select value from ws_settings where key='render_agent_seen'").fetchone()
        return jsonify(
            items=rows,
            agent_seen=float(seen[0]) if seen else None,
            agent_online=bool(seen and time.time() - float(seen[0]) < 90),
        )

    @app.post('/api/render-jobs/<jid>/retry')
    def render_retry(jid):
        with conn() as c:
            n = c.execute(
                "update ws_render_jobs set status='queued',error=null,attempts=0,lease_until=null where id=? and status in ('failed','canceled')",
                (jid,),
            ).rowcount
        return jsonify(ok=True) if n else err('Повторить можно только задачу с ошибкой или отменённую', 409)

    @app.post('/api/render-jobs/<jid>/cancel')
    def render_cancel(jid):
        with conn() as c:
            n = c.execute(
                "update ws_render_jobs set status='canceled' where id=? and status='queued'", (jid,)
            ).rowcount
        return jsonify(ok=True) if n else err('Отменить можно только задачу в очереди', 409)

    # ---------- sources ----------
    @app.route('/api/sources', methods=['GET', 'POST'])
    def sources():
        with conn() as c:
            if request.method == 'GET':
                out = []
                for s in c.execute(
                    'select s.*,a.username,a.platform,r.name recipe from ws_sources s left join accounts a on a.id=s.account_id left join ws_recipes r on r.id=s.recipe_id order by s.created_at'
                ):
                    s = dict(s)
                    s['quota'] = day_quota(s)
                    s['today'] = c.execute(
                        "select count(*) from ws_render_jobs where account_id=? and recipe_id=? and created_at>=? and status not in ('failed','canceled')",
                        (s['account_id'], s['recipe_id'], today_start()),
                    ).fetchone()[0]
                    out.append(s)
                return jsonify(out)
            x = body()
            if not c.execute('select 1 from accounts where id=?', (x.get('account_id'),)).fetchone():
                return err('Выберите аккаунт')
            if not c.execute('select 1 from ws_recipes where id=?', (x.get('recipe_id'),)).fetchone():
                return err('Выберите рецепт склейки')
            if c.execute(
                'select 1 from ws_sources where account_id=? and recipe_id=?',
                (x['account_id'], x['recipe_id']),
            ).fetchone():
                return err('Этот аккаунт уже получает видео из этого рецепта')
            sid = str(uuid.uuid4())
            st = _num(x.get('start_per_day'), 0, 50, 1)
            tg = _num(x.get('target_per_day'), 1, 50, 3)
            c.execute(
                'insert into ws_sources values(?,?,?,?,?,?,?,?,?)',
                (
                    sid,
                    x['account_id'],
                    x['recipe_id'],
                    _num(x.get('weight'), 1, 10, 1),
                    1 if x.get('active', True) else 0,
                    min(st, tg),
                    tg,
                    _num(x.get('ramp_days'), 1, 90, 7),
                    now(),
                ),
            )
            audit(c, 'create', 'source', sid, {})
            return jsonify(id=sid)

    @app.route('/api/sources/<sid>', methods=['PATCH', 'DELETE'])
    def source_one(sid):
        with conn() as c:
            s = c.execute('select * from ws_sources where id=?', (sid,)).fetchone()
            if not s:
                return err('Не найдено', 404)
            if request.method == 'DELETE':
                c.execute('delete from ws_sources where id=?', (sid,))
                return jsonify(ok=True)
            x = {**dict(s), **body()}
            st = _num(x['start_per_day'], 0, 50, 1)
            tg = _num(x['target_per_day'], 1, 50, 3)
            c.execute(
                'update ws_sources set weight=?,active=?,start_per_day=?,target_per_day=?,ramp_days=? where id=?',
                (
                    _num(x['weight'], 1, 10, 1),
                    1 if x['active'] else 0,
                    min(st, tg),
                    tg,
                    _num(x['ramp_days'], 1, 90, 7),
                    sid,
                ),
            )
            return jsonify(ok=True)

    @app.get('/render-install.py')
    def render_installer():
        # Public software only; no tokens. The Mac reads its own device credentials locally.
        return send_from_directory(
            os.path.join(app.root_path, 'bridge'), 'render_install.py', mimetype='text/plain'
        )

    @app.get('/api/render-install-command')
    def render_install_command():
        with open(os.path.join(app.root_path, 'bridge', 'render_install.py'), 'rb') as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        boot = (
            "import requests,hashlib; r=requests.get('https://verticalos-rxdl.onrender.com/render-install.py',timeout=60); r.raise_for_status(); s=r.content; hashlib.sha256(s).hexdigest()=="
            + repr(sha)
            + " or __import__('sys').exit('Checksum mismatch'); exec(compile(s,'render_install.py','exec'))"
        )
        return jsonify(
            command='"$HOME/Downloads/faxclip-telegram-bridge-v14/.venv/bin/python" -c ' + shlex.quote(boot),
            sha256=sha,
        )

    # ---------- Mac render agent ----------
    def seen(c):
        c.execute(
            "insert into ws_settings values('render_agent_seen',?) on conflict(key) do update set value=excluded.value",
            (str(time.time()),),
        )

    @app.post('/api/bridge/render/claim')
    def render_claim():
        info = body()
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            seen(c)
            if info.get('version'):
                c.execute(
                    "insert into ws_settings values('render_agent_info',?) on conflict(key) do update set value=excluded.value",
                    (
                        json.dumps(
                            {'version': str(info.get('version'))[:40], 'ffmpeg': bool(info.get('ffmpeg'))}
                        ),
                    ),
                )
            for j in c.execute(
                "select id,attempts from ws_render_jobs where status='running' and lease_until<?",
                (time.time(),),
            ).fetchall():
                c.execute(
                    "update ws_render_jobs set status=?,error=? where id=?",
                    (
                        'failed' if j['attempts'] >= MAX_ATTEMPTS else 'queued',
                        'Mac не ответил вовремя' if j['attempts'] >= MAX_ATTEMPTS else None,
                        j['id'],
                    ),
                )
            schedule_due(c)
            j = c.execute(
                "select * from ws_render_jobs where status='queued' order by case kind when 'scan' then 0 else 1 end,created_at limit 1"
            ).fetchone()
            if not j:
                return jsonify(job=None)
            c.execute(
                "update ws_render_jobs set status='running',attempts=attempts+1,lease_until=?,started_at=? where id=?",
                (time.time() + JOB_TTL, now(), j['id']),
            )
        return jsonify(job=dict(id=j['id'], kind=j['kind'], payload=json.loads(j['payload'])))

    def running(c, jid):
        return c.execute("select * from ws_render_jobs where id=? and status='running'", (jid,)).fetchone()

    @app.post('/api/bridge/render/<jid>/renew')
    def render_renew(jid):
        with conn() as c:
            seen(c)
            n = c.execute(
                "update ws_render_jobs set lease_until=? where id=? and status='running'",
                (time.time() + JOB_TTL, jid),
            ).rowcount
        return jsonify(ok=bool(n)), (200 if n else 409)

    @app.post('/api/bridge/render/<jid>/fail')
    def render_fail(jid):
        msg = str(body().get('error') or 'Ошибка рендера')[:500]
        with conn() as c:
            if not running(c, jid):
                return err('Задача не выполняется', 409)
            c.execute(
                "update ws_render_jobs set status='failed',error=?,finished_at=? where id=?",
                (msg, now(), jid),
            )
            j = c.execute('select kind,recipe_id from ws_render_jobs where id=?', (jid,)).fetchone()
            if j['kind'] == 'scan':
                c.execute(
                    'update ws_recipes set scan=? where id=?',
                    (json.dumps(dict(ok=False, error=msg, at=now()), ensure_ascii=False), j['recipe_id']),
                )
            else:
                try:
                    from notify import emit

                    r = c.execute('select name from ws_recipes where id=?', (j['recipe_id'],)).fetchone()
                    emit(
                        c,
                        'error',
                        'render_failed',
                        'Склейка не получилась · ' + (r['name'] if r else ''),
                        msg,
                        'assembly',
                    )
                except Exception:
                    pass
        return jsonify(ok=True)

    @app.post('/api/bridge/render/<jid>/scan-result')
    def render_scan_result(jid):
        x = body()
        with conn() as c:
            j = running(c, jid)
            if not j or j['kind'] != 'scan':
                return err('Задача не выполняется', 409)
            items = [
                dict(
                    path=str(i.get('path', ''))[:300],
                    videos=_num(i.get('videos'), 0, 100000, 0),
                    audio=_num(i.get('audio'), 0, 100000, 0),
                    error=str(i['error'])[:300] if i.get('error') else None,
                    folders=[str(f)[:100] for f in (i.get('folders') or [])][:60],
                )
                for i in (x.get('items') or [])[:40]
                if isinstance(i, dict)
            ]
            scan = dict(
                ok=bool(x.get('ok')),
                items=items,
                at=now(),
                error=str(x.get('error'))[:300] if x.get('error') else None,
            )
            c.execute(
                'update ws_recipes set scan=? where id=?',
                (json.dumps(scan, ensure_ascii=False), j['recipe_id']),
            )
            c.execute("update ws_render_jobs set status='done',finished_at=? where id=?", (now(), jid))
        return jsonify(ok=True)

    @app.post('/api/bridge/render/<jid>/result')
    def render_result(jid):
        f = request.files.get('file')
        try:
            meta = json.loads(request.form.get('meta') or '{}')
        except ValueError:
            meta = {}
        with conn() as c:
            j = running(c, jid)
            if not j or j['kind'] != 'render':
                return err('Задача не выполняется', 409)
        if not f:
            return err('Нужен MP4-файл')
        name = uuid.uuid4().hex + '.mp4'
        path = os.path.join(uploads, name)
        os.makedirs(uploads, exist_ok=True)
        f.save(path)
        with open(path, 'rb') as m:
            head = m.read(16)
        if len(head) < 12 or head[4:8] != b'ftyp':
            os.unlink(path)
            return err('Файл не MP4')
        res = json.loads(j['result'] or '{}')
        m = res.get('meta') or {}
        res.update(
            files=[str(x)[:200] for x in (meta.get('files') or [])][:20],
            duration=_num(meta.get('duration'), 0, 3600, 0, float),
        )
        cid = str(uuid.uuid4())
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            if not running(c, jid):
                os.unlink(path)
                return err('Задача уже завершена', 409)
            c.execute(
                'insert into clips values(?,?,?,?,?,?,?)',
                (cid, (m.get('title') or 'Склейка')[:100], name, res['duration'], 0, 'READY', now()),
            )
            c.execute(
                'insert or replace into clip_captions values(?,?)', (cid, (m.get('caption') or '')[:2200])
            )
            c.execute(
                "update ws_render_jobs set status='done',clip_id=?,result=?,finished_at=?,error=null where id=?",
                (cid, json.dumps(res, ensure_ascii=False), now(), jid),
            )
            r = c.execute('select * from ws_recipes where id=?', (j['recipe_id'],)).fetchone()
        post_id = None
        post_err = None
        if j['account_id'] and r and r['auto_publish'] and r['rights_confirmed']:
            try:
                post_id, results = app.extensions['faxclip_create_post'](
                    m.get('title'), m.get('caption') or '', cid, [j['account_id']]
                )
                post_err = next((e for _, _, e in results if e), None)
            except Exception as e:
                post_err = str(e)[:300]
        with conn() as c:
            c.execute(
                'update ws_render_jobs set post_id=?,error=? where id=?',
                (post_id, ('Пост не создан: ' + post_err) if post_err else None, jid),
            )
            try:
                from notify import emit

                emit(
                    c,
                    'success',
                    'render_done',
                    'Ролик склеен · ' + (r['name'] if r else ''),
                    ('Отправлен в посты' if post_id and not post_err else 'Лежит в «Контент»'),
                    'assembly',
                )
            except Exception:
                pass
        return jsonify(ok=True, clip_id=cid, post_id=post_id)
