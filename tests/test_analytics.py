import unittest,time,json
from unittest.mock import patch
from urllib.parse import urlparse,parse_qs
from test_system import client,request,device,conn
import analytics
class AnalyticsTests(unittest.TestCase):
 def setUp(self):
  with conn() as c:
   for t in ('ws_video_metrics','ws_account_metrics','ws_links','ws_link_clicks'):c.execute('delete from '+t)
   c.execute("delete from ws_settings where key='analytics_sync'")
 def pub(self):
  d,_=device();u='@an'+str(time.time_ns())[-5:];a=request('/accounts','POST',{'platform':'TikTok','username':u,'device_id':d['id']}).get_json();a['username']=u
  pid='p'+str(time.time_ns())
  with conn() as c:c.execute("insert into publications values(?,?,?,?,?,?,?,?,?,?)",(pid,None,a['id'],'TikTok','UI_CONFIRMED',None,'2026-01-01T10:00:00+00:00','https://www.tiktok.com/@redmaagi/video/1234567890123',None,'2026-01-01'))
  return a,pid
 def test_auth(self):
  for p in ('/api/analytics/v2','/api/links'):self.assertEqual(client.get(p).status_code,401)
 def test_sync_and_summary(self):
  a,pid=self.pub()
  with patch('analytics.fetch_profile',return_value=dict(followers=120,following=3,hearts=900,videos=4)),patch('analytics.fetch_video',return_value=dict(views=1000,likes=50,comments=5,shares=2,saves=7)),patch('analytics.time.sleep'):
   import app as appmod
   fns=[f for f in appmod.app.view_functions.values() if getattr(f,'__name__','')=='analytics_sync']
   self.assertEqual(request('/analytics/sync','POST',{}).status_code,200)
   for _ in range(50):
    time.sleep(0.05)
    if not request('/analytics/v2').get_json()['syncing']:break
  r=request('/analytics/v2').get_json()
  self.assertGreaterEqual(r['totals']['views'],1000);self.assertGreaterEqual(r['totals']['followers'],120);self.assertIn(pid,[t['publication_id'] for t in r['top']])
  self.assertEqual(r['series'][-1]['views'],r['totals']['views']);self.assertTrue([x for x in r['accounts'] if x['account_id']==a['id']][0]['followers']==120)
  self.assertEqual(request('/analytics/manual','POST',{'publication_id':pid,'views':1500,'likes':60}).status_code,200)
  self.assertEqual(request('/analytics/publications').get_json()[pid]['views'],1500)
  self.assertEqual(len(request(f'/analytics/publications/{pid}/history').get_json()),2)
 def test_links(self):
  a,_=self.pub()
  self.assertEqual(request('/links','POST',{'target_url':'javascript:alert(1)'}).status_code,400)
  self.assertEqual(request('/links','POST',{'target_url':'https://site.ru/x','offer':'bad offer<'}).status_code,400)
  r=request('/links','POST',{'target_url':'https://site.ru/land?a=1&utm_source=old','placement':'shapka','offer':'курс','keyword':'авто','account_id':a['id'],'metrika_goal':'lead'}).get_json()
  q=parse_qs(urlparse(r['full_url']).query)
  self.assertEqual((q['utm_source'],q['utm_medium'],q['utm_campaign'],q['utm_content'],q['utm_term'],q['a']),(['tiktok'],['shapka'],['курс'],[a['username'].lstrip('@')],['авто'],['1']))
  g=client.get('/l/'+r['code']);self.assertEqual(g.status_code,302);self.assertEqual(g.headers['Location'],r['full_url'])
  self.assertEqual(client.get('/l/nope00').status_code,404)
  lst=request('/links').get_json();self.assertEqual(lst['items'][0]['clicks'],1);self.assertEqual(sum(lst['daily'].values()),1)
  n=len(request('/accounts').get_json())
  self.assertGreaterEqual(request('/links/bulk','POST',{'target_url':'https://site.ru','offer':'o','placements':['shapka','direct']}).get_json()['created'],2)
  csvb=client.get('/api/links/export.csv',headers=__import__('test_system').owner).data.decode('utf-8-sig');self.assertIn('Короткая ссылка',csvb);self.assertIn('/l/',csvb)
  self.assertEqual(request('/links/'+r['id'],'DELETE').status_code,200)
 def test_parsers(self):
  html='<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">'+json.dumps({'__DEFAULT_SCOPE__':{'webapp.video-detail':{'itemInfo':{'itemStruct':{'stats':{'playCount':10,'diggCount':2,'commentCount':1,'shareCount':0,'collectCount':'3'}}}}}})+'</script>'
  class R:status_code=200;text=html
  with patch('requests.get',return_value=R()):self.assertEqual(analytics.fetch_video('https://www.tiktok.com/@x/video/1234567890'),dict(views=10,likes=2,comments=1,shares=0,saves=3))
  self.assertRaises(ValueError,analytics.fetch_profile,'bad name!')
if __name__=='__main__':unittest.main()
