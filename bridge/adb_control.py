"""ADB wrapper. Commands are arrays, never shell interpolation on the PC."""
import base64,json,re,subprocess,time,shlex,xml.etree.ElementTree as ET
from pathlib import Path

PACKAGES={'TikTok':'com.zhiliaoapp.musically','YouTube':'com.google.android.youtube','Instagram':'com.instagram.android','VK':'com.vkontakte.android'}
IME='com.faxclip.ime/.FaxClipIME'
class ScreenError(RuntimeError):pass

def parse_nodes(xml):
    nodes=[]
    try:root=ET.fromstring(xml)
    except ET.ParseError as exc:raise ScreenError('UI hierarchy is not available; stop rather than guess') from exc
    for e in root.iter('node'):
        a=dict(e.attrib);m=re.fullmatch(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]',a.get('bounds',''))
        if not m:continue
        x1,y1,x2,y2=map(int,m.groups())
        if x2<=x1 or y2<=y1:continue
        a['center']=((x1+x2)//2,(y1+y2)//2);nodes.append(a)
    return nodes

def matches(node,selector,variables=None):
    variables=variables or {}
    fields={'text':'text','id':'resource-id','desc':'content-desc','class':'class'}
    if not selector or not any(k in fields for k in selector):raise ScreenError('An exact selector is required')
    for key,field in fields.items():
        if key in selector:
            expected=selector[key]
            for name,value in variables.items():expected=expected.replace('${'+name+'}',str(value))
            if node.get(field,'')!=expected:return False
    return node.get('enabled','true')=='true'

class ADB:
    def __init__(self,serial,executable='adb'):
        if not serial or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',serial):raise ScreenError('Invalid ADB serial')
        self.serial=serial;self.executable=executable
    def run(self,*args,timeout=30,binary=False):
        try:r=subprocess.run([self.executable,'-s',self.serial,*map(str,args)],capture_output=True,timeout=timeout)
        except (OSError,subprocess.SubprocessError) as e:raise ScreenError('ADB unavailable, device offline, or command timed out') from e
        if r.returncode:raise ScreenError('ADB command failed. Check USB authorization and device connection.')
        return r.stdout if binary else r.stdout.decode('utf8',errors='replace').strip()
    def shell(self,*args,**kw):return self.run('shell',*(shlex.quote(str(a)) for a in args),**kw)
    def ready(self):
        if self.run('get-state')!='device':raise ScreenError('Phone is not authorized for USB debugging')
        # Do not unlock devices or interact with credential/challenge screens.
        policy=self.shell('dumpsys','window','policy')
        if re.search(r'(?:isStatusBarKeyguard|mShowingLockscreen|showing)=true',policy):raise ScreenError('Unlock the phone manually before starting work')
    def assert_app(self,package):
        windows=self.shell('dumpsys','window','windows')
        focus='\n'.join(line for line in windows.splitlines() if 'mCurrentFocus' in line or 'mFocusedApp' in line)
        if package not in focus:raise ScreenError('Unexpected foreground application; stop for review')
    def launch(self,package):
        if not re.fullmatch(r'[A-Za-z0-9_.]+',package):raise ScreenError('Invalid app package')
        if not self.shell('pm','path',package).startswith('package:'):raise ScreenError('Target app is not installed')
        self.shell('monkey','-p',package,'-c','android.intent.category.LAUNCHER','1');time.sleep(2)
    def tree(self):
        self.shell('uiautomator','dump','/sdcard/faxclip-ui.xml',timeout=30)
        raw=self.shell('cat','/sdcard/faxclip-ui.xml')
        start=raw.find('<?xml');return raw[start:] if start>=0 else raw
    def capture(self,directory):
        out=Path(directory);out.mkdir(parents=True,exist_ok=True)
        out.joinpath('screen.png').write_bytes(self.run('exec-out','screencap','-p',binary=True))
        out.joinpath('ui.xml').write_text(self.tree(),encoding='utf8')
        return out
    def find(self,selector,variables=None,timeout=20):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            nodes=[n for n in parse_nodes(self.tree()) if matches(n,selector,variables)]
            if len(nodes)==1:return nodes[0]
            if len(nodes)>1:raise ScreenError('Selector is ambiguous. Recalibrate; no random tap will be sent.')
            time.sleep(1)
        raise ScreenError('Expected UI element not found. Update the profile for this app version.')
    def tap(self,selector,variables=None):
        node=self.find(selector,variables);self.shell('input','tap',*node['center']);time.sleep(1)
    def write(self,selector,text,variables=None):
        node=self.find(selector,variables)
        if node.get('password')=='true':raise ScreenError('FaxClip will not type into password fields')
        if node.get('text'):raise ScreenError('Caption editor is not empty; clear it manually and recalibrate')
        previous=self.shell('settings','get','secure','default_input_method')
        try:
            self.shell('ime','enable',IME);self.shell('ime','set',IME)
            self.tap(selector,variables)
            value=base64.b64encode(text.encode('utf8')).decode()
            reply=self.shell('am','broadcast','-a','com.faxclip.ime.COMMIT','-p','com.faxclip.ime','--es','b64',value)
            if 'result=1' not in reply or 'COMMITTED' not in reply:raise ScreenError('Text helper did not confirm text input')
            time.sleep(1)
            expected_id=node.get('resource-id')
            editors=[n for n in parse_nodes(self.tree()) if n.get('class')=='android.widget.EditText' and (not expected_id or n.get('resource-id')==expected_id)]
            if not any(n.get('text')==text for n in editors):raise ScreenError('Caption text was not confirmed in the editor; stop before publishing')
        finally:
            if previous and previous!='null':self.shell('ime','set',previous)
    def share_video(self,path,package,job_id):
        # Filename contains only server-generated UUID chars, not arbitrary user input.
        if not re.fullmatch(r'[A-Fa-f0-9-]{36}',job_id):raise ScreenError('Invalid job ID')
        name='faxclip-'+job_id+'.mp4';remote='/sdcard/Movies/FaxClip/'+name
        self.shell('mkdir','-p','/sdcard/Movies/FaxClip');self.run('push',str(path),remote,timeout=180)
        self.shell('am','broadcast','-a','android.intent.action.MEDIA_SCANNER_SCAN_FILE','-d','file://'+remote)
        media_id=None
        for _ in range(30):
            result=self.shell('content','query','--uri','content://media/external/video/media','--projection','_id','--where',"_display_name='"+name+"'")
            match=re.search(r'_id=(\d+)',result)
            if match:media_id=match.group(1);break
            time.sleep(1)
        if not media_id:raise ScreenError('Android did not index the video. No unsafe file URI fallback is used.')
        uri='content://media/external/video/media/'+media_id
        self.shell('am','start','-a','android.intent.action.SEND','-t','video/mp4','--eu','android.intent.extra.STREAM',uri,'--grant-read-uri-permission','-p',package)
        time.sleep(2);self.assert_app(package)
        return remote
