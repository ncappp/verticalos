"""FaxClip: one-time fix for reading the copied TikTok link. Pauses the manager safely, patches, resumes."""
import fcntl,os,shutil,subprocess,sys,time
from pathlib import Path
ROOT=Path.home()/'Downloads/faxclip-telegram-bridge-v14'
SUPPORT=Path.home()/'Library/Application Support/FaxClip Manager'
LABEL='com.faxclip.devices'
PATCHES=[
 ("        self.adb.shell('am','start','-W','-n','com.faxclip.access/.VerificationClipboardActivity','--es','mode',mode,'--es','token',token)\n        time.sleep(2)\n",
  "        # LINK_FIX_V1: Android lets only a focused, awake app read the clipboard.\n        try:self.adb.shell('input','keyevent','KEYCODE_WAKEUP');self.adb.shell('wm','dismiss-keyguard')\n        except Exception:pass\n        time.sleep(1)\n        self.adb.shell('am','start','-W','-n','com.faxclip.access/.VerificationClipboardActivity','--es','mode',mode,'--es','token',token)\n        time.sleep(3)\n"),
 ("        for attempt in range(3):\n            result=self.clipboard('read',token)",
  "        for attempt in range(5):\n            result=self.clipboard('read',token)"),
 ("            if attempt<2:time.sleep(3)\n        raise ScreenError('LINK_READ_'+reason)",
  "            if attempt<4:time.sleep(3)\n        raise ScreenError('LINK_READ_'+reason)"),
 ("                code=str(exc)\n                retryable=code.startswith('LINK_READ_')",
  "                code=str(exc)\n                if code.startswith('LINK_READ_'):\n                    # The link was already copied once: never revisit or recopy. Owner adds the link in Mini App.\n                    try:(self.b.work/(self.job['id']+'-recovery-paused')).touch()\n                    except Exception:pass\n                    raise ScreenError('LINK_COPIED_NOT_READ')\n                retryable=code.startswith('LINK_READ_')"),
]
def targets():
 out=[]
 for folder in (ROOT/'bridge',SUPPORT/'bridge'):
  f=folder/'accessibility_publisher.py'
  if f.exists():out.append(f)
 return out
def patched(text):
 if 'LINK_FIX_V1' in text:return None
 for old,new in PATCHES:
  if text.count(old)!=1:raise RuntimeError('Версия файлов публикации не распознана. Ничего не изменено.')
  text=text.replace(old,new)
 compile(text,'accessibility_publisher.py','exec');return text
def main():
 files=targets()
 if not files:raise RuntimeError('Файлы FaxClip не найдены в Downloads. Ничего не изменено.')
 changes={}
 for f in files:
  t=patched(f.read_text(encoding='utf8'))
  if t is not None:changes[f]=t
 if not changes:print('Исправление уже установлено. Ничего делать не нужно.');return
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
