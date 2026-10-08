#!/usr/bin/env python3
import argparse,json,os,re,threading,time,tempfile,hashlib
from pathlib import Path
from urllib.parse import urlparse
import requests
from adb_control import ADB,PACKAGES,ScreenError,matches,parse_nodes

class Bridge:
    def __init__(self,server,phone,work,allow_local=False):
        self.base=server.rstrip('/');url=urlparse(self.base)
        if url.scheme!='https' and not (allow_local and url.hostname in ('localhost','127.0.0.1')):raise ValueError('HTTPS server required')
        self.phone=phone;self.adb=ADB(phone['serial'],phone.get('adb','adb'))
        self.headers={'X-Device-ID':phone['device_id'],'Authorization':'Bearer '+phone['device_token']}
        self.work=Path(work)/phone['device_id'];self.work.mkdir(parents=True,exist_ok=True)
        if os.name!='nt':os.chmod(self.work,0o700)
        import fcntl
        locks=Path.home()/'.faxclip-phone-locks';locks.mkdir(mode=0o700,exist_ok=True)
        self.phone_lock=open(locks/(hashlib.sha256(phone['serial'].encode()).hexdigest()+'.lock'),'a')
        try:fcntl.flock(self.phone_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise ScreenError('PHONE_ALREADY_OWNED_BY_ANOTHER_BRIDGE')
        self.stop=threading.Event();self.lost=threading.Event()
    def call(self,path,body=None,job=None,method='POST',binary=None):
        h={**self.headers}
        if job:h['X-Job-Lease']=job['lease']
        if binary is not None:h['Content-Type']='image/png'
        response=requests.request(method,self.base+'/api/bridge'+path,headers=h,json=body if binary is None else None,data=binary,timeout=30)
        if not response.ok:raise ScreenError(f'FaxClip request failed: HTTP {response.status_code}')
        return response.json()
    def heartbeat(self):
        self.adb.ready();battery=self.adb.shell('dumpsys','battery');match=re.search(r'level:\s*(\d+)',battery)
        self.call('/heartbeat',{'battery':int(match.group(1)) if match else 0,'mode':self.phone.get('mode'),'helper_version':14,'app_version':'44.6.4'})
    def renew(self,job):
        failures=0
        while not self.stop.wait(20):
            try:self.call('/jobs/'+job['id']+'/renew',job=job);failures=0
            except Exception:
                failures+=1
                if failures>=2:self.lost.set();return
    def check_lease(self):
        if self.lost.is_set():raise ScreenError('Job lease was lost; no further taps will be sent')
    def profile(self,platform):
        file=self.phone.get('profiles',{}).get(platform)
        if not file:raise ScreenError('No calibrated profile for '+platform)
        profile=json.loads(Path(file).read_text(encoding='utf8'))
        if not profile.get('enabled'):raise ScreenError('Profile is disabled until calibrated and tested')
        if profile.get('platform')!=platform:raise ScreenError('Profile platform mismatch')
        package=profile.get('package',PACKAGES[platform])
        current=self.adb.shell('dumpsys','package',package);v=re.search(r'versionName=([^\s]+)',current)
        if not v or v.group(1)!=profile.get('app_version'):raise ScreenError('App version differs from the calibrated profile')
        if not profile.get('account_selector') or not profile.get('verification'):raise ScreenError('Profile must verify account and publication outcome')
        if '${username}' not in json.dumps(profile['account_selector']):raise ScreenError('Account selector must check the job username')
        if not any('${caption}' in json.dumps(step) or '${title}' in json.dumps(step) for step in profile['verification']):
            raise ScreenError('Verification must match this particular video, not a generic success button')
        compose=profile.get('compose',[])
        if sum(1 for step in compose if step.get('publish'))!=1 or not compose[-1].get('publish'):
            raise ScreenError('Profile needs exactly one final publish action')
        if platform=='VK' and profile.get('publication_type')!='VK_CLIPS':raise ScreenError('VK profile must explicitly target Clips, not ordinary videos')
        return profile,package
    def step(self,step,variables,package,job):
        self.check_lease();self.adb.assert_app(package)
        action=step.get('action')
        if action=='tap':self.adb.tap(step['selector'],variables)
        elif action=='assert':self.adb.find(step['selector'],variables,timeout=step.get('timeout',20))
        elif action=='text':
            value=variables[step.get('value','caption')]
            if not value:raise ScreenError('Text field requires a non-empty caption/title')
            self.adb.write(step['selector'],value,variables)
        elif action=='back':self.adb.shell('input','keyevent','4');time.sleep(1)
        elif action=='wait':time.sleep(min(60,max(1,int(step.get('seconds',1)))))
        else:raise ScreenError('Unsupported profile action')
    def download(self,job):
        path=self.work/(job['id']+'.mp4');partial=path.with_suffix('.part')
        h={**self.headers,'X-Job-Lease':job['lease']};total=0
        with requests.get(self.base+'/api/bridge/jobs/'+job['id']+'/media',headers=h,stream=True,timeout=60) as response:
            if not response.ok:raise ScreenError('Video download rejected')
            expected=int(response.headers.get('Content-Length',0))
            if expected>1024**3:raise ScreenError('Video exceeds 1 GB limit')
            with open(partial,'wb') as f:
                for data in response.iter_content(1024*1024):
                    self.check_lease();total+=len(data)
                    if total>1024**3:raise ScreenError('Video exceeds 1 GB limit')
                    f.write(data)
            if not total or (expected and expected!=total):raise ScreenError('Video download incomplete')
        os.replace(partial,path);return path
    def execute(self,job):
        self.stop.clear();self.lost.clear();thread=threading.Thread(target=self.renew,args=(job,),daemon=True);thread.start()
        path=None;publisher=None
        try:
            if job.get('verification_only') is not True and job.get('phase')!='NEW':raise ScreenError('Prior publish attempt is ambiguous; no automatic retry')
            variables=job['payload']
            if self.phone.get('mode')=='ACCESSIBILITY_PUBLISH':
                from accessibility_publisher import AccessibilityPublisher
                publisher=AccessibilityPublisher(self,job)
                if job.get('verification_only') is True:
                    record=json.loads((self.work/(job['id']+'-verification.json')).read_text())
                    self.adb.ready()
                    if publisher.control('status')!='SERVICE_CONNECTED_V14':raise ScreenError('HELPER_V14_REQUIRED')
                    package=self.adb.shell('dumpsys','package','com.zhiliaoapp.musically')
                    if not re.search(r'versionName=44\.6\.4(?:\s|$)',package):raise ScreenError('TIKTOK_VERSION_NOT_CALIBRATED')
                    result=publisher.verify_existing(record)
                else:
                    path=self.download(job)
                    result=publisher.run(path)
                self.call('/jobs/'+job['id']+'/phase',{'phase':'UI_CONFIRMED'},job)
                self.call('/jobs/'+job['id']+'/evidence',job=job,binary=publisher.capture('verified'))
                self.call('/jobs/'+job['id']+'/complete',result,job)
                file=self.work/(job['id']+'-verification.json')
                if file.exists():file.rename(self.work/(job['id']+'-verification.done.json'))
                print('FaxClip: verified profile post and URL:',job['id'])
                return
            raise ScreenError('Use ACCESSIBILITY_PUBLISH with helper v14; legacy input-tap automation is disabled')
        except Exception as error:
            message=str(error) if isinstance(error,ScreenError) else 'Unexpected failure; review locally'
            print('Stopped for review:',job['id'],message)
            if job.get('verification_only') is True:(self.work/(job['id']+'-recovery-paused')).touch(mode=0o600)
            try:
                shot=self.adb.run('exec-out','screencap','-p',binary=True)
                self.call('/jobs/'+job['id']+'/evidence',job=job,binary=shot)
            except Exception:pass
            try:self.call('/jobs/'+job['id']+'/fail',{'code':message if re.fullmatch(r'[A-Z_]{1,60}',message) else 'UI_REVIEW'},job)
            except Exception:pass
        finally:
            if publisher:publisher.save_log()
            self.stop.set();thread.join(timeout=3)
            if path:path.unlink(missing_ok=True)
            (self.work/(job['id']+'.part')).unlink(missing_ok=True)
            # Phone copy is intentionally retained until publication processing is complete.
    def run(self,once=False):
        while True:
            try:
                self.heartbeat();job=None
                for file in sorted(self.work.glob('*-verification.json')):
                    try:
                        record=json.loads(file.read_text());jid=record.get('job_id','')
                        if not re.fullmatch(r'[a-f0-9-]{36}',jid):continue
                        if (self.work/(jid+'-recovery-paused')).exists():continue
                        if record.get('stage') not in ('SUBMITTED','LINK_CAPTURED','VERIFIED'):continue
                        response=self.call('/jobs/'+jid+'/verification-claim',{})
                        if response.get('done'):
                            file.rename(self.work/(jid+'-verification.done.json'));continue
                        candidate=response.get('job')
                        if candidate and candidate.get('verification_only') is True:job=candidate;break
                    except Exception:continue
                if job is None:job=self.call('/claim').get('job')
                if job:self.execute(job)
                if once:return
            except Exception:print('USB or server unavailable; retrying without logging credentials')
            time.sleep(10)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',default='bridge-config.json');parser.add_argument('--once',action='store_true');args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf8'));phones=config['phones']
    serials=[p['serial'] for p in phones];ids=[p['device_id'] for p in phones]
    if len(set(serials))!=len(serials) or len(set(ids))!=len(ids):raise ValueError('Each physical serial and FaxClip device ID must be unique')
    threads=[]
    for phone in phones:
        b=Bridge(config['server'],phone,config.get('work_dir','.faxclip-bridge'),config.get('allow_local_test',False))
        t=threading.Thread(target=b.run,args=(args.once,),daemon=False);t.start();threads.append(t)
    for t in threads:t.join()
if __name__=='__main__':main()
