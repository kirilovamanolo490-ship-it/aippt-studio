from __future__ import annotations
import hashlib, json, re, sys, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from PIL import Image, ImageOps, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / '模板库/镶金边高端动态ppt'
OUT = LIB / '分析预览'
RENDER = OUT / '逐页图'
sys.path.insert(0, str(ROOT / 'aippt-studio' / 'src'))
from aippt_studio.local_preview import render_slide_preview

def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def slide_count(p):
    with ZipFile(p) as z:
        root=ET.fromstring(z.read('ppt/presentation.xml'))
    ns={'p':'http://schemas.openxmlformats.org/presentationml/2006/main'}
    return len(root.findall('.//p:sldId',ns))

def safe(s): return re.sub(r'[^\w.-]+','_',s, flags=re.UNICODE).strip('_')[:110] or 'deck'
def sheet(paths, deckdir, deckname, batch):
    thumb_w=400; thumb_h=225; label_h=30; cols=4; rows=(len(paths)+cols-1)//cols
    canvas=Image.new('RGB',(cols*thumb_w, rows*(thumb_h+label_h)), 'white')
    d=ImageDraw.Draw(canvas)
    for i,p in enumerate(paths):
        x=(i%cols)*thumb_w; y=(i//cols)*(thumb_h+label_h)
        try:
            with Image.open(p) as im:
                im=ImageOps.contain(im.convert('RGB'),(thumb_w,thumb_h))
                canvas.paste(im,(x,y))
            d.text((x+5,y+thumb_h+3),f'{deckname} | slide {int(p.stem):03d}',fill='black')
        except Exception as e: d.text((x+5,y+5),f'FAILED {p.name}: {e}',fill='red')
    target=deckdir/f'contact-{batch:03d}.jpg'; canvas.save(target,quality=88,optimize=True); return target

def process(idx, p, total):
        rel=p.relative_to(LIB).as_posix(); sid=sha(p); name=p.stem; dest=RENDER/safe(rel)
        dest.mkdir(parents=True,exist_ok=True)
        record={'index':idx,'source':rel,'source_sha256':sid,'source_bytes':p.stat().st_size,'status':'started','slide_count':None,'rendered':0,'failed_slides':[],'contact_sheets':[],'visual_review':'pending','animation_review_boundary':'static preview only; animation start/end states and playback not evidenced'}
        try:
            n=slide_count(p); record['slide_count']=n
            for slide in range(1,n+1):
                png=dest/f'{slide:04d}.png'; receipt=png.with_suffix('.preview.json')
                if png.exists() and receipt.exists():
                    try:
                        old=json.loads(receipt.read_text())
                        if old.get('source_sha256')==sid and old.get('slide_number')==slide and old.get('output_sha256')==sha(png):
                            record['rendered']+=1; continue
                    except Exception: pass
                try:
                    render_slide_preview(p,slide,png,width=960,receipt_path=receipt,load_system_fonts=True)
                    record['rendered']+=1
                except Exception as e: record['failed_slides'].append({'slide':slide,'error':str(e)[:1500]})
                if slide%12==0: print(json.dumps({'progress':idx,'decks':total,'source':rel,'slides':slide,'total':n},ensure_ascii=False),flush=True)
            pngs=sorted(dest.glob('*.png'))
            for start in range(0,len(pngs),16): record['contact_sheets'].append(str(sheet(pngs[start:start+16],dest,name,start//16+1).relative_to(OUT)))
            record['status']='rendered' if record['rendered']==n else ('partial' if record['rendered'] else 'failed')
        except Exception as e:
            record['status']='unsupported'; record['error']=str(e)[:2000]
        print(json.dumps({'deck':idx,'status':record['status'],'source':rel,'slides':record['slide_count'],'rendered':record['rendered'],'failed':len(record['failed_slides'])},ensure_ascii=False),flush=True)
        return record

def main():
    files=sorted(p for p in LIB.rglob('*.pptx') if OUT not in p.parents)
    summary=[]
    # Three independent decks keep the renderer responsive without launching an unbounded batch.
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(process, idx, p, len(files)) for idx,p in enumerate(files,1)]
        for future in as_completed(futures):
            summary.append(future.result())
            summary.sort(key=lambda x:x['index'])
            (OUT/'manifest.json').write_text(json.dumps({'schema_version':1,'renderer':'Studio Office Kit local preview','approximate':True,'decks':summary},ensure_ascii=False,indent=2)+'\n')
    summary.sort(key=lambda x:x['index'])
    print(json.dumps({'complete':True,'decks':len(summary),'slides':sum(x['slide_count'] or 0 for x in summary),'rendered':sum(x['rendered'] for x in summary),'manifest':str((OUT/'manifest.json'))},ensure_ascii=False),flush=True)
if __name__=='__main__': main()
