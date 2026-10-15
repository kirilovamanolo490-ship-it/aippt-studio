"""Read-only structural inspection of PowerPoint motion and transitions.

The package is opened once and XML parts are consumed from ZipExtFile streams;
the deck is never copied into an in-memory part dictionary. This module reports
OOXML evidence only. It does not simulate or verify PowerPoint playback.
"""
from __future__ import annotations

import hashlib
import posixpath
import re
from pathlib import Path
from typing import Any
from zipfile import BadZipFile, ZipFile
import xml.etree.ElementTree as ET

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
NS = {"p": P, "a": A, "r": R, "mc": MC}
SCHEMA = "aippt.motion-library.v1"
_OBJECT_TAGS = {"sp", "pic", "grpSp", "graphicFrame", "cxnSp"}
_BEHAVIOR_TAGS = {"anim", "animEffect", "animMotion", "animScale", "animRot", "animClr", "set", "cmd", "audio", "video"}
_MEDIA_REL_TYPES = {"audio", "video", "image", "media"}


class MotionLibraryError(ValueError):
    """The source is not a readable PPTX package with a presentation order."""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


_CAPTURE_TAGS = {_q(P, "spTree"), _q(P, "timing"), _q(P, "transition")}


def _source_sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _part_for_relationships(owner: str) -> str:
    parent, leaf = posixpath.split(owner)
    return posixpath.join(parent, "_rels", leaf + ".rels")


def _normalize_target(owner: str, target: str) -> str | None:
    if not target or ":" in target.split("/", 1)[0]:
        return None
    base = "" if target.startswith("/") else posixpath.dirname(owner)
    normalized = posixpath.normpath(posixpath.join(base, target.lstrip("/")))
    if normalized == ".." or normalized.startswith("../"):
        return None
    return normalized.lstrip("/")


def _read_rels(archive: ZipFile, owner: str) -> dict[str, dict[str, Any]]:
    rel_part = _part_for_relationships(owner)
    try:
        with archive.open(rel_part) as stream:
            root = ET.parse(stream).getroot()
    except KeyError:
        return {}
    except ET.ParseError as error:
        raise MotionLibraryError(f"invalid_relationships_xml:{rel_part}") from error
    result: dict[str, dict[str, Any]] = {}
    for node in root.findall(_q(PKG_REL, "Relationship")):
        rid = node.get("Id")
        if not rid:
            continue
        external = node.get("TargetMode") == "External"
        target = node.get("Target", "")
        result[rid] = {
            "id": rid,
            "type": node.get("Type", "").rsplit("/", 1)[-1],
            "target": None if external else _normalize_target(owner, target),
            "target_raw": target,
            "external": external,
        }
    return result


def _presentation_order(archive: ZipFile) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        with archive.open("ppt/presentation.xml") as stream:
            root = ET.parse(stream).getroot()
    except (KeyError, ET.ParseError) as error:
        raise MotionLibraryError("missing_or_invalid_presentation_xml") from error
    rels = _read_rels(archive, "ppt/presentation.xml")
    size = root.find("p:sldSz", NS)
    slide_size = {"width_emu": _int_attr(size, "cx"), "height_emu": _int_attr(size, "cy")} if size is not None else None
    ordered: list[dict[str, Any]] = []
    for node in root.findall("p:sldIdLst/p:sldId", NS):
        rid = node.get(_q(R, "id"), "")
        rel = rels.get(rid)
        if rel is None or rel["type"] != "slide" or rel["external"] or not rel["target"]:
            continue
        part = rel["target"]
        try:
            archive.getinfo(part)
        except KeyError:
            continue
        hidden = node.get("show") in {"0", "false"}
        ordered.append({"part": part, "relationship_id": rid, "slide_id": node.get("id"), "hidden": hidden})
    if not ordered:
        raise MotionLibraryError("presentation_has_no_resolvable_slides")
    return ordered, slide_size or {}


def _int_attr(node: ET.Element | None, key: str) -> int | None:
    if node is None:
        return None
    try:
        return int(node.get(key, ""))
    except (TypeError, ValueError):
        return None


