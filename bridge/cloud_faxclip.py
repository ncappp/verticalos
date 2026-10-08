#!/usr/bin/env python3
"""USB bridge only. User uploads videos in the deployed Telegram Mini App."""
import argparse,getpass,json,os,sys
from pathlib import Path
import requests
from pc_bridge import Bridge
from adb_control import ADB
SERVER='https://verticalos-rxdl.onrender.com'

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--serial',required=True);parser.add_argument('--adb',required=True);parser.add_argument('--private-dir',required=True);parser.add_argument('--pair-again',action='store_true');args=parser.parse_args()
 adb=ADB(args.serial,args.adb);adb.ready()
 status=adb.shell('am','broadcast','-a','com.faxclip.access.CONTROL','-p','com.faxclip.access','--es','cmd','status')
 if 'SERVICE_CONNECTED_V14' not in status:raise SystemExit('Установите APK v14 поверх v13 и включите службу FaxClip. Отправка не началась.')
 capability=adb.shell('am','broadcast','-a','com.faxclip.access.CONTROL','-p','com.faxclip.access','--es','cmd','verification_capabilities')
 if 'FRESH_CLIP_TIMESTAMP_V1' not in capability:raise SystemExit('Нужен обновлённый APK автоматической проверки. Ничего не отправлено.')
 with requests.get(SERVER+'/api/health',timeout=60) as r:
  if not r.ok or r.json().get('verification_recovery')!=1:raise SystemExit('Сервер ещё не обновлён. Мост не запущен.')
 data=Path(args.private_dir);data.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(data,0o700)
 config=data/'cloud-device.json'
 if not config.exists() or args.pair_again:
  print('В Telegram FaxClip откройте «Устройства» → подключение выбранного устройства. Код нужно ввести здесь, не отправлять в чат.')
  code=getpass.getpass('Одноразовый код из Mini App (ввод скрыт): ').strip()
  with requests.post(SERVER+'/api/bridge/pair',json={'code':code},timeout=60) as r:
   if not r.ok:raise SystemExit('Подключение не завершено: код просрочен/использован, телефон занят или сервер ещё не обновлён. Получите новый код в Mini App.')
   paired=r.json()
  if paired.get('mode')!='ACCESSIBILITY_PUBLISH':raise SystemExit('Ответ подключения не соответствует поддерживаемому профилю.')
  fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
  with os.fdopen(fd,'w') as f:json.dump({'server':SERVER,'device_id':paired['device_id'],'device_token':paired['device_token']},f)
 os.chmod(config,0o600);settings=json.loads(config.read_text())
 if settings.get('server')!=SERVER:raise SystemExit('Конфигурация принадлежит другому серверу.')
 with requests.get(SERVER+'/api/bridge/jobs/00000000-0000-0000-0000-000000000000/media',headers={'X-Device-ID':settings['device_id'],'Authorization':'Bearer '+settings['device_token']},timeout=60) as r:
  if r.status_code in (401,403):raise SystemExit('Сохранённое подключение отклонено. Запустите 03-reconnect и получите новый код в Mini App. Задачи не повторялись.')
  if r.status_code!=409:raise SystemExit('Не удалось подтвердить серверное подключение. Мост не запущен.')
 print('FaxClip: мост слушает очередь Telegram. Здесь загружать видео не нужно. Оставьте Mac включённым, терминал открытым, телефон подключённым и разблокированным.')
 bridge=Bridge(SERVER,{'serial':args.serial,'adb':args.adb,**settings,'mode':'ACCESSIBILITY_PUBLISH'},data/'work')
 try:bridge.run()
 except KeyboardInterrupt:print('Мост остановлен. Неопределённые публикации автоматически не повторяются.')
if __name__=='__main__':main()
