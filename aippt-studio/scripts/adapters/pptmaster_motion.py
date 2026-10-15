"""Invoke the checked-out ppt-master timing library in an isolated process."""
from __future__ import annotations
import dataclasses
import json
import sys
from pathlib import Path
from zipfile import ZipFile
from lxml import etree as E


def main():
    scripts, request_path, output_path = map(Path, sys.argv[1:4])
    sys.path.insert(0, str(scripts))
    import pptx_animations as animations
    from pptx_transitions import create_transition_xml
    from aippt_studio.animation_edit import _package, _slides, _xml, NS
    def fragment(text):
        wrapper='<root xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'+text+'</root>'
        return E.fromstring(wrapper.encode())[0]
    request = json.loads(request_path.read_text())
    plan = request['compiled_plan']
    source = Path(request['source'])
    parts = _package(source)
    names = _slides(parts)
    roots = {}
    for i in range(1, len(names)+1):
        roots[i] = _xml(parts[names[i-1]])
    def shape(slide, name):
        if slide not in roots:
            raise ValueError(f'unknown slide {slide}')
        matches = roots[slide].xpath('./p:cSld/p:spTree/*/*/p:cNvPr[@name=$name]', namespaces=NS, name=name)
        if len(matches) != 1:
            raise ValueError(f'shape name is missing or ambiguous: {slide}:{name}')
        return matches[0]
    # Resolve all original names before renaming (Morph identities may be targets).
    resolved = {}
    for spec in plan['effects']:
        key = (spec['slide'], spec['shape'])
        resolved[key] = int(shape(*key).get('id'))
    touched = set()
    for spec in plan['renames']:
        node = shape(spec['slide'], spec['from'])
        collision = roots[spec['slide']].xpath('.//p:cNvPr[@name=$name]', namespaces=NS, name=spec['to'])
        if collision and any(n is not node for n in collision):
            raise ValueError('rename would create ambiguous shape names')
        node.set('name', spec['to']); touched.add(spec['slide'])
    by_slide = {}
    for spec in plan['effects']:
        effect = {'fade':'entrance_fade','wipe':'entrance_wipe','fly':'entrance_fly',
                  'zoom':'entrance_zoom','swivel':'entrance_swivel','float':'entrance_float',
                  'bounce':'entrance_bounce','appear-exit':'exit_appear',
                  'motion-right':'path_right','growShrink':'emphasis_grow_shrink'}[spec['effect']]
        if spec.get('exit') and effect.startswith('entrance_'):
            effect = effect.replace('entrance_', 'exit_', 1)
        if spec.get('exit') and spec['effect'] in {'motion-right','growShrink'}:
            raise ValueError('exit flag on motion/scale requires an explicit exit effect')
        target = {'shape_id':resolved[(spec['slide'],spec['shape'])], 'effect':effect,
                  'duration':spec['duration'], 'delay_ms':round(spec['delay']*1000),
                  'trigger':{'onClick':'on-click','withPrevious':'with-previous','afterPrevious':'after-previous'}[spec['trigger']],
                  'auto_reverse':spec.get('autoreverse',False)}
        if spec.get('repeat') is not None:target['repeat_count']=spec['repeat']
        if spec['effect']=='growShrink':target['effect_options']={'size':spec.get('scale_percent',112)}
        if spec['effect']=='wipe':target['effect_options']={'direction':spec.get('direction','left')}
        elif spec.get('direction') is not None:target['effect_options']={'direction':spec['direction']}
        by_slide.setdefault(spec['slide'],[]).append((target,spec))
    summaries=[]
    for slide, rows in by_slide.items():
        root=roots[slide]
        existing=root.xpath('./p:timing | ./mc:AlternateContent[.//p:timing]',namespaces=NS|{'mc':'http://schemas.openxmlformats.org/markup-compatibility/2006'})
        if existing and not plan['replace_existing']:
            raise ValueError(f'slide {slide} already has timing; explicit replace_existing is required for sequence authoring')
        for old in existing:root.remove(old)
        targets=[t for t,s in rows]
        timing=fragment(animations.create_sequence_timing_xml(targets))
        extension=root.find('p:extLst',NS)
        if extension is not None:root.insert(root.index(extension),timing)
        else:root.append(timing)
        # Validate the original upstream emission before applying the explicit
        # custom motion distance. That distance is an isolated Studio overlay.
        animations.validate_generated_animation_xml(E.tostring(root),targets)
        effects=timing.xpath('.//p:cTn[@presetClass]',namespaces=NS)
        if len(effects)!=len(rows):raise ValueError('upstream row count mismatch')
        for node,(target,spec) in zip(effects,rows,strict=True):
            if spec['effect']=='motion-right':
                motions=node.xpath('.//p:animMotion',namespaces=NS)
                if len(motions)!=1:raise ValueError('ambiguous motion behavior')
                distance=spec.get('motion_by_x',0.55)
                motions[0].set('path',f'M 0 0 L {distance:g} 0 E')
                motions[0].set('origin','layout')
        errors=animations.validate_slide_animation_structure(animations.ET.fromstring(E.tostring(root)))
        if errors:raise ValueError('; '.join(errors))
        summaries.append({'slide':slide,'sequence':dataclasses.asdict(animations.read_slide_animation_sequence(E.tostring(root))), 'requested_effects':rows})
        touched.add(slide)
    for spec in plan['transitions']:
        slide=spec['slide']
        if slide not in roots:raise ValueError(f'unknown slide {slide}')
        root=roots[slide]
        old=root.xpath('./p:transition | ./mc:AlternateContent[.//p:transition]',namespaces=NS|{'mc':'http://schemas.openxmlformats.org/markup-compatibility/2006'})
        for node in old:root.remove(node)
        transition=fragment(create_transition_xml('morph',duration=spec['duration'],advance_after=spec.get('advance_after_seconds'),advance_on_click=spec['advance_on_click']))
        following=root.xpath('./p:timing | ./p:extLst',namespaces=NS)
        if following:root.insert(root.index(following[0]),transition)
        else:root.append(transition)
        touched.add(slide)
    with ZipFile(source) as src, ZipFile(output_path,'w') as dst:
        dst.comment=src.comment
        for item in src.infolist():
            if item.filename in {names[i-1] for i in touched}:
                slide=names.index(item.filename)+1
                dst.writestr(item,E.tostring(roots[slide],xml_declaration=True,encoding='UTF-8',standalone=True))
            else:dst.writestr(item,parts[item.filename])
    for slide in touched:
        if slide not in by_slide:continue
        with ZipFile(output_path) as z:
            errors=animations.validate_slide_animation_structure(animations.ET.fromstring(z.read(names[slide-1])))
            if errors:raise ValueError('; '.join(errors))
    print(json.dumps({'status':'candidate_requires_review','backend':'ppt-master-ooxml','slides_changed':sorted(touched),
                      'effects':summaries,'transitions':plan['transitions'],'playback_verified':False},ensure_ascii=False))

if __name__=='__main__':main()
