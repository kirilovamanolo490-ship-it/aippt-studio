"""Hash-bound content replacement for editable template-derived PPTX files.

This module changes only selected slide XML/relationships and the required
embedded media/content-type parts. It does not rebuild shapes, layouts,
masters, theme, crop, or picture geometry.
"""
from __future__ import annotations

import hashlib
import os
import posixpath
from pathlib import Path
import tempfile
from zipfile import BadZipFile, ZipFile

from lxml import etree as ET

from .motion_clone import (
    MotionCloneError, _image_format, _read_xml, _safe_entries,
    _related_part, _slide_order, _slide_objects, text_sha256,
)

R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"


class TemplateContentReplaceError(ValueError):
    """A source binding or replacement cannot be applied safely."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _shape_text(shape) -> str:
    return "".join(node.text or "" for node in shape.xpath(".//a:t", namespaces={
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}))


def _rels_part(part: str) -> str:
    parent, name = part.rsplit("/", 1)
    return f"{parent}/_rels/{name}.rels"


def _resolve(base_part: str, target: str) -> str:
    return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))


def _object_metadata(shape, shape_id: str) -> dict:
    names = shape.xpath("./p:nvSpPr/p:cNvPr/@name | ./p:nvPicPr/p:cNvPr/@name | ./p:nvGraphicFramePr/p:cNvPr/@name | ./p:nvGrpSpPr/p:cNvPr/@name | ./p:nvCxnSpPr/p:cNvPr/@name",
                        namespaces={"p": P})
    xfrm = shape.find(f".//{{{A}}}xfrm")
    geometry = None
    if xfrm is not None:
        off = xfrm.find(f"{{{A}}}off")
        ext = xfrm.find(f"{{{A}}}ext")
        if off is not None and ext is not None:
            try:
                geometry = {"x": int(off.get("x")), "y": int(off.get("y")),
                            "cx": int(ext.get("cx")), "cy": int(ext.get("cy")), "unit": "EMU"}
            except (TypeError, ValueError):
                geometry = None
    local = ET.QName(shape).localname
    visual_type = "picture" if local == "pic" else "shape_image_fill" if local == "sp" else f"{local}_image"
    return {"shape_id": shape_id, "shape_name": names[0] if names else None,
            "visual_object_type": visual_type, "geometry_emu": geometry}


def _object_blips(shape):
    object_tags = {f"{{{P}}}{name}" for name in ("sp", "pic", "graphicFrame", "grpSp", "cxnSp")}
    found = []

    def visit(node):
        for child in node:
            if child.tag == f"{{{A}}}blip":
                found.append(child)
            elif child.tag in object_tags:
                continue
            else:
                visit(child)

    visit(shape)
    return found


def _image_refs(archive: ZipFile, entries: dict, part: str, root, slide_number: int | None):
    """Enumerate each embedded picture's shape ID and resolved media part."""
    rel_name = _rels_part(part)
    rels = {}
    if rel_name in entries:
        rel_root = _read_xml(archive, entries, rel_name)
        for rel in rel_root:
            rel_id = rel.get("Id")
            if not rel_id or rel_id in rels:
                raise TemplateContentReplaceError(f"missing or duplicate relationship ID in {rel_name}")
            if rel.get("TargetMode") == "External":
                rels[rel_id] = None
            else:
                resolved = _resolve(part, rel.get("Target", ""))
                if resolved.startswith("../") or resolved not in entries:
                    raise TemplateContentReplaceError(f"invalid relationship target in {rel_name}")
                rels[rel_id] = resolved
    out = []
    for shape_id, shape in _slide_objects(root).items():
        blips = _object_blips(shape)
        for blip_index, blip in enumerate(blips, 1):
            rid = blip.get(f"{{{R}}}embed")
            media = rels.get(rid) if rid else None
            if media and media.startswith("ppt/media/"):
                out.append({"slide": slide_number, "part": part, **_object_metadata(shape, shape_id),
                            "relationship_id": rid, "media_part": media,
                            "blip_index": blip_index, "blip_count": len(blips)})
    return out


