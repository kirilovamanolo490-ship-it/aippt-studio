"""Explicit, bounded edits to native PPTX objects, without rebuilding the deck.

The public interface is ``inspect_pptx(source, slides=None)`` and
``patch_pptx(request, output)``.  Output must be a new directory.  A patch is a
candidate for native visual review; this module does not assert that a larger
font fits, or infer which object a user intended from its text or location.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import posixpath
import re
import shutil
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from xml.parsers import expat
from zipfile import BadZipFile, ZipFile

from lxml import etree as ET
from PIL import Image, ImageOps

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
SHAPE_TAGS = {"sp", "pic", "grpSp", "cxnSp", "graphicFrame"}
EMU = {"mm": Decimal(36000), "inch": Decimal(914400)}
MAX_PACKAGE_BYTES = 512 * 1024 * 1024


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _xml(data: bytes):
    try:
        root = ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True))
    except ET.XMLSyntaxError as error:
        raise ValueError("invalid PPTX XML") from error
    if root.getroottree().docinfo.doctype:
        raise ValueError("DTD-bearing XML is unsupported")
    return root


def _package(data: bytes) -> dict[str, bytes]:
    if len(data) > MAX_PACKAGE_BYTES:
        raise ValueError("PPTX package exceeds the 512 MiB inspection limit")
    try:
        with ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError("duplicate ZIP members are unsupported")
            if sum(i.file_size for i in archive.infolist()) > MAX_PACKAGE_BYTES:
                raise ValueError("PPTX exceeds the 512 MiB uncompressed inspection limit")
            return {i.filename: archive.read(i) for i in archive.infolist()}
    except (BadZipFile, RuntimeError) as error:
        raise ValueError("invalid or encrypted PPTX package") from error


def _slides(parts: dict[str, bytes]) -> list[str]:
    rels = {r.get("Id"): r for r in _xml(parts["ppt/_rels/presentation.xml.rels"])}
    names = []
    for slide in _xml(parts["ppt/presentation.xml"]).findall("p:sldIdLst/p:sldId", NS):
        rel = rels.get(slide.get(f"{{{NS['r']}}}id"))
        if rel is None or rel.get("TargetMode") == "External":
            raise ValueError("missing or external slide relationship")
        target = rel.get("Target", "")
        name = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "ppt/" + target)
        if not name.startswith("ppt/slides/") or name not in parts or name in names:
            raise ValueError("invalid or duplicate slide relationship")
        names.append(name)
    return names


def _local(element) -> str:
    return ET.QName(element).localname if isinstance(element.tag, str) else ""


def _identity(element):
    matches = element.xpath("./*/p:cNvPr", namespaces=NS)
    return matches[0] if len(matches) == 1 else None


def _geometry(element) -> dict | None:
    xfrm = element.find("p:spPr/a:xfrm", NS)
    if xfrm is None:
        xfrm = element.find("p:xfrm", NS)
    if xfrm is None:
        return None
    off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
    if off is None or ext is None:
        return None
    try:
        values = {"x": int(off.attrib["x"]), "y": int(off.attrib["y"]),
                  "width": int(ext.attrib["cx"]), "height": int(ext.attrib["cy"])}
        rotation = int(xfrm.get("rot", "0")) / 60000
    except (KeyError, ValueError) as error:
        raise ValueError("invalid explicit object geometry") from error
    return {"emu": values, "mm": {k: v / 36000 for k, v in values.items()},
            "inch": {k: v / 914400 for k, v in values.items()},
            "rotation_degrees": rotation,
            "flip_horizontal": xfrm.get("flipH", "0") in {"1", "true"},
            "flip_vertical": xfrm.get("flipV", "0") in {"1", "true"}}


def _font_info(element) -> dict:
    runs = []
    body = element.find("p:txBody", NS)
    if body is not None:
        for paragraph_index, paragraph in enumerate(body.findall("a:p", NS), 1):
            default = paragraph.find("a:pPr/a:defRPr", NS)
            default_size = default.get("sz") if default is not None else None
            for run_index, run in enumerate((c for c in paragraph if _local(c) in {"r", "fld", "br"}), 1):
                prop = run.find("a:rPr", NS)
                raw_size = prop.get("sz") if prop is not None else None
                size = raw_size or default_size
                fonts = {}
                if prop is not None:
                    fonts = {k: node.get("typeface") for k in ("latin", "ea", "cs")
                             if (node := prop.find(f"a:{k}", NS)) is not None}
                runs.append({"paragraph": paragraph_index, "run": run_index,
                             "kind": _local(run), "text": "".join(run.xpath("./a:t/text()", namespaces=NS)),
                             "size_pt": int(size) / 100 if size is not None else None,
                             "size_origin": "run" if raw_size else "paragraph" if default_size else "inherited_unresolved",
                             "fonts": fonts, "properties": dict(prop.attrib) if prop is not None else {}})
    return {"runs": runs, "explicit_sizes_pt": sorted({r["size_pt"] for r in runs if r["size_pt"] is not None}),
            "inherited_fonts_resolved": False}


def _editable_reason(element, tree) -> str | None:
    if element.getparent() is not tree:
        return "grouped or nested object; only explicit top-level objects are editable"
    if _local(element) not in {"sp", "pic"}:
        return "only ordinary shapes, text boxes and pictures are supported"
    root_transform = tree.find("p:grpSpPr/a:xfrm", NS)
    if root_transform is not None:
        off, child_off = root_transform.find("a:off", NS), root_transform.find("a:chOff", NS)
        ext, child_ext = root_transform.find("a:ext", NS), root_transform.find("a:chExt", NS)
        if (root_transform.get("rot", "0") != "0"
                or root_transform.get("flipH", "0") not in {"0", "false"}
                or root_transform.get("flipV", "0") not in {"0", "false"}
                or (len(root_transform) and (off is None or child_off is None or ext is None or child_ext is None
                                            or dict(off.attrib) != dict(child_off.attrib)
                                            or dict(ext.attrib) != dict(child_ext.attrib)))):
            return "slide shape tree has a non-identity group transform; slide coordinates are ambiguous"
    if element.xpath("./p:spPr/a:scene3d | ./p:spPr/a:sp3d | ./p:spPr/a:custGeom", namespaces=NS):
        return "3D or custom geometry requires a dedicated adapter"
    if _geometry(element) is None:
        return "object has inherited or missing geometry; resolve it explicitly in the source first"
    return None


def _object(element, tree, order: list | None = None) -> dict:
    nv = _identity(element)
    if order is None:
        order = [e for e in tree if _local(e) in SHAPE_TAGS]
    body = element.find("p:txBody", NS)
    text = "\n".join("".join(p.xpath(".//a:t/text()", namespaces=NS))
                     for p in body.findall("a:p", NS)) if body is not None else ""
    bodypr = body.find("a:bodyPr", NS) if body is not None else None
    autofit = [c for c in bodypr if _local(c) in {"noAutofit", "normAutofit", "spAutoFit"}] if bodypr is not None else []
    reason = _editable_reason(element, tree)
    return {"shape_id": nv.get("id") if nv is not None else None,
            "name": nv.get("name", "") if nv is not None else "",
            "type": _local(element), "text": text, "geometry": _geometry(element),
            "font": _font_info(element),
            "text_autofit": {"mode": _local(autofit[0]) if len(autofit) == 1 else "inherited_unresolved",
                             "properties": dict(autofit[0].attrib) if len(autofit) == 1 else {}},
            "z_order": order.index(element) if element in order else None,
            "editable": reason is None, "unsupported_reason": reason}


def _inspect(parts: dict[str, bytes], source_sha256: str, slides: list[int] | None = None) -> dict:
    names = _slides(parts)
    if slides is not None and (not isinstance(slides, list) or not slides
                               or any(type(s) is not int or not 1 <= s <= len(names) for s in slides)
                               or len(set(slides)) != len(slides)):
        raise ValueError("slides must contain distinct existing 1-based slide numbers")
    size = _xml(parts["ppt/presentation.xml"]).find("p:sldSz", NS)
    result = []
    for number, name in enumerate(names, 1):
        if slides is not None and number not in slides:
            continue
        root = _xml(parts[name])
        tree = root.find("p:cSld/p:spTree", NS)
        if tree is None:
            raise ValueError(f"slide {number} has no explicit shape tree")
        objects = [_object(e, tree) for e in tree.iter() if _local(e) in SHAPE_TAGS]
        result.append({"slide": number, "part": name, "objects": objects})
    return {"schema_version": 1, "source_sha256": source_sha256,
            "slide_size_emu": {"width": int(size.get("cx")), "height": int(size.get("cy"))} if size is not None else None,
            "slides": result, "verification": "OOXML inspection; native visual fit and font rendering remain unverified"}


def inspect_pptx(source: Path, *, slides: list[int] | None = None) -> dict:
    """List exact shape IDs, explicit geometry, per-run sizes and edit support."""
    data = Path(source).read_bytes()
    return _inspect(_package(data), _sha(data), slides)


def _fields(value, allowed: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"{name} must be an object with only these fields: {', '.join(sorted(allowed))}")


def _number(value, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be a finite number")
    return number


def _rounded(number: Decimal) -> int:
    return int(number.to_integral_value(rounding=ROUND_HALF_UP))


def _shape_id(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        raise ValueError("shape_id must be an exact positive numeric object ID")
    return str(value)


def _select(tree, shape_id):
    identifier = _shape_id(shape_id)
    matches = [e for e in tree.iter() if _local(e) in SHAPE_TAGS and _identity(e) is not None
               and _identity(e).get("id") == identifier]
    if len(matches) != 1:
        raise ValueError(f"shape_id {identifier} is missing or ambiguous; inspect the source and use its exact ID")
    element = matches[0]
    reason = _editable_reason(element, tree)
    if reason:
        raise ValueError(f"shape_id {identifier}: {reason}")
    return element


def _set_geometry(element, updates: dict[str, int]) -> None:
    xfrm = element.find("p:spPr/a:xfrm", NS)
    for key, value in updates.items():
        node = xfrm.find("a:off" if key in {"x", "y"} else "a:ext", NS)
        node.set({"width": "cx", "height": "cy"}.get(key, key), str(value))


def _set_font(element, value, autofit: str | None = None) -> None:
    size = _number(value, "font_size_pt") * 100
    if not 100 <= size <= 400000 or size != size.to_integral_value():
        raise ValueError("font_size_pt must be 1–4000 pt in increments of 0.01 pt")
    body = element.find("p:txBody", NS)
    if body is None:
        raise ValueError("font_size_pt requires a shape with a native text body")
    if autofit is not None:
        if autofit != "none":
            raise ValueError("autofit supports only explicit 'none' with font_size_pt")
        bodypr = body.find("a:bodyPr", NS)
        if bodypr is None:
            raise ValueError("text body has no explicit bodyPr; resolve its inheritance in the source first")
        for child in list(bodypr):
            if _local(child) in {"noAutofit", "normAutofit", "spAutoFit"}:
                bodypr.remove(child)
        index = next((i for i, c in enumerate(bodypr) if _local(c) in {"scene3d", "sp3d", "flatTx", "extLst"}), len(bodypr))
        bodypr.insert(index, ET.Element(f"{{{NS['a']}}}noAutofit"))
    # normAutofit can shrink the actual rendered size below the requested size.
    # Silently disabling it is a different operation; refuse instead.
    if body.find("a:bodyPr/a:normAutofit", NS) is not None:
        raise ValueError("normal text autofit may shrink the requested font; explicitly set autofit:'none' to disable it")
    if body.find("a:bodyPr/a:spAutoFit", NS) is not None:
        raise ValueError("shape autofit may resize the object; explicitly set autofit:'none' to disable it")
    for paragraph in body.findall("a:p", NS):
        ppr = paragraph.find("a:pPr", NS)
        if ppr is None:
            ppr = ET.Element(f"{{{NS['a']}}}pPr")
            paragraph.insert(0, ppr)
        default = ppr.find("a:defRPr", NS)
        if default is None:
            default = ET.Element(f"{{{NS['a']}}}defRPr")
            index = next((i for i, c in enumerate(ppr) if _local(c) == "extLst"), len(ppr))
            ppr.insert(index, default)
        default.set("sz", str(int(size)))
        for run in paragraph:
            if _local(run) not in {"r", "fld", "br"}:
                continue
            prop = run.find("a:rPr", NS)
            if prop is None:
                prop = ET.Element(f"{{{NS['a']}}}rPr")
                run.insert(0, prop)
            prop.set("sz", str(int(size)))
        end = paragraph.find("a:endParaRPr", NS)
        if end is not None:
            end.set("sz", str(int(size)))


def _axis_geometry(element) -> dict:
    geometry = _geometry(element)
    if geometry["rotation_degrees"] % 360 or geometry["flip_horizontal"] or geometry["flip_vertical"]:
        raise ValueError("align/distribute requires unrotated, unflipped objects; transformed visual bounds are ambiguous")
    return geometry["emu"]


def _replace_text_run(element, edit: dict) -> None:
    """Replace exactly one ordinary text run without flattening its formatting.

    PowerPoint field text, paragraph breaks and rich-text spans carry structure
    that a generic string replacement cannot safely recreate.  The caller may
    use the 1-based paragraph/run coordinates from ``pptx-inspect`` when the
    same text occurs more than once in a shape.
    """
    expected = edit.get("expected_text")
    replacement = edit.get("replacement_text")
    if not isinstance(expected, str) or not expected or not isinstance(replacement, str):
        raise ValueError("replace_text requires nonempty expected_text and string replacement_text")
    if len(replacement) > 10000 or any(char in replacement for char in "\r\n\x00"):
        raise ValueError("replacement_text must be a single XML-safe text run of at most 10000 characters")
    paragraph_index, run_index = edit.get("paragraph"), edit.get("run")
    if (paragraph_index is None) != (run_index is None):
        raise ValueError("paragraph and run must be supplied together")
    if paragraph_index is not None and (type(paragraph_index) is not int or paragraph_index < 1
                                        or type(run_index) is not int or run_index < 1):
        raise ValueError("paragraph and run must be positive 1-based integers")
    body = element.find("p:txBody", NS)
    if body is None:
        raise ValueError("replace_text requires an ordinary text shape")
    matches = []
    for p_number, paragraph in enumerate(body.findall("a:p", NS), 1):
        for r_number, run in enumerate((child for child in paragraph if _local(child) in {"r", "fld", "br"}), 1):
            if paragraph_index is not None and (p_number, r_number) != (paragraph_index, run_index):
                continue
            if _local(run) != "r":
                if paragraph_index is not None:
                    raise ValueError("the selected text item is a field or break, not an ordinary run")
                continue
            texts = run.findall("a:t", NS)
            if len(texts) != 1:
                if paragraph_index is not None:
                    raise ValueError("the selected run does not have one editable text node")
                continue
            if (texts[0].text or "") == expected:
                matches.append(texts[0])
            elif paragraph_index is not None:
                raise ValueError("expected_text does not match the selected run; inspect the current PPTX")
    if len(matches) != 1:
        raise ValueError("expected_text must identify exactly one run; specify paragraph and run if repeated")
    node = matches[0]
    node.text = replacement
    if replacement[:1].isspace() or replacement[-1:].isspace():
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _add_textbox(tree, edit: dict, slide_size: tuple[int, int]):
    """Append one plain text box without reserializing existing slide children."""
    text = edit.get("text")
    if not isinstance(text, str) or not text or len(text) > 10000 or any(c in text for c in "\r\n\x00"):
        raise ValueError("add_textbox requires nonempty single-line XML-safe text of at most 10000 characters")
    geometry = edit.get("geometry")
    _fields(geometry, {"unit", "x", "y", "width", "height"}, "geometry")
    if set(geometry) != {"unit", "x", "y", "width", "height"} or geometry.get("unit") not in EMU:
        raise ValueError("add_textbox geometry requires unit mm or inch and explicit x, y, width, height")
    values = {key: _rounded(_number(geometry[key], f"geometry.{key}") * EMU[geometry["unit"]])
              for key in ("x", "y", "width", "height")}
    if values["x"] < 0 or values["y"] < 0 or values["width"] <= 0 or values["height"] <= 0:
        raise ValueError("add_textbox geometry must have nonnegative position and positive dimensions")
    if values["x"] + values["width"] > slide_size[0] or values["y"] + values["height"] > slide_size[1]:
        raise ValueError("add_textbox geometry must fit within the explicit slide bounds")
    if any(_local(e) not in SHAPE_TAGS | {"nvGrpSpPr", "grpSpPr", "extLst"} for e in tree):
        raise ValueError("unknown top-level slide content makes insertion order ambiguous")
    if tree.find("p:extLst", NS) is not None and list(tree).count(tree.find("p:extLst", NS)) != 1:
        raise ValueError("slide extension list is ambiguous")
    group = tree.find("p:grpSpPr/a:xfrm", NS)
    if group is not None:
        off, child_off = group.find("a:off", NS), group.find("a:chOff", NS)
        ext, child_ext = group.find("a:ext", NS), group.find("a:chExt", NS)
        if (group.get("rot", "0") != "0" or group.get("flipH", "0") not in {"0", "false"}
                or group.get("flipV", "0") not in {"0", "false"}
                or (len(group) and (off is None or child_off is None or ext is None or child_ext is None
                                    or dict(off.attrib) != dict(child_off.attrib)
                                    or dict(ext.attrib) != dict(child_ext.attrib)))):
            raise ValueError("slide shape tree has a non-identity group transform; insertion coordinates are ambiguous")
    font_size = edit.get("font_size_pt")
    font_name = edit.get("font_name")
    size_attr = None
    if font_size is not None:
        size = _number(font_size, "font_size_pt") * 100
        if not 100 <= size <= 400000 or size != size.to_integral_value():
            raise ValueError("font_size_pt must be 1–4000 pt in increments of 0.01 pt")
        size_attr = str(int(size))
    if font_name is not None and (not isinstance(font_name, str) or not font_name.strip() or len(font_name) > 128
                                  or any(c in font_name for c in "\r\n\x00")):
        raise ValueError("font_name must be a single nonempty typeface name of at most 128 characters")
    ids = []
    for node in tree.xpath(".//p:cNvPr", namespaces=NS):
        raw_id = node.get("id")
        if raw_id is None or not raw_id.isdigit():
            raise ValueError("slide contains an invalid shape ID; unique insertion cannot be guaranteed")
        ids.append(int(raw_id))
    new_id = max(ids, default=1) + 1
    shape = ET.Element(f"{{{NS['p']}}}sp")
    nv = ET.SubElement(shape, f"{{{NS['p']}}}nvSpPr")
    ET.SubElement(nv, f"{{{NS['p']}}}cNvPr", id=str(new_id), name=f"Text Box {new_id}")
    ET.SubElement(nv, f"{{{NS['p']}}}cNvSpPr", txBox="1")
    ET.SubElement(nv, f"{{{NS['p']}}}nvPr")
    sppr = ET.SubElement(shape, f"{{{NS['p']}}}spPr")
    xfrm = ET.SubElement(sppr, f"{{{NS['a']}}}xfrm")
    ET.SubElement(xfrm, f"{{{NS['a']}}}off", x=str(values["x"]), y=str(values["y"]))
    ET.SubElement(xfrm, f"{{{NS['a']}}}ext", cx=str(values["width"]), cy=str(values["height"]))
    geom = ET.SubElement(sppr, f"{{{NS['a']}}}prstGeom", prst="rect")
    ET.SubElement(geom, f"{{{NS['a']}}}avLst")
    body = ET.SubElement(shape, f"{{{NS['p']}}}txBody")
    ET.SubElement(body, f"{{{NS['a']}}}bodyPr", wrap="square", lIns="0", rIns="0", tIns="0", bIns="0", anchor="t")
    ET.SubElement(body, f"{{{NS['a']}}}lstStyle")
    paragraph = ET.SubElement(body, f"{{{NS['a']}}}p")
    run = ET.SubElement(paragraph, f"{{{NS['a']}}}r")
    if size_attr is not None or font_name is not None:
        props = ET.SubElement(run, f"{{{NS['a']}}}rPr", lang="en-US")
        if size_attr is not None:
            props.set("sz", size_attr)
        if font_name is not None:
            ET.SubElement(props, f"{{{NS['a']}}}latin", typeface=font_name)
            ET.SubElement(props, f"{{{NS['a']}}}ea", typeface=font_name)
    node = ET.SubElement(run, f"{{{NS['a']}}}t")
    node.text = text
    if text[:1].isspace() or text[-1:].isspace():
        node.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    end_props = ET.SubElement(paragraph, f"{{{NS['a']}}}endParaRPr", lang="en-US")
    if size_attr is not None:
        end_props.set("sz", size_attr)
    index = next((i for i, child in enumerate(tree) if _local(child) == "extLst"), len(tree))
    tree.insert(index, shape)
    return shape


_RASTER_EXTENSIONS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG",
                      ".gif": "GIF", ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF"}


def _picture_media_part(parts: dict[str, bytes], slide_part: str, element) -> str:
    """Resolve a picture's embedded raster media, refusing external/shared targets."""
    if _local(element) != "pic":
        raise ValueError("replace_picture requires an exact top-level picture object")
    blips = element.xpath("./p:blipFill/a:blip", namespaces=NS)
    if len(blips) != 1:
        raise ValueError("picture must have exactly one explicit embedded image")
    rid = blips[0].get(f"{{{NS['r']}}}embed")
    slide_root = _xml(parts[slide_part])
    if len(slide_root.xpath(".//a:blip[@r:embed=$rid]", namespaces=NS, rid=rid)) != 1:
        raise ValueError("selected image relationship is reused by multiple picture fills in the slide")
    rels_name = posixpath.join(posixpath.dirname(slide_part), "_rels", posixpath.basename(slide_part) + ".rels")
    if rels_name not in parts:
        raise ValueError("selected picture has no explicit slide relationships")
    rels = _xml(parts[rels_name])
    matches = [rel for rel in rels if rel.get("Id") == rid]
    if len(matches) != 1 or not matches[0].get("Type", "").endswith("/image"):
        raise ValueError("selected picture relationship is missing, ambiguous or not an image")
    relation = matches[0]
    if relation.get("TargetMode") == "External":
        raise ValueError("linked external pictures cannot be replaced by this local operation")
    target = relation.get("Target", "")
    media_part = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                                    else posixpath.join(posixpath.dirname(slide_part), target))
    if not media_part.startswith("ppt/media/") or media_part not in parts:
        raise ValueError("picture relationship does not resolve to an existing ppt/media raster part")
    references = 0
    for part_name, payload in parts.items():
        if not part_name.endswith(".rels"):
            continue
        rel_root = _xml(payload)
        for rel in rel_root:
            if rel.get("Type", "").endswith("/image") and rel.get("TargetMode") != "External":
                target_ref = rel.get("Target", "")
                owner_dir = posixpath.dirname(posixpath.dirname(part_name))
                resolved = posixpath.normpath(target_ref.lstrip("/") if target_ref.startswith("/")
                                              else posixpath.join(owner_dir, target_ref))
                references += resolved == media_part
    if references != 1:
        raise ValueError("selected image media is shared or ambiguously referenced; replacing it could alter other objects")
    return media_part


