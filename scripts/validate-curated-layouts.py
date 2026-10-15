#!/usr/bin/env python3
"""Build the scoped LAY-22 and LAY-25 full-content adaptation examples.
Run through scripts/with-studio-runtime.py --maintenance -- python ...
"""
from __future__ import annotations
import hashlib,json,sys,os,tempfile
from pathlib import Path
from zipfile import ZipFile
ROOT=Path(__file__).resolve().parents[1]; LIB=ROOT/'模板库'
OUT=ROOT/'diagnostics/studio-audit-2026-10-08/layout-validation/curated-final'
VARIANTS={
 'short':{
  'LAY-22':{'title':'职业兴趣画像','interest':'兴趣类型：SCR型','profile':'S型：乐于倾听、协作与支持他人。\nC型：重视规则、流程与准确记录。\nR型：喜欢动手操作并解决具体问题。\n组合特点：服务、秩序与实践并重。','anchor':'行动方向：服务、执行与实践结合。\n在校园服务或实训项目中练习沟通、流程管理和动手排障。'},
  'LAY-25':{'title':'二、校园环境','topic':'学习资源','school':'图书馆、自习区和专业实训室为课程与项目实践提供稳定、便利的学习空间。','support_title':'成长支持','support':'导师提供阶段反馈，家庭支持我持续学习和参与校内实践。'}},
 'long':{
  'LAY-22':{'title':'职业兴趣画像','interest':'兴趣类型：SCR型','profile':'S型：善倾听协作，关注服务需要。\nC型：做事有条理，重视流程与记录。\nR型：喜欢动手实践，解决具体问题。\n组合特征：服务、规则与实践并重。\n在流程清晰的协作任务中发挥所长。\n下一步：参与志愿服务或专业实训。','anchor':'行动方向：在实践中积累服务与执行能力。\n通过校园服务或专业实训，练习沟通、记录和问题处理。'},
  'LAY-25':{'title':'二、校园环境','topic':'课程与设施','school':'图书馆、自习区和实训室支持课程学习，也为小组项目提供场地与设备。','support_title':'指导支持','support':'教师以项目反馈帮助我提升调研与表达，家人支持我持续学习和探索。'}}}
TARGETS={
 'LAY-22':{'source':'BB0086 大学生职业生涯规划大赛 PPT/资料/03、36套人物访谈通用范文/16页绘画人物访谈.pptx','sha':'0c5339cf5977ff0fc2d365d54ab8738caab19d4ed80a3c179aa7326e1de5f9d8','slide':5,'text':{'10':'page_title','22':'interest_label','11':'profile_body','6':'action_footer'},'images':{'2':{'source':'SELF','shape_id':'2','semantic':'职业兴趣雷达图；保留原图以维持S-C-R数值证据与解释一致'}}},
 'LAY-25':{'source':'BB0086 大学生职业生涯规划大赛 PPT/资料/04、行业职业规划大赛参考案例【覆盖300+专业】/专业分类2/82-空乘专业(1)/最新空中乘务专业职业生涯规划书.pptx','sha':'5075b7b1e68acf4ef3ea78d74a93c7a42fba7d0cc08c633c39e7e33a7ecf51e8','slide':15,'text':{'2':'page_title','18':'topic_label','19':'school_environment_body','16':'support_label','17':'support_body'},'images':{'4':{'source':'SELF','shape_id':'5','semantic':'校园建筑照片'},'3':{'source':'SELF','shape_id':'4','semantic':'校园建筑照片'},'5':{'source':'SELF','shape_id':'3','semantic':'校园建筑照片'}}}}

def sha(data): return hashlib.sha256(data).hexdigest()
def file_sha(p): return sha(Path(p).read_bytes())
def get_entries(z):
 from aippt_studio.motion_clone import _safe_entries
 return _safe_entries(z)
def source_part(z,entries,slide):
 from aippt_studio.motion_clone import _slide_order
 _,parts=_slide_order(z,entries); return parts[slide-1]
