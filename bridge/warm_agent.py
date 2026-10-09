#!/usr/bin/env python3
"""FaxClip warm-up agent (Mac, USB). Runs rule-based TikTok sessions planned by the server:
watch the feed, sometimes search by the account keywords, like/follow within scenario limits.
No AI: it stops and asks for a human on captcha, lock screen or anything unexpected.
Publications always win: the server tells the agent to yield as soon as a post is due."""
import argparse,fcntl,json,os,random,re,shlex,subprocess,threading,time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote
import requests
SERVER=os.getenv('FAXCLIP_WARM_SERVER','https://verticalos-rxdl.onrender.com')
VERSION='WARM_AGENT_V1'
PKGS=('com.zhiliaoapp.musically','com.ss.android.ugc.trill')
LIKE=re.compile(r'(?i)^(like|нравится|лайк|поставить лайк)|(like video|нравится)')
FOLLOW=re.compile(r'(?i)^(follow|подписаться)$')
POPUP=re.compile(r'(?i)^(not now|не сейчас|skip|пропустить|close|закрыть|got it|понятно|ok|ок|later|позже|cancel|отмена|dismiss|no thanks|нет, спасибо)$')
CAPTCHA=re.compile(r'(?i)(verify|drag the slider|captcha|подтвердите|перетащите|не робот|security check|проверка безопасности)')
LOGGED_OUT=re.compile(r'(?i)(log in to tiktok|войти в tiktok|sign up for tiktok|зарегистрироваться в tiktok|use phone / email)')

class Stop(Exception):
    def __init__(self,status,msg):super().__init__(msg);self.status=status;self.msg=msg

