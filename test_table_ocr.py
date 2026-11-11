import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import batch_engine as b
from lead_preview import merge_leads
from table_ocr import parse_table


def word(text, x, y, **extras):
    return dict(text=text, left=str(x), top=str(y), height='12', width='80', **extras)


class ContactTableTests(unittest.TestCase):
    def test_visible_emails_and_phone_follow_the_correct_rows(self):
        words = [word('NAME',100,10),word('JOB',250,10),word('COMPANY',450,10),word('EMAILS',650,10),word('PHONE',900,10),
                 word('Jane',100,50),word('CEO',250,50),word('Firm',450,50),word('jane@firm.org',650,50),word('212-555-1234',900,50),
                 word('Joe',100,100),word('CFO',250,100),word('Other',450,100),word('Access email',650,100),word('Request phone number',900,100)]
        rows,_ = parse_table(words)
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[0]['email'],'jane@firm.org')
        self.assertEqual(rows[0]['published_phone_candidates'],'212-555-1234')
        self.assertEqual(rows[0]['discovery'],'VISIBLE_IN_UPLOAD')
        self.assertEqual(rows[0]['mailbox_status'],'NOT_CHECKED')
        self.assertEqual(rows[1]['email'],'')
        self.assertEqual(rows[1]['published_phone_candidates'],'')

    def test_cropped_rows_survive_without_company_and_footer_is_ignored(self):
        words = [word('NAME',100,10),word('EMAILS',500,10),word('PHONE',800,10),
                 word('Randy',100,50),word('randy@firm.org',500,50),
                 word('Help',100,150),word('Help',800,150)]
        rows,_ = parse_table(words)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['company'],'')
        self.assertEqual(rows[0]['extraction_status'],'REVIEW_CROPPED_COLUMNS')
        with tempfile.TemporaryDirectory() as d:
            result=merge_leads([{'id':'one','leads':rows}],Path(d))
            self.assertEqual(result['rows'][0]['email'],'randy@firm.org')

    def test_clipped_email_retained_as_text_without_promoting_address(self):
        words = [word('NAME',100,10),word('JOB',250,10),word('COMPANY',450,10),word('EMAILS',650,10),
                 word('Jane',100,50),word('CEO',250,50),word('Firm',450,50),word('jane@firm.or',650,50,clipped=True)]
        rows,_ = parse_table(words)
        self.assertEqual(rows[0]['email'],'')
        self.assertEqual(rows[0]['email_text'],'jane@firm.or')
        self.assertIn('clipped',rows[0]['reason'])

    def test_missing_company_does_not_trigger_search_or_discard_contact(self):
        lead={'visible_name':'Randy','company':'','role':'','email':'randy@firm.org'}
        with tempfile.TemporaryDirectory() as d,patch.object(b.Provider,'run') as search:
            result=b.research_batch([lead],Path(d))
            search.assert_not_called()
            self.assertEqual(result['matches'][0]['match_status'],'NEEDS_COMPANY')
            self.assertEqual(result['matches'][0]['email'],'randy@firm.org')

    def test_uploaded_contact_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'leads.csv'
            p.write_text('visible_name,role,company,email\nJane,CEO,Firm,jane@firm.org\n')
            rows,_=b.extract_batch([p],Path(d)/'output')
            self.assertEqual(rows[0]['email'],'jane@firm.org')

    def test_reviewed_fields_survive_extraction_retry(self):
        with tempfile.TemporaryDirectory() as d,patch.object(app,'ROOT',Path(d)),patch.object(app,'JOBS',{}):
            job=app.new_job()
            p=Path(d)/job['id']/'uploads'/'one.csv'
            p.write_text('visible_name,role,company,email\nJane,CEO,Firm,jane@firm.org\n')
            job['files']=[dict(stored='one.csv',name='leads.csv',size=p.stat().st_size)]
            app.work(job,'extracting',{})
            job['reviewed_leads']=[dict(job['leads'][0],company='Corrected company',email='corrected@firm.org')]
            app.work(job,'extracting',{})
            self.assertEqual(job['leads'][0]['company'],'Corrected company')
            self.assertEqual(job['leads'][0]['email'],'corrected@firm.org')


class UploadedScreenshotTests(unittest.TestCase):
    def test_real_uploaded_layouts(self):
        try:b.tesseract_command()
        except RuntimeError:self.skipTest('Tesseract unavailable')
        wanted={'Screenshot 2026-11-11 114912.png','Screenshot 2026-11-11 134240.png'}
        fixtures={}
        for state in (Path(__file__).parent/'batches').glob('*/state.json'):
            job=json.loads(state.read_text())
            for f in job.get('files',[]):
                p=state.parent/'uploads'/f['stored']
                if f['name'] in wanted and p.is_file():fixtures[f['name']]=p
        if len(fixtures)!=2:self.skipTest('Original screenshot uploads unavailable')
        rows=[r for p in fixtures.values() for _,page_rows,_ in b.file_pages(p) for r in page_rows]
        self.assertEqual(len(rows),14)
        emails={r['email'] for r in rows if r.get('email')}
        self.assertEqual(emails,{'rwarnke@sartoricheese.com','kritter@savorstreet.com','pgutwald@plastikon.com'})
        self.assertTrue(any('clipped' in r.get('reason','') for r in rows))


if __name__=='__main__':unittest.main()
