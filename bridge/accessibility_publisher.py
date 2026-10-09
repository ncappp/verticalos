"""FaxClip queue -> verified Android import -> caption -> one publish -> URL check.
No uiautomator during the route. No input taps, tests, retry authorization or credential UI.
"""
import base64,hashlib,json,os,re,shlex,subprocess,time,uuid
from pathlib import Path
from urllib.parse import urlparse
import requests
from adb_control import ScreenError

PKG='com.zhiliaoapp.musically'
VERIFY_OK='PROFILE_MATCHING_POST_REOPENED_TWICE_BY_URL'

class AccessibilityPublisher:
    def __init__(self,bridge,job):
        self.b=bridge;self.adb=bridge.adb;self.job=job;self.payload=job['payload'];self.name='faxclip-auto-'+uuid.uuid4().hex+'.mp4'
        self.log=[];self.name_bound=False
    def checkpoint(self,stage,prior_url=None,post_url=None):
        record=dict(job_id=self.job['id'],name=self.name,sha256=self.payload['sha256'],
                    caption=self.payload['caption'],prior_url=prior_url,post_url=post_url,stage=stage)
        file=self.b.work/(self.job['id']+'-verification.json');tmp=file.with_suffix('.tmp')
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f:
            json.dump(record,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(tmp,file)
        d=os.open(self.b.work,os.O_RDONLY)
        try:os.fsync(d)
        finally:os.close(d)
    def reopen_verified(self,url):
        for round in (1,2):
            self.open_url(url)
            for inspection in range(4):
                state=self.control('verification_reopened')
                if state=='MATCHING_POST_REOPENED_BY_URL':break
                if state not in ('POST_AUTHOR_OR_CAPTION_NOT_MATCHED','TIKTOK_NOT_FOREGROUND'):
                    raise ScreenError('REOPEN_'+state)
                if inspection<3:time.sleep(3)
            else:raise ScreenError('REOPEN_'+state)
            self.capture('reopened-'+str(round))
    def verify_existing(self,record):
        if record.get('job_id')!=self.job['id'] or record.get('sha256')!=self.payload.get('sha256') or record.get('caption')!=self.payload.get('caption'):
            raise ScreenError('RECOVERY_JOB_BINDING_MISMATCH')
        if record.get('stage') not in ('SUBMITTED','LINK_CAPTURED','VERIFIED') or not re.fullmatch(r'faxclip-auto-[a-f0-9]{32}\.mp4',record.get('name','')):
            raise ScreenError('NO_SUBMISSION_CHECKPOINT_NO_RETRY')
        self.name=record['name'];self.bind()
        previous=record.get('prior_url');known=record.get('post_url')
        for attempt in range(3):
            self.bind()
            try:
                if self.find_first_post():
                    url=self.post_link()
                    if url==previous:raise ScreenError('PRIOR_POST_NOT_NEW_NO_RETRY')
                    if known and url!=self.canonical(known):raise ScreenError('RECOVERY_POST_URL_CHANGED')
                    # Retain this exact link across all later retries, including this invocation.
                    known=url
                    self.checkpoint('LINK_CAPTURED',previous,url)
                    self.reopen_verified(url)
                    self.checkpoint('VERIFIED',previous,url)
                    return dict(post_url=url,verification=VERIFY_OK,sha256=self.payload['sha256'],caption=self.payload['caption'])
            except ScreenError as exc:
                code=str(exc)
                retryable=code.startswith('LINK_READ_') and code[10:] in ('NO_FRESH_LINK','NO_VALID_TIKTOK_LINK','CLIPBOARD_READ_FAILED','CLIPBOARD_NOT_FOCUSED','BASELINE_CAPTURED')
                retryable=retryable or code in ('REOPEN_POST_AUTHOR_OR_CAPTION_NOT_MATCHED','REOPEN_TIKTOK_NOT_FOREGROUND','PROFILE_TIKTOK_NOT_FOREGROUND','VERIFY_TIKTOK_NOT_FOREGROUND')
                if not retryable:raise
                self.log_state(code)
            if attempt<2:time.sleep(5)
        raise ScreenError('SUBMITTED_RESULT_NOT_CONFIRMED_NO_RETRY')
    def log_state(self,state):
        if not re.fullmatch(r'[A-Z0-9_;=:.@/\-]+',state):state='STATE_RECORDED'
        self.log.append(state)
        self.log=self.log[-200:]
    def control(self,cmd,*extras):
        self.b.check_lease()
        reply=self.adb.shell('am','broadcast','-a','com.faxclip.access.CONTROL','-p','com.faxclip.access','--es','cmd',cmd,*extras)
        match=re.search(r'data="([^"]*)"',reply)
        state=match.group(1) if match else 'NO_HELPER_RESPONSE';self.log_state(state);return state
    def provider(self,method,*extras):
        self.b.check_lease()
        return self.adb.shell('content','call','--uri','content://com.faxclip.access.media','--method',method,'--arg',self.name,*extras)
    def wait(self,start_cmd,accepted,target,seconds,*extras):
        start=self.control(start_cmd,*extras)
        if start!=accepted:raise ScreenError('HELPER_'+start)
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            self.b.check_lease();state=self.control('tiktok_route_state')
            if state==target:return state
            if not state.startswith('RUNNING_'):raise ScreenError('HELPER_'+state)
            time.sleep(1)
        raise ScreenError('HELPER_ROUTE_TIMEOUT')
    def capture(self,label):
        # Local evidence only; authenticated upload of the final image is performed by caller.
        raw=self.adb.run('exec-out','screencap','-p',binary=True)
        (self.b.work/(self.job['id']+'-'+label+'.png')).write_bytes(raw)
        return raw
    def clipboard(self,mode,token):
        self.b.check_lease()
        self.adb.shell('am','start','-W','-n','com.faxclip.access/.VerificationClipboardActivity','--es','mode',mode,'--es','token',token)
        time.sleep(2)
        return self.control('verification_link_result','--es','token',token)
    def open_url(self,url):
        self.b.check_lease()
        self.adb.shell('am','start','-W','-a','android.intent.action.VIEW','-d',url,'-p',PKG)
        time.sleep(8)
    @staticmethod
    def canonical(url):
        if not re.fullmatch(r'https://(?:www\.tiktok\.com/@redmaagi/video/[0-9]{10,25}|(?:vm|vt)\.tiktok\.com/[A-Za-z0-9]{4,40}/?)',url):raise ScreenError('INVALID_POST_URL')
        # Canonical link already has the account and ID; short-link resolution is bounded and HTTPS-only.
        for _ in range(4):
            if re.fullmatch(r'https://www\.tiktok\.com/@redmaagi/video/[0-9]{10,25}',url):return url
            parsed=urlparse(url)
            if parsed.scheme!='https' or parsed.hostname not in ('vm.tiktok.com','vt.tiktok.com','www.tiktok.com'):raise ScreenError('LINK_REDIRECT_REJECTED')
            with requests.head(url,allow_redirects=False,timeout=20) as r:
                if not (300<=r.status_code<400):raise ScreenError('LINK_NOT_RESOLVED')
                target=r.headers.get('Location','')
            url=target.split('?',1)[0]
        raise ScreenError('LINK_REDIRECT_LIMIT')
    def bind(self):
        if self.control('verification_job','--es','job_name',self.name)!='VERIFICATION_JOB_BOUND':raise ScreenError('VERIFICATION_JOB_NOT_BOUND')
    def find_first_post(self):
        """Only first PUBLIC tile, never a draft or another older matching caption."""
        self.clip_token=uuid.uuid4().hex
        if self.clipboard('baseline',self.clip_token)!='BASELINE_CAPTURED;url=':raise ScreenError('CLIPBOARD_BASELINE_FAILED')
        self.open_url('https://www.tiktok.com/@redmaagi')
        state=self.control('verification_candidate','--ei','index','0')
        if state=='NO_MORE_VISIBLE_PUBLIC_TILES':return None
        if state!='PROFILE_CANDIDATE_OPENED':raise ScreenError('PROFILE_'+state)
        time.sleep(5);self.capture('candidate')
        state=self.control('verification_inspect')
        if state=='POST_AUTHOR_OR_CAPTION_NOT_MATCHED':return None
        if state!='PROFILE_MATCHING_CAPTION_FOUND':raise ScreenError('VERIFY_'+state)
        return True
    def post_link(self):
        # Baseline must be read before copying. Activity returns to the same matched post.
        token=self.clip_token
        if self.control('verification_share')!='VERIFICATION_SHARE_OPENED':raise ScreenError('SHARE_LINK_UNAVAILABLE')
        time.sleep(2);self.capture('share-link')
        if self.control('verification_copy_link')!='COPY_LINK_ACTION_ACCEPTED':raise ScreenError('COPY_LINK_UNAVAILABLE')
        time.sleep(3)
        # Re-read only; never recopy, reset baseline, publish or authorize a retry.
        for attempt in range(3):
            result=self.clipboard('read',token)
            if result.startswith('FRESH_TIKTOK_LINK_CAPTURED;url='):
                return self.canonical(result.split(';url=',1)[1])
            reason=result.split(';',1)[0]
            if not re.fullmatch(r'[A-Z_]{1,48}',reason):reason='UNCLASSIFIED'
            if reason not in ('NO_FRESH_LINK','NO_VALID_TIKTOK_LINK','CLIPBOARD_READ_FAILED','CLIPBOARD_NOT_FOCUSED','BASELINE_CAPTURED'):
                break
            if attempt<2:time.sleep(3)
        raise ScreenError('LINK_READ_'+reason)
    def import_and_configure(self,path):
        sha=hashlib.sha256()
        with open(path,'rb') as f:
            for block in iter(lambda:f.read(1024*1024),b''):sha.update(block)
        digest=sha.hexdigest();size=Path(path).stat().st_size
        if digest!=self.payload.get('sha256') or size!=self.payload.get('bytes'):raise ScreenError('SOURCE_HASH_OR_SIZE_MISMATCH')
        self.b.check_lease()
        uri='content://com.faxclip.access.media/imports/'+self.name
        # Binary stdin, no shell text input or arbitrary local paths on Android.
        with open(path,'rb') as f:
            try:r=subprocess.run([self.adb.executable,'-s',self.adb.serial,'shell','-T','content','write','--uri',uri],stdin=f,capture_output=True,timeout=300)
            except (OSError,subprocess.SubprocessError) as exc:raise ScreenError('IMPORT_WRITE_FAILED') from exc
        if r.returncode:raise ScreenError('IMPORT_WRITE_FAILED')
        reply=self.provider('finish_import','--extra','sha256:s:'+digest,'--extra','size:l:'+str(size))
        match=re.search(r'content://media/external_primary/video/media/[0-9]+',reply)
        if 'status=IMPORTED' not in reply or not match:raise ScreenError('IMPORT_NOT_VERIFIED')
        source=match.group()
        caption=self.payload.get('caption','')
        if not caption.strip() or len(caption)>2200 or self.payload.get('rights_confirmed') is not True:raise ScreenError('CAPTION_OR_CONSENT_INVALID')
        caption64=base64.b64encode(caption.encode('utf8')).decode('ascii')
        reply=self.provider('configure_job','--extra','caption64:s:'+caption64,'--extra','mode:s:PUBLISH','--extra','account:s:@redmaagi','--extra','rights:s:confirmed')
        if 'status=JOB_CONFIGURED' not in reply:raise ScreenError('JOB_CONFIG_REJECTED')
        self.bind();self.name_bound=True
        return source,digest
    def run(self,path):
        if self.payload.get('platform')!='TikTok' or self.payload.get('username')!='@redmaagi':raise ScreenError('UNSUPPORTED_VERIFIED_ACCOUNT')
        self.adb.ready()
        if self.control('status')!='SERVICE_CONNECTED_V14':raise ScreenError('HELPER_V14_REQUIRED')
        package=self.adb.shell('dumpsys','package',PKG)
        if not re.search(r'versionName=44\.6\.4(?:\s|$)',package):raise ScreenError('TIKTOK_VERSION_NOT_CALIBRATED')
        source,sha=self.import_and_configure(path)
        # Obtain the URL of any existing top post with identical caption, BEFORE publishing.
        # If that URL cannot be read, fail closed rather than later claiming an older post as new.
        prior_url=self.post_link() if self.find_first_post() else None
        self.checkpoint('READY',prior_url)
        self.adb.shell('am','start','-W','-n',PKG+'/com.ss.android.ugc.aweme.splash.SplashActivity');time.sleep(2)
        self.wait('tiktok_open_videos_start','ROUTE_STARTED','VIDEO_TAB_SELECTED',130,'--es','expected_account','@redmaagi')
        self.capture('gallery')
        self.adb.shell('am','start','-W','-a','android.intent.action.SEND','-t','video/mp4','--eu','android.intent.extra.STREAM',source,'--grant-read-uri-permission','-p',PKG)
        time.sleep(1)
        self.wait('tiktok_share_video_start','SHARE_ROUTE_STARTED','SHARE_VIDEO_ACTION_ACCEPTED',35,'--es','job_name',self.name)
        time.sleep(3);self.capture('editor')
        self.wait('tiktok_editor_next_start','EDITOR_NEXT_ROUTE_STARTED','EDITOR_NEXT_ACTION_ACCEPTED',35)
        # Durable server intent BEFORE Android can issue any irreversible click.
        self.checkpoint('PUBLISH_INTENT',prior_url)
        self.b.call('/jobs/'+self.job['id']+'/phase',{'phase':'PUBLISH_STARTED'},self.job)
        self.wait('tiktok_submission_start','SUBMISSION_ROUTE_STARTED','PUBLICATION_SUBMITTED_UNVERIFIED',50,'--es','job_name',self.name)
        self.checkpoint('SUBMITTED',prior_url)
        self.b.call('/jobs/'+self.job['id']+'/phase',{'phase':'SUBMITTED'},self.job)
        self.capture('after-submit');time.sleep(15)
        record=json.loads((self.b.work/(self.job['id']+'-verification.json')).read_text())
        return self.verify_existing(record)
    def save_log(self):
        (self.b.work/(self.job['id']+'-route.json')).write_text(json.dumps({'states':self.log,'no_automatic_retry':True},indent=2),encoding='utf8')
