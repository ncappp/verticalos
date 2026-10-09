
import os, sqlite3, uuid, secrets, subprocess, shutil, hashlib, hmac, json
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory, g, has_request_context
from telegram_auth import validate_init_data, allowed_user, TelegramAuthError

BASE=os.path.dirname(os.path.abspath(__file__))
DATA=os.getenv("VERTICALOS_DATA_DIR",os.path.join(BASE,"data"))
DB=os.getenv("VERTICALOS_DB",os.path.join(DATA,"verticalos.db"))
UPLOAD=os.getenv("VERTICALOS_UPLOAD_DIR",os.path.join(DATA,"uploads"))
os.makedirs(DATA,exist_ok=True)
os.makedirs(UPLOAD,exist_ok=True)

app=Flask(__name__,static_folder=None)
app.config["MAX_CONTENT_LENGTH"]=1024*1024*1024

def now(): return datetime.now(timezone.utc).isoformat()

class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:return super().__exit__(*args)
        finally:self.close()

@app.teardown_request
def close_request_connections(error):
    for connection in getattr(g,"_db_connections",[]):
        try:connection.close()
        except sqlite3.Error:pass

def conn():
    c=sqlite3.connect(DB,timeout=30,factory=ClosingConnection)
    if has_request_context():
        if not hasattr(g,"_db_connections"):g._db_connections=[]
        g._db_connections.append(c)
    c.row_factory=sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA journal_mode=WAL")
    return c

def token_hash(token): return hashlib.sha256(token.encode("utf-8")).hexdigest()
def bearer():
    value=request.headers.get("Authorization","")
    return value[7:] if value.startswith("Bearer ") else ""
def device_auth(c,did):
    row=c.execute("select * from devices where id=?",(did,)).fetchone(); token=bearer()
    if not row or not token or not hmac.compare_digest(row["token_hash"] or "",token_hash(token)): return None
    return row
def audit(c,action,entity_type,entity_id,payload=None):
    c.execute("insert into audit_log values(?,?,?,?,?,?)",(str(uuid.uuid4()),action,entity_type,entity_id,json.dumps(payload or {},ensure_ascii=False),now()))

@app.before_request
def telegram_guard():
    local_token=os.getenv("FAXCLIP_LOCAL_TOKEN","")
    if local_token:
        from urllib.parse import urlparse
        if request.remote_addr not in ("127.0.0.1","::1") or request.host.split(":")[0] not in ("127.0.0.1","localhost"):
            return jsonify(error="Local FaxClip accepts only loopback requests"),403
        origin=request.headers.get("Origin")
        if origin and urlparse(origin).netloc!=request.host:return jsonify(error="Origin rejected"),403
        if request.path=="/api/local-session":return None
        if request.path.startswith("/api/") and not request.path.startswith("/api/bridge/") and request.path!="/api/health":
            cookie=request.cookies.get("faxclip_local","")
            if not cookie or not hmac.compare_digest(cookie,local_token):return jsonify(error="Open FaxClip from its Mac launcher"),401
            g.telegram_user={"id":0,"first_name":"Local owner"};return None
    if request.path.startswith("/api/bridge/"):return None
    if not request.path.startswith("/api/") or request.path=="/api/health":
        return None
    if request.path.startswith("/api/devices/") and request.headers.get("Authorization","").startswith("Bearer "):
        return None
    if os.getenv("ALLOW_DEV_AUTH","0")=="1":
        g.telegram_user={"id":0,"first_name":"Developer"}
        return None
    try:
        user=validate_init_data(request.headers.get("X-Telegram-Init-Data",""),os.getenv("TELEGRAM_BOT_TOKEN",""))
        if not allowed_user(user): return jsonify(error="Telegram user is not allowed"),403
        g.telegram_user=user
    except TelegramAuthError as exc:
        return jsonify(error=str(exc)),401

