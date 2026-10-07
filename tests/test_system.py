import sys,os,tempfile,json,time,unittest,hmac,hashlib,uuid,base64
from urllib.parse import urlencode
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path[:0]=[str(ROOT),str(ROOT/'bridge')]
os.environ['VERTICALOS_DATA_DIR']=tempfile.mkdtemp(prefix='faxclip-adb-tests-')
os.environ['TELEGRAM_BOT_TOKEN']='TEST_NOT_A_REAL_TOKEN';os.environ['TELEGRAM_ALLOWED_USER_IDS']='8784706094';os.environ['ALLOW_DEV_AUTH']='0'
from app import app,init_db,conn,UPLOAD
from adb_control import ADB,ScreenError,parse_nodes,matches
init_db();client=app.test_client()
values={'auth_date':str(int(time.time())),'user':json.dumps({'id':8784706094})}
key=hmac.new(b'WebAppData',os.environ['TELEGRAM_BOT_TOKEN'].encode(),hashlib.sha256).digest()
values['hash']=hmac.new(key,'\n'.join(f'{k}={values[k]}' for k in sorted(values)).encode(),hashlib.sha256).hexdigest()
owner={'X-Telegram-Init-Data':urlencode(values)}

def request(path,method='GET',body=None,headers=None):return client.open('/api'+path,method=method,json=body,headers=headers or owner)
def device():
 r=request('/devices','POST',{'name':'Test phone','model':'Android','connection':'USB / ADB'});assert r.status_code==200
 d=r.get_json();return d,{'X-Device-ID':d['id'],'Authorization':'Bearer '+d['device_token']}
def new_job(d):
 a=request('/accounts','POST',{'platform':'TikTok','username':'@redmaagi','device_id':d['id']}).get_json()
 filename=uuid.uuid4().hex+'.mp4';Path(UPLOAD,filename).write_bytes(b'TEST_MEDIA_BYTES_NOT_REAL_VIDEO')
 clip=request('/clips','POST',{'title':'caption','source_file':filename}).get_json()
 headers={**owner,'Idempotency-Key':str(uuid.uuid4())};body={'account_id':a['id'],'clip_id':clip['id'],'caption':'caption','confirmed':True,'rights_confirmed':True}
 r=request('/publications','POST',body,headers);assert r.status_code==200,r.get_json()
 return r.get_json(),body,headers

