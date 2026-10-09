"""Posts hub (QUICON-style): one post -> many accounts. Drafts, schedule, fan-out to the
existing safe publication pipeline (create_publication with idempotency + duplicate guards).
Side tables only; the original publications/ui_jobs flow is untouched."""
import json,time,uuid,os
from datetime import datetime
from flask import request,jsonify,send_from_directory

SCHEMA="""
CREATE TABLE IF NOT EXISTS ws_posts(id TEXT PRIMARY KEY,title TEXT,caption TEXT,clip_id TEXT,scheduled_at TEXT,
  status TEXT DEFAULT 'draft',created_at TEXT NOT NULL,updated_at TEXT);
CREATE TABLE IF NOT EXISTS ws_post_targets(post_id TEXT NOT NULL,account_id TEXT NOT NULL,publication_id TEXT,error TEXT,
  created_at TEXT NOT NULL,PRIMARY KEY(post_id,account_id));
"""
DONE=('UI_CONFIRMED','PUBLISHED')
BAD=('NEEDS_REVIEW','FAILED','TRANSFERRED_NEEDS_AUTOMATION')

def _status(post,targets,t_now):
    if post['status']=='draft' and not any(t['publication_id'] for t in targets):return 'draft'
    created=[t for t in targets if t.get('publication_id')]
    if not created:return 'error' if any(t.get('error') for t in targets) else 'draft'
    targets=created;st=[t.get('pub_status') or 'NONE' for t in created]
    if all(s in DONE for s in st):return 'published'
    if all(s=='CANCELLED' for s in st):return 'canceled'
    if any(s=='RUNNING' for s in st):return 'publishing'
    if any(s in BAD or s=='ERROR' for s in st):return 'partial' if any(s in DONE for s in st) else 'error'
    if any(s=='QUEUED' for s in st):
        q=[t for t in targets if t.get('pub_status')=='QUEUED']
        return 'scheduled' if all((t.get('available') or 0)>t_now+60 for t in q) else 'queued'
    return 'partial' if any(s in DONE for s in st) else 'error'

