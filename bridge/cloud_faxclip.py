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
 data=Path(args.private_dir);data.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(data,0o700)
 config=data/'cloud-device.json'
 if not config.exists() or args.pair_again:
  print('В Telegram FaxClip откройте «Публикации» → «Подключить Mac». Код нужно ввести здесь, не отправлять в чат.')
  code=getpass.getpass('Одноразовый код из Mini App (ввод скрыт): ').strip()
  with requests.post(SERVER+'/api/bridge/pair',json={'code':code},timeout=60) as r:
   if not r.ok:raise SystemExit('Подключение не завершено: код просрочен/использован, телефон занят или сервер ещё не обновлён. Получите новый код в Mini App.')
   paired=r.json()
  if paired.get('mode')!='ACCESSIBILITY_PUBLISH' or paired.get('account')!='@redmaagi':raise SystemExit('Ответ подключения не соответствует поддерживаемому профилю.')
  fd=os.open(config,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
  with os.fdopen(fd,'w') as f:json.dump({'server':SERVER,'device_id':paired['device_id'],'device_token':paired['device_token']},f)
 os.chmod(config,0o600);settings=json.loads(config.read_text())
 if settings.get('server')!=SERVER:raise SystemExit('Конфигурация принадлежит другому серверу.')
 print('FaxClip: мост слушает очередь Telegram. Здесь загружать видео не нужно. Оставьте Mac включённым, терминал открытым, телефон подключённым и разблокированным.')
 bridge=Bridge(SERVER,{'serial':args.serial,'adb':args.adb,**settings,'mode':'ACCESSIBILITY_PUBLISH'},data/'work')
 try:bridge.run()
 except KeyboardInterrupt:print('Мост остановлен. Неопределённые публикации автоматически не повторяются.')
if __name__=='__main__':main()
