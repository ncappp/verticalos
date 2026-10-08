import json,os,plistlib,re,subprocess,sys,time,fcntl
from pathlib import Path
ROOT=Path.home()/'Downloads/faxclip-telegram-bridge-v14';HERE=Path(__file__).resolve().parent
LABEL='com.faxclip.devices';SUPPORT=Path.home()/'Library/Application Support/FaxClip Manager'
def launch(action,*args):return subprocess.run(['launchctl',action,*args],capture_output=True,text=True,timeout=30)
def owned_agent():
 agent=Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist'
 try:
  data=plistlib.loads(agent.read_bytes());args=data.get('ProgramArguments',[])
  return agent if data.get('Label')==LABEL and args==[str(ROOT/'.venv/bin/python'),str(SUPPORT/'bridge/device_manager.py'),'--root',str(ROOT)] else None
 except Exception:return None

def pause():
 lock=open(ROOT/'.faxclip-cloud/device-manager.lock','a')
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);lock.close();return False
 except BlockingIOError:pass
 agent=owned_agent()
 if not agent:lock.close();raise RuntimeError('Остановите старый ручной мост Ctrl+C. Обновление не устанавливалось.')
 manager=(SUPPORT/'bridge/device_manager.py').read_text()
 if 'manager-stop' not in manager:lock.close();raise RuntimeError('Установленный менеджер не поддерживает безопасную паузу. Обновление не устанавливалось.')
 (ROOT/'.faxclip-cloud/manager-stop').touch(mode=0o600)
 print('Ожидаю завершения текущей операции менеджера. Принудительного завершения не будет.',flush=True)
 end=time.monotonic()+300
 while time.monotonic()<end:
  try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);lock.close();return True
  except BlockingIOError:time.sleep(1)
 lock.close();(ROOT/'.faxclip-cloud/manager-stop').unlink(missing_ok=True);raise RuntimeError('Текущая операция ещё не завершена. Файлы не заменены, запрос остановки снят.')

def resume():
 agent=owned_agent()
 if not agent:raise RuntimeError('Автозапуск не подтверждён: конфигурация службы не найдена.')
 (ROOT/'.faxclip-cloud/manager-stop').unlink(missing_ok=True)
 domain=f'gui/{os.getuid()}';target=domain+'/'+LABEL
 if launch('enable',target).returncode:raise RuntimeError('macOS не включила службу менеджера.')
 if launch('print',target).returncode:
  if launch('bootstrap',domain,str(agent)).returncode and launch('print',target).returncode:raise RuntimeError('macOS не зарегистрировала службу менеджера.')
 if launch('kickstart',target).returncode:raise RuntimeError('macOS не подтвердила запуск менеджера.')
 for _ in range(10):
  p=launch('print',target)
  if p.returncode==0 and re.search(r'(?m)^\s*pid\s*=\s*[1-9][0-9]*\s*$',p.stdout):
   time.sleep(3);stable=launch('print',target)
   if stable.returncode==0 and re.search(r'(?m)^\s*pid\s*=\s*[1-9][0-9]*\s*$',stable.stdout):print('МЕНЕДЖЕР ЗАПУЩЕН.');return
  time.sleep(1)
 raise RuntimeError('Работающий процесс менеджера не подтверждён. Данные подключений сохранены.')



def main():
 if sys.platform!='darwin':raise RuntimeError('Файл предназначен для Mac.')
 sys.path.insert(0,str(SUPPORT/'bridge'))
 from manager_runtime import request
 SERVER='https://verticalos-rxdl.onrender.com'
 with request('GET',SERVER+'/api/health',timeout=60) as r:
  if not r.ok or r.json().get('queue_cleanup')!=1:raise RuntimeError('Обновление сервера ещё не подтверждено. Менеджер остаётся на паузе.')
 files=list((ROOT/'.faxclip-cloud/devices').glob('*.json'))
 if not files:raise RuntimeError('Сохранённых подключений нет. Менеджер остаётся на паузе.')
 for file in files:
  x=json.loads(file.read_text())
  if x.get('server')!=SERVER:raise RuntimeError('Неожиданный сервер в подключении.')
  h={'X-Device-ID':x['device_id'],'Authorization':'Bearer '+x['device_token']}
  with request('GET',SERVER+'/api/bridge/queue-state',headers=h,timeout=60) as r:
   if r.status_code in (401,403):raise RuntimeError('Сервер не принял старое подключение. Менеджер остаётся на паузе; не получайте новый код до проверки сохранённой копии.')
   if not r.ok:raise RuntimeError('Не удалось проверить пустую очередь. Менеджер остаётся на паузе.')
   if r.json().get('queue_empty') is not True:raise RuntimeError('Старая очередь ещё не пуста. В Mini App → Публикации нажмите «Очистить очередь и историю». Затем повторите этот запуск.')
 print('СТАРЫЕ ПОДКЛЮЧЕНИЯ ПРИНЯТЫ. ОЧЕРЕДЬ ПУСТА.')
 resume()
 print('Можно отправлять НОВОЕ видео через Mini App → Публикации.')
if __name__=='__main__':
 try:main()
 except KeyboardInterrupt:print('Отменено.');sys.exit(1)
 except Exception as e:print('ОСТАНОВЛЕНО:',str(e) if isinstance(e,RuntimeError) else 'Сеть или локальная ошибка. Менеджер остаётся на паузе.');sys.exit(1)
