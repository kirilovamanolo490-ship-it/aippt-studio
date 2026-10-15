#!/usr/bin/env python3
"""Resume authorized Gaojie catalog reads and HTTPS resource downloads.

Only recorded catalog IDs are read. Category 57 is always excluded. A private
Copy-as-cURL file supplies the existing same-site session; it is parsed as data,
never executed, and cookies are never sent to resource/CDN hosts.
"""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, json, os, re, shlex, shutil
import threading, time, urllib.parse
from html.parser import HTMLParser
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT/'模板库/图表与视觉素材/版式与排版参考/高阶文化-待下载清单/资源清单.jsonl'
STATE = ROOT/'aippt-studio/.maintenance/gaojie-downloads'
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36'
SITE = 'www.gaojiewenhua.cn'
CDNS = {'wstx.web.vleader.net.cn'}
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
MAX_ERROR_PAGES = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
thread_sessions = threading.local()
error_pages_lock = threading.Lock()
PACE_LOCK = threading.Lock()
NEXT_READ = 0.0
PROXY = os.environ.get('GAOJIE_EXISTING_SYSTEM_PROXY','')
READ_INTERVAL = max(0.25,float(os.environ.get('GAOJIE_READ_INTERVAL','0.8')))

def rows(p):
    return [json.loads(s) for s in p.read_text().splitlines() if s.strip()] if p.exists() else []

class Links(HTMLParser):
    def __init__(self):
        super().__init__(); self.active=None; self.anchors=[]; self.title=[]; self.in_title=False
    def handle_starttag(self, tag, attrs):
        a=dict(attrs)
        if tag=='a': self.active={'href':a.get('href',''),'text':'','classes':a.get('class','').lower().split()}
        if tag=='title': self.in_title=True
    def handle_data(self, data):
        if self.active is not None: self.active['text']+=data
        if self.in_title:self.title.append(data)
    def handle_endtag(self, tag):
        if tag=='a' and self.active is not None:
            self.anchors.append(self.active)
            self.active=None
        if tag=='title':self.in_title=False

    def download_candidates(self):
        return [a for a in self.anchors if '立即下载' in a['text'] or 'downloadclass' in a['classes']]

    def labelled_candidates(self):
        return [a for a in self.anchors if '立即下载' in a['text']]


class RequestScopeError(RuntimeError):
    pass


def no_auth(request):
    """Keep requests from consulting .netrc while still honoring proxy env."""
    return request


def session_for(kind):
    sessions=getattr(thread_sessions,'sessions',None)
    if sessions is None:
        sessions={};thread_sessions.sessions=sessions
    if kind not in sessions:
        s=requests.Session()
        s.trust_env=True
        s.auth=no_auth
        if PROXY:s.proxies.update({'http':PROXY,'https':PROXY})
        s.headers.update({'User-Agent':UA})
        sessions[kind]=s
    return sessions[kind]


def site_origin_ok(url, allow_http=True):
    try:
        u=urllib.parse.urlsplit(url)
        if u.hostname!=SITE or u.username or u.password:return False
        if u.scheme=='http':return allow_http and u.port in (None,80)
        if u.scheme=='https':return u.port in (None,443)
        return False
    except ValueError:return False


def resource_url_ok(url):
    try:
        u=urllib.parse.urlsplit(url)
        if u.username or u.password:return False
        if u.hostname==SITE:
            return u.scheme=='http' and u.port in (None,80) and (u.path or '').startswith('/upload/')
        if u.hostname in CDNS:
            return u.scheme=='https' and u.port in (None,443)
        return False
    except ValueError:return False


def scoped_get(session, url, headers, scope, stream=False):
    """Follow only explicitly approved same-site/CDN redirects."""
    current=url
    for redirect_number in range(6):
        timeout=(CONNECT_TIMEOUT,180 if scope=='resource' and urllib.parse.urlsplit(current).hostname==SITE else READ_TIMEOUT)
        allowed=site_origin_ok(current) if scope=='detail' else resource_url_ok(current)
        if not allowed:raise RequestScopeError('unreviewed_url_or_redirect')
        if scope=='resource':session.cookies.clear()
        response=session.get(current,headers=headers,timeout=timeout,allow_redirects=False,stream=stream)
        if scope=='resource':session.cookies.clear()
        if response.status_code not in REDIRECT_STATUSES:
            return response,current
        location=response.headers.get('Location')
        response.close()
        if not location:raise RequestScopeError('redirect_missing_location')
        current=urllib.parse.urljoin(current,location)
    raise RequestScopeError('too_many_redirects')


