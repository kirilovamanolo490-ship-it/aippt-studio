#!/usr/bin/env python3
"""Continue this authorized acquisition, with bounded retries and live receipts."""
import argparse, collections, fcntl, json, os, signal, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
STATE=ROOT/'aippt-studio/.maintenance/gaojie-downloads'
# Archive files live directly under the website-category directories here.
ARCHIVE=ROOT/'模板库/高阶文化'
SCRIPT=ROOT/'scripts'
def rows(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []
def latest(path):return {str(x['id']):x for x in rows(path)}
def run(script,*args,proxy=True):
    env=os.environ.copy()
    if not proxy:env.pop('GAOJIE_EXISTING_SYSTEM_PROXY',None)
    subprocess.run([sys.executable,str(SCRIPT/script),*map(str,args)],cwd=ROOT,env=env,check=True)
class PausedByController(Exception):pass
def on_sigterm(_signum,_frame):raise PausedByController('SIGTERM')
def snapshot(status,reason=''):
    d=latest(STATE/'详情链接.jsonl');f=latest(STATE/'下载回执.jsonl');a=latest(ARCHIVE/'归档回执.jsonl')
    v={'status':status,'reason':reason,'pid':os.getpid(),'updated_at':time.strftime('%Y-%m-%d %H:%M:%S %z'),'target':6578,'excluded':196,'details':dict(collections.Counter(x['status'] for x in d.values())),'downloads':dict(collections.Counter(x['status'] for x in f.values())),'archived':sum(x.get('archive_status') in ('archived','deduplicated') for x in a.values())}
    p=STATE/'持续任务状态.json';q=p.with_suffix('.tmp');q.write_text(json.dumps(v,ensure_ascii=False,indent=2));q.replace(p)
    print(json.dumps(v,ensure_ascii=False),flush=True);return v
def archive_new():
    old=latest(ARCHIVE/'归档回执.jsonl')
    batch=[x for k,x in latest(STATE/'下载回执.jsonl').items() if x.get('status')=='downloaded' and old.get(k,{}).get('archive_status') not in ('archived','deduplicated')]
    if batch:
        p=STATE/'批次归档输入.jsonl';p.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in batch))
        run('archive-gaojie-downloads.py','--receipts',p)
    run('build-gaojie-download-status.py')
def main():
    p=argparse.ArgumentParser();p.add_argument('--session',type=Path,required=True);a=p.parse_args()
    STATE.mkdir(parents=True,exist_ok=True)
    lock=(STATE/'持续任务.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    signal.signal(signal.SIGTERM,on_sigterm)
    stalled=0;previous=-1
    try:
        snapshot('running')
        while True:
            d=latest(STATE/'详情链接.jsonl')
            inv={str(x['id']) for x in rows(ROOT/'模板库/图表与视觉素材/版式与排版参考/高阶文化-待下载清单/资源清单.jsonl') if str(x['category_id'])!='57'}
            stop_statuses=('needs_browser_verification','rate_limited','session_expired','forbidden')
            pending=sum(k not in d or d[k].get('status') in ('','stopped') for k in inv)
            if not pending:break
            before={k:x.get('status','') for k,x in d.items()}
            env=os.environ.copy();env['GAOJIE_READ_INTERVAL']=os.environ.get('GAOJIE_READ_INTERVAL','2')
            detail_workers=str(max(1,min(2,int(os.environ.get('GAOJIE_DETAIL_WORKERS','1')))))
            subprocess.run([sys.executable,str(SCRIPT/'download-gaojie-library.py'),'details','--session',str(a.session),'--workers',detail_workers,'--limit','60','--only-new'],cwd=ROOT,env=env,check=True)
            after=latest(STATE/'详情链接.jsonl')
            newly_blocked=[(k,x.get('status')) for k,x in after.items() if x.get('status') in stop_statuses and before.get(k)!=x.get('status')]
            # Only already parsed, public CDN PPTX links may proceed after an
            # origin detail response requires browser verification or review.
            run('download-gaojie-library.py','download','--workers','4','--only-new','--download-host','wstx.web.vleader.net.cn',proxy=False)
            archive_new()
            if newly_blocked:
                kind=newly_blocked[0][1]
                snapshot('needs_browser_verification' if kind=='needs_browser_verification' else 'needs_attention',
                         'New detail status requires browser review: '+', '.join(f'{k}={v}' for k,v in newly_blocked))
                return
            files_before={k:x.get('status') for k,x in latest(STATE/'下载回执.jsonl').items()}
            run('download-gaojie-library.py','download','--workers','1','--only-new','--download-host','www.gaojiewenhua.cn')
            archive_new()
            files_after=latest(STATE/'下载回执.jsonl')
            file_blocked=[k for k,x in files_after.items() if x.get('status') in ('forbidden','rate_limited','html_instead_of_file') and files_before.get(k)!=x.get('status')]
            if file_blocked:
                snapshot('needs_attention','New source-file response requires browser review: '+', '.join(file_blocked));return
            s=snapshot('running')
            progress=s['details'].get('resolved',0)+s['details'].get('no_attachment',0)+s['archived']
            stalled=stalled+1 if progress==previous else 0;previous=progress
            if stalled>=3:
                snapshot('needs_network','Three consecutive batches made no progress');return
            time.sleep(30)
        run('download-gaojie-library.py','download','--workers','4','--only-new','--download-host','wstx.web.vleader.net.cn',proxy=False)
        run('download-gaojie-library.py','download','--workers','1','--only-new','--download-host','www.gaojiewenhua.cn')
        archive_new();s=snapshot('finished_with_exceptions')
        if s['archived']==6578:s=snapshot('complete')
        (ARCHIVE/'本次处理结果.json').write_text(json.dumps(s,ensure_ascii=False,indent=2))
    except PausedByController as e:
        snapshot('paused_by_controller',str(e))
    except Exception as e:
        snapshot('needs_attention',type(e).__name__+': '+str(e));raise
if __name__=='__main__':main()
