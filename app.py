#!/usr/bin/env python3
"""Run the local batch research interface: python app.py"""
import argparse,csv,io,json,secrets,threading,uuid,zipfile,time,shutil,socket
from pathlib import Path
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from urllib.parse import urlsplit,parse_qs
from batch_engine import extract_batch,research_batch,LEAD_FIELDS
from research import write_csv
from lead_preview import merge_leads,csv_bytes,xlsx_bytes

ROOT=Path(__file__).parent/'batches';ROOT.mkdir(exist_ok=True)
TOKEN=secrets.token_urlsafe(24);JOBS={};LOCK=threading.Lock()

def save(job):
    (ROOT/job['id']/'state.json').write_text(json.dumps(job,indent=2))

def new_job():
    id=uuid.uuid4().hex;folder=ROOT/id;(folder/'uploads').mkdir(parents=True)
    job={'id':id,'state':'uploading','files':[],'progress':0,'message':'Ready for uploads','leads':[],'errors':[]}
    JOBS[id]=job;save(job);return job

def work(job,stage,config):
    job['state']=stage;job['started']=time.time();save(job)
    import psutil
    stop=threading.Event(); process=psutil.Process(); memory={'peak_rss_bytes':0}
    def sample():
        while not stop.wait(.1):
            try:
                rss=process.memory_info().rss+sum(p.memory_info().rss for p in process.children(recursive=True) if p.is_running())
                memory['peak_rss_bytes']=max(memory['peak_rss_bytes'],rss)
            except psutil.Error:pass
    threading.Thread(target=sample,daemon=True).start()
    def progress(i,total,label):
        job.update(progress=round(i/max(1,total)*100),message=label);save(job)
    try:
        folder=ROOT/job['id']
        if stage=='extracting':
            paths=[folder/'uploads'/r['stored'] for r in job['files']]
            leads,attempts=extract_batch(paths,folder/'extraction',progress)
            reviewed={r.get('lead_id'):r for r in job.get('reviewed_leads',[]) if r.get('lead_id')}
            leads=[{**lead,**reviewed.get(lead.get('lead_id'),{})} for lead in leads]
            for lead in leads:
                for original in job['files']:
                    lead['source_file']=lead['source_file'].replace(original['stored'],original['name'])
            for attempt in attempts:
                for original in job['files']:
                    if attempt.get('source_file'):attempt['source_file']=attempt['source_file'].replace(original['stored'],original['name'])
            write_csv(folder/'extraction'/'leads.csv',leads,LEAD_FIELDS)
            job['leads']=leads;job['errors']=[a for a in attempts if a['status'] in ('ERROR','REVIEW_REQUIRED')]
            job['state']='review' if leads else 'error'
        else:
            result=research_batch(job['leads'],folder/'research',config.get('provider','ddgs'),config.get('endpoint',''),
                min(1000,max(1,int(config.get('budget',100)))),min(100,max(1,int(config.get('pages',30)))),progress)
            job['errors']=result['errors'];job['state']='done_with_errors' if result['errors'] else 'done'
        if stage=='extracting':
            summary=f'Extracted {len(job["leads"])} leads from {len(job["files"])} files.'
            if job['errors']:summary+=f' {len(job["errors"])} file(s) need attention: '+job['errors'][0].get('error','Review extraction issues.')
            elif not job['leads']:summary+=' No readable lead rows found; check the extracted page text.'
        else:summary='Research finished. Review results and source evidence.'
        job.update(progress=100,message=summary,elapsed_seconds=time.time()-job['started'])
    except Exception as exc:job.update(state='error',message=str(exc)[:400])
    stop.set()
    metrics={'stage':stage,'elapsed_seconds':time.time()-job['started'],**memory,
             'uploaded_bytes':sum(x['size'] for x in job['files']),
             'output_bytes':sum(p.stat().st_size for p in (ROOT/job['id']).rglob('*') if p.is_file() and 'uploads' not in p.relative_to(ROOT/job['id']).parts)}
    (ROOT/job['id']/(stage+'-performance.json')).write_text(json.dumps(metrics,indent=2))
    save(job)

def cleanup(job):
    folder=ROOT/job['id']/'uploads'
    shutil.rmtree(folder,ignore_errors=False);folder.mkdir()
    # OCR text may include the same personal data as the originals.
    shutil.rmtree(ROOT/job['id']/'extraction'/'text',ignore_errors=True)
    shutil.rmtree(ROOT/job['id']/'extraction'/'page-cache',ignore_errors=True)
    (ROOT/job['id']/'extraction'/'file-cache.json').unlink(missing_ok=True)
    job['uploads_deleted']=True;save(job)

