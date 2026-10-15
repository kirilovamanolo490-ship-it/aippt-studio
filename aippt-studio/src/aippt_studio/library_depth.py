"""Incremental, explicitly unreviewed OOXML index for the local template library.

This module records structural evidence only. It does not render slides, infer
image meaning, grant media reuse, or turn a machine heuristic into visual QA.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import xml.etree.ElementTree as ET
from zipfile import BadZipFile, ZipFile


SCHEMA = "aippt.library-depth.v1"
SCHEMA_REVISION = 2
PARSER_VERSION = "library-depth-2"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
ASVG = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
NS = {"p": P, "a": A, "r": R, "asvg": ASVG}
_XML_PART_LIMIT = 24_000_000
_EXPANDED_LIMIT = 200_000_000
_MEDIA_HASH_LIMIT = 4_000_000
_TEXT_LIMIT = 1800
_MEDIA_TAGS = {"pic", "graphicFrame", "sp"}
_ROLE_RULES = (
    ("cover", ("封面", "参赛项目名称")),
    ("agenda", ("目录", "议程", "汇报提纲", "内容框架")),
    ("background_problem", ("背景", "现状", "问题", "痛点", "需求")),
    ("goal_positioning", ("目标", "定位", "愿景", "使命")),
    ("method_approach", ("方法", "路径", "方案", "策略", "技术路线")),
    ("process_architecture", ("流程", "机制", "架构", "体系", "协同")),
    ("evidence_results", ("成果", "成效", "结果", "数据", "验证", "评估")),
    ("team_organization", ("团队", "成员", "组织", "分工", "导师")),
    ("budget_model", ("预算", "经费", "财务", "商业模式", "收入")),
    ("timeline_plan", ("计划", "进度", "时间安排", "实施", "里程碑")),
    ("case_example", ("案例", "实践", "试点", "样本", "用户故事")),
    ("closing_outlook", ("总结", "展望", "下一步", "感谢")),
)
_TERM_STOP = {"项目", "方案", "服务", "工作", "我们", "进行", "通过", "以及", "相关", "一个", "主要", "内容", "介绍", "目录", "谢谢", "感谢", "汇报结束", "欢迎指导"}
_GENERIC_TITLES = {"目录", "议程", "contents", "agenda", "谢谢", "感谢", "感谢观看", "汇报结束", "欢迎指导"}


class UnsupportedDeck(ValueError):
    """PPTX uses malformed, oversized, or unsupported package structure."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _zip_part_hash(archive: ZipFile, part: str | None) -> tuple[str | None, int | None, str]:
    if not part:
        return None, None, "no_internal_part"
    try:
        info = archive.getinfo(part)
    except KeyError:
        return None, None, "part_missing"
    if info.file_size > _MEDIA_HASH_LIMIT:
        return None, info.file_size, "over_hash_limit"
    digest = hashlib.sha256()
    with archive.open(info) as stream:
        for block in iter(lambda: stream.read(256 * 1024), b""):
            digest.update(block)
    return digest.hexdigest(), info.file_size, "hashed"


def _xml(archive: ZipFile, part: str) -> ET.Element:
    try:
        info = archive.getinfo(part)
    except KeyError as exc:
        raise UnsupportedDeck(f"missing_part:{part}") from exc
    if info.file_size > _XML_PART_LIMIT:
        raise UnsupportedDeck(f"oversized_xml_part:{part}")
    try:
        return ET.fromstring(archive.read(part))
    except ET.ParseError as exc:
        raise UnsupportedDeck(f"invalid_xml:{part}") from exc


def _normalize_target(source_part: str, target: str) -> str | None:
    if not target or target.startswith(("http:", "https:", "file:")):
        return None
    raw = PurePosixPath(target)
    absolute_part_name = target.startswith("/")
    parts = [] if absolute_part_name else list(PurePosixPath(source_part).parent.parts)
    for item in raw.parts:
        if item in {"", ".", "/"}:
            continue
        if item == ".." and not absolute_part_name:
            if not parts:
                return None
            parts.pop()
        elif item == "..":
            return None
        else:
            parts.append(item)
    return "/".join(parts)


