#!/usr/bin/env python3
"""Read-only integrity audit for the Creative Cup and career style indexes."""
from __future__ import annotations
import argparse, hashlib, json, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COLLECTIONS = [
    ("creative", "模板库/创赛/分析索引", "模板库/创赛"),
    ("career", "模板库/BB0086 大学生职业生涯规划大赛 PPT/分析索引", "模板库/BB0086 大学生职业生涯规划大赛 PPT"),
]

def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

JSONL_ERRORS = defaultdict(list)
def rows(path):
    """Parse strict JSON Lines. Invalid lines are recorded and never repaired."""
    out=[]
    with path.open(encoding='utf-8') as f:
        for line_no,line in enumerate(f,1):
            if not line.strip(): continue
            try: out.append(json.loads(line))
            except json.JSONDecodeError as e:
                JSONL_ERRORS[str(path.relative_to(ROOT))].append({"line":line_no,"error":e.msg,"column":e.colno})
    return out
def add(checks, name, ok, **meta): checks.append({"check":name,"ok":bool(ok),**meta})
def relpath(base, value):
    p=Path(value)
    if p.is_absolute(): return p
    # References in catalog/detail are relative to the collection root or index root.
    candidates=[ROOT/p, ROOT/'模板库'/p, base/p]
    for c in candidates:
        if c.exists(): return c
    return base/p

