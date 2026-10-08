"""Prepare an enrollment-only cold-start seed. Does not contact the server or alter registration."""
import base64,fcntl,hashlib,json,os,plistlib,re,subprocess,sys,time,uuid
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



SERVER='https://verticalos-rxdl.onrender.com'
def write_private(path,body):
 fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 with os.fdopen(fd,'w') as f:f.write(body)

def main():
 if sys.platform!='darwin':raise RuntimeError('Файл предназначен для Mac.')
 if not owned_agent():raise RuntimeError('Ожидаемый менеджер не найден. Ничего не остановлено.')
 source=Path.home()/'Downloads/faxclip-before-deploy.json'
 if source.exists():
  try:old=json.loads(source.read_text())
  except Exception:raise RuntimeError('В Downloads уже есть файл с ожидаемым именем, но неизвестного формата. Сохраните его отдельно перед продолжением.')
  if old.get('format')!='FAXCLIP_PREDEPLOY_METADATA_V1':raise RuntimeError('В Downloads уже есть файл неизвестного формата. Ничего не перемещено.')
  archive=source.with_name('faxclip-before-deploy.previous-'+time.strftime('%Y%m%d-%H%M%S')+'.json')
  if archive.exists():raise RuntimeError('Предыдущая копия уже существует. Повторите через минуту.')
  source.chmod(0o600);source.rename(archive)
 print('ПОДГОТОВКА СОХРАНЕНИЯ ПОДКЛЮЧЕНИЯ ПЕРЕД DEPLOY. Не тест телефона.')
 print('Сначала сохраните метаданные из уже открытого FaxClip Mini App:')
 print('1. Откройте инструменты разработчика для страницы FaxClip и вкладку Console.')
 print('2. Код экспорта сейчас копируется в буфер. Вставьте его в Console и выполните.')
 print('3. Сохраните скачанный faxclip-before-deploy.json в Downloads этого Mac.')
 subprocess.run(['pbcopy'],input=(HERE/'export-before-deploy.js').read_bytes(),check=True)
 input('Когда файл сохранён в Downloads, нажмите Enter здесь: ')
 source=Path.home()/'Downloads/faxclip-before-deploy.json'
 if not source.exists():raise RuntimeError('Файл метаданных не найден. Deploy не запускайте.')
 if source.stat().st_size>5*1024*1024:raise RuntimeError('Файл метаданных слишком большой.')
 source.chmod(0o600)
 data=json.loads(source.read_text())
 if data.get('format')!='FAXCLIP_PREDEPLOY_METADATA_V1' or data.get('server')!=SERVER:raise RuntimeError('Это не ожидаемая резервная копия FaxClip.')
 configs={}
 for p in (ROOT/'.faxclip-cloud/devices').glob('*.json'):
  x=json.loads(p.read_text())
  if x.get('server')!=SERVER or not re.fullmatch(r'[a-f0-9-]{36}',x.get('device_id','')) or not isinstance(x.get('device_token'),str):raise RuntimeError('Не удалось подтвердить сохранённое подключение.')
  configs[x['device_id']]=x
 if not configs:raise RuntimeError('Нет сохранённых подключений. Deploy не запускайте.')
 sys.path.insert(0,str(SUPPORT/'bridge'))
 from manager_runtime import request
 for x in configs.values():
  h={'X-Device-ID':x['device_id'],'Authorization':'Bearer '+x['device_token']}
  with request('GET',SERVER+'/api/bridge/jobs/00000000-0000-0000-0000-000000000000/media',headers=h,timeout=60) as r:
   if r.status_code!=409:raise RuntimeError('Старое подключение не принято сервером. Ничего не заменено; Deploy не запускайте.')

 columns={
 'devices':['id','name','model','connection','status','battery','token_hash','last_seen','created_at'],
 'accounts':['id','platform','username','niche','audience','status','device_id','created_at'],
 'tasks':['id','title','target','done','unit','deadline','created_at','period','period_start','period_end']}
 devices=[]
 for original in data['devices']:
  if original.get('status')=='REVOKED':continue
  if original['id'] not in configs:raise RuntimeError('Не для каждого устройства найдены локальные данные подключения. Автоматическое сохранение отменено; Deploy не запускайте.')
  row={k:original[k] for k in columns['devices'] if k in original}
  row['token_hash']=hashlib.sha256(configs[row['id']]['device_token'].encode()).hexdigest()
  row.update(status='PENDING',battery=0,last_seen=None);devices.append(row)
 if set(configs)-{x['id'] for x in devices}:raise RuntimeError('Локальные подключения и серверный список не совпали. Deploy не запускайте.')
 tables={'devices':devices,'accounts':[{k:v for k,v in a.items() if k in columns['accounts']} for a in data['accounts']],
 'tasks':[{k:v for k,v in a.items() if k in columns['tasks']} for a in data['tasks']],
 'ui_account_media_guard':[],'ui_media_guard':[]}
 valid={x['id'] for x in devices}
 if any(a.get('device_id') and a['device_id'] not in valid for a in tables['accounts']):raise RuntimeError('Нельзя сохранить привязку одного из аккаунтов. Deploy не запускайте.')
 # Carry local fingerprints for previously submitted videos, without re-creating jobs.
 fingerprints=set()
 work=ROOT/'.faxclip-cloud/work'
 for path in work.rglob('*verification*.json'):
  try:
   x=json.loads(path.read_text());sha=x.get('sha256');did=path.parent.name
   if did not in valid or not re.fullmatch(r'[a-f0-9]{64}',sha or ''):continue
   candidates=[a for a in tables['accounts'] if a.get('device_id')==did and a.get('platform')=='TikTok' and a.get('username')=='@redmaagi']
   if len(candidates)==1:fingerprints.add((did,'TikTok',candidates[0]['username'],sha))
  except Exception:continue
 # Older successful files might precede verification journals. Hash locally retained originals
 # only when their exact title is present in the authorized export.
 clips={c['id']:c for c in data.get('clips',[])}
 accounts={a['id']:a for a in tables['accounts']}
 for p in data.get('publications',[]):
  if p.get('status')=='QUEUED':continue
  clip=clips.get(p.get('clip_id'));account=accounts.get(p.get('account_id'))
  if not clip or not account:continue
  name=clip.get('title','')
  if not name or Path(name).name!=name or not name.lower().endswith('.mp4'):continue
  file=Path.home()/'Downloads'/name
  if file.is_file():
   h=hashlib.sha256()
   with file.open('rb') as f:
    for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
   fingerprints.add((account['device_id'],account['platform'],account['username'],h.hexdigest()))
 account_guard_keys=set()
 for did,platform,username,sha in sorted(fingerprints):
  pid=str(uuid.uuid5(uuid.NAMESPACE_URL,'faxclip-deleted-attempt:'+did+username+sha))
  if (platform,username,sha) not in account_guard_keys:
   tables['ui_account_media_guard'].append(dict(platform=platform,username=username,sha256=sha,publication_id=pid));account_guard_keys.add((platform,username,sha))
  tables['ui_media_guard'].append(dict(device_id=did,username=username,sha256=sha,publication_id=pid))
 backup={'format':'FAXCLIP_ENROLLMENT_BACKUP_V1','created_at':data['created_at'],'tables':tables}
 encoded=base64.b64encode(json.dumps(backup,ensure_ascii=False,separators=(',',':')).encode()).decode()
 if len(encoded)>2*1024*1024:raise RuntimeError('Резервная копия слишком большая для этого способа.')
 directory=ROOT/'.faxclip-upgrade-backups'/('predeploy-'+time.strftime('%Y%m%d-%H%M%S'));directory.mkdir(mode=0o700,parents=True,exist_ok=False)
 write_private(directory/'enrollment.json',json.dumps(backup,ensure_ascii=False))
 write_private(directory/'FAXCLIP_BOOTSTRAP_B64.txt',encoded)
 # Stop queue consumption safely. Keep the manager paused until the owner restores and confirms.
 try:paused=pause()
 except BaseException:
  (ROOT/'.faxclip-cloud/manager-stop').unlink(missing_ok=True);raise
 (ROOT/'.faxclip-cloud/manager-stop').touch(mode=0o600)
 # Remove nothing yet. Journal files remain in the private directory and cannot auto-publish.
 subprocess.run(['pbcopy'],input=encoded.encode(),check=True)
 print('КОПИЯ ПОДГОТОВЛЕНА. МЕНЕДЖЕР НА ПАУЗЕ.')
 print('Устройств:',len(devices),'Аккаунтов:',len(tables['accounts']),'Локальных отпечатков старых видео:',len(fingerprints))
 print('В Render → Environment добавьте FAXCLIP_BOOTSTRAP_B64; значение уже в буфере обмена. Не присылайте его в чат.')
 print('Используйте Save only, если доступно. Deploy допускается только после сохранения этого значения.')
 print('После обновления используйте 21-resume-after-deploy.command. Он проверит, что сервер принимает старые подключения, и снимет паузу.')
 print('Важно: старые публикации, очередь, видео и скриншоты не входят в восстановление. Полный список старых отпечатков на старом сервере недоступен; повторно загружайте только НОВЫЕ видео.')
if __name__=='__main__':
 try:main()
 except KeyboardInterrupt:print('Отменено. Deploy не запускайте, пока резервная копия не готова.');sys.exit(1)
 except Exception as e:print('ОСТАНОВЛЕНО:',str(e) if isinstance(e,RuntimeError) else 'Не удалось подготовить копию. Deploy не запускайте.');sys.exit(1)