def _xfrm(element: ET.Element) -> dict[str, Any] | None:
    candidates = [
        element.find("p:spPr/a:xfrm", NS), element.find("p:xfrm", NS),
        element.find("p:grpSpPr/a:xfrm", NS),
    ]
    transform = next((item for item in candidates if item is not None), None)
    if transform is None:
        return None
    off = transform.find("a:off", NS)
    ext = transform.find("a:ext", NS)
    result = {
        "x_emu": _int_attr(off, "x"), "y_emu": _int_attr(off, "y"),
        "width_emu": _int_attr(ext, "cx"), "height_emu": _int_attr(ext, "cy"),
        "rotation_60000deg": _int_attr(transform, "rot"),
        "flip_h": transform.get("flipH"), "flip_v": transform.get("flipV"),
    }
    ch_off = transform.find("a:chOff", NS)
    ch_ext = transform.find("a:chExt", NS)
    if ch_off is not None or ch_ext is not None:
        result["child_coordinate_space"] = {
            "x_emu": _int_attr(ch_off, "x"), "y_emu": _int_attr(ch_off, "y"),
            "width_emu": _int_attr(ch_ext, "cx"), "height_emu": _int_attr(ch_ext, "cy"),
        }
    return result


def _object_id_name(element: ET.Element) -> tuple[str | None, str | None]:
    for node in element.iter():
        if _local(node.tag) == "cNvPr":
            return node.get("id"), node.get("name")
    return None, None


def _object_text(element: ET.Element) -> dict[str, Any]:
    paragraphs: list[dict[str, Any]] = []
    font_references: list[dict[str, Any]] = []
    for paragraph in element.iter(_q(A, "p")):
        pieces: list[str] = []
        for child in list(paragraph):
            tag = _local(child.tag)
            if tag in {"r", "fld"}:
                pieces.extend((text.text or "") for text in child.iter(_q(A, "t")))
                props = child.find("a:rPr", NS)
                font_references.extend(_font_refs(props, "run"))
            elif tag == "br":
                pieces.append("\n")
        font_references.extend(_font_refs(paragraph.find("a:pPr/a:defRPr", NS), "paragraph_default"))
        font_references.extend(_font_refs(paragraph.find("a:endParaRPr", NS), "end_paragraph"))
        text = "".join(pieces)
        paragraphs.append({"text": text, "properties": _attrs(paragraph.find("a:pPr", NS))})
    return {"text": "\n".join(p["text"] for p in paragraphs), "paragraphs": paragraphs,
            "font_references": font_references}


def _font_refs(properties: ET.Element | None, origin: str) -> list[dict[str, Any]]:
    if properties is None:
        return []
    refs = []
    for script in ("latin", "ea", "cs"):
        node = properties.find(f"a:{script}", NS)
        if node is not None and node.get("typeface"):
            refs.append({"script": script, "typeface": node.get("typeface"), "origin": origin,
                         "theme_reference": node.get("typeface", "").startswith("+")})
    return refs


def _attrs(node: ET.Element | None) -> dict[str, str]:
    return dict(node.attrib) if node is not None else {}


def _object_records(sp_tree: ET.Element) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    warnings: list[str] = []

    def walk(parent: ET.Element, ancestors: list[dict[str, str | None]]) -> None:
        for child in list(parent):
            kind = _local(child.tag)
            if kind not in _OBJECT_TAGS:
                continue
            object_id, name = _object_id_name(child)
            record: dict[str, Any] = {
                "order": len(records) + 1,
                "id": object_id, "name": name or "", "type": kind,
                "parent_group_id": ancestors[-1]["id"] if ancestors else None,
                "group_path": list(ancestors), "geometry": _xfrm(child),
                **(_object_text(child) if kind != "grpSp" else {"text": "", "paragraphs": [], "font_references": []}),
            }
            if kind == "pic":
                blip = child.find(".//a:blip", NS)
                if blip is not None:
                    record["image_relationship_ids"] = [v for k, v in blip.attrib.items() if k.startswith("{" + R + "}")]
            records.append(record)
            if kind == "grpSp":
                walk(child, [*ancestors, {"id": object_id, "name": name}])

    walk(sp_tree, [])
    seen: set[str] = set()
    for record in records:
        oid = record["id"]
        if oid is None:
            warnings.append("object_without_cNvPr_id")
        elif oid in seen:
            warnings.append(f"duplicate_object_id:{oid}")
        seen.add(oid)
    return records, warnings


