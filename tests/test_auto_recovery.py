import json,time,unittest,uuid,tempfile
from pathlib import Path
from unittest.mock import patch
from test_system import client,request,device,new_job,conn
from test_v14 import PNG,URL,FakeADB
from accessibility_publisher import AccessibilityPublisher
from adb_control import ScreenError
class ServerRecoveryTests(unittest.TestCase):
 def paused(self,phase='SUBMITTED'):
  d,h=device();pub,body,key=new_job(d);job=request('/bridge/claim','POST',{},h).get_json()['job'];leased={**h,'X-Job-Lease':job['lease']}
  if phase!='NEW':request('/bridge/jobs/'+job['id']+'/phase','POST',{'phase':'PUBLISH_STARTED'},leased)
  if phase=='SUBMITTED':request('/bridge/jobs/'+job['id']+'/phase','POST',{'phase':'SUBMITTED'},leased)
  request('/bridge/jobs/'+job['id']+'/fail','POST',{'code':'FRESH_LINK_NOT_CONFIRMED'},leased)
  return d,h,pub,job
 def test_device_scope(self):
  d,h,pub,job=self.paused();other,oh=device()
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/verification-claim','POST',{},oh).status_code,404)
  self.assertEqual(client.post('/api/bridge/jobs/'+job['id']+'/verification-claim',json={}).status_code,401)
 def test_never_lease_unsubmitted(self):
  for phase in ('NEW','PUBLISH_STARTED'):
   d,h,pub,job=self.paused(phase)
   self.assertEqual(request('/bridge/jobs/'+job['id']+'/verification-claim','POST',{},h).status_code,409)
 def test_recovery_cannot_publish_or_download(self):
  d,h,pub,job=self.paused();r=request('/bridge/jobs/'+job['id']+'/verification-claim','POST',{},h)
  self.assertEqual(r.status_code,200);recover=r.get_json()['job'];self.assertTrue(recover['verification_only']);leased={**h,'X-Job-Lease':recover['lease']}
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/media',headers=leased).status_code,409)
  for phase in ('NEW','PUBLISH_STARTED','SUBMITTED'):
   self.assertEqual(request('/bridge/jobs/'+job['id']+'/phase','POST',{'phase':phase},leased).status_code,409)
  self.assertTrue(request('/bridge/claim','POST',{},h).get_json()['paused'])
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/verification-claim','POST',{},h).status_code,409)
 def test_complete_once_guard_preserved(self):
  d,h,pub,job=self.paused();recover=request('/bridge/jobs/'+job['id']+'/verification-claim','POST',{},h).get_json()['job'];leased={**h,'X-Job-Lease':recover['lease']}
  proof=dict(post_url=URL,verification='PROFILE_MATCHING_POST_REOPENED_TWICE_BY_URL',sha256=job['payload']['sha256'],caption=job['payload']['caption'])
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/complete','POST',proof,leased).status_code,409)
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/phase','POST',{'phase':'UI_CONFIRMED'},leased).status_code,200)
  self.assertEqual(client.post('/api/bridge/jobs/'+job['id']+'/evidence',headers=leased,data=PNG).status_code,200)
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/complete','POST',{**proof,'sha256':'0'*64},leased).status_code,409)
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/complete','POST',proof,leased).status_code,200)
  self.assertTrue(request('/bridge/jobs/'+job['id']+'/complete','POST',{},leased).get_json()['existing'])
  with conn() as c:self.assertIsNotNone(c.execute('select * from ui_media_guard where publication_id=?',(pub['id'],)).fetchone())
  self.assertIsNone(request('/bridge/claim','POST',{},h).get_json()['job'])
 def test_expired_recovery_does_not_requeue(self):
  d,h,pub,job=self.paused();request('/bridge/jobs/'+job['id']+'/verification-claim','POST',{},h)
  with conn() as c:c.execute('update ui_jobs set lease_until=? where id=?',(time.time()-1,job['id']))
  self.assertTrue(request('/bridge/claim','POST',{},h).get_json()['paused'])
  with conn() as c:self.assertEqual(c.execute('select status from ui_jobs where id=?',(job['id'],)).fetchone()['status'],'NEEDS_REVIEW')
class PhoneRecoveryTests(unittest.TestCase):
 def publisher(self):
  adb=FakeADB();adb.sent=True
  b=type('B',(),{'adb':adb,'check_lease':lambda self:None,'work':Path(tempfile.mkdtemp())})()
  job={'id':str(uuid.uuid4()),'payload':{'sha256':'a'*64,'caption':'caption'}}
  p=AccessibilityPublisher(b,job);record=dict(job_id=job['id'],name='faxclip-auto-'+'b'*32+'.mp4',sha256='a'*64,caption='caption',prior_url=None,post_url=None,stage='SUBMITTED')
  return p,record,adb
 def test_verify_only_no_import_submit(self):
  p,record,adb=self.publisher()
  with patch('accessibility_publisher.time.sleep'):result=p.verify_existing(record)
  self.assertEqual(result['post_url'],URL);self.assertEqual(adb.clicks,0)
  for command in adb.commands:
   self.assertNotEqual(command[:2],('content','call'));self.assertNotIn('tiktok_submission_start',command);self.assertNotIn('android.intent.action.SEND',command)
  self.assertEqual(json.loads((p.b.work/(p.job['id']+'-verification.json')).read_text())['stage'],'VERIFIED')
 def test_wrong_job_no_actions(self):
  p,record,adb=self.publisher();record['job_id']='wrong'
  with self.assertRaisesRegex(ScreenError,'RECOVERY_JOB_BINDING_MISMATCH'):p.verify_existing(record)
  self.assertEqual(adb.commands,[])
 def test_prior_post_never_confirmed(self):
  p,record,adb=self.publisher();record['prior_url']=URL
  with patch('accessibility_publisher.time.sleep'),self.assertRaisesRegex(ScreenError,'PRIOR_POST_NOT_NEW_NO_RETRY'):p.verify_existing(record)
  self.assertEqual(adb.clicks,0)
 def test_delayed_reopening_reinspects_only(self):
  p,record,adb=self.publisher();count=[0];old=p.control
  def control(cmd,*args):
   if cmd=='verification_reopened':
    count[0]+=1
    if count[0]<3:return 'POST_AUTHOR_OR_CAPTION_NOT_MATCHED'
   return old(cmd,*args)
  p.control=control
  with patch('accessibility_publisher.time.sleep'):p.verify_existing(record)
  self.assertEqual(count[0],4);self.assertEqual(adb.clicks,0)
 def test_non_submission_checkpoint_rejected(self):
  p,record,adb=self.publisher();record['stage']='PUBLISH_INTENT'
  with self.assertRaisesRegex(ScreenError,'NO_SUBMISSION_CHECKPOINT_NO_RETRY'):p.verify_existing(record)
  self.assertEqual(adb.commands,[])
if __name__=='__main__':unittest.main()
