"""Executable whole-template adaptation with one or two reviewed layout swaps.

The primary deck supplies every output page. A page override imports a source
page and its static OOXML dependency graph; content edits are then applied to
the assembled editable PPTX by exact shape/media hashes. All outputs are
candidates until their actual pages and source facts have been reviewed.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any

from .library_read_guard import require_library_available
from zipfile import ZipFile

from .motion_clone import _read_xml, _safe_entries, _slide_objects, _slide_order
from .style_search import CAREER, CREATIVE, _curation, _rows, _safe_source
from .style_page_match import retrieve_page_layout_candidates, _page_score, _profession_domain
from .gaojie_layouts import GAOJIE, style_analysis_root


class StyleHybridWorkflowError(ValueError):
    """A plan or source cannot support a source-bound hybrid candidate."""


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _family(relative: str) -> str:
    if isinstance(relative, str) and relative.startswith(CREATIVE + "/"):
        return CREATIVE
    if isinstance(relative, str) and relative.startswith(CAREER + "/"):
        return CAREER
    if isinstance(relative, str) and relative.startswith(GAOJIE + "/"):
        return GAOJIE
    raise StyleHybridWorkflowError("source must belong to an indexed contest or Gaojie collection")


def _source(library: Path, relative: str, expected_sha: str, *, require_template: bool) -> dict[str, Any]:
    require_library_available(library, StyleHybridWorkflowError)
    family = _family(relative)
    source = _safe_source(library, relative)
    if source is None or source.suffix.lower() != ".pptx":
        raise StyleHybridWorkflowError("indexed editable PPTX source is missing")
    analysis = style_analysis_root(library, family)
    catalog_path = analysis / "STYLE-CATALOG.jsonl"
    row = next((item for item in _rows(catalog_path) if item.get("path") == relative), None)
    if row is None or row.get("status") != "parsed" or row.get("source_format") != "pptx":
        raise StyleHybridWorkflowError("source has no parsed whole-deck style index")
    if (not isinstance(expected_sha, str) or expected_sha != row.get("source_sha256")
            or _sha(source) != expected_sha):
        raise StyleHybridWorkflowError("source PPTX has changed since selection")
    meta = _curation(library / CREATIVE, library / CAREER).get(relative, {})
    if meta.get("curation_stale") or meta.get("source_sha256") != expected_sha:
        raise StyleHybridWorkflowError("source classification is stale for these bytes")
    if require_template and meta.get("asset_type") not in {"template", "template_with_example_content"}:
        raise StyleHybridWorkflowError("source is a reference, case or guide rather than a reusable template")
    if meta.get("off_topic") or meta.get("flattened_page_structure"):
        raise StyleHybridWorkflowError("source is off topic or made of flattened pages")
    pages = [item for item in _rows(analysis / "STYLE-PAGES.jsonl")
             if item.get("source_sha256") == expected_sha]
    if len(pages) != row.get("slide_count"):
        raise StyleHybridWorkflowError("page index does not cover the complete selected deck")
    return {"path": relative, "source": source, "sha256": expected_sha,
            "family": family, "catalog": row, "meta": meta,
            "pages": {int(item["slide"]): item for item in pages}}


def _reviewed_preview(review: dict[str, Any], *, source_sha: str, slide: int) -> dict[str, Any]:
    if not isinstance(review, dict) or review.get("slide") != slide:
        raise StyleHybridWorkflowError("a source-page review must identify the exact page")
    finding = review.get("finding")
    if not isinstance(finding, str) or len(finding.strip()) < 8:
        raise StyleHybridWorkflowError("a source-page review needs a concrete visual finding")
    raw_receipt = review.get("preview_receipt")
    if not isinstance(raw_receipt, str) or not raw_receipt:
        raise StyleHybridWorkflowError("a source-page review needs a preview receipt")
    receipt_path = Path(raw_receipt).expanduser().resolve()
    if not receipt_path.is_file():
        raise StyleHybridWorkflowError("preview receipt is missing")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StyleHybridWorkflowError("preview receipt is invalid") from exc
    png = Path(str(receipt.get("output_png") or ""))
    if (receipt.get("status") != "rendered_advisory"
            or receipt.get("source_sha256") != source_sha
            or receipt.get("slide_number") != slide
            or not png.is_absolute() or not png.is_file()
            or _sha(png) != receipt.get("output_sha256")):
        raise StyleHybridWorkflowError("preview receipt does not bind this source page and PNG")
    return {"slide": slide, "finding": finding.strip(), "preview_receipt": str(receipt_path),
            "png_sha256": receipt["output_sha256"], "renderer": receipt.get("renderer"),
            "approximate": receipt.get("approximate_renderer") is True}


_PLACEHOLDER = re.compile(r"单击此处|点击添加|请在这里|这里输入|在此录入|此处录入|请输入|请填写|请替换|待替换|代用名|所属部门|公司职位|(?:X{3,}|x{3,})|Lorem ipsum", re.I)


def _content_audit(pptx: Path) -> dict[str, Any]:
    """Locate visible slide-copy placeholders without treating them as facts."""
    from zipfile import ZipFile
    flagged = []
    with ZipFile(pptx) as archive:
        entries = _safe_entries(archive)
        _, parts = _slide_order(archive, entries)
        for number, part in enumerate(parts, 1):
            root = _read_xml(archive, entries, part)
            text = " ".join(str(value) for value in root.xpath(
                ".//a:t/text()", namespaces={"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}))
            matches = list(_PLACEHOLDER.finditer(text))
            if matches:
                flagged.append({"slide": number, "matches": len(matches),
                                "examples": [text[max(0, m.start()-15):m.end()+20] for m in matches[:4]]})
    return {"status": "review_needed" if flagged else "no_common_placeholder_found",
            "flagged_slide_count": len(flagged), "flagged_pages": flagged[:100],
            "scope": "visible slide text only; sample facts and images may remain even without placeholder tokens"}


def _unmapped_changed_page_content(pptx: Path, slides: set[int],
                                    text_edits: list[dict], image_edits: list[dict]) -> list[dict[str, Any]]:
    mapped_text = {(item.get("slide"), str(item.get("shape_id"))) for item in text_edits}
    mapped_images = {(item.get("slide"), str(item.get("shape_id"))) for item in image_edits}
    out = []
    with ZipFile(pptx) as archive:
        entries = _safe_entries(archive)
        _, parts = _slide_order(archive, entries)
        for number in sorted(slides):
            if number < 1 or number > len(parts):
                continue
            root = _read_xml(archive, entries, parts[number-1])
            texts, pictures = [], []
            for shape_id, shape in _slide_objects(root).items():
                kind = shape.tag.rsplit("}", 1)[-1]
                if kind == "grpSp":
                    continue  # child shapes are reported separately
                if kind == "pic" and (number, shape_id) not in mapped_images:
                    pictures.append(shape_id)
                content = "".join(shape.xpath(".//a:t/text()", namespaces={
                    "a": "http://schemas.openxmlformats.org/drawingml/2006/main"})).strip()
                if content and (number, shape_id) not in mapped_text:
                    texts.append({"shape_id": shape_id, "text_excerpt": content[:90]})
            out.append({"slide": number, "unmapped_text_object_count": len(texts),
                        "unmapped_picture_object_count": len(pictures),
                        "unmapped_text_examples": texts[:25], "unmapped_picture_shape_ids": pictures[:25]})
    return out


def _validate_image_intents(pptx: Path, edits: list[dict]) -> list[dict[str, Any]]:
    """Bind each requested image to its visible object's layer and size."""
    from .template_content_replace import inspect_page_images
    if not edits:
        return []
    with ZipFile(pptx) as archive:
        entries = _safe_entries(archive)
        pres = _read_xml(archive, entries, "ppt/presentation.xml")
        size = pres.find("{http://schemas.openxmlformats.org/presentationml/2006/main}sldSz")
        if size is None:
            raise StyleHybridWorkflowError("PPTX has no page size for image-target inspection")
        width, height = int(size.get("cx", "0")), int(size.get("cy", "0"))
        if width <= 0 or height <= 0:
            raise StyleHybridWorkflowError("PPTX has invalid page dimensions")
    allowed = {"portrait", "background", "illustration", "evidence", "logo", "texture"}
    cache: dict[int, dict] = {}
    resolved = []
    for item in edits:
        if not isinstance(item, dict) or type(item.get("slide")) is not int:
            raise StyleHybridWorkflowError("each image replacement needs an output slide number")
        purpose, role = item.get("purpose"), item.get("visual_role")
        if not isinstance(purpose, str) or len(purpose.strip()) < 8 or role not in allowed:
            raise StyleHybridWorkflowError("each image replacement needs a concrete purpose and visual_role")
        slide = item["slide"]
        if slide not in cache:
            cache[slide] = inspect_page_images(pptx, slide)
        matches = [obj for obj in cache[slide]["objects"] if obj.get("layer") == "slide"
                   and str(obj.get("shape_id")) == str(item.get("shape_id"))]
        if len(matches) != 1 or not matches[0].get("safe_for_single_image_replacement"):
            raise StyleHybridWorkflowError("image target is absent, inherited or has ambiguous media bindings")
        obj = matches[0]
        media = obj["media"][0]
        if media["media_sha256"] != item.get("original_media_sha256"):
            raise StyleHybridWorkflowError("image target media SHA does not match the reviewed object")
        geom = obj.get("geometry_emu") or {}
        area_ratio = ((geom.get("cx", 0) * geom.get("cy", 0)) / (width * height)
                      if geom.get("cx") and geom.get("cy") else None)
        if role == "portrait" and (area_ratio is None or area_ratio > .35):
            raise StyleHybridWorkflowError("portrait target covers too much of the page; inspect background versus face layer")
        if role == "background" and (area_ratio is None or area_ratio < .55):
            raise StyleHybridWorkflowError("background target is too small; inspect its visible layer")
        resolved.append({"slide": slide, "shape_id": str(item["shape_id"]),
                         "purpose": purpose.strip(), "visual_role": role,
                         "layer": obj["layer"], "visual_object_type": obj["visual_object_type"],
                         "geometry_emu": geom, "page_area_ratio": round(area_ratio, 5) if area_ratio is not None else None,
                         "original_media_part": media["media_part"],
                         "original_media_sha256": media["media_sha256"]})
    return resolved


