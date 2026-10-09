"""FaxClip: one-time fix for reading the copied TikTok link. Pauses the manager safely, patches, resumes."""
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
def targets():
 out=[]
 for folder in (ROOT/'bridge',SUPPORT/'bridge'):
  f=folder/'accessibility_publisher.py'
  if f.exists():out.append(f)
 return out
def patched(text):
 if 'LINK_FIX_V1' in text:return None
 if text.count(CLIP)!=1 or text.count(EXC)!=1:raise RuntimeError('Версия файлов публикации не распознана. Ничего не изменено.')
 text=text.replace(CLIP,WAKE+CLIP).replace(EXC,STOP)
 if text.count(RECOPY)==1:text=text.replace(RECOPY,REREAD)
 for old,new in (OLDLOOP,OLDWAIT):
  if text.count(old)==1:text=text.replace(old,new)
 compile(text,'accessibility_publisher.py','exec');return text
def main():
 files=targets()
 if not files:raise RuntimeError('Файлы FaxClip не найдены в Downloads. Ничего не изменено.')
 changes={};done=0;unknown=0
 for f in files:
  try:t=patched(f.read_text(encoding='utf8'))
  except (RuntimeError,UnicodeDecodeError,SyntaxError):unknown+=1;continue
  if t is None:done+=1
  else:changes[f]=t
 if not changes:
  if done:print('Исправление уже установлено. Ничего делать не нужно.');return
  raise RuntimeError('Версия файлов публикации не распознана. Ничего не изменено.')
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
   shutil.copy2(f,f.with_name(f.name+'.before-linkfix-'+stamp))
   tmp=f.with_name(f.name+'.linkfix.tmp');tmp.write_text(t,encoding='utf8');os.replace(tmp,f)
  # Stop endless re-verification of already submitted jobs: owner confirms them with the link.
  work=private/'work'
  if work.exists():
   for rec in work.rglob('*-verification.json'):
    (rec.parent/(rec.name[:-len('-verification.json')]+'-recovery-paused')).touch()
  print('ГОТОВО: чтение ссылки исправлено, повторных заходов в видео больше не будет.')
 finally:
  lock.close()
  if paused:(private/'manager-stop').unlink(missing_ok=True)
  subprocess.run(['launchctl','kickstart','-k',f'gui/{os.getuid()}/{LABEL}'],capture_output=True,text=True,timeout=30)
if __name__=='__main__':
 try:main()
 except Exception as e:print('Остановлено:',str(e) if isinstance(e,RuntimeError) else 'Локальная ошибка; ничего не изменено.');sys.exit(1)