def audit(name,index_rel,collection_rel):
    index=ROOT/index_rel; collection=ROOT/collection_rel; checks=[]
    catalog=rows(index/'STYLE-CATALOG.jsonl'); pages=rows(index/'STYLE-PAGES.jsonl')
    for filename in ['STYLE-CATALOG.jsonl','STYLE-PAGES.jsonl']:
        rel=str((index/filename).relative_to(ROOT)); errs=JSONL_ERRORS.get(rel,[])
        add(checks,'strict_jsonl_'+filename.lower().replace('.','_'),not errs,errors=errs[:100],error_count=len(errs))
    cat_by_sha=defaultdict(list); page_by_sha=defaultdict(list)
    for r in catalog: cat_by_sha[r.get('source_sha256')].append(r)
    for r in pages: page_by_sha[r.get('source_sha256')].append(r)
    inconsistent=[k for k,v in cat_by_sha.items() if not k or len({(r.get('detail_ref'),r.get('detail_sha256'),r.get('slide_count'),r.get('status')) for r in v})!=1]
    add(checks,'catalog_unique_sha_identity_consistent',not inconsistent,rows=len(catalog),unique_sha=len(cat_by_sha),duplicate_path_rows=sum(len(v)-1 for v in cat_by_sha.values()),inconsistent_sha=inconsistent)
    add(checks,'pages_have_valid_source_sha',all(k and k in cat_by_sha for k in page_by_sha),rows=len(pages),unique_sha=len(page_by_sha),unknown_sha=[k for k in page_by_sha if not k or k not in cat_by_sha])
    catalog_hashes=set(cat_by_sha); page_hashes=set(page_by_sha)
    expected_page_hashes={h for h,rs in cat_by_sha.items() if rs[0].get('slide_count',0)>0}
    add(checks,'catalog_pages_same_source_sha_set',expected_page_hashes==page_hashes,catalog_only=sorted(expected_page_hashes-page_hashes),pages_only=sorted(page_hashes-expected_page_hashes))
    page_counts=Counter(r.get('source_sha256') for r in pages)
    mismatches=[]
    for h, records in cat_by_sha.items():
        r=records[0]; expected=r.get('slide_count'); actual=page_counts[h]
        if expected != actual: mismatches.append({'sha256':h,'catalog_slide_count':expected,'page_rows':actual})
    add(checks,'catalog_page_count_matches',not mismatches,mismatches=mismatches[:100],mismatch_count=len(mismatches))
    # Verify every unique source byte stream, including all known duplicate paths.
    source_refs={}
    for r in catalog:
        for value in dict.fromkeys([r.get('path'),r.get('canonical_path')]):
            if value: source_refs.setdefault((r['source_sha256'],value),r)
    bad=[]; verified=0
    for (expected,value),r in source_refs.items():
        p=relpath(collection,value)
        if not p.is_file(): bad.append({'path':value,'reason':'missing'}); continue
        actual=sha(p); verified+=1
        if actual != expected: bad.append({'path':value,'expected':expected,'actual':actual})
    add(checks,'source_file_sha256',not bad,paths_checked=verified,errors=bad[:100],error_count=len(bad))
    # Every detail reference is hashed once per SHA and checked against its catalog identity.
    detail_errors=[]; details=set()
    for r in catalog:
        ref=r.get('detail_ref'); expected=r.get('detail_sha256')
        if not ref or not expected: detail_errors.append({'sha256':r.get('source_sha256'),'reason':'missing_detail_identity'}); continue
        p=relpath(index,ref)
        if not p.is_file(): detail_errors.append({'ref':ref,'reason':'missing'}); continue
        key=(str(p),expected)
        if key in details: continue
        details.add(key); actual=sha(p)
        if actual!=expected: detail_errors.append({'ref':ref,'expected':expected,'actual':actual})
    add(checks,'detail_json_sha256',not detail_errors,details_checked=len(details),errors=detail_errors[:100],error_count=len(detail_errors))
    # Visual sample products: real PNG hash, bytes, preview receipt existence and matching data.
    vis=json.loads((index/'VISUAL-SAMPLE.json').read_text(encoding='utf-8')); visual_errors=[]; rendered=0
    for r in vis.get('render_results',[]):
        if r.get('status')!='rendered_advisory': continue
        rendered+=1
        for field in ('png','receipt'):
            if not r.get(field): visual_errors.append({'sample':r.get('source_sha256'),'reason':'missing_'+field}); continue
        png=relpath(index,r['png']); receipt=relpath(index,r['receipt'])
        if not png.is_file(): visual_errors.append({'png':r['png'],'reason':'missing'}); continue
        actual=sha(png)
        if actual != r.get('png_sha256'): visual_errors.append({'png':r['png'],'expected':r.get('png_sha256'),'actual':actual})
        if png.stat().st_size != r.get('bytes'): visual_errors.append({'png':r['png'],'reason':'byte_count_mismatch','expected':r.get('bytes'),'actual':png.stat().st_size})
        if not receipt.is_file(): visual_errors.append({'receipt':r['receipt'],'reason':'missing'}); continue
        try:
            d=json.loads(receipt.read_text(encoding='utf-8'))
            # Receipts are renderer-defined; check any identities they expose.
            for key,receipt_key,expected in [('png_sha256','output_sha256',r.get('png_sha256')),('source_sha256','source_sha256',r.get('source_sha256')),('slide','slide_number',r.get('slide')),('bytes','output_bytes',r.get('bytes'))]:
                if receipt_key in d and expected is not None and d[receipt_key] != expected: visual_errors.append({'receipt':r['receipt'],'field':receipt_key,'expected':expected,'actual':d[receipt_key]})
            if d.get('output_png') and Path(d['output_png']).resolve() != png.resolve(): visual_errors.append({'receipt':r['receipt'],'field':'output_png','expected':str(png.resolve()),'actual':d['output_png']})
        except Exception as e: visual_errors.append({'receipt':r['receipt'],'reason':'invalid_json','error':str(e)})
    add(checks,'visual_png_receipt_sha256',rendered>0 and not visual_errors,rendered_pages=rendered,errors=visual_errors[:100],error_count=len(visual_errors))
    # Reconcile legacy source paths and processing outcomes against the complete inventory.
    if name=='career':
        status_path=index/'legacy-近似结构/LEGACY-STYLE-STATUS.jsonl'; legacy_path=index/'legacy-近似结构/LEGACY-STYLE-CATALOG.jsonl'
        status=rows(status_path); legacy=rows(legacy_path)
        manifest=rows(index/'SOURCE-MANIFEST.jsonl'); taxonomy=rows(index/'SOURCE-TAXONOMY.jsonl')
        expected={r.get('source_path') for r in manifest}; actual={r.get('source_path') for r in taxonomy}
        old=[r for r in manifest if str(r.get('format','')).lower()=='ppt']
        status_by_path={r.get('source_path'):r for r in status}
        legacy_path_errors=[]
        for r in old:
            path=r.get('source_path'); st=status_by_path.get(path)
            if not st: legacy_path_errors.append({'path':path,'reason':'missing_status'}); continue
            if st.get('source_sha256')!=r.get('sha256'): legacy_path_errors.append({'path':path,'reason':'status_sha_mismatch','expected':r.get('sha256'),'actual':st.get('source_sha256')})
            if st.get('conversion') not in ('success','failed'): legacy_path_errors.append({'path':path,'reason':'invalid_processing_status','status':st.get('conversion')})
            source=(collection/str(path)).resolve()
            try: source.relative_to(collection.resolve())
            except ValueError:
                legacy_path_errors.append({'path':path,'reason':'unsafe_source_path'}); continue
            if not source.is_file(): legacy_path_errors.append({'path':path,'reason':'missing_source_file'})
            elif sha(source)!=r.get('sha256'): legacy_path_errors.append({'path':path,'reason':'source_sha_mismatch'})
    else:
        status_path=None; legacy_path=index/'LEGACY-STYLE-CATALOG.jsonl'
        legacy=rows(legacy_path); status=legacy
        curation=rows(index/'creativecup-curation.jsonl')
        old=[r for r in curation if str(r.get('path','')).lower().endswith('.ppt')]
        # Every catalog original path and every old-format inventory path must be represented.
        expected={'模板库/'+str(r.get('path')) for r in catalog} | {str(r.get('path')) for r in old}
        actual={r.get('path') for r in curation}
        summary=json.loads((index/'LEGACY-STYLE-SUMMARY.json').read_text(encoding='utf-8'))
        legacy_sources={}
        for entry in legacy:
            for path in entry.get('source_paths',[]): legacy_sources[path]={'sha256':entry.get('source_sha256'),'status':entry.get('parse_status')}
        for failed in summary.get('failed_sources',[]):
            for path in failed.get('source_paths',[]): legacy_sources[path]={'sha256':failed.get('source_sha256'),'status':'failed'}
        legacy_path_errors=[]
        curation_by_path={r.get('path'):r for r in old}
        for r in old:
            path=r.get('path'); cur_sha=r.get('sha256'); state=legacy_sources.get(path)
            if not state: legacy_path_errors.append({'path':path,'reason':'missing_legacy_processing_status'}); continue
            if state.get('sha256')!=cur_sha: legacy_path_errors.append({'path':path,'reason':'legacy_status_sha_mismatch','inventory_sha256':cur_sha,'status_sha256':state.get('sha256')})
            src=ROOT/path
            if not src.is_file(): legacy_path_errors.append({'path':path,'reason':'missing_source_file'})
            elif sha(src)!=cur_sha: legacy_path_errors.append({'path':path,'reason':'source_sha_mismatch'})
    status_hashes={r.get('source_sha256') for r in status if r.get('source_sha256')}
    add(checks,'legacy_status_path_and_sha_coverage',not legacy_path_errors,legacy_inventory_paths=len(old),status_rows=len(status),status_hashes=len(status_hashes),errors=legacy_path_errors[:100],error_count=len(legacy_path_errors))
    if name=='career': missing=sorted(expected-actual); extra=sorted(actual-expected); absent=[]
    else:
        missing=sorted(expected-actual); extra=sorted(actual-expected); absent=sorted(p for p in actual if not (ROOT/p).is_file())
    add(checks,'taxonomy_source_path_coverage',not missing and not absent,expected_paths=len(expected),taxonomy_paths=len(actual),missing=missing[:100],extra=extra[:100],missing_source_files=absent[:100])
    # Fail the relevant audit if any other JSONL read in scope contained invalid lines.
    scoped_jsonl=[k for k in JSONL_ERRORS if k.startswith(index_rel+'/')]
    jsonl_errors=[{'file':k,'errors':JSONL_ERRORS[k]} for k in scoped_jsonl]
    add(checks,'all_scoped_jsonl_valid',not jsonl_errors,files_with_errors=len(jsonl_errors),errors=jsonl_errors[:30])
    return {"collection":name,"index":index_rel,"catalog_rows":len(catalog),"page_rows":len(pages),"checks":checks,"ok":all(c['ok'] for c in checks)}