def _replacement_raster(path_value, media_part: str, original: bytes) -> bytes:
    """Encode a local static raster as the existing media format, without cropping."""
    if not isinstance(path_value, (str, Path)) or not str(path_value):
        raise ValueError("replace_picture requires replacement_image as a local image path")
    suffix = Path(media_part).suffix.lower()
    image_format = _RASTER_EXTENSIONS.get(suffix)
    if image_format is None:
        raise ValueError("replace_picture supports only PNG, JPEG, GIF, BMP and TIFF media")
    try:
        with Image.open(io.BytesIO(original)) as existing:
            if existing.format != image_format:
                raise ValueError("existing media extension and raster format do not match")
        requested_path = Path(path_value).expanduser()
        if requested_path.is_symlink():
            raise ValueError("replacement_image symlinks are unsupported; use the resolved regular file path")
        replacement_path = requested_path.resolve(strict=True)
        if not replacement_path.is_file() or replacement_path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("replacement_image must be a regular image file no larger than 64 MiB")
        with Image.open(replacement_path) as source_image:
            if source_image.format not in _RASTER_EXTENSIONS.values():
                raise ValueError("replacement_image must be a supported raster image")
            if getattr(source_image, "n_frames", 1) != 1:
                raise ValueError("animated replacement images are unsupported")
            if source_image.width * source_image.height > 50_000_000:
                raise ValueError("replacement_image exceeds the 50 megapixel limit")
            image = ImageOps.exif_transpose(source_image)
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            if image_format == "JPEG":
                if has_alpha:
                    raise ValueError("transparent replacement cannot be encoded as JPEG; choose an opaque image")
                image = image.convert("RGB")
            elif image_format in {"BMP", "GIF"} and has_alpha:
                raise ValueError("alpha transparency is unsupported for the existing media format")
            elif image_format == "TIFF":
                image = image.copy()
            elif image_format == "PNG":
                image = image.convert("RGBA" if has_alpha else "RGB")
            elif image_format == "GIF":
                image = image.convert("P", palette=Image.Palette.ADAPTIVE)
            encoded = io.BytesIO()
            save_options = {"quality": 95, "subsampling": 0} if image_format == "JPEG" else {}
            icc_profile = source_image.info.get("icc_profile")
            if icc_profile and image_format in {"JPEG", "PNG", "TIFF"}:
                save_options["icc_profile"] = icc_profile
            image.save(encoded, format=image_format, **save_options)
            result = encoded.getvalue()
        # Reopen the encoded result before it can enter the candidate package.
        with Image.open(io.BytesIO(result)) as check:
            check.verify()
            if check.format != image_format:
                raise ValueError("replacement raster encoding did not preserve the target media format")
        return result
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("replacement_image is not a readable supported raster image") from error


