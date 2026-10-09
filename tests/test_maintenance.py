import base64,json,os,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from test_system import client,request,device,new_job,conn,owner,UPLOAD
from maintenance import apply_bootstrap
class MaintenanceTests(unittest.TestCase):
 def setUp(self):
  with conn() as c:
   c.execute('delete from ui_jobs');c.execute('delete from device_jobs');c.execute('delete from publications');c.execute('delete from ui_deleted_jobs')
 def clear(self,preview=None):
  p=preview or request('/maintenance/attempts-preview').get_json()
  return request('/maintenance/clear-attempts','POST',{'confirmation':'DELETE_ALL_ATTEMPTS','fingerprint':p['fingerprint']})
 def test_owner_authorization_required(self):
  self.assertEqual(client.get('/api/maintenance/attempts-preview').status_code,401)
  self.assertEqual(client.post('/api/maintenance/clear-attempts',json={'confirmation':'DELETE_ALL_ATTEMPTS'}).status_code,401)
  d,h=device();self.assertEqual(request('/maintenance/clear-attempts','POST',{'confirmation':'DELETE_ALL_ATTEMPTS'},h).status_code,401)
  self.assertEqual(request('/maintenance/enrollment-backup',headers=h).status_code,401)
 def test_confirmation_required(self):
  d,h=device();new_job(d)
  self.assertEqual(request('/maintenance/clear-attempts','POST',{}).status_code,400)
  self.assertEqual(request('/maintenance/attempts-preview').get_json()['publications'],1)
 def test_active_lease_not_deleted(self):
  d,h=device();new_job(d);request('/bridge/claim','POST',{},h)
  self.assertEqual(self.clear().status_code,409)
  with conn() as c:self.assertEqual(c.execute('select count(*) n from ui_jobs').fetchone()['n'],1)
 def test_stale_snapshot_rejected(self):
  d,h=device();new_job(d);p=request('/maintenance/attempts-preview').get_json();new_job(d)
  self.assertEqual(self.clear(p).status_code,409)
  self.assertEqual(request('/maintenance/attempts-preview').get_json()['publications'],2)
 def test_clear_unblocks_queue_preserves_devices_accounts_clips_and_guards(self):
  d,h=device();pub,body,key=new_job(d);job=request('/bridge/claim','POST',{},h).get_json()['job']
  lease={**h,'X-Job-Lease':job['lease']};request('/bridge/jobs/'+job['id']+'/fail','POST',{'code':'LINK_READ_CLIPBOARD_NOT_FOCUSED'},lease)
  self.assertTrue(request('/bridge/claim','POST',{},h).get_json()['paused'])
  with conn() as c:
   before={t:c.execute('select count(*) n from '+t).fetchone()['n'] for t in ('devices','accounts','clips','ui_media_guard','ui_account_media_guard','ui_idempotency','tasks')}
  result=self.clear();self.assertEqual(result.status_code,200);self.assertTrue(result.get_json()['queue_empty'])
  self.assertEqual(request('/publications').get_json(),[])
  state=request('/bridge/queue-state',headers=h).get_json();self.assertTrue(state['queue_empty'])
  self.assertEqual(request('/bridge/heartbeat','POST',{},h).status_code,200)
  response=request('/bridge/claim','POST',{},h).get_json();self.assertIsNone(response['job']);self.assertFalse(response.get('paused',False))
  with conn() as c:
   for t,n in before.items():self.assertEqual(c.execute('select count(*) n from '+t).fetchone()['n'],n,t)
  recovery=request('/bridge/jobs/'+job['id']+'/verification-claim','POST',{},h).get_json();self.assertTrue(recovery['done']);self.assertTrue(recovery['deleted'])
  other,oh=device();self.assertEqual(request('/bridge/jobs/'+job['id']+'/verification-claim','POST',{},oh).status_code,404)
  self.assertTrue(request('/publications','POST',body,key).get_json()['existing'])
  # A new idempotency key cannot bypass the retained media fingerprint.
  from test_system import uuid
  self.assertEqual(request('/publications','POST',body,{**owner,'Idempotency-Key':str(uuid.uuid4())}).status_code,409)
 def test_evidence_deleted_source_video_retained(self):
  d,h=device();pub,body,key=new_job(d);job=request('/bridge/claim','POST',{},h).get_json()['job']
  directory=Path(UPLOAD).parent/'evidence';directory.mkdir(exist_ok=True);e=directory/(job['id']+'.png');e.write_bytes(b'test')
  with conn() as c:
   c.execute("update ui_jobs set status='NEEDS_REVIEW',evidence=? where id=?",(e.name,job['id']))
   video=c.execute('select source_file from clips where id=?',(body['clip_id'],)).fetchone()['source_file']
  self.assertEqual(self.clear().status_code,200);self.assertFalse(e.exists());self.assertTrue((Path(UPLOAD)/video).exists())
 def test_backup_has_no_tokens_or_queue(self):
  d,h=device();new_job(d);r=request('/maintenance/enrollment-backup');self.assertEqual(r.status_code,200)
  backup=r.get_json();self.assertNotIn(d['device_token'],r.get_data(as_text=True));self.assertNotIn('ui_jobs',backup['tables']);self.assertNotIn('publications',backup['tables'])
  self.assertTrue(all(len(x['token_hash'])==64 for x in backup['tables']['devices']))
 def test_bootstrap_restores_once_without_queue(self):
  d,h=device();new_job(d);backup=request('/maintenance/enrollment-backup').get_json()
  with conn() as c:schema=';\n'.join(x['sql'] for x in c.execute("select sql from sqlite_master where type='table' and sql is not null"))+';'
  with tempfile.TemporaryDirectory() as tmp:
   db=Path(tmp)/'cold.db'
   c=sqlite3.connect(db);c.executescript(schema);c.close()
   def cold():
    from app import ClosingConnection
    c=sqlite3.connect(db,factory=ClosingConnection);c.row_factory=sqlite3.Row;c.execute('pragma foreign_keys=ON');return c
   value=base64.b64encode(json.dumps(backup).encode()).decode()
   with patch.dict(os.environ,{'FAXCLIP_BOOTSTRAP_B64':value}):
    apply_bootstrap(cold,lambda:'test-time')
    with cold() as c:
     self.assertEqual(c.execute('select count(*) n from ui_jobs').fetchone()['n'],0)
     self.assertEqual(c.execute('select count(*) n from devices').fetchone()['n'],len(backup['tables']['devices']))
     stored=c.execute('select * from devices where id=?',(d['id'],)).fetchone();self.assertEqual(stored['status'],'PENDING')
     import hashlib
     self.assertEqual(stored['token_hash'],hashlib.sha256(d['device_token'].encode()).hexdigest())
     c.execute('delete from accounts');c.execute('delete from devices')
    apply_bootstrap(cold,lambda:'later')
    with cold() as c:self.assertEqual(c.execute('select count(*) n from devices').fetchone()['n'],0)
 def test_invalid_bootstrap_rejected(self):
  with patch.dict(os.environ,{'FAXCLIP_BOOTSTRAP_B64':'NOT_VALID_BASE64'}):
   with self.assertRaises(RuntimeError):apply_bootstrap(conn,lambda:'now')
