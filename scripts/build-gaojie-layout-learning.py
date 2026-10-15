#!/usr/bin/env python3
"""Incrementally index every Gaojie source without changing category folders."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from aippt_studio.gaojie_layouts import CATEGORIES, GAOJIE, INDEX_VERSION, analysis_root, page_measurements, _sha
from aippt_studio.style_library import PARSER_VERSION, inspect_style_deck

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "模板库"
ARCHIVE = LIBRARY / GAOJIE
ANALYSIS = analysis_root(LIBRARY)


def read_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()] if path.is_file() else []


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    os.replace(temp, path)


def jsonl(path: Path, rows: list[dict]) -> None:
    write(path, "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows))


def index() -> dict:
    spec = importlib.util.spec_from_file_location("shared_style_index", ROOT / "scripts/build-style-library-index.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    ANALYSIS.mkdir(parents=True, exist_ok=True)
    detail_dir = ANALYSIS / "逐份结构"
    detail_dir.mkdir(exist_ok=True)
    receipts = {str(row["id"]): row for row in read_rows(ARCHIVE / "归档回执.jsonl")}
    curation = {row["path"]: row for row in read_rows(LIBRARY / "CONTENT-CURATION.jsonl") if str(row.get("path", "")).startswith(GAOJIE + "/")}
    all_paths = sorted(ARCHIVE.rglob("*.pptx"))
    catalog, pages, errors, canonical = [], [], [], {}
    hash_sources, hash_categories = defaultdict(list), defaultdict(set)
    manifest = []
    now = lambda: datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    for i, path in enumerate(all_paths, 1):
        relative = path.relative_to(LIBRARY).as_posix()
        category = path.parent.name
        if category not in CATEGORIES or path.parent.parent != ARCHIVE:
            raise ValueError("Unexpected source topology: " + relative)
        item_id = path.name.split(" - ", 1)[0]
        receipt = receipts.get(item_id, {})
        digest = _sha(path)
        meta = curation.get(relative, {})
        if meta.get("sha256") != digest:
            raise ValueError("Current source no longer matches curation: " + relative)
        manifest.append({"path": relative, "id": item_id, "category": category, "sha256": digest, "bytes": path.stat().st_size})
        if receipt.get("archive_status") not in {"archived", "deduplicated"} or meta.get("asset_type") != "template":
            catalog.append({"path": relative, "category": category, "resource_id": item_id, "source_sha256": digest,
                            "source_format": "pptx", "status": "excluded_corrupt_source", "slide_count": 0,
                            "errors": [receipt.get("reason") or "unavailable_source"], "asset_type": meta.get("asset_type")})
            continue
        hash_sources[digest].append(relative)
        hash_categories[digest].add(category)
        if digest not in canonical:
            detail_path = detail_dir / (digest + ".json")
            result = None
            if detail_path.is_file():
                try:
                    cache = json.loads(detail_path.read_text())
                    if cache.get("source_sha256") == digest and cache.get("parser_version") == PARSER_VERSION:
                        result = cache
                except (OSError, ValueError):
                    pass
            fresh = result is None
            if result is None:
                result = inspect_style_deck(path, relative)
            if (result.get("status") != "parsed" and
                    any("oversized_xml_part:" in error for error in result.get("errors", []))):
                # A measured 32.23 MB vector-heavy slide needs a bounded
                # per-task allowance. The default parser cap remains 32 MB.
                result = inspect_style_deck(path, relative, xml_part_limit=40_000_000)
                fresh = True
            if result.get("source_sha256") != digest:
                raise ValueError("Source changed while learning: " + relative)
            if fresh:
                write(detail_path, json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
            detail_ref = detail_path.relative_to(LIBRARY).as_posix()
            profile = helper._style_profile(result)
            stub = {"source_sha256": digest, "source_format": "pptx", "status": result.get("status"),
                    "slide_count": len(result.get("slides", [])), "errors": result.get("errors", [])}
            canonical[digest] = (result, stub, profile, relative, detail_ref, _sha(detail_path))
            if result.get("status") == "parsed":
                for slide in result["slides"]:
                    page = helper._page_row(result, slide, detail_ref, relative)
                    # A one-page component is not automatically a cover merely
                    # because it happens to be first in a presentation.
                    if len(result["slides"]) == 1:
                        page["role_evidence"] = [role for role in page.get("role_evidence", []) if role.get("basis") != "presentation_order_heuristic"]
                    page.update(schema="aippt.gaojie-style-page.v1", index_version=INDEX_VERSION,
                                page_status=slide.get("status"), layout_measurements=page_measurements(result, slide))
                    pages.append(page)
            else:
                errors.append({"source_sha256": digest, "path": relative, "errors": result.get("errors", [])})
        result, stub, profile, source_path, detail_ref, detail_hash = canonical[digest]
        row = helper._deck_row(stub, relative, detail_ref, detail_hash, profile, source_path, path.stat().st_size)
        row.update(index_version=INDEX_VERSION, collection="gaojie", category=category, resource_id=item_id,
                   category_function=CATEGORIES[category][0], asset_type="template")
        catalog.append(row)
        if i % 100 == 0 or i == len(all_paths):
            progress = {"status": "indexing", "processed_files": i, "target_files": len(all_paths),
                        "unique_source_sha256": len(canonical), "pages": len(pages), "updated_at": now()}
            write(ANALYSIS / "学习进度.json", json.dumps(progress, ensure_ascii=False, indent=2))
            print(json.dumps(progress, ensure_ascii=False), flush=True)
    for page in pages:
        categories = sorted(hash_categories[page["source_sha256"]])
        page["source_aliases"] = hash_sources[page["source_sha256"]]
        page["category_associations"] = categories
        page["role_evidence"].extend({"role": CATEGORIES[category][0], "basis": "website_category_function_candidate",
                                      "evidence": [category]} for category in categories)
    jsonl(ANALYSIS / "SOURCE-MANIFEST.jsonl", manifest)
    jsonl(ANALYSIS / "STYLE-CATALOG.jsonl", catalog)
    jsonl(ANALYSIS / "STYLE-PAGES.jsonl", pages)
    per_category = []
    for category, description in CATEGORIES.items():
        decks = [row for row in catalog if row["category"] == category]
        hashes = {row["source_sha256"] for row in decks if row["status"] == "parsed"}
        category_pages = [page for page in pages if page["source_sha256"] in hashes]
        per_category.append({"category": category, "function": description[0], "purpose": description[1],
                             "source_files": len(decks), "parsed_source_files": sum(row["status"] == "parsed" for row in decks),
                             "unique_parsed_sources": len(hashes), "page_records": len(category_pages),
                             "unique_structure_signatures": len({page["layout_measurements"]["structure_signature"] for page in category_pages}),
                             "layout_patterns": dict(Counter(page.get("layout_pattern", {}).get("kind", "unknown") for page in category_pages)),
                             "native_charts": sum(page.get("chart_count", 0) for page in category_pages),
                             "native_tables": sum(page.get("table_count", 0) for page in category_pages),
                             "status": "indexed_structure_needs_visual_learning" if hashes else "no_pptx_source",
                             "missing_source_reason": "用户仅收录PPTX，该类提供图片" if category == "素材图片" else "用户仅收录PPTX，该类提供PDF" if category == "发布会" else ""})
    report = {"schema": "aippt.gaojie-learning-build.v1", "index_version": INDEX_VERSION, "parser_version": PARSER_VERSION,
              "updated_at": now(), "source_files": len(all_paths), "unique_parsed_sources": sum(result[1]["status"] == "parsed" for result in canonical.values()),
              "parsed_source_files": sum(row["status"] == "parsed" for row in catalog),
              "unique_pages": len(pages), "excluded_corrupt_sources": sum(row["status"] == "excluded_corrupt_source" for row in catalog),
              "parse_errors": errors, "categories": per_category, "source_manifest_sha256": _sha(ANALYSIS / "SOURCE-MANIFEST.jsonl"),
              "visual_scope": "structure_only_until_source_images_are_observed"}
    write(ANALYSIS / "STYLE-BUILD-REPORT.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    write(ANALYSIS / "学习进度.json", json.dumps({"status": "structural_index_complete", **{key: report[key] for key in
          ("updated_at", "source_files", "parsed_source_files", "unique_parsed_sources", "unique_pages", "excluded_corrupt_sources")}}, ensure_ascii=False, indent=2))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", action="store_true", required=True)
    parser.parse_args()
    result = index()
    print(json.dumps({key: result[key] for key in ("source_files", "parsed_source_files", "unique_parsed_sources", "unique_pages", "excluded_corrupt_sources")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