def _apply_edit(tree, order: list, edit: dict, *, parts=None, slide_part=None) -> tuple[dict, set, dict]:
    common = {"action", "slide"}
    action = edit.get("action") if isinstance(edit, dict) else None
    fields = {"set": {"shape_id", "geometry", "font_size_pt", "autofit"},
              "add_textbox": {"text", "geometry", "font_size_pt", "font_name"},
              "replace_text": {"shape_id", "expected_text", "replacement_text", "paragraph", "run"},
              "replace_picture": {"shape_id", "replacement_image"},
              "z_order": {"shape_id", "position"},
              "align": {"shape_ids", "axis", "anchor_shape_id"},
              "distribute": {"shape_ids", "axis"}}
    if action not in fields:
        raise ValueError("action must be set, add_textbox, replace_text, z_order, align or distribute")
    _fields(edit, common | fields[action], "edit")
    if action == "add_textbox":
        if parts is None:
            raise ValueError("add_textbox requires slide-size package context")
        slide_size = _xml(parts["ppt/presentation.xml"]).find("p:sldSz", NS)
        if slide_size is None:
            raise ValueError("presentation has no explicit slide size")
        added = _add_textbox(tree, edit, (int(slide_size.get("cx")), int(slide_size.get("cy"))))
        targets = [added]
    elif action in {"set", "replace_text", "replace_picture", "z_order"}:
        targets = [_select(tree, edit.get("shape_id"))]
    else:
        ids = edit.get("shape_ids")
        minimum = 3 if action == "distribute" else 2
        if not isinstance(ids, list) or not minimum <= len(ids) <= 100:
            raise ValueError(f"{action} requires {minimum}–100 explicit shape_ids")
        targets = [_select(tree, i) for i in ids]
        if len(set(targets)) != len(targets):
            raise ValueError("shape_ids must be distinct")
    before = [] if action == "add_textbox" else [_object(e, tree, order) for e in targets]
    media_changes = {}
    if action == "add_textbox":
        pass
    elif action == "set":
        if "autofit" in edit and (edit["autofit"] != "none" or "font_size_pt" not in edit):
            raise ValueError("autofit supports only explicit 'none' with font_size_pt")
        if "geometry" not in edit and "font_size_pt" not in edit:
            raise ValueError("set requires geometry and/or font_size_pt")
        if "geometry" in edit:
            geometry = edit["geometry"]
            _fields(geometry, {"unit", "x", "y", "width", "height"}, "geometry")
            if geometry.get("unit") not in EMU or len(geometry) < 2:
                raise ValueError("geometry requires unit mm or inch and at least one coordinate or size")
            updates = {}
            for key, value in geometry.items():
                if key == "unit":
                    continue
                emu = _rounded(_number(value, f"geometry.{key}") * EMU[geometry["unit"]])
                if abs(emu) > 27273042316900 or (key in {"width", "height"} and emu <= 0):
                    raise ValueError("geometry is outside DrawingML bounds or has non-positive dimensions")
                updates[key] = emu
            _set_geometry(targets[0], updates)
        if "font_size_pt" in edit:
            _set_font(targets[0], edit["font_size_pt"], edit.get("autofit"))
    elif action == "replace_text":
        _replace_text_run(targets[0], edit)
    elif action == "replace_picture":
        if parts is None or slide_part is None:
            raise ValueError("replace_picture requires package relationship context")
        media_part = _picture_media_part(parts, slide_part, targets[0])
        media_changes[media_part] = _replacement_raster(edit.get("replacement_image"), media_part, parts[media_part])
    elif action == "z_order":
        position = edit.get("position")
        if position not in {"front", "back", "forward", "backward"}:
            raise ValueError("z_order position must be front, back, forward or backward")
        if any(_local(e) not in SHAPE_TAGS | {"nvGrpSpPr", "grpSpPr", "extLst", ""} for e in tree):
            raise ValueError("unknown top-level content makes stacking order ambiguous")
        element = targets[0]
        old = order.index(element)
        order.remove(element)
        index = {"front": len(order), "back": 0,
                 "forward": min(old + 1, len(order)), "backward": max(old - 1, 0)}[position]
        order.insert(index, element)
    elif action == "align":
        axis = edit.get("axis")
        axes = {"left": ("x", "width", 0), "center": ("x", "width", Decimal("0.5")),
                "right": ("x", "width", 1), "top": ("y", "height", 0),
                "middle": ("y", "height", Decimal("0.5")), "bottom": ("y", "height", 1)}
        if axis not in axes:
            raise ValueError("align axis must be left, center, right, top, middle or bottom")
        anchor = _select(tree, edit.get("anchor_shape_id"))
        if anchor not in targets:
            raise ValueError("anchor_shape_id must be one of the explicit shape_ids")
        coordinate, dimension, factor = axes[axis]
        values = {e: _axis_geometry(e) for e in targets}
        reference = Decimal(values[anchor][coordinate]) + Decimal(values[anchor][dimension]) * factor
        for element in targets:
            _set_geometry(element, {coordinate: _rounded(reference - Decimal(values[element][dimension]) * factor)})
    else:
        axis = edit.get("axis")
        if axis not in {"horizontal", "vertical"}:
            raise ValueError("distribute axis must be horizontal or vertical")
        coordinate, dimension = ("x", "width") if axis == "horizontal" else ("y", "height")
        values = {e: _axis_geometry(e) for e in targets}
        ordered = sorted(targets, key=lambda e: values[e][coordinate])
        if len({v[coordinate] for v in values.values()}) != len(values):
            raise ValueError("distribution order is ambiguous because selected objects have equal positions")
        start = values[ordered[0]][coordinate]
        end = max(v[coordinate] + v[dimension] for v in values.values())
        free = end - start - sum(v[dimension] for v in values.values())
        if free < 0:
            raise ValueError("selected outer bounds have insufficient space for equal non-overlapping gaps")
        gap = Decimal(free) / (len(ordered) - 1)
        position = Decimal(start)
        for element in ordered:
            _set_geometry(element, {coordinate: _rounded(position)})
            position += values[element][dimension] + gap
    return ({"action": action, "slide": edit["slide"], "request": edit,
             "before": before, "after": [_object(e, tree, order) for e in targets]}, set(targets), media_changes)