if __name__=='__main__':unittest.main()

class ConfirmLinkTests(unittest.TestCase):
 def test_owner_link_completes_submitted_review_and_unblocks_queue(self):
  d,h=device();pub,_,_=new_job(d)
  pid=pub.get('id') or pub.get('publication_id')
  with conn() as c:
   jid=c.execute('select id from ui_jobs where publication_id=?',(pid,)).fetchone()['id']
   c.execute("update ui_jobs set status='NEEDS_REVIEW',phase='SUBMITTED' where id=?",(jid,))
   c.execute("update publications set status='NEEDS_REVIEW',error='LINK_COPIED_NOT_READ' where id=?",(pid,))
  self.assertEqual(request('/publications/'+pid+'/confirm-link','POST',{'url':'https://example.com/x'}).status_code,400)
  r=request('/publications/'+pid+'/confirm-link','POST',{'url':'https://www.tiktok.com/@redmaagi/video/7412345678901234567?is_from_webapp=1'})
  self.assertEqual(r.status_code,200,r.get_json())
  with conn() as c:
   self.assertEqual(c.execute('select status from ui_jobs where id=?',(jid,)).fetchone()['status'],'DONE')
   self.assertEqual(c.execute('select external_id from publications where id=?',(pid,)).fetchone()['external_id'],'https://www.tiktok.com/@redmaagi/video/7412345678901234567')
  self.assertEqual(request('/publications/'+pid+'/confirm-link','POST',{'url':'https://www.tiktok.com/@redmaagi/video/7412345678901234567'}).get_json().get('existing'),True)
 def test_link_rejected_before_submission(self):
  d,h=device();pub,_,_=new_job(d);pid=pub.get('id') or pub.get('publication_id')
  with conn() as c:c.execute("update ui_jobs set status='NEEDS_REVIEW',phase='READY' where publication_id=?",(pid,))
  self.assertEqual(request('/publications/'+pid+'/confirm-link','POST',{'url':'https://www.tiktok.com/@redmaagi/video/7412345678901234568'}).status_code,409)

