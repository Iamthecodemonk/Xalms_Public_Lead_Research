#!/usr/bin/env python3
"""Extract published email evidence from selected HTML/PDF sources and check DNS."""
import argparse
import csv
import datetime as dt
import html
from html.parser import HTMLParser
import io
import ipaddress
import json
from pathlib import Path
import re
import socket
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from research import write_csv

LIMIT = 20 * 1024 * 1024
UA = 'XalmsPublicLeadResearch/2.0'
FREE_MAIL = {'gmail.com','yahoo.com','hotmail.com','outlook.com','juno.com','aol.com','icloud.com','proton.me','protonmail.com'}


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def normalized(text):
    # Canva and similar pages often put zero-width characters inside addresses.
    return ''.join(c for c in html.unescape(text) if unicodedata.category(c) != 'Cf')


def email_occurrences(text):
    text = normalized(text)
    pattern = r'(?<![\w.*%+\-@])[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9](?:[A-Za-z0-9.\-]*[A-Za-z0-9])?\.[A-Za-z]{2,}(?![\w@])'
    for m in re.finditer(pattern, text):
        address = m.group().rstrip('.')
        # Never treat a substring of a masked address as a published address.
        if address.lower().startswith(('example@','firstname@','first.last@')):
            continue
        yield address, ' '.join(text[max(0,m.start()-220):m.end()+220].split())


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.mailtos, self.hidden = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','noscript'):
            self.hidden += 1
        if tag in ('p','div','br','li','tr','h1','h2','h3','section'):
            self.parts.append('\n')
        if not self.hidden and tag == 'a':
            href = dict(attrs).get('href','')
            if href.lower().startswith('mailto:'):
                addresses = urllib.parse.unquote(href[7:].split('?',1)[0])
                self.mailtos.append(addresses)

    def handle_endtag(self, tag):
        if tag in ('p','div','li','tr','h1','h2','h3','section'):
            self.parts.append('\n')
        if tag in ('script','style','noscript'):
            self.hidden = max(0,self.hidden-1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract(data, content_type, url):
    if data.startswith(b'%PDF-') or 'application/pdf' in content_type:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise ValueError('PDF extraction requires: python -m pip install pypdf') from None
        reader = PdfReader(io.BytesIO(data))
        if len(reader.pages) > 500:
            raise ValueError('PDF exceeds 500-page limit')
        for i, page in enumerate(reader.pages,1):
            yield str(i), 'pdf_text', page.extract_text() or ''
    elif 'html' in content_type or content_type.startswith('text/'):
        charset = re.search(r'charset=([^;\s]+)',content_type)
        encoding = charset.group(1).strip('"\'') if charset else 'utf-8'
        try:
            text = data.decode(encoding,errors='replace')
        except LookupError:
            text = data.decode('utf-8',errors='replace')
        page = Page(); page.feed(text)
        yield '', 'html_text', ''.join(page.parts)
        for address in page.mailtos:
            yield '', 'mailto_link', address
    else:
        raise ValueError('Unsupported content type; expected HTML, text or PDF')


def public_url(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ('https','http') or not u.hostname or u.username or u.password:
        raise ValueError('Source must be a public HTTP(S) URL')
    if u.port not in (None,80,443):
        raise ValueError('Nonstandard source port blocked')
    try:
        addresses=[r[4][0] for r in socket.getaddrinfo(u.hostname,u.port or (443 if u.scheme=='https' else 80),type=socket.SOCK_STREAM)]
    except socket.gaierror:
        # HTTP proxies may resolve remote hosts even when local DNS is unavailable.
        addresses=[]
        for kind,number in [('A',1),('AAAA',28)]:
            answer,_=dns_query(u.hostname,kind)
            addresses.extend(r.get('data','') for r in answer.get('Answer',[]) if r.get('type')==number)
        if not addresses:
            raise ValueError('Source hostname could not be resolved') from None
    for address in addresses:
        if not ipaddress.ip_address(address).is_global:
            raise ValueError('Private/local source address blocked')
    return url


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def fetch(url):
    public_url(url)
    request = urllib.request.Request(url,headers={'User-Agent':UA,'Accept':'text/html,application/pdf,text/plain'})
    with urllib.request.build_opener(CheckedRedirect()).open(request,timeout=25) as response:
        data = response.read(LIMIT+1)
        if len(data)>LIMIT:
            raise ValueError('Source exceeds 20 MiB limit')
        return data,response.headers.get('Content-Type',''),response.geturl()


def dns_query(domain, record):
    errors=[]
    for base in ('https://dns.google/resolve','https://cloudflare-dns.com/dns-query'):
        url = base+'?'+urllib.parse.urlencode({'name':domain,'type':record})
        try:
            request=urllib.request.Request(url,headers={'Accept':'application/dns-json','User-Agent':UA})
            with urllib.request.urlopen(request,timeout=10) as response:
                payload=json.load(response)
            if payload.get('Status') not in (0,3):
                errors.append('DNS server inconclusive')
                continue
            return payload,urllib.parse.urlsplit(base).hostname
        except (OSError,ValueError):
            errors.append('DNS request failed')
    raise ValueError('; '.join(errors))


def check_domain(domain, query=dns_query):
    result={'dns_checked_at':now(),'dns_status':'UNKNOWN','dns_records':'','dns_resolver':'','dns_note':''}
    try:
        payload,resolver=query(domain,'MX')
        result['dns_resolver']=resolver
        status=payload.get('Status')
        if status==3:
            result['dns_status']='DOMAIN_NOT_FOUND'
            return result
        if status!=0:
            return result
        mx=[x.get('data','') for x in payload.get('Answer',[]) if x.get('type')==15]
        if mx:
            result['dns_records']='; '.join(mx)
            if any(re.fullmatch(r'\d+\s+\.',x.strip()) for x in mx):
                result['dns_status']='NULL_MX_NO_MAIL'
            else:
                result['dns_status']='MX_PRESENT'
            return result
        # RFC 5321 implicit MX: absent MX is not proof of an invalid email domain.
        ips=[]
        for typ,num in [('A',1),('AAAA',28)]:
            answer,_=query(domain,typ)
            if answer.get('Status') not in (0,3):
                raise ValueError('Address lookup inconclusive')
            ips.extend(x.get('data','') for x in answer.get('Answer',[]) if x.get('type')==num)
        result['dns_status']='IMPLICIT_MX_POSSIBLE' if ips else 'NO_MAIL_ROUTE_FOUND'
        result['dns_records']='; '.join(ips)
    except (OSError,ValueError) as exc:
        result['dns_note']=str(exc)
    return result


FIELDS=['company','person_hint','email','email_kind','publication_status','person_association',
        'source_url','final_url','source_date','page','extraction_method','context','retrieved_at',
        'dns_status','dns_records','dns_checked_at','dns_resolver','dns_note','mailbox_status']


def audit(manifest, out, dns=True, delay=1):
    out.mkdir(parents=True,exist_ok=True)
    with manifest.open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not {'company','source_url'}.issubset(reader.fieldnames or []):
            raise ValueError('Manifest requires company and source_url columns')
        sources=list(reader)
    contacts,attempts,domain_cache=[],[],{}
    for source in sources:
        url=source.get('source_url','').strip()
        if not source.get('company','').strip() or not url:
            continue
        print('Reading '+source['company'],flush=True)
        attempt={'company':source['company'],'source_url':url,'status':'ERROR','retrieved_at':now(),'note':''}
        try:
            local=source.get('local_path','').strip()
            if local:
                p=(manifest.parent/local).resolve()
                if not p.is_relative_to(manifest.parent.resolve()):
                    raise ValueError('Local source must be inside manifest folder')
                if p.stat().st_size>LIMIT: raise ValueError('Local source exceeds 20 MiB')
                data=p.read_bytes();ctype='application/pdf' if data.startswith(b'%PDF-') else 'text/html';final=url
                attempt['note']='Local copy supplied by user; source URL is provenance, not a live fetch.'
            else:
                data,ctype,final=fetch(url)
            domains={x.strip().lower() for x in source.get('allowed_email_domains','').split(';') if x.strip()}
            seen=set(); count=0
            for page,method,text in extract(data,ctype,final):
                for address,context in email_occurrences(text):
                    domain=address.rsplit('@',1)[1].lower()
                    if domains and domain not in domains: continue
                    identity=(address.lower(),page)
                    if identity in seen: continue
                    seen.add(identity)
                    if domain not in domain_cache:
                        domain_cache[domain]=check_domain(domain) if dns else {'dns_status':'NOT_CHECKED'}
                    row={'company':source['company'],'person_hint':source.get('person',''),
                         'email':address,'email_kind':'FREE_MAIL_REVIEW_BUSINESS_CONTEXT' if domain in FREE_MAIL else 'DOMAIN_EMAIL',
                         'publication_status':'FOUND_IN_LOCAL_COPY' if local else 'FOUND_ON_SOURCE',
                         'person_association':'NEEDS_REVIEW','source_url':url,'final_url':final,
                         'source_date':source.get('source_date',''),'page':page,'extraction_method':method,
                         'context':context,'retrieved_at':attempt['retrieved_at'],'mailbox_status':'NOT_CHECKED',
                         **domain_cache[domain]}
                    contacts.append(row); count+=1
            attempt['status']='OK' if count else 'NO_EMAIL_FOUND'
            attempt['email_count']=count
            if count==0:
                attempt['note']+=' No literal emails extracted. Scans or JavaScript-only pages may need manual review.'
        except Exception as exc:
            attempt['note']=type(exc).__name__+': '+str(exc)[:250]
        attempts.append(attempt)
        # Save checkpoints, including completed work if a subsequent source fails.
        write_csv(out/'contacts.csv',contacts,FIELDS)
        write_csv(out/'sources.csv',attempts,['company','source_url','status','email_count','retrieved_at','note'])
        (out/'audit.json').write_text(json.dumps({'sources':attempts,'contacts':contacts},ensure_ascii=False,indent=2),encoding='utf-8')
        if not local: time.sleep(delay)
    render(out/'email_report.html',contacts,attempts)
    errors=sum(a['status']=='ERROR' for a in attempts)
    print(f'{len(contacts)} email evidence rows; {errors} source failures. Open {out / "email_report.html"}')
    return 2 if errors else 0


def render(path,contacts,attempts):
    e=lambda x:html.escape(str(x or ''),quote=True)
    chunks=['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Email source audit</title>',
    '<style>body{font:16px system-ui;max-width:1100px;margin:35px auto;padding:20px;background:#f4f7f7;color:#18302d}article{background:white;padding:20px;margin:16px 0;border-radius:10px}a{color:#08776d}small{color:#52635e}code{overflow-wrap:anywhere}</style>',
    '<h1>Published email evidence</h1><p>Publication, person association and mailbox status are separate. DNS checks do not verify a mailbox. No messages sent.</p>']
    for r in contacts:
        u=r['source_url'];link=('<a href="'+e(u)+'" rel="noopener noreferrer">Source</a>') if urllib.parse.urlsplit(u).scheme in ('https','http') else e(u)
        chunks.append('<article><h2>'+e(r['company'])+'</h2><b>'+e(r['email'])+'</b><p>'+e(r['publication_status'])+' · '+e(r['dns_status'])+' · Mailbox: NOT_CHECKED · Person association: NEEDS_REVIEW</p><p>'+e(r['context'])+'</p>'+link+' <small>Page '+e(r['page'] or 'HTML')+' · Source date '+e(r['source_date'] or 'Unknown')+' · Retrieved '+e(r['retrieved_at'])+'</small></article>')
    chunks.append('<h2>Source attempts</h2>')
    for a in attempts:
        chunks.append('<p><b>'+e(a['company'])+'</b>: '+e(a['status'])+' '+e(a['note'])+'</p>')
    path.write_text('\n'.join(chunks)+'</html>',encoding='utf-8')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('manifest',type=Path)
    p.add_argument('--out',type=Path,default=Path('email-results'))
    p.add_argument('--no-dns',action='store_true')
    args=p.parse_args()
    try:
        return audit(args.manifest,args.out,not args.no_dns)
    except (OSError,ValueError) as exc:
        p.error(str(exc))


if __name__=='__main__':
    sys.exit(main())
