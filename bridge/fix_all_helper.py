"""FaxClip: all-in-one reliability fix. Pauses the manager safely, patches recognised files, resumes."""
import fcntl,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path.home()/'Downloads/faxclip-telegram-bridge-v14'
SUPPORT=Path.home()/'Library/Application Support/FaxClip Manager'
LABEL='com.faxclip.devices'
CLIP="        self.adb.shell('am','start','-W','-n','com.faxclip.access/.VerificationClipboardActivity','--es','mode',mode,'--es','token',token)\n"
WAKE="        # LINK_FIX_V1: Android lets only a focused, awake app read the clipboard.\n        try:self.adb.shell('input','keyevent','KEYCODE_WAKEUP');self.adb.shell('wm','dismiss-keyguard')\n        except Exception:pass\n        time.sleep(1)\n"
EXC="                code=str(exc)\n"
STOP=EXC+"                if code.startswith('LINK_READ_') or code=='CLIPBOARD_READ_TIMEOUT':\n                    # Link already copied once: never revisit or recopy. Owner adds the link in Mini App.\n                    try:(self.b.work/(self.job['id']+'-recovery-paused')).touch()\n                    except Exception:pass\n                    raise ScreenError('LINK_COPIED_NOT_READ')\n"
# Optional: re-read the same copied link instead of copying again (newer builds).
RECOPY="                self.log_state('SAME_POST_LINK_RETRY');continue\n"
REREAD="                self.log_state('SAME_POST_LINK_REREAD')\n                for _ in range(3):\n                    time.sleep(3);result=self.clipboard('read',token)\n                    if result.startswith('FRESH_TIKTOK_LINK_CAPTURED;url='):return self.canonical(result.split(';url=',1)[1])\n                reason=result.split(';',1)[0]\n                if not re.fullmatch(r'[A-Z_]{1,48}',reason):reason='UNCLASSIFIED'\n                raise ScreenError('LINK_READ_'+reason)\n"
OLDLOOP=("        for attempt in range(3):\n            result=self.clipboard('read',token)","        for attempt in range(5):\n            result=self.clipboard('read',token)")
OLDWAIT=("            if attempt<2:time.sleep(3)\n        raise ScreenError('LINK_READ_'+reason)","            if attempt<4:time.sleep(3)\n        raise ScreenError('LINK_READ_'+reason)")

def patched(text):
 if 'LINK_FIX_V1' in text:return None
 if text.count(CLIP)!=1 or text.count(EXC)!=1:raise RuntimeError('Версия файлов публикации не распознана. Ничего не изменено.')
 text=text.replace(CLIP,WAKE+CLIP).replace(EXC,STOP)
 if text.count(RECOPY)==1:text=text.replace(RECOPY,REREAD)
 for old,new in (OLDLOOP,OLDWAIT):
  if text.count(old)==1:text=text.replace(old,new)
 compile(text,'accessibility_publisher.py','exec');return text
def patch_publisher(text):
 out=text;applied=[]
 if 'LINK_FIX_V1' not in out:
  out=patched(out);applied.append('ссылка')
 if 'LEDGER_V1' not in out:
  A="        if self.payload.get('platform')!='TikTok' or self.payload.get('username')!='@redmaagi':raise ScreenError('UNSUPPORTED_VERIFIED_ACCOUNT')\n"
  B="        self.checkpoint('PUBLISH_INTENT',prior_url)\n"
  if out.count(A)==1 and out.count(B)==1:
   L="        # LEDGER_V1: Mac-side record of every video sent to the account; survives server restarts.\n        ledger=Path.home()/'Downloads/faxclip-telegram-bridge-v14/.faxclip-cloud/published-sha256.txt'\n"
   out=out.replace(A,A+L+"        if ledger.exists() and (self.payload.get('username','')+' '+str(self.payload.get('sha256',''))) in ledger.read_text().split('\\n'):raise ScreenError('DUPLICATE_VIDEO_ALREADY_POSTED')\n")
   out=out.replace(B,B+L+"        with ledger.open('a') as f:f.write(self.payload.get('username','')+' '+str(self.payload.get('sha256',''))+'\\n')\n")
   applied.append('защита от дублей')
 if out==text:return None,applied
 compile(out,'accessibility_publisher.py','exec');return out,applied
