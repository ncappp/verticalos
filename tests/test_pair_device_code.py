import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_system import client,request,device
from pair_device_code import config_path,pick_serial,redeem_code,code_mode_source
class PairByCodeTests(unittest.TestCase):
 def response(self,r):
  class R:
   status_code=r.status_code
   ok=r.status_code==200
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def json(self):return r.get_json()
  return R()
 def post(self,url,**kw):return self.response(client.post('/api/bridge/pair',json=kw['json']))
 def test_one_code_saves_correct_slot_for_existing_manager(self):
  d,h=device();code=request('/devices/'+d['id']+'/pairing','POST',{}).get_json()['code'];root=Path(tempfile.mkdtemp());serial='PHONE_TWO'
  record=redeem_code(code,serial,root,self.post);file=config_path(root,serial)
  self.assertEqual(record['device_id'],d['id']);self.assertEqual(json.loads(file.read_text()),record);self.assertEqual(file.stat().st_mode&0o777,0o600)
  h={'X-Device-ID':record['device_id'],'Authorization':'Bearer '+record['device_token']}
  self.assertEqual(request('/bridge/heartbeat','POST',{},h).status_code,200)
  self.assertEqual(request('/bridge/disconnect','POST',{},h).status_code,200)
  self.assertEqual(request('/bridge/heartbeat','POST',{},h).status_code,200)
  self.assertEqual(json.loads(file.read_text()),record)
 def test_failed_code_never_overwrites_saved_credentials(self):
  root=Path(tempfile.mkdtemp());file=config_path(root,'PHONE');file.parent.mkdir(parents=True);file.write_text('{"old":"preserved"}')
  with self.assertRaises(RuntimeError):redeem_code('ABCDE-FGHJK-LMNPQ','PHONE',root,self.post)
  self.assertEqual(json.loads(file.read_text()),{'old':'preserved'})
 def test_consumed_code_is_not_reusable(self):
  d,h=device();code=request('/devices/'+d['id']+'/pairing','POST',{}).get_json()['code'];root=Path(tempfile.mkdtemp())
  record=redeem_code(code,'PHONE',root,self.post)
  with self.assertRaises(RuntimeError):redeem_code(code,'PHONE',root,self.post)
  self.assertEqual(json.loads(config_path(root,'PHONE').read_text()),record)
 def test_no_guess_between_phones(self):
  self.assertIsNone(pick_serial(['A','B']));self.assertEqual(pick_serial(['A','B'],'2'),'B');self.assertIsNone(pick_serial(['A','B'],'3'))
 def test_public_helper_no_credentials_and_matches_checksum(self):
  command=request('/pairing-command').get_json();script=client.get('/device-pairing-helper.py')
  self.assertEqual(script.status_code,200);self.assertIn(hashlib.sha256(script.data).hexdigest(),command['command']);self.assertFalse(command['application_install_required']);script.close()
  self.assertEqual(client.get('/api/pairing-command').status_code,401)
 def test_ui_does_not_download_connection_file(self):
  s=(Path(__file__).parents[1]/'app.js').read_text()
  self.assertNotIn('downloadDeviceSetup',s);self.assertNotIn('faxclip-connect-',s);self.assertIn('Подключить по коду',s)
 def test_code_mode_update_keeps_saved_config_reader_and_workers(self):
  raw=(Path(__file__).parents[1]/'bridge/device_manager.py').read_bytes();new=code_mode_source(raw)
  self.assertIn(b'PAIR_BY_CODE_ONLY',new);self.assertNotIn(b"for file in downloads.glob('faxclip-connect-*.json')",new)
  self.assertIn(b"for file in configs.glob('*.json')",new);self.assertIn(b'workers',new)
  self.assertEqual(code_mode_source(new),new)
  with self.assertRaises(RuntimeError):code_mode_source(b'unknown user-modified code')
if __name__=='__main__':unittest.main()