def _all_image_refs(archive: ZipFile, entries: dict, slide_parts: list[str]):
    refs = []
    for slide, part in enumerate(slide_parts, 1):
        for row in _image_refs(archive, entries, part, _read_xml(archive, entries, part), slide):
            row["layer"] = "slide"
            refs.append(row)
    for part in entries:
        if part.startswith(("ppt/slideLayouts/", "ppt/slideMasters/")) and part.endswith(".xml"):
            layer = "layout" if part.startswith("ppt/slideLayouts/") else "master"
            for row in _image_refs(archive, entries, part, _read_xml(archive, entries, part), None):
                row["layer"] = layer
                refs.append(row)
    return refs


def inspect_page_images(source_pptx: str | Path, slide: int) -> dict:
    """List image-bearing slide, layout, and master objects for one page.

    Geometry is reported in each object's own EMU coordinates. Grouped-shape
    transforms are not resolved. Replacement safety requires exactly one
    supported internal image blip on the object.
    """
    source = Path(source_pptx).expanduser().resolve(strict=True)
    source_digest = _sha(source.read_bytes())
    with ZipFile(source) as archive:
        entries = _safe_entries(archive)
        _, slide_parts = _slide_order(archive, entries)
        if type(slide) is not int or not 1 <= slide <= len(slide_parts):
            raise TemplateContentReplaceError("slide must be an existing 1-based page number")
        slide_part = slide_parts[slide - 1]
        layout_part = _related_part(archive, entries, slide_part, "slideLayout")
        master_part = _related_part(archive, entries, layout_part, "slideMaster") if layout_part else None
        layers = [("slide", slide_part)]
        if layout_part:
            layers.append(("layout", layout_part))
        if master_part:
            layers.append(("master", master_part))
        found = []
        for layer, part in layers:
            root = _read_xml(archive, entries, part)
            refs = _image_refs(archive, entries, part, root, slide if layer == "slide" else None)
            by_shape = {}
            for row in refs:
                by_shape.setdefault(row["shape_id"], []).append(row)
            for shape_id, shape in _slide_objects(root).items():
                blips = _object_blips(shape)
                if not blips:
                    continue
                image_records = []
                for row in by_shape.get(shape_id, []):
                    image_records.append({"relationship_id": row["relationship_id"],
                                          "media_part": row["media_part"],
                                          "media_sha256": _sha(archive.read(row["media_part"])),
                                          "blip_index": row["blip_index"]})
                safe = len(blips) == 1 and len(image_records) == 1
                found.append({"layer": layer, "part": part, **_object_metadata(shape, shape_id),
                              "media": image_records, "safe_for_single_image_replacement": safe,
                              "replacement_block_reason": None if safe else "object must have exactly one supported embedded image relationship"})
        return {"source_sha256": source_digest, "slide": slide, "objects": found,
                "coverage": {"layers": [layer for layer, _ in layers], "grouped_shape_geometry_resolved": False},
                "verification": "OOXML relationship and media inspection; visual rendering not performed"}


def _new_media_part(entries: dict, source_part: str) -> str:
    ext = posixpath.splitext(source_part)[1]
    used = set(entries)
    index = 1
    while f"ppt/media/image{index}{ext}" in used:
        index += 1
    return f"ppt/media/image{index}{ext}"


def _new_rid(rel_root) -> str:
    used = {node.get("Id") for node in rel_root}
    index = 1
    while f"rId{index}" in used:
        index += 1
    return f"rId{index}"


def _content_type_for_part(content_root, part: str) -> str | None:
    name = "/" + part
    override = content_root.find(f"{{{CT}}}Override[@PartName='{name}']")
    if override is not None:
        return override.get("ContentType")
    ext = posixpath.splitext(part)[1].lstrip(".").lower()
    default = content_root.find(f"{{{CT}}}Default[@Extension='{ext}']")
    return default.get("ContentType") if default is not None else None