def _child_spans(raw: bytes) -> list[tuple[int, int]]:
    """Locate direct spTree children so untouched XML stays byte-for-byte intact."""
    # Byte splicing requires an ASCII-compatible XML encoding. Presentation XML
    # is normally UTF-8; UTF-16/32 needs a separate adapter, never a guessed span.
    if b"\x00" in raw:
        raise ValueError("UTF-16/32 slide XML is unsupported for precise byte edits")
    parser = expat.ParserCreate(namespace_separator="}")
    stack, spans, active = [], [], []
    path = [f"{NS['p']}}}{name}" for name in ("sld", "cSld", "spTree")]

    def start(name, attrs):
        if stack == path:
            begin = parser.CurrentByteIndex
            quote, end = None, begin
            while end < len(raw):
                character = raw[end]
                if quote is not None:
                    if character == quote:
                        quote = None
                elif character in (34, 39):
                    quote = character
                elif character == 62:
                    break
                end += 1
            active.append((begin, end + 1, raw[begin:end].rstrip().endswith(b"/")))
        stack.append(name)

    def end(name):
        stack.pop()
        if stack == path:
            begin, start_end, empty = active.pop()
            finish = start_end if empty else raw.index(b">", parser.CurrentByteIndex) + 1
            spans.append((begin, finish))

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.Parse(raw, True)
    return spans


