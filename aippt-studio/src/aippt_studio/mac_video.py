"""Actual Mac PowerPoint movie export, transcoded and decoded before publication."""
from __future__ import annotations
import hashlib
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from zipfile import ZipFile
from lxml import etree as E
from .config import PROJECT_ROOT
from .animation_edit import _package, _slides, _xml, NS
from .storage import write_json
from .runtime import resolve_executable, system_osascript


def media_executable() -> Path | None:
    candidates = tuple(sorted((PROJECT_ROOT / '.tools/media-runtime/imageio_ffmpeg/binaries').glob('ffmpeg-*')))
    return resolve_executable('ffmpeg', candidates)


def _subset(source: Path, target: Path, spec: dict):
    parts=_package(source); names=_slides(parts)
    selected=spec['slides']
    if not selected or any(type(i) is not int or not 1<=i<=len(names) for i in selected):raise ValueError('preview slide selection out of range')
    presentation=_xml(parts['ppt/presentation.xml']); listing=presentation.find('p:sldIdLst',NS)
    for index,slide in reversed(list(enumerate(list(listing),1))):
        if index not in selected:listing.remove(slide)
    parts['ppt/presentation.xml']=E.tostring(presentation,xml_declaration=True,encoding='UTF-8',standalone=True)
    if not spec['use_timings_and_narrations']:
        for index in selected:
            root=_xml(parts[names[index-1]])
            transitions=root.xpath('./p:transition | ./mc:AlternateContent//p:transition',namespaces=NS|{'mc':'http://schemas.openxmlformats.org/markup-compatibility/2006'})
            if not transitions:
                trans=E.Element(f"{{{NS['p']}}}transition")
                following=root.xpath('./p:timing | ./p:extLst',namespaces=NS)
                root.insert(root.index(following[0]) if following else len(root),trans);transitions=[trans]
            for trans in transitions:
                trans.set('advTm',str(round(spec['seconds_per_slide']*1000)));trans.set('advClick','0')
            parts[names[index-1]]=E.tostring(root,xml_declaration=True,encoding='UTF-8',standalone=True)
    with ZipFile(source) as src,ZipFile(target,'w') as dst:
        for item in src.infolist():dst.writestr(item,parts[item.filename])
    size=presentation.find('p:sldSz',NS)
    return float(size.get('cx'))/float(size.get('cy'))


