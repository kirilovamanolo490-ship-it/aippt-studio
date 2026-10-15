"""Conservative, hash-bound static slide grafting without rewriting retained parts."""
from __future__ import annotations
import hashlib
import copy
import io
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import tempfile
import zipfile
import xml.etree.ElementTree as ET

P = 'http://schemas.openxmlformats.org/presentationml/2006/main'
R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
A = 'http://schemas.openxmlformats.org/drawingml/2006/main'
CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
_ALLOWED = {'slideLayout', 'slideMaster', 'theme', 'image', 'chart', 'chartStyle', 'chartColorStyle', 'themeOverride', 'hdphoto', 'tags'}

class UnsafeSlideImport(ValueError):
    """The package cannot be migrated under the static-page contract."""

def _xml(data):
    if b'<!DOCTYPE' in data or b'<!ENTITY' in data:
        raise UnsafeSlideImport('DTD/entity declarations are forbidden')
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise UnsafeSlideImport(f'Invalid XML: {exc}') from exc

def _bytes(root):
    data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    # Some Office package readers require the OPC roots in the default
    # namespace. Preserve that conventional serialization without global
    # ElementTree namespace mutations (which would race concurrent builds).
    if root.tag in {f'{{{CT}}}Types', f'{{{REL}}}Relationships'}:
        match = re.search(br'<([A-Za-z0-9_]+):(?:Types|Relationships)', data)
        if match:
            prefix = match.group(1)
            data = data.replace(b'xmlns:' + prefix + b'=', b'xmlns=')
            data = data.replace(b'<' + prefix + b':', b'<').replace(b'</' + prefix + b':', b'</')
    return data

def _rels(part):
    p = PurePosixPath(part)
    return str(p.parent / '_rels' / (p.name + '.rels'))

def _target(part, target):
    if '\\' in target or '?' in target or '#' in target or ':' in target:
        raise UnsafeSlideImport(f'Unsafe relationship target: {target}')
    resolved = posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(posixpath.dirname(part), target))
    if resolved == '..' or resolved.startswith('../'):
        raise UnsafeSlideImport('Relationship escapes package')
    return resolved

def _read(path, expected):
    if Path(path).stat().st_size > 512 * 1024 * 1024:
        raise UnsafeSlideImport('Source ZIP exceeds 512 MiB input limit')
    raw = Path(path).read_bytes()
    if len(raw) > 512 * 1024 * 1024:
        raise UnsafeSlideImport('Source ZIP exceeds 512 MiB input limit')
    digest = hashlib.sha256(raw).hexdigest()
    if not expected or digest != expected.lower():
        raise UnsafeSlideImport(f'SHA256 mismatch: {path}')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = z.namelist()
            if len(names) != len(set(names)):
                raise UnsafeSlideImport('Duplicate ZIP entries')
            if len(names) > 20000 or sum(i.file_size for i in z.infolist()) > 512 * 1024 * 1024:
                raise UnsafeSlideImport('ZIP exceeds migration resource limits')
            for n in names:
                if '\\' in n or n.startswith('/') or '..' in PurePosixPath(n).parts:
                    raise UnsafeSlideImport('Unsafe ZIP entry')
            return {n: z.read(n) for n in names}, digest
    except zipfile.BadZipFile as exc:
        raise UnsafeSlideImport('Invalid PPTX ZIP') from exc

def _slides(parts):
    pres = _xml(parts['ppt/presentation.xml'])
    rel = _xml(parts['ppt/_rels/presentation.xml.rels'])
    targets = {r.get('Id'): _target('ppt/presentation.xml', r.get('Target', '')) for r in rel}
    ids = pres.find(f'{{{P}}}sldIdLst')
    if ids is None:
        raise UnsafeSlideImport('Missing slide list')
    return [targets[n.get(f'{{{R}}}id')] for n in ids], pres, rel

