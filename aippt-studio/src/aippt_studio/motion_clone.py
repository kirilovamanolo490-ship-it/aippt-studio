"""Conservative OOXML cloning for decks that already contain native motion.

The operation copies every package member and changes only presentation slide
order (when a contiguous selection is requested) and explicitly hash-bound
single-run text targets. Timelines, transitions, relationships and media are
never reconstructed. This preserves package evidence; it does not prove that
PowerPoint will play every vendor-specific effect correctly.
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import shutil
import stat
import tempfile
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from lxml import etree as ET

from .animation_edit import _xml

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS = {"p": P, "a": A, "r": R}


class MotionCloneError(ValueError):
    """The requested clone cannot be proven safe for this source package."""


def text_sha256(text: str) -> str:
    """SHA-256 of the exact UTF-8 shape text used to bind a replacement."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slide_objects(root):
    tree = root.find("p:cSld/p:spTree", NS)
    if tree is None:
        raise MotionCloneError("slide has no shape tree")
    objects = {}
    for obj in tree.iter():
        if ET.QName(obj).namespace != P or ET.QName(obj).localname not in {
            "sp", "pic", "graphicFrame", "grpSp", "cxnSp"
        }:
            continue
        props = obj.find("p:nvSpPr/p:cNvPr", NS)
        if props is None:
            props = obj.find("p:nvPicPr/p:cNvPr", NS)
        if props is None:
            props = obj.find("p:nvGraphicFramePr/p:cNvPr", NS)
        if props is None:
            props = obj.find("p:nvGrpSpPr/p:cNvPr", NS)
        if props is None:
            props = obj.find("p:nvCxnSpPr/p:cNvPr", NS)
        if props is None:
            continue
        shape_id = props.get("id")
        if not shape_id or shape_id in objects:
            raise MotionCloneError("slide has missing or duplicate shape IDs")
        objects[shape_id] = obj
    return objects


def _motion_signature(root):
    """Canonical timing/transition XML for exact semantic readback."""
    values = []
    for node in root.iter():
        # PowerPoint stores Morph in an extension namespace on some decks,
        # rather than as a child of the standard p:transition element.
        if isinstance(node.tag, str) and ET.QName(node).localname in {"timing", "transition", "morph"}:
            values.append(ET.tostring(node, method="c14n", exclusive=False))
    return tuple(values)


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_entries(archive: ZipFile) -> dict[str, object]:
    entries = {}
    for info in archive.infolist():
        name = info.filename
        path_body = name[:-1] if name.endswith("/") else name
        parts = path_body.split("/")
        if (not name or "\x00" in name or "\\" in name or name.startswith("/")
                or any(not part or part in {".", ".."} for part in parts)
                or any(":" in part for part in parts)):
            raise MotionCloneError(f"unsafe ZIP member path: {name!r}")
        if name in entries:
            raise MotionCloneError(f"duplicate ZIP member: {name}")
        if info.flag_bits & 1:
            raise MotionCloneError("encrypted ZIP members are unsupported")
        # The clone streams members as package data; reject special filesystem
        # entries so an unusual archive cannot smuggle links or devices through.
        unix_mode = info.external_attr >> 16
        file_type = stat.S_IFMT(unix_mode)
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise MotionCloneError(f"unsupported special ZIP member: {name}")
        if name.endswith("/") and file_type not in {0, stat.S_IFDIR}:
            raise MotionCloneError(f"invalid ZIP directory entry: {name}")
        if not name.endswith("/") and file_type == stat.S_IFDIR:
            raise MotionCloneError(f"invalid ZIP file entry: {name}")
        entries[name] = info
    return entries


def _read_xml(archive: ZipFile, entries, name: str):
    info = entries.get(name)
    if info is None or info.file_size > 32 * 1024 * 1024:
        raise MotionCloneError(f"missing or oversized required XML part: {name}")
    try:
        with archive.open(info) as stream:
            data = stream.read(32 * 1024 * 1024 + 1)
    except (BadZipFile, OSError) as error:
        raise MotionCloneError(f"could not read package part: {name}") from error
    if len(data) > 32 * 1024 * 1024:
        raise MotionCloneError(f"oversized required XML part: {name}")
    return _xml(data)


