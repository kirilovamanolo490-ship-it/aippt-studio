#!/usr/bin/env python3
"""Build and render a small, source-bound set of executable PPTX layout examples.

The registry is sourced only from the already rendered contest/career samples.
Every output stays outside 模板库; source decks are immutable. Visual status is
never inferred from indexing or geometry: candidate renders require observation.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sys
from pathlib import Path
from zipfile import ZipFile
from lxml import etree
from PIL import Image, ImageDraw, ImageFont

ROOT=Path(__file__).resolve().parents[1]
LIB=ROOT/'模板库'
TMP=Path('/tmp/aippt-layout-validation-20261008')
ROLES={'cover':'封面/开场','evidence':'证据与成果','closing_or_plan':'计划/收束'}
VARIANTS=[
 {'id':'short','title':'青禾计划','body':'三年服务 12 个社区','long':False,'color':'#16836E'},
 {'id':'long','title':'青禾社区共创与成长计划','body':'通过需求访谈、课程共创与持续跟踪，三年内服务 12 个社区，形成可复制的协作机制。','long':True,'color':'#285D9B'}]

def sha(data): return hashlib.sha256(data).hexdigest()
def png(path,label,color):
 im=Image.new('RGB',(960,540),'white'); d=ImageDraw.Draw(im)
 d.rectangle((0,0,960,540),fill=color); d.rectangle((26,26,934,514),outline='white',width=4)
 d.ellipse((80,120,400,440),fill='#FFFFFF'); d.ellipse((130,170,350,390),fill=color)
 try: f=ImageFont.truetype('/System/Library/Fonts/PingFang.ttc',48)
 except Exception: f=ImageFont.load_default()
 d.text((455,180),label,fill='white',font=f); d.text((455,265),'内部替换示意图',fill='white',font=f)
 im.save(path)

def load_samples():
 out=[]
 for collection,folder in [('创赛','创赛'),('职规','BB0086 大学生职业生涯规划大赛 PPT')]:
  base=LIB/folder/'分析索引'; sample=json.loads((base/'VISUAL-SAMPLE.json').read_text())
  rows=[]
  for line in (base/'STYLE-PAGES.jsonl').read_text().splitlines():
   try: rows.append(json.loads(line))
   except json.JSONDecodeError: continue
  by={(r['source_sha256'],r['slide']):r for r in rows}
  # Use only page images already present in the reviewed contact sheets.
  for deck in sample['samples']:
   for role,slide in deck['pages'].items():
    row=by.get((deck['source_sha256'],slide))
    if not row or role not in ROLES: continue
    image=base/'静态预览'/f"{deck['source_sha256'][:12]}-{role}.png"
    if image.is_file():
     out.append({'collection':collection,'path':deck['canonical_path'],'sha256':deck['source_sha256'],'slide':slide,'role':role,'title':deck.get('page_titles',{}).get(role,''),'image':str(image),'image_sha256':sha(image.read_bytes()),'signals':deck.get('page_signals',{}).get(role,{}),'index':row})
 return out

def build_registry():
 samples=load_samples(); selected=[]
 # 5 per role per collection, spread across the existing stratified visual samples.
 for collection in ('创赛','职规'):
  for role in ROLES:
   candidates=[x for x in samples if x['collection']==collection and x['role']==role]
   # Favor editable pages with a real picture and visible text; source page images have already been inspected.
   candidates.sort(key=lambda x:(x['signals'].get('images',0)==0, x['signals'].get('text_chars',0)==0, x['sha256']))
   selected.extend(candidates[:5])
 return selected

def preserve_rejected_evaluation(item, prior):
 """Carry a rejection forward when the same source bytes and page survive a re-render."""
 if (not prior or
     (prior.get('id'),prior.get('source_sha256'),prior.get('source_slide')) !=
     (item.get('id'),item.get('source_sha256'),item.get('source_slide')) or
     prior.get('design_review',{}).get('decision')!='reject_as_design_reference'):
  return False
 for field in ('design_review','review','review_history'):
  if field in prior: item[field]=prior[field]
 item['status']=prior.get('status','user_rejected_as_design_reference')
 return True

def preserve_design_references(registry, previous):
 """Keep separately curated visual references and the user's selection metadata."""
 if not previous: return registry
 ids={e.get('id') for e in registry.get('entries',[])}
 for entry in previous.get('entries',[]):
  if entry.get('record_kind')=='design_reference' or str(entry.get('id','')).startswith('REF-'):
   if entry.get('id') not in ids:
    registry.setdefault('entries',[]).append(entry)
    ids.add(entry.get('id'))
 if 'design_selection' in previous:
  registry['design_selection']=previous['design_selection']
  registry['scope']='Technical slot experiments plus separately reviewed source-page design references. Neither is a count of finished high-quality adapted layouts; read current design_review and record_kind.'
 return registry

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--run',action='store_true',help='create two real text+image replacements and render them'); ap.add_argument('--output',type=Path,default=TMP); a=ap.parse_args()
 sys.path.insert(0,str(ROOT/'aippt-studio'/'src'))
 from aippt_studio.motion_clone import clone_motion_deck, text_sha256, _slide_order, _read_xml, _slide_objects, _image_bindings
 from aippt_studio.local_preview import render_slide_preview
 samples=build_registry()
 if len(samples)<30: raise SystemExit(f'only {len(samples)} visual samples have usable local source-page images')
 a.output.mkdir(parents=True,exist_ok=True)
 image_a=a.output/'internal-example-a.png'; image_b=a.output/'internal-example-b.png'
 png(image_a,'成长路径','#16836E'); png(image_b,'社区协作','#285D9B')
 entries=[]
 for i,s in enumerate(samples[:30],1):
  relative=s['path']; source=LIB/relative
  common={'id':f'LAY-{i:02d}','collection':s['collection'],'source_path':relative,'source_sha256':s['sha256'],'source_slide':s['slide'],'page_role':s['role'],'page_role_label':ROLES[s['role']],'source_title':s['title'],'source_preview':s['image'],'source_preview_sha256':s['image_sha256'],'use_case':{'cover':'主题明确的项目开场，保留主视觉与少量定位文案','evidence':'单一主张配图表/现场/成果证据，避免沿用源样例事实','closing_or_plan':'阶段计划或项目收束，保留清晰终点/下一步'}[s['role']],'replaceable_slots':[],'image_relation':'参照实页判断图片作为主视觉、照片证据或成果缩略图；替换图应匹配原对象裁切框与主题语义。','text_capacity_strategy':'短版为标题+短说明；长版优先删减次要解释、拆成短句或分配到相邻页，不以缩小字体挤入。','boundaries':['该页源图经过联系表抽样视觉查看；本条不代表整份来源模板的全部页面已审。','静态渲染不证明PowerPoint原生效果、动画播放或素材权利。'],'status':'candidate','validation':[]}
  if a.run:
   try:
    with ZipFile(source) as z:
     archive_entries={item.filename:item for item in z.infolist()}; _presentation,names=_slide_order(z, archive_entries); part=names[s['slide']-1]; root=_read_xml(z,archive_entries,part); objects=_slide_objects(root)
     text_choices=[]
     for oid,obj in objects.items():
      ts=obj.xpath('.//a:t',namespaces={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'})
      value=''.join((t.text or '') for t in ts)
      if len(ts)==1 and len(value.strip())>=2: text_choices.append((len(value.strip()),oid,value))
     text_choices.sort(reverse=True)
     image_refs=_image_bindings(z,archive_entries,part,s['slide'],root,objects)
     image_refs=[r for r in image_refs if r.get('media_part')]
     if not text_choices or not image_refs: raise ValueError('no editable single-run text or picture slot')
     from PIL import Image as PILImage
     import tempfile
     chosen=None
     # Test candidate slot pairs in isolated temp directories; shared master/deck media is rejected by clone_motion_deck.
     for _,shape_id,original in text_choices[:12]:
      for pic in image_refs:
       media_part=pic['media_part']; ext=Path(media_part).suffix.lower()
       if ext not in ('.jpg','.jpeg','.png'): continue
       with ZipFile(source) as zz: original_media=zz.read(media_part)
       pair_ok=True
       with tempfile.TemporaryDirectory(prefix='layout-slot-probe-') as td:
        td=Path(td)
        for variant,imgpath in zip(VARIANTS,[image_a,image_b]):
         converted=td/('replacement'+ext)
         with PILImage.open(imgpath) as im:
          if ext in ('.jpg','.jpeg'): im.convert('RGB').save(converted,format='JPEG',quality=90)
          else: im.save(converted,format='PNG')
         try:
          clone_motion_deck(source,td/(variant['id']+'.pptx'),source_sha256=s['sha256'],slides=[s['slide']],replacements=[{'slide':s['slide'],'shape_id':shape_id,'original_text_sha256':text_sha256(original),'text':variant['title']+'｜'+variant['body']}],image_replacements=[{'slide':s['slide'],'shape_id':pic['shape_id'],'original_media_sha256':sha(original_media),'path':converted}])
         except Exception: pair_ok=False; break
       if pair_ok: chosen=(shape_id,original,pic,media_part,original_media,ext); break
      if chosen: break
     if not chosen: raise ValueError('no unshared page-local picture/text object pair accepts both replacements')
     shape_id,original,pic,media_part,original_media,ext=chosen
     common['replaceable_slots']=[{'type':'text','shape_id':shape_id,'source_text':original,'source_text_sha256':text_sha256(original),'capacity_note':'可替换对象；两组真实替换渲染待整页观察。'},{'type':'image','shape_id':pic['shape_id'],'media_part':media_part,'capacity_note':'安全的页内图片；保留原图框几何、裁切及关系。'}]
     for variant,imgpath in zip(VARIANTS,[image_a,image_b]):
      converted=a.output/f"{common['id']}-{variant['id']}{ext}"
      with PILImage.open(imgpath) as im:
       if ext in ('.jpg','.jpeg'): im.convert('RGB').save(converted,format='JPEG',quality=90)
       else: im.save(converted,format='PNG')
      out=a.output/common['id']/variant['id']; out.mkdir(parents=True,exist_ok=True)
      candidate=out/'candidate.pptx'
      clone_motion_deck(source,candidate,source_sha256=s['sha256'],slides=[s['slide']],replacements=[{'slide':s['slide'],'shape_id':shape_id,'original_text_sha256':text_sha256(original),'text':variant['title']+'｜'+variant['body']}],image_replacements=[{'slide':s['slide'],'shape_id':pic['shape_id'],'original_media_sha256':sha(original_media),'path':converted}])
      preview=out/'preview.png'; receipt=out/'preview-receipt.json'
      render_slide_preview(candidate,1,preview,receipt_path=receipt,width=1280,load_system_fonts=True)
      common['validation'].append({'variant':variant['id'],'text':variant['title']+'｜'+variant['body'],'replacement_image_sha256':sha(converted.read_bytes()),'candidate':str(candidate),'candidate_sha256':sha(candidate.read_bytes()),'preview':str(preview),'preview_sha256':sha(preview.read_bytes()),'preview_receipt':str(receipt),'rendered':True,'visual_status':'needs_human_whole_page_observation','remaining_source_copy':'retained outside selected objects; slot stress test, not complete content adaptation'})
     common['status']='verified_rendered_candidate' 
   except Exception as e:
    common['status']='candidate_unverified'; common['validation_error']=f'{type(e).__name__}: {e}'
  entries.append(common)
 registry_path=LIB/'REUSABLE-LAYOUTS.json'; previous={}
 if registry_path.is_file():
  previous=json.loads(registry_path.read_text())
  old={(e.get('id'),e.get('source_sha256'),e.get('source_slide'),e.get('source_preview_sha256')):e for e in previous.get('entries',[])}
  old_source={(e.get('id'),e.get('source_sha256'),e.get('source_slide')):e for e in previous.get('entries',[])}
  for item in entries:
   key=(item.get('id'),item.get('source_sha256'),item.get('source_slide'),item.get('source_preview_sha256'))
   prior=old.get(key)
   if not prior:
    same_page=old_source.get(key[:3])
    preserve_rejected_evaluation(item,same_page)
    continue
   if prior.get('design_review',{}).get('decision')=='reject_as_design_reference':
    item.clear(); item.update(prior); continue
   # A curated adaptation is a distinct source-bound result; preserve it while its four assets still match.
   if str(prior.get('status','')).startswith('curated_'):
    variants=prior.get('validation',[])
    valid=bool(variants) and all(Path(v.get('png','')).is_file() and sha(Path(v['png']).read_bytes())==v.get('png_sha256') and Path(v.get('pptx','')).is_file() and sha(Path(v['pptx']).read_bytes())==v.get('pptx_sha256') for v in variants)
    if valid:
     item.clear(); item.update(prior); continue
    item['review']={'status':'stale_evidence','reason':'curated candidate bytes or previews changed/absent; re-review required'}
   else:
    # Review follows only the same source page and the same reviewed source-page preview bytes.
    if prior.get('review'): item['review']=prior['review']
    if prior.get('review_history'): item['review_history']=prior['review_history']
 registry={'schema':'aippt.reusable-layouts.v1','created_on':'2026-10-08','source_contact_sheets_reviewed':['模板库/创赛/分析索引/联系表/创赛-evidence-contact-sheet.jpg','模板库/BB0086 大学生职业生涯规划大赛 PPT/分析索引/联系表/career-evidence-contact-sheet.jpg'],'scope':'First 30 page-level layout examples from previously rendered sample decks. Every entry binds a source SHA and page. Rendered validation is candidate evidence; visual acceptance is recorded separately.','entries':entries}
 preserve_design_references(registry,previous)
 out=registry_path; out.write_text(json.dumps(registry,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({'registry':str(out),'entries':len(entries),'rendered':sum(x['status']=='verified_rendered_candidate' for x in entries),'candidate':sum(x['status']!='verified_rendered_candidate' for x in entries),'preview_root':str(a.output)},ensure_ascii=False))
if __name__=='__main__': main()