def save_error_page(state, item_id, body):
    folder=state/'errors'
    with error_pages_lock:
        folder.mkdir(parents=True,exist_ok=True)
        existing=list(folder.glob('*.html'))
        dest=folder/f'{item_id}.html'
        if dest.exists() or len(existing)>=MAX_ERROR_PAGES:return
        dest.write_bytes(body)

def session_headers(path):
    a=shlex.split(path.read_text())
    urls=[v for v in a if v.startswith(('http://','https://'))]
    if a[0]!='curl' or len(urls)!=1 or urllib.parse.urlsplit(urls[0]).hostname!=SITE:
        raise ValueError('expected_same_site_copy_as_curl')
    h={'User-Agent':UA}
    for i,v in enumerate(a[:-1]):
        if v in ('-b','--cookie'):h['Cookie']=a[i+1]
        if v in ('-H','--header') and a[i+1].lower().startswith('cookie:'):
            h['Cookie']=a[i+1].split(':',1)[1].strip()
    if 'Cookie' not in h:raise ValueError('session_cookie_missing')
    return h

def collect(item, headers, stop, state):
    out={'id':item['id'],'category_id':item['category_id'],'detail_url':item['detail_url']}
    if stop.is_set():return {**out,'status':'stopped'}
    u=urllib.parse.urlsplit(item['detail_url'])
    if not site_origin_ok(item['detail_url']) or u.path!='/products_show.aspx':return {**out,'status':'bad_detail_url'}
    global NEXT_READ
    with PACE_LOCK:
        time.sleep(max(0,NEXT_READ-time.monotonic()))
        NEXT_READ=time.monotonic()+READ_INTERVAL
    if stop.is_set():return {**out,'status':'stopped'}
    session=session_for('detail')
    for attempt in range(3):
        response=None
        try:
            response,final=scoped_get(session,item['detail_url'],headers,'detail')
            if response.status_code in (403,429):
                result={'403':'forbidden','429':'rate_limited'}[str(response.status_code)]
                row={**out,'status':result,'http_status':response.status_code}
                stop.set()
                if response.status_code==429:
                    row['retry_after']=response.headers.get('Retry-After','')
                    stop.set()
                return row
            if response.status_code>=500:
                if attempt<2:
                    response.close();time.sleep(1+attempt*2);continue
                return {**out,'status':'detail_error','http_status':response.status_code}
            if response.status_code!=200:return {**out,'status':'detail_error','http_status':response.status_code}
            b=response.content
            if 'login' in final.lower():
                stop.set();return {**out,'status':'session_expired'}
            if not b.strip():raise RuntimeError('empty_response')
            p=Links();p.feed(b.decode('utf-8-sig'))
            candidates=p.download_candidates();labelled=p.labelled_candidates()
            candidate_links=[urllib.parse.urljoin(final,x['href']) for x in candidates if x.get('href')]
            title=''.join(p.title)
            # A blank title and absent site navigation is not evidence of a
            # missing file. Stop for ordinary browser verification; never run
            # response JavaScript or try to manufacture challenge cookies.
            if not title.strip() and not any('专用PPT模板' in x['text'] for x in p.anchors):
                save_error_page(state,item['id'],b)
                stop.set()
                return {**out,'status':'needs_browser_verification','detail_sha256':hashlib.sha256(b).hexdigest(),'response_bytes':len(b)}
            if not labelled and any('downloadclass' in x['classes'] for x in p.anchors):
                save_error_page(state,item['id'],b)
                return {**out,'status':'download_label_changed','candidate_links':candidate_links,'candidate_labels':[x['text'].strip() for x in candidates],'page_title':title}
            if len(candidates)!=1 or len(labelled)!=1:
                save_error_page(state,item['id'],b)
                evidence=state/'needs-review'/f"{item['id']}.html"
                evidence.parent.mkdir(parents=True,exist_ok=True)
                evidence.write_bytes(b)
                return {**out,'status':'missing_or_ambiguous_download','candidate_links':candidate_links,'candidate_labels':[x['text'].strip() for x in candidates],'page_title':title,'detail_sha256':hashlib.sha256(b).hexdigest(),'evidence_file':str(evidence.relative_to(state))}
            url=urllib.parse.urljoin(final,labelled[0]['href'])
            return {**out,'status':'resolved','url':url,'page_title':''.join(p.title),'detail_sha256':hashlib.sha256(b).hexdigest()}
        except (requests.RequestException,RequestScopeError,RuntimeError) as e:
            if isinstance(e,RequestScopeError):return {**out,'status':'unreviewed_detail_redirect','error':str(e)}
            if attempt==2:return {**out,'status':'detail_error','error_type':type(e).__name__,'error':str(e)}
            time.sleep(1+attempt)
        finally:
            if response is not None:response.close()

