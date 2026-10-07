import time,unittest
from test_system import client,request,owner,conn

class CloudPairingTests(unittest.TestCase):
 def make_pair(self):
  # Isolated owner fixture: choose a non-running account slot to avoid prior independent protocol fixtures.
  with conn() as c:
   c.execute("update ui_jobs set status='NEEDS_REVIEW' where status='RUNNING'")
  r=request('/phone-pairing','POST',{})
  self.assertEqual(r.status_code,200,r.get_json());return r.get_json()
 def test_owner_auth_required(self):
  self.assertEqual(client.post('/api/phone-pairing',json={}).status_code,401)
 def test_code_is_one_use_and_bridge_scope_only(self):
  pair=self.make_pair();code=pair['code']
  self.assertRegex(code,r'^[A-Z2-9]{5}-[A-Z2-9]{5}-[A-Z2-9]{5}$')
  r=client.post('/api/bridge/pair',json={'code':code});self.assertEqual(r.status_code,200)
  data=r.get_json();h={'X-Device-ID':data['device_id'],'Authorization':'Bearer '+data['device_token']}
  self.assertEqual(request('/bridge/heartbeat','POST',{'battery':80},h).status_code,200)
  self.assertEqual(request('/accounts',headers=h).status_code,401)
  self.assertEqual(client.post('/api/bridge/pair',json={'code':code}).status_code,401)
 def test_old_code_invalid_after_regeneration(self):
  first=self.make_pair();second=self.make_pair()
  self.assertEqual(first['device_id'],second['device_id'])
  self.assertEqual(client.post('/api/bridge/pair',json={'code':first['code']}).status_code,401)
 def test_expired_code_rejected(self):
  pair=self.make_pair()
  with conn() as c:c.execute('update ui_phone_pairs set expires=?',(time.time()-1,))
  self.assertEqual(client.post('/api/bridge/pair',json={'code':pair['code']}).status_code,401)
 def test_wrong_code_rejected(self):
  self.assertEqual(client.post('/api/bridge/pair',json={'code':'WRONG'}).status_code,401)
 def test_busy_device_repair_rejected(self):
  pair=self.make_pair()
  with conn() as c:
   c.execute("insert into ui_jobs(id,device_id,publication_id,account_id,status,payload,available,phase,created_at) values('pairing-busy',?,'pairing-busy-pub','fixture','RUNNING','{}',0,'NEW','test')",(pair['device_id'],))
  self.assertEqual(request('/phone-pairing','POST',{}).status_code,409)
  self.assertEqual(client.post('/api/bridge/pair',json={'code':pair['code']}).status_code,409)
  with conn() as c:c.execute("delete from ui_jobs where id='pairing-busy'")