def _static(part, data, *, allow_local_timing=False):
    if part.endswith('.xml'):
        root = _xml(data)
        for el in root.iter():
            local = el.tag.split('}')[-1]
            if local == 'timing' and allow_local_timing:
                if allow_local_timing == 'preserve':
                    for node in el.iter():
                        if not node.tag.startswith('{' + P + '}') or node.tag == f'{{{P}}}extLst' or any(name.startswith('{' + R + '}') or name.split('}')[-1] in {'sldId', 'slideId'} for name in node.attrib) or (node.tag == f'{{{P}}}sldTgt' and node.attrib):
                            raise UnsafeSlideImport(f'Cross-page timing reference: {part}')
                else:
                    _safe_timing(root, el, part)
                continue
            if local in {'timing', 'oleObj', 'audio', 'video', 'audioFile', 'videoFile', 'wavAudioFile', 'morph', 'morphTransition'}:
                raise UnsafeSlideImport(f'Unsupported nonstatic content {local} in {part}')
            if local == 'transition':
                _ordinary_transition(el, part)


def _plain_tags(parts, part):
    """Preserve only ordinary name/value tag metadata, never an arbitrary graph."""
    if part not in parts:
        raise UnsafeSlideImport('Missing tags dependency')
    root = _xml(parts[part])
    if (root.tag != f'{{{P}}}tagLst' or root.attrib or (root.text or '').strip()
            or any(node.tag != f'{{{P}}}tag' or set(node.attrib) != {'name', 'val'}
                   or len(node) or (node.text or '').strip() or (node.tail or '').strip()
                   for node in root)):
        raise UnsafeSlideImport('Unsupported non-plain tags metadata')
    if _rels(part) in parts:
        rels = _xml(parts[_rels(part)])
        if (rels.tag != f'{{{REL}}}Relationships' or rels.attrib
                or (rels.text or '').strip() or len(rels)):
            raise UnsafeSlideImport('Tags metadata must have a valid empty relationship list')

