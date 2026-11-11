import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import io
import source_audit as a


class SourceTests(unittest.TestCase):
    def test_html_entities_mailto_hidden_and_zero_width(self):
        data=b'<script>secret@other.com</script><p>Contact: hello&#64;firm.com</p><a href="mailto:Jane%40firm.com?subject=hi">Write</a>'
        blocks=list(a.extract(data,'text/html','https://firm.com'))
        emails=[x[0] for _,_,t in blocks for x in a.email_occurrences(t)]
        self.assertEqual(emails,['hello@firm.com','Jane@firm.com'])
        self.assertEqual(list(a.email_occurrences('Contactenos@dulcegrac\u200bia.co'))[0][0],'Contactenos@dulcegracia.co')
        self.assertEqual(list(a.email_occurrences('x***@firm.com')),[])

    def test_mail_routing_statuses(self):
        def answer(payload): return lambda *_:(payload,'test-resolver')
        self.assertEqual(a.check_domain('x.com',answer({'Status':3}))['dns_status'],'DOMAIN_NOT_FOUND')
        self.assertEqual(a.check_domain('x.com',answer({'Status':0,'Answer':[{'type':15,'data':'0 .'}]}))['dns_status'],'NULL_MX_NO_MAIL')
        self.assertEqual(a.check_domain('x.com',answer({'Status':0,'Answer':[{'type':15,'data':'10 mx.x.com.'}]}))['dns_status'],'MX_PRESENT')
        def implicit(_,kind):
            return {'Status':0,'Answer':[{'type':1,'data':'1.1.1.1'}] if kind=='A' else []},'test'
        self.assertEqual(a.check_domain('x.com',implicit)['dns_status'],'IMPLICIT_MX_POSSIBLE')
        self.assertEqual(a.check_domain('x.com',answer({'Status':2}))['dns_status'],'UNKNOWN')

    def test_pdf_extraction(self):
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            self.skipTest('PDF fixture generation requires reportlab, only for this test')
        buf=io.BytesIO();pdf=canvas.Canvas(buf)
        pdf.drawString(40,700,'Randy Example - randy@firm.com');pdf.save()
        blocks=list(a.extract(buf.getvalue(),'application/pdf','https://firm.com/directory.pdf'))
        self.assertEqual(blocks[0][0],'1')
        self.assertEqual(list(a.email_occurrences(blocks[0][2]))[0][0],'randy@firm.com')

    def test_local_workflow_filters_context_and_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'source.html').write_text('<p>Jane Smith: jane@gmail.com</p><p>Other: press@other.com</p>')
            (root/'sources.csv').write_text('company,person,source_url,allowed_email_domains,local_path\nFirm,Jane Smith,https://firm.com,gmail.com,source.html\nMissing,,https://firm.com,,missing.pdf\n')
            self.assertEqual(a.audit(root/'sources.csv',root/'out',dns=False,delay=0),2)
            with (root/'out/contacts.csv').open(encoding='utf-8-sig') as f: rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['email'],'jane@gmail.com')
            self.assertEqual(rows[0]['publication_status'],'FOUND_IN_LOCAL_COPY')
            self.assertEqual(rows[0]['person_association'],'NEEDS_REVIEW')
            self.assertEqual(rows[0]['mailbox_status'],'NOT_CHECKED')

    def test_private_source_blocked(self):
        with self.assertRaises(ValueError): a.public_url('file:///etc/passwd')
        with patch('source_audit.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',443))]):
            with self.assertRaises(ValueError): a.public_url('https://local.test/')


if __name__=='__main__':unittest.main()
