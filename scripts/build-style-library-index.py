"""Build complete-page OOXML style/content evidence for competition collections.

This is a source-bound machine index, not visual QA. It leaves every PPTX,
PPSX and PPTM unchanged, deduplicates exact bytes, and never opens media data.
Legacy PPT and PDF companions are inventoried separately from this OOXML pass.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
import re

from aippt_studio.style_library import PARSER_VERSION, inspect_style_deck


PROJECT = Path(__file__).resolve().parents[1]
LIBRARY = PROJECT / "模板库"
COLLECTIONS = {
    "creative": LIBRARY / "创赛",
    "career": LIBRARY / "BB0086 大学生职业生涯规划大赛 PPT",
}
OOXML_EXTENSIONS = {".pptx", ".pptm", ".ppsx"}
INDEX_VERSION = "competition-style-index-4"


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")


def _quantiles(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    values.sort()
    def at(fraction: float) -> float:
        index = round((len(values) - 1) * fraction)
        return round(values[index], 2)
    return {"p10": at(.1), "p50": at(.5), "p90": at(.9)}


def _color_hex(value: object) -> str | None:
    if isinstance(value, dict):
        val = value.get("val") or value.get("lastClr")
        if isinstance(val, str) and re.fullmatch(r"[0-9A-Fa-f]{6}", val):
            return val.upper()
    return None


def _style_profile(deck: dict) -> dict:
    slides = deck.get("slides", [])
    if not slides:
        return {"basis": "unavailable", "visual_reviewed": False}
    object_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    face_counts: Counter[str] = Counter()
    fill_counts: Counter[str] = Counter()
    theme_counts: Counter[str] = Counter()
    layout_counts: Counter[str] = Counter()
    master_counts: Counter[str] = Counter()
    font_sizes: list[float] = []
    pictures_per_slide: list[float] = []
    text_objects_per_slide: list[float] = []
    media_bytes_per_slide: list[float] = []
    role_sequence = []
    for slide in slides:
        counts = slide.get("composition", {}).get("object_counts", {})
        object_counts.update(counts)
        pictures_per_slide.append(sum(len(obj.get("image_fills", [])) or (obj.get("type") == "pic")
                                      for obj in slide.get("objects", [])))
        text_objects_per_slide.append(sum(bool(obj.get("text", "").strip()) for obj in slide.get("objects", [])))
        media_bytes_per_slide.append(sum(fill.get("declared_bytes") or 0
                                         for obj in slide.get("objects", [])
                                         for fill in obj.get("image_fills", [])))
        roles = [item.get("role") for item in slide.get("role_evidence", []) if item.get("role")]
        role_counts.update(roles)
        role_sequence.append({"slide": slide["order"], "hidden": slide.get("hidden", False),
                              "title": slide.get("title", ""), "roles": roles})
        context = slide.get("style_context") or {}
        layout = context.get("slide_layout_part")
        master = context.get("slide_master_part")
        if layout:
            layout_counts[layout] += 1
        if master:
            master_counts[master] += 1
        theme = context.get("theme") or {}
        if theme.get("part"):
            theme_counts[theme["part"]] += 1
        for obj in slide.get("objects", []):
            for run in obj.get("text_runs", []):
                size = run.get("font_size_pt")
                if isinstance(size, (int, float)) and size > 0:
                    font_sizes.append(float(size))
                face = run.get("east_asian_typeface") or run.get("latin_typeface")
                if face:
                    face_counts[str(face)] += 1
            fill = (obj.get("shape_style") or {}).get("fill")
            if isinstance(fill, dict):
                for item in fill.values():
                    color = _color_hex(item)
                    if color:
                        fill_counts[color] += 1
    dominant_theme = theme_counts.most_common(1)[0][0] if theme_counts else None
    palette = {}
    theme_fonts = {}
    for slide in slides:
        theme = (slide.get("style_context") or {}).get("theme") or {}
        if theme.get("part") == dominant_theme and theme.get("status") == "parsed":
            palette = {slot: _color_hex(value) or value.get("val") or value.get("lastClr")
                       for slot, value in (theme.get("palette") or {}).items()}
            theme_fonts = theme.get("fonts") or {}
            break
    page = deck.get("presentation_size") or {}
    width, height = page.get("width_emu") or 0, page.get("height_emu") or 0
    return {
        "basis": "full_presentation_ooxml_measurement_only",
        "visual_reviewed": False,
        "image_meaning_understood": False,
        "page_aspect_ratio": round(width / height, 5) if height else None,
        "theme_parts": dict(theme_counts), "dominant_theme": dominant_theme,
        "theme_palette": palette, "theme_fonts": theme_fonts,
        "layout_usage": dict(layout_counts), "master_usage": dict(master_counts),
        "object_counts": dict(object_counts),
        "font_typefaces_top": face_counts.most_common(12),
        "explicit_font_size_pt": _quantiles(font_sizes),
        "explicit_fill_colors_top": fill_counts.most_common(12),
        "pictures_per_slide": _quantiles(pictures_per_slide),
        "text_objects_per_slide": _quantiles(text_objects_per_slide),
        "declared_picture_bytes_per_slide": _quantiles(media_bytes_per_slide),
        "page_roles": dict(role_counts), "page_role_sequence": role_sequence,
        "unique_layout_count": len(layout_counts), "unique_master_count": len(master_counts),
        "theme_consistency_ratio": round(max(theme_counts.values()) / len(slides), 4) if theme_counts else None,
    }


def _page_row(deck: dict, slide: dict, detail_ref: str, canonical_path: str) -> dict:
    objects = slide.get("objects", [])
    image_count = sum(len(obj.get("image_fills", [])) or (obj.get("type") == "pic") for obj in objects)
    chart_count = sum((obj.get("graphic") or {}).get("kind") == "chart" for obj in objects)
    table_count = sum((obj.get("graphic") or {}).get("kind") == "table" for obj in objects)
    role_evidence = slide.get("role_evidence", [])
    text = "\n".join(item for item in slide.get("content_sequence", []) if item)
    size = deck.get("presentation_size") or {}
    slide_width, slide_height = size.get("width_emu") or 0, size.get("height_emu") or 0
    area = slide_width * slide_height
    by_id = {str(obj.get("id")): obj for obj in objects if obj.get("id")}
    image_boxes = []
    for obj in objects:
        if not (obj.get("image_fills") or obj.get("type") == "pic"):
            continue
        box = obj.get("geometry")
        group_path = obj.get("group_path") or []
        if group_path:
            # A nested shape's local xfrm is relative to its group. The
            # outer group's page box is a safer composition proxy than
            # pretending the child coordinates are already page absolute.
            outer = by_id.get(str(group_path[0].get("id")))
            box = (outer or {}).get("geometry") or box
        if box:
            image_boxes.append(box)
    def is_canvas_background(box: dict) -> bool:
        return bool(slide_width and slide_height
                    and box.get("x_emu", 0) <= slide_width * .05
                    and box.get("y_emu", 0) <= slide_height * .05
                    and box.get("width_emu", 0) >= slide_width * .9
                    and box.get("height_emu", 0) >= slide_height * .9)
    background_boxes = [box for box in image_boxes if is_canvas_background(box)]
    content_image_boxes = [box for box in image_boxes if not is_canvas_background(box)]
    text_boxes = [obj["geometry"] for obj in objects if obj.get("text", "").strip()
                  and not obj.get("group_path") and obj.get("geometry")]
    image_area = [box.get("width_emu", 0) * box.get("height_emu", 0) / area
                  for box in image_boxes] if area else []
    content_image_area = [box.get("width_emu", 0) * box.get("height_emu", 0) / area
                          for box in content_image_boxes] if area else []
    largest_image = max(image_area, default=0.0)
    if background_boxes and not content_image_boxes:
        arrangement = "large_image_canvas"
    elif len(content_image_boxes) >= 4 and max(content_image_area, default=0) < .35:
        arrangement = "multi_image_gallery"
    elif content_image_boxes and text_boxes and slide_width:
        image_centers = [box.get("x_emu", 0) + box.get("width_emu", 0) / 2 for box in content_image_boxes]
        text_centers = [box.get("x_emu", 0) + box.get("width_emu", 0) / 2 for box in text_boxes]
        difference = (sum(image_centers) / len(image_centers) - sum(text_centers) / len(text_centers)) / slide_width
        arrangement = "image_left_text_right" if difference < -.2 else "text_left_image_right" if difference > .2 else "image_text_interleaved"
    elif content_image_boxes and not text_boxes and max(content_image_area, default=0) >= .6:
        arrangement = "large_image_canvas"
    elif chart_count or table_count:
        arrangement = "structured_data_frame"
    elif text_boxes:
        arrangement = "text_and_native_shapes"
    else:
        arrangement = "native_shapes_or_unresolved"
    return {
        "schema": "aippt.competition-style-page.v4", "index_version": INDEX_VERSION,
        "canonical_path": canonical_path, "source_sha256": deck["source_sha256"],
        "detail_ref": detail_ref, "slide": slide["order"], "slide_part": slide["part"],
        "hidden": slide.get("hidden", False), "title": slide.get("title", ""),
        "text_excerpt": text[:1800], "text_characters": len(text),
        "role_evidence": role_evidence,
        "composition": slide.get("composition", {}),
        "layout_pattern": {"kind": arrangement, "basis": "explicit_top_level_geometry_heuristic",
                           "largest_image_area_ratio": round(largest_image, 4),
                           "image_area_sum_ratio": round(sum(image_area), 4),
                           "background_image_count": len(background_boxes),
                           "largest_content_image_area_ratio": round(max(content_image_area, default=0), 4)},
        "image_count": image_count, "chart_count": chart_count, "table_count": table_count,
        "layout_part": (slide.get("style_context") or {}).get("slide_layout_part"),
        "master_part": (slide.get("style_context") or {}).get("slide_master_part"),
        "shared_layer_summary": _shared_layer_summary(deck, slide),
        "image_meaning_status": "unreviewed",
        "visual_status": "not_reviewed",
    }


def _shared_layer_summary(deck: dict, slide: dict) -> dict:
    refs = []
    context = slide.get("style_context") or {}
    layers = deck.get("shared_layers") or {}
    for part, kind in ((context.get("slide_layout_part"), "slide_layout"),
                       (context.get("slide_master_part"), "slide_master")):
        if not part:
            continue
        layer = layers.get(part) or {}
        objects = layer.get("objects", [])
        refs.append({"part": part, "kind": kind, "status": layer.get("status", "unavailable"),
                     "object_count": len(objects), "image_count": sum(len(obj.get("image_fills", []))
                                                                         or (obj.get("type") == "pic")
                                                                         for obj in objects)})
    return {"basis": "shared_layers_keyed_by_part", "layers": refs}


def _deck_row(deck: dict, relative: str, detail_ref: str, detail_sha: str,
              profile: dict, canonical_path: str, source_bytes: int) -> dict:
    return {
        "schema": "aippt.competition-style-deck.v4", "index_version": INDEX_VERSION, "path": relative,
        "canonical_path": canonical_path, "source_sha256": deck["source_sha256"],
        "source_bytes": source_bytes, "source_format": deck.get("source_format"),
        "status": deck.get("status"), "slide_count": deck.get("slide_count", len(deck.get("slides", []))),
        "detail_ref": detail_ref, "detail_sha256": detail_sha,
        "style_profile": profile,
        "source_errors": deck.get("errors", []),
        "review": {"page_structure": "full_ooxml" if deck.get("status") == "parsed" else "unsupported",
                   "visual": "not_reviewed", "content_semantics": "heuristic_evidence",
                   "media_rights": "unverified"},
    }


def build(collection: Path, *, limit: int | None = None) -> dict:
    collection = collection.resolve()
    analysis = collection / "分析索引"
    files = sorted(path for path in collection.rglob("*")
                   if path.is_file() and analysis not in path.parents and path.suffix.lower() in OOXML_EXTENSIONS)
    total_source_files = len(files)
    if limit is not None:
        if limit < 1:
            raise ValueError("--limit must be positive")
        files = files[:limit]
    # Diagnostic subsets must never replace the authoritative whole-library
    # indexes, even when a maintainer runs a quick bounded probe later.
    output_dir = analysis if limit is None else analysis / "diagnostics" / f"limit-{limit}"
    detail_dir = output_dir / "逐份结构"
    detail_dir.mkdir(parents=True, exist_ok=True)
    decks: list[dict] = []
    pages: list[dict] = []
    failures: list[dict] = []
    unique: dict[str, tuple[str, str, dict, dict, str]] = {}
    for index, path in enumerate(files, 1):
        relative = path.relative_to(LIBRARY).as_posix()
        try:
            digest = _sha(path)
            if digest not in unique:
                detail_path = detail_dir / f"{digest}.json"
                result = None
                cache_used = False
                if detail_path.is_file():
                    try:
                        cached = json.loads(detail_path.read_text(encoding="utf-8"))
                        if (cached.get("source_sha256") == digest
                                and cached.get("parser_version") == PARSER_VERSION):
                            result = cached
                            cache_used = True
                    except (OSError, ValueError, TypeError):
                        pass
                if result is None:
                    result = inspect_style_deck(path, relative)
                if result.get("source_sha256") != digest:
                    raise ValueError("source changed during inspection")
                if not cache_used or result.get("path") != relative:
                    result["path"] = relative
                    detail_path.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
                detail_ref = detail_path.relative_to(LIBRARY).as_posix()
                detail_sha = _sha(detail_path)
                profile = _style_profile(result)
                if result.get("status") == "parsed":
                    pages.extend(_page_row(result, slide, detail_ref, relative) for slide in result.get("slides", []))
                stub = {"source_sha256": digest, "source_format": result.get("source_format"),
                        "status": result.get("status"), "slide_count": len(result.get("slides", [])),
                        "errors": result.get("errors", [])}
                unique[digest] = (detail_ref, detail_sha, stub, profile, relative)
            detail_ref, detail_sha, result, profile, canonical = unique[digest]
            decks.append(_deck_row(result, relative, detail_ref, detail_sha, profile, canonical, path.stat().st_size))
            if result.get("status") != "parsed":
                failures.append({"path": relative, "sha256": digest, "errors": result.get("errors", [])})
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
            failures.append({"path": relative, "error": f"{type(error).__name__}:{error}"})
        if index % 100 == 0:
            print(f"indexed {index}/{len(files)} source OOXML presentations", flush=True)
    _jsonl(output_dir / "STYLE-CATALOG.jsonl", decks)
    _jsonl(output_dir / "STYLE-PAGES.jsonl", pages)
    report = {
        "schema": "aippt.competition-style-build.v4", "index_version": INDEX_VERSION,
        "collection": collection.relative_to(LIBRARY).as_posix(),
        "source_files": len(files), "total_source_files": total_source_files,
        "scope": "full_collection" if limit is None else "diagnostic_subset_not_authoritative",
        "output_dir": str(output_dir), "catalog_rows": len(decks),
        "unique_source_sha256": len(unique),
        "unique_parsed": sum(item[2].get("status") == "parsed" for item in unique.values()),
        "page_records_unique": len(pages),
        "unsupported_or_errors": failures,
        "boundary": "OOXML source and measurable style/content only; visual review, image meaning, rights and legacy PPT are separate.",
    }
    (output_dir / "STYLE-BUILD-REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collection", choices=list(COLLECTIONS), required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    result = build(COLLECTIONS[args.collection], limit=args.limit)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
