import unittest,uuid
from test_system import client,request,device,conn
from device_manager import choose_serial,valid_setup
class DevicesFlowTests(unittest.TestCase):
 def test_add_any_brand_no_default_account(self):
  before=len(request('/accounts').get_json())
  data=request('/devices','POST',{'name':'Second phone','model':'Samsung','connection':'USB / ADB'}).get_json()
  self.assertTrue(valid_setup(data['setup']))
  self.assertEqual(len(request('/accounts').get_json()),before)
  paired=client.post('/api/bridge/pair',json={'code':data['setup']['code']}).get_json()
  self.assertEqual(paired['device_id'],data['id']);self.assertEqual(paired['accounts'],[]);self.assertIsNone(paired['account'])
 def test_specific_device_not_first_tiktok_slot(self):
  a,ah=device();b,bh=device();r=request('/devices/'+b['id']+'/pairing','POST',{}).get_json()
  paired=client.post('/api/bridge/pair',json={'code':r['code']}).get_json();self.assertEqual(paired['device_id'],b['id']);self.assertNotEqual(paired['device_id'],a['id'])
 def test_pairing_auth_and_scope(self):
  d,h=device()
  self.assertEqual(client.post('/api/devices/'+d['id']+'/pairing',json={}).status_code,401)
  self.assertEqual(request('/devices/'+d['id']+'/pairing','POST',{},h).status_code,410)
 def test_busy_device_no_rotation(self):
  d,h=device()
  with conn() as c:c.execute("insert into ui_jobs(id,device_id,publication_id,account_id,status,payload,available,phase,created_at) values(?,?,?,?,?,?,?,?,?)",('busy-'+d['id'],d['id'],'busy-pub-'+d['id'],'fixture','VERIFYING','{}',0,'SUBMITTED','test'))
  self.assertEqual(request('/devices/'+d['id']+'/pairing','POST',{}).status_code,409)
 def test_multiple_phones_never_guessed(self):
  self.assertIsNone(choose_serial({},['Samsung','Pixel'],{}));self.assertEqual(choose_serial({'serial':'Pixel'},['Samsung','Pixel'],{}),'Pixel');self.assertEqual(choose_serial({},['Samsung','Pixel'],{'Samsung':{}}),'Pixel')
 def test_no_foreign_setup_server(self):
  d,h=device();setup=request('/devices/'+d['id']+'/pairing','POST',{}).get_json();self.assertTrue(valid_setup(setup));setup['server']='https://evil.example';self.assertFalse(valid_setup(setup))
 def test_integrations_not_falsely_ready(self):
  rows=request('/integrations').get_json();self.assertEqual(len(rows),4)
  for item in rows:
   if item['id']!='tiktok_android_v14':self.assertEqual(item['state'],'NOT_IMPLEMENTED')
 def test_profiles_platforms_register_without_fake_automation(self):
  d,h=device()
  for platform in ('TikTok','YouTube','Instagram','VK'):
   result=request('/accounts','POST',{'platform':platform,'username':'@another_profile','device_id':d['id']});self.assertEqual(result.status_code,200)
   profile=next(x for x in request('/accounts').get_json() if x['id']==result.get_json()['id']);self.assertFalse(profile['automation_ready'])
 def test_same_account_same_file_other_phone_blocked(self):
  from test_system import new_job
  d,h=device();pub,body,key=new_job(d);other,oh=device()
  account=request('/accounts','POST',{'platform':'TikTok','username':'@redmaagi','device_id':other['id']}).get_json()
  body={**body,'account_id':account['id']}
  from test_system import owner
  r=request('/publications','POST',body,{**owner,'Idempotency-Key':uuid.uuid4().hex})
  self.assertEqual(r.status_code,409);self.assertEqual(r.get_json()['publication_id'],pub['id'])
 def test_requested_ui_buttons(self):
  from pathlib import Path
  s=(Path(__file__).parents[1]/'app.js').read_text()
  publishing=s.split('async function publishing()',1)[1].split('function publicationInfo()',1)[0]
  self.assertNotIn('connectMac',publishing);self.assertNotIn('addPublication()',publishing)
  dashboard=s.split('async function dashboard()',1)[1].split('async function accounts()',1)[0]
  self.assertNotIn('9:16',dashboard)
 def test_assign_existing_profile(self):
  d,h=device();other,oh=device();a=request('/accounts','POST',{'platform':'YouTube','username':'my-channel','device_id':d['id']}).get_json()
  self.assertEqual(request('/accounts/'+a['id']+'/device','PATCH',{'device_id':other['id']}).status_code,200)
  with conn() as c:self.assertEqual(c.execute('select device_id from accounts where id=?',(a['id'],)).fetchone()['device_id'],other['id'])
 def test_never_move_unfinished_job(self):
  from test_system import new_job
  d,h=device();pub,body,key=new_job(d);other,oh=device()
  self.assertEqual(request('/accounts/'+body['account_id']+'/device','PATCH',{'device_id':other['id']}).status_code,409)
 def test_disconnect_keeps_identity_credentials_and_bindings(self):
  d,h=device();a=request('/accounts','POST',{'platform':'YouTube','username':'my-channel','device_id':d['id']}).get_json()
  request('/bridge/heartbeat','POST',{'battery':80},h)
  self.assertEqual(request('/bridge/disconnect','POST',{},h).status_code,200)
  with conn() as c:
   row=c.execute('select * from devices where id=?',(d['id'],)).fetchone();self.assertEqual(row['status'],'OFFLINE')
   self.assertEqual(c.execute('select device_id from accounts where id=?',(a['id'],)).fetchone()['device_id'],d['id'])
  self.assertEqual(request('/bridge/heartbeat','POST',{'battery':81},h).status_code,200)
  with conn() as c:self.assertEqual(c.execute('select status from devices where id=?',(d['id'],)).fetchone()['status'],'ONLINE')
 def test_disconnect_scope_only_own_device(self):
  a,ah=device();b,bh=device();request('/bridge/heartbeat','POST',{},bh)
  self.assertEqual(request('/bridge/disconnect','POST',{},ah).status_code,200)
  with conn() as c:self.assertEqual(c.execute('select status from devices where id=?',(b['id'],)).fetchone()['status'],'ONLINE')
  self.assertEqual(client.post('/api/bridge/disconnect',json={}).status_code,401)
if __name__=='__main__':unittest.main()