def audit_registration():
    """Verify that the source-bound learning indexes reached Studio's live lookup files."""
    library = ROOT / '模板库'
    curation = rows(library / 'CONTENT-CURATION.jsonl')
    catalog = rows(library / 'CATALOG.jsonl')
    design = rows(library / 'DESIGN-STRUCTURE.jsonl')
    depth = rows(library / 'DEEP-INDEX.jsonl')
    target = {r['path']:r for r in curation
              if (r.get('classification_observation') or {}).get('source_collection') in ('creative','career')}
    by_catalog = {r['path']:r for r in catalog}
    by_design = {r['source_path']:r for r in design}
    by_depth = {r['path']:r for r in depth}
    problems=[]; pptx_count=0; unsupported=0; off_topic=0; flattened=0; career_candidates=0
    for path, cur in target.items():
        cat=by_catalog.get(path)
        if cat is None:
            problems.append({'path':path,'reason':'catalog_missing'});continue
        if (cat.get('source_sha256')!=cur.get('sha256') or cat.get('review_status')=='filename_only'
                or cat.get('title')!=cur.get('display_name')):
            problems.append({'path':path,'reason':'catalog_identity_or_name_mismatch'})
        observation=cur.get('classification_observation') or {}
        if observation.get('off_topic') is True:
            off_topic+=1
            if cat.get('asset_type')!='reference' or cat.get('off_topic') is not True:
                problems.append({'path':path,'reason':'off_topic_not_reference'})
        if observation.get('flattened_page_structure') is True:
            flattened+=1
            if cat.get('asset_type')!='reference':
                problems.append({'path':path,'reason':'flattened_source_not_reference'})
        if (observation.get('source_collection')=='career' and cur.get('asset_type')=='template'
                and observation.get('off_topic') is not True and path.lower().endswith('.pptx')):
            career_candidates+=1
            if not cat.get('occasion_candidate_basis') or not any('竞赛' in tag for tag in cat.get('occasion_tags',[])):
                problems.append({'path':path,'reason':'career_competition_candidate_tag_missing'})
        if path.lower().endswith(('.ppsx','.pptm')):
            if cat.get('asset_type')!='reference' or cat.get('reuse_scope')!='inspect_only_non_pptx_source':
                problems.append({'path':path,'reason':'special_format_not_reference_only'})
        if not path.lower().endswith('.pptx'):
            continue
        pptx_count+=1
        d=by_design.get(path);deep=by_depth.get(path)
        if (d is None or d.get('status')!='parsed' or d.get('source_sha256')!=cur.get('sha256')
                or (d.get('coverage') or {}).get('full_slide_coverage') is not True):
            problems.append({'path':path,'reason':'design_structure_missing_or_stale'})
        if deep is None or deep.get('expected_sha256')!=cur.get('sha256'):
            problems.append({'path':path,'reason':'depth_index_missing_or_stale'})
        elif deep.get('parse_status')!='parsed':
            unsupported+=1
    expected_target=2533
    expected_pptx=1989
    checks=[{'check':'live_catalog_curation_design_depth_registration',
             'ok':not problems and len(target)==expected_target and pptx_count==expected_pptx,
             'target_paths':len(target),'expected_target_paths':expected_target,'pptx_paths':pptx_count,
             'expected_pptx_paths':expected_pptx,'depth_unsupported':unsupported,
             'off_topic_reference_paths':off_topic,'flattened_reference_paths':flattened,
             'career_template_candidate_paths':career_candidates,
             'errors':problems[:100],'error_count':len(problems)}]
    return {'checks':checks,'ok':all(item['ok'] for item in checks)}

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--output',help='write audit JSON here (default: 模板库/CONTEST-STYLE-AUDIT.json)'); args=ap.parse_args()
    report={"schema":"aippt.contest-style-index-audit.v1","read_only":True,
            "collections":[audit(*c) for c in COLLECTIONS],"registration":audit_registration()}
    report['ok']=all(c['ok'] for c in report['collections']) and report['registration']['ok']
    payload=json.dumps(report,ensure_ascii=False,indent=2)+'\n'
    out=Path(args.output) if args.output else ROOT/'模板库/CONTEST-STYLE-AUDIT.json'
    if not out.is_absolute(): out=ROOT/out
    out.parent.mkdir(parents=True,exist_ok=True); out.write_text(payload,encoding='utf-8')
    return 0 if report['ok'] else 1
if __name__=='__main__': raise SystemExit(main())