class ProtocolTests(unittest.TestCase):
 def test_01_auth_required(self):
  self.assertEqual(client.get('/api/accounts').status_code,401)
  self.assertEqual(client.post('/api/bridge/heartbeat',json={}).status_code,401)
 def test_02_heartbeat_and_scope(self):
  d,h=device();self.assertEqual(request('/bridge/heartbeat','POST',{'battery':80},h).status_code,200)
  self.assertEqual(request('/accounts',headers=h).status_code,401)
 def test_03_queue_idempotency(self):
  d,h=device();pub,body,key=new_job(d);again=request('/publications','POST',body,key).get_json();self.assertEqual(pub['id'],again['id']);self.assertTrue(again['existing'])
 def test_04_claim_and_lease(self):
  d,h=device();new_job(d);job=request('/bridge/claim','POST',{},h).get_json()['job'];self.assertIsNotNone(job)
  self.assertIsNone(request('/bridge/claim','POST',{},h).get_json()['job'])
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/renew','POST',{},h).status_code,409)
  leased={**h,'X-Job-Lease':job['lease']};self.assertEqual(request('/bridge/jobs/'+job['id']+'/renew','POST',{},leased).status_code,200)
  media=request('/bridge/jobs/'+job['id']+'/media',headers=leased);self.assertEqual(media.data,b'TEST_MEDIA_BYTES_NOT_REAL_VIDEO');media.close()
  other,oh=device();oh['X-Job-Lease']=job['lease'];self.assertEqual(request('/bridge/jobs/'+job['id']+'/media',headers=oh).status_code,409)
 def test_05_no_fake_completion(self):
  d,h=device();new_job(d);job=request('/bridge/claim','POST',{},h).get_json()['job'];h={**h,'X-Job-Lease':job['lease']}
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/complete','POST',{},h).status_code,409)
 def test_06_completion_once(self):
  d,h=device();pub,_,_=new_job(d);job=request('/bridge/claim','POST',{},h).get_json()['job'];h={**h,'X-Job-Lease':job['lease']}
  for phase in ('PUBLISH_STARTED','SUBMITTED','UI_CONFIRMED'):self.assertEqual(request('/bridge/jobs/'+job['id']+'/phase','POST',{'phase':phase},h).status_code,200)
  r=client.post('/api/bridge/jobs/'+job['id']+'/evidence',data=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII='),headers=h);self.assertEqual(r.status_code,200)
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/complete','POST',dict(post_url='https://www.tiktok.com/@redmaagi/video/1234567890123456789',verification='PROFILE_MATCHING_POST_REOPENED_TWICE_BY_URL',sha256=job['payload']['sha256'],caption=job['payload']['caption']),h).status_code,200)
  self.assertTrue(request('/bridge/jobs/'+job['id']+'/complete','POST',{},h).get_json()['existing'])
  rows=request('/publications').get_json();self.assertEqual(next(x for x in rows if x['id']==pub['id'])['status'],'UI_CONFIRMED')
 def test_07_expiry_no_repost(self):
  d,h=device();pub,_,_=new_job(d);job=request('/bridge/claim','POST',{},h).get_json()['job']
  with conn() as c:c.execute('update ui_jobs set lease_until=? where id=?',(time.time()-1,job['id']))
  self.assertIsNone(request('/bridge/claim','POST',{},h).get_json()['job'])
  with conn() as c:self.assertEqual(c.execute('select status from ui_jobs where id=?',(job['id'],)).fetchone()['status'],'NEEDS_REVIEW')
 def test_08_revoke(self):
  d,h=device();self.assertEqual(request('/devices/'+d['id']+'/revoke','POST',{}).status_code,200)
  self.assertEqual(request('/bridge/heartbeat','POST',{},h).status_code,401)
 def test_09_media_private(self):self.assertEqual(client.get('/uploads/test.mp4').status_code,403)
 def test_10_manual_status_disabled(self):self.assertEqual(request('/publications/test/status','POST',{'status':'PUBLISHED'}).status_code,410)

 def test_16_prepared_is_not_published(self):
  d,h=device();pub,_,_=new_job(d);job=request('/bridge/claim','POST',{},h).get_json()['job'];h={**h,'X-Job-Lease':job['lease']}
  png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=')
  client.post('/api/bridge/jobs/'+job['id']+'/evidence',data=png,headers=h)
  r=request('/bridge/jobs/'+job['id']+'/prepared','POST',{'sha256':'0'*64,'remote_filename':'faxclip-'+job['id']+'.mp4'},h)
  self.assertEqual(r.status_code,200)
  with conn() as c:
   row=c.execute('select * from publications where id=?',(pub['id'],)).fetchone()
   self.assertEqual(row['status'],'TRANSFERRED_NEEDS_AUTOMATION');self.assertIsNone(row['published_at']);self.assertIsNone(row['external_id'])
  self.assertIsNone(request('/bridge/claim','POST',{},h).get_json()['job'])

class UITests(unittest.TestCase):
 XML='<hierarchy><node text="Next" resource-id="app:id/next" package="app" enabled="true" bounds="[10,20][110,80]" /></hierarchy>'
 def test_11_bounds_and_exact_match(self):
  node=parse_nodes(self.XML)[0];self.assertEqual(node['center'],(60,50));self.assertTrue(matches(node,{'id':'app:id/next','text':'Next'}));self.assertFalse(matches(node,{'text':'Publish'}))
 def test_12_duplicate_selector_stops(self):
  adb=ADB('TESTSERIAL');xml=self.XML.replace('</hierarchy>',self.XML.split('<hierarchy>')[1])
  with patch.object(adb,'tree',return_value=xml):
   with self.assertRaises(ScreenError):adb.find({'text':'Next'},timeout=1)
 def test_13_shell_quoting(self):
  adb=ADB('TESTSERIAL')
  with patch.object(adb,'run',return_value='ok') as run:
   adb.shell('content','query','--where',"_display_name='a b.mp4'")
   args=run.call_args.args;self.assertNotEqual(args[-1],"_display_name='a b.mp4'")
 def test_14_invalid_serial(self):
  with self.assertRaises(ScreenError):ADB('x;rm -rf /')
 def test_15_no_guessed_selector(self):
  with self.assertRaises(ScreenError):matches(parse_nodes(self.XML)[0],{})
if __name__=='__main__':unittest.main(verbosity=2)