def _rels(archive: ZipFile, source_part: str) -> dict[str, dict]:
    base = PurePosixPath(source_part)
    part = str(base.parent / "_rels" / (base.name + ".rels"))
    if part not in archive.namelist():
        return {}
    root = _xml(archive, part)
    result = {}
    for rel in root.findall(f"{{{REL}}}Relationship"):
        rid = rel.get("Id")
        if not rid:
            continue
        external = rel.get("TargetMode") == "External"
        result[rid] = {"relationship": rid, "type": rel.get("Type", "").rsplit("/", 1)[-1],
                       "target": None if external else _normalize_target(source_part, rel.get("Target", "")),
                       "external": external}
    return result


def _visible_slide_parts(archive: ZipFile) -> list[str]:
    presentation = _xml(archive, "ppt/presentation.xml")
    relationships = _rels(archive, "ppt/presentation.xml")
    result = []
    for node in presentation.findall("p:sldIdLst/p:sldId", NS):
        rid = node.get(f"{{{R}}}id")
        rel = relationships.get(rid or "")
        if rel and rel["type"] == "slide" and rel["target"] in archive.namelist():
            result.append(rel["target"])
    return result


def _shape_name_id(element: ET.Element) -> tuple[str | None, str | None]:
    for candidate in element.iter():
        tag = candidate.tag.rsplit("}", 1)[-1]
        if tag in {"cNvPr", "nvSpPr"} and (candidate.get("id") or candidate.get("name")):
            return candidate.get("id"), candidate.get("name")
    return None, None


def _bounds(element: ET.Element) -> dict | None:
    xfrm = element.find("p:spPr/a:xfrm", NS)
    if xfrm is None:
        xfrm = element.find("p:xfrm", NS)
    if xfrm is None:
        xfrm = element.find("p:grpSpPr/a:xfrm", NS)
    if xfrm is None:
        return None
    off, ext = xfrm.find("a:off", NS), xfrm.find("a:ext", NS)
    if off is None or ext is None:
        return None
    try:
        values = {"x": int(off.get("x", 0)), "y": int(off.get("y", 0)),
                  "width": int(ext.get("cx", 0)), "height": int(ext.get("cy", 0))}
    except (TypeError, ValueError):
        return None
    return values if values["width"] > 0 and values["height"] > 0 else None


def _shape_text(element: ET.Element) -> tuple[str, list[dict]]:
    paragraphs = []
    for paragraph in element.findall(".//a:p", NS):
        chunks, sizes, bold = [], [], False
        for run in paragraph.findall(".//a:r", NS) + paragraph.findall(".//a:fld", NS):
            text = "".join((node.text or "") for node in run.findall(".//a:t", NS))
            if text:
                chunks.append(text)
            props = run.find("a:rPr", NS)
            if props is not None:
                if props.get("sz", "").isdigit():
                    sizes.append(int(props.get("sz")) / 100)
                bold = bold or props.get("b") in {"1", "true"}
        for node in paragraph.findall("a:endParaRPr", NS):
            if node.get("sz", "").isdigit():
                sizes.append(int(node.get("sz")) / 100)
        if chunks:
            paragraphs.append({"text": "".join(chunks).strip(), "font_size_pt": max(sizes) if sizes else None,
                               "bold": bold})
    return "\n".join(p["text"] for p in paragraphs if p["text"]), paragraphs


def _usable_title(text: str) -> bool:
    compact = re.sub(r"[\s，。！？、：:;；·—_\-]+", "", text).casefold()
    if not compact or compact in _GENERIC_TITLES or compact.isdigit():
        return False
    cjk = len(re.findall(r"[\u3400-\u9fff]", compact))
    letters = len(re.findall(r"[a-z]", compact))
    return cjk >= 2 or letters >= 3


def _terms(text: str) -> list[str]:
    chunks = re.findall(r"[\u3400-\u9fff]{2,16}|[A-Za-z][A-Za-z0-9+.-]{1,}|\d+(?:\.\d+)?%?", text)
    terms = []
    for chunk in chunks:
        if re.fullmatch(r"\d+(?:\.\d+)?%?", chunk):
            continue
        if re.fullmatch(r"[\u3400-\u9fff]+", chunk):
            # Overlapping Chinese terms support narrower literal queries while
            # keeping this explicitly lexical rather than claiming NLP.
            terms.extend(chunk[i:i + size] for size in (2, 3, 4)
                         for i in range(max(0, len(chunk) - size + 1)))
        else:
            terms.append(chunk.casefold())
    counts = Counter(term for term in terms if term not in _TERM_STOP)
    return [word for word, _ in counts.most_common(18)]