def _reference_layout_inventory(library: Path, donor: dict[str, Any], donor_slide: int,
                                target_slide: int, request: dict[str, Any],
                                text_edits: list[dict], image_edits: list[dict]) -> dict[str, Any]:
    """Require a case/reference page's old facts and media to be accounted for."""
    detail_ref = donor["catalog"].get("detail_ref")
    if not isinstance(detail_ref, str):
        raise StyleHybridWorkflowError("reference page lacks a complete source detail record")
    detail = (library / detail_ref).resolve()
    if (not detail.is_relative_to(library) or not detail.is_file()
            or _sha(detail) != donor["catalog"].get("detail_sha256")):
        raise StyleHybridWorkflowError("reference page structural detail is missing or stale")
    packet = json.loads(detail.read_text(encoding="utf-8"))
    if packet.get("source_sha256") != donor["sha256"]:
        raise StyleHybridWorkflowError("reference detail is bound to different source bytes")
    pages = packet.get("slides") or []
    if donor_slide > len(pages) or pages[donor_slide-1].get("status") != "parsed":
        raise StyleHybridWorkflowError("reference page is not completely parsed")
    page = pages[donor_slide-1]
    objects = page.get("objects") or []
    if any((obj.get("graphic") or {}).get("kind") in {"chart", "table", "smartart"} for obj in objects):
        raise StyleHybridWorkflowError("reference page contains chart/table/diagram facts needing a separate rebuild")
    # Cross-check the actual package. Chart series and table cells can live
    # outside the text/image fields of an index record.
    with ZipFile(donor["source"]) as archive:
        entries = _safe_entries(archive)
        _, source_parts = _slide_order(archive, entries)
        source_part = source_parts[donor_slide-1]
        source_root = _read_xml(archive, entries, source_part)
        forbidden_nodes = {"graphicFrame", "graphicData", "chart", "tbl", "oleObj", "contentPart"}
        if any(node.tag.rsplit("}", 1)[-1] in forbidden_nodes for node in source_root.iter() if isinstance(node.tag, str)):
            raise StyleHybridWorkflowError("reference page has native chart/table/diagram or embedded facts; rebuild that content first")
        rel_part = source_part.rsplit("/", 1)[0] + "/_rels/" + source_part.rsplit("/", 1)[-1] + ".rels"
        if rel_part in entries:
            rels = _read_xml(archive, entries, rel_part)
            if any((rel.get("Type") or "").rsplit("/", 1)[-1] in
                   {"chart", "diagramData", "diagramLayout", "diagramColors", "diagramQuickStyle", "oleObject", "package"}
                   for rel in rels):
                raise StyleHybridWorkflowError("reference page has linked chart/diagram data requiring a separate rebuild")
    text_ids = {str(obj["id"]) for obj in objects if obj.get("id") and str(obj.get("text") or "").strip()}
    image_ids = {str(obj["id"]) for obj in objects if obj.get("id") and obj.get("image_fills")}
    from .template_content_replace import _object_blips
    actual_text_ids, actual_image_ids = set(), set()
    for shape_id, shape in _slide_objects(source_root).items():
        if shape.tag.rsplit("}", 1)[-1] == "grpSp":
            continue
        if "".join(shape.xpath(".//a:t/text()", namespaces={
                "a": "http://schemas.openxmlformats.org/drawingml/2006/main"})).strip():
            actual_text_ids.add(shape_id)
        if _object_blips(shape):
            actual_image_ids.add(shape_id)
    if actual_text_ids != text_ids or actual_image_ids != image_ids:
        raise StyleHybridWorkflowError("reference detail omits or misidentifies source text/image objects")
    mapped_text = {str(item.get("shape_id")) for item in text_edits if item.get("slide") == target_slide}
    mapped_images = {str(item.get("shape_id")) for item in image_edits if item.get("slide") == target_slide}
    retained_text = request.get("retain_source_text") or []
    retained_images = request.get("retain_source_images") or []
    if not isinstance(retained_text, list) or not isinstance(retained_images, list):
        raise StyleHybridWorkflowError("reference keep-dispositions must be lists")
    for item in retained_text + retained_images:
        if (not isinstance(item, dict) or not str(item.get("shape_id") or "")
                or not isinstance(item.get("reason"), str) or len(item["reason"].strip()) < 8):
            raise StyleHybridWorkflowError("each retained source object needs an exact shape ID and reason")
    for item in retained_images:
        if (not isinstance(item.get("rights_handling_note"), str)
                or len(item["rights_handling_note"].strip()) < 10):
            raise StyleHybridWorkflowError("retaining a source case image requires its rights disposition")
    keep_text = {str(item["shape_id"]) for item in retained_text}
    keep_images = {str(item["shape_id"]) for item in retained_images}
    if keep_text - text_ids or keep_images - image_ids:
        raise StyleHybridWorkflowError("retained source object IDs do not exist on the selected page")
    if mapped_text & keep_text or mapped_images & keep_images:
        raise StyleHybridWorkflowError("one reference object cannot be both replaced and retained")
    missing_text = sorted(text_ids - mapped_text - keep_text)
    missing_images = sorted(image_ids - mapped_images - keep_images)
    if missing_text or missing_images:
        raise StyleHybridWorkflowError(
            "layout-only reference needs a disposition for every source text/image object: "
            f"unmapped text IDs={missing_text[:20]}, image IDs={missing_images[:20]}")
    context = page.get("style_context") or {}
    layers = packet.get("shared_layers") or {}
    reviewed_layers = {item.get("part"): item for item in request.get("shared_layer_reviews") or []
                       if isinstance(item, dict)}
    inherited = []
    for key in ("slide_layout_part", "slide_master_part"):
        part = context.get(key)
        layer = layers.get(part) if part else None
        if not layer:
            continue
        inherited_objects = [obj for obj in layer.get("objects", [])
                             if str(obj.get("text") or "").strip() or obj.get("image_fills")]
        if not inherited_objects:
            continue
        generic_master_line = re.compile(
            r"^(?:单击此处编辑母版(?:标题|文本)样式|[一二三四五六七八九十]级|Click to edit Master.*|(?:Second|Third|Fourth|Fifth) level)$",
            re.I,
        )
        for obj in inherited_objects:
            if obj.get("image_fills"):
                raise StyleHybridWorkflowError("reference source has inherited media; rebuild the layout instead of copying old imagery")
            lines = [line.strip() for line in str(obj.get("text") or "").splitlines() if line.strip()]
            placeholder = obj.get("placeholder_type")
            def generic_line(line: str) -> bool:
                if generic_master_line.fullmatch(line):
                    return True
                if placeholder == "dt" and line in {"1/27/13", "Date"}:
                    return True
                if placeholder == "sldNum" and line in {"‹#›", "<#>"}:
                    return True
                if placeholder == "ftr" and line == "Footer":
                    return True
                return False
            if any(not generic_line(line) for line in lines):
                raise StyleHybridWorkflowError("reference source has inherited case text; rebuild the layout before copying")
        review = reviewed_layers.get(part)
        if (not isinstance(review, dict) or not isinstance(review.get("finding"), str)
                or len(review["finding"].strip()) < 10
                or not isinstance(review.get("rights_handling_note"), str)
                or len(review["rights_handling_note"].strip()) < 10):
            raise StyleHybridWorkflowError("reference source has inherited text/images; inspect each layer and record its disposition")
        inherited.append({"part": part, "object_count": len(inherited_objects),
                          "review_finding": review["finding"].strip(),
                          "rights_handling_note": review["rights_handling_note"].strip()})
    return {"mode": "layout_only_reference", "source_text_objects": len(text_ids),
            "source_image_objects": len(image_ids), "mapped_text_objects": len(mapped_text),
            "mapped_image_objects": len(mapped_images), "retained_text_objects": len(keep_text),
            "retained_image_objects": len(keep_images), "reviewed_inherited_layers": inherited,
            "source_text_shape_ids": sorted(text_ids), "source_image_shape_ids": sorted(image_ids),
            "retained_source_text": {str(obj["id"]): str(obj.get("text") or "").replace("\n", "")
                                     for obj in objects if str(obj.get("id")) in keep_text},
            "retained_source_image_ids": sorted(keep_images),
            "source_facts_status": "all_slide_text_and_image_objects_dispositioned_structurally",
            "inherited_source_content_status": "generic_master_placeholders_only_no_inherited_media",
            "limitation": "object mapping does not verify the semantic truth of new text or images"}