def replace_slides(primary_pptx: Path, output_pptx: Path, *, primary_sha256: str, overrides: list[dict]) -> dict:
    """Replace 1–2 numbered slides; new output only, source hashes mandatory.

    Each override contains target_slide, donor_pptx, donor_sha256, donor_slide.
    Imported dependencies are assigned fresh names; retained base ZIP entry bytes
    are preserved except presentation.xml, its relationships and Content Types.
    Notes, external links, embedded packages and Morph are refused. Explicit
    omit_donor_notes=True omits only the imported slide's notes relationship;
    it does not import old talk tracks, and is recorded in the receipt.
    Donor timing is refused by default. Explicit staticize_donor_timing=True
    authorizes removing verified slide-local shape animation; callers must use
    this only when their current task does not require dynamic playback.
    dynamic_required=True prevents staticization. Master/layout timing stays
    forbidden. Retained neighboring local timing is preserved byte for byte.
    allow_minor_size_delta=True permits at most 0.5 mm difference per axis
    and 0.001 aspect ratio difference, keeping primary size and object geometry.
    The receipt reports the difference and potential edge clipping.
    theme_accent_overrides supports accent1..accent6 and six-digit HEX.
    Explicit scheme fills/strokes in imported slide/layout/master XML are
    resolved to those RGB values with their transforms preserved; style refs
    remain theme based. direct_rgb_overrides maps six-digit old HEX to new HEX.
    Imported media and all primary non-target parts are never recolored.
    Ordinary target-page transitions are retained and reported; donor transitions
    are omitted under that policy. Neighboring ordinary transitions are allowed.
    """
    primary_pptx, output_pptx = Path(primary_pptx), Path(output_pptx)
    if output_pptx.exists() or output_pptx.resolve() == primary_pptx.resolve():
        raise UnsafeSlideImport('Output must be a new isolated candidate')
    if not 1 <= len(overrides) <= 2:
        raise UnsafeSlideImport('Specify one or two overrides')
    base, base_sha = _read(primary_pptx, primary_sha256)
    slides, pres, presrels = _slides(base)
    ct = _xml(base['[Content_Types].xml'])
    masterlist = pres.find(f'{{{P}}}sldMasterIdLst')
    seen = set()
    receipts = []
    imported = set()
    for ordinal, override in enumerate(overrides, 1):
        target_number = override['target_slide']
        if isinstance(target_number, bool) or not isinstance(target_number, int) or not 1 <= target_number <= len(slides) or target_number in seen:
            raise UnsafeSlideImport('Invalid or repeated target slide')
        seen.add(target_number)
        # Timing/Morph can refer across pages; ordinary transitions do not.
        for neighbor in range(max(0, target_number - 2), min(len(slides), target_number + 1)):
            _static(slides[neighbor], base[slides[neighbor]], allow_local_timing='preserve')
        donor, donor_sha = _read(override['donor_pptx'], override['donor_sha256'])
        donor_slides, donor_pres, _ = _slides(donor)
        size_policy = _size_policy(pres, donor_pres, override.get('allow_minor_size_delta', False))
        source_number = override['donor_slide']
        if isinstance(source_number, bool) or not isinstance(source_number, int) or not 1 <= source_number <= len(donor_slides):
            raise UnsafeSlideImport('Invalid donor slide')
        source = donor_slides[source_number - 1]
        destination = slides[target_number - 1]
        original_transition = _xml(base[destination]).find(f'{{{P}}}transition')
        donor_transition = _xml(donor[source]).find(f'{{{P}}}transition')
        mapping = {source: destination}
        queue = [source]
        graph = {}
        staticized = []
        theme_colors, direct_colors = _color_options(override)
        color_changes = []
        staticize = override.get('staticize_donor_timing', False)
        omit_notes = override.get('omit_donor_notes', False)
        if type(omit_notes) is not bool:
            raise UnsafeSlideImport('omit_donor_notes must be a boolean')
        omitted_notes = []
        if not isinstance(staticize, bool):
            raise UnsafeSlideImport('staticize_donor_timing must be a boolean')
        if staticize and override.get('dynamic_required', False):
            raise UnsafeSlideImport('Cannot staticize a task requiring dynamic playback')
        while queue:
            part = queue.pop()
            if part not in donor:
                raise UnsafeSlideImport(f'Missing dependency: {part}')
            _static(part, donor[part], allow_local_timing=staticize and part == source)
            relationships = _xml(donor[_rels(part)]) if _rels(part) in donor else None
            graph[part] = relationships
            if relationships is not None:
                ids = set()
                for rel in list(relationships):
                    rid = rel.get('Id')
                    if not rid or rid in ids:
                        raise UnsafeSlideImport('Duplicate or absent relationship ID')
                    ids.add(rid)
                    kind = rel.get('Type', '').rsplit('/', 1)[-1]
                    if kind == 'notesSlide' and omit_notes and part == source and rel.get('TargetMode') != 'External':
                        note_part = _target(part, rel.get('Target', ''))
                        omitted_notes.append({'source_part': note_part, 'relationship_id': rid,
                                              'source_sha256': hashlib.sha256(donor[note_part]).hexdigest() if note_part in donor else None})
                        relationships.remove(rel)
                        continue
                    if rel.get('TargetMode') == 'External' or kind not in _ALLOWED:
                        raise UnsafeSlideImport(f'Unsupported dependency: {kind}')
                    dependency = _target(part, rel.get('Target', ''))
                    if kind == 'tags':
                        _plain_tags(donor, dependency)
                    if dependency not in mapping:
                        old = PurePosixPath(dependency)
                        candidate = str(old.parent / f'hybrid{ordinal}_{donor_sha[:12]}_{old.name}')
                        counter = 0
                        while candidate in base or candidate in mapping.values():
                            counter += 1
                            candidate = str(old.parent / f'hybrid{ordinal}_{donor_sha[:12]}_{counter}_{old.name}')
                        mapping[dependency] = candidate
                        queue.append(dependency)
        donorct = _xml(donor['[Content_Types].xml'])
        defaults = {x.get('Extension'): x.get('ContentType') for x in donorct if x.tag.endswith('Default')}
        types = {x.get('PartName').lstrip('/'): x.get('ContentType') for x in donorct if x.tag.endswith('Override')}
        existingtypes = {x.get('PartName') for x in ct if x.tag.endswith('Override')}
        for old, new in mapping.items():
            imported.add(new)
            base[new] = donor[old]
            if old == source:
                grafted = _xml(base[new])
                if staticize:
                    for timing in list(grafted.findall(f'{{{P}}}timing')):
                        _safe_timing(grafted, timing, old)
                        staticized.append({'source_part': old, 'output_part': new, 'removed_node': 'p:timing', 'removed_node_count': sum(1 for _ in timing.iter()), 'removed_node_sha256': hashlib.sha256(_bytes(timing)).hexdigest()})
                        grafted.remove(timing)
                for transition in list(grafted.findall(f'{{{P}}}transition')):
                    grafted.remove(transition)
                if original_transition is not None:
                    extension = grafted.find(f'{{{P}}}extLst')
                    index = list(grafted).index(extension) if extension is not None else len(grafted)
                    grafted.insert(index, copy.deepcopy(original_transition))
                base[new] = _bytes(grafted)
            base[new], color_change = _recolor(base[new], old, new, theme_colors, direct_colors)
            if color_change is not None:
                color_changes.append(color_change)
            relationships = graph[old]
            if relationships is not None:
                for rel in relationships:
                    target = mapping[_target(old, rel.get('Target', ''))]
                    rel.set('Target', posixpath.relpath(target, posixpath.dirname(new)))
                base[_rels(new)] = _bytes(relationships)
            else:
                base.pop(_rels(new), None)
            contenttype = types.get(old) or defaults.get(PurePosixPath(old).suffix.lstrip('.'))
            if not contenttype:
                raise UnsafeSlideImport(f'Missing content type: {old}')
            if '/' + new not in existingtypes:
                ET.SubElement(ct, f'{{{CT}}}Override', {'PartName': '/' + new, 'ContentType': contenttype})
                existingtypes.add('/' + new)
            if contenttype.endswith('slideMaster+xml'):
                if masterlist is None:
                    masterlist = ET.Element(f'{{{P}}}sldMasterIdLst')
                    pres.insert(0, masterlist)
                usedids = {r.get('Id') for r in presrels}
                rid = f'rIdHybrid{ordinal}_{len(masterlist)}'
                while rid in usedids:
                    rid += '_'
                ET.SubElement(presrels, f'{{{REL}}}Relationship', {'Id': rid, 'Type': R + '/slideMaster', 'Target': posixpath.relpath(new, 'ppt')})
                masterid = max([2147483647] + [int(x.get('id')) for x in masterlist]) + 1
                ET.SubElement(masterlist, f'{{{P}}}sldMasterId', {'id': str(masterid), f'{{{R}}}id': rid})
        receipts.append({'target_slide': target_number, 'donor_slide': source_number, 'donor_sha256': donor_sha, 'imported_parts': len(mapping), 'part_map': mapping, 'color_adaptation': {'theme_accent_overrides': theme_colors, 'direct_rgb_overrides': direct_colors, 'part_changes': color_changes, 'bitmap_recoloring': False, 'unchanged_media_parts': [new for old, new in mapping.items() if old.startswith('ppt/media/')]}, 'size_policy': size_policy, 'staticize_donor_timing': staticize, 'staticized_parts': staticized, 'transition_policy': 'preserve_primary_ordinary_transition', 'primary_transition_preserved': original_transition is not None, 'donor_transition_omitted': donor_transition is not None})
        receipts[-1].update(omit_donor_notes=omit_notes, omitted_donor_notes=omitted_notes)
    base['ppt/presentation.xml'] = _bytes(pres)
    base['ppt/_rels/presentation.xml.rels'] = _bytes(presrels)
    base['[Content_Types].xml'] = _bytes(ct)
    _validate_import(base, imported, pres, presrels)
    output_pptx.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.hybrid-', suffix='.pptx', dir=output_pptx.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            # Normalize envelope metadata, not part contents. A fixed DOS
            # timestamp and sorted entry order make repeated builds byte-stable
            # under the same Python/zlib runtime, regardless of wall-clock time.
            for name in sorted(base):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o600 << 16
                z.writestr(info, base[name], compresslevel=9)
        with zipfile.ZipFile(temporary) as check:
            readback = {name: check.read(name) for name in check.namelist()}
        _validate_import(readback, imported, _xml(readback['ppt/presentation.xml']),
                         _xml(readback['ppt/_rels/presentation.xml.rels']))
        for receipt in receipts:
            for changed in receipt['color_adaptation']['part_changes']:
                if hashlib.sha256(readback[changed['output_part']]).hexdigest() != changed['after_sha256']:
                    raise UnsafeSlideImport('Color adaptation readback SHA mismatch')
        # Hard link publishes atomically and cannot clobber a concurrently created candidate.
        os.link(temporary, output_pptx)
    finally:
        os.unlink(temporary)
    return {'status': 'candidate', 'output_pptx': str(output_pptx), 'output_sha256': hashlib.sha256(output_pptx.read_bytes()).hexdigest(), 'primary_sha256': base_sha, 'overrides': receipts, 'native_review': 'not_performed'}