def init_db():
    import tg_backup
    tg_backup.restore_if_empty(DB)
    c=conn()
    c.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE IF NOT EXISTS devices(
      id TEXT PRIMARY KEY,name TEXT NOT NULL,model TEXT,connection TEXT,
      status TEXT DEFAULT 'PENDING',battery INTEGER DEFAULT 0,token_hash TEXT,
      last_seen TEXT,created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS accounts(
      id TEXT PRIMARY KEY,platform TEXT NOT NULL,username TEXT,niche TEXT,
      audience TEXT,status TEXT DEFAULT 'PENDING',device_id TEXT,
      created_at TEXT NOT NULL,FOREIGN KEY(device_id) REFERENCES devices(id)
    );
    CREATE TABLE IF NOT EXISTS clips(
      id TEXT PRIMARY KEY,title TEXT NOT NULL,source_file TEXT,duration REAL,
      score INTEGER DEFAULT 0,status TEXT DEFAULT 'READY',created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS publications(
      id TEXT PRIMARY KEY,clip_id TEXT,account_id TEXT,platform TEXT,status TEXT DEFAULT 'QUEUED',
      scheduled_at TEXT,published_at TEXT,external_id TEXT,error TEXT,created_at TEXT NOT NULL,
      FOREIGN KEY(clip_id) REFERENCES clips(id),FOREIGN KEY(account_id) REFERENCES accounts(id)
    );
    CREATE TABLE IF NOT EXISTS tasks(
      id TEXT PRIMARY KEY,title TEXT NOT NULL,target INTEGER NOT NULL,done INTEGER DEFAULT 0,
      unit TEXT DEFAULT 'ед.',deadline TEXT,created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS metrics(
      id TEXT PRIMARY KEY,account_id TEXT,clip_id TEXT,platform TEXT,views INTEGER DEFAULT 0,
      likes INTEGER DEFAULT 0,comments INTEGER DEFAULT 0,shares INTEGER DEFAULT 0,
      followers INTEGER DEFAULT 0,retention REAL DEFAULT 0,created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS activity_plans(
      id TEXT PRIMARY KEY,account_id TEXT UNIQUE,enabled INTEGER DEFAULT 0,
      session_items INTEGER DEFAULT 30,min_watch INTEGER DEFAULT 5,max_watch INTEGER DEFAULT 20,
      like_percent INTEGER DEFAULT 15,follow_percent INTEGER DEFAULT 3,
      schedule TEXT DEFAULT '09:00,14:00,20:00',updated_at TEXT NOT NULL,
      FOREIGN KEY(account_id) REFERENCES accounts(id)
    );
    CREATE TABLE IF NOT EXISTS media_variants(
      id TEXT PRIMARY KEY,clip_id TEXT,kind TEXT,filename TEXT,created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS device_jobs(
      id TEXT PRIMARY KEY,device_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,
      status TEXT DEFAULT 'QUEUED',attempts INTEGER DEFAULT 0,available_at TEXT,
      locked_at TEXT,completed_at TEXT,result TEXT,error TEXT,created_at TEXT NOT NULL,
      FOREIGN KEY(device_id) REFERENCES devices(id)
    );
    CREATE TABLE IF NOT EXISTS audit_log(
      id TEXT PRIMARY KEY,action TEXT NOT NULL,entity_type TEXT NOT NULL,entity_id TEXT,
      payload TEXT,created_at TEXT NOT NULL
    );
    """)
    cols={r["name"] for r in c.execute("pragma table_info(tasks)")}
    if "period" not in cols:c.execute("alter table tasks add column period TEXT DEFAULT 'DAY'")
    if "period_start" not in cols:c.execute("alter table tasks add column period_start TEXT")
    if "period_end" not in cols:c.execute("alter table tasks add column period_end TEXT")
    from workspace import SCHEMA as WS_SCHEMA
    c.executescript(WS_SCHEMA)
    from notify import SCHEMA as NOTIFY_SCHEMA
    c.executescript(NOTIFY_SCHEMA)
    c.commit(); c.close()
    from maintenance import apply_bootstrap
    apply_bootstrap(conn,now)
    tg_backup.start(DB)

@app.post("/api/local-session")
def local_session():
    token=os.getenv("FAXCLIP_LOCAL_TOKEN","")
    value=(request.get_json(silent=True) or {}).get("token","")
    if not token or not isinstance(value,str) or not hmac.compare_digest(token,value):return jsonify(error="Invalid local session"),401
    r=jsonify(ok=True);r.set_cookie("faxclip_local",token,httponly=True,samesite="Strict",secure=False,max_age=43200);return r

@app.get("/")
def home():
    if os.getenv("FAXCLIP_LOCAL_TOKEN"):
        with open(os.path.join(BASE,"index.html"),encoding="utf8") as f:html=f.read()
        return html.replace('<script src="https://telegram.org/js/telegram-web-app.js"></script>','')
    return send_from_directory(BASE,"index.html")

@app.get("/static/<path:name>")
def static_files(name):
    if name not in {"app.css","app.js","workspace.js"}: return jsonify(error="not found"),404
    return send_from_directory(BASE,name)

@app.get("/uploads/<path:name>")
def uploads(name): return send_from_directory(UPLOAD,name)

@app.get("/api/dashboard")
def dashboard():
    c=conn()
    accounts=c.execute("select count(*) n from accounts").fetchone()["n"]
    devices=c.execute("select count(*) n from devices where status='ONLINE' and datetime(last_seen)>=datetime('now','-2 minutes')").fetchone()["n"]
    clips=c.execute("select count(*) n from clips").fetchone()["n"]
    pubs=c.execute("select count(*) n from publications where status in ('PUBLISHED','UI_CONFIRMED')").fetchone()["n"]
    m=c.execute("select coalesce(sum(views),0) views,coalesce(sum(likes),0) likes,coalesce(sum(comments),0) comments,coalesce(sum(followers),0) followers from metrics").fetchone()
    t=c.execute("select coalesce(sum(done),0) done,coalesce(sum(target),0) target from tasks").fetchone()
    return jsonify(accounts=accounts,devices=devices,clips=clips,publications=pubs,**dict(m),
        task_done=t["done"],task_target=t["target"],task_percent=round(t["done"]*100/t["target"]) if t["target"] else 0)

@app.get("/api/me")
def me():
    return jsonify(g.telegram_user)

@app.route("/api/devices",methods=["GET","POST"])
def devices():
    c=conn()
    if request.method=="POST":
        x=request.json or {}
        did=str(uuid.uuid4()); token=secrets.token_urlsafe(32)
        c.execute("insert into devices values(?,?,?,?,?,?,?,?,?)",
            (did,x.get("name","Phone"),x.get("model","Android"),x.get("connection","USB / ADB"),
             "PENDING",0,token_hash(token),None,now()))
        audit(c,"create","device",did,{"name":x.get("name","Phone")})
        c.commit()
        return jsonify(id=did,device_token=token,warning="Токен показывается один раз. Сохраните его в Device Agent.")
    rows=c.execute("""select d.id,d.name,d.model,d.connection,d.status,d.battery,d.last_seen,d.created_at,count(a.id) accounts from devices d left join accounts a on a.device_id=d.id group by d.id order by d.created_at desc""").fetchall()
    result=[]
    for row in rows:
        d=dict(row)
        if d["status"]=="ONLINE" and (not d["last_seen"] or (datetime.now(timezone.utc)-datetime.fromisoformat(d["last_seen"])).total_seconds()>120):d["status"]="OFFLINE"
        result.append(d)
    return jsonify(result)

@app.post("/api/devices/<did>/heartbeat")
def heartbeat(did):
    x=request.json or {}; c=conn()
    row=device_auth(c,did)
    if not row:return jsonify(error="unauthorized device"),401
    c.execute("update devices set status='ONLINE',battery=?,last_seen=? where id=?",(int(x.get("battery",0)),now(),did))
    c.commit(); return jsonify(ok=True)

@app.get("/api/devices/<did>/jobs")
def device_jobs(did):
    c=conn()
    if not device_auth(c,did):return jsonify(error="unauthorized device"),401
    rows=c.execute("""select * from device_jobs where device_id=? and status='QUEUED'
      and (available_at is null or datetime(available_at)<=datetime(?)) order by created_at limit 10""",(did,now())).fetchall()
    result=[]
    for r in rows:
        item=dict(r); item["payload"]=json.loads(item["payload"]); result.append(item)
    return jsonify(result)

@app.post("/api/devices/<did>/jobs/<jid>/claim")
def claim_device_job(did,jid):
    c=conn()
    if not device_auth(c,did):return jsonify(error="unauthorized device"),401
    cur=c.execute("""update device_jobs set status='RUNNING',locked_at=?,attempts=attempts+1
      where id=? and device_id=? and status='QUEUED'""",(now(),jid,did)); c.commit()
    return jsonify(ok=True) if cur.rowcount else (jsonify(error="job unavailable"),409)

@app.post("/api/devices/<did>/jobs/<jid>/complete")
def complete_device_job(did,jid):
    x=request.json or {}; c=conn()
    if not device_auth(c,did):return jsonify(error="unauthorized device"),401
    status="DONE" if x.get("ok") else "FAILED"
    cur=c.execute("""update device_jobs set status=?,completed_at=?,result=?,error=?
      where id=? and device_id=? and status='RUNNING'""",(status,now(),json.dumps(x.get("result"),ensure_ascii=False),x.get("error"),jid,did))
    if not cur.rowcount:return jsonify(error="job unavailable"),409
    job=c.execute("select * from device_jobs where id=?",(jid,)).fetchone(); payload=json.loads(job["payload"])
    if job["kind"]=="PUBLISH":
        pid=payload.get("publication_id")
        c.execute("update publications set status=?,published_at=?,external_id=?,error=? where id=?",("PUBLISHED" if status=="DONE" else "FAILED",now() if status=="DONE" else None,(x.get("result") or {}).get("external_id"),x.get("error"),pid))
        if status=="DONE":c.execute("update tasks set done=min(target,done+1) where unit in ('клипов','публикаций')")
    audit(c,"complete","device_job",jid,{"status":status}); c.commit()
    return jsonify(ok=True,status=status)

@app.route("/api/accounts",methods=["GET","POST"])
def accounts():
    c=conn()
    if request.method=="POST":
        x=request.json or {}; aid=str(uuid.uuid4())
        c.execute("insert into accounts values(?,?,?,?,?,?,?,?)",
            (aid,x.get("platform","TikTok"),x.get("username",""),x.get("niche",""),
             x.get("audience",""),"ADDED",x.get("device_id"),now()))
        c.execute("insert or ignore into activity_plans(id,account_id,updated_at) values(?,?,?)",(str(uuid.uuid4()),aid,now()))
        audit(c,"create","account",aid,{"platform":x.get("platform","TikTok")})
        c.commit(); return jsonify(id=aid)
    rows=c.execute("""select a.*,d.name device from accounts a left join devices d on d.id=a.device_id
                      order by a.created_at desc""").fetchall()
    return jsonify([dict(r) for r in rows])

@app.get("/api/accounts/<aid>/activity")
def activity_get(aid):
    c=conn(); r=c.execute("select * from activity_plans where account_id=?",(aid,)).fetchone()
    return jsonify(dict(r) if r else {})

@app.post("/api/accounts/<aid>/activity")
def activity_set(aid):
    x=request.json or {}; c=conn()
    r=c.execute("select id from activity_plans where account_id=?",(aid,)).fetchone()
    vals=(int(bool(x.get("enabled"))),int(x.get("session_items",30)),int(x.get("min_watch",5)),
          int(x.get("max_watch",20)),int(x.get("like_percent",15)),int(x.get("follow_percent",3)),
          x.get("schedule","09:00,14:00,20:00"),now())
    if r:
        c.execute("""update activity_plans set enabled=?,session_items=?,min_watch=?,max_watch=?,
          like_percent=?,follow_percent=?,schedule=?,updated_at=? where account_id=?""",(*vals,aid))
    else:
        c.execute("""insert into activity_plans(id,account_id,enabled,session_items,min_watch,max_watch,
          like_percent,follow_percent,schedule,updated_at) values(?,?,?,?,?,?,?,?,?,?)""",
          (str(uuid.uuid4()),aid,*vals))
    c.commit(); return jsonify(ok=True)

@app.route("/api/tasks",methods=["GET","POST"])
def tasks():
    c=conn()
    if request.method=="POST":
        x=request.json or {}; tid=str(uuid.uuid4())
        target=max(1,int(x.get("target",1)))
        c.execute("""insert into tasks(id,title,target,done,unit,deadline,created_at,period,period_start,period_end)
          values(?,?,?,?,?,?,?,?,?,?)""",(tid,x.get("title","Новая задача"),target,0,x.get("unit","ед."),x.get("deadline"),now(),x.get("period","DAY"),x.get("period_start"),x.get("period_end")))
        audit(c,"create","task",tid,{"target":target,"period":x.get("period","DAY")})
        c.commit(); return jsonify(id=tid)
    return jsonify([dict(r) for r in c.execute("select * from tasks order by created_at desc")])

@app.patch("/api/tasks/<tid>")
def task_update(tid):
    x=request.json or {}; c=conn()
    c.execute("update tasks set done=min(target,?) where id=?",(max(0,int(x.get("done",0))),tid)); c.commit()
    return jsonify(ok=True)

@app.route("/api/clips",methods=["GET","POST"])
def clips():
    c=conn()
    if request.method=="POST":
        x=request.json or {}; cid=str(uuid.uuid4())
        c.execute("insert into clips values(?,?,?,?,?,?,?)",(cid,x.get("title","Vertical clip"),x.get("source_file"),
          float(x.get("duration",0)),int(x.get("score",0)),"READY",now())); c.commit()
        return jsonify(id=cid)
    return jsonify([dict(r) for r in c.execute("select * from clips order by created_at desc")])

@app.post("/api/media/upload")
def media_upload():
    f=request.files.get("file")
    if not f:return jsonify(error="file required"),400
    safe=os.path.basename(f.filename or "source.mp4")
    name=f"{uuid.uuid4().hex}_{safe}"
    f.save(os.path.join(UPLOAD,name))
    return jsonify(filename=name,url=f"/uploads/{name}")

@app.post("/api/media/render")
def media_render():
    x=request.json or {}; src=x.get("filename"); out=x.get("output","vertical.mp4")
    if not src:return jsonify(error="filename required"),400
    srcp=os.path.join(UPLOAD,os.path.basename(src))
    if not os.path.exists(srcp):return jsonify(error="source not found"),404
    ffmpeg=shutil.which("ffmpeg")
    if not ffmpeg:return jsonify(error="FFmpeg is not installed on the server"),501
    outname=f"{uuid.uuid4().hex}_{os.path.basename(out)}"; outp=os.path.join(UPLOAD,outname)
    # Safe baseline 9:16 center crop; face-aware crop is a later worker.
    cmd=[ffmpeg,"-y","-i",srcp,"-vf",
         "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920",
         "-c:v","libx264","-preset","medium","-crf","22","-c:a","aac","-movflags","+faststart",outp]
    p=subprocess.run(cmd,capture_output=True,text=True)
    if p.returncode:return jsonify(error=p.stderr[-2000:]),500
    return jsonify(filename=outname,url=f"/uploads/{outname}")

@app.route("/api/publications",methods=["GET","POST"])
def publications():
    c=conn()
    if request.method=="POST":
        x=request.json or {}; pid=str(uuid.uuid4())
        clip=c.execute("select * from clips where id=?",(x.get("clip_id"),)).fetchone()
        acc=c.execute("select * from accounts where id=?",(x.get("account_id"),)).fetchone()
        if not clip or not acc:return jsonify(error="clip/account not found"),404
        c.execute("insert into publications values(?,?,?,?,?,?,?,?,?,?)",
          (pid,clip["id"],acc["id"],acc["platform"],"QUEUED",x.get("scheduled_at"),None,None,None,now()))
        if acc["device_id"]:
            jid=str(uuid.uuid4()); payload={"publication_id":pid,"clip_id":clip["id"],"account_id":acc["id"],"platform":acc["platform"],"source_file":clip["source_file"]}
            c.execute("insert into device_jobs values(?,?,?,?,?,?,?,?,?,?,?,?)",(jid,acc["device_id"],"PUBLISH",json.dumps(payload,ensure_ascii=False),"QUEUED",0,x.get("scheduled_at") or now(),None,None,None,None,now()))
        audit(c,"create","publication",pid,{"platform":acc["platform"]})
        c.commit();return jsonify(id=pid)
    rows=c.execute("""select p.*,c.title clip,a.username account from publications p
      left join clips c on c.id=p.clip_id left join accounts a on a.id=p.account_id
      order by p.created_at desc""").fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/publications/<pid>/status")
def publication_status(pid):
    x=request.json or {}; c=conn(); status=x.get("status","PUBLISHED")
    pub=c.execute("select * from publications where id=?",(pid,)).fetchone()
    if not pub:return jsonify(error="not found"),404
    c.execute("update publications set status=?,published_at=?,external_id=?,error=? where id=?",
      (status,now() if status=="PUBLISHED" else None,x.get("external_id"),x.get("error"),pid))
    if status=="PUBLISHED":
        # Automatic KPI progress for tasks measured in publications.
        c.execute("update tasks set done=min(target,done+1) where unit in ('клипов','публикаций')")
    c.commit();return jsonify(ok=True)

@app.get("/api/analytics")
def analytics():
    c=conn()
    total=c.execute("select coalesce(sum(views),0) views,coalesce(sum(likes),0) likes,coalesce(sum(comments),0) comments,coalesce(sum(followers),0) followers from metrics").fetchone()
    by=c.execute("""select platform,count(distinct clip_id) clips,coalesce(sum(views),0) views,coalesce(sum(likes),0) likes
                    from metrics group by platform order by views desc""").fetchall()
    acc=c.execute("""select a.username,a.platform,coalesce(sum(m.views),0) views,coalesce(sum(m.followers),0) followers
                     from accounts a left join metrics m on m.account_id=a.id group by a.id order by views desc""").fetchall()
    return jsonify(**dict(total),platforms=[dict(r) for r in by],accounts=[dict(r) for r in acc])

@app.post("/api/analytics/ingest")
def ingest():
    x=request.json or {}; c=conn(); mid=str(uuid.uuid4())
    c.execute("insert into metrics values(?,?,?,?,?,?,?,?,?,?,?)",(mid,x.get("account_id"),x.get("clip_id"),x.get("platform"),
      int(x.get("views",0)),int(x.get("likes",0)),int(x.get("comments",0)),int(x.get("shares",0)),
      int(x.get("followers",0)),float(x.get("retention",0)),now()))
    c.commit();return jsonify(ok=True)

@app.get("/api/health")
def health():
    c=conn(); c.execute("select 1").fetchone()
    return jsonify(ok=True,database=True,ffmpeg=bool(shutil.which("ffmpeg")),time=now(),faxclip_version=14,phone_route="TIKTOK_REDMAAGI",verification_recovery=1,device_setup=1,device_disconnect=1,device_pairing_code=1,storage="sqlite_with_telegram_backup",queue_cleanup=1,enrollment_backup=1)

from adb_backend import register_adb
register_adb(app,conn,now,UPLOAD)
from maintenance import register_maintenance
register_maintenance(app,conn,now,UPLOAD)
from workspace import register_workspace
register_workspace(app,conn,now,audit)
from screen import register_screen
register_screen(app,conn,now)
from notify import register_notify
register_notify(app,conn,now)

if __name__=="__main__":
    init_db()
    app.run(host="0.0.0.0",port=int(os.getenv("PORT","8000")),debug=False)