def _slide_relationships(archive: ZipFile, entries, slide_part: str):
    rel_part = posixpath.join(posixpath.dirname(slide_part), "_rels", posixpath.basename(slide_part) + ".rels")
    if rel_part not in entries:
        return {}
    root = _read_xml(archive, entries, rel_part)
    result = {}
    for rel in root:
        rel_id = rel.get("Id")
        if not rel_id or rel_id in result:
            raise MotionCloneError(f"missing or duplicate relationship ID in {rel_part}")
        if rel.get("TargetMode") == "External":
            result[rel_id] = None
            continue
        target = rel.get("Target", "")
        if target.startswith("/"):
            part = target.lstrip("/")
        else:
            part = posixpath.normpath(posixpath.join(posixpath.dirname(slide_part), target))
        if part.startswith("../") or part not in entries:
            raise MotionCloneError(f"invalid or missing relationship target in {rel_part}")
        result[rel_id] = part
    return result


def _relationships_part(part: str) -> str:
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def _related_part(archive: ZipFile, entries, part: str, suffix: str) -> str | None:
    rel_part = _relationships_part(part)
    if rel_part not in entries:
        return None
    root = _read_xml(archive, entries, rel_part)
    for rel in root:
        if (rel.get("Type") or "").endswith("/" + suffix):
            if rel.get("TargetMode") == "External":
                raise MotionCloneError(f"external {suffix} relationship is unsupported in {rel_part}")
            target = rel.get("Target", "")
            resolved = (target.lstrip("/") if target.startswith("/") else
                        posixpath.normpath(posixpath.join(posixpath.dirname(part), target)))
            if resolved.startswith("../") or resolved not in entries:
                raise MotionCloneError(f"invalid or missing {suffix} relationship target in {rel_part}")
            return resolved
    return None


def _layer_motion(archive: ZipFile, entries, part: str, linked_parts: list[str]):
    """Validate animation IDs on a slide/layout/master and report cross-layer IDs."""
    root = _read_xml(archive, entries, part)
    own = _slide_objects(root)
    linked_ids = set()
    for linked in linked_parts:
        linked_ids.update(_slide_objects(_read_xml(archive, entries, linked)))
    cross_layer = []
    for target in root.xpath(".//p:timing//p:spTgt", namespaces=NS):
        spid = target.get("spid")
        if spid in own:
            continue
        if spid and spid in linked_ids:
            cross_layer.append(spid)
            continue
        raise MotionCloneError(f"unresolved animation shape target in {part}: {spid or '(missing)'}")
    return root, _motion_signature(root), sorted(set(cross_layer))


def _image_format(part_name: str, data: bytes) -> str:
    """Return a conservative image-format identity for media replacement."""
    ext = posixpath.splitext(part_name)[1].lower()
    signatures = {
        ".png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": data.startswith(b"\xff\xd8\xff"),
        ".jpeg": data.startswith(b"\xff\xd8\xff"),
        ".gif": data.startswith((b"GIF87a", b"GIF89a")),
        ".bmp": data.startswith(b"BM"),
        ".tif": data.startswith((b"II*\x00", b"MM\x00*")),
        ".tiff": data.startswith((b"II*\x00", b"MM\x00*")),
    }
    if ext in signatures:
        if not signatures[ext]:
            raise MotionCloneError(f"image bytes do not match media format {ext}")
        return "jpeg" if ext in {".jpg", ".jpeg"} else ext[1:]
    raise MotionCloneError(f"unsupported embedded image format: {ext or '(no extension)'}")


