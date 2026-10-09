"""One-time code enrollment using the installed Mac runtime. No APK/app installation."""
import fcntl,getpass,hashlib,json,os,plistlib,re,subprocess,sys,time
from pathlib import Path
import requests
SERVER='https://verticalos-rxdl.onrender.com'
LABEL='com.faxclip.devices'

def pick_serial(phones,choice=None):
 if len(phones)==1:return phones[0]
 if choice is None:return None
 try:index=int(choice)-1
 except (ValueError,TypeError):return None
 return phones[index] if 0<=index<len(phones) else None

def config_path(root,serial):return root/'.faxclip-cloud/devices'/(hashlib.sha256(serial.encode()).hexdigest()+'.json')

def save_config(file,data):
 file.parent.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(file.parent,0o700);os.chmod(file.parent.parent,0o700)
 tmp=file.with_suffix('.pair.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
 with os.fdopen(fd,'w') as f:json.dump(data,f);f.flush();os.fsync(f.fileno())
 os.replace(tmp,file)

def redeem_code(code,serial,root,post=requests.post):
 clean=code.replace('-','').strip().upper()
 if not re.fullmatch(r'[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{15}',clean):raise RuntimeError('Неверный формат кода. Возьмите код выбранного устройства в Mini App.')
 with post(SERVER+'/api/bridge/pair',json={'code':clean},timeout=30) as r:
  if r.status_code==429:raise RuntimeError('Слишком много попыток подключения. Подождите минуту и повторите команду; данные не изменены.')
  if not r.ok:raise RuntimeError('Код истёк, уже использован или устройство занято. Конфигурация не заменена.')
  data=r.json()
 if data.get('mode')!='ACCESSIBILITY_PUBLISH' or not isinstance(data.get('device_id'),str) or not re.fullmatch(r'[a-f0-9-]{36}',data['device_id']) or not isinstance(data.get('device_token'),str) or not data['device_token']:
  raise RuntimeError('Сервер не подтвердил подключение; данные не сохранены.')
 record={'server':SERVER,'serial':serial,'device_id':data['device_id'],'device_token':data['device_token']}
 save_config(config_path(root,serial),record)
 return record

def managed_agent(root):
 file=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
 try:
  x=plistlib.loads(file.read_bytes());args=x.get('ProgramArguments',[])
  return x.get('Label')==LABEL and len(args)==4 and args[2]=='--root' and Path(args[3])==root and args[1]==str(Path.home()/'Library/Application Support/FaxClip Manager/bridge/device_manager.py')
 except Exception:return False

def pause_manager(root):
 private=root/'.faxclip-cloud';private.mkdir(mode=0o700,parents=True,exist_ok=True)
 lock=open(private/'device-manager.lock','a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);lock.close();return False
 except BlockingIOError:pass
 if not managed_agent(root):raise RuntimeError('Сначала остановите старый ручной менеджер Ctrl+C. Ничего не переподключено.')
 (private/'manager-stop').touch(mode=0o600)
 print('Ожидаю безопасной паузы менеджера, не прерывая текущие публикации…',flush=True)
 end=time.monotonic()+180
 while time.monotonic()<end:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);lock.close();return True
  except BlockingIOError:time.sleep(1)
 lock.close();raise RuntimeError('Менеджер ещё завершает операцию. Принудительное завершение не выполнялось; повторите позже.')

def resume_manager(root):
 if managed_agent(root):
  (root/'.faxclip-cloud/manager-stop').unlink(missing_ok=True)
  subprocess.run(['launchctl','kickstart',f'gui/{os.getuid()}/{LABEL}'],capture_output=True,text=True,timeout=30)

def code_mode_source(raw):
 # Any FaxClip manager build reads saved connections from .faxclip-cloud/devices.
 # Disabling legacy file import is an optional hardening step: apply it only when the
 # exact anchor is present once; otherwise keep the installed manager untouched.
 text=raw.decode('utf-8',errors='strict')
 if 'PAIR_BY_CODE_ONLY' in text:return raw
 anchor="for file in downloads.glob('faxclip-connect-*.json'):"
 if text.count(anchor)!=1 or "configs.glob('*.json')" not in text:return raw
 text=text.replace(anchor,'for file in []: # PAIR_BY_CODE_ONLY: preserve credentials, disable legacy file enrollment')
 text=text.replace('файлы подключения сохраняйте в Downloads этого Mac.','первичное подключение выполняйте по коду в Устройствах.')
 text=text.replace('В Устройствах скачайте новый файл подключения; код вводить не нужно.','В Устройствах получите код восстановления и введите его один раз.')
 try:compile(text,'device_manager.py','exec')
 except SyntaxError:return raw
 return text.encode()

def enable_code_mode(root):
 if not managed_agent(root):return
 file=Path.home()/'Library/Application Support/FaxClip Manager/bridge/device_manager.py'
 old=file.read_bytes()
 try:new=code_mode_source(old)
 except UnicodeDecodeError:return
 if new==old:return
 backup=root/'.faxclip-cloud/manager-before-code.py'
 if not backup.exists():backup.write_bytes(old);backup.chmod(0o600)
 tmp=file.with_suffix('.code.tmp');tmp.write_bytes(new);tmp.chmod(0o600);os.replace(tmp,file)

def main():
 root=Path.home()/'Downloads/faxclip-telegram-bridge-v14'
 adb=root/'platform-tools/adb'
 if not adb.exists():raise RuntimeError('Основная папка существующего моста не найдена. Ничего не установлено.')
 listing=subprocess.run([str(adb),'devices'],capture_output=True,text=True,timeout=30,check=True).stdout
 phones=[x.split()[0] for x in listing.splitlines()[1:] if len(x.split())>1 and x.split()[1]=='device' and re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',x.split()[0])]
 if not phones:raise RuntimeError('Нет авторизованного USB-телефона. Подключите телефон и подтвердите USB-отладку при первом подключении.')
 choice=None
 if len(phones)>1:
  print('Подключено несколько телефонов. Выберите именно тот, который добавляете:')
  for i,serial in enumerate(phones,1):print(i,serial)
  choice=input('Номер телефона: ')
 serial=pick_serial(phones,choice)
 if serial is None:raise RuntimeError('Телефон не выбран. Между устройствами случайный выбор не выполняется.')
 file=config_path(root,serial);paused=pause_manager(root);phone_lock=None
 try:
  enable_code_mode(root)
  locks=Path.home()/'.faxclip-phone-locks';locks.mkdir(mode=0o700,exist_ok=True)
  phone_lock=open(locks/(hashlib.sha256(serial.encode()).hexdigest()+'.lock'),'a')
  try:fcntl.flock(phone_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:raise RuntimeError('Старый мост 02/03 ещё использует этот телефон. Остановите его Ctrl+C; код не использован.')
  if file.exists():
   saved=json.loads(file.read_text())
   if saved.get('server')!=SERVER:raise RuntimeError('Сохранённая конфигурация принадлежит другому серверу. Она не изменена.')
   h={'X-Device-ID':saved['device_id'],'Authorization':'Bearer '+saved['device_token']}
   with requests.get(SERVER+'/api/bridge/jobs/00000000-0000-0000-0000-000000000000/media',headers=h,timeout=30) as r:
    if r.status_code==409:
     print('Этот телефон уже сохранён и данные доступа действуют. Повторный код не нужен. Старый импорт файлов отключён; просто подключайте USB.');return
    if r.status_code not in (401,403):raise RuntimeError('Не удалось проверить существующее подключение. Его данные не заменены.')
  print('В Mini App: Устройства → Подключить по коду. Код не отправляйте в чат.')
  code=getpass.getpass('Одноразовый код выбранного устройства (ввод скрыт): ')
  redeem_code(code,serial,root)
  print('Устройство сохранено. Дальше повторно вводить код при обычном подключении USB не нужно.')
  if managed_agent(root):print('Подключение передано уже установленному фоновому менеджеру.')
  else:print('Подключение сохранено, но автозапуск менеджера не обнаружен. Нужен запуск ранее установленного менеджера.')
 finally:
  if phone_lock:phone_lock.close()
  if paused or managed_agent(root):resume_manager(root)
if __name__=='__main__':
 try:main()
 except Exception as e:
  print('Остановлено:',str(e) if isinstance(e,RuntimeError) else 'Локальная или сетевая ошибка. Коды и токены не выведены.')
  sys.exit(1)
