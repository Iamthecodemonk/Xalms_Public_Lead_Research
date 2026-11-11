#!/usr/bin/env python3
"""Source-first public business lead research. Python 3.10+, standard library only."""
import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def clean(value):
    return html.unescape(re.sub(r"<[^>]+>", " ", str(value or ""))).strip()


def quote(value):
    return '"' + value.replace('"', '').strip() + '"'


def queries(lead):
    company = quote(lead['company'])
    name = lead.get('visible_name', '').strip()
    # Remove explicit blur markers; never manufacture hidden letters.
    name = re.sub(r'\[.*?\]|\*+|_{2,}|-{2,}', '', name).strip()
    role = lead.get('role', '').strip()
    result = [f'{company} {name} site:linkedin.com/in/',
              f'{company} {name} {role}',
              f'{company} founder leadership team',
              f'{company} contact email phone']
    domain = lead.get('domain', '').strip()
    if domain:
        host = urllib.parse.urlsplit('https://' + domain.removeprefix('https://').removeprefix('http://')).hostname
        if not host or not re.fullmatch(r'[a-zA-Z0-9.-]+', host):
            raise ValueError('Invalid domain in input')
        result.append(f'site:{host} contact email phone')
    return list(dict.fromkeys(q.strip() for q in result))


def safe_url(url):
    try:
        u = urllib.parse.urlsplit(url)
        return url if u.scheme in ('http', 'https') and u.hostname and not u.username else ''
    except ValueError:
        return ''


def public_business_emails(text):
    # A literal occurrence is evidence of publication, NOT ownership/deliverability.
    personal = {'gmail.com', 'yahoo.com', 'outlook.com', 'hotmail.com', 'icloud.com',
                'aol.com', 'proton.me', 'protonmail.com', 'live.com', 'msn.com'}
    found = re.findall(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', text)
    return sorted({e for e in found if e.rsplit('@', 1)[1].lower() not in personal
                   and '*' not in e})


def phones(text):
    # Deliberately limited to NANP numbers, appropriate for the supplied US leads.
    pattern = r'(?<!\d)(?:\+?1[ .-]?)?(?:\([2-9]\d{2}\)|[2-9]\d{2})[ .-][2-9]\d{2}[ .-]\d{4}(?!\d)'
    return sorted(set(re.findall(pattern, text)))


def csv_safe(value):
    value = str(value or '')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def write_csv(path, rows, fields):
    with path.open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: csv_safe(row.get(k, '')) for k in fields})