def _image_bindings(archive: ZipFile, entries, slide_part: str, slide_num: int, root, objects):
    rels = _slide_relationships(archive, entries, slide_part)
    bindings = []
    for shape_id, shape in objects.items():
        if ET.QName(shape).localname != "pic":
            continue
        blip = shape.find(".//a:blip", NS)
        if blip is None:
            continue
        rel_id = blip.get(f"{{{R}}}embed")
        if not rel_id or rel_id not in rels or rels[rel_id] is None:
            # Unrelated linked or unsupported pictures do not block a bounded
            # edit elsewhere; a requested target with no binding fails below.
            continue
        media_part = rels[rel_id]
        if not media_part.startswith("ppt/media/") or media_part not in entries:
            continue
        bindings.append({"slide": slide_num, "shape_id": shape_id, "media_part": media_part})
    return bindings


def _slide_order(archive: ZipFile, entries):
    pres = _read_xml(archive, entries, "ppt/presentation.xml")
    rel_root = _read_xml(archive, entries, "ppt/_rels/presentation.xml.rels")
    rels = {node.get("Id"): node for node in rel_root}
    slide_list = pres.find("p:sldIdLst", NS)
    if slide_list is None:
        raise MotionCloneError("presentation has no slide list")
    ordered = []
    for slide in slide_list:
        rel = rels.get(slide.get(f"{{{R}}}id"))
        if rel is None or rel.get("TargetMode") == "External":
            raise MotionCloneError("missing or external presentation slide relationship")
        target = rel.get("Target", "")
        if target.startswith("/"):
            part = target.lstrip("/")
        else:
            part = posixpath.normpath(posixpath.join("ppt", target))
        if not part.startswith("ppt/slides/") or part not in entries:
            raise MotionCloneError("invalid or missing slide relationship target")
        ordered.append(part)
    if not ordered or len(ordered) != len(set(ordered)):
        raise MotionCloneError("presentation has no slides or duplicate slide targets")
    return pres, ordered


def _validate_motion(root):
    objects = _slide_objects(root)
    ids = set(objects)
    for target in root.xpath(".//p:timing//p:spTgt", namespaces=NS):
        spid = target.get("spid")
        if not spid or spid not in ids:
            raise MotionCloneError(f"unresolved animation shape target: {spid or '(missing)'}")
    return objects


def _set_selected_slides(pres, selected_indices: list[int]) -> bytes:
    slide_list = pres.find("p:sldIdLst", NS)
    if slide_list is None:
        raise MotionCloneError("presentation has no slide list")
    entries = list(slide_list)
    if not selected_indices or selected_indices != list(
        range(selected_indices[0], selected_indices[-1] + 1)
    ):
        raise MotionCloneError("slide selection must be a nonempty contiguous source-order range")
    if selected_indices[0] < 1 or selected_indices[-1] > len(entries):
        raise MotionCloneError("selected slide index is outside the source deck")
    keep = set(selected_indices)
    for index, entry in enumerate(entries, start=1):
        if index not in keep:
            slide_list.remove(entry)
    return ET.tostring(pres, xml_declaration=True, encoding="UTF-8", standalone=True)