class LocalHTTPServer(ThreadingHTTPServer):
    # Windows otherwise permits a second process to bind the same address,
    # leaving browsers connected to an older version of the application.
    allow_reuse_address=False

    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,status,data,ctype='application/json'):
        payload=json.dumps(data).encode() if ctype=='application/json' else data
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(payload)
    def do_GET(self):
        if self.headers.get('Host') not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):return self.send(403,{'error':'Local access only'})
        route=urlsplit(self.path).path
        if route=='/':
            page=(Path(__file__).parent/'interface.html').read_text(encoding='utf-8').replace('__TOKEN__',TOKEN)
            return self.send(200,page.encode(),'text/html; charset=utf-8')
        if route=='/preview.js':
            return self.send(200,(Path(__file__).parent/'preview.js').read_bytes(),'text/javascript; charset=utf-8')
        if self.headers.get('X-Token')!=TOKEN:return self.send(403,{'error':'Invalid session'})
        if route=='/batches':
            return self.send(200,{'batches':[{'id':j['id'],'state':j['state'],'count':len(j.get('leads',[])),
                'files':[f['name'] for f in j.get('files',[])],'errors':j.get('errors',[])} for j in sorted(JOBS.values(),key=lambda j:j.get('started',0),reverse=True)]})
        parts=route.strip('/').split('/')
        if len(parts)!=2 or parts[1] not in JOBS:return self.send(404,{'error':'Unknown batch'})
        job=JOBS[parts[1]]
        if parts[0]=='status':return self.send(200,job)
        if parts[0]=='texts':
            paths=sorted((ROOT/job['id']/'extraction'/'text').glob('*.txt'))
            return self.send(200,{'files':[p.name for p in paths]})
        if parts[0]=='text':
            name=parse_qs(urlsplit(self.path).query).get('name',[''])[0]
            if Path(name).name!=name or not name.endswith('.txt'):return self.send(400,{'error':'Invalid text file'})
            path=ROOT/job['id']/'extraction'/'text'/name
            return self.send(200,{'text':path.read_text(encoding='utf-8')[:200000] if path.is_file() else 'Text unavailable or deleted.'})
        if parts[0]=='results':
            if job['state'] in ('extracting','researching','uploading'):return self.send(409,{'error':'Batch is busy'})
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
                for path in (ROOT/job['id']).rglob('*'):
                    if path.is_file() and 'uploads' not in path.relative_to(ROOT/job['id']).parts and 'text' not in path.relative_to(ROOT/job['id']).parts and 'page-cache' not in path.relative_to(ROOT/job['id']).parts:
                        z.write(path,path.relative_to(ROOT/job['id']))
            return self.send(200,buf.getvalue(),'application/zip')
        return self.send(404,{'error':'Unknown route'})
    def do_POST(self):
        try:self.post()
        except Exception as exc:self.send(400,{'error':str(exc)[:400]})
    def post(self):
        if self.headers.get('X-Token')!=TOKEN:return self.send(403,{'error':'Invalid session'})
        route=urlsplit(self.path);parts=route.path.strip('/').split('/')
        if parts in (['preview'],['export']):
            length=int(self.headers.get('Content-Length','0'))
            if length<0 or length>2*1024*1024:raise ValueError('Preview settings too large')
            body=json.loads(self.rfile.read(length) or b'{}')
            ids=body.get('batches',[])
            if not isinstance(ids,list) or not ids or any(not isinstance(i,str) or i not in JOBS for i in ids):
                raise ValueError('Select at least one saved batch')
            selected=[JOBS[i] for i in dict.fromkeys(ids)]
            if any(j['state'] in ('uploading','extracting','researching') for j in selected):
                return self.send(409,{'error':'Wait for selected batches to finish processing'})
            overrides=body.get('overrides',{})
            if not isinstance(overrides,dict):raise ValueError('Invalid reviewed leads')
            for batch_id,rows in overrides.items():
                if batch_id not in ids or not isinstance(rows,list) or len(rows)>1000 or any(not isinstance(r,dict) for r in rows):
                    raise ValueError('Invalid reviewed leads; maximum 1000 rows per batch')
            result=merge_leads(selected,ROOT,overrides,str(body.get('query','')))
            if parts==['preview']:return self.send(200,result)
            fmt=body.get('format','csv')
            if fmt=='csv':return self.send(200,csv_bytes(result['rows']),'text/csv; charset=utf-8')
            if fmt=='xlsx':return self.send(200,xlsx_bytes(result['rows']),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            raise ValueError('Choose CSV or Excel format')
        if parts==['new']:return self.send(200,new_job())
        if len(parts)!=2 or parts[1] not in JOBS:return self.send(404,{'error':'Unknown batch'})
        job=JOBS[parts[1]];folder=ROOT/job['id'];action=parts[0]
        length=int(self.headers.get('Content-Length','0'))
        if length<0 or length>25*1024*1024:raise ValueError('Maximum file size is 25 MiB')
        if action=='upload':
            if job['state']!='uploading':raise ValueError('Batch no longer accepts uploads')
            name=parse_qs(route.query).get('name',['file'])[0];suffix=Path(name).suffix.lower()
            if suffix not in ('.png','.jpg','.jpeg','.webp','.tif','.tiff','.pdf','.csv'):raise ValueError('Unsupported file type')
            if len(job['files'])>=60:raise ValueError('Maximum 60 files per batch')
            if sum(x['size'] for x in job['files'])+length>300*1024*1024:raise ValueError('Batch exceeds 300 MiB')
            stored=f'{len(job["files"]):03}_{uuid.uuid4().hex[:8]}{suffix}';path=folder/'uploads'/stored
            remaining=length
            try:
                with path.open('wb') as f:
                    while remaining:
                        block=self.rfile.read(min(65536,remaining))
                        if not block:raise ValueError('Incomplete upload')
                        f.write(block);remaining-=len(block)
            except Exception:path.unlink(missing_ok=True);raise
            job['files'].append({'name':Path(name).name,'stored':stored,'size':length});save(job)
            return self.send(200,{'ok':True,'files':len(job['files'])})
        if length>2*1024*1024:raise ValueError('Settings too large')
        body=json.loads(self.rfile.read(length) or b'{}')
        if action=='review':
            if job['state'] in ('uploading','extracting','researching'):raise ValueError('Wait for processing before saving edits')
            leads=body.get('leads')
            if not isinstance(leads,list) or len(leads)>1000 or any(not isinstance(r,dict) for r in leads):raise ValueError('Maximum 1000 reviewed lead rows')
            job['leads']=[{k:str(r.get(k,''))[:4000] for k in LEAD_FIELDS} for r in leads]
            job['reviewed_leads']=job['leads']
            job['state']='review'
            write_csv(folder/'extraction'/'reviewed_leads.csv',job['leads'],LEAD_FIELDS)
            save(job)
            return self.send(200,{'ok':True,'count':len(job['leads'])})
        if action in ('extract','research'):
            with LOCK:
                if any(j['state'] in ('extracting','researching') for j in JOBS.values()):raise ValueError('Another batch is running; wait until it finishes')
                if action=='extract':
                    if not job['files'] or job.get('uploads_deleted'):raise ValueError('No uploaded copies remain')
                    stage='extracting'
                else:
                    leads=body.get('leads',job['leads'])
                    if not isinstance(leads,list) or len(leads)>1000:raise ValueError('Maximum 1000 lead rows per research run')
                    reviewed=[{k:str(r.get(k,''))[:1000] for k in LEAD_FIELDS} for r in leads if isinstance(r,dict) and any(str(r.get(k,'')).strip() for k in ('company','visible_name','email'))]
                    if not any(r['company'].strip() for r in reviewed):raise ValueError('Enter a company for at least one lead before public research. Cropped rows can still be previewed and exported.')
                    job['leads']=reviewed
                    job['reviewed_leads']=reviewed
                    write_csv(folder/'extraction'/'reviewed_leads.csv',job['leads'],LEAD_FIELDS)
                    stage='researching'
                job['state']=stage
                threading.Thread(target=work,args=(job,stage,body),daemon=True).start()
            return self.send(200,{'ok':True})
        if action=='delete':
            if job['state'] in ('extracting','researching','uploading'):raise ValueError('Finish processing before deleting copies')
            cleanup(job);return self.send(200,{'ok':True})
        raise ValueError('Unknown action')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8765);args=p.parse_args()
    for state in ROOT.glob('*/state.json'):
        try:
            job=json.loads(state.read_text())
            if job['state'] in ('extracting','researching'):job.update(state='error',message='Interrupted by server shutdown. Retry the stage to resume cached searches.')
            JOBS[job['id']]=job
        except (ValueError,KeyError):pass
    try:server=LocalHTTPServer(('127.0.0.1',args.port),Handler)
    except OSError as exc:
        raise SystemExit(f'Cannot start on port {args.port}: {exc}. Stop the existing app with Ctrl+C, then start again, or choose --port {args.port+1}.')
    print(f'Open http://127.0.0.1:{args.port} in your browser. Keep this terminal open.',flush=True)
    server.serve_forever()
