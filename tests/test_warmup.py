import unittest,time,json
from test_system import client,request,device,conn,new_job
def setup(slots=('morning','afternoon','evening','night'),sessions=2):
 d,h=device()
 p=request('/personas','POST',{'name':'P','device_id':d['id'],'preferred_times':[{'time_slot':s,'weight':3} for s in slots]}).get_json()['id']
 with conn() as c:c.execute('update ws_personas set max_sessions_per_account=? where id=?',(sessions,p))
 a=request('/accounts','POST',{'platform':'TikTok','username':'@w'+str(time.time_ns())[-6:],'device_id':d['id']}).get_json()
 request('/account-profiles/'+a['id'],'PUT',{'persona_id':p,'search_keywords':'авто, ремонт','work_mode':'young'})
 return d,h,a,p
class WarmupTests(unittest.TestCase):
 def setUp(self):
  with conn() as c:
   for t in ('ws_tasks','ws_task_events'):c.execute('delete from '+t)
   c.execute("delete from ws_settings where key='warmup'")
 def test_auth_and_system_scenarios(self):
  self.assertEqual(client.get('/api/warmup/tasks').status_code,401)
  s=request('/scenarios').get_json();self.assertEqual([x['name'] for x in s if x['system']],['Прогрев 1','Прогрев 2','Прогрев 3'])
  self.assertEqual(request('/scenarios/sys-warm-1','PATCH',{'name':'x'}).status_code,409)
  r=request('/scenarios','POST',{'name':'Мой','watch_min':50,'watch_max':10,'like_pct':99,'account_mode':'bad'});self.assertEqual(r.status_code,200)
  m=[x for x in request('/scenarios').get_json() if x['id']==r.get_json()['id']][0]
  self.assertEqual((m['watch_max'],m['like_pct'],m['account_mode']),(50,60,'any'))
  self.assertEqual(request('/scenarios/'+m['id'],'DELETE').status_code,200)
 def test_disabled_by_default_and_planning(self):
  d,h,a,p=setup()
  self.assertIsNone(request('/bridge/warm/claim','POST',{},h).get_json()['task'])
  st=request('/warmup/settings','PUT',{'enabled':True}).get_json();self.assertTrue(st['enabled'])
  tasks=[t for t in request('/warmup/tasks').get_json() if t['account_id']==a['id']]
  self.assertTrue(len(tasks)<=2)
  for t in tasks:self.assertEqual(t['scenario_id'],'sys-warm-1');self.assertGreater(t['planned_start'],time.time())
  n=len(request('/warmup/tasks').get_json());request('/warmup/tasks');self.assertEqual(len(request('/warmup/tasks').get_json()),n)  # planned once a day
 def test_session_flow_promotion_and_publication_priority(self):
  d,h,a,p=setup()
  request('/warmup/settings','PUT',{'enabled':True,'to_warming':1,'to_hot':2})
  tid=request('/warmup/run-now','POST',{'account_id':a['id'],'minutes':3}).get_json()['id']
  task=request('/bridge/warm/claim','POST',{},h).get_json()['task'];self.assertEqual(task['id'],tid)
  self.assertEqual(task['keywords'],['авто','ремонт']);self.assertEqual(task['duration'],180)
  self.assertEqual(request('/bridge/claim','POST',{},h).get_json().get('busy'),'warmup')  # publication waits for the session
  self.assertTrue(request(f'/bridge/warm/{tid}/events','POST',{'events':[{'type':'like','message':'Лайк'}],'counts':{'videos':3,'likes':1}},h).get_json()['go'])
  self.assertEqual(request(f'/bridge/warm/{tid}/finish','POST',{'status':'done','counts':{'videos':9,'likes':2},'seconds':175},h).status_code,200)
  t=request('/warmup/tasks/'+tid).get_json();self.assertEqual(t['status'],'done');self.assertEqual(t['actions']['likes'],2);self.assertTrue(any(e['event_type']=='stage' for e in t['events']))
  prof=[x for x in request('/account-profiles').get_json() if x['id']==a['id']][0];self.assertEqual(prof['work_mode'],'warming')
  # a due publication makes the agent yield
  tid2=request('/warmup/run-now','POST',{'account_id':a['id'],'minutes':3}).get_json()['id']
  self.assertEqual(request('/bridge/warm/claim','POST',{},h).get_json()['task']['id'],tid2)
  with conn() as c:c.execute("insert into ui_jobs(id,device_id,publication_id,account_id,status,payload,available,phase,created_at) values('wj1',?,'wp1',?,'QUEUED','{}',0,'NEW','x')",(d['id'],a['id']))
  self.assertFalse(request(f'/bridge/warm/{tid2}/events','POST',{'events':[]},h).get_json()['go'])
  request(f'/bridge/warm/{tid2}/finish','POST',{'status':'done'},h)
  request('/warmup/run-now','POST',{'account_id':a['id']})
  self.assertEqual(request('/bridge/warm/claim','POST',{},h).get_json().get('reason'),'publication')
  with conn() as c:c.execute("delete from ui_jobs where id='wj1'")
 def test_cancel_restart_delete_and_lease(self):
  d,h,a,p=setup();request('/warmup/settings','PUT',{'enabled':True})
  tid=request('/warmup/run-now','POST',{'account_id':a['id']}).get_json()['id']
  self.assertEqual(request(f'/warmup/tasks/{tid}/delete','POST',{}).status_code,200)
  tid=request('/warmup/run-now','POST',{'account_id':a['id']}).get_json()['id']
  request('/bridge/warm/claim','POST',{},h)
  self.assertEqual(request(f'/warmup/tasks/{tid}/delete','POST',{}).status_code,409)
  self.assertEqual(request(f'/warmup/tasks/{tid}/cancel','POST',{}).status_code,200)
  self.assertFalse(request(f'/bridge/warm/{tid}/events','POST',{},h).get_json()['go'])
  self.assertEqual(request(f'/warmup/tasks/{tid}/restart','POST',{}).status_code,200)
  self.assertEqual(request('/bridge/warm/claim','POST',{},h).get_json()['task']['id'],tid)
  with conn() as c:c.execute('update ws_tasks set lease_until=0 where id=?',(tid,))
  request('/bridge/warm/claim','POST',{},h)
  self.assertEqual(request('/warmup/tasks/'+tid).get_json()['status'],'failed')
  r=request(f'/bridge/warm/{tid}/finish','POST',{'status':'done'},h);self.assertTrue(r.get_json().get('existing'))
 def test_human_intervention_alert_and_not_eligible(self):
  d,h,a,p=setup();request('/warmup/settings','PUT',{'enabled':True})
  tid=request('/warmup/run-now','POST',{'account_id':a['id']}).get_json()['id'];request('/bridge/warm/claim','POST',{},h)
  request(f'/bridge/warm/{tid}/finish','POST',{'status':'human_intervention','error':'капча'},h)
  al=request('/alerts?limit=5').get_json();self.assertTrue(any(x['event_type']=='human_intervention' for x in al['items']))
  b=request('/accounts','POST',{'platform':'TikTok','username':'@nobody'}).get_json()
  self.assertEqual(request('/warmup/run-now','POST',{'account_id':b['id']}).status_code,400)
  self.assertIn('warm-install.py',request('/warm-install-command').get_json()['command'])
if __name__=='__main__':unittest.main()
