"""Bounded batch OCR, editable lead extraction, and iterative public research."""
import csv, hashlib, io, json, re, shutil, subprocess, time, urllib.parse, urllib.request, os
from pathlib import Path
from collections import defaultdict
from PIL import Image, ImageOps
from table_ocr import extract_words, parse_table, word_lines
from research import clean, queries, safe_url, write_csv, phones
from source_audit import fetch, extract, email_occurrences, check_domain, now

LEAD_FIELDS=['visible_name','role','company','source_file','page','extraction_status',
    'email','published_phone_candidates','discovery','person_association','mailbox_status',
    'dns_status','context','email_text','phone_text','reason','lead_id']
EXTRACTOR_VERSION=3

def tesseract_command():
    configured=os.environ.get('TESSERACT_CMD')
    if configured:
        if Path(configured).is_file():return configured
        raise RuntimeError('TESSERACT_CMD does not point to a Tesseract executable.')
    command=shutil.which('tesseract')
    if command:return command
    for base in (os.environ.get('ProgramFiles'),os.environ.get('LOCALAPPDATA')):
        if not base:continue
        for relative in ('Tesseract-OCR/tesseract.exe','Programs/Tesseract-OCR/tesseract.exe'):
            candidate=Path(base)/relative
            if candidate.is_file():return str(candidate)
    raise RuntimeError('Tesseract OCR is missing. Install Tesseract, then retry extraction. CSV imports work without OCR.')


def ocr(image):
    return extract_words(image,tesseract_command())


def parse_words(words):
    rows,text=parse_table(words)
    return (parse_text(text) if rows is None else rows),text


def parse_text(text):
    rows=[]
    for line in text.splitlines():
        parts=[x.strip() for x in re.split(r'\s*\|\s*|\t+| {2,}',line.strip()) if x.strip()]
        if len(parts)==3 and all(parts) and parts[0].lower() not in ('name','full name','visible_name'):
            rows.append(dict(zip(['visible_name','role','company'],parts),extraction_status='REVIEW_TEXT'))
    return rows

def file_pages(path):
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                if any(row.get(k) for k in ('company','visible_name','email')):
                    yield 1,[{**{k:row.get(k,'') for k in LEAD_FIELDS},'extraction_status':'CSV_INPUT'}],''
    elif path.suffix.lower()=='.pdf':
        import pypdfium2 as pdfium
        doc=pdfium.PdfDocument(str(path))
        try:
            if len(doc)>300:raise ValueError('PDF exceeds 300 pages')
            for i in range(len(doc)):
                page=doc[i]
                try:
                    tp=page.get_textpage();text=tp.get_text_range();tp.close()
                    rows=parse_text(text)
                    if not rows:
                        scale=min(2,3500/max(page.get_size()))
                        bitmap=page.render(scale=scale)
                        im=bitmap.to_pil().copy();bitmap.close()
                        rows,ocr_text=parse_words(ocr(im));im.close()
                        text=text+'\n'+ocr_text
                    yield i+1,rows,text
                finally:page.close()
        finally:doc.close()
    else:
        with Image.open(path) as im:
            rows,text=parse_words(ocr(im))
        yield 1,rows,text

