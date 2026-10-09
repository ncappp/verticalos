"""Analytics (QUICON module 11, TikTok via public pages) + link generator with own short links (module 13).
Metrics are read from public TikTok pages every 12 h (no official API needed); manual entry is the fallback."""
import json,re,threading,time,uuid,csv,io,secrets
from datetime import datetime,timedelta,timezone
from urllib.parse import urlparse,urlencode,parse_qsl,urlunparse
from flask import request,jsonify,redirect,Response

SCHEMA="""
CREATE TABLE IF NOT EXISTS ws_video_metrics(id TEXT PRIMARY KEY,publication_id TEXT,account_id TEXT,url TEXT,views INTEGER,likes INTEGER,
  comments INTEGER,shares INTEGER,saves INTEGER,source TEXT,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ws_video_metrics_pub ON ws_video_metrics(publication_id,created_at);
CREATE TABLE IF NOT EXISTS ws_account_metrics(id TEXT PRIMARY KEY,account_id TEXT,followers INTEGER,following INTEGER,hearts INTEGER,videos INTEGER,
  source TEXT,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ws_account_metrics_acc ON ws_account_metrics(account_id,created_at);
CREATE TABLE IF NOT EXISTS ws_links(id TEXT PRIMARY KEY,code TEXT UNIQUE NOT NULL,name TEXT,target_url TEXT NOT NULL,full_url TEXT NOT NULL,
  network TEXT,placement TEXT,offer TEXT,page TEXT,keyword TEXT,metrika_goal TEXT,account_id TEXT,clicks INTEGER DEFAULT 0,last_click_at TEXT,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ws_link_clicks(day TEXT NOT NULL,link_id TEXT NOT NULL,n INTEGER DEFAULT 0,PRIMARY KEY(day,link_id));
"""
UA='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
SYNC_EVERY=12*3600
PLACEMENTS={'shapka':'Шапка профиля','direct':'Директ','post':'Описание поста','comment':'Комментарий','other':'Другое'}
VIDEO_URL=re.compile(r'https://www\.tiktok\.com/@([A-Za-z0-9._]{1,40})/video/([0-9]{10,25})')

def _int(v):
    try:return int(float(v))
    except (TypeError,ValueError):return None
def _page_json(url,timeout=20):
    import requests
    r=requests.get(url,headers={'User-Agent':UA,'Accept-Language':'en-US,en;q=0.9'},timeout=timeout)
    if r.status_code!=200:raise ValueError('HTTP %s'%r.status_code)
    m=re.search(r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',r.text,re.S)
    if not m:raise ValueError('TikTok не отдал данные страницы')
    return json.loads(m.group(1)).get('__DEFAULT_SCOPE__',{})
def fetch_video(url):
    d=_page_json(url).get('webapp.video-detail',{});st=((d.get('itemInfo') or {}).get('itemStruct') or {}).get('stats')
    if not st:raise ValueError('Видео недоступно (код %s)'%d.get('statusCode'))
    return dict(views=_int(st.get('playCount')),likes=_int(st.get('diggCount')),comments=_int(st.get('commentCount')),shares=_int(st.get('shareCount')),saves=_int(st.get('collectCount')))
def fetch_profile(username):
    u=username.lstrip('@')
    if not re.fullmatch(r'[A-Za-z0-9._]{1,40}',u):raise ValueError('Неверный username')
    d=_page_json('https://www.tiktok.com/@'+u).get('webapp.user-detail',{});st=(d.get('userInfo') or {}).get('stats')
    if not st:raise ValueError('Профиль недоступен (код %s)'%d.get('statusCode'))
    return dict(followers=_int(st.get('followerCount')),following=_int(st.get('followingCount')),hearts=_int(st.get('heartCount') or st.get('heart')),videos=_int(st.get('videoCount')))

def build_url(target,network,placement,offer,page,keyword):
    p=urlparse(target);q=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.startswith('utm_')]
    for k,v in (('utm_source',network),('utm_medium',placement),('utm_campaign',offer),('utm_content',page),('utm_term',keyword)):
        if v:q.append((k,v))
    return urlunparse(p._replace(query=urlencode(q)))

