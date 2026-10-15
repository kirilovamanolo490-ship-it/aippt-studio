"""Mac implementation of Studio native-effects: OOXML + real PowerPoint QA/video."""
from __future__ import annotations
import shutil
import uuid
from pathlib import Path
from .renderer import sha256, officecli_validate, powerpoint_roundtrip
from .storage import write_json


def apply_mac_native_plan(source: Path, output: Path, plan: dict) -> dict:
    from .upstream_tools import invoke
    from .native_powerpoint import NativePowerPointError, _preview_root
    from .mac_video import export_movie
    work=output.parent/('mac-native-'+uuid.uuid4().hex)
    work.mkdir(parents=True)
    motion_plan=dict(plan);motion_plan['previews']=[]
    try:
        receipt=invoke('ppt-master','native-motion',{'source':str(source),'source_sha256':sha256(source),'plan':motion_plan},work/'motion')
        candidate=work/'office/candidate.pptx';candidate.parent.mkdir()
        shutil.copy2(work/'motion/candidate.pptx',candidate)
        # Native Office save/reopen plus image exports; never mutate call evidence.
        roundtrip=powerpoint_roundtrip(candidate,candidate.parent)
        validation=officecli_validate(candidate)
        from .animation_edit import inspect_animations
        readback=inspect_animations(candidate)
        write_json(work/'office/readback.json',readback)
        # Compare native timing/transition nodes after Office save using canonical
        # XML, to reject Office silently dropping effects or Morph extensions.
        from .animation_edit import _package, _slides, _xml, NS
        original_parts=_package(work/'motion/candidate.pptx');saved_parts=_package(candidate)
        original_names=_slides(original_parts);saved_names=_slides(saved_parts)
        if len(original_names)!=len(saved_names):raise ValueError('Office changed slide count')
        for before_name,after_name in zip(original_names,saved_names,strict=True):
            before=_xml(original_parts[before_name]);after=_xml(saved_parts[after_name])
            for path in ['./p:timing','./p:transition','./mc:AlternateContent[.//p:transition]']:
                ns=NS|{'mc':'http://schemas.openxmlformats.org/markup-compatibility/2006'}
                old=before.xpath(path,namespaces=ns);new=after.xpath(path,namespaces=ns)
                def signature(node):
                    return (node.tag,tuple(sorted(node.attrib.items())),(node.text or '').strip(),tuple(signature(c) for c in node))
                if [signature(n) for n in old]!=[signature(n) for n in new]:
                    raise ValueError('PowerPoint changed native timing/transition semantics; inspect '+str(work/'office'))
        previews=[]
        for spec in plan['previews']:
            previews.append(export_movie(candidate,_preview_root(output)/(spec['id']+'.mp4'),spec))
        shutil.copy2(candidate,output)
        report={'status':'passed','backend':'mac-pptmaster-powerpoint','output':str(output),'roundtrip':roundtrip,'validation':validation,
                'motion_receipt':str(work/'motion/receipt.json'),'previews':previews,
                'operators':{'carousels':len(plan['carousels']),'morph_links':len(plan['morph_links'])},
                'playback_verified':False,'video_exported':bool(previews),'visual_review':'required'}
        write_json(work/'report.json',report)
        return report
    except Exception as error:
        preview_root = _preview_root(output)
        if preview_root.is_dir():
            shutil.copytree(preview_root, work/'failed-video-evidence')
        write_json(work/'failure.json',{'status':'failed','error':str(error)})
        raise NativePowerPointError('mac_native',str(error)) from error


def probe_mac_powerpoint(baseline: Path, candidate: Path, output: Path) -> dict:
    results=[]
    for name,source in [('baseline',baseline),('candidate',candidate)]:
        dest=output/name;dest.mkdir(parents=True,exist_ok=True)
        copy=dest/'probe.pptx';shutil.copy2(source,copy)
        try:
            results.append({'name':name,'roundtrip':powerpoint_roundtrip(copy,dest),'validation':officecli_validate(copy)})
        except Exception as error:
            return {'status':'failed','backend':'mac-applescript','failed_stage':name,'error':str(error),'checks':results}
    return {'status':'passed','backend':'mac-applescript','checks':results}