def extract_batch(paths,out,progress=lambda *a:None):
    out.mkdir(parents=True,exist_ok=True);(out/'text').mkdir(exist_ok=True)
    leads=[];attempts=[];seen={};start=time.perf_counter()
    cache_path=out/'file-cache.json'
    cache=json.loads(cache_path.read_text()) if cache_path.exists() else {}
    (out/'page-cache').mkdir(exist_ok=True)
    for index,path in enumerate(paths):
        progress(index,len(paths),path.name)
        attempt={'source_file':path.name,'status':'OK','pages':0,'rows':0}
        try:
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            cached=out/'page-cache'/(digest+'.json')
            pages=json.loads(cached.read_text()) if cache.get(digest)==EXTRACTOR_VERSION and cached.exists() else None
            successful=[]
            for page,rows,text in (pages if pages is not None else file_pages(path)):
                text=text[:200000]
                successful.append((page,rows,text))
                attempt['pages']=max(attempt['pages'],page)
                (out/'text'/f'{index:03}_{page}.txt').write_text(text[:2_000_000],encoding='utf-8')
                for row_index,row in enumerate(rows):
                    row={**row,'lead_id':hashlib.sha256(f'{digest}:{page}:{row_index}'.encode()).hexdigest()[:24]}
                    key=tuple(re.sub(r'\W+','',row.get(k,'').lower()) for k in ('visible_name','role','company'))
                    source=f'{path.name}:page {page}'
                    if key in seen:
                        existing=leads[seen[key]]
                        if source not in existing['source_file']:existing['source_file']+='; '+source
                        for field in (k for k in LEAD_FIELDS[6:] if k!='lead_id'):
                            values=list(dict.fromkeys(str(existing.get(field,'')).splitlines()+str(row.get(field,'')).splitlines()))
                            existing[field]='\n'.join(v for v in values if v)
                        continue
                    seen[key]=len(leads)
                    leads.append({**row,'source_file':source,'page':page});attempt['rows']+=1
                write_csv(out/'leads.csv',leads,LEAD_FIELDS)
            if any(rows for _,rows,_ in successful):
                cached.write_text(json.dumps(successful));cache[digest]=EXTRACTOR_VERSION
            else:
                cache.pop(digest,None);cached.unlink(missing_ok=True)
            cache_path.write_text(json.dumps(cache))
            if not any(rows for _,rows,_ in successful):
                attempt.update(status='REVIEW_REQUIRED',error='No lead rows could be read. Review the extracted page text or import a CSV with visible_name, role and company columns.')
            elif any(r.get('extraction_status')=='REVIEW_CROPPED_COLUMNS' for _,rows,_ in successful for r in rows):
                attempt.update(status='REVIEW_REQUIRED',error='Visible names and contacts were retained. Role/company columns are cropped; enter the missing company before public research.')
            elif attempt['rows']==0:attempt['status']='DUPLICATE'
        except Exception as exc:
            attempt['status']='ERROR';attempt['error']=str(exc)[:300]
        attempts.append(attempt)
        (out/'extraction.json').write_text(json.dumps({'files':attempts,'elapsed_seconds':time.perf_counter()-start},indent=2))
    write_csv(out/'leads.csv',leads,LEAD_FIELDS)
    return leads,attempts

class Provider:
    def __init__(self,folder,kind='ddgs',endpoint='',max_queries=100):
        self.folder=folder;folder.mkdir(exist_ok=True,parents=True)
        self.kind=kind;self.endpoint=endpoint;self.max=max_queries;self.calls=0;self.last=0
    def run(self,q):
        key=hashlib.sha256((self.kind+self.endpoint+q).encode()).hexdigest()
        path=self.folder/(key+'.json')
        if path.exists() and time.time()-path.stat().st_mtime<86400:return json.loads(path.read_text())
        if self.calls>=self.max:raise RuntimeError('Batch search budget reached; increase budget and retry research to resume.')
        self.calls+=1;time.sleep(max(0,1.2-(time.monotonic()-self.last)));self.last=time.monotonic()
        if self.kind=='searxng':
            u=urllib.parse.urlsplit(self.endpoint)
            if u.scheme not in ('http','https') or not u.hostname or u.username:raise ValueError('Enter your SearXNG HTTP(S) base URL')
            url=self.endpoint.rstrip('/')+'/search?'+urllib.parse.urlencode({'q':q,'format':'json'})
            with urllib.request.urlopen(url,timeout=25) as f:d=json.load(f)
            results=[{'title':r.get('title',''),'url':r.get('url',''),'snippet':r.get('content','')} for r in d.get('results',[])[:6]]
        else:
            from ddgs import DDGS
            results=[{'title':r.get('title',''),'url':r.get('href',''),'snippet':r.get('body','')} for r in DDGS(timeout=15).text(q,max_results=6,backend='duckduckgo')]
        results=[{k:clean(v) for k,v in r.items()} for r in results if safe_url(r['url'])]
        path.write_text(json.dumps(results));return results

def candidate(lead,result):
    u=urllib.parse.urlsplit(result['url']);title=clean(result['title'])
    if not (u.hostname=='linkedin.com' or (u.hostname or '').endswith('.linkedin.com')) or '/in/' not in u.path:return None
    name=re.split(r'\s[-|–]\s',title)[0].strip()
    if len(name.split())<2:return None
    first=lead.get('visible_name','').split()
    if first and first[0].lower() not in name.lower().split():return None
    tokens=[x for x in re.findall(r'\w+',lead['company'].lower()) if len(x)>2 and x not in ('company','the','inc','llc')]
    body=(title+' '+result['snippet']).lower()
    if not tokens or not any(t in body for t in tokens):return None
    return {'candidate_name':name,'linkedin_url':result['url'],'match_status':'UNCERTAIN_CANDIDATE','reason':'First visible name and company keyword appear in a public profile result; inspect role and company.'}