def fetch(item, state, stop):
    out={k:item[k] for k in ('id','category_id','url','detail_url')}
    if stop.is_set():return {**out,'status':'stopped'}
    u=urllib.parse.urlsplit(item['url'])
    if u.hostname not in CDNS | {SITE}:return {**out,'status':'unreviewed_download_host','host':u.hostname}
    if u.scheme not in ('http','https'):return {**out,'status':'unsupported_url_scheme'}
    # The site's own observed /upload URLs are HTTP-only, as is the site itself.
    # Reuse the published address without cookies. CDN resources support HTTPS.
    if u.hostname==SITE and not u.path.startswith('/upload/'):
        return {**out,'status':'unreviewed_site_download_path'}
    secure=urllib.parse.urlunsplit(('https' if u.hostname in CDNS else u.scheme,u.netloc,u.path,u.query,''))
    ext=Path(urllib.parse.unquote(u.path)).suffix.lower()
    if not re.fullmatch(r'\.[a-z0-9]{1,8}',ext):return {**out,'status':'missing_file_extension'}
    if ext!='.pptx':return {**out,'status':'excluded_non_pptx','extension':ext}
    dest=state/'files'/f"{item['id']}{ext}"; dest.parent.mkdir(parents=True,exist_ok=True)
    part=dest.with_suffix(ext+'.part')
    part.unlink(missing_ok=True)
    if shutil.disk_usage(state).free < 20*1024**3:
        stop.set();return {**out,'status':'disk_space_low'}
    session=session_for('resource')
    request_headers={'User-Agent':UA,'Referer':item['detail_url'],'Accept-Encoding':'identity'}
    last_error='';last_status=''
    for attempt in range(3):
        if stop.is_set():return {**out,'status':'stopped'}
        response=None
        try:
            response,final_url=scoped_get(session,secure,request_headers,'resource',stream=True)
            status=response.status_code
            if status==429:
                stop.set()
                return {**out,'status':'rate_limited','http_status':status,'retry_after':response.headers.get('Retry-After','')}
            if status==403:
                if u.hostname==SITE:stop.set()
                return {**out,'status':'forbidden','http_status':status}
            if status>=500:
                last_status=status
                if attempt<2:time.sleep(1+attempt*2);continue
                return {**out,'status':'download_error','http_status':status}
            if status!=200:return {**out,'status':'download_error','http_status':status}
            content_type=response.headers.get('Content-Type','').lower()
            raw_length=response.headers.get('Content-Length')
            try:expected_length=int(raw_length) if raw_length is not None else None
            except ValueError:return {**out,'status':'invalid_content_length','content_length':raw_length}
            if expected_length is not None and expected_length<=0:
                return {**out,'status':'empty_response','content_length':expected_length}
            if 'text/html' in content_type:
                if u.hostname==SITE:stop.set()
                return {**out,'status':'html_instead_of_file','content_type':content_type}
            size=0;digest=hashlib.sha256();prefix=bytearray()
            part.unlink(missing_ok=True)
            with part.open('wb') as stream:
                for chunk in response.iter_content(chunk_size=1024*1024):
                    if not chunk:continue
                    if len(prefix)<512:prefix.extend(chunk[:512-len(prefix)])
                    stream.write(chunk);digest.update(chunk);size+=len(chunk)
            if size==0:
                part.unlink(missing_ok=True)
                return {**out,'status':'empty_response','content_length':expected_length}
            if expected_length is not None and size!=expected_length:
                part.unlink(missing_ok=True)
                last_error='content_length_mismatch';last_status=status
                if attempt<2:time.sleep(1+attempt*2);continue
                return {**out,'status':'download_error','http_status':status,'error':last_error,'expected_bytes':expected_length,'received_bytes':size}
            normalized=bytes(prefix).lstrip().lower()
            if normalized.startswith((b'<!doctype html',b'<html')):
                part.unlink(missing_ok=True)
                if u.hostname==SITE:stop.set()
                return {**out,'status':'html_instead_of_file','content_type':content_type}
            part.replace(dest)
            return {**out,'status':'downloaded','download_url':final_url,'local_path':str(dest),'bytes':size,'content_length':expected_length,'sha256':digest.hexdigest()}
        except RequestScopeError as e:
            part.unlink(missing_ok=True)
            return {**out,'status':'unreviewed_download_redirect','error':str(e)}
        except requests.RequestException as e:
            part.unlink(missing_ok=True)
            last_error=type(e).__name__
            if attempt<2:time.sleep(1+attempt*2);continue
            return {**out,'status':'download_error','http_status':last_status,'error_type':last_error}
        except OSError as e:
            part.unlink(missing_ok=True)
            return {**out,'status':'download_error','http_status':last_status,'error_type':type(e).__name__}
        finally:
            if response is not None:response.close()
    return {**out,'status':'download_error','http_status':last_status,'error':last_error}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=['details','download'])
    ap.add_argument('--session',type=Path);ap.add_argument('--state',type=Path,default=STATE)
    ap.add_argument('--workers',type=int,default=4);ap.add_argument('--limit',type=int)
    ap.add_argument('--ids',nargs='+',help='Restrict a reviewed retry to recorded inventory IDs')
    ap.add_argument('--only-new',action='store_true',help='Leave previously attempted errors for a separate retry pass')
    ap.add_argument('--download-host',choices=[SITE,*sorted(CDNS)],help='Limit downloads to one observed resource host')
    args=ap.parse_args();args.state.mkdir(parents=True,exist_ok=True)
    inv=[x for x in rows(INVENTORY) if str(x['category_id'])!='57']
    assert len(inv)==6578 and len({x['id'] for x in inv})==6578
    stop=threading.Event()
    if args.mode=='details':
        path=args.state/'详情链接.jsonl'; old={str(x['id']):x for x in rows(path)}
        work=[x for x in inv if old.get(str(x['id']),{}).get('status') not in ('resolved','no_attachment','needs_browser_verification')]
        if args.only_new:work=[x for x in work if old.get(str(x['id']),{}).get('status','') in ('','stopped')]
        headers=session_headers(args.session)
        task=lambda x:collect(x,headers,stop,args.state)
    else:
        path=args.state/'下载回执.jsonl';old={str(x['id']):x for x in rows(path)}
        ids={str(x['id']) for x in inv}
        details={str(x['id']):x for x in rows(args.state/'详情链接.jsonl')}
        work=[x for k,x in details.items() if k in ids and x.get('status')=='resolved' and old.get(k,{}).get('status')!='browser_security_review_required' and not (old.get(k,{}).get('status')=='downloaded' and Path(old[k].get('local_path','')).is_file())]
        if args.only_new:work=[x for x in work if str(x['id']) not in old or old[str(x['id'])].get('status')=='stopped']
        if args.download_host:work=[x for x in work if urllib.parse.urlsplit(x['url']).hostname==args.download_host]
        task=lambda x:fetch(x,args.state,stop)
    if args.ids:work=[x for x in work if str(x['id']) in set(args.ids)]
    if args.limit:work=work[:args.limit]
    print(json.dumps({'mode':args.mode,'pending':len(work),'previous':len(old),'excluded':196}),flush=True)
    start=time.time();counts={};n=0;consecutive_errors=0
    with path.open('a',encoding='utf-8') as log, concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs={pool.submit(task,x):x for x in work}
        for f in concurrent.futures.as_completed(jobs):
            result=f.result();log.write(json.dumps(result,ensure_ascii=False)+'\n');log.flush()
            n+=1;counts[result['status']]=counts.get(result['status'],0)+1
            if result['status'] in ('detail_error','download_error','session_expired'):
                consecutive_errors+=1
                if consecutive_errors>=6:stop.set()
            elif result['status']!='stopped':consecutive_errors=0
            if n%25==0 or n==len(work):print(json.dumps({'done':n,'total':len(work),'counts':counts,'elapsed_s':round(time.time()-start)}),flush=True)
    print(json.dumps({'complete':not stop.is_set(),'counts':counts}),flush=True)
if __name__=='__main__':main()