def export_movie(source: Path, output: Path, spec: dict) -> dict:
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    binary=media_executable()
    if binary is None:
        raise ValueError('Mac video requires a compatible host FFmpeg runtime; run aippt-studio/scripts/setup-media.sh from the workspace root or set AIPPT_FFMPEG to a verified local executable')
    bridge = system_osascript()
    if bridge is None:
        raise ValueError('Mac video requires the verified system /usr/bin/osascript bridge')
    output=output.resolve(); output.parent.mkdir(parents=True,exist_ok=True)
    # PowerPoint's private container is not always writable by the Studio
    # process under current macOS privacy rules.  Use it when verified, or a
    # Work-owned cache that both the input deck and exported movie can use.
    from .mac_powerpoint import _powerpoint_temporary_root
    cache, cache_mode = _powerpoint_temporary_root(output.parent, fallback_name='.powerpoint-movie-cache')
    with tempfile.TemporaryDirectory(prefix='aippt-movie-',dir=output.parent) as staging, tempfile.TemporaryDirectory(prefix='aippt-movie-',dir=cache) as office:
        staging=Path(staging); pptx=Path(office)/(staging.name+'.pptx');mov=Path(office)/'native.mov'
        ratio=_subset(source,pptx,spec);height=spec['vertical_resolution'];width=round(height*ratio/2)*2
        shutil.copy2(pptx, output.with_suffix('.preview-input.pptx'))
        # The AppleScript save-as-movie bridge accepts only these three values.
        # Its native export settings are not configurable through this route;
        # width and frame rate below belong to FFmpeg's final transcode.
        result=subprocess.run([str(bridge),str(PROJECT_ROOT/'scripts/powerpoint_movie.applescript'),str(pptx),str(mov),pptx.stem],capture_output=True,text=True,timeout=spec['timeout_seconds'])
        log=output.with_suffix('.export.log');log.write_text(result.stdout+'\n'+result.stderr)
        if result.returncode or not mov.is_file() or mov.stat().st_size<100:
            raise ValueError('Mac PowerPoint did not produce a movie: '+(result.stderr or result.stdout)+'; inspect '+str(log))
        # A successful Apple event alone does not prove a file or valid video.
        staged=staging/'verified.mp4'
        command=[str(binary),'-nostdin','-y','-i',str(mov),'-vf',f'scale={width}:{height}', '-r',str(spec['frames_per_second']),'-c:v','libx264','-crf',str(round(35-spec['quality']*0.2)),'-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart',str(staged)]
        if not spec['use_timings_and_narrations']:
            command[-1:-1] = ['-an']
        transcode=subprocess.run(command,capture_output=True,text=True,timeout=spec['timeout_seconds'])
        output.with_suffix('.encode.log').write_text(transcode.stderr)
        if transcode.returncode or not staged.is_file():raise ValueError('native movie transcoding failed')
        decode=subprocess.run([str(binary),'-nostdin','-v','error','-xerror','-i',str(staged),'-f','null','-'],capture_output=True,text=True,timeout=spec['timeout_seconds'])
        if decode.returncode:raise ValueError('exported MP4 did not decode completely: '+decode.stderr)
        # Optional registered provider augments, never substitutes, full decode.
        media_check = {'status': 'not_installed', 'note': 'Mediabunny packet inspection did not run'}
        if (PROJECT_ROOT/'.tools/media-inspect-runtime/node_modules/mediabunny/package.json').is_file():
            from .upstream_tools import invoke
            inspection_dir = output.parent/(output.stem+'-media-'+uuid.uuid4().hex[:12])
            receipt = invoke('mediabunny', 'inspect', {'source': str(staged), 'require_video': True}, inspection_dir)
            media_check = {'status': 'passed', 'receipt': str(inspection_dir/'receipt.json'),
                           'result': receipt['result']}
        # Representative frames belong to the Work evidence, never presenter notes.
        # Re-exports of the same output name must never inherit old frames.
        # Use a unique per-run directory and bind every sampled file in the
        # receipt, preserving older successful evidence without mixing it in.
        frames=Path(tempfile.mkdtemp(prefix=output.stem+'-frames-',dir=output.parent))
        try:
            sampled=subprocess.run([str(binary),'-nostdin','-y','-i',str(staged),'-vf','fps=1,scale=640:-2','-frames:v','60',str(frames/'frame-%03d.png')],capture_output=True,text=True,timeout=120)
            frame_files=sorted(frames.glob('frame-*.png'))
            if sampled.returncode or not frame_files:raise ValueError('movie frame extraction failed')
            if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha256:
                raise ValueError('source presentation changed during Mac movie export; discard this preview and retry from a stable source')
        except Exception:
            shutil.rmtree(frames,ignore_errors=True)
            raise
        report={'id':spec['id'],'path':str(output),'slides':spec['slides'],'backend':'mac-powerpoint-movie',
                'source_sha256': source_sha256,
                'preview_input_sha256': hashlib.sha256(pptx.read_bytes()).hexdigest(),
                'native_movie_sha256':hashlib.sha256(mov.read_bytes()).hexdigest(),'ffmpeg_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
                'decoded':True,'frames':str(frames),'frame_count':len(frame_files),
                'frame_sha256s':{item.name:hashlib.sha256(item.read_bytes()).hexdigest() for item in frame_files},
                'visual_review':'pending',
                'preview_role':'slide_sequence_only','playback_verified':False,
                'animation_export_support':'object_animations_not_played_by_powerpoint_mac_movie',
                'timing_source':'not_verified',
                'timing_request': 'recorded_timings_and_narrations' if spec['use_timings_and_narrations'] else 'prepared_slide_advance',
                'prepared_slide_advance_ms': None if spec['use_timings_and_narrations'] else round(spec['seconds_per_slide']*1000),
                'native_export_options': {'resolution':'not_configured_by_applescript',
                                          'timings_and_narrations':'not_configured_by_applescript'},
                'width':width,'height':height,'fps':spec['frames_per_second'],
                'media_inspection':media_check,
                'resolution_control':'FFmpeg transcode; Office native export uses its available default',
                'audio_policy':'carry_native_movie_audio_if_present_unverified' if spec['use_timings_and_narrations'] else 'muted_by_ffmpeg',
                'temporary_cache_mode': cache_mode}
        staged.replace(output);report['sha256']=hashlib.sha256(output.read_bytes()).hexdigest();report['bytes']=output.stat().st_size
        write_json(output.with_suffix('.video.json'),report)
        return report