def register_analytics(app,conn,now,audit):
    def err(m,code=400):return jsonify(error=m),code
    def body():return request.get_json(silent=True) or {}
    lock=threading.Lock();state={'running':False,'last_error':None,'progress':''}

    def published(c,limit=80):
        return c.execute("""select p.id,p.account_id,p.external_id url,p.published_at,a.username,coalesce(cc.caption,c2.title,'') caption,c2.title
          from publications p left join accounts a on a.id=p.account_id left join clips c2 on c2.id=p.clip_id left join clip_captions cc on cc.clip_id=p.clip_id
          where p.status in ('UI_CONFIRMED','PUBLISHED') and p.external_id like 'https://www.tiktok.com/@%/video/%' order by p.published_at desc limit ?""",(limit,)).fetchall()

    def sync(force=False):
        with lock:
            if state['running']:return False
            state['running']=True
        try:
            with conn() as c:
                last=c.execute("select value from ws_settings where key='analytics_sync'").fetchone()
                if not force and last and time.time()-float(last[0])<SYNC_EVERY:return False
                c.execute("insert into ws_settings values('analytics_sync',?) on conflict(key) do update set value=excluded.value",(str(time.time()),))
                vids=[dict(r) for r in published(c)]
                accs=[dict(r) for r in c.execute("select id,username from accounts where lower(platform)='tiktok' and username is not null and username!=''")]
            errors=[];ok=0
            for i,a in enumerate(accs):
                state['progress']=f'Профили {i+1}/{len(accs)}'
                try:
                    m=fetch_profile(a['username'])
                    with conn() as c:c.execute('insert into ws_account_metrics values(?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),a['id'],m['followers'],m['following'],m['hearts'],m['videos'],'tiktok_public',now()));ok+=1
                except Exception as e:errors.append(f"{a['username']}: {str(e)[:80]}")
                time.sleep(1.2)
            for i,v in enumerate(vids):
                state['progress']=f'Видео {i+1}/{len(vids)}'
                try:
                    m=fetch_video(v['url'])
                    with conn() as c:c.execute('insert into ws_video_metrics values(?,?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),v['id'],v['account_id'],v['url'],m['views'],m['likes'],m['comments'],m['shares'],m['saves'],'tiktok_public',now()));ok+=1
                except Exception as e:errors.append(f"видео: {str(e)[:80]}")
                time.sleep(1.2)
            state['last_error']=('; '.join(errors[:5])+(f' и ещё {len(errors)-5}' if len(errors)>5 else '')) if errors else None
            with conn() as c:
                c.execute("delete from ws_video_metrics where created_at<?",((datetime.now(timezone.utc)-timedelta(days=400)).isoformat(),))
                if errors and not ok:
                    try:
                        from notify import emit;emit(c,'warning','analytics_failed','Статистика TikTok не обновилась',state['last_error'],'analytics')
                    except Exception:pass
            return True
        finally:
            state['running']=False;state['progress']=''

    def maybe_sync():
        with conn() as c:last=c.execute("select value from ws_settings where key='analytics_sync'").fetchone()
        if (not last or time.time()-float(last[0])>=SYNC_EVERY) and not state['running']:threading.Thread(target=sync,daemon=True).start()

    @app.post('/api/analytics/sync')
    def analytics_sync():
        if state['running']:return jsonify(ok=True,running=True)
        threading.Thread(target=sync,kwargs={'force':True},daemon=True).start();return jsonify(ok=True,running=True)

    def latest_video(c,before=None):
        q="select m.* from ws_video_metrics m join (select publication_id,max(created_at) mx from ws_video_metrics {w} group by publication_id) l on l.publication_id=m.publication_id and l.mx=m.created_at"
        return c.execute(q.format(w='where created_at<=?' if before else ''),((before,) if before else ())).fetchall()
    def latest_account(c,before=None):
        q="select m.* from ws_account_metrics m join (select account_id,max(created_at) mx from ws_account_metrics {w} group by account_id) l on l.account_id=m.account_id and l.mx=m.created_at"
        return c.execute(q.format(w='where created_at<=?' if before else ''),((before,) if before else ())).fetchall()

    @app.get('/api/analytics/v2')
    def analytics_v2():
        maybe_sync()
        days=max(7,min(90,_int(request.args.get('days')) or 30));acc=request.args.get('account_id') or None
        with conn() as c:
            vids={r['publication_id']:dict(r) for r in latest_video(c) if not acc or r['account_id']==acc}
            pubs={r['id']:dict(r) for r in published(c,500)}
            accm={r['account_id']:dict(r) for r in latest_account(c) if not acc or r['account_id']==acc}
            week=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat()
            accw={r['account_id']:dict(r) for r in latest_account(c,week)}
            names={r['id']:dict(r) for r in c.execute('select id,username,platform from accounts')}
            series=[]
            for i in range(days,-1,-1):
                d=(datetime.now(timezone.utc)-timedelta(days=i)).replace(hour=23,minute=59,second=59).isoformat()
                vv=[r for r in latest_video(c,d) if not acc or r['account_id']==acc];aa=[r for r in latest_account(c,d) if not acc or r['account_id']==acc]
                posted=c.execute("select count(*) from publications where status in ('UI_CONFIRMED','PUBLISHED') and substr(published_at,1,10)=?"+(" and account_id=?" if acc else ""),((d[:10],acc) if acc else (d[:10],))).fetchone()[0]
                series.append(dict(day=d[:10],views=sum(r['views'] or 0 for r in vv),likes=sum(r['likes'] or 0 for r in vv),followers=sum(r['followers'] or 0 for r in aa),posted=posted))
            last=c.execute("select value from ws_settings where key='analytics_sync'").fetchone()
        tot=lambda k:sum(v[k] or 0 for v in vids.values())
        top=sorted(vids.values(),key=lambda v:v['views'] or 0,reverse=True)[:20]
        top=[dict(**{k:v[k] for k in ('publication_id','url','views','likes','comments','shares','saves','created_at','source')},username=(names.get(v['account_id']) or {}).get('username'),
          caption=(pubs.get(v['publication_id']) or {}).get('caption'),published_at=(pubs.get(v['publication_id']) or {}).get('published_at')) for v in top]
        no_metrics=[dict(publication_id=p['id'],url=p['url'],username=p['username'],caption=p['caption'],published_at=p['published_at']) for p in pubs.values() if p['id'] not in vids and (not acc or p['account_id']==acc)][:30]
        accounts=[]
        for aid,a in names.items():
            if acc and aid!=acc:continue
            m=accm.get(aid);w=accw.get(aid)
            av=[v for v in vids.values() if v['account_id']==aid]
            accounts.append(dict(account_id=aid,username=a['username'],platform=a['platform'],followers=m['followers'] if m else None,
              followers_7d=(m['followers']-w['followers']) if m and w and m['followers'] is not None and w['followers'] is not None else None,
              hearts=m['hearts'] if m else None,videos=m['videos'] if m else None,views=sum(v['views'] or 0 for v in av),posts=len(av),updated_at=m['created_at'] if m else None))
        accounts.sort(key=lambda x:x['followers'] or -1,reverse=True)
        return jsonify(totals=dict(views=tot('views'),likes=tot('likes'),comments=tot('comments'),shares=tot('shares'),saves=tot('saves'),
            followers=sum(a['followers'] or 0 for a in accm.values()),videos=len(vids),published=len([p for p in pubs.values() if not acc or p['account_id']==acc])),
          series=series,top=top,no_metrics=no_metrics,accounts=accounts,last_sync=float(last[0]) if last else None,
          syncing=state['running'],progress=state['progress'],last_error=state['last_error'])

    @app.get('/api/analytics/publications')
    def analytics_publications():
        with conn() as c:return jsonify({r['publication_id']:{k:r[k] for k in ('views','likes','comments','shares','saves','created_at')} for r in latest_video(c)})

    @app.get('/api/analytics/publications/<pid>/history')
    def analytics_history(pid):
        with conn() as c:return jsonify([dict(r) for r in c.execute('select views,likes,comments,shares,saves,source,created_at from ws_video_metrics where publication_id=? order by created_at',(pid,))])

    @app.post('/api/analytics/manual')
    def analytics_manual():
        x=body()
        with conn() as c:
            p=c.execute('select id,account_id,external_id from publications where id=?',(x.get('publication_id'),)).fetchone()
            if not p:return err('Публикация не найдена',404)
            vals=[max(0,min(10**12,_int(x.get(k)) or 0)) for k in ('views','likes','comments','shares','saves')]
            c.execute('insert into ws_video_metrics values(?,?,?,?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),p['id'],p['account_id'],p['external_id'],*vals,'manual',now()))
        return jsonify(ok=True)

    # ---------- link generator ----------
    def clean_target(u):
        u=str(u or '').strip()
        p=urlparse(u)
        if p.scheme not in ('http','https') or not p.netloc or len(u)>1500 or any(ch in u for ch in '\r\n\t <>"'):raise ValueError('Нужна ссылка вида https://сайт.ru/страница')
        return u
    def label(v,n=60):
        v=re.sub(r'\s+','_',str(v or '').strip())[:n]
        if v and not re.fullmatch(r'[\w.\-@]+',v,re.U):raise ValueError('Метки — буквы, цифры, «_», «-», «.», «@»')
        return v
    def code():
        while True:
            k=secrets.token_urlsafe(5).replace('-','').replace('_','')[:6]
            if len(k)==6:return k
    def link_out(r,base):
        d=dict(r);d['short_url']=base+'l/'+d['code'];d['placement_name']=PLACEMENTS.get(d['placement'],d['placement']);return d
    def make(c,x,account=None):
        target=clean_target(x.get('target_url'))
        network=label(x.get('network') or 'tiktok',30).lower();placement=x.get('placement') if x.get('placement') in PLACEMENTS else 'other'
        offer=label(x.get('offer'));page=label(x.get('page') or (account['username'].lstrip('@') if account else ''));kw=label(x.get('keyword'))
        full=build_url(target,network,placement,offer,page,kw);lid=str(uuid.uuid4());k=code()
        name=str(x.get('name') or '').strip()[:100] or ' · '.join(filter(None,[account['username'] if account else page,PLACEMENTS[placement],offer]))
        c.execute('insert into ws_links(id,code,name,target_url,full_url,network,placement,offer,page,keyword,metrika_goal,account_id,created_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?)',
          (lid,k,name,target,full,network,placement,offer,page,kw,str(x.get('metrika_goal') or '').strip()[:100],account['id'] if account else x.get('account_id'),now()))
        return lid
    @app.route('/api/links',methods=['GET','POST'])
    def links():
        base=request.host_url.replace('http://','https://') if 'onrender.com' in request.host_url else request.host_url
        with conn() as c:
            if request.method=='GET':
                rows=c.execute('select l.*,a.username from ws_links l left join accounts a on a.id=l.account_id order by l.created_at desc').fetchall()
                since=(datetime.now(timezone.utc)-timedelta(days=13)).date().isoformat()
                daily={}
                for r in c.execute('select day,sum(n) n from ws_link_clicks where day>=? group by day',(since,)):daily[r['day']]=r['n']
                return jsonify(items=[link_out(r,base) for r in rows],placements=PLACEMENTS,daily=daily)
            x=body()
            try:
                acc=c.execute('select id,username from accounts where id=?',(x.get('account_id'),)).fetchone() if x.get('account_id') else None
                lid=make(c,x,dict(acc) if acc else None)
            except ValueError as e:return err(str(e))
            audit(c,'create','link',lid,{});return jsonify(link_out(c.execute('select l.*,null username from ws_links l where id=?',(lid,)).fetchone(),base))
    @app.post('/api/links/bulk')
    def links_bulk():
        x=body();pl=[p for p in (x.get('placements') or ['shapka','direct']) if p in PLACEMENTS]
        if not pl:return err('Выберите места: шапка, директ…')
        with conn() as c:
            q='select id,username from accounts where username is not null and username!=""'
            accs=[dict(r) for r in c.execute(q)]
            if x.get('account_ids'):accs=[a for a in accs if a['id'] in set(x['account_ids'])]
            if not accs:return err('Нет аккаунтов')
            made=[]
            try:
                for a in accs:
                    for p in pl:made.append(make(c,{**x,'placement':p,'page':None,'name':None},a))
            except ValueError as e:c.rollback();return err(str(e))
            audit(c,'create','links_bulk','-',{'count':len(made)})
        return jsonify(ok=True,created=len(made))
    @app.route('/api/links/<lid>',methods=['DELETE'])
    def link_delete(lid):
        with conn() as c:
            c.execute('delete from ws_links where id=?',(lid,));c.execute('delete from ws_link_clicks where link_id=?',(lid,))
        return jsonify(ok=True)
    @app.get('/api/links/export.csv')
    def links_export():
        base=request.host_url.replace('http://','https://') if 'onrender.com' in request.host_url else request.host_url
        out=io.StringIO();w=csv.writer(out,delimiter=';')
        w.writerow(['Название','Аккаунт','Место','Короткая ссылка','Полная ссылка','Оффер','Ключевое слово','Цель Метрики','Переходы','Создана'])
        with conn() as c:
            for r in c.execute('select l.*,a.username from ws_links l left join accounts a on a.id=l.account_id order by a.username,l.placement'):
                w.writerow([r['name'],r['username'] or r['page'],PLACEMENTS.get(r['placement'],r['placement']),base+'l/'+r['code'],r['full_url'],r['offer'],r['keyword'],r['metrika_goal'],r['clicks'],r['created_at'][:10]])
        return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename="faxclip-links.csv"'})
    @app.get('/l/<code>')
    def short_redirect(code):
        if not re.fullmatch(r'[A-Za-z0-9]{4,12}',code):return 'Ссылка не найдена',404
        with conn() as c:
            r=c.execute('select id,full_url from ws_links where code=?',(code,)).fetchone()
            if not r:return 'Ссылка не найдена',404
            c.execute('update ws_links set clicks=clicks+1,last_click_at=? where id=?',(now(),r['id']))
            c.execute('insert into ws_link_clicks values(?,?,1) on conflict(day,link_id) do update set n=n+1',(datetime.now(timezone.utc).date().isoformat(),r['id']))
        resp=redirect(r['full_url'],302);resp.headers['Cache-Control']='no-store';resp.headers['Referrer-Policy']='no-referrer-when-downgrade';return resp