def research_batch(leads,out,kind='ddgs',endpoint='',budget=100,max_pages=30,progress=lambda *a:None):
    out.mkdir(parents=True,exist_ok=True)
    provider=Provider(out/'search-cache',kind,endpoint,budget)
    evidence=[];matches=[];contacts=[];errors=[];page_cache={};dns={};fetched=0
    search_links=[{'company':lead['company'],'query':q,'search_url':'https://www.google.com/search?'+urllib.parse.urlencode({'q':q})} for lead in leads if lead.get('company','').strip() for q in queries(lead)]
    write_csv(out/'search_links.csv',search_links,['company','query','search_url'])
    for i,lead in enumerate(leads):
        progress(i,len(leads),lead['company']);results={};candidates={}
        try:
            if not lead.get('company','').strip():raise ValueError('Enter the missing company before researching this cropped row. Uploaded contacts are retained.')
            # Start broad: name/company, role/company, and business email evidence.
            qs=queries(lead)
            for q in [qs[0],qs[1],f'"{lead["company"]}" contact email filetype:pdf']:
                for r in provider.run(q):
                    results[r['url']]=r
                    c=candidate(lead,r)
                    if c:candidates[c['linkedin_url']]=c
            # Follow a discovered public candidate with a more precise evidence search.
            for c in list(candidates.values())[:2]:
                q=f'"{c["candidate_name"]}" "{lead["company"]}" contact email'
                for r in provider.run(q):results[r['url']]=r
            if not candidates:
                for r in provider.run(f'"{lead["company"]}" {lead.get("role","")} founder leadership'):
                    results[r['url']]=r
                    c=candidate(lead,r)
                    if c:candidates[c['linkedin_url']]=c
            for c in candidates.values():matches.append({**lead,**c})
            if not candidates:matches.append({**lead,'match_status':'UNRESOLVED','reason':'No supported profile candidate found; check company spelling and extracted fields.'})
            inspected=0
            for r in results.values():
                evidence.append({**lead,**r,'retrieved_at':now()})
                host=(urllib.parse.urlsplit(r['url']).hostname or '').lower()
                # Inspect accessible public sources; never fetch private/profile databases.
                if any(x in host for x in ('linkedin.com','facebook.com','instagram.com','contactout.com','signalhire.com','rocketreach.co','apollo.io')):continue
                if inspected>=3:continue
                inspected+=1
                try:
                    if r['url'] not in page_cache:
                        if fetched>=max_pages:raise RuntimeError('Source-page budget reached')
                        fetched+=1
                        data,ctype,final=fetch(r['url']);found=[]
                        for page,method,text in extract(data,ctype,final):
                            for address,context in email_occurrences(text):found.append((address,context,page,method))
                            if len(found)>=100:break
                        page_cache[r['url']]=found[:100]
                    for address,context,page,method in page_cache[r['url']]:
                        domain=address.rsplit('@',1)[1].lower()
                        if domain not in dns:dns[domain]=check_domain(domain)
                        contacts.append({**lead,'email':address,'context':context,'page_on_source':page,'source_url':r['url'],
                          'published_phone_candidates':'; '.join(phones(context)),'discovery':'PUBLISHED_ON_SOURCE','person_association':'NEEDS_REVIEW','mailbox_status':'NOT_CHECKED',**dns[domain]})
                except Exception as exc:errors.append({'company':lead['company'],'stage':'source','url':r['url'],'error':str(exc)[:200]})
        except Exception as exc:
            errors.append({'company':lead['company'],'stage':'input' if not lead.get('company','').strip() else 'search','error':str(exc)[:200]})
            matches.append({**lead,'match_status':'NEEDS_COMPANY' if not lead.get('company','').strip() else 'UNRESOLVED_SEARCH_ERROR','reason':str(exc)[:200]})
        payload={'matches':matches,'contacts':contacts,'evidence':evidence,'errors':errors,'queries':provider.calls,'pages_fetched':fetched}
        (out/'research.json').write_text(json.dumps(payload,indent=2))
        for filename,rows in [('matches',matches),('contacts',contacts),('evidence',evidence),('errors',errors)]:
            fields=list(dict.fromkeys(k for row in rows for k in row)) or ['status']
            write_csv(out/(filename+'.csv'),rows,fields)
    return payload if leads else {'matches':[],'contacts':[],'errors':[]}