class Phone:
    def __init__(self,adb,serial,cfg,cache):
        self.adb=adb;self.serial=serial;self.h={'X-Device-ID':cfg['device_id'],'Authorization':'Bearer '+cfg['device_token']}
        self.w=1080;self.hh=2340;self.pkg=None;self.cache=cache;self.events=[];self.counts={}
    def sh(self,cmd,timeout=30):
        r=subprocess.run([self.adb,'-s',self.serial,'shell',cmd],capture_output=True,timeout=timeout)
        return (r.stdout or b'').decode('utf-8','replace')
    def size(self):
        m=re.search(r'(\d+)x(\d+)',self.sh('wm size'))
        if m:self.w,self.hh=int(m.group(1)),int(m.group(2))
    def tap(self,x,y):self.sh(f'input tap {int(x)} {int(y)}')
    def j(self,v,spread):return v+random.randint(-spread,spread)
    def swipe_next(self):
        x=self.j(self.w//2,self.w//10);y1=self.j(int(self.hh*0.75),self.hh//20);y2=self.j(int(self.hh*0.25),self.hh//20)
        self.sh(f'input swipe {x} {y1} {self.j(x,30)} {y2} {random.randint(180,380)}')
    def foreground(self):
        out=self.sh('dumpsys window | grep -E "mCurrentFocus|mFocusedApp"')
        return any(p in out for p in PKGS)
    def locked(self):
        out=self.sh('dumpsys window | grep -E "mDreamingLockscreen|isStatusBarKeyguard|mShowingLockscreen|KeyguardShowing"')
        return bool(re.search(r'(mDreamingLockscreen|isStatusBarKeyguard|mShowingLockscreen|KeyguardShowing)=true',out))
    def dump(self):
        for _ in range(2):
            out=self.sh('uiautomator dump /sdcard/faxclip-warm.xml >/dev/null 2>&1 && cat /sdcard/faxclip-warm.xml',timeout=25)
            i=out.find('<')
            if i>=0:
                try:
                    root=ET.fromstring(out[i:]);nodes=[]
                    for n in root.iter('node'):
                        m=re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]',n.get('bounds',''))
                        if not m:continue
                        x1,y1,x2,y2=map(int,m.groups())
                        if x2>x1 and y2>y1:nodes.append(dict(text=n.get('text',''),desc=n.get('content-desc',''),click=n.get('clickable')=='true',sel=n.get('selected')=='true',cx=(x1+x2)//2,cy=(y1+y2)//2,x1=x1,wd=x2-x1))
                    return nodes
                except ET.ParseError:pass
            time.sleep(1)
        return None
    def log(self,kind,msg,**details):self.events.append({'type':kind,'message':msg,'details':details})
    def inc(self,k,n=1):self.counts[k]=self.counts.get(k,0)+n
    def check_screen(self,nodes):
        if nodes is None:return
        texts=[(n['text'] or n['desc']).strip() for n in nodes]
        if any(CAPTCHA.search(t) for t in texts if t):raise Stop('human_intervention','TikTok просит пройти проверку (капча). Пройдите её на телефоне и перезапустите сессию.')
        if any(LOGGED_OUT.search(t) for t in texts if t):raise Stop('human_intervention','В TikTok не выполнен вход в аккаунт.')
        for n in nodes:
            t=(n['text'] or n['desc']).strip()
            if n['click'] and t and POPUP.match(t):
                self.tap(n['cx'],n['cy']);self.inc('popups');self.log('popup','Закрыто окно «%s»'%t[:40]);time.sleep(1.2);return
    def launch(self):
        installed=self.sh('pm list packages')
        self.pkg=next((p for p in PKGS if 'package:'+p in installed),None)
        if not self.pkg:raise Stop('failed','На телефоне не установлен TikTok')
        self.sh(f'monkey -p {self.pkg} -c android.intent.category.LAUNCHER 1');time.sleep(7)
        if not self.foreground():raise Stop('failed','TikTok не открылся')
    def like(self,cache):
        cx,cy=self.w//2,int(self.hh*0.45)
        self.tap(cx,cy);time.sleep(0.9)  # pause the video so the screen becomes idle for reading
        nodes=self.dump();done=False
        if nodes:
            self.check_screen(nodes)
            cand=[n for n in nodes if n['click'] and n['x1']>self.w*0.7 and LIKE.search(n['desc'] or n['text'] or '')]
            if cand:
                n=cand[0];cache['like']=[n['cx'],n['cy']]
                if not n['sel']:self.tap(n['cx'],n['cy']);done=True
        elif cache.get('like'):self.tap(*cache['like']);done=True
        time.sleep(0.6);self.tap(cx,cy)  # resume
        return done,nodes
    def follow(self,nodes):
        cand=[n for n in (nodes or []) if n['click'] and FOLLOW.match((n['desc'] or n['text'] or '').strip())]
        if cand:self.tap(cand[0]['cx'],cand[0]['cy']);return True
        return False
    def search(self,kw):
        tag=re.sub(r'[^\w]','',kw,flags=re.U)
        if not tag:return False
        self.sh(f"am start -a android.intent.action.VIEW -d {shlex.quote('https://www.tiktok.com/tag/'+quote(tag))} {self.pkg}");time.sleep(6)
        if not self.foreground():return False
        nodes=self.dump();self.check_screen(nodes)
        grid=[n for n in (nodes or []) if n['click'] and self.hh*0.25<n['cy']<self.hh*0.85 and self.w*0.25<n['wd']<self.w*0.6 and not LIKE.search(n['desc'] or '')]
        x,y=(grid[0]['cx'],grid[0]['cy']) if grid else (self.w//4,int(self.hh*0.55))
        self.tap(x,y);time.sleep(3);return True
    def flush(self,tid):
        r=requests.post(SERVER+f'/api/bridge/warm/{tid}/events',headers=self.h,json={'events':self.events,'counts':self.counts},timeout=20)
        self.events=[]
        if r.status_code==404:raise Stop('failed','Задача удалена')
        x=r.json()
        if not x.get('go'):raise Stop('yield','Пауза: на телефоне ждёт публикация' if x.get('reason')=='publication' else 'Сессия остановлена владельцем')
    def session(self,task):
        sc=task['scenario'];end=time.time()+task['duration'];self.events=[];self.counts={};start=time.time()
        cfile=self.cache/(re.sub(r'[^A-Za-z0-9]','_',self.serial)+'.json')
        try:cache=json.loads(cfile.read_text())
        except Exception:cache={}
        self.sh('input keyevent 224');time.sleep(1);self.size()
        if self.locked():
            self.sh('wm dismiss-keyguard');time.sleep(1.5)
            if self.locked():raise Stop('human_intervention','Телефон заблокирован. Снимите блокировку экрана (или поставьте «Нет»), чтобы прогрев работал.')
        self.launch();self.log('open','TikTok открыт')
        nodes=self.dump();self.check_screen(nodes)
        kws=[k for k in task.get('keywords') or [] if not any(d in k.lower() for d in task.get('denied') or [])]
        if kws and random.randint(1,100)<=sc['search_pct']:
            kw=random.choice(kws)
            if self.search(kw):self.inc('searches');self.log('search','Поиск по #%s'%kw)
            else:self.log('search','Поиск не открылся — смотрю ленту');self.launch()
        last_flush=0;n=0
        while time.time()<end:
            watch=random.uniform(sc['watch_min'],sc['watch_max'])
            if random.random()<0.08:watch*=random.uniform(1.5,2.5)  # sometimes watch longer, like a person
            t0=time.time()
            while time.time()-t0<watch and time.time()<end:
                time.sleep(min(5,watch-(time.time()-t0)+0.01))
                if time.time()-last_flush>25:self.counts['seconds']=int(time.time()-start);self.flush(task['id']);last_flush=time.time()
            n+=1;self.inc('videos');nodes=None
            if self.counts.get('likes',0)<sc['max_likes'] and random.randint(1,100)<=sc['like_pct']:
                ok,nodes=self.like(cache)
                if ok:self.inc('likes');self.log('like','Лайк')
            if self.counts.get('follows',0)<sc['max_follows'] and random.randint(1,100)<=sc['follow_pct']:
                if nodes is None:nodes=self.dump();self.check_screen(nodes)
                if self.follow(nodes):self.inc('follows');self.log('follow','Подписка')
            if n%6==0:
                if not self.foreground():self.log('recover','TikTok ушёл с экрана — открываю снова');self.launch()
                else:self.check_screen(self.dump())
            if time.time()<end:self.swipe_next();time.sleep(random.uniform(0.6,1.6))
        try:cfile.write_text(json.dumps(cache))
        except Exception:pass
        self.sh('input keyevent 3');self.counts['seconds']=int(time.time()-start)
    def run_task(self,task):
        tid=task['id'];status,msg='done',None
        try:self.session(task)
        except Stop as e:
            status,msg=('done' if e.status=='yield' else e.status),e.msg
            if e.status=='yield':self.log('yield',e.msg)
            try:self.sh('input keyevent 3')
            except Exception:pass
        except Exception as e:status,msg='failed','Ошибка на Mac: '+type(e).__name__+' '+str(e)[:160]
        for _ in range(3):
            try:
                if self.events:
                    try:self.flush(tid)
                    except Stop:pass
                requests.post(SERVER+f'/api/bridge/warm/{tid}/finish',headers=self.h,json={'status':status,'error':msg,'counts':self.counts,'seconds':self.counts.get('seconds',0)},timeout=20);break
            except Exception:time.sleep(5)
        print('task',tid,status,msg or '',flush=True)
    def loop(self,stop):
        while not stop.is_set():
            try:
                r=requests.post(SERVER+'/api/bridge/warm/claim',headers=self.h,json={'version':VERSION},timeout=30)
                if r.status_code in (401,403,404):time.sleep(120);continue
                r.raise_for_status();task=r.json().get('task')
                if task:self.run_task(task)
                else:time.sleep(30)
            except Exception:time.sleep(30)

def configs(root):
    out={}
    for f in (root/'.faxclip-cloud/devices').glob('*.json'):
        try:
            x=json.loads(f.read_text())
            if x.get('server')==SERVER and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',x.get('serial','')) and x.get('device_id') and x.get('device_token'):out[x['serial']]=x
        except Exception:continue
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);a=p.parse_args();root=Path(a.root)
    adb=str(root/'platform-tools/adb')
    lock=open(Path.home()/'.faxclip-warm-agent.lock','a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Прогрев FaxClip уже запущен.')
    cache=Path.home()/'Library/Application Support/FaxClip Warmup/calibration';cache.mkdir(parents=True,exist_ok=True)
    print(VERSION,'started',flush=True);threads={}
    while True:
        try:
            listing=subprocess.run([adb,'devices'],capture_output=True,text=True,timeout=30).stdout
            phones={l.split()[0] for l in listing.splitlines()[1:] if len(l.split())>1 and l.split()[1]=='device'}
            for serial,cfg in configs(root).items():
                key=(serial,cfg['device_id']);alive=key in threads and threads[key][0].is_alive()
                if serial in phones and not alive:
                    stop=threading.Event();ph=Phone(adb,serial,cfg,cache);t=threading.Thread(target=ph.loop,args=(stop,),daemon=True);t.start();threads[key]=(t,stop)
                if serial not in phones and alive:threads[key][1].set();threads.pop(key)
        except Exception:pass
        time.sleep(20)
if __name__=='__main__':main()