def _roles(title: str, text: str) -> list[dict]:
    haystack = f"{title}\n{text}".casefold()
    matches = []
    for role, markers in _ROLE_RULES:
        evidence = [marker for marker in markers if marker.casefold() in haystack]
        if evidence:
            matches.append({"role": role, "evidence": evidence[:5], "basis": "literal_text_keyword_heuristic"})
    return matches[:5]


def _region(bounds: dict, width: int, height: int) -> str | None:
    if not width or not height:
        return None
    x = (bounds["x"] + bounds["width"] / 2) / width
    y = (bounds["y"] + bounds["height"] / 2) / height
    return ("top" if y < .34 else "bottom" if y > .68 else "middle") + "_" + ("left" if x < .36 else "right" if x > .64 else "center")


def _distance(a: dict, b: dict) -> float:
    ax, ay = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    bx, by = b["x"] + b["width"] / 2, b["y"] + b["height"] / 2
    return math.hypot(ax - bx, ay - by)


def _slide_record(archive: ZipFile, slide_part: str, number: int, slide_width: int, slide_height: int) -> dict:
    root = _xml(archive, slide_part)
    rels = _rels(archive, slide_part)
    tree = root.find("p:cSld/p:spTree", NS)
    if tree is None:
        raise UnsupportedDeck(f"missing_spTree:{slide_part}")
    shapes = []
    media = []
    counts: Counter = Counter()
    picture_area = 0.0
    slide_area = float(slide_width * slide_height) if slide_width and slide_height else 0.0
    for order, element in enumerate(list(tree), 1):
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in {"sp", "pic", "graphicFrame", "cxnSp", "grpSp"}:
            continue
        shape_id, shape_name = _shape_name_id(element)
        text, paragraphs = _shape_text(element)
        placeholder = element.find("p:nvSpPr/p:nvPr/p:ph", NS)
        placeholder_type = placeholder.get("type") if placeholder is not None else None
        bounds = _bounds(element)
        kind = {"sp": "shape", "pic": "picture", "graphicFrame": "graphic_frame", "cxnSp": "connector", "grpSp": "group"}[tag]
        if text and kind == "shape":
            kind = "text_shape"
        counts[kind] += 1
        if tag == "pic" and bounds and slide_area:
            picture_area += bounds["width"] * bounds["height"] / slide_area
        shape = {"order": order, "shape_id": shape_id, "shape_name": shape_name, "kind": kind,
                 "placeholder_type": placeholder_type, "text": text, "paragraphs": paragraphs[:20],
                 "bounds_emu": bounds, "region": _region(bounds, slide_width, slide_height) if bounds else None}
        shapes.append(shape)
        if tag not in _MEDIA_TAGS:
            continue
        for node in element.iter():
            local = node.tag.rsplit("}", 1)[-1]
            if local not in {"blip", "svgBlip"}:
                continue
            rel_ids = []
            for key, value in node.attrib.items():
                if key.startswith("{" + R + "}"):
                    rel_ids.append((key.rsplit("}", 1)[-1], value))
            for relationship_kind, rid in rel_ids:
                rel = rels.get(rid, {})
                target = rel.get("target")
                is_svg = local == "svgBlip" or (target and target.lower().endswith(".svg"))
                if relationship_kind not in {"embed", "link", "id"}:
                    continue
                media_hash, media_bytes, media_hash_status = _zip_part_hash(archive, target)
                media.append({"media_kind": "svg" if is_svg else "image", "kind": "svg" if is_svg else "image",
                              "description": "Embedded media candidate; visual meaning has not been reviewed.",
                              "format": (target.rsplit(".", 1)[-1].lower() if target and "." in target else ("svg" if is_svg else "unknown")),
                              "sha256": media_hash, "bytes": media_bytes, "hash_status": media_hash_status,
                              "reuse_status": "candidate_only", "rights_status": "unverified",
                              "source_slide": number,
                              "source_shape_id": shape_id, "source_shape_name": shape_name,
                              "relationship_id": rid, "relationship_type": rel.get("type"),
                              "media_part": target, "external": rel.get("external", False),
                              "part_exists": bool(target and target in archive.namelist()),
                              "bounds_emu": bounds, "region": shape["region"]})
    text_shapes = [shape for shape in shapes if shape["text"].strip()]
    title_shape = next((s for s in text_shapes
                        if s["placeholder_type"] in {"title", "ctrTitle"} and _usable_title(s["text"])), None)
    title_source = "title_placeholder" if title_shape else None
    if title_shape is None:
        usable_shapes = [s for s in text_shapes if _usable_title(s["text"])]
        if usable_shapes:
            title_shape = max(usable_shapes, key=lambda s: (
                max((p["font_size_pt"] or 0 for p in s["paragraphs"]), default=0),
                s["region"].startswith("top_") if s["region"] else False,
                -s["order"]))
            title_source = "largest_text"
    title = (title_shape["text"].splitlines()[0][:180] if title_shape else "")
    excerpt = "\n".join(s["text"] for s in text_shapes if s is not title_shape)
    excerpt = excerpt[:_TEXT_LIMIT]
    title_size = max((p["font_size_pt"] or 0 for p in title_shape["paragraphs"]), default=0) if title_shape else 0
    body_sizes = [p["font_size_pt"] for s in text_shapes if s is not title_shape for p in s["paragraphs"] if p["font_size_pt"]]
    evidence_shapes = [s for s in shapes if s["kind"] in {"picture", "graphic_frame"}]
    title_box = title_shape.get("bounds_emu") if title_shape else None
    relationships = []
    for item in evidence_shapes:
        b = item.get("bounds_emu")
        near = sorted(((_distance(b, other["bounds_emu"]), other["shape_id"], other["text"][:80])
                       for other in text_shapes if b and other.get("bounds_emu")), key=lambda row: row[0])[:2]
        relationships.append({"shape_id": item["shape_id"], "kind": item["kind"], "region": item["region"],
                              "nearest_text_shapes": [{"shape_id": sid, "text": text, "center_distance_emu": round(dist)}
                                                      for dist, sid, text in near]})
    role_candidates = _roles(title, excerpt)
    if number == 1 and title and not any(item["role"] == "cover" for item in role_candidates):
        role_candidates.insert(0, {"role": "cover", "evidence": ["first_visible_slide_with_title"],
                                   "basis": "visible_order_position_heuristic"})
    regions = list(dict.fromkeys(s["region"] for s in shapes if s["region"]))
    if counts["picture"] and counts["text_shape"]:
        composition_pattern = "image_text_mixed"
        image_text_relation = "image_and_text_nearby" if any(item["nearest_text_shapes"] for item in relationships) else "co_present_relation_unresolved"
    elif counts["picture"]:
        composition_pattern, image_text_relation = "image_dominant", "no_text_shape_detected"
    elif counts["graphic_frame"]:
        composition_pattern, image_text_relation = "chart_or_graphic_frame", "structured_graphic_with_optional_text"
    elif counts["connector"]:
        composition_pattern, image_text_relation = "diagram_or_process", "connector_relationships_present"
    else:
        composition_pattern, image_text_relation = "text_or_shape_composition", "no_embedded_image_detected"
    levels = []
    if title_shape:
        levels.append({"level": "title", "shape_id": title_shape["shape_id"], "text": title[:180],
                       "font_size_pt": title_size or None, "evidence": "title_placeholder_or_largest_explicit_font"})
    for shape in text_shapes:
        if shape is title_shape:
            continue
        levels.append({"level": "body_or_label", "shape_id": shape["shape_id"], "text": shape["text"][:100],
                       "font_size_pt": max((p["font_size_pt"] or 0 for p in shape["paragraphs"]), default=0) or None,
                       "evidence": "text_shape_order_and_explicit_run_font"})
    for shape in evidence_shapes:
        levels.append({"level": "visual_or_data_evidence", "shape_id": shape["shape_id"],
                       "evidence": "picture_or_graphic_frame_object_type"})
    excerpt_density = len(excerpt) / max(1, len(text_shapes))
    return {"slide": number, "part": slide_part, "title": title,
            "title_source": title_source or "unavailable",
            "title_confidence": 0.95 if title_source == "title_placeholder" else 0.58 if title_source == "largest_text" else 0.0,
            "text_excerpt": excerpt, "content_terms": _terms(title + "\n" + excerpt),
            "role_candidates": role_candidates, "narrative_roles": [item["role"] for item in role_candidates],
            "composition": {"basis": "OOXML_geometry_and_object_types_only", "pattern": composition_pattern,
                            "regions": regions, "image_text_relation": image_text_relation, "slide_width_emu": slide_width,
                            "slide_height_emu": slide_height,
                            "aspect_ratio": round(slide_width / slide_height, 5) if slide_height else None,
                            "object_counts": dict(counts), "text_shape_count": len(text_shapes),
                            "picture_count": counts["picture"], "chart_or_graphic_frame_count": counts["graphic_frame"],
                            "connector_count": counts["connector"], "picture_area_sum_ratio": round(picture_area, 5) if slide_area else None,
                            "text_regions": [{"shape_id": s["shape_id"], "region": s["region"], "bounds_emu": s["bounds_emu"], "text": s["text"][:100]} for s in text_shapes[:20]],
                            "evidence_regions": relationships},
            "hierarchy": {"basis": "placeholder_role_then_explicit_run_font_size_heuristic", "levels": levels,
                          "reading_order": [item["shape_id"] for item in sorted(shapes, key=lambda row: row["order"]) if item["shape_id"]],
                          "density": "high" if excerpt_density > 140 else "medium" if excerpt_density > 60 else "low",
                          "title": ({"shape_id": title_shape["shape_id"], "text": title, "font_size_pt": title_size} if title_shape else None),
                          "body_font_size_pt_median": round(sorted(body_sizes)[len(body_sizes)//2], 2) if body_sizes else None,
                          "title_to_body_font_ratio": round(title_size / sorted(body_sizes)[len(body_sizes)//2], 2) if title_size and body_sizes and sorted(body_sizes)[len(body_sizes)//2] else None,
                          "evidence_shape_ids": [s["shape_id"] for s in evidence_shapes],
                          "body_shape_ids": [s["shape_id"] for s in text_shapes if s is not title_shape]},
            "media_candidates": media}


def inspect_library_deck(path: Path, relative_path: str, expected_sha256: str | None = None) -> dict:
    """Index one PPTX's slide hierarchy and embedded image/SVG candidates."""
    path = Path(path)
    try:
        stat = path.stat()
        if not path.is_file() or path.suffix.lower() != ".pptx":
            raise UnsupportedDeck("unsupported_file_type")
        with ZipFile(path) as archive:
            if sum(info.file_size for info in archive.infolist()) > _EXPANDED_LIMIT:
                raise UnsupportedDeck("expanded_package_over_limit")
            presentation = _xml(archive, "ppt/presentation.xml")
            size = presentation.find("p:sldSz", NS)
            width, height = (int(size.get("cx", 0)), int(size.get("cy", 0))) if size is not None else (0, 0)
            parts = _visible_slide_parts(archive)
            if not parts:
                raise UnsupportedDeck("no_visible_slides_resolved")
            slides = [_slide_record(archive, part, number, width, height) for number, part in enumerate(parts, 1)]
        return {"schema": SCHEMA, "schema_revision": SCHEMA_REVISION, "parser_version": PARSER_VERSION,
                "path": relative_path, "expected_sha256": expected_sha256,
                "source_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "status": "parsed", "parse_status": "parsed",
                "inspection_status": "machine_ooxml_unreviewed",
                "slides": slides, "slide_count": len(slides), "inspection": "ooxml_structure_only",
                "review": {"visual_status": "machine_ooxml_unreviewed", "reviewed_pages": [],
                           "review_method": "OOXML structure parser; no rendering", "media_rights": "unverified"},
                "visual_reviewed": False, "content_understanding": "literal_text_and_keyword_heuristics_only",
                "media_meaning_understood": False, "media_rights_verified": False,
                "actual_source_sha256": None, "actual_hash_on_selection_required": True,
                "unsupported": [], "errors": []}
    except Exception as exc:
        try:
            stat = path.stat()
            size_bytes, mtime = stat.st_size, stat.st_mtime_ns
        except OSError:
            size_bytes, mtime = None, None
        return {"schema": SCHEMA, "schema_revision": SCHEMA_REVISION, "parser_version": PARSER_VERSION,
                "path": relative_path, "expected_sha256": expected_sha256,
                "source_bytes": size_bytes, "mtime_ns": mtime, "status": "unsupported", "parse_status": "unsupported", "slides": [],
                "slide_count": 0, "inspection": "ooxml_structure_only", "visual_reviewed": False,
                "review": {"visual_status": "unreviewed", "reviewed_pages": [],
                           "review_method": "unavailable", "media_rights": "unverified"},
                "content_understanding": "unavailable", "media_meaning_understood": False,
                "media_rights_verified": False, "actual_source_sha256": None,
                "actual_hash_on_selection_required": True, "unsupported": [f"{type(exc).__name__}:{exc}"], "errors": []}


def _normal_catalog_path(raw: object) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        return None
    parts = list(path.parts)
    if parts and parts[0] == "模板库":
        parts = parts[1:]
    return PurePosixPath(*parts).as_posix() if parts else None


def _read_jsonl(path: Path) -> list[dict]:
    try:
        rows = []
        with path.open("r", encoding="utf-8-sig") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows
    except OSError:
        return []


def _review_pages(row: dict) -> list[int]:
    values = row.get("reviewed_pages", row.get("pages", []))
    pages = {page for page in values if isinstance(page, int) and not isinstance(page, bool) and page > 0} if isinstance(values, list) else set()
    if pages:
        return sorted(pages)
    # Some review rows encode explicit page ranges only in the recorded
    # inspection method (for example "PDF pages_1_4"). Do not infer pages
    # from generic contact-sheet text.
    method = row.get("inspection", "")
    if isinstance(method, str):
        for first, last in re.findall(r"pages?[_ -](\d{1,3})[_ -](\d{1,3})", method, flags=re.I):
            start, end = int(first), int(last)
            if 0 < start <= end <= 200:
                pages.update(range(start, end + 1))
    return sorted(pages)


def _companion_evidence(catalog: Path) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Load exact-path structure hashes and source-bound review observations."""
    root = catalog.parent
    structures: dict[str, dict] = {}
    for row in _read_jsonl(root / "DESIGN-STRUCTURE.jsonl"):
        relative = _normal_catalog_path(row.get("source_path"))
        coverage = row.get("coverage")
        digest = row.get("source_sha256")
        if (relative and row.get("schema") == "aippt.template-design-index.v1" and row.get("status") == "parsed"
                and isinstance(coverage, dict) and coverage.get("full_slide_coverage") is True
                and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
            structures[relative] = row
    observations: dict[str, list[dict]] = {}

    def add(raw_path: object, digest: object, observation: dict) -> None:
        relative = _normal_catalog_path(raw_path)
        if not relative or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            return
        observations.setdefault(relative, []).append({**observation, "source_path": relative, "source_sha256": digest})

    try:
        payload = json.loads((root / "DESIGN-LANGUAGE.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        payload = {}
    for row in payload.get("designs", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict) or row.get("review_status") not in {"visual_verified", "visual_observed_with_font_limit"}:
            continue
        pages = _review_pages(row)
        add(row.get("source_path"), row.get("source_sha256"), {
            "source": "DESIGN-LANGUAGE.json", "visual_status": row["review_status"],
            "review_method": "visually observed page-language record", "reviewed_pages": pages,
            "design_id": row.get("id")})
    try:
        payload = json.loads((root / "REVIEWED-SAMPLES.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        payload = {}
    for row in payload.get("entries", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        visual_status = row.get("visual_status")
        if not isinstance(visual_status, str) or visual_status in {"not_visually_reviewed", "structure_only", "unreviewed"}:
            continue
        pages = _review_pages(row)
        add(row.get("path"), row.get("source_sha256"), {
            "source": "REVIEWED-SAMPLES.json", "visual_status": visual_status,
            "review_method": row.get("inspection", "recorded sample review"),
            "reviewed_pages": pages, "preview": row.get("preview")})
    return structures, observations


def _catalog_templates(catalog: Path) -> list[dict]:
    structures, observations = _companion_evidence(catalog)
    rows = []
    with catalog.open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            relative = _normal_catalog_path(row.get("path"))
            if not relative or PurePosixPath(relative).suffix.lower() != ".pptx":
                continue
            structure = structures.get(relative)
            catalog_hash = row.get("source_sha256")
            if not isinstance(catalog_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", catalog_hash):
                catalog_hash = None
            expected_hash = catalog_hash
            hash_source = "catalog" if catalog_hash else None
            deck_path = catalog.parent / Path(*PurePosixPath(relative).parts)
            try:
                source_size_matches = bool(structure and deck_path.stat().st_size == structure.get("source_bytes"))
            except OSError:
                source_size_matches = False
            if expected_hash is None and structure and source_size_matches:
                expected_hash = structure["source_sha256"]
                hash_source = "design_structure_full_coverage_cached_hash"
            review_items = [item for item in observations.get(relative, []) if item["source_sha256"] == expected_hash]
            rows.append({"path": relative, "expected_sha256": expected_hash, "expected_sha_source": hash_source,
                         "occasion_tags": row.get("occasion_tags", []), "style_tags": row.get("style_tags", []),
                         "page_role_tags": row.get("page_role_tags", []), "review_provenance": review_items,
                         "line": line_number})
    return rows


def _apply_catalog_metadata(record: dict, row: dict) -> dict:
    """Bind catalog metadata and only source-matching partial review records."""
    record["expected_sha256"] = row.get("expected_sha256")
    record["expected_sha_source"] = row.get("expected_sha_source")
    record["occasion_tags"] = row.get("occasion_tags", []) if isinstance(row.get("occasion_tags"), list) else []
    record["style_tags"] = row.get("style_tags", []) if isinstance(row.get("style_tags"), list) else []
    observations = row.get("review_provenance", []) if isinstance(row.get("review_provenance"), list) else []
    pages = sorted({page for item in observations for page in item.get("reviewed_pages", [])})
    if observations:
        record["review"] = {"visual_status": "partial_visual_observation", "reviewed_pages": pages,
                            "review_method": "page-limited observations; no whole-deck visual review inferred",
                            "media_rights": "unverified", "provenance": observations}
        by_page: dict[int, list[dict]] = {}
        for item in observations:
            for page in item.get("reviewed_pages", []):
                by_page.setdefault(page, []).append({key: item.get(key) for key in
                    ("source", "visual_status", "review_method", "design_id", "preview", "source_sha256") if item.get(key) is not None})
        for slide in record.get("slides", []):
            slide["review_provenance"] = by_page.get(slide.get("slide"), [])
            slide["visual_review_status"] = "partial_visual_observation" if slide["review_provenance"] else "unreviewed"
    else:
        record["review"] = {"visual_status": "machine_ooxml_unreviewed" if record.get("parse_status") == "parsed" else "unreviewed",
                            "reviewed_pages": [], "review_method": "OOXML structure parser; no matching visual review record",
                            "media_rights": "unverified", "provenance": []}
    record["narrative_roles"] = list(dict.fromkeys(
        [role for slide in record.get("slides", []) for role in slide.get("narrative_roles", [])]
        + (row.get("page_role_tags", []) if isinstance(row.get("page_role_tags"), list) else [])))
    record["topic_terms"] = _terms(" ".join(slide.get("title", "") + " " + slide.get("text_excerpt", "") for slide in record.get("slides", [])))
    return record


def build_depth_index(catalog: Path, index_path: Path, *, limit: int | None = None,
                      path_prefix: str | None = None) -> dict:
    """Incrementally build JSONL depth records, caching only path/mtime/size.

    Catalog hashes remain expectations; selecting a deck must independently
    compute and compare the real file hash before applying its contents.
    """
    catalog = Path(catalog).expanduser().resolve()
    index_path = Path(index_path).expanduser()
    rows = _catalog_templates(catalog)
    if path_prefix is None:
        scoped_rows = rows
    else:
        prefix = _normal_catalog_path(path_prefix)
        if (prefix is None or PurePosixPath(prefix).suffix.lower() == ".pptx"
                or prefix != path_prefix.strip().strip("/")):
            raise ValueError("path_prefix must be a normalized library-relative directory path")
        scoped_rows = [row for row in rows if row["path"] == prefix or row["path"].startswith(prefix + "/")]
    chosen = scoped_rows if limit is None else scoped_rows[:max(0, limit)]
    old: dict[str, dict] = {}
    if index_path.is_file():
        try:
            for line in index_path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("schema") == SCHEMA and isinstance(record.get("path"), str):
                    old[record["path"]] = record
        except OSError:
            pass
    root = catalog.parent
    retained = {row["path"]: old[row["path"]] for row in rows if row["path"] in old}
    counts = Counter()
    errors = []
    for row in chosen:
        source = root / Path(*PurePosixPath(row["path"]).parts)
        try:
            resolved = source.resolve(strict=False)
            resolved.relative_to(root.resolve())
            stat = resolved.stat()
            cached = old.get(row["path"])
            if (cached and cached.get("source_bytes") == stat.st_size and cached.get("mtime_ns") == stat.st_mtime_ns
                    and cached.get("expected_sha256") == row.get("expected_sha256")
                    and cached.get("schema_revision") == SCHEMA_REVISION
                    and cached.get("parser_version") == PARSER_VERSION):
                retained[row["path"]] = _apply_catalog_metadata(cached, row)
                counts["reused" if cached.get("parse_status") == "parsed" else "reused_unsupported"] += 1
                continue
            record = inspect_library_deck(resolved, row["path"], row.get("expected_sha256"))
            record = _apply_catalog_metadata(record, row)
            retained[row["path"]] = record
            counts["indexed" if record["parse_status"] == "parsed" else "unsupported"] += 1
        except (OSError, ValueError) as exc:
            retained[row["path"]] = {"schema": SCHEMA, "schema_revision": SCHEMA_REVISION,
                                      "parser_version": PARSER_VERSION, "path": row["path"],
                                      "expected_sha256": row.get("expected_sha256"),
                                      "source_bytes": None, "mtime_ns": None, "status": "unsupported", "slides": [],
                                      "parse_status": "unsupported", "occasion_tags": row.get("occasion_tags", []),
                                      "topic_terms": [], "style_tags": row.get("style_tags", []), "narrative_roles": [],
                                      "slide_count": 0, "inspection": "ooxml_structure_only", "visual_reviewed": False,
                                      "review": {"visual_status": "unreviewed", "reviewed_pages": [],
                                                 "review_method": "unavailable", "media_rights": "unverified"},
                                      "media_meaning_understood": False, "media_rights_verified": False,
                                      "actual_source_sha256": None, "actual_hash_on_selection_required": True,
                                      "unsupported": [f"path_unavailable_or_unsafe:{type(exc).__name__}"], "errors": []}
            counts["unsupported"] += 1
            errors.append({"path": row["path"], "error": type(exc).__name__})
    # Preserve unprocessed rows under --limit for a resumable bounded scan;
    # remove rows no longer in the authoritative catalog.
    order = {row["path"]: i for i, row in enumerate(rows)}
    records = sorted(retained.values(), key=lambda item: order.get(item["path"], len(order)))
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = index_path.with_name(index_path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(index_path)
    return {"schema": SCHEMA, "status": "ready" if not errors and not counts["unsupported"] and not counts["reused_unsupported"] else "partial", "index_path": str(index_path),
            "catalog_path": str(catalog), "catalog_entry_count": len(rows),
            "scoped_entry_count": len(scoped_rows), "path_prefix": path_prefix,
            "processed_count": len(chosen),
            "record_count": len(records), "statistics": dict(counts), "errors": errors,
            "limitations": ["OOXML structure and literal text heuristics only; no rendering or visual review.",
                            "Catalog hash is expected metadata; actual hash is recomputed at selection time.",
                            "Embedded media are candidates only; meaning, reuse rights, and visual fit are unverified."]}
