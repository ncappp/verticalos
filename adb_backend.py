"""Authenticated queue for PC-controlled Android devices. No platform API tokens."""
import hashlib,hmac,json,os,re,secrets,time,uuid,struct,threading,shlex
from flask import request,jsonify,g,send_from_directory
from datetime import datetime
from platform_adapters import ADAPTERS,account_capability

TTL=90

def register_adb(app,conn,now,uploads):
    def digest(value):return hashlib.sha256(value.encode()).hexdigest()
    with conn() as c:c.executescript('''
        CREATE TABLE IF NOT EXISTS ui_jobs(id TEXT PRIMARY KEY,device_id TEXT NOT NULL,publication_id TEXT UNIQUE NOT NULL,account_id TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,available REAL NOT NULL,lease_until REAL,lease_hash TEXT,phase TEXT,evidence TEXT,result TEXT,error TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ui_phone_pairs(code_hash TEXT PRIMARY KEY,device_id TEXT NOT NULL,expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS ui_account_media_guard(platform TEXT,username TEXT,sha256 TEXT,publication_id TEXT,PRIMARY KEY(platform,username,sha256));
        CREATE TABLE IF NOT EXISTS ui_media_guard(device_id TEXT,username TEXT,sha256 TEXT,publication_id TEXT,PRIMARY KEY(device_id,username,sha256));
        CREATE TABLE IF NOT EXISTS clip_captions(clip_id TEXT PRIMARY KEY,caption TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS device_capabilities(device_id TEXT PRIMARY KEY,mode TEXT,helper_version INTEGER,app_version TEXT);
        CREATE TABLE IF NOT EXISTS ui_request_hash(key TEXT PRIMARY KEY,body_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ui_idempotency(key TEXT PRIMARY KEY,publication_id TEXT NOT NULL);
    ''')
    with conn() as c:
        for old in c.execute('select publication_id,payload from ui_jobs order by created_at').fetchall():
            try:
                payload=json.loads(old['payload'])
                if all(isinstance(payload.get(k),str) and payload.get(k) for k in ('platform','username','sha256')):
                    c.execute('insert or ignore into ui_account_media_guard values(?,?,?,?)',(payload['platform'],payload['username'],payload['sha256'],old['publication_id']))
            except (ValueError,TypeError):continue
    @app.before_request
    def bridge_guard():
        if request.path=='/api/bridge/pair':return None
        if request.path.startswith('/api/bridge/'):
            did=request.headers.get('X-Device-ID','');bearer=request.headers.get('Authorization','')
            with conn() as c:row=c.execute('select * from devices where id=?',(did,)).fetchone()
            if not row or not bearer.startswith('Bearer ') or not hmac.compare_digest(row['token_hash'],digest(bearer[7:])):
                return jsonify(error='Invalid bridge credentials'),401
            g.bridge_device=dict(row)
        if request.path.startswith('/uploads/'):return jsonify(error='Media requires an authenticated job'),403
        if request.path.startswith('/api/devices/') and request.headers.get('Authorization','').startswith('Bearer '):
            return jsonify(error='Old protocol disabled; use /api/bridge/'),410
    @app.after_request
    def private_response(r):
        if request.path.startswith('/api/'):r.headers['Cache-Control']='no-store'
        r.headers['X-Content-Type-Options']='nosniff';return r
    original_pubs=app.view_functions['publications']
    def create_publication():
        if request.method=='GET':return original_pubs()
        x=request.get_json(silent=True) or {};key=request.headers.get('Idempotency-Key','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{16,100}',key):return jsonify(error='Idempotency-Key required'),400
        if x.get('confirmed') is not True:return jsonify(error='Publication confirmation required'),400
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('select publication_id from ui_idempotency where key=?',(key,)).fetchone()
            if old:
                stored=c.execute('select body_hash from ui_request_hash where key=?',(key,)).fetchone()
                fingerprint=hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
                if not stored or stored['body_hash']!=fingerprint:return jsonify(error='Этот ключ уже использован для другого содержимого запроса; повтор не выполнен'),409
                return jsonify(id=old['publication_id'],existing=True)
            a=c.execute('select * from accounts where id=?',(x.get('account_id'),)).fetchone()
            clip=c.execute('select * from clips where id=?',(x.get('clip_id'),)).fetchone()
            if not a or not clip:return jsonify(error='Account or clip not found'),404
            if not account_capability(dict(a))['automation_ready']:return jsonify(error='Для выбранного аккаунта нет активного адаптера публикации. Регистрация профиля не означает готовность автоматизации.'),409
            if x.get('rights_confirmed') is not True:return jsonify(error='Подтвердите права на видео и музыку'),400
            if not a['device_id'] or not a['username']:return jsonify(error='Assign a phone and specify exact account username'),400
            d=c.execute('select * from devices where id=?',(a['device_id'],)).fetchone()
            if not d or d['status']=='REVOKED':return jsonify(error='Device not available'),409
            name=clip['source_file'] or ''
            if name!=os.path.basename(name) or not name.lower().endswith('.mp4') or not os.path.isfile(os.path.join(uploads,name)):
                return jsonify(error='Upload a real MP4 file first'),400
            try:
                schedule=x.get('scheduled_at');t=datetime.fromisoformat(schedule.replace('Z','+00:00')) if schedule else None
                if t and not t.tzinfo:return jsonify(error='Schedule timezone is required'),400
                available=t.timestamp() if t else time.time()
            except (ValueError,TypeError):return jsonify(error='Invalid schedule'),400
            caption=str(x.get('caption',''))
            if not caption.strip() or len(caption)>2200 or any(ord(ch)<32 and ch not in '\n\t' for ch in caption):return jsonify(error='Описание должно быть непустым, до 2200 символов, без управляющих знаков'),400
            hasher=hashlib.sha256()
            with open(os.path.join(uploads,name),'rb') as media:
                for block in iter(lambda:media.read(1024*1024),b''):hasher.update(block)
            sha=hasher.hexdigest()
            prior_account=c.execute('select publication_id from ui_account_media_guard where platform=? and username=? and sha256=?',(a['platform'],a['username'],sha)).fetchone()
            if prior_account:return jsonify(error='Для этого файла уже есть попытка на выбранном аккаунте, в том числе с другого устройства. Автоповтор запрещён.',publication_id=prior_account['publication_id']),409
            previous=c.execute('select publication_id from ui_media_guard where device_id=? and username=? and sha256=?',(a['device_id'],a['username'],sha)).fetchone()
            if previous:return jsonify(error='Для этого файла уже есть попытка на аккаунте. Автоповтор запрещён; проверьте предыдущую запись',publication_id=previous['publication_id']),409
            pid=str(uuid.uuid4());jid=str(uuid.uuid4())
            payload=dict(platform=a['platform'],username=a['username'],caption=caption,title=str(x.get('title') or clip['title'])[:100],sha256=sha,bytes=os.path.getsize(os.path.join(uploads,name)),rights_confirmed=True,mode='ACCESSIBILITY_PUBLISH')
            c.execute('insert into publications values(?,?,?,?,?,?,?,?,?,?)',(pid,clip['id'],a['id'],a['platform'],'QUEUED',schedule,None,None,None,now()))
            c.execute('insert into ui_jobs(id,device_id,publication_id,account_id,status,payload,available,phase,created_at) values(?,?,?,?,?,?,?,?,?)',
                (jid,a['device_id'],pid,a['id'],'QUEUED',json.dumps(payload,ensure_ascii=False),available,'NEW',now()))
            c.execute('insert into ui_idempotency values(?,?)',(key,pid))
            c.execute('insert into ui_request_hash values(?,?)',(key,hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False).encode()).hexdigest()))
            c.execute('insert into ui_account_media_guard values(?,?,?,?)',(a['platform'],a['username'],sha,pid))
            c.execute('insert into ui_media_guard values(?,?,?,?)',(a['device_id'],a['username'],sha,pid))
        return jsonify(id=pid,job_id=jid)
    app.view_functions['publications']=create_publication
    app.view_functions['publication_status']=lambda pid:(jsonify(error='Use a calibrated bridge report; manual published-status overrides disabled'),410)
    @app.get('/api/integrations')
    def integrations():return jsonify(list(ADAPTERS.values()))

    original_accounts=app.view_functions['accounts']
    def accounts_with_capabilities():
        response=original_accounts()
        if request.method=='GET':
            return jsonify([{**a,**account_capability(a)} for a in response.get_json()])
        return response
    app.view_functions['accounts']=accounts_with_capabilities

    @app.patch('/api/accounts/<aid>/device')
    def assign_account_device(aid):
        body=request.get_json(silent=True) or {};did=body.get('device_id')
        if did is not None and (not isinstance(did,str) or not re.fullmatch(r'[a-f0-9-]{36}',did)):return jsonify(error='Некорректное устройство'),400
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            account=c.execute('select id from accounts where id=?',(aid,)).fetchone()
            if not account:return jsonify(error='Аккаунт не найден'),404
            if did is not None:
                device=c.execute("select id from devices where id=? and status!='REVOKED'",(did,)).fetchone()
                if not device:return jsonify(error='Устройство не найдено или отозвано'),404
            if c.execute("select id from ui_jobs where account_id=? and status in ('QUEUED','RUNNING','VERIFYING','NEEDS_REVIEW')",(aid,)).fetchone():
                return jsonify(error='У аккаунта есть незавершённые задания. Их нельзя незаметно перенести на другой телефон.'),409
            c.execute('update accounts set device_id=? where id=?',(did,aid))
        return jsonify(ok=True)

    def device_setup(did,serial=''):
        raw=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(15))
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            device=c.execute('select * from devices where id=?',(did,)).fetchone()
            if not device or device['status']=='REVOKED':return None,404
            if c.execute("select id from ui_jobs where device_id=? and status in ('RUNNING','VERIFYING')",(did,)).fetchone():return None,409
            c.execute('delete from ui_phone_pairs where device_id=? or expires<?',(did,time.time()))
            c.execute('insert into ui_phone_pairs values(?,?,?)',(digest(raw),did,time.time()+600))
        return {'format':'FAXCLIP_DEVICE_SETUP_V1','server':'https://verticalos-rxdl.onrender.com','device_id':did,'code':'-'.join(raw[i:i+5] for i in range(0,15,5)),'serial':serial,'expires_seconds':600},200

    @app.get('/device-pairing-helper.py')
    def device_pairing_helper():
        # Public software source only; never contains a user's pairing code or token.
        return send_from_directory(os.path.join(app.root_path,'bridge'),'pair_device_code.py',mimetype='text/plain')

    @app.get('/api/pairing-command')
    def pairing_command():
        with open(os.path.join(app.root_path,'bridge','pair_device_code.py'),'rb') as f:script=f.read()
        sha=hashlib.sha256(script).hexdigest()
        bootstrap="import requests,hashlib; r=requests.get('https://verticalos-rxdl.onrender.com/device-pairing-helper.py',timeout=30); r.raise_for_status(); s=r.content; hashlib.sha256(s).hexdigest()=="+repr(sha)+" or __import__('sys').exit('Helper checksum mismatch'); exec(compile(s,'pair_device_code.py','exec'))"
        command='"$HOME/Downloads/faxclip-telegram-bridge-v14/.venv/bin/python" -c '+shlex.quote(bootstrap)
        return jsonify(command=command,application_install_required=False)

    original_devices=app.view_functions['devices']
    def devices_with_setup():
        if request.method!='POST':
            response=original_devices()
            return jsonify([{**d,'enrolled':bool(d.get('last_seen'))} for d in response.get_json()])
        body=request.get_json(silent=True) or {};serial=body.get('usb_serial','')
        if not isinstance(serial,str) or (serial and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',serial)):return jsonify(error='Недопустимый USB serial'),400
        for key in ('name','model','connection'):
            value=body.get(key,'')
            if not isinstance(value,str) or len(value)>150:return jsonify(error='Некорректные данные устройства'),400
        response=original_devices();data=response.get_json()
        setup,status=device_setup(data['id'],serial)
        if status!=200:return jsonify(error='Устройство создано, но подключение не подготовлено'),status
        return jsonify({**data,'setup':setup})
    app.view_functions['devices']=devices_with_setup

    @app.post('/api/devices/<did>/pairing')
    def pair_selected_device(did):
        body=request.get_json(silent=True) or {};serial=body.get('usb_serial','')
        if not isinstance(serial,str) or (serial and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',serial)):return jsonify(error='Недопустимый USB serial'),400
        setup,status=device_setup(did,serial)
        if status==404:return jsonify(error='Устройство не найдено'),404
        if status==409:return jsonify(error='Устройство занято; переподключение запрещено'),409
        return jsonify(setup)

    @app.post('/api/phone-pairing')
    def create_phone_pairing():
        # Owner Telegram authentication has already run. Explicit owner action re-pairs one known account.
        raw=''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(15))
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            account=c.execute("select * from accounts where platform='TikTok' and username='@redmaagi'").fetchone()
            did=account['device_id'] if account and account['device_id'] else str(uuid.uuid4())
            if c.execute("select id from ui_jobs where device_id=? and status in ('RUNNING','VERIFYING')",(did,)).fetchone():return jsonify(error='Телефон выполняет задачу. Переподключение пока запрещено'),409
            device=c.execute('select * from devices where id=?',(did,)).fetchone()
            if not device:
                c.execute('insert into devices values(?,?,?,?,?,?,?,?,?)',(did,'Redmi для TikTok','23053RN02Y','USB / Mac bridge','PENDING',0,digest(secrets.token_urlsafe(48)),None,now()))
            if not account:
                c.execute('insert into accounts values(?,?,?,?,?,?,?,?)',(str(uuid.uuid4()),'TikTok','@redmaagi','','','ADDED',did,now()))
            elif not account['device_id']:c.execute('update accounts set device_id=? where id=?',(did,account['id']))
            c.execute('delete from ui_phone_pairs where device_id=? or expires<?',(did,time.time()))
            c.execute('insert into ui_phone_pairs values(?,?,?)',(digest(raw),did,time.time()+600))
        return jsonify(code='-'.join(raw[i:i+5] for i in range(0,15,5)),expires_seconds=600,device_id=did,account='@redmaagi')

    pair_limit_lock=threading.Lock()
    pair_requests=[]
    @app.post('/api/bridge/pair')
    def redeem_phone_pairing():
        # Bootstrap endpoint: a high-entropy owner-generated code, NOT a public device-registration API.
        if not request.content_length or request.content_length>2048:return jsonify(error='Pairing request size rejected'),400
        with pair_limit_lock:
            pair_requests[:]=[t for t in pair_requests if t>time.time()-60]
            if len(pair_requests)>=20:return jsonify(error='Pairing rate limit; wait a minute'),429
            pair_requests.append(time.time())
        x=request.get_json(silent=True) or {};code=x.get('code','')
        if not isinstance(code,str):return jsonify(error='Invalid pairing code'),401
        code=code.replace('-','').strip().upper()
        if not re.fullmatch(r'[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{15}',code):return jsonify(error='Invalid or expired pairing code'),401
        token=secrets.token_urlsafe(48)
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('select * from ui_phone_pairs where code_hash=? and expires>?',(digest(code),time.time())).fetchone()
            if not row:return jsonify(error='Invalid, used or expired pairing code'),401
            if c.execute("select id from ui_jobs where device_id=? and status in ('RUNNING','VERIFYING')",(row['device_id'],)).fetchone():return jsonify(error='Device is busy; pairing rejected'),409
            c.execute('delete from ui_phone_pairs where code_hash=?',(digest(code),))
            c.execute("update devices set token_hash=?,status='PENDING',last_seen=null where id=?",(digest(token),row['device_id']))
        with conn() as c:profiles=c.execute('select platform,username from accounts where device_id=?',(row['device_id'],)).fetchall()
        profiles=[dict(p) for p in profiles]
        return jsonify(device_id=row['device_id'],device_token=token,account=profiles[0]['username'] if len(profiles)==1 else None,accounts=profiles,mode='ACCESSIBILITY_PUBLISH')

    @app.post('/api/bridge/heartbeat')
    def bridge_heartbeat():
        x=request.get_json(silent=True) or {}
        try:battery=max(0,min(100,int(x.get('battery',0))))
        except (ValueError,TypeError):return jsonify(error='Invalid battery'),400
        with conn() as c:
            c.execute("update devices set status='ONLINE',battery=?,last_seen=? where id=?",(battery,now(),g.bridge_device['id']))
            if x.get('mode')=='ACCESSIBILITY_PUBLISH' and x.get('helper_version')==14 and x.get('app_version')=='44.6.4':
                c.execute('insert or replace into device_capabilities values(?,?,?,?)',(g.bridge_device['id'],x['mode'],14,x['app_version']))
        return jsonify(ok=True)
    @app.post('/api/bridge/disconnect')
    def bridge_disconnect():
        # Keep device, credentials, account bindings and job state; change connectivity only.
        with conn() as c:c.execute("update devices set status='OFFLINE' where id=?",(g.bridge_device['id'],))
        return jsonify(ok=True)
    @app.post('/api/bridge/claim')
    def bridge_claim():
        with conn() as c:
            c.execute('BEGIN IMMEDIATE');did=g.bridge_device['id']
            expired=c.execute("select publication_id from ui_jobs where device_id=? and status in ('RUNNING','VERIFYING') and lease_until<?",(did,time.time())).fetchall()
            for row in expired:c.execute("update publications set status='NEEDS_REVIEW',error='Bridge lost connection; inspect app before retry' where id=?",(row['publication_id'],))
            c.execute("update ui_jobs set status='NEEDS_REVIEW',lease_hash=null where device_id=? and status in ('RUNNING','VERIFYING') and lease_until<?",(did,time.time()))
            if c.execute("select id from ui_jobs where device_id=? and status in ('RUNNING','VERIFYING','NEEDS_REVIEW')",(did,)).fetchone():return jsonify(job=None,paused=True)
            row=c.execute("select * from ui_jobs where device_id=? and status='QUEUED' and available<=? order by created_at limit 1",(did,time.time())).fetchone()
            if not row:return jsonify(job=None)
            lease=secrets.token_urlsafe(32)
            c.execute("update ui_jobs set status='RUNNING',lease_until=?,lease_hash=? where id=?",(time.time()+TTL,digest(lease),row['id']))
            c.execute("update publications set status='RUNNING' where id=?",(row['publication_id'],))
        return jsonify(job=dict(id=row['id'],lease=lease,payload=json.loads(row['payload']),phase=row['phase']))
    def current(jid,allow_done=False):
        with conn() as c:row=c.execute('select * from ui_jobs where id=? and device_id=?',(jid,g.bridge_device['id'])).fetchone()
        if not row or not row['lease_hash'] or not hmac.compare_digest(row['lease_hash'],digest(request.headers.get('X-Job-Lease',''))):return None
        if allow_done and row['status']=='DONE':return dict(row)
        if row['status'] not in ('RUNNING','VERIFYING') or row['lease_until']<time.time():return None
        return dict(row)
    @app.post('/api/bridge/jobs/<jid>/verification-claim')
    def verification_claim(jid):
        with conn() as c:
            c.execute('BEGIN IMMEDIATE');did=g.bridge_device['id']
            row=c.execute('select * from ui_jobs where id=? and device_id=?',(jid,did)).fetchone()
            if not row:return jsonify(error='Unknown job; never reconstruct or resend'),404
            if row['status']=='DONE':return jsonify(job=None,done=True)
            if row['status'] not in ('NEEDS_REVIEW','VERIFYING') or row['phase'] not in ('SUBMITTED','UI_CONFIRMED'):
                return jsonify(error='Only submitted jobs may be verified; no publication retry'),409
            if row['status']=='VERIFYING' and row['lease_until'] and row['lease_until']>=time.time():return jsonify(error='Verification already owned'),409
            if c.execute("select id from ui_jobs where device_id=? and id!=? and status in ('RUNNING','VERIFYING')",(did,jid)).fetchone():return jsonify(error='Phone busy'),409
            lease=secrets.token_urlsafe(32)
            c.execute("update ui_jobs set status='VERIFYING',lease_hash=?,lease_until=? where id=?",(digest(lease),time.time()+TTL,jid))
        return jsonify(job=dict(id=jid,lease=lease,payload=json.loads(row['payload']),phase=row['phase'],verification_only=True))
    @app.post('/api/bridge/jobs/<jid>/renew')
    def bridge_renew(jid):
        if not current(jid):return jsonify(error='Invalid job lease'),409
        with conn() as c:c.execute('update ui_jobs set lease_until=? where id=?',(time.time()+TTL,jid))
        return jsonify(ok=True)
    @app.get('/api/bridge/jobs/<jid>/media')
    def bridge_media(jid):
        row=current(jid)
        if not row or row['status']=='VERIFYING':return jsonify(error='Media unavailable for verification-only lease'),409
        with conn() as c:r=c.execute('select c.source_file from clips c join publications p on c.id=p.clip_id where p.id=?',(row['publication_id'],)).fetchone()
        return send_from_directory(uploads,r['source_file'],as_attachment=True)
    @app.post('/api/bridge/jobs/<jid>/phase')
    def bridge_phase(jid):
        row=current(jid)
        if not row:return jsonify(error='Invalid job lease'),409
        value=(request.get_json(silent=True) or {}).get('phase')
        if row['status']=='VERIFYING' and value!='UI_CONFIRMED':return jsonify(error='Verification-only phase restriction'),409
        allowed={'NEW':['PUBLISH_STARTED'],'PUBLISH_STARTED':['SUBMITTED'],'SUBMITTED':['UI_CONFIRMED']}
        if value!=row['phase'] and value not in allowed.get(row['phase'],[]):return jsonify(error='Invalid phase transition'),409
        with conn() as c:c.execute('update ui_jobs set phase=? where id=?',(value,jid))
        return jsonify(ok=True)
    @app.post('/api/bridge/jobs/<jid>/evidence')
    def bridge_evidence(jid):
        if not current(jid):return jsonify(error='Invalid job lease'),409
        body=request.stream.read(4*1024*1024+1)
        valid=len(body)>=45 and body.startswith(b'\x89PNG\r\n\x1a\n') and body[12:16]==b'IHDR' and body[-8:-4]==b'IEND'
        if valid:
            width,height=struct.unpack('>II',body[16:24]);valid=0<width<=8192 and 0<height<=8192
        if len(body)>4*1024*1024 or not valid:return jsonify(error='PNG screenshot required (max 4 MB)'),400
        directory=os.path.join(os.path.dirname(uploads),'evidence');os.makedirs(directory,exist_ok=True)
        name=jid+'.png';path=os.path.join(directory,name)
        with open(path,'wb') as f:f.write(body)
        with conn() as c:c.execute('update ui_jobs set evidence=? where id=?',(name,jid))
        return jsonify(ok=True)
    @app.post('/api/bridge/jobs/<jid>/complete')
    def bridge_complete(jid):
        row=current(jid,allow_done=True)
        if not row:return jsonify(error='Invalid job lease'),409
        if row['status']=='DONE':return jsonify(ok=True,existing=True)
        if row['phase']!='UI_CONFIRMED' or not row['evidence']:return jsonify(error='Calibrated UI confirmation and screenshot are required'),409
        x=request.get_json(silent=True) or {};payload=json.loads(row['payload'])
        url=x.get('post_url','')
        if not isinstance(url,str) or not re.fullmatch(r'https://www\.tiktok\.com/@redmaagi/video/[0-9]{10,25}',url) or x.get('verification')!='PROFILE_MATCHING_POST_REOPENED_TWICE_BY_URL' or x.get('sha256')!=payload.get('sha256') or x.get('caption')!=payload.get('caption'):
            return jsonify(error='Verified post URL, caption, and source hash are required'),409
        result={'confirmed_by':'ANDROID_PROFILE_AND_URL','post_url':url,'source_sha256':x['sha256'],'verification':x['verification'],'note':'Matched profile/caption and reopened twice; not public-viewer or binary identity verification'}
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            changed=c.execute("update ui_jobs set status='DONE',result=? where id=? and status in ('RUNNING','VERIFYING')",(json.dumps(result),jid))
            if changed.rowcount:
                c.execute("update publications set status='UI_CONFIRMED',published_at=?,external_id=?,error=null where id=?",(now(),url,row['publication_id']))
                c.execute("update tasks set done=min(target,done+1) where unit in ('клипов','публикаций')")
        return jsonify(ok=True)
    @app.post('/api/bridge/jobs/<jid>/fail')
    def bridge_fail(jid):
        row=current(jid)
        if not row:return jsonify(error='Invalid job lease'),409
        # Never accept full HTTP exception strings containing URLs or bearer tokens.
        code=str((request.get_json(silent=True) or {}).get('code','UI_REVIEW'))
        if not re.fullmatch(r'[A-Z_]{1,60}',code):code='UI_REVIEW'
        with conn() as c:
            c.execute("update ui_jobs set status='NEEDS_REVIEW',error=? where id=?",(code,jid))
            c.execute("update publications set status='NEEDS_REVIEW',error=? where id=?",(code,row['publication_id']))
        return jsonify(ok=True)
    @app.post('/api/bridge/jobs/<jid>/prepared')
    def bridge_prepared(jid):
        row=current(jid)
        if not row:return jsonify(error='Invalid job lease'),409
        if row['phase']!='NEW' or not row['evidence']:return jsonify(error='Preparation requires NEW phase and screenshot evidence'),409
        x=request.get_json(silent=True) or {}
        if not re.fullmatch(r'[a-f0-9]{64}',str(x.get('sha256',''))):return jsonify(error='File SHA-256 required'),400
        expected='faxclip-'+jid+'.mp4'
        if x.get('remote_filename')!=expected:return jsonify(error='Unexpected phone filename'),400
        with conn() as c:
            c.execute('BEGIN IMMEDIATE')
            changed=c.execute("update ui_jobs set status='TRANSFERRED_NEEDS_AUTOMATION',result=? where id=? and status='RUNNING'",(json.dumps(x),jid))
            if changed.rowcount!=1:return jsonify(error='Job state changed; preparation cancelled'),409
            c.execute("update publications set status='TRANSFERRED_NEEDS_AUTOMATION',error='File transferred; editor and publishing not implemented' where id=?",(row['publication_id'],))
        # No published_at, external_id, or KPI change. This is not a publication result.
        return jsonify(ok=True,status='TRANSFERRED_NEEDS_AUTOMATION')

    @app.get('/api/publications/<pid>/evidence')
    def bridge_get_evidence(pid):
        with conn() as c:row=c.execute('select evidence from ui_jobs where publication_id=?',(pid,)).fetchone()
        if not row or not row['evidence']:return jsonify(error='Screenshot not available'),404
        return send_from_directory(os.path.join(os.path.dirname(uploads),'evidence'),row['evidence'])
    @app.post('/api/devices/<did>/revoke')
    def bridge_revoke(did):
        with conn() as c:
            c.execute("update devices set token_hash=?,status='REVOKED' where id=?",(digest(secrets.token_urlsafe(64)),did))
            c.execute("update publications set status='NEEDS_REVIEW' where id in (select publication_id from ui_jobs where device_id=? and status in ('RUNNING','VERIFYING'))",(did,))
            c.execute("update ui_jobs set status='NEEDS_REVIEW',lease_hash=null where device_id=? and status in ('RUNNING','VERIFYING')",(did,))
        return jsonify(ok=True)

    original_clips=app.view_functions['clips']
    def clips_with_captions():
        if request.method=='GET':
            with conn() as c:rows=c.execute("select c.*,coalesce(t.caption,'') caption from clips c left join clip_captions t on t.clip_id=c.id order by c.created_at desc").fetchall()
            return jsonify([dict(r) for r in rows])
        x=request.get_json(silent=True) or {};caption=x.get('caption','')
        if not isinstance(caption,str) or len(caption)>2200:return jsonify(error='Invalid description'),400
        r=original_clips();cid=r.get_json().get('id')
        with conn() as c:c.execute('insert into clip_captions values(?,?)',(cid,caption))
        return r
    app.view_functions['clips']=clips_with_captions
    def upload_mp4():
        f=request.files.get('file')
        if not f or not (f.filename or '').lower().endswith('.mp4'):return jsonify(error='Выберите MP4-видео'),400
        name=uuid.uuid4().hex+'.mp4';path=os.path.join(uploads,name);f.save(path)
        with open(path,'rb') as media:head=media.read(64)
        if len(head)<16 or head[4:8]!=b'ftyp':os.unlink(path);return jsonify(error='Файл не распознан как контейнер MP4'),400
        return jsonify(filename=name)
    app.view_functions['media_upload']=upload_mp4
