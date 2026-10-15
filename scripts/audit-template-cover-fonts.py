#!/usr/bin/env python3
"""Read cover font requirements without changing any template package."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import posixpath
import re
import sys
from zipfile import ZipFile, is_zipfile
from lxml import etree as ET

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'aippt-studio/src'))
from aippt_studio.pptx_edit import _inspect

NS = {'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
      'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
PARSER = ET.XMLParser(resolve_entities=False, no_network=True, recover=False)

def xml(data):
    return ET.fromstring(data, PARSER)

def rels(z, part):
    name = posixpath.join(posixpath.dirname(part), '_rels', posixpath.basename(part)+'.rels')
    if name not in z.namelist(): return {}
    result = {}
    for n in xml(z.read(name)):
        if n.get('TargetMode') == 'External': continue
        target = n.get('Target', '')
        if target.startswith('/'): target = target.lstrip('/')
        else: target = posixpath.normpath(posixpath.join(posixpath.dirname(part), target))
        result[n.get('Id')] = (n.get('Type', '').rsplit('/', 1)[-1], target)
    return result

def child(n, path):
    return n.find(path, NS) if n is not None else None

def ph(shape):
    return child(shape, 'p:nvSpPr/p:nvPr/p:ph')

def shape_name(shape):
    n = child(shape, 'p:nvSpPr/p:cNvPr')
    return n.get('name', '') if n is not None else ''

def placeholders(shape, layout, master):
    ref = ph(shape)
    if ref is None: return [], None
    idx, kind = ref.get('idx', '0'), ref.get('type', 'obj')
    chain = []
    if layout is not None:
        for candidate in layout.findall('.//p:sp', NS):
            cp = ph(candidate)
            if cp is not None and cp.get('idx', '0') == idx:
                chain.append(('layout_placeholder', candidate))
                kind = cp.get('type', kind)
                break
    mkind = 'title' if kind in ('title','ctrTitle') else 'body' if kind in ('body','obj','subTitle') else kind
    if master is not None:
        for candidate in master.findall('.//p:sp', NS):
            cp = ph(candidate)
            if cp is not None and cp.get('type', 'obj') == mkind:
                chain.append(('master_placeholder', candidate))
                break
    return chain, kind

def script_for(ch):
    o = ord(ch)
    if 0x2e80 <= o <= 0x9fff or 0xac00 <= o <= 0xd7af or 0xf900 <= o <= 0xfaff or 0xff00 <= o <= 0xffef or o >= 0x20000:
        return 'ea'
    if 0x590 <= o <= 0x8ff or 0xfb1d <= o <= 0xfeff:
        return 'cs'
    return 'latin'

def theme_fonts(theme):
    result = {}
    for kind, tag in [('major','majorFont'),('minor','minorFont')]:
        node = child(theme, 'a:themeElements/a:fontScheme/a:'+tag)
        if node is None: continue
        result[kind] = {s: (child(node,'a:'+s).get('typeface','') if child(node,'a:'+s) is not None else '') for s in ('latin','ea','cs')}
        result[kind]['scripts'] = {n.get('script'): n.get('typeface') for n in node.findall('a:font', NS)}
    return result

def theme_name(token, script, language, tf):
    m = re.fullmatch(r'\+(mj|mn)-(lt|ea|cs)', token or '')
    if not m: return token
    group = tf.get('major' if m[1]=='mj' else 'minor', {})
    key = {'lt':'latin','ea':'ea','cs':'cs'}[m[2]]
    face = group.get(key)
    if face: return face
    lang = (language or '').lower()
    hint = 'Hant' if any(x in lang for x in ('tw','hk','hant')) else 'Jpan' if lang.startswith('ja') else 'Hang' if lang.startswith('ko') else 'Hans'
    return group.get('scripts',{}).get(hint) if key=='ea' else None

def requirements(root, presentation, layout, master, theme):
    tf = theme_fonts(theme)
    runs, unresolved = [], []
    layers = [('slide', root)]
    if root.get('showMasterSp', '1') not in ('0','false'):
        if layout is not None: layers.append(('layout', layout))
        if master is not None and (layout is None or layout.get('showMasterSp','1') not in ('0','false')):
            layers.append(('master', master))
    for layer, owner in layers:
        shapes = owner.findall('.//p:sp', NS)
        for shape in shapes:
            identity = child(shape, 'p:nvSpPr/p:cNvPr')
            if identity is not None and identity.get('hidden') in ('1','true'): continue
            if layer != 'slide' and ph(shape) is not None: continue
            body = child(shape, 'p:txBody')
            if body is None: continue
            chain, kind = placeholders(shape, layout, master) if layer=='slide' else ([], None)
            style = 'titleStyle' if kind in ('title','ctrTitle') else 'bodyStyle' if kind in ('body','obj','subTitle') else 'otherStyle'
            for para in body.findall('a:p', NS):
                pp = child(para,'a:pPr')
                level = min(8,max(0,int(pp.get('lvl','0')))) if pp is not None else 0
                for run in para:
                    if ET.QName(run).localname not in ('r','fld'): continue
                    text = ''.join(run.xpath('./a:t/text()', namespaces=NS))
                    if not text.strip(): continue
                    rp = child(run,'a:rPr')
                    candidates = [('run',rp),('paragraph',child(para,'a:pPr/a:defRPr')),
                                  ('shape_level',child(body,f'a:lstStyle/a:lvl{level+1}pPr/a:defRPr')),
                                  ('shape_default',child(body,'a:lstStyle/a:defPPr/a:defRPr'))]
                    for label, candidate in chain:
                        cb = child(candidate,'p:txBody')
                        candidates += [(label+'_paragraph',child(cb,'a:p/a:pPr/a:defRPr')),
                                       (label+'_level',child(cb,f'a:lstStyle/a:lvl{level+1}pPr/a:defRPr'))]
                    fontref = child(shape,'p:style/a:fontRef')
                    if fontref is not None and fontref.get('idx') in ('major','minor'):
                        candidates.append(('shape_fontref',fontref))
                    candidates += [('master_textstyle',child(master,f'p:txStyles/p:{style}/a:lvl{level+1}pPr/a:defRPr')),
                                   ('presentation_default',child(presentation,f'p:defaultTextStyle/a:lvl{level+1}pPr/a:defRPr'))]
                    language = next((n.get('lang') for _,n in candidates if n is not None and n.get('lang')), '')
                    for script in sorted({script_for(c) for c in text if not c.isspace()}):
                        face, origin = None, None
                        for label, n in candidates:
                            if n is None: continue
                            if label=='shape_fontref': raw = ('+mj-' if n.get('idx')=='major' else '+mn-')+{'latin':'lt','ea':'ea','cs':'cs'}[script]
                            else:
                                f = child(n,'a:'+script)
                                raw = f.get('typeface') if f is not None else None
                            if raw:
                                resolved = theme_name(raw,script,language,tf)
                                if resolved: face,origin=resolved,label; break
                        if not face:
                            face = theme_name('+mn-'+{'latin':'lt','ea':'ea','cs':'cs'}[script],script,language,tf)
                            origin = 'theme_default' if face else 'unresolved'
                        sample = ''.join(c for c in text if not c.isspace() and script_for(c)==script)
                        entry = {'font':face,'script':script,'text':sample,'shape':shape_name(shape),'layer':layer,'origin':origin,
                                 'bold':rp.get('b') if rp is not None else None,'italic':rp.get('i') if rp is not None else None}
                        (runs if face else unresolved).append(entry)
        # Native table text is uncommon on covers; record all explicit fonts plus theme defaults.
        for body in owner.findall('.//a:tc/a:txBody', NS):
            text = ''.join(body.xpath('.//a:t/text()', namespaces=NS))
            if not text.strip(): continue
            for script in sorted({script_for(c) for c in text if not c.isspace()}):
                names = body.xpath('.//a:'+script+'/@typeface', namespaces=NS)
                names = [theme_name(n,script,'',tf) for n in names if n]
                names = [n for n in names if n] or [theme_name('+mn-'+{'latin':'lt','ea':'ea','cs':'cs'}[script],script,'',tf)]
                for name in set(names):
                    entry={'font':name,'script':script,'text':text,'shape':'table','layer':layer,'origin':'table_font_or_theme'}
                    (runs if name else unresolved).append(entry)
    return runs, unresolved

def cover(path, original=None, source_sha=None):
    with ZipFile(path) as z:
        presentation = xml(z.read('ppt/presentation.xml'))
        pr = rels(z,'ppt/presentation.xml')
        slide_ids = presentation.findall('p:sldIdLst/p:sldId',NS)
        if not slide_ids: raise ValueError('no slides')
        parts = {'ppt/presentation.xml':z.read('ppt/presentation.xml'),
                 'ppt/_rels/presentation.xml.rels':z.read('ppt/_rels/presentation.xml.rels')}
        index, name, root = None,None,None
        for i,sid in enumerate(slide_ids,1):
            rid = sid.get('{'+NS['r']+'}id')
            name=pr[rid][1]
            data=z.read(name); root=xml(data)
            if root.get('show','1') not in ('0','false'): index=i; parts[name]=data; break
        if index is None: raise ValueError('no visible slides')
        # Studio validates every referenced slide identity before applying its page filter.
        # Empty non-target placeholders preserve that validation without decompressing media/pages.
        for sid in slide_ids:
            rid = sid.get('{'+NS['r']+'}id')
            target = pr[rid][1]
            if target not in z.namelist(): raise ValueError('missing source slide part')
            parts.setdefault(target,b'')
        # Reuse Studio's object inspector on XML-only parts; media bytes need not be loaded.
        inspected = _inspect(parts,source_sha or 'read-only-cover-xml',slides=[index])
        chain=[]; current=name
        for reltype in ('slideLayout','slideMaster','theme'):
            matches=[target for kind,target in rels(z,current).values() if kind==reltype]
            if not matches: chain.append(None); continue
            current=matches[0]; chain.append(xml(z.read(current)))
        layout,master,theme=chain
        runs,unresolved=requirements(root,presentation,layout,master,theme)
        byfont={}
        for run in runs:
            f=run['font']; item=byfont.setdefault(f,{'name':f,'characters':set(),'run_count':0,'samples':[]})
            item['characters'].update(run['text']); item['run_count']+=1
            if len(item['samples'])<4: item['samples'].append({k:v for k,v in run.items() if k!='font'})
        for item in byfont.values(): item['characters']=''.join(sorted(item['characters']))
        orig=original or path; st=orig.stat()
        return {'path':str(orig),'inspected_path':str(path),'source_bytes':st.st_size,'source_mtime_ns':st.st_mtime_ns,
                'legacy_observation':original is not None,'source_sha256':source_sha,
                'cover_slide':index,'cover_xml_sha256':hashlib.sha256(parts[name]).hexdigest(),
                'object_count':len(inspected['slides'][0]['objects']),'fonts':list(byfont.values()),
                'unresolved_runs':unresolved,'cover_text':'\n'.join(o['text'] for o in inspected['slides'][0]['objects'] if o['text'])[:1600]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True);ap.add_argument('--limit',type=int);args=ap.parse_args()
    roots=[REPO/'模板库/创赛',REPO/'模板库/BB0086 大学生职业生涯规划大赛 PPT']
    diagnostic=REPO/'diagnostics/cover-font-audit-20261011'
    legacy_table_path=diagnostic/'legacy-font-tables.json'
    legacy_tables={r['sha256']:r['fonts'] for r in json.loads(legacy_table_path.read_text())['records'] if r.get('status')=='ok'} if legacy_table_path.is_file() else {}
    paths=sorted(p for root in roots for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.pptx','.ppt','.pptm','.potx','.pot','.ppsx','.ppsm','.pps') and not p.name.startswith('~$'))
    if args.limit: paths=paths[:args.limit]
    records=[];errors=[];fonts=defaultdict(lambda:{'paths':[],'characters':set(),'run_count':0})
    for i,path in enumerate(paths,1):
        try:
            inspected_path=path;original=None;sha=None
            with path.open('rb') as stream: signature=stream.read(8)
            # OLE PowerPoint files may contain a complete embedded SmartArt ZIP.
            # zipfile.is_zipfile() sees that nested archive; trust the outer signature.
            if signature == bytes.fromhex('d0cf11e0a1b11ae1') or not signature.startswith(b'PK'):
                sha=hashlib.sha256(path.read_bytes()).hexdigest()
                root=next(r for r in roots if path.is_relative_to(r))
                observation=root/'分析索引/legacy-ppt-observations'/f'{sha}.pptx.observation'
                if not observation.is_file():
                    observation=diagnostic/'legacy-conversion/output'/f'{sha}.pptx'
                if not observation.is_file():raise ValueError('legacy PPT has no hash-matched observation')
                inspected_path=observation;original=path
            result=cover(inspected_path,original,sha)
            if original is not None and sha in legacy_tables:
                original_names=legacy_tables[sha]
                folded={n.casefold() for n in original_names}
                kept=[];conversion_only=[]
                for f in result['fonts']:
                    (kept if f['name'].casefold() in folded else conversion_only).append(f)
                result['fonts']=kept
                result['original_legacy_font_table']=original_names
                result['conversion_only_fonts']=conversion_only
                result['legacy_font_name_validation']='reported cover fonts occur in original binary font table; conversion-only fonts excluded'
                result['legacy_default_font_candidates']=original_names[:1] if conversion_only else []
            records.append(result)
            for item in result['fonts']:
                f=fonts[item['name']];f['paths'].append(str(path));f['characters'].update(item['characters']);f['run_count']+=item['run_count']
        except Exception as e: errors.append({'path':str(path),'error':str(e),'type':type(e).__name__})
        if i%200==0:print(f'covers inspected {i}/{len(paths)}; errors {len(errors)}; unique fonts {len(fonts)}',flush=True)
    fontlist=[{'name':name,'characters':''.join(sorted(item['characters'])),'deck_count':len(item['paths']),
               'run_count':item['run_count'],'paths':item['paths']} for name,item in fonts.items()]
    fontlist.sort(key=lambda f:(-f['deck_count'],f['name']))
    report={'date':'2026-10-11','scope':[str(r) for r in roots],'total_files':len(paths),'parsed_files':len(records),'errors':errors,
            'unique_fonts':len(fontlist),'legacy_observations':sum(r['legacy_observation'] for r in records),
            'unresolved_run_count':sum(len(r['unresolved_runs']) for r in records),'records':records,'fonts':fontlist,
            'verification':'cover OOXML, including inherited fonts; this is not a claim that every page was viewed in native PowerPoint'}
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('total_files','parsed_files','unique_fonts','legacy_observations','unresolved_run_count')},ensure_ascii=False),flush=True)
    print('errors',len(errors),'report',args.output,flush=True)

if __name__=='__main__':main()