def _validate_import(parts, imported, pres, presrels):
    """Validate the serialized graph and master registration before publication."""
    relationships = {r.get('Id'): r for r in presrels}
    if len(relationships) != len(presrels):
        raise UnsafeSlideImport('Duplicate presentation relationship IDs')
    masterlist = pres.find(f'{{{P}}}sldMasterIdLst')
    ids = set()
    if masterlist is not None:
        for master in masterlist:
            mid = master.get('id')
            if mid in ids or not mid or not 2147483648 <= int(mid) <= 4294967295:
                raise UnsafeSlideImport('Invalid or duplicate slide master ID')
            ids.add(mid)
            rel = relationships.get(master.get(f'{{{R}}}id'))
            if rel is None or not rel.get('Type', '').endswith('/slideMaster') or _target('ppt/presentation.xml', rel.get('Target', '')) not in parts:
                raise UnsafeSlideImport('Unregistered slide master dependency')
    registered = {_target('ppt/presentation.xml', rel.get('Target', '')) for rel in presrels if rel.get('Type', '').endswith('/slideMaster')}
    for part in imported:
        rels = _xml(parts[_rels(part)]) if _rels(part) in parts else []
        ids = {rel.get('Id') for rel in rels}
        if len(ids) != len(rels):
            raise UnsafeSlideImport('Duplicate imported relationship IDs')
        for rel in rels:
            if _target(part, rel.get('Target', '')) not in parts:
                raise UnsafeSlideImport('Imported relationship has missing target')
        if part.endswith('.xml'):
            root = _xml(parts[part])
            for element in root.iter():
                for attribute, value in element.attrib.items():
                    if attribute.startswith('{' + R + '}') and value not in ids:
                        raise UnsafeSlideImport('Imported XML references an absent relationship')
            if root.tag == f'{{{P}}}sldMaster' and part not in registered:
                raise UnsafeSlideImport('Imported master absent from presentation list')