class DismissTests(unittest.TestCase):
 def _job(self,phase,status='NEEDS_REVIEW'):
  d,h=device();pub,_,_=new_job(d);pid=pub.get('id') or pub.get('publication_id')
  with conn() as c:c.execute("update ui_jobs set status=?,phase=? where publication_id=?",(status,phase,pid))
  return d,h,pid
 def test_dismiss_unsubmitted_unblocks_queue_and_keeps_guard(self):
  d,h,pid=self._job('READY')
  self.assertTrue(request('/bridge/claim','POST',{},h).get_json().get('paused'))
  self.assertEqual(request('/publications/'+pid+'/dismiss','POST',{}).status_code,200)
  self.assertFalse(request('/bridge/claim','POST',{},h).get_json().get('paused'))
  with conn() as c:self.assertTrue(c.execute('select 1 from ui_account_media_guard where publication_id=?',(pid,)).fetchone())
 def test_dismiss_submitted_requires_confirmation(self):
  d,h,pid=self._job('SUBMITTED')
  r=request('/publications/'+pid+'/dismiss','POST',{});self.assertEqual(r.status_code,409);self.assertTrue(r.get_json().get('needs_confirm'))
  self.assertEqual(request('/publications/'+pid+'/dismiss','POST',{'confirm':'NOT_PUBLISHED'}).status_code,200)
 def test_running_cannot_be_dismissed(self):
  d,h,pid=self._job('READY','RUNNING')
  self.assertEqual(request('/publications/'+pid+'/dismiss','POST',{}).status_code,409)

class TelegramBackupTests(unittest.TestCase):
 def test_disabled_with_test_token_and_roundtrip_validation(self):
  import tg_backup,sqlite3,tempfile,os
  self.assertFalse(tg_backup.enabled())
  fd,p=tempfile.mkstemp(suffix='.db');os.close(fd)
  c=sqlite3.connect(p);c.execute('create table devices(id text)');c.execute("insert into devices values('x')");c.commit();c.close()
  raw=tg_backup._snapshot(p);self.assertTrue(tg_backup._valid_db(raw));self.assertFalse(tg_backup._valid_db(b'garbage'))
  self.assertTrue(tg_backup._has_state(p))
 def test_restore_and_backup_with_fake_telegram(self):
  import tg_backup,sqlite3,tempfile,os,gzip
  from unittest.mock import patch
  fd,src=tempfile.mkstemp(suffix='.db');os.close(fd)
  c=sqlite3.connect(src);c.execute('create table devices(id text)');c.execute('create table accounts(id text)');c.execute("insert into devices values('dev1')");c.commit();c.close()
  store={}
  class R:
   def __init__(s,j=None,content=b''):s._j=j;s.content=content
   def json(s):return s._j
   def raise_for_status(s):pass
  def post(url,timeout=None,data=None,files=None):
   m=url.rsplit('/',1)[1]
   if m=='sendDocument':store['doc']=files['document'][1].read();store['name']=files['document'][0];return R({'ok':True,'result':{'message_id':7}})
   if m=='pinChatMessage':store['pinned']=data['message_id'];return R({'ok':True,'result':True})
   if m=='getChat':return R({'ok':True,'result':{'pinned_message':{'message_id':7,'document':{'file_name':store['name'],'file_id':'F'}}}})
   if m=='getFile':return R({'ok':True,'result':{'file_path':'x'}})
   return R({'ok':True,'result':True})
  def get(url,timeout=None):return R(content=store['doc'])
  env={'TELEGRAM_BOT_TOKEN':'123456:'+'A'*35,'TELEGRAM_ALLOWED_USER_IDS':'8784706094','FAXCLIP_TG_BACKUP':'1'}
  with patch.dict(os.environ,env),patch.object(tg_backup.requests,'post',post),patch.object(tg_backup.requests,'get',get):
   tg_backup._state.update(hash=None,message_id=None)
   self.assertTrue(tg_backup.backup_now(src));self.assertEqual(store['pinned'],7)
   self.assertFalse(tg_backup.backup_now(src)) # unchanged -> no resend
   fd,dst=tempfile.mkstemp(suffix='.db');os.close(fd);os.unlink(dst)
   self.assertTrue(tg_backup.restore_if_empty(dst))
   c=sqlite3.connect(dst);self.assertEqual(c.execute('select id from devices').fetchone()[0],'dev1');c.close()
   self.assertFalse(tg_backup.restore_if_empty(dst)) # never overwrites live data