class Search:
    def __init__(self, key, cache, delay=1.1):
        self.key, self.cache, self.delay = key, cache, delay
        self.last = 0
        cache.mkdir(parents=True, exist_ok=True)

    def run(self, query):
        cache_file = self.cache / (hashlib.sha256(query.encode()).hexdigest() + '.json')
        if cache_file.exists():
            return json.loads(cache_file.read_text(encoding='utf-8'))
        params = urllib.parse.urlencode({'q': query[:600], 'count': 8})
        request = urllib.request.Request('https://api.search.brave.com/res/v1/web/search?' + params,
            headers={'Accept': 'application/json', 'X-Subscription-Token': self.key})
        for attempt in range(3):
            time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    data = json.load(response)
                payload = {'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                           'results': data.get('web', {}).get('results', [])}
                temp = cache_file.with_suffix('.tmp')
                temp.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
                temp.replace(cache_file)
                return payload
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise RuntimeError(f'Search HTTP {exc.code}; check API key, quota or provider status') from None
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                raise RuntimeError('Search connection/response failure; rerun to resume cached queries') from None


def evidence(lead, result, retrieved):
    url = safe_url(result.get('url', ''))
    if not url:
        return None
    title = clean(result.get('title'))
    snippet = clean(result.get('description'))
    host = urllib.parse.urlsplit(url).hostname.lower()
    profile = (host == 'linkedin.com' or host.endswith('.linkedin.com')) and '/in/' in urllib.parse.urlsplit(url).path
    return {'company': lead['company'], 'visible_name': lead.get('visible_name', ''),
            'input_role': lead.get('role', ''),
            'candidate_name': title.split(' - ')[0].split(' | ')[0].strip() if profile else '',
            'linkedin_url': url if profile else '', 'title': title, 'snippet': snippet,
            'published_email_candidates': '; '.join(public_business_emails(snippet)),
            'published_phone_candidates': '; '.join(phones(snippet)),
            'source_url': url, 'source_date': result.get('page_age', '') or '',
            'retrieved_at': retrieved, 'status': 'UNREVIEWED',
            'notes': 'Search evidence only. Check identity, company, role date and contact ownership on source.'}


def report(path, leads, query_rows, rows, errors):
    e = lambda x: html.escape(str(x or ''), quote=True)
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
        '<title>Xalms Public Lead Research</title><style>body{font:16px system-ui;background:#f5f7f8;color:#18252a;max-width:1100px;margin:36px auto;padding:0 20px}h1{color:#087f76}section{background:white;padding:22px;border-radius:12px;margin:18px 0}article{border-top:1px solid #ddd;padding:16px 0}a{color:#087f76}small{color:#54646b}li{margin:10px 0}code{overflow-wrap:anywhere}input{padding:12px;width:95%;font:inherit}</style>',
        '<h1>Xalms Public Lead Research</h1><p>Public-source candidates for review. No guessed emails, blurred-text recovery or automated identity verification.</p>',
        '<p>Open each source before approving a match. Retrieval date does not establish a current role. Email publication does not establish deliverability.</p>']
    if errors:
        parts.append('<section><h2>Incomplete searches</h2><ul>' + ''.join('<li>'+e(x)+'</li>' for x in errors) + '</ul></section>')
    for lead in leads:
        parts.append('<section><h2>'+e(lead['company'])+'</h2><p>'+e(lead.get('visible_name'))+' · '+e(lead.get('role'))+'</p><details><summary>Targeted searches</summary><ul>')
        for q in query_rows:
            if q['company'] == lead['company']:
                parts.append('<li><a target="_blank" rel="noopener noreferrer" href="'+e(q['search_url'])+'">'+e(q['query'])+'</a></li>')
        parts.append('</ul></details>')
        found = [r for r in rows if r['company'] == lead['company']]
        if not found:
            parts.append('<p>No evidence collected. Use the searches above or enable automatic search.</p>')
        for r in found:
            parts.append('<article><strong>UNREVIEWED</strong> · <a target="_blank" rel="noopener noreferrer" href="'+e(r['source_url'])+'">'+e(r['title'])+'</a><p>'+e(r['snippet'])+'</p>')
            if r['candidate_name']:
                parts.append('<p>Possible profile name: <b>'+e(r['candidate_name'])+'</b></p>')
            if r['published_email_candidates'] or r['published_phone_candidates']:
                parts.append('<p>Literal contact candidates in snippet: <code>'+e(r['published_email_candidates'])+' '+e(r['published_phone_candidates'])+'</code></p>')
            parts.append('<small>Source date: '+e(r['source_date'] or 'Unknown')+' · Retrieved: '+e(r['retrieved_at'])+'</small></article>')
        parts.append('</section>')
    parts.append('</html>')
    path.write_text('\n'.join(parts), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path, help='CSV with company,visible_name,role,domain')
    parser.add_argument('--out', type=Path, default=Path('results'))
    parser.add_argument('--auto', action='store_true', help='Search using BRAVE_SEARCH_API_KEY')
    parser.add_argument('--max-queries', type=int, default=40, help='Budget cap; includes cached queries')
    args = parser.parse_args()
    if args.max_queries < 1:
        parser.error('--max-queries must be positive')
    with args.input.open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if 'company' not in (reader.fieldnames or []):
            parser.error('CSV must contain a company column')
        leads = [{k: (v or '').strip() for k,v in row.items() if k} for row in reader if row.get('company', '').strip()]
    key = os.environ.get('BRAVE_SEARCH_API_KEY', '')
    if args.auto and not key:
        parser.error('Set BRAVE_SEARCH_API_KEY or omit --auto for the free search-link mode')
    args.out.mkdir(parents=True, exist_ok=True)
    search = Search(key, args.out / 'cache') if args.auto else None
    query_rows, rows, errors, seen = [], [], [], set()
    used = 0
    for lead in leads:
        for query in queries(lead):
            query_rows.append({'company':lead['company'], 'query':query,
                'search_url':'https://www.google.com/search?' + urllib.parse.urlencode({'q':query})})
            if search is None:
                continue
            if used >= args.max_queries:
                errors.append(f"Budget reached: skipped query for {lead['company']}: {query}")
                continue
            used += 1
            print(f"[{used}/{args.max_queries}] Searching {lead['company']}", flush=True)
            try:
                payload = search.run(query)
                for result in payload['results']:
                    row = evidence(lead, result, payload['retrieved_at'])
                    if row and (lead['company'], row['source_url']) not in seen:
                        seen.add((lead['company'], row['source_url']))
                        rows.append(row)
            except RuntimeError as exc:
                errors.append(f"{lead['company']}: {exc}")
    fields = ['company','visible_name','input_role','candidate_name','linkedin_url','title','snippet',
        'published_email_candidates','published_phone_candidates','source_url','source_date','retrieved_at','status','notes']
    write_csv(args.out/'evidence.csv', rows, fields)
    write_csv(args.out/'searches.csv', query_rows, ['company','query','search_url'])
    if not (args.out/'review.csv').exists():
        write_csv(args.out/'review.csv', [{'company':x['company'], 'visible_name':x.get('visible_name',''),
        'identity_status':'UNKNOWN', 'role_status':'UNKNOWN', 'contact_status':'UNKNOWN'} for x in leads],
        ['company','visible_name','approved_full_name','approved_linkedin_url','approved_business_email',
         'approved_business_phone','identity_status','role_status','role_last_confirmed','contact_status','source_urls','notes'])
    (args.out/'run.json').write_text(json.dumps({'automatic':args.auto,'queries_attempted':used,
        'evidence_count':len(rows),'errors':errors},indent=2), encoding='utf-8')
    report(args.out/'report.html',leads,query_rows,rows,errors)
    print(f"Saved {args.out.resolve() / 'report.html'} ({len(rows)} evidence rows; {len(errors)} issues)")
    return 2 if errors else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        print(f'Input/output error: {exc}', file=sys.stderr)
        sys.exit(1)