def _ordinary_transition(element, part):
    # ECMA-376 ordinary visual page effects. Unknown extension effects may
    # include Morph or relationships across pages, so require explicit support.
    effects = {'transition', 'blinds', 'checker', 'circle', 'comb', 'cover',
               'cut', 'diamond', 'dissolve', 'fade', 'newsflash', 'plus',
               'pull', 'push', 'random', 'randomBar', 'split', 'strips',
               'wedge', 'wheel', 'wipe', 'zoom'}
    for node in element.iter():
        if not node.tag.startswith('{' + P + '}') or node.tag.split('}')[-1] not in effects:
            raise UnsafeSlideImport(f'Unsupported dynamic transition in {part}: {node.tag}')
        if any(name.startswith('{' + R + '}') for name in node.attrib):
            raise UnsafeSlideImport(f'Transition relationship is not static in {part}')


def _safe_timing(root, timing, part):
    """Accept only slide-local object animation, with verifiable targets."""
    if root.tag != f'{{{P}}}sld':
        raise UnsafeSlideImport(f'Layout/master timing cannot be staticized: {part}')
    tags = {'timing', 'tnLst', 'par', 'seq', 'cTn', 'childTnLst',
            'subTnLst', 'stCondLst', 'endCondLst', 'prevCondLst',
            'nextCondLst', 'cond', 'tn', 'tgtEl', 'spTgt', 'sldTgt',
            'set', 'anim', 'animEffect', 'animMotion', 'animRot',
            'animScale', 'animClr', 'cBhvr', 'attrNameLst', 'attrName',
            'to', 'from', 'by', 'strVal', 'fltVal', 'intVal', 'boolVal',
            'tavLst', 'tav', 'val', 'bldLst', 'bldP', 'bldDgm',
            'bldOleChart', 'txEl', 'pRg', 'charRg', 'rtn', 'pt'}
    shapeids = {n.get('id') for n in root.iter() if n.tag == f'{{{P}}}cNvPr'}
    nodeids = [n.get('id') for n in timing.iter() if n.tag == f'{{{P}}}cTn']
    if len(nodeids) != len(set(nodeids)) or None in nodeids:
        raise UnsafeSlideImport(f'Uncertain timing node IDs: {part}')
    parents = {child: parent for parent in timing.iter() for child in parent}
    for node in timing.iter():
        local = node.tag.split('}')[-1]
        if not node.tag.startswith('{' + P + '}') or local not in tags:
            raise UnsafeSlideImport(f'Unsupported timing control {local}: {part}')
        if any(name.startswith('{' + R + '}') or name.split('}')[-1] in {'sldId', 'slideId'} for name in node.attrib):
            raise UnsafeSlideImport(f'Cross-part timing reference: {part}')
        if node.get('spid') is not None and node.get('spid') not in shapeids:
            raise UnsafeSlideImport(f'Uncertain timing shape target: {part}')
        if local == 'tn' and node.get('val') not in nodeids:
            raise UnsafeSlideImport(f'Uncertain timing node reference: {part}')
        if local == 'cond' and node.get('evt') not in {None, 'onBegin', 'onEnd', 'onNext', 'onPrev'}:
            raise UnsafeSlideImport(f'Interactive timing trigger cannot be staticized: {part}')
        if local == 'sldTgt':
            condition = parents.get(node)
            if condition is not None and condition.tag == f'{{{P}}}tgtEl':
                condition = parents.get(condition)
            if condition is None or condition.tag != f'{{{P}}}cond' or condition.get('evt') not in {'onNext', 'onPrev'} or node.attrib:
                raise UnsafeSlideImport(f'Uncertain slide timing target: {part}')