def extract_asset(source,slide,shape_id,dest):
 from aippt_studio.template_content_replace import _image_refs,_read_xml
 from aippt_studio.motion_clone import _safe_entries,_slide_order,_slide_objects
 with ZipFile(source) as z:
  es=_safe_entries(z); _,parts=_slide_order(z,es); part=parts[slide-1]; root=_read_xml(z,es,part); objects=_slide_objects(root)
  ref=next(r for r in _image_refs(z,es,part,root,slide) if r['shape_id']==str(shape_id))
  data=z.read(ref['media_part']); dest.parent.mkdir(parents=True,exist_ok=True); dest.write_bytes(data)
  return {'source_sha256':sha(source.read_bytes()),'slide':slide,'shape_id':str(shape_id),'media_part':ref['media_part'],'media_sha256':sha(data),'path':str(dest)}
def adjust_target_text_geometry(pptx, slide_number, shape_ids, height_emu_by_shape=None, width_emu_by_shape=None):
 """Apply explicit wrap/height to selected output text boxes only."""
 from zipfile import ZipFile, ZIP_DEFLATED
 from lxml import etree as ET
 from aippt_studio.motion_clone import _safe_entries,_slide_order
 path=Path(pptx); tmp=path.with_suffix('.adjusted.tmp')
 ns={'p':'http://schemas.openxmlformats.org/presentationml/2006/main','a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
 with ZipFile(path,'r') as zin:
  entries=_safe_entries(zin); _,parts=_slide_order(zin,entries); target=parts[slide_number-1]
  with ZipFile(tmp,'w',ZIP_DEFLATED) as zout:
   for info in zin.infolist():
    data=zin.read(info.filename)
    if info.filename==target:
     root=ET.fromstring(data)
     for sid in shape_ids:
      found=root.xpath('.//p:sp[p:nvSpPr/p:cNvPr[@id=$sid]]',namespaces=ns,sid=str(sid))
      if len(found)!=1: raise ValueError(f'expected one text shape id {sid} in {target}, found {len(found)}')
      shape=found[0]; body=shape.find('p:txBody/a:bodyPr',namespaces=ns)
      if body is None: raise ValueError(f'text shape {sid} has no bodyPr')
      body.set('wrap','square')
      height=(height_emu_by_shape or {}).get(str(sid))
      width=(width_emu_by_shape or {}).get(str(sid))
      if height or width:
       xfrm=shape.find('p:spPr/a:xfrm',namespaces=ns); ext=xfrm.find('a:ext',namespaces=ns) if xfrm is not None else None
       if ext is None: raise ValueError(f'text shape {sid} has no editable ext')
       if height: ext.set('cy',str(height))
       if width: ext.set('cx',str(width))
     data=ET.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    zout.writestr(info,data)
 os.replace(tmp,path)
 return {'slide':slide_number,'wrap_shape_ids':[str(x) for x in shape_ids],'height_emu_by_shape':height_emu_by_shape or {},'width_emu_by_shape':width_emu_by_shape or {},'text_autofit':'unchanged'}

def main():
 from aippt_studio.template_content_replace import replace_template_content,inspect_page_images
 from aippt_studio.local_preview import render_slide_preview
 from aippt_studio.motion_clone import text_sha256
 OUT.mkdir(parents=True,exist_ok=True)
 assets=OUT/'assets'; assets.mkdir(exist_ok=True)
 # Resolve source media by hash and record origin. LAY-25 cycles its own three campus photos,
 # preserving subject meaning while exercising replacement/copy-on-write safely.
 replacement_assets={}
 for lid,conf in TARGETS.items():
  src=LIB/conf['source']; assert file_sha(src)==conf['sha'],f"stale source: {src}"
  replacement_assets[lid]={}
  for target_id,asset in conf['images'].items():
   ref_source=src if asset['source']=='SELF' else LIB/asset['source']
   ref_slide=conf['slide'] if asset['source']=='SELF' else asset['slide']
   ref_id=asset['shape_id']
   path=assets/f'{lid}-{target_id}{Path(ref_source).suffix}.png'
   replacement_assets[lid][target_id]={**asset,**extract_asset(ref_source,ref_slide,ref_id,path)}
 # Render originals and candidate image-source pages for direct visual observation.
 original_renders=[]
 for lid,conf in TARGETS.items():
  src=LIB/conf['source']; p=OUT/'source-pages'/f'{lid}-source.png'; p.parent.mkdir(parents=True,exist_ok=True)
  rec=render_slide_preview(src,conf['slide'],p,receipt_path=p.with_suffix('.receipt.json'),width=1600,load_system_fonts=True)
  original_renders.append({'id':lid,'path':str(p),'sha256':sha(p.read_bytes()),'receipt':rec})
 for lid,conf in TARGETS.items():
  for aid,asset in conf['images'].items():
   if asset['source']=='SELF': continue
   src=LIB/asset['source']; p=OUT/'source-pages'/f'{lid}-image-source-{aid}.png'; p.parent.mkdir(parents=True,exist_ok=True)
   render_slide_preview(src,asset['slide'],p,receipt_path=p.with_suffix('.receipt.json'),width=1600,load_system_fonts=True)
 # Discover exact text hashes from current source, then replace every audience-facing text object on the selected page.
 from aippt_studio.motion_clone import _safe_entries,_slide_order,_read_xml,_slide_objects
 registry=json.loads((LIB/'REUSABLE-LAYOUTS.json').read_text())
 rejected={e['id']:e for e in registry['entries'] if e.get('design_review',{}).get('decision')=='reject_as_design_reference'}
 rejected={k:{'review':dict(v.get('review',{})),'design_review':dict(v['design_review'])} for k,v in rejected.items()}
 entries=[]
 for lid,conf in TARGETS.items():
  src=LIB/conf['source']; source_images=inspect_page_images(src,conf['slide']); source_image_map={x['shape_id']:x for x in source_images['objects'] if x['layer']=='slide'}
  with ZipFile(src) as z:
   es=_safe_entries(z); _,parts=_slide_order(z,es); root=_read_xml(z,es,parts[conf['slide']-1]); objects=_slide_objects(root)
   text_items=[]
   for sid,role in conf['text'].items():
    obj=objects.get(sid)
    if obj is None: raise ValueError(f'{lid} missing mapped text shape {sid}')
    text=''.join(x.text or '' for x in obj.xpath('.//a:t',namespaces={'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}))
    xfrm=obj.find('.//{http://schemas.openxmlformats.org/drawingml/2006/main}xfrm'); geo=None
    if xfrm is not None:
     off=xfrm.find('{http://schemas.openxmlformats.org/drawingml/2006/main}off'); ext=xfrm.find('{http://schemas.openxmlformats.org/drawingml/2006/main}ext')
     if off is not None and ext is not None: geo={'x':int(off.get('x')),'y':int(off.get('y')),'cx':int(ext.get('cx')),'cy':int(ext.get('cy')),'unit':'EMU'}
    text_items.append({'slide':conf['slide'],'shape_id':sid,'original_text_sha256':text_sha256(text),'slot_role':role,'source_text':text,'geometry_emu':geo})
   for sid in conf['images']:
    if sid not in source_image_map or not source_image_map[sid]['safe_for_single_image_replacement']:
     raise ValueError(f'{lid} image shape {sid} is not safe for single-image replacement')
  example={'id':lid,'status':'curated_adapted_candidate_pending_review','validation':[],'source_path':conf['source'],'source_sha256':conf['sha'],'source_slide':conf['slide'],'slot_map':[],'variants':[],'review':{'status':'pending_primary_visual_review','scope':'full page PNG review by primary agent required','acceptance':'not claimed','ready_for_automatic_application':False},'boundaries':['Internal fictional example, not client content.','Local preview render proves output and visually inspectable PNG only; PowerPoint native display remains unverified.']}
  for target in text_items:
   example['slot_map'].append({'type':'text','shape_id':target['shape_id'],'role':target['slot_role'],'source_text':target['source_text'],'source_char_count':len(target['source_text']),'original_text_sha256':target['original_text_sha256'],'geometry_emu':target['geometry_emu'],'font_policy':'preserve original size; no forced shrink','observed_text_lengths':{}})
  for sid,a in replacement_assets[lid].items():
   example['slot_map'].append({'type':'image','shape_id':sid,'role':a['semantic'],'source_path':a['source'],'source_sha256':a['source_sha256'],'source_slide':a['slide'],'source_shape_id':a['shape_id'],'source_media_part':a['media_part'],'source_media_sha256':a['media_sha256'],'source_image_path':a['path'],'target_media_sha256':source_image_map[sid]['media'][0]['media_sha256'],'crop_policy':'preserve original target picture XML crop, rotation and frame; image replacement does not alter geometry'})
  for vname,copy in VARIANTS.items():
   fields=copy[lid]
   values={'10':fields.get('title'),'22':fields.get('interest'),'11':fields.get('profile'),'6':fields.get('anchor'),'2':fields.get('title'),'18':fields.get('topic'),'19':fields.get('school'),'16':fields.get('support_title'),'17':fields.get('support')}
   text_replacements=[{'slide':conf['slide'],'shape_id':t['shape_id'],'original_text_sha256':t['original_text_sha256'],'new_text':values[t['shape_id']]} for t in text_items]
   image_replacements=[]
   for sid,a in replacement_assets[lid].items():
    image_replacements.append({'slide':conf['slide'],'shape_id':sid,'original_media_sha256':source_image_map[sid]['media'][0]['media_sha256'],'image_path':a['path']})
   variant_dir=OUT/lid/vname; variant_dir.mkdir(parents=True,exist_ok=True)
   output=variant_dir/f'{lid}-{vname}.pptx'
   receipt=replace_template_content(src,output,source_sha256=conf['sha'],text_replacements=text_replacements,image_replacements=image_replacements)
   if lid=='LAY-22': geometry_adjustment=adjust_target_text_geometry(output,conf['slide'],['11'],width_emu_by_shape={'11':int(4.0*914400)})
   else: geometry_adjustment={'slide':conf['slide'],'wrap_shape_ids':[],'geometry':'preserved from accepted v3; no additional geometry changes','text_autofit':'unchanged'}
   receipt['target_text_geometry_adjustment']=geometry_adjustment
   receipt_path=variant_dir/'replace-receipt.json'; receipt_path.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
   png=variant_dir/'full-page.png'; preview_receipt=variant_dir/'preview-receipt.json'
   render=render_slide_preview(output,conf['slide'],png,receipt_path=preview_receipt,width=1600,load_system_fonts=True)
   content=fields
   example['variants'].append({'id':vname,'status':'rendered_candidate','copy':content,'text_lengths':{k:len(val or '') for k,val in content.items()},'pptx':str(output),'pptx_sha256':file_sha(output),'png':str(png),'png_sha256':file_sha(png),'replace_receipt':str(receipt_path),'replace_receipt_sha256':file_sha(receipt_path),'render_receipt':str(preview_receipt),'render_receipt_sha256':file_sha(preview_receipt),'operations':receipt.get('operations'),'images':receipt.get('image_replacements'),'render_backend':'officekit'})
   example['validation'].append(example['variants'][-1])
   role_to_key={'page_title':'title','interest_label':'interest','profile_body':'profile','action_footer':'anchor','topic_label':'topic','school_environment_body':'school','support_label':'support_title','support_body':'support'}
   for slot in example['slot_map']:
    if slot['type']=='text':
     key=role_to_key[slot['role']]
     slot['observed_text_lengths'][vname]=len(fields[key])
  # Explicit capacity checks are observational and can be revised after the actual PNG review.
  example['capacity_policy']={'text_autofit':'unchanged; never shrink to force fit','long_copy_policy':'if copy exceeds the largest tested slot length or clips/overlaps in full-page review, reject this variant and split supporting explanation to a neighboring page; do not compress all copy','limits':'observed successful lengths are sample bounds, not universal maxima; inspect full-page PNG'}
  for slot in example['slot_map']:
   if slot['type']=='text':
    vals=slot['observed_text_lengths']
    example['capacity_policy'].setdefault('per_text_slot',{})[slot['shape_id']]={'role':slot['role'],'geometry_emu':slot['geometry_emu'],'tested_lengths_chars':vals,'largest_successfully_rendered_chars':max(vals.values()) if vals else None,'font_policy':'source size retained','beyond_tested_length':'shorten or split supporting explanation to an adjacent page'}
  entries.append(example)
 # Update only the two existing IDs, preserving all other 28 sample rows and review state.
 byid={e['id']:e for e in entries}
 for i,e in enumerate(registry['entries']):
  if e['id'] in byid: registry['entries'][i].update(byid[e['id']])
 negative={'LAY-03':'主标题与源标题重叠且黑字落在暗底；负责人小字槽不能承载页面主张。','LAY-04':'长版覆盖来源元信息。','LAY-06':'替换奖状证据图为装饰示意图，破坏证据含义。','LAY-07':'需保留方法/产品图语义；替换成色块破坏图像关系。','LAY-08':'只改了小节描述，未改整页核心内容。','LAY-09':'短长版均出现竖向异常或页面左侧溢出。','LAY-10':'原三列价格页需按三列字段映射；当前只换副标题和装饰对象。','LAY-14':'短长版均出现竖向异常或页面左侧溢出。','LAY-15':'短长版均出现竖向异常或页面左侧溢出。','LAY-18':'长版压住主标题。','LAY-19':'年终总结封面与职规主题不匹配，替换图还遮挡标题。','LAY-20':'长版压住大学生主题主标题。','LAY-22':'长版与下方原正文挤压；需整段适配。','LAY-24':'长副标题拥挤，中图破坏三图证据关系。','LAY-25':'下方整栏短句可用；长文字越过内容边界。','LAY-29':'替换作用不可辨，不能作为有效示例。'}
 for i,e in enumerate(registry['entries']):
  e['review_history']=e.get('review_history',[])
  if e['id'] in negative:
   old={'reviewer':'primary_agent','status':'stress_test_failed_or_scope_limited','scope':'32张第一轮槽压力试验联系表','finding':negative[e['id']]}
   if old not in e['review_history']: e['review_history'].append(old)
   if e['id'] not in byid:
    e['status']='stress_test_failed_or_scope_limited'
    e['review']=old
   else:
    e['review']=byid[e['id']]['review']
  elif e['id'] not in byid:
   e['review']=e.get('review') or {'status':'candidate_unverified','finding':e.get('validation_error','first-round replacement pair not safely available')}
  for v in e.get('validation',[]):
   cp=v.get('candidate')
   if cp and not Path(cp).is_file():
    v['candidate_sha256']=v.get('candidate_sha256'); v['candidate']=None; v['candidate_retention']='not_retained_temporary_output'
 for i,e in enumerate(registry['entries']):
  if e['id'] in byid:
   prior=registry['entries'][i]
   history=prior.get('review_history',[])
   registry['entries'][i].update(byid[e['id']]); registry['entries'][i]['review_history']=history
   registry['entries'][i]['review']=byid[e['id']]['review']
 for e in registry['entries']:
  if e['id'] in rejected:
   e.update(rejected[e['id']]); e['status']='user_rejected_as_design_reference'
 outreg=LIB/'REUSABLE-LAYOUTS.json'; outreg.write_text(json.dumps(registry,ensure_ascii=False,indent=2)+'\n')
 summary={'status':'curated_adapted_candidates_pending_primary_visual_review','entries':[{'id':e['id'],'variants':[{'id':v['id'],'pptx':v['pptx'],'png':v['png']} for v in e['variants']]} for e in entries],'registry_sha256':file_sha(outreg),'source_page_renders':original_renders,'deliverable_root':str(OUT)}
 (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