def _relationship_embed_uses(root, rid: str) -> int:
    return len(root.xpath(".//*[@r:embed=$rid]", namespaces={"r": R}, rid=rid))


def replace_template_content(source: str | Path, output: str | Path, *, source_sha256: str,
                             text_replacements: list[dict] | None = None,
                             image_replacements: list[dict] | None = None) -> dict:
    """Write a new PPTX with exact hash-bound text and image replacements.

    Text item: ``{slide, shape_id, original_text_sha256, new_text}`` (``text``
    is accepted as a compatibility alias). The text hash
    covers the exact concatenation of all ``a:t`` nodes in document order.
    Image item: ``{slide, shape_id, original_media_sha256, path}``. Targets may
    be p:pic objects or p:sp shapes with exactly one embedded a:blip fill. The media
    hash binds the existing bytes; replacement extension and byte signature
    must match. Slide numbers are 1-based source presentation order.

    Multi-run text keeps the first run (and its style), clears the remaining
    run text, and removes later paragraphs to prevent stale copy from surviving.
    Pictures keep their shape XML (crop, transform and frame). Shared media uses
    copy-on-write: the selected picture gets a private media part and rel target,
    while every other slide/layout/master consumer remains bound to source bytes.
    """
    src = Path(source).expanduser().resolve(strict=True)
    dst = Path(output).expanduser().absolute()
    if dst.exists() or dst.is_symlink() or dst == src:
        raise TemplateContentReplaceError("output must be a new path distinct from source")
    source_bytes = src.read_bytes()
    actual_source_sha = _sha(source_bytes)
    if not isinstance(source_sha256, str) or source_sha256.lower() != actual_source_sha:
        raise TemplateContentReplaceError("source_sha256 is missing or does not match the current PPTX")
    texts = list(text_replacements or [])
    images = list(image_replacements or [])
    if not texts and not images:
        raise TemplateContentReplaceError("at least one text or image replacement is required")
    try:
        with ZipFile(src) as archive:
            entries = _safe_entries(archive)
            _, slide_parts = _slide_order(archive, entries)
            changed: dict[str, bytes] = {}
            slide_roots = {}
            for item in texts:
                slide, shape_id = item.get("slide"), str(item.get("shape_id", ""))
                if type(slide) is not int or not 1 <= slide <= len(slide_parts):
                    raise TemplateContentReplaceError("text slide must be an existing 1-based source slide number")
                if slide in slide_roots:
                    root, objects = slide_roots[slide]
                else:
                    part = slide_parts[slide - 1]
                    root = _read_xml(archive, entries, part)
                    objects = _slide_objects(root)
                    slide_roots[slide] = (root, objects)
                shape = objects.get(shape_id)
                if shape is None or ET.QName(shape).localname != "sp":
                    raise TemplateContentReplaceError(f"text target {shape_id!r} is not a text shape on slide {slide}")
                if shape.xpath(".//a:fld", namespaces={"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}):
                    raise TemplateContentReplaceError("field-based text is unsupported; it may be regenerated by PowerPoint")
                nodes = shape.xpath(".//a:t", namespaces={"a": "http://schemas.openxmlformats.org/drawingml/2006/main"})
                original = "".join(node.text or "" for node in nodes)
                expected = item.get("original_text_sha256")
                if not nodes or not isinstance(expected, str) or text_sha256(original) != expected:
                    raise TemplateContentReplaceError(f"original text SHA mismatch for slide {slide}, shape {shape_id}")
                if "new_text" in item and "text" in item:
                    raise TemplateContentReplaceError("specify only one of new_text or text")
                new_text = item.get("new_text", item.get("text"))
                if not isinstance(new_text, str) or not new_text.strip() or "\x00" in new_text:
                    raise TemplateContentReplaceError("replacement text must be nonempty XML-safe text")
                # Preserve the first paragraph and first run properties. Removing
                # subsequent paragraphs avoids blank bullets and hidden stale facts.
                body = shape.find("{http://schemas.openxmlformats.org/presentationml/2006/main}txBody")
                paragraphs = body.findall("{http://schemas.openxmlformats.org/drawingml/2006/main}p") if body is not None else []
                if not paragraphs:
                    raise TemplateContentReplaceError("text shape has no editable text paragraph")
                first_para = paragraphs[0]
                first_run = first_para.find("{http://schemas.openxmlformats.org/drawingml/2006/main}r")
                first_text = first_run.find("{http://schemas.openxmlformats.org/drawingml/2006/main}t") if first_run is not None else None
                if first_text is None:
                    # Use existing first text node; do not synthesize unstyled XML.
                    first_text = nodes[0]
                for node in nodes:
                    node.text = ""
                first_text.text = new_text
                for paragraph in paragraphs[1:]:
                    body.remove(paragraph)
                if _shape_text(shape) != new_text:
                    raise TemplateContentReplaceError("text replacement readback retained unexpected content")
            for slide, (root, _) in slide_roots.items():
                part = slide_parts[slide - 1]
                changed[part] = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

            all_refs = _all_image_refs(archive, entries, slide_parts)
            media_consumers = {}
            for row in all_refs:
                media_consumers.setdefault(row["media_part"], []).append(row)
            image_slide_roots = {}
            image_rels_roots = {}
            content_root = _read_xml(archive, entries, "[Content_Types].xml")
            image_receipts = []
            seen_image_targets = set()
            for item in images:
                slide, shape_id = item.get("slide"), str(item.get("shape_id", ""))
                if type(slide) is not int or not 1 <= slide <= len(slide_parts):
                    raise TemplateContentReplaceError("image slide must be an existing 1-based source slide number")
                target_key = (slide, shape_id)
                if target_key in seen_image_targets:
                    raise TemplateContentReplaceError("each picture shape may be replaced only once per request")
                seen_image_targets.add(target_key)
                part = slide_parts[slide - 1]
                original_root = _read_xml(archive, entries, part)
                original_objects = _slide_objects(original_root)
                shape = original_objects.get(shape_id)
                if shape is None or ET.QName(shape).localname not in {"pic", "sp"}:
                    raise TemplateContentReplaceError(f"image target {shape_id!r} is not a supported picture or shape fill on slide {slide}")
                refs = [r for r in _image_refs(archive, entries, part, original_root, slide) if r["shape_id"] == shape_id]
                shape_blips = _object_blips(shape)
                if len(shape_blips) != 1 or len(refs) != 1 or refs[0].get("blip_count") != 1:
                    raise TemplateContentReplaceError("target must have exactly one supported embedded image relationship")
                binding = refs[0]
                media = binding["media_part"]
                info = entries[media]
                if info.file_size > 64 * 1024 * 1024:
                    raise TemplateContentReplaceError("embedded media exceeds 64 MiB")
                original = archive.read(info)
                if not isinstance(item.get("original_media_sha256"), str) or _sha(original) != item["original_media_sha256"]:
                    raise TemplateContentReplaceError(f"original media SHA mismatch for slide {slide}, shape {shape_id}")
                if "image_path" in item and "path" in item:
                    raise TemplateContentReplaceError("specify only one of image_path or path")
                replacement_path = Path(item.get("image_path", item.get("path", ""))).expanduser().resolve(strict=True)
                if not replacement_path.is_file() or replacement_path.stat().st_size == 0 or replacement_path.stat().st_size > 64 * 1024 * 1024:
                    raise TemplateContentReplaceError("replacement image must be a nonempty regular file no larger than 64 MiB")
                replacement = replacement_path.read_bytes()
                fmt = _image_format(media, original)
                if _image_format(media, replacement) != fmt or _image_format(replacement_path.name, replacement) != fmt:
                    raise TemplateContentReplaceError("replacement image must use the same supported format and extension")
                consumers = media_consumers.get(media, [])
                is_shared = len(consumers) > 1
                media_type = _content_type_for_part(content_root, media)
                if not media_type:
                    raise TemplateContentReplaceError(f"no content type is declared for source media {media}")
                if is_shared:
                    new_media = _new_media_part(entries | changed, media)
                    rid = binding["relationship_id"]
                    rel_name = _rels_part(part)
                    rel_root = image_rels_roots.get(part)
                    if rel_root is None:
                        rel_root = _read_xml(archive, entries, rel_name)
                        image_rels_roots[part] = rel_root
                    rel = next((r for r in rel_root if r.get("Id") == rid), None)
                    if rel is None or rel.get("TargetMode") == "External":
                        raise TemplateContentReplaceError("target picture relationship is missing or external")
                    target_blips = shape.xpath(".//a:blip[@r:embed=$rid]", namespaces={"a": A, "r": R}, rid=rid)
                    if not target_blips:
                        raise TemplateContentReplaceError("target picture no longer references its bound image relationship")
                    # Account for DrawingML picture fills as well as p:pic
                    # objects that may also reference this relationship.
                    same_slide_root = _read_xml(archive, entries, part)
                    slide_embed_uses = _relationship_embed_uses(same_slide_root, rid)
                    if slide_embed_uses == len(target_blips):
                        rel.set("Target", posixpath.relpath(new_media, posixpath.dirname(part)))
                    else:
                        rid = _new_rid(rel_root)
                        ET.SubElement(rel_root, f"{{{REL}}}Relationship", Id=rid,
                                      Type=rel.get("Type"), Target=posixpath.relpath(new_media, posixpath.dirname(part)))
                        if part not in image_slide_roots:
                            existing_slide_root = slide_roots.get(slide, (None, None))[0]
                            image_slide_roots[part] = existing_slide_root if existing_slide_root is not None else _read_xml(archive, entries, part)
                        target_pic = _slide_objects(image_slide_roots[part]).get(shape_id)
                        for target_blip in _object_blips(target_pic):
                            if target_blip.get(f"{{{R}}}embed") == binding["relationship_id"]:
                                target_blip.set(f"{{{R}}}embed", rid)
                    changed[new_media] = replacement
                    ct = _content_type_for_part(content_root, new_media)
                    if ct != media_type:
                        if ct is not None:
                            raise TemplateContentReplaceError("copy-on-write media resolves to a conflicting content type")
                        ET.SubElement(content_root, f"{{{CT}}}Override", PartName="/" + new_media, ContentType=media_type)
                        changed["[Content_Types].xml"] = ET.tostring(content_root, xml_declaration=True, encoding="UTF-8", standalone=True)
                    other_consumers = [row for row in consumers if not (row.get("layer") == "slide" and row.get("slide") == slide and row.get("shape_id") == shape_id)]
                    image_receipts.append({"slide": slide, "shape_id": shape_id,
                                           "visual_object_type": binding["visual_object_type"],
                                           "geometry_emu": binding["geometry_emu"],
                                           "source_relationship_id": binding["relationship_id"],
                                           "output_relationship_id": rid,
                                           "source_media_part": media, "source_media_sha256": _sha(original),
                                           "source_content_type": media_type,
                                           "output_media_part": new_media, "copy_on_write": True,
                                           "replacement_media_sha256": _sha(replacement),
                                           "original_media_retained": True,
                                           "remaining_original_consumers": len(other_consumers),
                                           "remaining_consumer_layers": sorted({row.get("layer", "slide") for row in other_consumers}),
                                           "privacy_cleanup_pending": True})
                else:
                    changed[media] = replacement
                    image_receipts.append({"slide": slide, "shape_id": shape_id,
                                           "visual_object_type": binding["visual_object_type"],
                                           "geometry_emu": binding["geometry_emu"],
                                           "source_relationship_id": binding["relationship_id"],
                                           "output_relationship_id": binding["relationship_id"],
                                           "source_media_part": media, "source_media_sha256": _sha(original),
                                           "source_content_type": media_type,
                                           "output_media_part": media, "copy_on_write": False,
                                           "replacement_media_sha256": _sha(replacement),
                                           "original_media_retained": False,
                                           "remaining_original_consumers": 0,
                                           "remaining_consumer_layers": [], "privacy_cleanup_pending": False})
            for part, root in image_slide_roots.items():
                changed[part] = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
            for part, root in image_rels_roots.items():
                changed[_rels_part(part)] = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
    except (BadZipFile, OSError, MotionCloneError) as error:
        if isinstance(error, TemplateContentReplaceError):
            raise
        raise TemplateContentReplaceError(str(error)) from error

    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=dst.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with ZipFile(src) as original, ZipFile(temp, "w") as candidate:
            candidate.comment = original.comment
            for info in original.infolist():
                if info.filename in changed:
                    candidate.writestr(info, changed[info.filename])
                else:
                    candidate.writestr(info, original.read(info))
            for name, data in changed.items():
                if name not in entries:
                    candidate.writestr(name, data)
        with ZipFile(temp) as check, ZipFile(src) as source_check:
            for part, expected in changed.items():
                if check.read(part) != expected:
                    raise TemplateContentReplaceError(f"candidate readback mismatch for {part}")
            for info in source_check.infolist():
                if info.filename not in changed and check.read(info.filename) != source_check.read(info.filename):
                    raise TemplateContentReplaceError(f"unrequested source package part changed: {info.filename}")
            checked_entries = _safe_entries(check)
            checked_content_types = _read_xml(check, checked_entries, "[Content_Types].xml")
            for receipt in image_receipts:
                target_part = slide_parts[receipt["slide"] - 1]
                root = _read_xml(check, checked_entries, target_part)
                row = next((r for r in _image_refs(check, checked_entries, target_part, root, receipt["slide"])
                            if r["shape_id"] == receipt["shape_id"]), None)
                if row is None or row["media_part"] != receipt["output_media_part"]:
                    raise TemplateContentReplaceError("candidate picture relationship failed readback")
                output_media = check.read(row["media_part"])
                if _sha(output_media) != receipt["replacement_media_sha256"]:
                    raise TemplateContentReplaceError("candidate image bytes failed readback")
                expected_type = _content_type_for_part(checked_content_types, row["media_part"])
                if expected_type != receipt["source_content_type"]:
                    raise TemplateContentReplaceError("candidate media content type did not preserve the source type")
        if _sha(src.read_bytes()) != actual_source_sha:
            raise TemplateContentReplaceError("source changed while replacement was prepared")
        try:
            os.link(temp, dst)
        except FileExistsError as error:
            raise TemplateContentReplaceError("output already exists; refusing to overwrite") from error
        temp.unlink()
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    return {"output": str(dst), "source_sha256": actual_source_sha,
            "output_sha256": _sha(dst.read_bytes()), "text_replacements": len(texts),
            "image_replacements": len(images), "changed_parts": sorted(changed),
            "image_bindings": image_receipts,
            "non_target_package_parts_preserved": True,
            "native_visual_review_required": True,
            "limitations": ["replacement preserves existing layout/shape geometry and first-run style; it does not fit text",
                            "text SHA covers concatenated a:t nodes without paragraph separators",
                            "only PNG, JPEG, GIF, BMP and TIFF media are supported",
                            "shared media uses copy-on-write; retained original media is reported for privacy review",
                            "PowerPoint rendering and semantic correctness are not verified"]}


def replace_content(source_pptx: Path, output_pptx: Path, *, source_sha256: str,
                    text_replacements: list[dict], image_replacements: list[dict]) -> dict:
    """Stable adapter-facing alias used after source pages are composed.

    Text records use ``new_text`` and image records use ``image_path``. Slide
    numbers bind to ``source_pptx`` presentation order. A hybrid builder
    should pass its synthesized output PPTX here and use that deck's output
    slide order and shape IDs in the replacement records.
    """
    return replace_template_content(
        source_pptx, output_pptx, source_sha256=source_sha256,
        text_replacements=text_replacements, image_replacements=image_replacements,
    )
