#!/usr/bin/env python3
"""Prepare only exact name-matched original font files for registration."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import unicodedata

def normal(value): return ' '.join(unicodedata.normalize('NFKC',value).split()).casefold()

def main():
    p=argparse.ArgumentParser();p.add_argument('--audit',type=Path,required=True);p.add_argument('--status',type=Path,required=True)
    p.add_argument('--sources',type=Path,required=True);p.add_argument('--font-dir',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    requirements=json.loads(a.audit.read_text());status=json.loads(a.status.read_text());sources=json.loads(a.sources.read_text())
    failed={f['name'] for f in status['fonts'] if not f['available']}
    index={normal(k):v for k,v in sources['name_index'].items()}
    faces={f['sha256']:f for f in sources['files']}
    a.font_dir.mkdir(parents=True,exist_ok=True);entries=[];paths={};unmatched=[]
    for req in requirements['fonts']:
        name=req['name']
        if name not in failed:continue
        matches=[m for m in index.get(normal(name),[]) if m['name_id'] in (1,4,6,16,17)]
        if not matches:unmatched.append({'name':name,'deck_count':req['deck_count'],'paths':req['paths']});continue
        # Prefer a genuine family/full name; identical bytes need only one registered file.
        matches.sort(key=lambda x:(0 if x['name_id'] in (1,16) else 1,0 if x['name_id']==4 else 1,x['sha256']))
        m=matches[0];source=Path(m['paths'][0]);sha=m['sha256']
        if sha not in paths:
            dest=a.font_dir/(sha[:12]+'-'+source.name)
            if hashlib.sha256(source.read_bytes()).hexdigest()!=sha:raise RuntimeError('source changed: '+str(source))
            if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()!=sha:raise RuntimeError('destination conflict: '+str(dest))
            if not dest.exists():shutil.copy2(source,dest)
            paths[sha]=str(dest)
        info=faces[sha]['faces'][m['face_index']]
        entries.append({'name':name,'matched_name':m['name'],'source':str(source),'source_sha256':sha,'path':paths[sha],
                        'face_index':m['face_index'],'fsType':info.get('fsType'),'deck_count':req['deck_count']})
    result={'entries':entries,'files':sorted(set(paths.values())),'unmatched':unmatched,'original_files_unchanged':True}
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('name_matches',len(entries),'files',len(paths),'unmatched',len(unmatched))

if __name__=='__main__':main()