def _patch_slide(raw: bytes, tree, originals: dict, order: list) -> bytes:
    children = [e for e in tree if isinstance(e.tag, str)]
    spans = _child_spans(raw)
    original_children = [e for e in children if e in originals]
    if len(original_children) != len(spans):
        raise ValueError("cannot uniquely locate top-level XML object bytes")
    source = {e: raw[start:end] for e, (start, end) in zip(original_children, spans)}
    shape_order = iter(order)
    replacements = []
    for element, (start, end) in zip(original_children, spans):
        replacement = next(shape_order) if _local(element) in SHAPE_TAGS else element
        current = ET.tostring(replacement, with_tail=False)
        payload = source[replacement] if current == originals[replacement] else current
        replacements.append((start, end, payload))
    for start, end, payload in reversed(replacements):
        raw = raw[:start] + payload + raw[end:]
    additions = [e for e in children if e not in originals]
    if additions:
        if len(additions) != 1:
            raise ValueError("only one new top-level shape can be safely inserted per slide")
        current_tree = _xml(raw).find("p:cSld/p:spTree", NS)
        extension = current_tree.find("p:extLst", NS)
        if extension is not None:
            position = _child_spans(raw)[list(current_tree).index(extension)][0]
        else:
            match = re.search(rb"</(?:[A-Za-z_][\w.-]*:)?spTree\s*>", raw)
            if match is None:
                raise ValueError("cannot locate shape-tree closing tag")
            position = match.start()
        payload = ET.tostring(additions[0], encoding="UTF-8", with_tail=False)
        raw = raw[:position] + payload + raw[position:]
    _xml(raw)
    return raw


