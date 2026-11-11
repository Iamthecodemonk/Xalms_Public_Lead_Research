"""Reproducible 60-file offline benchmark. Requires reportlab for fixture PDFs."""
import argparse,json,os,tempfile,time,threading
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
from reportlab.pdfgen import canvas
import psutil
from batch_engine import extract_batch

def run(n=60):
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);paths=[]
        try:font=ImageFont.truetype('DejaVuSans.ttf',24)
        except OSError:font=ImageFont.load_default()
        for i in range(n):
            if i%2==0:
                p=root/f'lead_{i}.pdf';c=canvas.Canvas(str(p));c.drawString(40,700,f'Person{i} Example | Founder | Company{i} Foods');c.save()
            else:
                p=root/f'lead_{i}.png';im=Image.new('RGB',(1350,230),'white');d=ImageDraw.Draw(im)
                for x,t in [(35,'NAME'),(480,'JOB TITLE'),(900,'COMPANY')]:d.text((x,35),t,font=font,fill='black')
                for x,t in [(35,f'Person{i} Example'),(480,'Founder'),(900,f'Company{i} Foods')]:d.text((x,110),t,font=font,fill='black')
                im.save(p);im.close()
            paths.append(p)
        stop=threading.Event();memory=[];proc=psutil.Process()
        def sample():
            while not stop.wait(.05):
                try:memory.append(proc.memory_info().rss+sum(x.memory_info().rss for x in proc.children(recursive=True)))
                except psutil.Error:pass
        threading.Thread(target=sample,daemon=True).start()
        t=time.perf_counter();leads,attempts=extract_batch(paths,root/'out');elapsed=time.perf_counter()-t;stop.set()
        exact=sum(lead['company']==f'Company{i} Foods' for i in range(n) for lead in leads if lead['visible_name']==f'Person{i} Example')
        metrics={'exact_name_company_matches':exact,'files':n,'pdf_files':(n+1)//2,'image_files':n//2,'unique_leads':len(leads),'failed_files':sum(x['status']=='ERROR' for x in attempts),
                 'elapsed_seconds':round(elapsed,3),'peak_process_and_children_rss_mib':round(max(memory,default=0)/1024**2,2),
                 'input_mib':round(sum(p.stat().st_size for p in paths)/1024**2,3),
                 'output_mib':round(sum(p.stat().st_size for p in (root/'out').rglob('*') if p.is_file())/1024**2,3),
                 'notes':'60 unique synthetic single-page files; half selectable-text PDFs, half clean table images. Offline extraction only, no search/network. Does not predict performance of scanned multipage PDFs or arbitrary layouts.'}
        return metrics
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--files',type=int,default=60);p.add_argument('--out',default='benchmark_results.json');a=p.parse_args()
    r=run(a.files);Path(a.out).write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
