import csv,io,json,tempfile,threading,time,unittest,urllib.request,zipfile
from pathlib import Path
from unittest.mock import patch
import batch_engine as b
import app

class BatchTests(unittest.TestCase):
 def test_empty_extraction_is_reported_and_retried(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/'test.png';p.write_bytes(b'image')
   for _ in range(2):
    with patch.object(b,'file_pages',return_value=iter([(1,[],'Unreadable table')])) as reader:
     leads,attempts=b.extract_batch([p],root/'out')
     self.assertEqual(reader.call_count,1)
     self.assertEqual(leads,[])
     self.assertEqual(attempts[0]['status'],'REVIEW_REQUIRED')
     self.assertIn('No lead rows',attempts[0]['error'])
 def test_table_excludes_neighboring_email_column(self):
  def word(text,x,y):return {'text':text,'left':str(x),'top':str(y),'height':'12'}
  words=[word('NAME',100,10),word('JOB',250,10),word('COMPANY',500,10),word('EMAILS',700,10),
   word('Jane',100,50),word('CEO',250,50),word('Example',500,50),word('Access email',700,50)]
  rows,_=b.parse_words(words)
  self.assertEqual(rows[0]['company'],'Example')
 def test_cropped_table_does_not_extract_toolbar_as_lead(self):
  def word(text,x,y):return {'text':text,'left':str(x),'top':str(y),'height':'12'}
  words=[word('Default view | Create workflow | Save new search',100,10),word('NAME',100,50),word('EMAILS',400,50)]
  self.assertEqual(b.parse_words(words)[0],[])
 def test_failed_extraction_never_reports_finished(self):
  with tempfile.TemporaryDirectory() as d,patch.object(app,'ROOT',Path(d)),patch.object(app,'JOBS',{}):
   job=app.new_job();job['files']=[{'stored':'test.png','name':'test.png','size':5}]
   def extract(*args):
    (Path(d)/job['id']/'extraction').mkdir()
    return [],[{'status':'ERROR','error':'Tesseract OCR is missing'}]
   with patch.object(app,'extract_batch',side_effect=extract):app.work(job,'extracting',{})
   self.assertEqual(job['state'],'error')
   self.assertIn('Extracted 0 leads',job['message'])
   self.assertIn('Tesseract',job['message'])
 def test_parse_text_and_dedupe(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/'leads.csv';p.write_text('visible_name,role,company\nJane S,Founder,Example Foods\nJane S,Founder,Example Foods\n')
   leads,attempts=b.extract_batch([p],root/'out')
   self.assertEqual(len(leads),1);self.assertEqual(leads[0]['visible_name'],'Jane S')
   self.assertEqual(b.parse_text('Name | Role | Company\nJane Smith | CEO | Example Foods')[0]['company'],'Example Foods')
 def test_searxng_http_contract_cache_and_budget(self):
  from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
  class Server(BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_GET(self):
    self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'results':[{'title':'Jane - Firm','url':'https://example.org','content':'Founder'}]}).encode())
  server=ThreadingHTTPServer(('127.0.0.1',0),Server);threading.Thread(target=server.serve_forever,daemon=True).start()
  try:
   with tempfile.TemporaryDirectory() as d:
    provider=b.Provider(Path(d),'searxng',f'http://127.0.0.1:{server.server_port}',1)
    self.assertEqual(provider.run('Firm')[0]['snippet'],'Founder')
    self.assertEqual(provider.run('Firm')[0]['url'],'https://example.org')
    self.assertEqual(provider.calls,1)
    with self.assertRaises(RuntimeError):provider.run('Other query')
  finally:server.shutdown();server.server_close()
 def test_candidates_are_not_verified(self):
  lead={'visible_name':'Jane S','company':'Example Foods','role':'CEO'}
  r={'url':'https://www.linkedin.com/in/jane-smith','title':'Jane Smith - Example Foods | LinkedIn','snippet':'Founder'}
  self.assertEqual(b.candidate(lead,r)['match_status'],'UNCERTAIN_CANDIDATE')
  r['url']='https://fakelinkedin.com/in/jane-smith';self.assertIsNone(b.candidate(lead,r))
 def test_iterative_search_and_publication_separation(self):
  calls=[]
  def search(_,q):
   calls.append(q)
   return [{'url':'https://www.linkedin.com/in/jane-smith','title':'Jane Smith - Example Foods','snippet':'CEO'},
           {'url':'https://example.org/contact','title':'Example Foods','snippet':'Contact'}]
  with tempfile.TemporaryDirectory() as d,patch.object(b.Provider,'run',search),patch.object(b,'fetch',return_value=(b'<p>Press: info@example.org</p>','text/html','https://example.org/contact')),patch.object(b,'check_domain',return_value={'dns_status':'MX_PRESENT'}):
   r=b.research_batch([{'visible_name':'Jane S','role':'CEO','company':'Example Foods'}],Path(d))
   self.assertTrue(any('Jane Smith' in q for q in calls))
   self.assertEqual(r['contacts'][0]['person_association'],'NEEDS_REVIEW')
   self.assertEqual(r['contacts'][0]['mailbox_status'],'NOT_CHECKED')
 def test_cached_files_skip_ocr_and_failed_files_retry(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);p=root/'test.pdf';p.write_bytes(b'fake')
   with patch.object(b,'file_pages',return_value=iter([(1,[{'visible_name':'Jane','company':'Firm','role':'CEO'}],'original text')])) as reader:
    b.extract_batch([p],root/'out');self.assertEqual(reader.call_count,1)
   with patch.object(b,'file_pages',side_effect=AssertionError('Should use cache')):
    leads,_=b.extract_batch([p],root/'out');self.assertEqual(len(leads),1)
 def test_browser_http_upload_extract_download_delete(self):
  with tempfile.TemporaryDirectory() as d,patch.object(app,'ROOT',Path(d)),patch.object(app,'JOBS',{}):
   server=app.ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
   threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
   def request(path,body=None,raw=False):
    data=body if raw else json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(base+path,data=data,headers={'X-Token':app.TOKEN})
    with urllib.request.urlopen(req) as f:
     payload=f.read();return payload if raw and path.startswith('/results') else json.loads(payload)
   try:
    job=request('/new',{})['id']
    request('/upload/'+job+'?name=leads.csv',b'visible_name,role,company\nJane S,Founder,Example Foods\n',True)
    request('/extract/'+job,{})
    for _ in range(100):
     state=request('/status/'+job)
     if state['state'] not in ('extracting','uploading'):break
     time.sleep(.05)
    self.assertEqual(state['state'],'review');self.assertEqual(len(state['leads']),1)
    data=request('/results/'+job,raw=True)
    self.assertIn('extraction/leads.csv',zipfile.ZipFile(io.BytesIO(data)).namelist())
    request('/delete/'+job,{})
    self.assertFalse(list((Path(d)/job/'uploads').iterdir()))
    self.assertFalse((Path(d)/job/'extraction'/'page-cache').exists())
    self.assertTrue((Path(d)/job/'extraction'/'leads.csv').exists())
   finally:server.shutdown();server.server_close()
if __name__=='__main__':unittest.main()