def _reference_output_readback(pptx: Path, sources: list[dict], text_edits: list[dict],
                               image_edits: list[dict]) -> list[dict[str, Any]]:
    from .template_content_replace import inspect_page_images
    reviews = []
    with ZipFile(pptx) as archive:
        entries = _safe_entries(archive)
        _, slide_parts = _slide_order(archive, entries)
        for source in sources:
            coverage = source.get("reference_content_coverage")
            if not coverage:
                continue
            number = source["target_slide"]
            root = _read_xml(archive, entries, slide_parts[number-1])
            if any(node.tag.rsplit("}", 1)[-1] in
                   {"graphicFrame", "graphicData", "chart", "tbl", "oleObj", "contentPart"}
                   for node in root.iter() if isinstance(node.tag, str)):
                raise StyleHybridWorkflowError("reference output contains unresolved chart/table/diagram data")
            slide_part = slide_parts[number-1]
            rel_part = slide_part.rsplit("/", 1)[0] + "/_rels/" + slide_part.rsplit("/", 1)[-1] + ".rels"
            if rel_part in entries:
                rels = _read_xml(archive, entries, rel_part)
                if any((rel.get("Type") or "").rsplit("/", 1)[-1] in
                       {"chart", "diagramData", "diagramLayout", "diagramColors", "diagramQuickStyle", "oleObject", "package"}
                       for rel in rels):
                    raise StyleHybridWorkflowError("reference output retained a chart/diagram relationship")
            objects = _slide_objects(root)
            verified_text = []
            for edit in (item for item in text_edits if item.get("slide") == number):
                shape_id = str(edit.get("shape_id"))
                shape = objects.get(shape_id)
                expected = edit.get("new_text", edit.get("text"))
                actual = "".join(shape.xpath(".//a:t/text()", namespaces={
                    "a": "http://schemas.openxmlformats.org/drawingml/2006/main"})) if shape is not None else None
                if actual != expected:
                    raise StyleHybridWorkflowError(f"reference output text replacement did not bind shape {shape_id}")
                verified_text.append(shape_id)
            for shape_id, original in coverage.get("retained_source_text", {}).items():
                shape = objects.get(shape_id)
                actual = "".join(shape.xpath(".//a:t/text()", namespaces={
                    "a": "http://schemas.openxmlformats.org/drawingml/2006/main"})) if shape is not None else None
                if actual != original:
                    raise StyleHybridWorkflowError(f"reference output retained text changed unexpectedly at shape {shape_id}")
            mapped_image_ids = []
            if any(item.get("slide") == number for item in image_edits):
                inventory = inspect_page_images(pptx, number)
                for edit in (item for item in image_edits if item.get("slide") == number):
                    shape_id = str(edit.get("shape_id"))
                    matches = [obj for obj in inventory["objects"] if obj.get("layer") == "slide"
                               and str(obj.get("shape_id")) == shape_id]
                    image_path = edit.get("image_path", edit.get("path"))
                    if len(matches) != 1 or len(matches[0].get("media") or []) != 1 or not image_path:
                        raise StyleHybridWorkflowError("reference output image binding is absent or ambiguous")
                    if matches[0]["media"][0]["media_sha256"] != _sha(Path(image_path)):
                        raise StyleHybridWorkflowError(f"reference output image replacement did not bind shape {shape_id}")
                    mapped_image_ids.append(shape_id)
            if set(verified_text) | set(coverage.get("retained_source_text", {})) != set(coverage["source_text_shape_ids"]):
                raise StyleHybridWorkflowError("reference output text mapping does not cover every source object")
            if set(mapped_image_ids) | set(coverage.get("retained_source_image_ids", [])) != set(coverage["source_image_shape_ids"]):
                raise StyleHybridWorkflowError("reference output image mapping does not cover every source object")
            reviews.append({"target_slide": number, "verified_text_shape_ids": verified_text,
                            "verified_image_shape_ids": mapped_image_ids,
                            "retained_text_shape_ids": sorted(coverage.get("retained_source_text", {})),
                            "retained_image_shape_ids": coverage.get("retained_source_image_ids", []),
                            "chart_table_diagram_absent": True})
    return reviews