def patch_manager(text):
 if 'FIX_ALL_V1' in text:return None,[]
 A="     ready='FRESH_CLIP_TIMESTAMP_V1' in cap and bool(re.search(r'versionName=44\\.6\\.4(?:\\s|$)',package))\n"
 if text.count(A)!=1:return None,[]
 add=A+r"""     if not ready and 'FRESH_CLIP_TIMESTAMP_V1' not in cap:
      # FIX_ALL_V1: Android may silently switch off the FaxClip accessibility service.
      try:
       svc='com.faxclip.access/com.faxclip.access.FaxClipAccessibility'
       cur=adb.shell('settings','get','secure','enabled_accessibility_services').strip()
       if svc not in cur:adb.shell('settings','put','secure','enabled_accessibility_services',svc if cur in ('','null') else cur+':'+svc)
       adb.shell('settings','put','secure','accessibility_enabled','1');time.sleep(4)
       cap=adb.shell('am','broadcast','-a','com.faxclip.access.CONTROL','-p','com.faxclip.access','--es','cmd','verification_capabilities')
       ready='FRESH_CLIP_TIMESTAMP_V1' in cap and bool(re.search(r'versionName=44\.6\.4(?:\s|$)',package))
       if ready:STATUS.emit('a11y-'+serial,'HELPER_REENABLED','Помощник FaxClip был выключен Android и включён автоматически.')
      except Exception:pass
     try:adb.shell('svc','power','stayon','usb')
     except Exception:pass
"""
 out=text.replace(A,add);compile(out,'device_manager.py','exec');return out,['автовключение помощника','экран не гаснет при USB']
def main():
 jobs=[]
 for folder in (ROOT/'bridge',SUPPORT/'bridge'):
  for name,fn in (('accessibility_publisher.py',patch_publisher),('device_manager.py',patch_manager)):
   f=folder/name
   if f.exists():jobs.append((f,fn))
 if not jobs:raise RuntimeError('Файлы FaxClip не найдены в Downloads. Ничего не изменено.')
 changes={};applied=set();already=0
 for f,fn in jobs:
  try:t,what=fn(f.read_text(encoding='utf8'))
  except (RuntimeError,UnicodeDecodeError,SyntaxError):continue
  if t is None:already+=1;continue
  changes[f]=t;applied.update(what)
 if not changes:
  if already:print('Все исправления уже установлены.');return
  raise RuntimeError('Версия файлов не распознана. Ничего не изменено.')
 private=ROOT/'.faxclip-cloud';private.mkdir(mode=0o700,parents=True,exist_ok=True)
 lock=open(private/'device-manager.lock','a');paused=False
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:
  (private/'manager-stop').touch(mode=0o600);paused=True
  print('Жду безопасной паузы менеджера (не прерываю публикацию)…',flush=True)
  end=time.monotonic()+600
  while True:
   try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);break
   except BlockingIOError:
    if time.monotonic()>end:
     (private/'manager-stop').unlink(missing_ok=True);raise RuntimeError('Менеджер занят публикацией. Ничего не изменено; повторите позже.')
    time.sleep(1)
 try:
  stamp=time.strftime('%Y%m%d-%H%M%S')
  for f,t in changes.items():
   shutil.copy2(f,f.with_name(f.name+'.before-fixall-'+stamp))
   tmp=f.with_name(f.name+'.fixall.tmp');tmp.write_text(t,encoding='utf8');os.replace(tmp,f)
  work=private/'work';ledger=private/'published-sha256.txt';seen=set(ledger.read_text().split('\n')) if ledger.exists() else set()
  if work.exists():
   for rec in work.rglob('*-verification*.json'):
    if rec.name.endswith('-verification.json'):(rec.parent/(rec.name[:-len('-verification.json')]+'-recovery-paused')).touch()
    try:
     import json as _j;x=_j.loads(rec.read_text());line='@redmaagi '+str(x.get('sha256',''))
     if len(str(x.get('sha256','')))==64 and line not in seen:
      with ledger.open('a') as fh:fh.write(line+'\n')
      seen.add(line)
    except Exception:pass
  print('ГОТОВО. Установлено:',', '.join(sorted(applied)) or 'ничего нового')
 finally:
  lock.close()
  if paused:(private/'manager-stop').unlink(missing_ok=True)
  subprocess.run(['launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],capture_output=True,text=True,timeout=30)
if __name__=='__main__':
 try:main()
 except Exception as e:print('Остановлено:',str(e) if isinstance(e,RuntimeError) else 'Локальная ошибка; ничего не изменено.');sys.exit(1)
