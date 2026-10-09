"""Notifications (alerts), Telegram delivery and live activity feed."""
import json,os,re,threading,time,uuid
from flask import request,jsonify,g
import requests

LEVELS=("info","success","warning","error")
CODES={
 "HELPER_PUBLICATION_PREVIOUSLY_ATTEMPTED":"Это видео уже публиковалось на аккаунте — повтор заблокирован. Загрузите новое видео.",
 "DUPLICATE_VIDEO_ALREADY_POSTED":"Это видео уже есть в профиле — повтор заблокирован.",
 "ADAPTER_WAIT":"На телефоне выключена служба FaxClip.",
 "TIKTOK_VERSION_NOT_CALIBRATED":"Версия TikTok на телефоне не поддерживается (нужна 44.6.4).",
}
SCHEMA="CREATE TABLE IF NOT EXISTS ws_alerts(id TEXT PRIMARY KEY,level TEXT NOT NULL,event_type TEXT NOT NULL,title TEXT NOT NULL,message TEXT,page TEXT,link TEXT,is_read INTEGER DEFAULT 0,created_at TEXT NOT NULL);"
ICON={"info":"ℹ️","success":"✅","warning":"⚠️","error":"⛔"}

def _tg_target():
    t=os.getenv('TELEGRAM_BOT_TOKEN','');raw=os.getenv('TELEGRAM_ALLOWED_USER_IDS','').replace(' ','').split(',')
    if os.getenv('FAXCLIP_LOCAL_TOKEN') or not re.fullmatch(r'\d{5,15}:[A-Za-z0-9_-]{30,}',t) or not raw or not re.fullmatch(r'\d{3,20}',raw[0]):return None
    return t,raw[0]

def settings(c):
    r=c.execute("select value from ws_settings where key='notify'").fetchone()
    v=json.loads(r[0]) if r else {}
    return {"telegram":v.get("telegram",True),"levels":v.get("levels",["success","warning","error"])}

def send_telegram(text):
    tg=_tg_target()
    if not tg:return
    def go():
        try:requests.post(f'https://api.telegram.org/bot{tg[0]}/sendMessage',data={'chat_id':tg[1],'text':text,'disable_web_page_preview':'true'},timeout=20)
        except requests.RequestException:pass
    threading.Thread(target=go,daemon=True).start()

def emit(c,level,event_type,title,message="",page=None,link=None):
    level=level if level in LEVELS else "info"
    c.execute("insert into ws_alerts values(?,?,?,?,?,?,?,0,?)",(str(uuid.uuid4()),level,event_type,title[:200],(message or "")[:1000],page,link,_now()))
    c.execute("delete from ws_alerts where id in (select id from ws_alerts order by created_at desc limit -1 offset 500)")
    s=settings(c)
    if s["telegram"] and level in s["levels"]:
        send_telegram(f"{ICON[level]} FaxClip: {title}"+(f"\n{message}" if message else "")+(f"\n{link}" if link else ""))

_now=lambda:time.strftime('%Y-%m-%dT%H:%M:%S+00:00',time.gmtime())

AUDIT={("create","persona"):"Создана персона",("update","persona"):"Изменена персона",("delete","persona"):"Удалена персона",
 ("create","proxy"):"Добавлен прокси",("update","proxy"):"Изменён прокси",("delete","proxy"):"Удалён прокси",
 ("create","account"):"Добавлен аккаунт",("update","account_profile"):"Изменён профиль аккаунта",("create","device"):"Добавлено устройство",
 ("update","device_profile"):"Изменены настройки устройства",("create","task"):"Создана задача"}

def _fresh(ts,limit=120):
    from datetime import datetime,timezone
    try:return (datetime.now(timezone.utc)-datetime.fromisoformat(ts)).total_seconds()<limit
    except (TypeError,ValueError):return False