def plan_hybrid_adaptation(library: Path, request: dict[str, Any], *, limit: int = 5) -> dict[str, Any]:
    """Propose primary whole decks and layout-only substitutes for scoped pages."""
    from .style_search import search_style_library

    if not isinstance(request, dict) or not isinstance(request.get("project"), dict):
        raise StyleHybridWorkflowError("project context is required")
    library = Path(library).resolve()
    require_library_available(library, StyleHybridWorkflowError)
    project = request["project"]
    query = " ".join(str(project.get(key) or "") for key in
                     ("occasion", "competition", "track", "profession", "topic", "style"))
    planned_pages = request.get("planned_pages") or []
    if not isinstance(planned_pages, list) or len(planned_pages) > 80:
        raise StyleHybridWorkflowError("planned_pages must be a list of at most 80 current page messages")
    if any(not isinstance(item, dict) for item in planned_pages):
        raise StyleHybridWorkflowError("every planned page must be an object")
    desired_count = project.get("desired_slide_count")
    page_range = project.get("page_range")
    if desired_count is not None:
        if type(desired_count) is not int or not 1 <= desired_count <= 100:
            raise StyleHybridWorkflowError("desired_slide_count must be between 1 and 100")
        query += f" 约{desired_count}页"
    elif page_range is not None:
        if (not isinstance(page_range, list) or len(page_range) != 2
                or any(type(number) is not int for number in page_range)
                or not 1 <= page_range[0] <= page_range[1] <= 100):
            raise StyleHybridWorkflowError("page_range must be [minimum, maximum] within 1..100")
        query += f" 约{round(sum(page_range) / 2)}页"
    query += " " + " ".join(str(item.get("message") or item.get("title") or "") for item in planned_pages)
    if not query.strip():
        raise StyleHybridWorkflowError("project needs a concrete occasion or topic")
    desired_pages = request.get("pages_to_relayout") or []
    if not isinstance(desired_pages, list) or len(desired_pages) > 2:
        raise StyleHybridWorkflowError("at most two pages can be requested for local re-layout")
    primary_path = request.get("primary_source_path")
    primary = None
    if primary_path:
        primary = _source(library, primary_path, request.get("primary_source_sha256"), require_template=True)
    collection = ("creative" if primary and primary["family"] == CREATIVE else
                  "career" if primary and primary["family"] == CAREER else
                  "gaojie" if primary and primary["family"] == GAOJIE else
                  project.get("collection", "auto"))
    roles = [str(item["role"]) for item in planned_pages if item.get("role")]
    search = search_style_library(library, query, collection=collection, roles=roles or None, limit=limit)
    suggestions = []
    if primary:
        for target in desired_pages:
            if (not isinstance(target, dict) or type(target.get("target_slide")) is not int
                    or target["target_slide"] not in primary["pages"]):
                raise StyleHybridWorkflowError("a re-layout target must identify a primary page")
            if not target.get("message") or not target.get("role"):
                raise StyleHybridWorkflowError("a re-layout target needs its message and function")
            match = retrieve_page_layout_candidates(library, {
                "current_project": {**project, "collection": project.get("donor_collection")},
                "current_template_source_sha256": primary["sha256"],
                "target_page": target,
            }, limit=limit)
            suggestions.append({"target_slide": target["target_slide"],
                                "primary_page": primary["pages"][target["target_slide"]],
                                "layout_candidates": match["candidates"],
                                "layout_reference_candidates": match.get("reference_candidates", []),
                                "search_status": match["status"]})
    return {"schema": "aippt.style-hybrid-plan.v1", "status": "review_source_pages",
            "query": query, "primary_source_path": primary_path,
            "primary_source_sha256": primary["sha256"] if primary else None,
            "primary_slide_count": primary["catalog"]["slide_count"] if primary else None,
            "primary_page_count_fit": ("within_range" if page_range and page_range[0] <= primary["catalog"]["slide_count"] <= page_range[1]
                                       else "outside_range" if page_range and primary
                                       else "exact" if desired_count is not None and primary and primary["catalog"]["slide_count"] == desired_count
                                       else "different_count" if desired_count is not None and primary
                                       else "not_specified") if primary else None,
            "planned_page_roles": roles,
            "whole_deck_candidates": search.get("candidates", []),
            "reference_candidates": search.get("reference_candidates", []),
            "pages_to_relayout": suggestions,
            "next_action": "Inspect real source pages, map every retained/replaced fact and picture, then build one editable candidate.",
            "visual_status": "machine_index_and_approximate_previews_only"}


