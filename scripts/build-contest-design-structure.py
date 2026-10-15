#!/usr/bin/env python3
"""Build a review-only DESIGN-STRUCTURE candidate from contest STYLE-CATALOGs.

This is a read-only bridge: it never writes to the global template index or to
source decks. Every candidate row is admitted only when the current PPTX SHA256
matches the catalog row.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable

EMU_PER_INCH = 914400
SCHEMA = "aippt.template-design-index.v1"
CATALOG_SCHEMAS = {"aippt.competition-style-deck.v1", "aippt.competition-style-deck.v2",
                   "aippt.competition-style-deck.v3", "aippt.competition-style-deck.v4"}
CATALOG_SCHEMA = "aippt.competition-style-deck.v4"


def read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def quantiles(values: list[float]) -> dict[str, float] | None:
    values = sorted(v for v in values if math.isfinite(v))
    if not values:
        return None

    def q(p: float) -> float:
        pos = (len(values) - 1) * p
        lo = int(pos)
        hi = min(lo + 1, len(values) - 1)
        return round(values[lo] + (values[hi] - values[lo]) * (pos - lo), 5)

    return {"p10": q(.1), "p50": q(.5), "p90": q(.9)}


def load_curation(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "模板库/CONTENT-CURATION.jsonl"
    if not path.is_file():
        return {}
    result = {}
    for item in read_jsonl(path):
        if isinstance(item.get("path"), str):
            result[item["path"]] = item
    return result


def profile_metrics(profile: dict[str, Any]) -> dict[str, Any]:
    objects = profile.get("object_counts") or {}
    explicit_sizes = profile.get("explicit_font_size_pt") or {}
    colors = profile.get("explicit_fill_colors_top") or []
    font_top = profile.get("font_typefaces_top") or []
    return {
        "slide_object_counts": {k: v for k, v in objects.items() if k != "text_shape"},
        "font_typefaces_top": font_top,
        "font_size_pt": {
            "all_runs_quantiles": explicit_sizes,
            "evidence_note": "Explicit OOXML run sizes only; inherited/theme sizes are not included.",
        },
        "theme1_palette": [
            {"slot": slot, "color": color}
            for slot, color in (profile.get("theme_palette") or {}).items()
        ],
        "theme_fonts": profile.get("theme_fonts"),
        "run_colors_top": [],
        "shape_fill_colors_top": colors,
        "theme_palette_source": "dominant theme measured from source OOXML",
        "explicit_fill_colors_top": colors,
        "layout_usage": profile.get("layout_usage", {}),
        "master_usage": profile.get("master_usage", {}),
        "pictures_per_slide": profile.get("pictures_per_slide"),
        "text_objects_per_slide": profile.get("text_objects_per_slide"),
        "declared_picture_bytes_per_slide": profile.get("declared_picture_bytes_per_slide"),
        "page_roles": profile.get("page_roles", {}),
        "page_role_sequence": profile.get("page_role_sequence", []),
    }


def detail_metrics(detail: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    size = detail.get("presentation_size") or {}
    width = size.get("width_emu") if isinstance(size, dict) else None
    height = size.get("height_emu") if isinstance(size, dict) else None
    w_in = round(width / EMU_PER_INCH, 5) if width else None
    h_in = round(height / EMU_PER_INCH, 5) if height else None
    slides = detail.get("slides") or []
    count = len(slides)
    object_counts: dict[str, int] = {}
    pic_areas: list[float] = []
    text_widths: list[float] = []
    for slide in slides:
        pics = 0
        pic_area = 0.0
        for obj in slide.get("objects", []):
            typ = obj.get("type")
            object_counts[typ] = object_counts.get(typ, 0) + 1
            geom = obj.get("geometry") or {}
            ow, oh = geom.get("width_emu"), geom.get("height_emu")
            if typ == "pic":
                pics += 1
                if width and height and ow and oh:
                    pic_area += (ow * oh) / (width * height)
            if obj.get("text_runs") and width and ow:
                text_widths.append(ow / width)
        if count:
            pic_areas.append(pic_area)
    return {
        "page": {
            "width_in": w_in,
            "height_in": h_in,
            "aspect": round(width / height, 5) if width and height else None,
            "aspect_label": "wide_16_9" if width and height and abs(width / height - 16 / 9) < .02 else "measured_custom",
        },
        "text_box_width_ratio_quantiles": quantiles(text_widths),
        "picture_area_sum_per_slide": quantiles(pic_areas),
        "slide_object_counts_from_detail": object_counts,
        "detail_slide_count": count,
    }


def make_row(catalog: dict[str, Any], catalog_path: Path, root: Path, detail_root: Path,
             curation: dict[str, dict[str, Any]] | None = None) -> tuple[dict[str, Any] | None, str]:
    # A SHA-deduplicated detail can describe many actual library paths. Keep
    # one registered structure row for every path so search and media binding
    # can resolve the exact file selected by the user.
    path_text = catalog.get("path") or catalog.get("canonical_path")
    if catalog.get("schema") not in CATALOG_SCHEMAS:
        return None, "unsupported_catalog_schema"
    if catalog.get("source_format") != "pptx" or catalog.get("status") != "parsed":
        return None, "not_parsed_pptx"
    if not path_text or not catalog.get("source_sha256"):
        return None, "missing_path_or_sha"
    # STYLE-CATALOG paths are relative to the shared 模板库 root, not to the
    # workspace root (the legacy DESIGN-STRUCTURE uses the same convention).
    library_root = (root / "模板库").resolve()
    source = (library_root / path_text).resolve()
    try:
        source.relative_to(library_root)
    except ValueError:
        return None, "unsafe_source_path"
    if not source.is_file():
        return None, "source_missing"
    current_sha = sha256_file(source)
    if current_sha != catalog["source_sha256"]:
        return None, "source_sha_mismatch"
    detail_ref = catalog.get("detail_ref")
    if not isinstance(detail_ref, str) or not detail_ref:
        return None, "missing_detail_ref"
    detail_path = (library_root / detail_ref).resolve()
    try:
        detail_path.relative_to(library_root)
    except ValueError:
        return None, "unsafe_detail_ref"
    if not detail_path.is_file():
        return None, "detail_missing"
    expected_detail_sha = catalog.get("detail_sha256")
    if not isinstance(expected_detail_sha, str) or sha256_file(detail_path) != expected_detail_sha:
        return None, "detail_sha_mismatch"
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    if detail.get("source_sha256") != current_sha:
        return None, "detail_source_sha_mismatch"
    slides = detail.get("slides") or []
    total = int(detail.get("slide_count") or catalog.get("slide_count") or 0)
    parsed = sum(1 for slide in slides if isinstance(slide, dict) and slide.get("status") == "parsed"
                 and isinstance(slide.get("objects"), list) and isinstance(slide.get("content_sequence"), list))
    if detail.get("status") != "parsed" or total < 1 or len(slides) != total or parsed != total:
        return None, "partial_detail"
    profile = catalog.get("style_profile") or {}
    semantic = (curation or {}).get(path_text, {})
    group = semantic.get("category") or "待核类别"
    category = "/".join(part for part in (group, semantic.get("subcategory")) if part)
    measured = detail_metrics(detail, profile)
    row = {
        "schema": SCHEMA,
        "source_path": path_text,
        "source_sha256": current_sha,
        "source_bytes": source.stat().st_size,
        "status": "parsed" if total > 0 and parsed == total else "unsupported",
        "category": category,
        "category_group": group,
        "coverage": {
            "slides_in_deck": total,
            "slides_parsed": parsed,
            "slide_records_emitted": parsed,
            "full_slide_coverage": bool(total > 0 and parsed == total),
        },
        **measured,
        **profile_metrics(profile),
        "source_catalog": str(catalog_path),
        "source_catalog_schema": catalog.get("schema"),
        "source_detail_sha256": catalog.get("detail_sha256"),
        "candidate_only": True,
        "review_status": "machine_ooxml_unreviewed",
        "visual_verified": False,
        "rights_claim": None,
        "evidence_basis": "OOXML measurements bridged from source-bound style catalog and detail; no visual review; no rights assessment",
        "limits": [
            "OOXML measurement does not verify rendered visual quality or exact PowerPoint/WPS playback.",
            "Image meaning and licensing/usage rights are not assessed.",
            "Explicit font sizes omit inherited/theme sizes where the source catalog does not expose resolved values.",
        ],
    }
    return row, "included"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help="workspace root containing source folders")
    parser.add_argument("--output", type=Path, required=True, help="review-only candidate JSONL path")
    parser.add_argument("--catalog", type=Path, action="append", help="STYLE-CATALOG.jsonl; may be repeated")
    args = parser.parse_args()
    root = args.root.resolve()
    catalogs = args.catalog or [
        root / "模板库/创赛/分析索引/STYLE-CATALOG.jsonl",
        root / "模板库/BB0086 大学生职业生涯规划大赛 PPT/分析索引/STYLE-CATALOG.jsonl",
    ]
    output = args.output.resolve()
    curation = load_curation(root)
    counts: dict[str, int] = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as out:
        for catalog_path in catalogs:
            catalog_path = catalog_path.resolve()
            detail_root = catalog_path.parent / "逐份结构"
            for item in read_jsonl(catalog_path):
                row, reason = make_row(item, catalog_path, root, detail_root, curation)
                counts[reason] = counts.get(reason, 0) + 1
                if row is not None:
                    out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"output": str(output), "counts": counts}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