def _write_candidate(source: bytes, candidate: Path, parts: dict[str, bytes]) -> None:
    with ZipFile(io.BytesIO(source)) as original, ZipFile(candidate, "w") as archive:
        archive.comment = original.comment
        for info in original.infolist():
            archive.writestr(info, parts[info.filename])


def _verify_candidate(candidate: Path, expected: dict[str, bytes]) -> bytes:
    data = candidate.read_bytes()
    if _package(data) != expected:
        raise ValueError("candidate package verification failed; no output was published")
    return data


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def patch_pptx(request: dict, output: Path) -> dict:
    """Plan/apply explicit edits; publish a verified candidate into a new directory.

    ``edits`` are ordered. ``replace_text`` changes one exact ordinary run and
    retains its formatting; it does not rebuild paragraphs or fields. Geometry
    uses explicit mm/inch units; ``font_size_pt``
    changes all runs in the chosen text shape without rewriting text or styles.
    Explicit ``autofit: 'none'`` alongside a font edit disables automatic font
    shrinking/shape growth while retaining the object's geometry and insets.
    Align requires an explicitly selected anchor; distribute retains the chosen
    selection's outer extent. No font fitting or native rendering is performed.
    """
    _fields(request, {"source", "source_sha256", "edits", "dry_run", "expected_plan_sha256"}, "request")
    if not isinstance(request.get("source"), (str, Path)) or not str(request["source"]):
        raise ValueError("source must be an existing PPTX path")
    source = Path(request["source"]).expanduser().resolve(strict=True)
    data = source.read_bytes()
    source_hash = _sha(data)
    if not isinstance(request.get("source_sha256"), str) or request["source_sha256"].lower() != source_hash:
        raise ValueError("source changed or source_sha256 is missing; inspect the current original first")
    dry_run = request.get("dry_run", False)
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be a boolean")
    edits = request.get("edits")
    if not isinstance(edits, list) or not 1 <= len(edits) <= 100:
        raise ValueError("edits must contain 1–100 explicit operations")
    output = Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise ValueError("output must be a new directory; existing files and the source are never overwritten")
    parts = _package(data)
    names = _slides(parts)
    states, changes = {}, []
    media_changes = {}
    for edit in edits:
        slide = edit.get("slide") if isinstance(edit, dict) else None
        if type(slide) is not int or not 1 <= slide <= len(names):
            raise ValueError("slide must be an existing 1-based slide number")
        if slide not in states:
            root = _xml(parts[names[slide - 1]])
            tree = root.find("p:cSld/p:spTree", NS)
            if tree is None:
                raise ValueError(f"slide {slide} has no explicit shape tree")
            originals = {e: ET.tostring(e, with_tail=False) for e in tree if isinstance(e.tag, str)}
            states[slide] = (tree, originals, [e for e in tree if _local(e) in SHAPE_TAGS], names[slide - 1])
        tree, _, order, slide_part = states[slide]
        change, _, edited_media = _apply_edit(tree, order, edit, parts=parts, slide_part=slide_part)
        media_changes.update(edited_media)
        changes.append(change)
    patched = dict(parts)
    for slide, (tree, originals, order, _) in states.items():
        name = names[slide - 1]
        patched[name] = _patch_slide(parts[name], tree, originals, order)
    patched.update(media_changes)
    changed_parts = [{"part": name, "before_sha256": _sha(parts[name]), "after_sha256": _sha(patched[name])}
                     for name in sorted(parts) if parts[name] != patched[name]]
    if not changed_parts:
        raise ValueError("the explicit operations make no change to the source")
    plan = {"schema_version": 1, "source_sha256": source_hash, "operations": changes,
            "changed_parts": changed_parts}
    plan_hash = _sha(json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode())
    if "expected_plan_sha256" in request and request["expected_plan_sha256"] != plan_hash:
        raise ValueError("expected_plan_sha256 does not match the current resolved plan")
    selected_slides = sorted(states)
    before = _inspect(parts, source_hash, selected_slides)
    after = _inspect(patched, source_hash, selected_slides)
    after["state"] = "planned_candidate"
    receipt = {"schema_version": 1, "status": "planned" if dry_run else "candidate",
               "source": str(source), "source_sha256": source_hash, "plan_sha256": plan_hash,
               "changed_parts": changed_parts, "operations": changes,
               "non_target_parts_byte_preserved": True, "unselected_object_xml_byte_preserved": True,
               "native_visual_review_required": True, "native_visual_verified": False,
               "candidate": None, "candidate_sha256": None}
    # Reserve the new directory exclusively. All request errors have been checked
    # before this point, and the PPTX itself is published by a single rename.
    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir()
    try:
        staged = output / ".candidate.pptx.tmp"
        if not dry_run:
            _write_candidate(data, staged, patched)
            candidate_data = _verify_candidate(staged, patched)
            after["source_sha256"] = _sha(candidate_data)
            after["state"] = "candidate"
            receipt["candidate"] = str(output / "candidate.pptx")
            receipt["candidate_sha256"] = _sha(candidate_data)
        _write_json(output / "before.json", before)
        _write_json(output / "after.json", after)
        _write_json(output / "plan.json", plan | {"plan_sha256": plan_hash})
        _write_json(output / "receipt.json", receipt)
        if _sha(source.read_bytes()) != source_hash:
            raise ValueError("source changed while preparing the candidate; inspect and retry")
        if not dry_run:
            os.replace(staged, output / "candidate.pptx")
        return receipt
    except BaseException:
        shutil.rmtree(output)
        raise
