#!/usr/bin/env python3
"""Retrieve published original open fonts with licenses and source receipts."""
import argparse,concurrent.futures,hashlib,json,time
from pathlib import Path
from urllib.request import Request,urlopen
from urllib.parse import quote
from fontTools.ttLib import TTFont

def fetch(url):
    error=None
    for attempt in range(2):
        try:
            with urlopen(Request(url,headers={'User-Agent':'CoverFontAudit/1.0'}),timeout=25) as response:return response.read()
        except Exception as e:error=e
    raise error

def main():
    p=argparse.ArgumentParser();p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--manifest',type=Path,required=True);a=p.parse_args()
    a.output_dir.mkdir(parents=True,exist_ok=True)
    jobs=[];errors=[]
    for folder in ['arsenal','lato','bebasneue','montserrat','opensans','dosis','josefinsans','robotocondensed']:
        api='https://api.github.com/repos/google/fonts/contents/ofl/'+folder
        try:
            entries=json.loads(fetch(api));license=next(e for e in entries if e['name']=='OFL.txt')
            (a.output_dir/(folder+'-OFL.txt')).write_bytes(fetch(license['download_url']))
            fonts=[e for e in entries if e['name'].endswith('.ttf') and 'Italic' not in e['name']]
            if folder=='lato':fonts=[e for e in fonts if e['name'] in ['Lato-Regular.ttf','Lato-Light.ttf','Lato-Black.ttf']]
            for e in fonts:jobs.append({'name':e['name'],'url':e['download_url'],'license':'OFL','license_file':str(a.output_dir/(folder+'-OFL.txt'))})
        except Exception as e:errors.append({'family':folder,'source':api,'error':str(e)})
    # Author repository still exposes static regular/bold condensed faces.
    ddapi='https://api.github.com/repos/amcchord/datto-d-din/contents/'
    try:
        entries=json.loads(fetch(ddapi));lic=next(e for e in entries if e['name']=='COPYING.txt')
        (a.output_dir/'D-DIN-LICENSE.txt').write_bytes(fetch(lic['download_url']))
        for e in entries:
            if e['name'] in ['D-DINCondensed.otf','D-DINCondensed-Bold.otf']:
                jobs.append({'name':e['name'],'url':e['download_url'],'license':'OFL','license_file':str(a.output_dir/'D-DIN-LICENSE.txt')})
    except Exception as e:errors.append({'family':'D-DIN Condensed','error':str(e)})
    # Use the historical author tag because current Source Sans 3 has a new family name.
    ref=quote('2.045R-ro/1.095R-it',safe='')
    api='https://api.github.com/repos/adobe-fonts/source-sans/contents/OTF?ref='+ref
    try:
        entries=json.loads(fetch(api));licurl='https://raw.githubusercontent.com/adobe-fonts/source-sans/'+ref+'/LICENSE.md'
        try:(a.output_dir/'SourceSansPro-LICENSE.md').write_bytes(fetch(licurl))
        except Exception:licurl='https://raw.githubusercontent.com/adobe-fonts/source-sans/release/LICENSE.md';(a.output_dir/'SourceSansPro-LICENSE.md').write_bytes(fetch(licurl))
        for e in entries:
            if e['name'] in ['SourceSansPro-Regular.otf','SourceSansPro-Light.otf','SourceSansPro-Semibold.otf']:
                jobs.append({'name':e['name'],'url':e['download_url'],'license':'OFL','license_file':str(a.output_dir/'SourceSansPro-LICENSE.md')})
    except Exception as e:errors.append({'family':'Source Sans Pro','source':api,'error':str(e)})
    # Public versioned CDN distribution for the original v4 icon font.
    jobs.append({'name':'FontAwesome-4.7.ttf','url':'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/fonts/fontawesome-webfont.ttf',
                 'license':'Font Awesome v4 font license (OFL)','license_url':'https://fontawesome.com/v4/license/'})
    files=[]
    def download(job):
        data=fetch(job['url']);out=a.output_dir/job['name'];out.write_bytes(data)
        f=TTFont(out,lazy=True);names=[]
        for n in f['name'].names:
            if n.nameID in (1,2,4,6,16,17):
                try:names.append({'id':n.nameID,'value':n.toUnicode()})
                except UnicodeError:pass
        return {**job,'path':str(out),'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
                'names':names,'fsType':int(f['OS/2'].fsType) if 'OS/2'in f else None,
                'glyph_count':len(f.getGlyphOrder()),'variable':'fvar'in f}
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        for job,future in [(j,pool.submit(download,j)) for j in jobs]:
            try:files.append(future.result());print('downloaded',job['name'],flush=True)
            except Exception as e:errors.append({'family':job['name'],'source':job['url'],'error':str(e)})
    a.manifest.write_text(json.dumps({'files':files,'failures':errors,'font_data_unmodified':True},ensure_ascii=False,indent=2),encoding='utf-8')
    print('verified_files',len(files),'failures',len(errors),flush=True)

if __name__=='__main__':main()
