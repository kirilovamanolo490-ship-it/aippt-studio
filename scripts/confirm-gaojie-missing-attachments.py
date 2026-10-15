#!/usr/bin/env python3
"""Record manually reviewed, normal product pages without attachments."""
import argparse, hashlib, json, os, re, time
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'aippt-studio/.maintenance/gaojie-downloads'

class Page(HTMLParser):
    def __init__(self):
        super().__init__(); self.links=[]; self.h1=[]; self.in_h1=False
    def handle_starttag(self, tag, attrs):
        if tag == 'h1': self.in_h1=True
        self.links.extend(v for k,v in attrs if k in ('href','src','data-url','data-href') and v)
    def handle_endtag(self, tag):
        if tag == 'h1': self.in_h1=False
    def handle_data(self, data):
        if self.in_h1: self.h1.append(data)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ids',nargs='+',required=True)
    parser.add_argument('--expected-heading',required=True)
    args=parser.parse_args()
    path=STATE/'详情链接.jsonl'
    latest={str(row['id']):row for line in path.read_text().splitlines() if line.strip() for row in [json.loads(line)]}
    updates=[]
    for item_id in args.ids:
        row=latest[item_id]
        assert row['status']=='missing_or_ambiguous_download', item_id
        evidence=(STATE/row['evidence_file']).resolve()
        assert evidence.is_relative_to(STATE.resolve())
        content=evidence.read_bytes()
        assert hashlib.sha256(content).hexdigest()==row['detail_sha256'], item_id
        html=content.decode('utf-8'); page=Page(); page.feed(html)
        assert ''.join(page.h1).strip()==args.expected_heading, item_id
        assert '立即下载' not in html and not row.get('candidate_links') and not row.get('candidate_labels'), item_id
        assert not any(re.search(r'\.(?:pptx?|zip|rar|7z)(?:[?#]|$)',v,re.I) for v in page.links), item_id
        assert any(re.search(r'\.(?:png|jpe?g)(?:[?#]|$)',v,re.I) for v in page.links), item_id
        updates.append({**row,'status':'no_attachment','verification':'normal_product_html_matching_h1_preview_png_only_no_download_or_attachment','verified_at':time.strftime('%Y-%m-%d %H:%M:%S %z')})
    payload=''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in updates).encode()
    descriptor=os.open(path,os.O_WRONLY|os.O_APPEND)
    try: assert os.write(descriptor,payload)==len(payload)
    finally: os.close(descriptor)
    print(json.dumps({'marked_no_attachment':args.ids,'evidence_sha256_matched':True}))

if __name__=='__main__': main()
