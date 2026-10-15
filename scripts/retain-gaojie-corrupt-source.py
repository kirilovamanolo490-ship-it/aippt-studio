#!/usr/bin/env python3
"""Preserve a twice-downloaded corrupt source with an explicit filename label."""
import hashlib, json, shutil, time, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'aippt-studio/.maintenance/gaojie-downloads'
evidence_path=STATE/'源文件损坏证据.json'
evidence=json.loads(evidence_path.read_text())
assert evidence['source_download_reproduced'] is True
files=evidence['files']; assert len(files)>=2
hashes=set(); failures=set()
for record in files:
    source=Path(record['path']); data=source.read_bytes()
    assert source.resolve().is_relative_to(STATE.resolve())
    assert len(data)==record['bytes']
    digest=hashlib.sha256(data).hexdigest()
    assert digest==record['sha256']; hashes.add(digest)
    with zipfile.ZipFile(source) as package: failure=package.testzip()
    assert failure and failure==record['zip_testzip_first_failure']; failures.add(failure)
assert len(hashes)==len(failures)==1
item=evidence['item']
assert str(item['id'])=='3890' and item['category']=='实用素材'
target=ROOT/'模板库/高阶文化'/item['category']/f"{item['id']} - {item['title']}（网站原文件损坏）.pptx"
source=Path(files[0]['path'])
if not target.exists(): shutil.copy2(source,target)
assert hashlib.sha256(target.read_bytes()).hexdigest() in hashes
evidence.update(retained_path=str(target),retained_sha256=next(iter(hashes)),retained_bytes=target.stat().st_size,retained_at=time.strftime('%Y-%m-%d %H:%M:%S %z'),retention_status='corrupt_source_preserved_without_modification')
temporary=evidence_path.with_suffix('.tmp'); temporary.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n'); temporary.replace(evidence_path)
print(json.dumps({'retained_path':str(target),'source_bytes_preserved':True,'integrity_valid':False},ensure_ascii=False))
