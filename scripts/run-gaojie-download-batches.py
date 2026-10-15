#!/usr/bin/env python3
"""Consume resolved URLs in small batches while a detail collector runs."""
import argparse, json, os, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'aippt-studio/.maintenance/gaojie-downloads'
# Archive files live directly under the website-category directories here.
ARCHIVE=ROOT/'模板库/高阶文化'
def rows(p):
    return [json.loads(x) for x in p.read_text().splitlines() if x.strip()] if p.exists() else []
def alive(pid):
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
def run(name,*args):
    subprocess.run([sys.executable,str(ROOT/'scripts'/name),*map(str,args)],check=True,cwd=ROOT)
def main():
    p=argparse.ArgumentParser();p.add_argument('--details-pid',type=int,required=True);a=p.parse_args()
    while True:
        run('download-gaojie-library.py','download','--workers','3','--only-new')
        downloads={str(r['id']):r for r in rows(STATE/'下载回执.jsonl')}
        archived={str(r['id']):r for r in rows(ARCHIVE/'归档回执.jsonl')}
        batch=[r for k,r in downloads.items() if r.get('status')=='downloaded' and archived.get(k,{}).get('archive_status') not in ('archived','deduplicated')]
        if batch:
            path=STATE/'批次归档输入.jsonl'
            path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in batch))
            run('archive-gaojie-downloads.py','--receipts',path)
        status=ROOT/'scripts/build-gaojie-download-status.py'
        if status.exists():run(status.name)
        if not alive(a.details_pid):break
        time.sleep(25)
if __name__=='__main__':main()