def _size_policy(primary, donor, allow_minor):
    if not isinstance(allow_minor, bool):
        raise UnsafeSlideImport('allow_minor_size_delta must be a boolean')
    try:
        psize = primary.find(f'{{{P}}}sldSz')
        dsize = donor.find(f'{{{P}}}sldSz')
        px, py = int(psize.get('cx')), int(psize.get('cy'))
        dx, dy = int(dsize.get('cx')), int(dsize.get('cy'))
        if min(px, py, dx, dy) <= 0: raise ValueError()
    except (AttributeError, TypeError, ValueError) as exc:
        raise UnsafeSlideImport('Invalid slide dimensions') from exc
    delta = [abs(px - dx), abs(py - dy)]
    ratio_delta = abs(px / py - dx / dy)
    if any(delta) and (not allow_minor or max(delta) > 18000 or ratio_delta > 0.001):
        raise UnsafeSlideImport('Slide sizes differ')
    return {'allow_minor_size_delta': allow_minor, 'primary_emu': [px, py],
            'donor_emu': [dx, dy], 'absolute_delta_emu': delta,
            'absolute_delta_mm': [v / 36000 for v in delta],
            'aspect_ratio_delta': ratio_delta, 'objects_scaled': False,
            'output_size': 'primary', 'potential_edge_clipping': any(delta)}


