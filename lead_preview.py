"""Combine extracted leads and public research for preview and spreadsheet export."""
import csv
import io
import json
import re
import zipfile
from xml.etree import ElementTree as ET

FIELDS = [
    'visible_name', 'role', 'company', 'candidate_name', 'linkedin_url',
    'email', 'published_phone_candidates', 'match_status', 'person_association',
    'mailbox_status', 'dns_status', 'discovery', 'source_url', 'context', 'reason', 'email_text', 'phone_text',
    'source_file', 'page', 'extraction_status', 'batch_ids',
]


def identity(row):
    # Keep different roles and partial/full names separate rather than guessing identity.
    return tuple(' '.join(str(row.get(k, '')).split()).casefold()
                 for k in ('visible_name', 'role', 'company'))


def merge_leads(jobs, root, overrides=None, query=''):
    merged = {}
    total = 0
    for job in jobs:
        research_path = root / job['id'] / 'research' / 'research.json'
        research = json.loads(research_path.read_text()) if research_path.exists() else {}
        related = {}
        for kind in ('matches', 'contacts'):
            for record in research.get(kind, []):
                related.setdefault(identity(record), []).append(record)
        leads = (overrides or {}).get(job['id'], job.get('leads', []))
        for lead in leads:
            if not any(str(lead.get(k, '')).strip() for k in ('company','visible_name','email')):
                continue
            total += 1
            key = identity(lead)
            row = merged.setdefault(key, {k: [] for k in FIELDS})
            # Base fields come from reviewed input; research must not replace edits.
            for record in [lead] + related.get(key, []):
                for field in FIELDS:
                    if record is not lead and field in ('visible_name', 'role', 'company', 'source_file', 'page', 'extraction_status'):
                        continue
                    value = str(record.get(field, '')).strip()
                    if value and value not in row[field]:
                        row[field].append(value)
            if job['id'] not in row['batch_ids']:
                row['batch_ids'].append(job['id'])
    rows = [{k: '\n'.join(v) for k, v in row.items()} for row in merged.values()]
    needle = query.strip().lower()
    filtered = [r for r in rows if not needle or any(needle in v.lower() for v in r.values())]
    return {'fields': FIELDS, 'rows': filtered, 'total': len(rows),
            'input_count': total, 'duplicates_merged': total - len(rows)}


def csv_bytes(rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=FIELDS)
    writer.writeheader()
    # CSV has no cell types, so neutralize spreadsheet formulas in imported text.
    writer.writerows({k: "'" + v if v.lstrip().startswith(('=', '+', '-', '@')) or v.startswith(('\t', '\r', '\n')) else v
                     for k, v in row.items()} for row in rows)
    return out.getvalue().encode('utf-8-sig')


def xlsx_bytes(rows):
    """Write actual XLSX with typed text cells, without an extra dependency."""
    ns = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    ET.register_namespace('', ns)
    tag = lambda name: '{' + ns + '}' + name
    sheet = ET.Element(tag('worksheet'))
    views = ET.SubElement(sheet, tag('sheetViews'))
    view = ET.SubElement(views, tag('sheetView'), workbookViewId='0')
    ET.SubElement(view, tag('pane'), ySplit='1', topLeftCell='A2', state='frozen', activePane='bottomLeft')
    data = ET.SubElement(sheet, tag('sheetData'))
    def column(n):
        result = ''
        while n:
            n, rem = divmod(n - 1, 26)
            result = chr(65 + rem) + result
        return result
    for index, values in enumerate([FIELDS] + [[r.get(k, '') for k in FIELDS] for r in rows], 1):
        row = ET.SubElement(data, tag('row'), r=str(index))
        for col, value in enumerate(values, 1):
            cell = ET.SubElement(row, tag('c'), r=f'{column(col)}{index}', t='inlineStr')
            text = ET.SubElement(ET.SubElement(cell, tag('is')), tag('t'))
            text.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
            text.text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', '', str(value))[:32767]
    ET.SubElement(sheet, tag('autoFilter'), ref=f'A1:{column(len(FIELDS))}{len(rows)+1}')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<workbook xmlns="'+ns+'" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Merged leads" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet1.xml', ET.tostring(sheet, encoding='utf-8', xml_declaration=True))
    return buf.getvalue()
