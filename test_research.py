import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import research as r


class Tests(unittest.TestCase):
    def test_literal_contacts_only(self):
        self.assertEqual(r.public_business_emails('hello@firm.com jane@gmail.com x***@firm.com'), ['hello@firm.com'])
        self.assertEqual(r.public_business_emails('firstname at firm dot com'), [])

    def test_source_and_candidate(self):
        row = r.evidence({'company':'Plift'}, {'url':'https://www.linkedin.com/in/example',
            'title':'Glenn Example - Plift | LinkedIn','description':'Co-founder'}, '2026-11-11')
        self.assertEqual(row['candidate_name'], 'Glenn Example')
        self.assertEqual(row['status'], 'UNREVIEWED')
        self.assertEqual(row['published_email_candidates'], '')
        self.assertIsNone(r.evidence({'company':'X'}, {'url':'javascript:alert(1)'}, ''))

    def test_spreadsheet_safety(self):
        self.assertTrue(r.csv_safe('=CMD()').startswith("'"))
        self.assertTrue(r.csv_safe(' +123').startswith("'"))

    def test_cache_and_api_response(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({'web':{'results':[{'title':'Example','url':'https://example.com'}]}}).encode()
        with tempfile.TemporaryDirectory() as tmp, patch('research.urllib.request.urlopen', return_value=Response()) as mock:
            search = r.Search('test-key',Path(tmp),delay=0)
            a = search.run('company founder')
            b = search.run('company founder')
            self.assertEqual(a,b)
            self.assertEqual(mock.call_count,1)

    def test_free_workflow_and_review_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); input=root/'leads.csv'; out=root/'out'
            input.write_text('company,visible_name,role\nExample,Jane S,Founder\n')
            with patch('sys.argv',['research.py',str(input),'--out',str(out)]):
                self.assertEqual(r.main(),0)
            self.assertIn('Example',(out/'report.html').read_text())
            with (out/'searches.csv').open(encoding='utf-8-sig') as f:
                self.assertEqual(len(list(csv.DictReader(f))),4)
            (out/'review.csv').write_text('my approved work')
            with patch('sys.argv',['research.py',str(input),'--out',str(out)]):
                r.main()
            self.assertEqual((out/'review.csv').read_text(),'my approved work')

    def test_html_escapes_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'report.html'
            r.report(p,[{'company':'<script>alert(1)</script>'}],[],[],[])
            self.assertNotIn('<script>',p.read_text())


if __name__ == '__main__':
    unittest.main()
