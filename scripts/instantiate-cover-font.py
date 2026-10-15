#!/usr/bin/env python3
"""Materialize one author-declared named variable-font instance, preserving coverage."""
import argparse,hashlib,json
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--style',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True);a=p.parse_args()
    f=TTFont(a.source);instance=None
    for i in f['fvar'].instances:
        label=f['name'].getDebugName(i.subfamilyNameID)
        if label.casefold()==a.style.casefold():instance=i;break
    if instance is None:raise ValueError('style is not declared by the font author')
    before=set(f.getBestCmap());flags=f['OS/2'].fsType
    limits={axis.axisTag:instance.coordinates.get(axis.axisTag,axis.defaultValue) for axis in f['fvar'].axes}
    result=instantiateVariableFont(f,limits,inplace=False,updateFontNames=True)
    assert set(result.getBestCmap())==before
    assert result['OS/2'].fsType==flags
    result.save(a.output)
    names=[{'id':n.nameID,'value':n.toUnicode()} for n in result['name'].names if n.nameID in(1,2,4,6,16,17)]
    a.receipt.write_text(json.dumps({'files':[str(a.output)],'source':str(a.source),'source_sha256':hashlib.sha256(a.source.read_bytes()).hexdigest(),
                                   'style':a.style,'axes':limits,'all_cmap_characters_preserved':True,'embedding_flags_unchanged':True,
                                   'names':names,'sha256':hashlib.sha256(a.output.read_bytes()).hexdigest()},ensure_ascii=False,indent=2))
    print('materialized author named instance',a.style,'characters',len(before))

if __name__=='__main__':main()
