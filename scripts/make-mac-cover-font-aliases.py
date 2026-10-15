#!/usr/bin/env python3
"""Add modern family-name records for existing, verified legacy name aliases.

Only names already present in a supplied font may be exposed. Glyph tables and
embedding flags remain unchanged; original files are retained separately.
"""
import argparse,hashlib,json,re,unicodedata,os,tempfile
from pathlib import Path
from fontTools.ttLib import TTFont,TTCollection
from fontTools.ttLib.sfnt import SFNTWriter

def normal(s):return ' '.join(unicodedata.normalize('NFKC',s).split()).casefold()

def main():
    p=argparse.ArgumentParser();p.add_argument('--plan',type=Path,required=True);p.add_argument('--status',type=Path,required=True)
    p.add_argument('--output-dir',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True);p.add_argument('--only');a=p.parse_args()
    plan=json.loads(a.plan.read_text());status=json.loads(a.status.read_text());missing={f['name'] for f in status['fonts'] if not f['available']}
    a.output_dir.mkdir(parents=True,exist_ok=True);entries=[]
    for item in plan['entries']:
        name=item['name']
        if name not in missing or (a.only and name!=a.only):continue
        source=Path(item['path']);rawhash=hashlib.sha256(source.read_bytes()).hexdigest()
        if rawhash!=item['source_sha256']:raise ValueError('original hash mismatch')
        collection=TTCollection(source,lazy=False) if source.suffix.lower()=='.ttc' else None
        f=collection.fonts[item['face_index']] if collection else TTFont(source,recalcTimestamp=False,lazy=False)
        known=[]
        for n in f['name'].names:
            if n.nameID in (1,4,6,16):
                try:known.append(n.toUnicode())
                except UnicodeError:pass
        if normal(name) not in {normal(n) for n in known}:raise ValueError('requested alias is not present in original font names')
        raw_tables={t:f.reader[t] for t in f.reader.keys()}
        original_tables={t:hashlib.sha256(data).hexdigest() for t,data in raw_tables.items() if t not in ('name','head','DSIG')}
        original_fs=int(f['OS/2'].fsType) if 'OS/2'in f else None
        style=f['name'].getDebugName(17) or f['name'].getDebugName(2) or 'Regular'
        # Modern Unicode family records let macOS match the actual Windows alias.
        for platform,encoding,language in [(0,4,0),(3,1,0x409),(3,1,0x804)]:
            f['name'].setName(name,16,platform,encoding,language)
            f['name'].setName(style,17,platform,encoding,language)
        safe=re.sub(r'[/\\:*?"<>|]','_',name)
        out=a.output_dir/('Mac命名兼容-'+safe+'-'+rawhash[:8]+('.otf' if f.sfntVersion=='OTTO' else '.ttf'))
        # Rewrite only the name table at the SFNT layer; a TTFont.save() roundtrip
        # can normalize legacy glyph padding, which is unnecessary for this fix.
        tables={tag:data for tag,data in raw_tables.items() if tag!='DSIG'}
        tables['name']=f['name'].compile(f)
        with tempfile.NamedTemporaryFile(dir=a.output_dir,suffix=out.suffix,delete=False) as stream:
            temporary=Path(stream.name)
            writer=SFNTWriter(stream,len(tables),sfntVersion=f.sfntVersion)
            for tag,data in sorted(tables.items()):writer[tag]=data
            writer.close()
        checked=TTFont(temporary,recalcTimestamp=False,lazy=True)
        new_tables={t:hashlib.sha256(checked.reader[t]).hexdigest() for t in original_tables}
        if new_tables!=original_tables:raise ValueError('a non-name font table changed: '+name)
        if (int(checked['OS/2'].fsType) if 'OS/2'in checked else None)!=original_fs:raise ValueError('embedding permission changed')
        checked.close();os.replace(temporary,out)
        entries.append({**item,'path':str(out),'original_path':str(source),'compatibility':'modern Unicode nameID16/17 for an original alias',
                        'glyph_and_metrics_tables_identical':True,'embedding_flags_unchanged':True,'sha256':hashlib.sha256(out.read_bytes()).hexdigest()})
    a.receipt.write_text(json.dumps({'entries':entries,'files':[x['path'] for x in entries]},ensure_ascii=False,indent=2),encoding='utf-8')
    print('compatible_copies',len(entries),'glyph_and_metrics_tables_identical=true')

if __name__=='__main__':main()