def _color_options(override):
    def hexcolor(value):
        if not isinstance(value, str) or re.fullmatch(r'[0-9A-Fa-f]{6}', value) is None:
            raise UnsafeSlideImport('Colors must be exactly six hexadecimal digits')
        return value.upper()
    theme = override.get('theme_accent_overrides', {})
    direct = override.get('direct_rgb_overrides', {})
    if not isinstance(theme, dict) or not isinstance(direct, dict):
        raise UnsafeSlideImport('Color overrides must be dictionaries')
    if any(slot not in {f'accent{i}' for i in range(1, 7)} for slot in theme):
        raise UnsafeSlideImport('Only accent1 through accent6 theme slots are allowed')
    normalized = {}
    for old, new in direct.items():
        old, new = hexcolor(old), hexcolor(new)
        if old in normalized and normalized[old] != new:
            raise UnsafeSlideImport('Conflicting direct RGB color overrides')
        normalized[old] = new
    return {slot: hexcolor(color) for slot, color in theme.items()}, normalized


def _recolor(data, source, output, theme, direct):
    if not theme and not direct:
        return data, None
    if not source.endswith('.xml'):
        return data, None
    root = _xml(data)
    if root.tag not in {f'{{{P}}}sld', f'{{{P}}}sldLayout', f'{{{P}}}sldMaster', f'{{{A}}}theme', f'{{{A}}}themeOverride'}:
        return data, None
    before = hashlib.sha256(data).hexdigest()
    count = 0
    slot_count = 0
    direct_count = 0
    scheme_count = 0
    for node in root.iter(f'{{{A}}}srgbClr'):
        old = node.get('val', '').upper()
        if old in direct and old != direct[old]:
            node.set('val', direct[old])
            count += 1
            direct_count += 1
    if root.tag in {f'{{{P}}}sld', f'{{{P}}}sldLayout', f'{{{P}}}sldMaster'}:
        parents = {child: parent for parent in root.iter() for child in parent}
        for node in root.iter(f'{{{A}}}schemeClr'):
            parent = parents.get(node)
            # Keep style-reference color placeholders native. Some advisory
            # readers fail to resolve a direct color inside fillRef/lnRef.
            if parent is not None and parent.tag.split('}')[-1] in {'lnRef', 'fillRef', 'effectRef', 'fontRef'}:
                continue
            slot = node.get('val')
            if slot in theme:
                node.tag = f'{{{A}}}srgbClr'
                node.set('val', theme[slot])
                count += 1
                scheme_count += 1
    if root.tag in {f'{{{A}}}theme', f'{{{A}}}themeOverride'}:
        for scheme in root.iter(f'{{{A}}}clrScheme'):
            for slot, color in theme.items():
                element = scheme.find(f'{{{A}}}{slot}')
                if element is None or len(element) != 1 or element[0].tag not in {f'{{{A}}}srgbClr', f'{{{A}}}sysClr'}:
                    raise UnsafeSlideImport(f'Unsupported or missing theme color slot {slot}: {source}')
                node = element[0]
                if node.tag != f'{{{A}}}srgbClr' or node.get('val', '').upper() != color:
                    node.tag = f'{{{A}}}srgbClr'
                    node.attrib.clear()
                    node.set('val', color)
                    count += 1
                    slot_count += 1
    after_data = _bytes(root) if count else data
    return after_data, {'source_part': source, 'output_part': output,
                        'modification_count': count, 'theme_slot_count': slot_count,
                        'direct_rgb_count': direct_count, 'scheme_reference_count': scheme_count, 'before_sha256': before,
                        'after_sha256': hashlib.sha256(after_data).hexdigest()}