def build_hybrid_candidate(library: Path, request: dict[str, Any], output: Path) -> dict[str, Any]:
    """Copy the whole selected deck, swap up to two pages, then edit mapped content."""
    from .style_hybrid_ooxml import UnsafeSlideImport, replace_slides
    from .template_content_replace import TemplateContentReplaceError, replace_content
    from .pptx_edit import patch_pptx

    if not isinstance(request, dict):
        raise StyleHybridWorkflowError("hybrid build request must be an object")
    library = Path(library).resolve()
    require_library_available(library, StyleHybridWorkflowError)
    output = Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink() or output.resolve().is_relative_to(library):
        raise StyleHybridWorkflowError("output must be a new editable PPTX outside the library")
    primary = _source(library, request.get("primary_source_path"),
                      request.get("primary_source_sha256"), require_template=True)
    project = request.get("project") or {}
    if not isinstance(project, dict):
        raise StyleHybridWorkflowError("project must be an object when provided")
    count = primary["catalog"]["slide_count"]
    desired = project.get("desired_slide_count")
    page_range = project.get("page_range")
    if desired is not None and (type(desired) is not int or not 1 <= desired <= 100):
        raise StyleHybridWorkflowError("desired_slide_count must be between 1 and 100")
    if page_range is not None and (not isinstance(page_range, list) or len(page_range) != 2
                                   or any(type(value) is not int for value in page_range)
                                   or not 1 <= page_range[0] <= page_range[1] <= 100):
        raise StyleHybridWorkflowError("page_range must be [minimum, maximum] within 1..100")
    count_fit = ("exact" if desired is not None and count == desired else
                 "different_count" if desired is not None else
                 "within_range" if page_range and page_range[0] <= count <= page_range[1] else
                 "outside_requested_range" if page_range else "not_specified")
    if count_fit in {"different_count", "outside_requested_range"}:
        note = request.get("page_count_disposition")
        if not isinstance(note, str) or len(note.strip()) < 12:
            raise StyleHybridWorkflowError("selected whole template differs from requested page count; record its adaptation plan")
    reviewed = request.get("reviewed_primary_pages")
    if not isinstance(reviewed, list) or len({item.get("slide") for item in reviewed if isinstance(item, dict)}) < 2:
        raise StyleHybridWorkflowError("inspect at least two distinct primary pages, including a content page")
    primary_reviews = [_reviewed_preview(item, source_sha=primary["sha256"], slide=item.get("slide"))
                       for item in reviewed]
    if 1 not in {item["slide"] for item in primary_reviews}:
        raise StyleHybridWorkflowError("primary review must include the cover")
    if not any(item["slide"] > 1 and (
               any(role.get("role") not in {"cover", "agenda"}
                   for role in primary["pages"][item["slide"]].get("role_evidence", []))
               or (primary["pages"][item["slide"]].get("text_characters", 0) >= 60
                   and "目录" not in str(primary["pages"][item["slide"]].get("title") or "")))
               for item in primary_reviews):
        raise StyleHybridWorkflowError("primary review must include a substantive content page beyond cover/agenda")
    for field in ("selection_note", "source_content_replacement_plan", "rights_handling_note"):
        if not isinstance(request.get(field), str) or len(request[field].strip()) < 10:
            raise StyleHybridWorkflowError(f"{field} must explain the specific adaptation")
    swaps = request.get("page_overrides") or []
    if not isinstance(swaps, list) or len(swaps) > 2:
        raise StyleHybridWorkflowError("page_overrides must contain at most two local changes")
    texts = request.get("text_replacements") or []
    images = request.get("image_replacements") or []
    geometry_edits = request.get("geometry_edits") or []
    if not isinstance(texts, list) or not isinstance(images, list) or not isinstance(geometry_edits, list):
        raise StyleHybridWorkflowError("text, image and geometry replacements must be lists")
    if len(geometry_edits) > 100:
        raise StyleHybridWorkflowError("at most 100 explicit geometry adjustments are supported")
    if "dynamic_required" in request and type(request["dynamic_required"]) is not bool:
        raise StyleHybridWorkflowError("dynamic_required must be a boolean")
    dynamic_required = request.get("dynamic_required") is True
    if dynamic_required and swaps:
        raise StyleHybridWorkflowError("cross-source page replacement is currently static-only; keep the dynamic source sequence")
    graph_overrides = []
    sources = []
    target_seen = set()
    for swap in swaps:
        if not isinstance(swap, dict) or type(swap.get("target_slide")) is not int:
            raise StyleHybridWorkflowError("each override needs a numbered target slide")
        target_number = swap["target_slide"]
        if target_number not in primary["pages"] or target_number in target_seen:
            raise StyleHybridWorkflowError("override target is repeated or outside the primary deck")
        target_seen.add(target_number)
        layout_only_reference = swap.get("layout_only_reference") is True
        if "layout_only_reference" in swap and type(swap["layout_only_reference"]) is not bool:
            raise StyleHybridWorkflowError("layout_only_reference must be a boolean")
        donor = _source(library, swap.get("donor_source_path"),
                        swap.get("donor_source_sha256"), require_template=not layout_only_reference)
        if layout_only_reference and donor["meta"].get("asset_type") not in {"portfolio", "reference"}:
            raise StyleHybridWorkflowError("layout_only_reference must name a curated case or reference source")
        donor_number = swap.get("donor_slide")
        if type(donor_number) is not int or donor_number not in donor["pages"]:
            raise StyleHybridWorkflowError("donor slide is missing from its complete page index")
        if not isinstance(swap.get("selection_note"), str) or len(swap["selection_note"].strip()) < 10:
            raise StyleHybridWorkflowError("local re-layout needs a concrete selection note")
        donor_review = _reviewed_preview(swap.get("reviewed_donor_page"),
                                         source_sha=donor["sha256"], slide=donor_number)
        target = swap.get("target_page") or {}
        if not isinstance(target, dict) or not target.get("message") or not target.get("role"):
            raise StyleHybridWorkflowError("local re-layout needs the current target page message and role")
        score, fit = _page_score(target, donor["pages"][donor_number], str(target["role"]))
        if not fit["exact_role_match"] and fit["content_coverage"] < .12:
            raise StyleHybridWorkflowError("donor page does not match the target's content function")
        if layout_only_reference:
            if not fit["exact_role_match"] or fit["content_coverage"] < .15:
                raise StyleHybridWorkflowError("case layout needs the same page role and concrete content overlap")
            requested_domain = _profession_domain(str(project.get("profession") or ""))[0]
            donor_context = " ".join((donor["path"], str(donor["pages"][donor_number].get("title") or ""),
                                      str(donor["pages"][donor_number].get("text_excerpt") or "")))
            donor_domains = {cue.split(":", 1)[0] for cue in _profession_domain(donor_context)[1]}
            if requested_domain and requested_domain not in donor_domains:
                raise StyleHybridWorkflowError("case layout belongs to a different explicit profession")
            if not requested_domain and fit["content_coverage"] < .25:
                raise StyleHybridWorkflowError("case layout without a professional anchor needs stronger content evidence")
        reference_coverage = (_reference_layout_inventory(
            library, donor, donor_number, target_number, swap,
            texts, images)
            if layout_only_reference else None)
        if swap.get("staticize_donor_timing") is True:
            note = swap.get("static_timing_disposition")
            if not isinstance(note, str) or len(note.strip()) < 12:
                raise StyleHybridWorkflowError("staticizing source timing requires a specific disposition note")
        if swap.get("omit_donor_notes") is True:
            note = swap.get("donor_notes_disposition")
            if not isinstance(note, str) or len(note.strip()) < 12:
                raise StyleHybridWorkflowError("omitting donor notes requires a specific current-talk-track disposition")
        for field in ("staticize_donor_timing", "allow_minor_size_delta", "omit_donor_notes"):
            if field in swap and type(swap[field]) is not bool:
                raise StyleHybridWorkflowError(f"{field} must be a boolean")
        if swap.get("allow_minor_size_delta") is True:
            note = swap.get("minor_size_disposition")
            if not isinstance(note, str) or len(note.strip()) < 12:
                raise StyleHybridWorkflowError("minor page-size tolerance requires an edge-fit review note")
        accent_overrides = swap.get("theme_accent_overrides") or {}
        rgb_overrides = swap.get("direct_rgb_overrides") or {}
        if not isinstance(accent_overrides, dict) or not isinstance(rgb_overrides, dict):
            raise StyleHybridWorkflowError("theme and direct RGB overrides must be objects")
        if accent_overrides or rgb_overrides:
            note = swap.get("visual_coherence_note")
            if not isinstance(note, str) or len(note.strip()) < 12:
                raise StyleHybridWorkflowError("color harmonization requires a concrete visual-coherence note")
        graph_overrides.append({"target_slide": target_number, "donor_pptx": donor["source"],
                                "donor_sha256": donor["sha256"], "donor_slide": donor_number,
                                "staticize_donor_timing": swap.get("staticize_donor_timing") is True,
                                "allow_minor_size_delta": swap.get("allow_minor_size_delta") is True,
                                "omit_donor_notes": swap.get("omit_donor_notes") is True,
                                "dynamic_required": dynamic_required,
                                "theme_accent_overrides": accent_overrides,
                                "direct_rgb_overrides": rgb_overrides})
        sources.append({"target_slide": target_number, "source_path": donor["path"],
                        "source_sha256": donor["sha256"], "source_slide": donor_number,
                        "source_page_review": donor_review, "page_fit_score": score,
                        "page_fit_evidence": fit, "selection_note": swap["selection_note"].strip(),
                        "reuse_mode": "layout_only_reference" if layout_only_reference else "source_template_page",
                        "reference_content_coverage": reference_coverage})
        if swap.get("staticize_donor_timing") is True:
            sources[-1]["static_timing_disposition"] = swap["static_timing_disposition"].strip()
        if swap.get("omit_donor_notes") is True:
            sources[-1]["donor_notes_disposition"] = swap["donor_notes_disposition"].strip()
        if swap.get("allow_minor_size_delta") is True:
            sources[-1]["minor_size_disposition"] = swap["minor_size_disposition"].strip()
        if accent_overrides or rgb_overrides:
            sources[-1]["visual_coherence_note"] = swap["visual_coherence_note"].strip()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aippt-hybrid-build-", dir=output.parent) as work:
        stage = Path(work) / "assembled.pptx"
        if graph_overrides:
            try:
                graph_receipt = replace_slides(primary["source"], stage,
                                               primary_sha256=primary["sha256"], overrides=graph_overrides)
            except UnsafeSlideImport as exc:
                raise StyleHybridWorkflowError(f"hybrid page import rejected: {exc}") from exc
        else:
            shutil.copyfile(primary["source"], stage)
            graph_receipt = {"status": "whole_source_copied", "output_sha256": _sha(stage),
                             "overrides": [], "native_review": "not_performed"}
        image_binding_evidence = _validate_image_intents(stage, images)
        if texts or images:
            changed = Path(work) / "content-edited.pptx"
            try:
                edit_receipt = replace_content(stage, changed, source_sha256=_sha(stage),
                                               text_replacements=texts, image_replacements=images)
            except TemplateContentReplaceError as exc:
                raise StyleHybridWorkflowError(f"mapped content replacement rejected: {exc}") from exc
            candidate = changed
        else:
            edit_receipt = {"text_replacements": 0, "image_replacements": 0,
                            "status": "content_replacement_pending"}
            candidate = stage
        if geometry_edits:
            try:
                geometry_receipt = patch_pptx({"source": str(candidate), "source_sha256": _sha(candidate),
                                               "edits": geometry_edits, "dry_run": False},
                                              Path(work) / "geometry-adjusted")
            except (OSError, ValueError) as exc:
                raise StyleHybridWorkflowError(f"explicit layout adjustment rejected: {exc}") from exc
            candidate = Path(str(geometry_receipt["candidate"]))
        else:
            geometry_receipt = {"status": "not_requested", "operations": []}
        with ZipFile(candidate) as archive:
            entries = _safe_entries(archive)
            _, parts = _slide_order(archive, entries)
            if len(parts) != primary["catalog"]["slide_count"]:
                raise StyleHybridWorkflowError("hybrid output changed the primary slide count")
        content_audit = _content_audit(candidate)
        reference_readback = _reference_output_readback(candidate, sources, texts, images)
        changed_slides = {int(item["slide"]) for item in texts + images if type(item.get("slide")) is int}
        changed_slides.update(source["target_slide"] for source in sources)
        changed_slides.update(int(item["slide"]) for item in geometry_edits if isinstance(item, dict)
                              and type(item.get("slide")) is int)
        unmapped_changed_content = _unmapped_changed_page_content(candidate, changed_slides, texts, images)
        if _sha(primary["source"]) != primary["sha256"]:
            raise StyleHybridWorkflowError("primary source changed during build")
        for item in sources:
            donor = library / item["source_path"]
            if _sha(donor) != item["source_sha256"]:
                raise StyleHybridWorkflowError("donor source changed during build")
        temp_publish = Path(work) / "publish.pptx"
        shutil.copyfile(candidate, temp_publish)
        try:
            os.link(temp_publish, output)
        except FileExistsError as exc:
            raise StyleHybridWorkflowError("output appeared during build; existing file was kept") from exc
    # The staging directory is gone. Keep its hash evidence but never publish
    # a receipt that points readers to a vanished temporary file.
    graph_receipt = dict(graph_receipt)
    graph_receipt.pop("output_pptx", None)
    graph_receipt["staging_file_removed"] = True
    edit_receipt = dict(edit_receipt)
    edit_receipt["output"] = str(output)
    edit_receipt["staging_file_removed"] = True
    geometry_receipt = dict(geometry_receipt)
    geometry_receipt.pop("candidate", None)
    geometry_receipt.pop("source", None)
    geometry_receipt["staging_file_removed"] = True
    origin = [{"target_slide": number, "source_path": primary["path"],
               "source_sha256": primary["sha256"], "source_slide": number}
              for number in range(1, primary["catalog"]["slide_count"] + 1)]
    for source in sources:
        origin[source["target_slide"] - 1] = {key: source[key] for key in
                                                ("target_slide", "source_path", "source_sha256", "source_slide")}
    visual_review_pages = sorted({page for number in changed_slides for page in (number-1, number, number+1)
                                  if 1 <= page <= len(origin)})
    return {"schema": "aippt.style-hybrid-build.v1", "status": "editable_candidate_content_review_pending",
            "output": str(output), "output_sha256": _sha(output), "slide_count": len(origin),
            "requested_page_count_fit": count_fit,
            "page_count_disposition": request.get("page_count_disposition") if count_fit in {"different_count", "outside_requested_range"} else None,
            "primary_source_path": primary["path"], "primary_source_sha256": primary["sha256"],
            "page_origin_map": origin, "reviewed_primary_pages": primary_reviews,
            "page_overrides": sources, "graph_receipt": graph_receipt,
            "content_replacement_receipt": edit_receipt,
            "image_binding_evidence": image_binding_evidence,
            "geometry_adjustment_receipt": geometry_receipt,
            "content_audit": content_audit, "unmapped_changed_page_content": unmapped_changed_content,
            "reference_output_readback": reference_readback,
            "visual_review_pages": visual_review_pages,
            "source_content_replacement_plan": request["source_content_replacement_plan"].strip(),
            "rights_handling_note": request["rights_handling_note"].strip(),
            "visual_status": "output_pages_not_reviewed",
            "limitations": ["source visuals and media rights require current-project review",
                            "new text may overflow and must be checked on rendered whole pages",
                            "native PowerPoint display and animation playback are not verified"]}


__all__ = ["StyleHybridWorkflowError", "plan_hybrid_adaptation", "build_hybrid_candidate"]
