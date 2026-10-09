import unittest,uuid,time
from pathlib import Path
from test_system import client,request,device,conn,UPLOAD
def setup():
 d,h=device()
 a=request('/accounts','POST',{'platform':'TikTok','username':'@redmaagi','device_id':d['id']}).get_json()
 b=request('/accounts','POST',{'platform':'TikTok','username':'@other','device_id':d['id']}).get_json()
 fn=uuid.uuid4().hex+'.mp4';Path(UPLOAD,fn).write_bytes(b'POSTS_TEST'+uuid.uuid4().bytes)
 clip=request('/clips','POST',{'title':'clip.mp4','source_file':fn}).get_json()
 return d,h,a,b,clip
class PostsTests(unittest.TestCase):
 def test_auth(self):self.assertEqual(client.get('/api/posts').status_code,401)
 def test_draft_edit_publish_fanout(self):
  d,h,a,b,clip=setup()
  r=request('/posts','POST',{'title':'T','caption':'','account_ids':[a['id'],b['id']]});self.assertEqual(r.status_code,200,r.get_json())
  p=r.get_json();self.assertEqual(p['state'],'draft');self.assertEqual(len(p['targets']),2)
  self.assertEqual(request('/posts/'+p['id']+'/publish','POST',{'confirmed':True,'rights_confirmed':True}).status_code,400)
  self.assertEqual(request('/posts/'+p['id'],'PATCH',{'caption':'hello','clip_id':clip['id']}).status_code,200)
  self.assertEqual(request('/posts/'+p['id']+'/publish','POST',{}).status_code,400)
  x=request('/posts/'+p['id']+'/publish','POST',{'confirmed':True,'rights_confirmed':True}).get_json()
  t={t['account_id']:t for t in x['targets']}
  self.assertTrue(t[a['id']]['publication_id']);self.assertIsNone(t[b['id']]['publication_id']);self.assertTrue(t[b['id']]['error'])
  self.assertEqual(x['state'],'queued');self.assertEqual(x['failed_targets'],1)
  self.assertGreaterEqual(request('/posts/counts').get_json()['attention'],1)
  self.assertEqual(request('/posts/'+p['id'],'PATCH',{'caption':'x'}).status_code,409)
  self.assertEqual(request('/posts/'+p['id']+'/publish','POST',{'confirmed':True,'rights_confirmed':True}).status_code,409)
  ids=[i['id'] for i in request('/posts?tab=drafts').get_json()['items']];self.assertNotIn(p['id'],ids)
  # the publication is not duplicated as a virtual legacy post
  allp=request('/posts').get_json()['items'];self.assertFalse(any(i['id']=='pub_'+t[a['id']]['publication_id'] for i in allp))
 def test_schedule_now_cancel_retry(self):
  d,h,a,b,clip=setup()
  later=time.strftime('%Y-%m-%dT%H:%M:%S+00:00',time.gmtime(time.time()+86400))
  p=request('/posts','POST',{'action':'publish','title':'S','caption':'c','clip_id':clip['id'],'account_ids':[a['id']],'scheduled_at':later,'confirmed':True,'rights_confirmed':True}).get_json()
  self.assertEqual(p['state'],'scheduled')
  self.assertIn(p['id'],[i['id'] for i in request('/posts?tab=scheduled').get_json()['items']])
  self.assertEqual(request('/bridge/claim','POST',{},h).get_json()['job'],None)
  self.assertEqual(request('/posts/'+p['id']+'/publish-now','POST',{}).get_json()['moved'],1)
  self.assertEqual(request('/posts/'+p['id']).get_json()['state'],'queued')
  self.assertEqual(request('/posts/'+p['id']+'/cancel','POST',{}).get_json()['cancelled'],1)
  self.assertEqual(request('/posts/'+p['id']).get_json()['state'],'canceled')
  self.assertEqual(request('/posts/'+p['id']+'/retry','POST',{}).get_json()['retried'],1)
  job=request('/bridge/claim','POST',{},h).get_json()['job'];self.assertTrue(job)
  self.assertEqual(request('/posts/'+p['id']).get_json()['state'],'publishing')
  self.assertEqual(request('/posts/'+p['id']+'/cancel','POST',{}).get_json()['busy'],1)
  hh={**h,'X-Job-Lease':job['lease']}
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/phase','POST',{'phase':'PUBLISH_STARTED'},hh).status_code,200)
  self.assertEqual(request('/bridge/jobs/'+job['id']+'/fail','POST',{'code':'UI_REVIEW'},hh).status_code,200)
  self.assertEqual(request('/posts/'+p['id']).get_json()['state'],'error')
  self.assertEqual(request('/posts/'+p['id']+'/retry','POST',{}).status_code,409)  # phone already started: never auto-repeat
  self.assertEqual(request('/posts/'+p['id'],'DELETE').status_code,409)
 def test_reschedule_duplicate_delete_media(self):
  d,h,a,b,clip=setup()
  later=time.strftime('%Y-%m-%dT%H:%M:%S+00:00',time.gmtime(time.time()+3600))
  p=request('/posts','POST',{'action':'publish','caption':'c','clip_id':clip['id'],'account_ids':[a['id']],'scheduled_at':later,'confirmed':True,'rights_confirmed':True}).get_json()
  self.assertEqual(request('/posts/'+p['id']+'/reschedule','POST',{'scheduled_at':'2020-01-01T00:00:00+00:00'}).status_code,400)
  far=time.strftime('%Y-%m-%dT%H:%M:%S+00:00',time.gmtime(time.time()+7200))
  self.assertEqual(request('/posts/'+p['id']+'/reschedule','POST',{'scheduled_at':far}).get_json()['moved'],1)
  with conn() as c:av=c.execute('select available from ui_jobs where publication_id=?',(p['targets'][0]['publication_id'],)).fetchone()[0]
  self.assertGreater(av,time.time()+7000)
  dup=request('/posts/'+p['id']+'/duplicate','POST',{}).get_json();self.assertEqual(dup['state'],'draft')
  self.assertEqual(request('/posts/'+dup['id'],'DELETE').status_code,200)
  r=request('/posts/'+p['id']+'/media');self.assertEqual(r.status_code,200);self.assertTrue(r.data.startswith(b'POSTS_TEST'))
 def test_legacy_publication_visible(self):
  from test_system import new_job
  d,_=device();pub,_,_=new_job(d)
  items=request('/posts').get_json()['items'];v=[i for i in items if i['id']=='pub_'+pub['id']]
  self.assertEqual(len(v),1);self.assertTrue(v[0]['virtual']);self.assertEqual(v[0]['state'],'queued')
  self.assertEqual(request('/posts/pub_'+pub['id']+'/cancel','POST',{}).get_json()['cancelled'],1)
  c=request('/posts/counts').get_json();self.assertIn('drafts',c)
if __name__=='__main__':unittest.main()