def register_notify(app,conn,now):
    global _now
    _now=now
    def pub_info(c,jid):
        r=c.execute("select j.publication_id,a.username,a.platform from ui_jobs j left join accounts a on a.id=j.account_id where j.id=?",(jid,)).fetchone()
        return r
    @app.before_request
    def notify_before():
        p=request.path
        if p in ('/api/bridge/heartbeat','/api/bridge/disconnect'):
            did=request.headers.get('X-Device-ID','')
            with conn() as c:
                r=c.execute("select status,last_seen,name from devices where id=?",(did,)).fetchone()
            g.notify_prev=dict(r) if r else None
    @app.after_request
    def notify_after(resp):
        try:
            p=request.path
            if resp.status_code!=200 or not p.startswith('/api/'):return resp
            m=re.fullmatch(r'/api/bridge/jobs/([0-9a-f-]{36})/(complete|fail)',p)
            if m and request.method=='POST':
                with conn() as c:
                    info=pub_info(c,m.group(1))
                    who=f"{info['platform']} {info['username']}" if info else "аккаунт"
                    if m.group(2)=='complete':
                        j=c.execute("select result from ui_jobs where id=?",(m.group(1),)).fetchone()
                        url=(json.loads(j['result'] or '{}') or {}).get('post_url') if j else None
                        emit(c,"success","publication_published",f"Видео опубликовано · {who}","",'publishing',url)
                    else:
                        code=str((request.get_json(silent=True) or {}).get('code','UI_REVIEW'))
                        emit(c,"warning","publication_review",f"Публикация остановлена · {who}",CODES.get(code,"Нужна проверка: "+code),'publishing')
            elif p in ('/api/bridge/heartbeat','/api/bridge/disconnect') and getattr(g,'notify_prev',None):
                prev=g.notify_prev;was_online=prev['status']=='ONLINE' and _fresh(prev['last_seen'])
                with conn() as c:
                    if p.endswith('heartbeat') and not was_online:emit(c,"info","device_online",f"Телефон подключён · {prev['name']}","",'devices')
                    if p.endswith('disconnect') and was_online:emit(c,"warning","device_offline",f"Телефон отключён · {prev['name']}","Проверьте USB-кабель и Mac.",'devices')
            elif p=='/api/publications' and request.method=='POST':
                with conn() as c:emit(c,"info","publication_queued","Видео поставлено в очередь","",'publishing')
            elif re.fullmatch(r'/api/proxies/[0-9a-f-]{36}/check',p):
                body=resp.get_json(silent=True) or {}
                if not body.get('ok'):
                    with conn() as c:emit(c,"error","proxy_failed","Прокси не работает",body.get('message',''),'proxies')
        except Exception:pass
        return resp

    @app.get('/api/alerts')
    def alerts():
        c=conn();q="select * from ws_alerts where 1=1";args=[]
        if request.args.get('level') in LEVELS:q+=" and level=?";args.append(request.args['level'])
        if request.args.get('unread')=='1':q+=" and is_read=0"
        q+=" order by created_at desc limit ? offset ?";args+= [min(100,int(request.args.get('limit',30))),max(0,int(request.args.get('offset',0)))]
        rows=[dict(r) for r in c.execute(q,args)]
        unread=c.execute("select count(*) from ws_alerts where is_read=0").fetchone()[0]
        total=c.execute("select count(*) from ws_alerts").fetchone()[0]
        return jsonify(items=rows,unread=unread,total=total,settings=settings(c),telegram_available=bool(_tg_target()))
    @app.post('/api/alerts/<aid>/read')
    def alert_read(aid):
        c=conn();c.execute("update ws_alerts set is_read=1 where id=?",(aid,));c.commit();return jsonify(ok=True)
    @app.post('/api/alerts/read-all')
    def alerts_read_all():
        c=conn();c.execute("update ws_alerts set is_read=1");c.commit();return jsonify(ok=True)
    @app.delete('/api/alerts')
    def alerts_clear():
        c=conn();c.execute("delete from ws_alerts");c.commit();return jsonify(ok=True)
    @app.put('/api/alerts/settings')
    def alerts_settings():
        x=request.get_json(silent=True) or {};c=conn()
        v={"telegram":bool(x.get("telegram",True)),"levels":[l for l in (x.get("levels") or []) if l in LEVELS]}
        c.execute("insert or replace into ws_settings(key,value) values('notify',?)",(json.dumps(v),));c.commit()
        return jsonify(ok=True,**v)
    @app.post('/api/alerts/test')
    def alerts_test():
        if not _tg_target():return jsonify(error='Бот Telegram не настроен на сервере'),400
        send_telegram("🔔 FaxClip: тестовое уведомление. Всё работает.");return jsonify(ok=True)

    @app.get('/api/activity')
    def activity():
        c=conn();limit=min(60,int(request.args.get('limit',25)))
        items=[{"at":r["created_at"],"kind":"alert","level":r["level"],"title":r["title"],"message":r["message"],"page":r["page"],"link":r["link"]}
               for r in c.execute("select * from ws_alerts order by created_at desc limit ?",(limit,))]
        for r in c.execute("select * from audit_log order by created_at desc limit ?",(limit,)):
            t=AUDIT.get((r["action"],r["entity_type"]))
            if not t:continue
            try:pl=json.loads(r["payload"] or "{}")
            except ValueError:pl={}
            extra=pl.get("name") or pl.get("ip") or pl.get("platform") or ""
            items.append({"at":r["created_at"],"kind":"audit","level":"info","title":t+(f" · {extra}" if extra else ""),"message":"","page":None,"link":None})
        items.sort(key=lambda x:x["at"],reverse=True)
        return jsonify(items[:limit])