def _xml_tree(node: ET.Element) -> dict[str, Any]:
    """Lossless-for-OOXML subtree projection preserving order, attrs and text."""
    record: dict[str, Any] = {"tag": _local(node.tag), "attributes": dict(node.attrib)}
    if node.text and node.text.strip():
        record["text"] = node.text.strip()
    children = [_xml_tree(child) for child in list(node)]
    if children:
        record["children"] = children
    return record


def _transition_records(root: ET.Element) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for node in root.iter(_q(P, "transition")):
        parent_branch = "direct"
        for alternate in root.iter(_q(MC, "AlternateContent")):
            for branch in list(alternate):
                if any(candidate is node for candidate in branch.iter()):
                    parent_branch = "choice" if _local(branch.tag) == "Choice" else "fallback"
                    records.append({
                        "branch": parent_branch, "requires": branch.get("Requires"),
                        "transition": _xml_tree(node),
                    })
        if parent_branch == "direct":
            records.append({"branch": "direct", "requires": None, "transition": _xml_tree(node)})
    return records


def _timing_behaviors(timing: ET.Element, object_ids: set[str]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    tree = _xml_tree(timing)
    behaviors: list[dict[str, Any]] = []
    warnings: list[str] = []
    for node in timing.iter():
        tag = _local(node.tag)
        if tag not in _BEHAVIOR_TAGS:
            continue
        targets: list[dict[str, Any]] = []
        for target in node.iter():
            target_tag = _local(target.tag)
            if target_tag == "spTgt":
                shape_id = target.get("spid")
                targets.append({"kind": "shape", "id": shape_id})
                if shape_id is None:
                    warnings.append(f"animation_target_missing_spid:{tag}")
                elif shape_id not in object_ids:
                    warnings.append(f"unresolved_animation_target:{shape_id}")
            elif target_tag in {"sldTgt", "graphicEl", "oleChartEl", "chart", "tbl"}:
                targets.append({"kind": target_tag, "attributes": dict(target.attrib)})
        behavior = {
            "type": tag,
            "attributes": dict(node.attrib),
            "targets": targets,
            "xml": _xml_tree(node),
        }
        behaviors.append(behavior)
    return tree, behaviors, warnings


def _relationship_ids(element: ET.Element) -> set[str]:
    return {value for key, value in element.attrib.items() if key.startswith("{" + R + "}") and value}


def _package_context(archive: ZipFile, slide_part: str) -> dict[str, Any]:
    """Resolve slide→layout→master→theme relationship identities and dependencies."""
    chain: dict[str, Any] = {"slide": slide_part, "layout": None, "master": None, "theme": None}
    all_dependencies: list[dict[str, Any]] = []
    current_part = slide_part
    current_type = "slideLayout"
    for level in ("layout", "master", "theme"):
        rels = _read_rels(archive, current_part)
        for rel in rels.values():
            try:
                exists = rel["external"] or bool(rel["target"] and archive.getinfo(rel["target"]))
            except KeyError:
                exists = False
            all_dependencies.append({**rel, "part_exists": bool(exists)})
        candidates = [rel for rel in rels.values() if rel["type"] == current_type and not rel["external"]]
        if not candidates:
            break
        selected = candidates[0]
        if len(candidates) > 1:
            selected = {**selected, "resolution_warning": f"multiple_{current_type}_relationships"}
        target = selected["target"]
        chain[level] = {"relationship_id": selected["id"], "part": target,
                        "part_exists": bool(target and _has_part(archive, target)),
                        "resolution": "resolved" if target and _has_part(archive, target) else "unresolved"}
        if not target or not _has_part(archive, target):
            break
        current_part = target
        current_type = "slideMaster" if level == "layout" else "theme"
    chain["dependencies"] = all_dependencies
    theme_part = chain["theme"].get("part") if chain["theme"] else None
    chain["theme_fonts"] = _theme_fonts(archive, theme_part) if theme_part else {}
    if theme_part and not chain["theme_fonts"]:
        chain["theme_font_resolution"] = "unresolved_or_empty"
    else:
        chain["theme_font_resolution"] = "resolved" if theme_part else "no_theme_relationship"
    return chain


def _has_part(archive: ZipFile, part: str) -> bool:
    try:
        archive.getinfo(part)
        return True
    except KeyError:
        return False


def _theme_fonts(archive: ZipFile, theme_part: str) -> dict[str, Any]:
    try:
        with archive.open(theme_part) as stream:
            root = ET.parse(stream).getroot()
    except (KeyError, ET.ParseError):
        return {}
    scheme = root.find("a:themeElements/a:fontScheme", NS)
    if scheme is None:
        return {}
    result: dict[str, Any] = {}
    for style_name, tag in (("major", "majorFont"), ("minor", "minorFont")):
        style = scheme.find(f"a:{tag}", NS)
        if style is None:
            continue
        result[style_name] = {}
        for script in ("latin", "ea", "cs"):
            face = style.find(f"a:{script}", NS)
            result[style_name][script] = face.get("typeface") if face is not None else None
        result[style_name]["script_fonts"] = {
            face.get("script"): face.get("typeface")
            for face in style.findall("a:font", NS) if face.get("script")
        }
    return result


def _resolve_font_tokens(objects: list[dict[str, Any]], theme_fonts: dict[str, Any]) -> None:
    for obj in objects:
        for font in obj.get("font_references", []):
            token = font.get("typeface", "")
            match = re.fullmatch(r"\+(mj|mn)-(lt|ea|cs)", token)
            if not match:
                font["resolved_typeface"] = token if token else None
                font["resolution"] = "explicit" if token else "unresolved"
                continue
            style = "major" if match.group(1) == "mj" else "minor"
            script = {"lt": "latin", "ea": "ea", "cs": "cs"}[match.group(2)]
            font["resolved_typeface"] = theme_fonts.get(style, {}).get(script)
            font["resolution"] = "theme_resolved" if font["resolved_typeface"] else "theme_token_unresolved"


def _slide_record(archive: ZipFile, slide: dict[str, Any], order: int, visible_order: int | None,
                  inherited_cache: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    part = slide["part"]
    try:
        info = archive.getinfo(part)
        objects: list[dict[str, Any]] = []
        warnings: list[str] = []
        timing_tree = None
        behaviors: list[dict[str, Any]] = []
        transitions: list[dict[str, Any]] = []
        referenced_ids: set[str] = set()
        object_ids: set[str] = set()
        stack: list[ET.Element] = []
        # iterparse holds only the current slide subtrees needed for records;
        # each completed spTree/timing/transition is projected and released.
        with archive.open(info) as stream:
            for event, node in ET.iterparse(stream, events=("start", "end")):
                if event == "start":
                    stack.append(node)
                    continue
                tag = _local(node.tag)
                if tag == "spTree" and node.tag == _q(P, "spTree"):
                    found, object_warnings = _object_records(node)
                    objects.extend(found)
                    warnings.extend(object_warnings)
                    object_ids.update(record["id"] for record in found if record["id"] is not None)
                    for descendant in node.iter():
                        referenced_ids.update(_relationship_ids(descendant))
                    node.clear()
                elif tag == "timing" and node.tag == _q(P, "timing"):
                    timing_tree, behaviors, timing_warnings = _timing_behaviors(node, object_ids)
                    warnings.extend(timing_warnings)
                    for descendant in node.iter():
                        referenced_ids.update(_relationship_ids(descendant))
                    node.clear()
                elif tag == "transition" and node.tag == _q(P, "transition"):
                    branch = "direct"
                    requirements = None
                    for ancestor in reversed(stack[:-1]):
                        if ancestor.tag == _q(MC, "Choice"):
                            branch, requirements = "choice", ancestor.get("Requires")
                            break
                        if ancestor.tag == _q(MC, "Fallback"):
                            branch = "fallback"
                            break
                    transitions.append({"branch": branch, "requires": requirements, "transition": _xml_tree(node)})
                    referenced_ids.update(_relationship_ids(node))
                    node.clear()
                elif not any(ancestor.tag in _CAPTURE_TAGS for ancestor in stack[:-1]):
                    node.clear()
                # Release completed non-captured elements. A captured ancestor
                # has already consumed its descendants before being cleared.
                if stack:
                    stack.pop()
    except (KeyError, ET.ParseError, OSError) as error:
        return {"presentation_order": order, "visible_order": visible_order, "part": part,
                "hidden": slide["hidden"], "parse_error": str(error), "objects": [],
                "timing_tree": None, "behaviors": [], "transitions": [], "media_relationships": []}
    if not objects:
        warnings.append("missing_or_empty_spTree")
    context = _package_context(archive, part)
    # Layout and master XML are shared by many pages. Parse each part once per
    # deck and retain the projected record for every slide that inherits it.
    # The JSON shape remains explicit by source level so consumers can explain
    # where an effect came from instead of flattening it into slide timing.
    inherited_motion = _inherited_motion(archive, context, inherited_cache)
    for level in ("layout", "master"):
        layer = inherited_motion.get(level)
        if layer:
            warnings.extend(f"inherited_{level}:{warning}" for warning in layer["warnings"])
    _resolve_font_tokens(objects, context["theme_fonts"])
    for dependency in context["dependencies"]:
        if not dependency["external"] and not dependency["part_exists"]:
            warnings.append(f"unresolved_package_relationship:{dependency['id']}:{dependency['target_raw']}")
    rels = _read_rels(archive, part)
    notes_part = next((rel.get("target") for rel in rels.values()
                       if rel.get("type") == "notesSlide" and not rel.get("external")), None)
    notes_text = ""
    notes_status = "absent"
    if notes_part:
        try:
            with archive.open(notes_part) as stream:
                notes_root = ET.parse(stream).getroot()
            notes_text = "\n".join((node.text or "") for node in notes_root.iter(_q(A, "t")) if (node.text or "").strip())
            notes_status = "parsed"
        except (KeyError, ET.ParseError, OSError):
            notes_status = "unreadable"
            warnings.append("notes_part_unreadable")
    for obj in objects:
        obj["embedded_media_targets"] = [
            rels[rid]["target"] for rid in obj.get("image_relationship_ids", [])
            if rid in rels and not rels[rid]["external"] and rels[rid]["target"]
        ]
    media_relationships = []
    for rid in sorted(referenced_ids):
        rel = rels.get(rid)
        if rel is None:
            warnings.append(f"unresolved_relationship:{rid}")
            continue
        if rel["type"] in _MEDIA_REL_TYPES or (rel["target"] or "").startswith("ppt/media/"):
            try:
                archive.getinfo(rel["target"] or "")
                part_exists = bool(rel["target"])
            except KeyError:
                part_exists = False
            media_relationships.append({**rel, "referenced_by_shape": any(rid in obj.get("image_relationship_ids", []) for obj in objects),
                                       "part_exists": part_exists})
    return {
        "presentation_order": order,
        "visible_order": visible_order,
        "slide_id": slide["slide_id"],
        "part": part,
        "hidden": slide["hidden"],
        "objects": objects,
        "font_references": [
            {"object_id": obj["id"], **font}
            for obj in objects for font in obj.get("font_references", [])
        ],
        "font_coverage": "explicit run/paragraph/end-paragraph typeface declarations; inherited placeholder/layout/master defaults are not merged",
        "package_context": context,
        "inherited_motion": inherited_motion,
        "timing_tree": timing_tree,
        "behaviors": behaviors,
        "transitions": transitions,
        "media_relationships": media_relationships,
        "notes_part": notes_part,
        "notes_text": notes_text,
        "notes_status": notes_status,
        "timing_interpretation": "recognized behaviors are extracted; all original OOXML timing elements remain represented in timing_tree; playback semantics not simulated",
        "warnings": sorted(set(warnings)),
    }


def _part_motion_record(archive: ZipFile, part: str, source_level: str,
                        applies_to_slide: bool) -> dict[str, Any]:
    """Project one layout/master part into reusable structural motion evidence."""
    warnings: list[str] = []
    objects: list[dict[str, Any]] = []
    timing_tree = None
    behaviors: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    try:
        with archive.open(part) as stream:
            root = ET.parse(stream).getroot()
    except KeyError:
        warnings.append("inherited_part_missing")
        root = None
    except ET.ParseError:
        warnings.append("inherited_part_invalid_xml")
        root = None
    if root is not None:
        sp_tree = root.find(".//p:spTree", NS)
        if sp_tree is not None:
            objects, object_warnings = _object_records(sp_tree)
            warnings.extend(object_warnings)
        object_ids = {str(obj["id"]) for obj in objects if obj.get("id") is not None}
        timing = root.find("p:timing", NS)
        if timing is not None:
            timing_tree, behaviors, timing_warnings = _timing_behaviors(timing, object_ids)
            warnings.extend(timing_warnings)
        transitions = _transition_records(root)
    by_id = {str(obj["id"]): obj for obj in objects if obj.get("id") is not None}
    target_map = []
    for behavior in behaviors:
        for target in behavior["targets"]:
            if target.get("kind") != "shape":
                target_map.append({"behavior_type": behavior["type"], "target": target,
                                   "object": None, "resolution": "non_shape_target"})
                continue
            obj = by_id.get(str(target.get("id")))
            target_map.append({"behavior_type": behavior["type"], "target": target,
                               "object": ({"id": obj["id"], "name": obj["name"], "type": obj["type"]}
                                          if obj else None),
                               "resolution": "resolved" if obj else "unresolved"})
    return {"source_level": source_level, "part": part,
            "applies_to_slide": applies_to_slide, "objects": objects,
            "object_target_map": target_map, "timing_tree": timing_tree,
            "behaviors": behaviors, "transitions": transitions,
            "warnings": sorted(set(warnings))}


def _inherited_motion(archive: ZipFile, context: dict[str, Any],
                      cache: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Read motion-bearing layout/master parts, with per-deck part caching."""
    cache = cache if cache is not None else {}
    result: dict[str, Any] = {}
    layout = context.get("layout")
    master = context.get("master")
    for level, dependency in (("layout", layout), ("master", master)):
        if not dependency or not dependency.get("part"):
            continue
        part = dependency["part"]
        if part not in cache:
            cache[part] = _part_motion_record(archive, part, level, True)
        result[level] = cache[part]
    return result


def _transition_has_morph(slide: dict[str, Any]) -> bool:
    for candidate in slide.get("transitions", []):
        transition = candidate.get("transition", {})
        if any(child.get("tag") == "morph" for child in transition.get("children", [])):
            return True
    return False


def _morph_candidates(previous: dict[str, Any], current: dict[str, Any], slide_size: dict[str, Any]) -> list[dict[str, Any]]:
    """Create object-pair hypotheses only; this is never Morph playback evidence."""
    prev_objects, curr_objects = previous["objects"], current["objects"]
    width, height = slide_size.get("width_emu"), slide_size.get("height_emu")
    pairs: dict[tuple[str | None, str | None], set[str]] = {}

    def offer(a: dict[str, Any], b: dict[str, Any], basis: str) -> None:
        pairs.setdefault((a.get("id"), b.get("id")), set()).add(basis)

    def indexed(objects: list[dict[str, Any]], field: str):
        result: dict[str, list[dict[str, Any]]] = {}
        for obj in objects:
            value = obj.get(field)
            if value:
                result.setdefault(str(value), []).append(obj)
        return result

    for field, basis in (("id", "same_shape_id"), ("name", "same_name")):
        left, right = indexed(prev_objects, field), indexed(curr_objects, field)
        for value, a_items in left.items():
            for a in a_items:
                for b in right.get(value, []):
                    offer(a, b, "forced_name" if field == "name" and value.startswith("!!") else basis)
    for a in prev_objects:
        media_a = set(a.get("embedded_media_targets", []))
        text_a = re.sub(r"\s+", " ", a.get("text", "")).strip().casefold()
        ga = a.get("geometry") or {}
        for b in curr_objects:
            media_b = set(b.get("embedded_media_targets", []))
            if media_a & media_b:
                offer(a, b, "same_embedded_media_part")
            text_b = re.sub(r"\s+", " ", b.get("text", "")).strip().casefold()
            if text_a and text_a == text_b:
                offer(a, b, "same_text")
            gb = b.get("geometry") or {}
            if width and height and ga.get("x_emu") is not None and gb.get("x_emu") is not None:
                delta = max(
                    abs(ga["x_emu"] - gb["x_emu"]) / width,
                    abs(ga.get("y_emu", 0) - gb.get("y_emu", 0)) / height,
                    abs(ga.get("width_emu", 0) - gb.get("width_emu", 0)) / width,
                    abs(ga.get("height_emu", 0) - gb.get("height_emu", 0)) / height,
                )
                if delta <= 0.025:
                    offer(a, b, "geometry_close_2_5pct")
    return [
        {"from_object_id": pair[0], "to_object_id": pair[1], "match_basis": sorted(bases),
         "status": "heuristic_candidate_only"}
        for pair, bases in pairs.items()
    ]


def inspect_motion_library(path: str | Path) -> dict[str, Any]:
    """Return read-only OOXML motion evidence for a single PPTX.

    Slides follow ``presentation.xml`` relationship order. Hidden pages remain
    in ``slides`` with ``visible_order=None`` and ``hidden=True``. Source bytes
    are hashed incrementally; media bytes are not loaded or interpreted.
    """
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise MotionLibraryError("source_must_be_an_existing_file")
    digest = _source_sha(source)
    try:
        with ZipFile(source) as archive:
            slide_order, slide_size = _presentation_order(archive)
            slides: list[dict[str, Any]] = []
            inherited_cache: dict[str, dict[str, Any]] = {}
            visible_order = 0
            for index, slide in enumerate(slide_order, 1):
                visible = None
                if not slide["hidden"]:
                    visible_order += 1
                    visible = visible_order
                slides.append(_slide_record(archive, slide, index, visible, inherited_cache))
            visible_slides = [item for item in slides if item["visible_order"] is not None]
            for index, item in enumerate(visible_slides):
                if not _transition_has_morph(item):
                    continue
                previous = visible_slides[index - 1] if index else None
                item["morph_relationship"] = {
                    "direction": "previous_visible_slide_to_this_slide" if previous else "no_previous_visible_slide",
                    "previous_slide_part": previous["part"] if previous else None,
                    "matching_basis": ["!!forced_name", "same_name", "same_shape_id", "same_embedded_media_part", "same_text", "geometry_close_2_5pct"],
                    "candidates": _morph_candidates(previous, item, slide_size) if previous else [],
                    "verification": "OOXML matching heuristic only; does not prove PowerPoint Morph interpolation or playback",
                }
    except (BadZipFile, OSError) as error:
        raise MotionLibraryError("source_is_not_a_readable_pptx_zip") from error
    return {
        "schema": SCHEMA,
        "source": str(source),
        "source_sha256": digest,
        "slide_size": slide_size,
        "slides": slides,
        "verification": {
            "basis": "OOXML structural inspection",
            "playback_status": "not_verified",
            "visual_status": "not_reviewed",
        },
    }


__all__ = ["MotionLibraryError", "inspect_motion_library"]
