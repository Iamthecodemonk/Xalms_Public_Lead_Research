import csv
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import app
from lead_preview import FIELDS, csv_bytes, merge_leads, xlsx_bytes


class PreviewTests(unittest.TestCase):
    def test_server_rejects_another_listener_on_same_port(self):
        server = app.LocalHTTPServer(('127.0.0.1', 0), app.Handler)
        try:
            with self.assertRaises(OSError):
                duplicate = app.LocalHTTPServer(('127.0.0.1', server.server_port), app.Handler)
                duplicate.server_close()
        finally:
            server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.lead = {'visible_name': 'Jane Doe', 'role': 'CEO', 'company': 'Café, Inc.', 'source_file': 'one.csv', 'page': '1'}
        self.jobs = [dict(id='one', state='review', leads=[self.lead], files=[]),
                     dict(id='two', state='done', leads=[dict(self.lead, source_file='two.csv')], files=[])]
        folder = self.root / 'two' / 'research'
        folder.mkdir(parents=True)
        self.contact = dict(self.lead, email='press@example.org', source_url='https://example.org/contact',
                            context='Press contact, not an assigned executive email',
                            person_association='NEEDS_REVIEW', mailbox_status='NOT_CHECKED')
        (folder / 'research.json').write_text(json.dumps({'contacts': [self.contact, self.contact],
            'matches': [dict(self.lead, candidate_name='Jane Doe', match_status='UNCERTAIN_CANDIDATE')]}))

    def test_merge_preserves_sources_and_uncertain_research(self):
        result = merge_leads(self.jobs, self.root)
        self.assertEqual((result['input_count'], result['total'], result['duplicates_merged']), (2, 1, 1))
        row = result['rows'][0]
        self.assertEqual(row['source_file'], 'one.csv\ntwo.csv')
        self.assertEqual(row['email'], 'press@example.org')
        self.assertEqual(row['batch_ids'], 'one\ntwo')
        self.assertEqual(row['match_status'], 'UNCERTAIN_CANDIDATE')
        self.assertEqual(row['person_association'], 'NEEDS_REVIEW')
        self.assertEqual(row['mailbox_status'], 'NOT_CHECKED')

    def test_distinct_people_roles_and_reviewed_edits(self):
        self.jobs[0]['leads'] += [dict(self.lead, role='CFO'), dict(self.lead, visible_name='Jane D')]
        self.assertEqual(merge_leads(self.jobs, self.root)['total'], 3)
        changed = dict(self.lead, company='Another company')
        result = merge_leads([self.jobs[1]], self.root, {'two': [changed]})
        self.assertEqual(result['rows'][0]['email'], '')
        self.assertEqual(result['rows'][0]['company'], 'Another company')
        self.assertEqual(merge_leads(self.jobs, self.root, query='press@example.org')['total'], 3)
        self.assertEqual(len(merge_leads(self.jobs, self.root, query='press@example.org')['rows']), 1)
        self.assertEqual(merge_leads(self.jobs, self.root, query='no match')['rows'], [])

    def test_csv_unicode_multiline_and_formula_safety(self):
        rows = merge_leads(self.jobs, self.root)['rows']
        rows[0]['visible_name'] = '=HYPERLINK("https://example.org")'
        payload = csv_bytes(rows)
        self.assertTrue(payload.startswith(b'\xef\xbb\xbf'))
        decoded = list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))
        self.assertEqual(decoded[0]['company'], 'Café, Inc.')
        self.assertEqual(decoded[0]['source_file'], 'one.csv\ntwo.csv')
        self.assertTrue(decoded[0]['visible_name'].startswith("'="))

    def test_xlsx_is_real_workbook_with_typed_text(self):
        rows = merge_leads(self.jobs, self.root)['rows']
        rows[0]['visible_name'] = '=1+1'
        rows[0]['context'] = 'A & B <C>\x00'
        with zipfile.ZipFile(io.BytesIO(xlsx_bytes(rows))) as book:
            for name in book.namelist():
                ET.fromstring(book.read(name))
            sheet = ET.fromstring(book.read('xl/worksheets/sheet1.xml'))
            ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
            self.assertEqual(len(sheet.findall('.//m:row', ns)), 2)
            self.assertEqual(len(sheet.findall('.//m:c', ns)), 2 * len(FIELDS))
            self.assertFalse(sheet.findall('.//m:f', ns))
            self.assertIn('=1+1', [e.text for e in sheet.findall('.//m:t', ns)])
            self.assertIsNotNone(sheet.find('m:autoFilter', ns))

    def test_http_preview_export_auth_busy_and_overrides(self):
        with patch.object(app, 'ROOT', self.root), patch.object(app, 'JOBS', {j['id']: j for j in self.jobs}):
            server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            def request(path, body=None, token=app.TOKEN):
                req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,
                    data=json.dumps(body).encode() if body is not None else None, headers={'X-Token': token})
                with urllib.request.urlopen(req) as response:
                    return response.headers, response.read()
            try:
                self.assertEqual(len(json.loads(request('/batches')[1])['batches']), 2)
                self.assertIn(b'function renderPreview', request('/preview.js')[1])
                body = {'batches': ['one', 'two']}
                self.assertEqual(json.loads(request('/preview', body)[1])['total'], 1)
                for fmt in ('csv', 'xlsx'):
                    headers, payload = request('/export', dict(body, format=fmt))
                    if fmt == 'xlsx': self.assertTrue(zipfile.is_zipfile(io.BytesIO(payload)))
                    else: self.assertEqual(len(list(csv.DictReader(io.StringIO(payload.decode('utf-8-sig'))))), 1)
                changed = dict(self.lead, visible_name='Edited name')
                result = json.loads(request('/preview', dict(body, overrides={'one': [changed]}))[1])
                self.assertEqual(result['total'], 2)
                for path, data, token, status in [('/preview', body, 'wrong', 403),
                        ('/preview', {'batches': ['missing']}, app.TOKEN, 400),
                        ('/export', dict(body, format='pdf'), app.TOKEN, 400)]:
                    with self.assertRaises(urllib.error.HTTPError) as error: request(path, data, token)
                    self.assertEqual(error.exception.code, status)
                self.jobs[0]['state'] = 'researching'
                with self.assertRaises(urllib.error.HTTPError) as error: request('/preview', body)
                self.assertEqual(error.exception.code, 409)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
