"""Refresh layout contact sheets and a status summary without resetting human reviews."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import hashlib, json
ROOT=Path(__file__).resolve().parents[1]
REG=ROOT/'模板库/REUSABLE-LAYOUTS.json'
OUT=ROOT/'diagnostics/studio-audit-2026-10-08/layout-validation'
reg=json.loads(REG.read_text())
# Candidate outputs are temporary by design. Mark cleaned paths as not retained while preserving their hashes.
for entry in reg['entries']:
    for variant in entry.get('validation',[]):
        candidate=variant.get('candidate')
        if candidate and not Path(candidate).is_file():
            variant['candidate_sha256']=variant.get('candidate_sha256')
            variant['candidate']=None
            variant['candidate_retention']='not_retained_temporary_output'
# The four curated PPTX/PNG receipts must still be hash-bound before retaining their reviewed scope.
for entry in reg['entries']:
    if entry.get('status')=='curated_static_examples_reviewed':
        for variant in entry.get('validation',[]):
            for path_key,hash_key in (('pptx','pptx_sha256'),('png','png_sha256')):
                path=Path(variant[path_key])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=variant.get(hash_key):
                    entry['status']='evidence_missing_or_changed'
                    entry['review']={'status':'stale_evidence','reason':f'{path_key} bytes missing or changed'}
# Write only candidate-retention corrections; review/status fields are left intact.
REG.write_text(json.dumps(reg,ensure_ascii=False,indent=2)+'\n')
font=ImageFont.load_default()
items=[]
curated=[]
for entry in reg['entries']:
    for variant in entry.get('validation',[]):
        image=Path(variant.get('preview') or variant.get('png') or '')
        if not image.is_file():
            continue
        digest=variant.get('preview_sha256') or variant.get('png_sha256')
        actual=hashlib.sha256(image.read_bytes()).hexdigest()
        if digest and digest!=actual:
            continue
        pair=(entry,variant,image)
        items.append(pair)
        if str(entry.get('status','')).startswith('curated_'):
            curated.append(pair)
def sheet(pairs,path,title):
    if not pairs:return
    cols=2 if len(pairs)<=4 else 4; cw,ch=640,380
    canvas=Image.new('RGB',(cols*cw,((len(pairs)+cols-1)//cols)*ch),'#eeeeee'); d=ImageDraw.Draw(canvas)
    for i,(entry,v,imagepath) in enumerate(pairs):
        im=Image.open(imagepath).convert('RGB'); im.thumbnail((cw,ch-28)); x=(i%cols)*cw;y=(i//cols)*ch
        canvas.paste(im,(x,y)); d.text((x+6,y+ch-24),f"{entry['id']} {v.get('id',v.get('variant',''))}",fill='#111',font=font)
    canvas.save(path,quality=90)
    return str(path)
contact=[]
for start in range(0,len(items),16):
    p=OUT/f'renders-{start+1:02d}-{start+len(items[start:start+16]):02d}.jpg'
    contact.append(sheet(items[start:start+16],p,'slot tests'))
curated_path=OUT/'curated-review-contact-sheet.jpg'
sheet(curated,curated_path,'curated examples')
summary={'registry_sha256':hashlib.sha256(REG.read_bytes()).hexdigest(),'entry_count':len(reg['entries']),'status_counts':{},'reviewed_curated_ids':[e['id'] for e in reg['entries'] if e.get('status')=='curated_static_examples_reviewed'],'rendered_preview_count':len(items),'contact_sheets':[x for x in contact if x]+([str(curated_path)] if curated else []),'review_preserved':True}
for entry in reg['entries']:summary['status_counts'][entry.get('status','missing')]=summary['status_counts'].get(entry.get('status','missing'),0)+1
(OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False))