def clone_motion_deck(
    source: str | Path,
    output: str | Path,
    *,
    source_sha256: str,
    slides: list[int],
    replacements: list[dict[str, str | int]] | None = None,
    image_replacements: list[dict[str, str | int | Path]] | None = None,
) -> dict:
    """Create an animation-preserving PPTX clone.

    ``slides`` are 1-based indices in original presentation order and must form
    one contiguous sequence, preserving Morph-adjacent source slide pairs.
    Each text replacement is ``{slide, shape_id, original_text_sha256, text}``.
    Each image replacement is ``{slide, shape_id, original_media_sha256, path}``.
    Image bytes replace an existing embedded media part in-place, retaining its
    relationship and picture XML (geometry, crop, and mask). Shared media in
    selected slides requires every reference to be mapped to identical bytes.
    Only shapes containing exactly one ``a:t`` run are editable. The function
    never changes the source and publishes output atomically after validation.
    """
    source = Path(source).resolve()
    output = Path(output).resolve()
    if source == output:
        raise MotionCloneError("output must be a new path; source is immutable")
    if not source.is_file() or source.suffix.lower() != ".pptx":
        raise MotionCloneError("source must be an existing PPTX")
    actual_sha = _sha_file(source)
    if actual_sha != source_sha256:
        raise MotionCloneError("source SHA-256 does not match current source")
    try:
        source_archive = ZipFile(source, "r")
        source_entries = _safe_entries(source_archive)
        presentation_root, source_order = _slide_order(source_archive, source_entries)
    except Exception as error:
        if "source_archive" in locals():
            source_archive.close()
        if isinstance(error, MotionCloneError):
            raise
        raise MotionCloneError(f"unreadable source PPTX: {error}") from error
    if not slides or slides != list(range(slides[0], slides[-1] + 1)):
        source_archive.close()
        raise MotionCloneError("slide selection must be a nonempty contiguous source-order range")
    if slides[0] < 1 or slides[-1] > len(source_order):
        source_archive.close()
        raise MotionCloneError("selected slide index is outside the source deck")

    if output.exists():
        source_archive.close()
        raise MotionCloneError("output already exists; refusing to overwrite")

    replacement_items = replacements or []
    if any(not isinstance(item, dict) for item in replacement_items):
        source_archive.close()
        raise MotionCloneError("replacements must be a list of replacement records")
    keys = [(item.get("slide"), str(item.get("shape_id"))) for item in replacement_items]
    if len(set(keys)) != len(keys):
        source_archive.close()
        raise MotionCloneError("duplicate text replacement target")
    if any(item.get("slide") not in slides for item in replacement_items):
        source_archive.close()
        raise MotionCloneError("replacement targets a slide outside the selected range")
    snapshots = {}
    layer_snapshots = {}
    cross_layer_targets = []
    changed_parts = {}
    changed_slides = set()
    mapped_image_targets = set()
    image_items = image_replacements or []
    if any(not isinstance(item, dict) for item in image_items):
        source_archive.close()
        raise MotionCloneError("image_replacements must be a list of replacement records")
    image_keys = [(item.get("layer", "slide"), item.get("slide"), item.get("layout_part"), str(item.get("shape_id"))) for item in image_items]
    if len(set(image_keys)) != len(image_keys):
        source_archive.close()
        raise MotionCloneError("duplicate image replacement target")
    slide_image_items = [item for item in image_items if item.get("layer", "slide") == "slide"]
    layout_image_items = [item for item in image_items if item.get("layer") == "layout"]
    if len(slide_image_items) + len(layout_image_items) != len(image_items):
        source_archive.close()
        raise MotionCloneError("image replacement layer must be slide or layout")
    if any(item.get("slide") not in slides for item in slide_image_items):
        source_archive.close()
        raise MotionCloneError("image replacement targets a slide outside the selected range")
    try:
        media_references = []
        image_targets = {}
        selected_layouts = {}
        selected_masters = {}
        for slide_num in slides:
            slide_part = source_order[slide_num - 1]
            layout_part = _related_part(source_archive, source_entries, slide_part, "slideLayout")
            if layout_part:
                selected_layouts[slide_num] = layout_part
                master_part = _related_part(source_archive, source_entries, layout_part, "slideMaster")
                if master_part:
                    selected_masters[slide_num] = master_part
        for slide_num in slides:
            name = source_order[slide_num - 1]
            root = _read_xml(source_archive, source_entries, name)
            linked = [p for p in (selected_layouts.get(slide_num), selected_masters.get(slide_num)) if p]
            root, slide_signature, cross_ids = _layer_motion(source_archive, source_entries, name, linked)
            cross_layer_targets.extend({"part": name, "shape_id": value} for value in cross_ids)
            snapshots[slide_num] = slide_signature
            objects = _slide_objects(root)
            if image_items:
                media_references.extend(_image_bindings(source_archive, source_entries, name, slide_num, root, objects))
            for layer_part in (selected_layouts.get(slide_num), selected_masters.get(slide_num)):
                if not layer_part or layer_part in layer_snapshots:
                    continue
                layer_linked = [selected_masters[slide_num]] if layer_part == selected_layouts.get(slide_num) and slide_num in selected_masters else []
                _, signature, cross_ids = _layer_motion(source_archive, source_entries, layer_part, layer_linked)
                layer_snapshots[layer_part] = signature
                cross_layer_targets.extend({"part": layer_part, "shape_id": value} for value in cross_ids)
            for item in replacement_items:
                if item.get("slide") != slide_num:
                    continue
                shape_id = str(item.get("shape_id", ""))
                shape = objects.get(shape_id)
                if shape is None:
                    raise MotionCloneError(f"shape ID {shape_id!r} is missing on slide {slide_num}")
                text_nodes = shape.xpath(".//a:t", namespaces=NS)
                original = "".join((node.text or "") for node in text_nodes)
                if len(text_nodes) != 1:
                    raise MotionCloneError("only single-run text shapes are supported")
                expected = item.get("original_text_sha256")
                if not isinstance(expected, str) or text_sha256(original) != expected:
                    raise MotionCloneError(f"original text hash mismatch for slide {slide_num}, shape {shape_id}")
                new_text = item.get("text")
                if not isinstance(new_text, str) or not new_text:
                    raise MotionCloneError("replacement text must be a nonempty string")
                text_nodes[0].text = new_text
                changed_slides.add(slide_num)
            for item in slide_image_items:
                if item.get("slide") != slide_num:
                    continue
                shape_id = str(item.get("shape_id", ""))
                shape = objects.get(shape_id)
                if shape is None or ET.QName(shape).localname != "pic":
                    raise MotionCloneError(f"image replacement target {shape_id!r} is not a picture on slide {slide_num}")
                binding = next((row for row in media_references if row["slide"] == slide_num and row["shape_id"] == shape_id), None)
                if binding is None:
                    raise MotionCloneError(f"picture has no embedded media on slide {slide_num}, shape {shape_id}")
                media_part = binding["media_part"]
                if source_entries[media_part].file_size > 64 * 1024 * 1024:
                    raise MotionCloneError("embedded image is larger than the supported 64 MiB limit")
                with source_archive.open(source_entries[media_part]) as stream:
                    original_bytes = stream.read()
                expected = item.get("original_media_sha256")
                if not isinstance(expected, str) or hashlib.sha256(original_bytes).hexdigest() != expected:
                    raise MotionCloneError(f"original media hash mismatch for slide {slide_num}, shape {shape_id}")
                replacement_path = Path(item.get("path", "")).expanduser().resolve()
                if not replacement_path.is_file() or replacement_path.stat().st_size > 64 * 1024 * 1024:
                    raise MotionCloneError("image replacement path must be an existing file no larger than 64 MiB")
                replacement_bytes = replacement_path.read_bytes()
                if not replacement_bytes:
                    raise MotionCloneError("image replacement file is empty")
                original_format = _image_format(media_part, original_bytes)
                replacement_format = _image_format(media_part, replacement_bytes)
                path_format = _image_format(replacement_path.name, replacement_bytes)
                if replacement_format != original_format or path_format != original_format:
                    raise MotionCloneError("replacement image must preserve the embedded media format")
                old = image_targets.get(media_part)
                record = {"slide": slide_num, "shape_id": shape_id, "media_part": media_part,
                          "bytes": replacement_bytes, "sha256": hashlib.sha256(replacement_bytes).hexdigest()}
                if old and old["sha256"] != record["sha256"]:
                    raise MotionCloneError("shared media references must use the same replacement bytes")
                image_targets.setdefault(media_part, record)
                mapped_image_targets.add(("slide", slide_num, shape_id))
            if slide_num in changed_slides:
                changed_parts[name] = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

        for item in layout_image_items:
            layout_part = item.get("layout_part")
            shape_id = str(item.get("shape_id", ""))
            if not isinstance(layout_part, str) or layout_part not in source_entries or not layout_part.startswith("ppt/slideLayouts/"):
                raise MotionCloneError("layout image replacement requires a valid layout_part")
            selected_users = [n for n, part in selected_layouts.items() if part == layout_part]
            if not selected_users:
                raise MotionCloneError(f"layout image target is not used by a selected slide: {layout_part}")
            all_users = []
            for n, slide_part in enumerate(source_order, start=1):
                if _related_part(source_archive, source_entries, slide_part, "slideLayout") == layout_part:
                    all_users.append(n)
            if any(n not in slides for n in all_users):
                raise MotionCloneError(f"layout image replacement would affect unselected slides using {layout_part}")
            layout_root = _read_xml(source_archive, source_entries, layout_part)
            layout_objects = _slide_objects(layout_root)
            shape = layout_objects.get(shape_id)
            if shape is None or ET.QName(shape).localname != "pic":
                raise MotionCloneError(f"layout image target {shape_id!r} is not a picture in {layout_part}")
            refs = _image_bindings(source_archive, source_entries, layout_part, selected_users[0], layout_root, layout_objects)
            binding = next((row for row in refs if row["shape_id"] == shape_id), None)
            if binding is None:
                raise MotionCloneError(f"layout picture has no embedded media: {layout_part}, shape {shape_id}")
            media_part = binding["media_part"]
            if source_entries[media_part].file_size > 64 * 1024 * 1024:
                raise MotionCloneError("embedded image is larger than the supported 64 MiB limit")
            with source_archive.open(source_entries[media_part]) as stream:
                original_bytes = stream.read()
            expected = item.get("original_media_sha256")
            if not isinstance(expected, str) or hashlib.sha256(original_bytes).hexdigest() != expected:
                raise MotionCloneError(f"original media hash mismatch for layout {layout_part}, shape {shape_id}")
            replacement_path = Path(item.get("path", "")).expanduser().resolve()
            if not replacement_path.is_file() or replacement_path.stat().st_size > 64 * 1024 * 1024:
                raise MotionCloneError("image replacement path must be an existing file no larger than 64 MiB")
            replacement_bytes = replacement_path.read_bytes()
            if not replacement_bytes:
                raise MotionCloneError("image replacement file is empty")
            fmt = _image_format(media_part, original_bytes)
            if _image_format(media_part, replacement_bytes) != fmt or _image_format(replacement_path.name, replacement_bytes) != fmt:
                raise MotionCloneError("replacement image must preserve the embedded media format")
            old = image_targets.get(media_part)
            record = {"bytes": replacement_bytes, "sha256": hashlib.sha256(replacement_bytes).hexdigest()}
            if old and old["sha256"] != record["sha256"]:
                raise MotionCloneError("shared media references must use the same replacement bytes")
            image_targets.setdefault(media_part, record)
            mapped_image_targets.add(("layout", layout_part, shape_id))
            # A layout picture is one object shared by every selected user of this layout.
            media_references.append({"layer": "layout", "part": layout_part, "slide": selected_users[0], "shape_id": shape_id, "media_part": media_part})

        for media_part in image_targets:
            refs = [row for row in media_references if row["media_part"] == media_part]
            if any((row.get("layer", "slide"), row.get("part", row.get("slide")), row["shape_id"]) not in mapped_image_targets for row in refs):
                raise MotionCloneError("all selected-slide references to shared media must be explicitly mapped")
            for slide_num, slide_part in enumerate(source_order, start=1):
                slide_root = _read_xml(source_archive, source_entries, slide_part)
                slide_refs = _image_bindings(source_archive, source_entries, slide_part, slide_num, slide_root, _slide_objects(slide_root))
                for row in slide_refs:
                    if row["media_part"] != media_part:
                        continue
                    key = ("slide", slide_num, row["shape_id"])
                    if key not in mapped_image_targets:
                        raise MotionCloneError("image replacement would affect an unmapped slide sharing the media")
            for layer_part in source_entries:
                if not (layer_part.startswith(("ppt/slideLayouts/", "ppt/slideMasters/")) and layer_part.endswith(".xml")):
                    continue
                layer_root = _read_xml(source_archive, source_entries, layer_part)
                for row in _image_bindings(source_archive, source_entries, layer_part, 0, layer_root, _slide_objects(layer_root)):
                    if row["media_part"] == media_part and ("layout" if layer_part.startswith("ppt/slideLayouts/") else "master", layer_part, row["shape_id"]) not in mapped_image_targets:
                        raise MotionCloneError("image replacement would affect an unmapped layout or master sharing the media")
            changed_parts[media_part] = image_targets[media_part]["bytes"]

        changed_parts["ppt/presentation.xml"] = _set_selected_slides(presentation_root, slides)
        expected_order = [source_order[i - 1] for i in slides]
    except Exception:
        source_archive.close()
        raise
    source_archive.close()

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with ZipFile(source, "r") as original, ZipFile(temp, "w") as clone:
            clone.comment = original.comment
            entries = _safe_entries(original)
            for info in original.infolist():
                if info.filename in changed_parts:
                    clone.writestr(info, changed_parts[info.filename])
                else:
                    with original.open(info) as in_stream, clone.open(info, "w") as out_stream:
                        shutil.copyfileobj(in_stream, out_stream, length=1024 * 1024)
        # Re-open only the required XML, not large media parts.
        with ZipFile(temp, "r") as emitted_archive:
            emitted_entries = _safe_entries(emitted_archive)
            _, output_order = _slide_order(emitted_archive, emitted_entries)
            if output_order != expected_order:
                raise MotionCloneError("emitted PPTX slide order failed readback")
            for slide_num, name in zip(slides, output_order):
                root = _read_xml(emitted_archive, emitted_entries, name)
                linked = [p for p in (selected_layouts.get(slide_num), selected_masters.get(slide_num)) if p]
                _, signature, _ = _layer_motion(emitted_archive, emitted_entries, name, linked)
                if signature != snapshots[slide_num]:
                    raise MotionCloneError(f"emitted timing or transition mismatch on source slide {slide_num}")
            for part, signature in layer_snapshots.items():
                linked = []
                if part in selected_layouts.values():
                    users = [n for n, p in selected_layouts.items() if p == part]
                    linked = [selected_masters[users[0]]] if users[0] in selected_masters else []
                _, actual, _ = _layer_motion(emitted_archive, emitted_entries, part, linked)
                if actual != signature:
                    raise MotionCloneError(f"emitted timing or transition mismatch in related part {part}")
        if _sha_file(source) != actual_sha:
            raise MotionCloneError("source changed while clone was being prepared")
        try:
            os.link(temp, output)
        except FileExistsError as error:
            raise MotionCloneError("output already exists; refusing to overwrite") from error
        temp.unlink()
    except Exception:
        temp.unlink(missing_ok=True)
        raise

    return {
        "output": str(output),
        "source_sha256": actual_sha,
        "output_sha256": _sha_file(output),
        "source_slides": slides,
        "replacements": len(replacement_items),
        "image_replacements": len(image_items),
        "timing_transition_readback": "passed",
        "related_motion_readback": "passed",
        "cross_layer_animation_targets": cross_layer_targets,
        "playback_verified": False,
        "limitations": [
            "slide selection supports only contiguous source-order ranges",
            "text replacement supports one a:t run per shape",
            "image replacement supports PNG, JPEG, GIF, BMP, and TIFF media with matching filename extensions",
            "image replacement preserves picture XML and replaces shared media only when every selected-slide reference is mapped to the same bytes",
            "layout image replacement is blocked when that layout is also used by an unselected slide",
            "master-layer image replacement is not supported",
            "PowerPoint playback is not verified",
        ],
    }