def register_posts(app,conn,now,audit,uploads):
    def err(m,code=400):return jsonify(error=m),code
    def body():return request.get_json(silent=True) or {}

    def targets_of(c,pid):
        rows=c.execute("""select t.*,a.username,a.platform,p.status pub_status,p.external_id,p.published_at,p.error pub_error,p.scheduled_at pub_scheduled,
          j.phase,j.available,j.status job_status,j.evidence from ws_post_targets t left join accounts a on a.id=t.account_id
          left join publications p on p.id=t.publication_id left join ui_jobs j on j.publication_id=t.publication_id
          where t.post_id=? order by t.created_at""",(pid,)).fetchall()
        out=[]
        for r in rows:
            d=dict(r);d['has_evidence']=bool(d.pop('evidence',None));out.append(d)
        return out

    def full(c,p):
        p=dict(p);t=targets_of(c,p['id']);p['targets']=t;p['state']=_status(p,t,time.time());p['failed_targets']=sum(1 for x in t if x.get('error') and not x.get('publication_id'))
        clip=c.execute('select title,source_file from clips where id=?',(p['clip_id'],)).fetchone() if p.get('clip_id') else None
        p['clip_title']=clip['title'] if clip else None;p['has_media']=bool(clip and clip['source_file'])
        p['virtual']=False;return p

    def orphan_posts(c):
        rows=c.execute("""select p.*,c.title clip_title,c.source_file,cc.caption,a.username,j.phase,j.available,j.status job_status,j.evidence,j.payload
          from publications p left join clips c on c.id=p.clip_id left join accounts a on a.id=p.account_id
          left join ui_jobs j on j.publication_id=p.id left join clip_captions cc on cc.clip_id=p.clip_id
          where p.id not in (select publication_id from ws_post_targets where publication_id is not null)""").fetchall()
        out=[]
        for r in rows:
            r=dict(r)
            try:pl=json.loads(r.get('payload') or '{}')
            except ValueError:pl={}
            t=dict(post_id='pub_'+r['id'],account_id=r['account_id'],publication_id=r['id'],error=None,created_at=r['created_at'],username=r['username'],
              platform=r['platform'],pub_status=r['status'],external_id=r['external_id'],published_at=r['published_at'],pub_error=r['error'],
              pub_scheduled=r['scheduled_at'],phase=r['phase'],available=r['available'],job_status=r['job_status'],has_evidence=bool(r['evidence']))
            p=dict(id='pub_'+r['id'],title=pl.get('title') or r['clip_title'],caption=pl.get('caption') or r.get('caption'),clip_id=r['clip_id'],
              scheduled_at=r['scheduled_at'],status='sent',created_at=r['created_at'],updated_at=None,targets=[t],clip_title=r['clip_title'],
              has_media=bool(r['source_file']),virtual=True)
            p['state']=_status(p,[t],time.time());p['failed_targets']=0;out.append(p)
        return out

    def load(c,pid):
        if pid.startswith('pub_'):
            return next((p for p in orphan_posts(c) if p['id']==pid),None)
        r=c.execute('select * from ws_posts where id=?',(pid,)).fetchone()
        return full(c,r) if r else None

    def parse_when(v):
        if not v:return None
        t=datetime.fromisoformat(str(v).replace('Z','+00:00'))
        if not t.tzinfo:raise ValueError('tz')
        return t

    def payload(x,draft):
        title=str(x.get('title') or '').strip()[:100];caption=str(x.get('caption') or '')
        if len(caption)>2200:raise ValueError('Описание длиннее 2200 символов')
        try:when=parse_when(x.get('scheduled_at'))
        except (ValueError,TypeError):raise ValueError('Неверная дата публикации (нужен часовой пояс)')
        ids=x.get('account_ids') or []
        if not isinstance(ids,list) or not all(isinstance(i,str) for i in ids):raise ValueError('Неверный список аккаунтов')
        ids=list(dict.fromkeys(ids))
        if not draft:
            if not x.get('clip_id'):raise ValueError('Выберите видео')
            if not caption.strip():raise ValueError('Добавьте описание')
            if not ids:raise ValueError('Выберите хотя бы один аккаунт')
        return dict(title=title,caption=caption,clip_id=x.get('clip_id') or None,scheduled_at=when.isoformat() if when else None),ids

    def fan_out(pid,ids,confirmed):
        """Create one safe publication per account via the guarded pipeline. Idempotent per (post,account)."""
        view=app.view_functions['publications'];results=[]
        with conn() as c:
            p=dict(c.execute('select * from ws_posts where id=?',(pid,)).fetchone())
            existing={r['account_id']:dict(r) for r in c.execute('select * from ws_post_targets where post_id=?',(pid,))}
        for aid in ids:
            ex=existing.get(aid)
            if ex and ex['publication_id']:results.append((aid,ex['publication_id'],None));continue
            key=('post-'+pid+'-'+aid).replace('_','-')[:100]
            data=dict(clip_id=p['clip_id'],account_id=aid,title=p['title'] or '',caption=p['caption'],scheduled_at=p['scheduled_at'],
              confirmed=confirmed is True,rights_confirmed=confirmed is True)
            with app.test_request_context('/api/publications',method='POST',json=data,headers={'Idempotency-Key':key}):
                rv=view()
            resp,code=(rv if isinstance(rv,tuple) else (rv,200));out=resp.get_json(silent=True) or {}
            results.append((aid,out.get('id') if code<300 else None,None if code<300 else (out.get('error') or 'Ошибка')))
        with conn() as c:
            for aid,pub,e in results:
                c.execute('insert into ws_post_targets(post_id,account_id,publication_id,error,created_at) values(?,?,?,?,?) on conflict(post_id,account_id) do update set publication_id=coalesce(excluded.publication_id,publication_id),error=excluded.error',
                  (pid,aid,pub,e,now()))
            c.execute("update ws_posts set status='sent',updated_at=? where id=?",(now(),pid))
            ok=sum(1 for r in results if r[1]);bad=len(results)-ok
            try:
                from notify import emit
                if ok:emit(c,'info','post_queued',f"Пост {'запланирован' if p['scheduled_at'] else 'поставлен в очередь'} · аккаунтов: {ok}",p['title'] or '','publishing')
                if bad:emit(c,'warning','post_target_failed',f"Пост не отправлен на {bad} акк.",'; '.join(e for _,_,e in results if e)[:900],'publishing')
            except Exception:pass
        return results

    def create_post(title,caption,clip_id,account_ids,scheduled_at=None):
        """Internal: create a post and fan it out (used by the video assembly module)."""
        pid=str(uuid.uuid4())
        with conn() as c:
            c.execute('insert into ws_posts values(?,?,?,?,?,?,?,?)',(pid,(title or '')[:100],caption,clip_id,scheduled_at,'draft',now(),now()))
            for aid in account_ids:c.execute('insert or ignore into ws_post_targets(post_id,account_id,created_at) values(?,?,?)',(pid,aid,now()))
        res=fan_out(pid,account_ids,True);return pid,res
    app.extensions['faxclip_create_post']=create_post

    @app.route('/api/posts',methods=['GET','POST'])
    def posts():
        if request.method=='POST':
            x=body();draft=x.get('action')!='publish'
            try:data,ids=payload(x,draft)
            except ValueError as e:return err(str(e))
            if not draft and (x.get('confirmed') is not True or x.get('rights_confirmed') is not True):return err('Подтвердите публикацию и права на видео')
            pid=str(uuid.uuid4())
            with conn() as c:
                if data['clip_id'] and not c.execute('select 1 from clips where id=?',(data['clip_id'],)).fetchone():return err('Видео не найдено',404)
                c.execute('insert into ws_posts values(?,?,?,?,?,?,?,?)',(pid,data['title'],data['caption'],data['clip_id'],data['scheduled_at'],'draft',now(),now()))
                for aid in ids:
                    if c.execute('select 1 from accounts where id=?',(aid,)).fetchone():
                        c.execute('insert or ignore into ws_post_targets(post_id,account_id,created_at) values(?,?,?)',(pid,aid,now()))
                audit(c,'create','post',pid,{'accounts':len(ids)})
            if not draft:fan_out(pid,ids,True)
            with conn() as c:return jsonify(load(c,pid))
        tab=request.args.get('tab','all');state=request.args.get('status');plat=request.args.get('platform');acc=request.args.get('account_id')
        with conn() as c:
            items=[full(c,r) for r in c.execute('select * from ws_posts order by created_at desc')]+orphan_posts(c)
        def keep(p):
            s=p['state']
            if tab=='published' and s not in ('published','partial'):return False
            if tab=='scheduled' and s not in ('scheduled','queued','publishing'):return False
            if tab=='drafts' and s!='draft':return False
            if state and s!=state:return False
            if plat and not any(t['platform']==plat for t in p['targets']):return False
            if acc and not any(t['account_id']==acc for t in p['targets']):return False
            return True
        items=[p for p in items if keep(p)]
        key=(lambda p:min([t['available'] or 0 for t in p['targets']] or [0])) if tab=='scheduled' else (lambda p:p['created_at'] or '')
        items.sort(key=key,reverse=tab!='scheduled')
        return jsonify(items=items)

    @app.get('/api/posts/counts')
    def posts_counts():
        with conn() as c:items=[full(c,r) for r in c.execute('select * from ws_posts')]+orphan_posts(c)
        n=lambda f:sum(1 for p in items if f(p['state']))
        return jsonify(all=len(items),published=n(lambda s:s in ('published','partial')),scheduled=n(lambda s:s in ('scheduled','queued','publishing')),
          drafts=n(lambda s:s=='draft'),attention=sum(1 for p in items if p['state'] in ('error','partial') or p.get('failed_targets')))

    @app.route('/api/posts/<pid>',methods=['GET','PATCH','DELETE'])
    def post_one(pid):
        with conn() as c:
            p=load(c,pid)
            if not p:return err('Пост не найден',404)
            if request.method=='GET':return jsonify(p)
            if request.method=='DELETE':
                if p['virtual']:return err('Это запись старой очереди — её можно только отменить')
                if p['state'] not in ('draft','canceled'):return err('Удалить можно черновик или отменённый пост. Сначала отмените публикацию.',409)
                c.execute('delete from ws_post_targets where post_id=?',(pid,));c.execute('delete from ws_posts where id=?',(pid,))
                audit(c,'delete','post',pid,{});return jsonify(ok=True)
            if p['state']!='draft' or p['virtual']:return err('Изменять можно только черновик',409)
            try:data,ids=payload({**{k:p.get(k) for k in ('title','caption','clip_id','scheduled_at')},'account_ids':[t['account_id'] for t in p['targets']],**body()},True)
            except ValueError as e:return err(str(e))
            c.execute('update ws_posts set title=?,caption=?,clip_id=?,scheduled_at=?,updated_at=? where id=?',(data['title'],data['caption'],data['clip_id'],data['scheduled_at'],now(),pid))
            c.execute('delete from ws_post_targets where post_id=? and publication_id is null',(pid,))
            for aid in ids:
                if c.execute('select 1 from accounts where id=?',(aid,)).fetchone():
                    c.execute('insert or ignore into ws_post_targets(post_id,account_id,created_at) values(?,?,?)',(pid,aid,now()))
            audit(c,'update','post',pid,{});return jsonify(load(c,pid))

    @app.post('/api/posts/<pid>/publish')
    def post_publish(pid):
        x=body()
        if x.get('confirmed') is not True or x.get('rights_confirmed') is not True:return err('Подтвердите публикацию и права на видео')
        with conn() as c:
            p=load(c,pid)
            if not p or p['virtual']:return err('Пост не найден',404)
            if p['state']!='draft':return err('Пост уже отправлен',409)
            try:payload({k:p.get(k) for k in ('title','caption','clip_id','scheduled_at')}|{'account_ids':[t['account_id'] for t in p['targets']]},False)
            except ValueError as e:return err(str(e))
            if x.get('now'):c.execute('update ws_posts set scheduled_at=null where id=?',(pid,))
        fan_out(pid,[t['account_id'] for t in p['targets']],True)
        with conn() as c:return jsonify(load(c,pid))

    def pubs(p,pred):return [t for t in p['targets'] if t['publication_id'] and pred(t)]

    @app.post('/api/posts/<pid>/cancel')
    def post_cancel(pid):
        with conn() as c:
            p=load(c,pid)
            if not p:return err('Пост не найден',404)
            c.execute('BEGIN IMMEDIATE');n=0;busy=0
            for t in pubs(p,lambda t:True):
                j=c.execute('select * from ui_jobs where publication_id=?',(t['publication_id'],)).fetchone()
                if not j:continue
                if j['status']=='QUEUED' and j['phase']=='NEW':
                    c.execute("update ui_jobs set status='CANCELLED',lease_hash=null where id=?",(j['id'],))
                    c.execute("update publications set status='CANCELLED' where id=?",(t['publication_id'],));n+=1
                elif j['status'] in ('RUNNING','VERIFYING'):busy+=1
            if not p['virtual']:c.execute('update ws_posts set updated_at=? where id=?',(now(),pid))
            audit(c,'cancel','post',pid,{'cancelled':n})
        return jsonify(ok=True,cancelled=n,busy=busy)

    @app.post('/api/posts/<pid>/publish-now')
    def post_now(pid):
        with conn() as c:
            p=load(c,pid)
            if not p:return err('Пост не найден',404)
            n=0
            for t in pubs(p,lambda t:t['job_status']=='QUEUED'):
                n+=c.execute("update ui_jobs set available=? where publication_id=? and status='QUEUED'",(time.time(),t['publication_id'])).rowcount
                c.execute('update publications set scheduled_at=null where id=?',(t['publication_id'],))
            if not p['virtual']:c.execute('update ws_posts set scheduled_at=null,updated_at=? where id=?',(now(),pid))
        return jsonify(ok=True,moved=n)

    @app.post('/api/posts/<pid>/reschedule')
    def post_reschedule(pid):
        try:when=parse_when(body().get('scheduled_at'))
        except (ValueError,TypeError):return err('Неверная дата (нужен часовой пояс)')
        if not when:return err('Укажите дату')
        if when.timestamp()<time.time()-60:return err('Дата уже прошла')
        with conn() as c:
            p=load(c,pid)
            if not p:return err('Пост не найден',404)
            n=0
            for t in pubs(p,lambda t:t['job_status']=='QUEUED'):
                n+=c.execute("update ui_jobs set available=? where publication_id=? and status='QUEUED'",(when.timestamp(),t['publication_id'])).rowcount
                c.execute('update publications set scheduled_at=? where id=?',(when.isoformat(),t['publication_id']))
            if not p['virtual']:c.execute('update ws_posts set scheduled_at=?,updated_at=? where id=?',(when.isoformat(),now(),pid))
        return jsonify(ok=True,moved=n)

    @app.post('/api/posts/<pid>/retry')
    def post_retry(pid):
        """Safe retry only: the phone never started publishing (phase NEW). Same job id -> phone-side guard still applies."""
        with conn() as c:
            p=load(c,pid)
            if not p:return err('Пост не найден',404)
            c.execute('BEGIN IMMEDIATE');n=0;blocked=0
            for t in pubs(p,lambda t:True):
                j=c.execute('select * from ui_jobs where publication_id=?',(t['publication_id'],)).fetchone()
                if not j or j['status'] in ('DONE','QUEUED','RUNNING','VERIFYING'):continue
                pub=c.execute('select status,error from publications where id=?',(t['publication_id'],)).fetchone()
                dup=(pub['error'] or '') in ('HELPER_PUBLICATION_PREVIOUSLY_ATTEMPTED','DUPLICATE_VIDEO_ALREADY_POSTED')
                if j['phase']!='NEW' or dup:blocked+=1;continue
                c.execute("update ui_jobs set status='QUEUED',available=?,lease_hash=null,lease_until=null,error=null where id=?",(time.time(),j['id']))
                c.execute("update publications set status='QUEUED',error=null where id=?",(t['publication_id'],));n+=1
            audit(c,'retry','post',pid,{'retried':n})
        missing=[t['account_id'] for t in p['targets'] if not t['publication_id']] if not p['virtual'] else []
        if missing:
            res=fan_out(pid,missing,True);n+=sum(1 for r in res if r[1])
        if not n and blocked:return err('Повтор небезопасен: телефон уже начинал публикацию. Проверьте профиль в TikTok и при необходимости вставьте ссылку.',409)
        return jsonify(ok=True,retried=n,blocked=blocked)

    @app.post('/api/posts/<pid>/duplicate')
    def post_duplicate(pid):
        with conn() as c:
            p=load(c,pid)
            if not p:return err('Пост не найден',404)
            nid=str(uuid.uuid4())
            c.execute('insert into ws_posts values(?,?,?,?,?,?,?,?)',(nid,p['title'],p['caption'],p['clip_id'],None,'draft',now(),now()))
            for t in p['targets']:c.execute('insert or ignore into ws_post_targets(post_id,account_id,created_at) values(?,?,?)',(nid,t['account_id'],now()))
            audit(c,'create','post',nid,{'from':pid});return jsonify(load(c,nid))

    @app.get('/api/posts/<pid>/media')
    def post_media(pid):
        with conn() as c:
            p=load(c,pid)
            clip=c.execute('select source_file,title from clips where id=?',(p['clip_id'],)).fetchone() if p and p.get('clip_id') else None
        name=clip['source_file'] if clip else None
        if not name or name!=os.path.basename(name) or not os.path.isfile(os.path.join(uploads,name)):return err('Видео не найдено на сервере',404)
        return send_from_directory(uploads,name,as_attachment=True,download_name=(clip['title'] or 'video')[:80].replace('/','_')+('' if (clip['title'] or '').lower().endswith('.mp4') else '.mp4'))
