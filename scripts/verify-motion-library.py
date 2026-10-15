"""Recheck the current dynamic collection, indexes, catalog and static PNGs."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile


PROJECT = Path(__file__).resolve().parents[1]
LIBRARY = PROJECT / "模板库"
COLLECTION = LIBRARY / "镶金边高端动态ppt"
ANALYSIS = COLLECTION / "分析索引"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def verify() -> dict:
    errors: list[str] = []
    decks = _rows(ANALYSIS / "MOTION-CATALOG.jsonl")
    pages = _rows(ANALYSIS / "MOTION-INDEX.jsonl")
    curation = {row["path"]: row for row in _rows(LIBRARY / "CONTENT-CURATION.jsonl")}
    catalog = {row["path"]: row for row in _rows(LIBRARY / "CATALOG.jsonl")}
    unique_paths = {row["path"] for row in decks}
    actual = {path.relative_to(LIBRARY).as_posix(): path
              for path in COLLECTION.rglob("*.pptx") if path.is_file() and ANALYSIS not in path.parents}
    if len(decks) != len(unique_paths) or set(actual) != unique_paths:
        errors.append("source_catalog_path_coverage_mismatch")
    page_by_path: dict[str, list[dict]] = defaultdict(list)
    for row in pages:
        page_by_path[row.get("path", "")].append(row)
    if set(page_by_path) != unique_paths:
        errors.append("slide_index_path_coverage_mismatch")
    seen_details = set()
    zip_crc_errors = []
    for row in decks:
        rel = row["path"]
        source = actual.get(rel)
        if not source:
            continue
        digest = _sha(source)
        if digest != row.get("source_sha256"):
            errors.append(f"source_hash_mismatch:{rel}")
        for index, expected in ((curation, "sha256"), (catalog, "source_sha256")):
            if rel not in index or index[rel].get(expected) != digest:
                errors.append(f"semantic_catalog_mismatch:{rel}")
        indexed = page_by_path.get(rel, [])
        if len(indexed) != row["slide_count"] or sorted(item.get("presentation_order") for item in indexed) != list(range(1, row["slide_count"] + 1)):
            errors.append(f"page_count_or_order_mismatch:{rel}")
        if any(item.get("source_sha256") != digest or item.get("playback_status") != "not_verified" for item in indexed):
            errors.append(f"page_source_or_playback_claim_mismatch:{rel}")
        detail = LIBRARY / row["detail_ref"]
        if detail not in seen_details:
            seen_details.add(detail)
            if not detail.is_file() or _sha(detail) != row.get("detail_sha256"):
                errors.append(f"detail_hash_mismatch:{rel}")
        readable = LIBRARY / row["readable_ref"]
        if not readable.is_file():
            errors.append(f"readable_report_missing:{rel}")
        try:
            with ZipFile(source) as archive:
                bad = archive.testzip()
            if bad:
                zip_crc_errors.append(f"{rel}:{bad}")
        except Exception as error:
            zip_crc_errors.append(f"{rel}:{type(error).__name__}:{error}")
    if zip_crc_errors:
        errors.extend(f"source_zip_crc:{item}" for item in zip_crc_errors)
    build = json.loads((ANALYSIS / "BUILD-REPORT.json").read_text(encoding="utf-8"))
    if build.get("indexed_decks") != len(decks) or build.get("indexed_slides") != len(pages) or build.get("failures"):
        errors.append("build_report_mismatch")
    preview = json.loads((ANALYSIS / "分析预览/PREVIEW-MANIFEST.json").read_text(encoding="utf-8"))
    checked_png = 0
    for row in preview.get("rendered_decks", []):
        rel = "镶金边高端动态ppt/" + row["relative_path"]
        if rel not in actual or row.get("source_sha256") != _sha(actual[rel]):
            errors.append(f"preview_source_mismatch:{rel}")
            continue
        for item in row.get("rendered_slides", []):
            image = ANALYSIS / "分析预览" / row["output_directory"] / item["file"]
            if not image.is_file() or image.stat().st_size != item["bytes"] or _sha(image) != item["sha256"]:
                errors.append(f"preview_png_mismatch:{rel}:{item['slide']}")
            else:
                checked_png += 1
    fallback_packet = json.loads((ANALYSIS / "分析预览/fallback-single/FALLBACK-MANIFEST.json").read_text(encoding="utf-8"))
    checked_fallback = 0
    for row in fallback_packet.get("results", []):
        if row.get("status") != "rendered_first_page":
            continue
        rel = "镶金边高端动态ppt/" + row["source"]
        image = ANALYSIS / "分析预览" / row["output"]
        receipt = ANALYSIS / "分析预览" / row["receipt"]
        if not image.is_file() or not receipt.is_file() or rel not in actual:
            errors.append(f"fallback_preview_missing:{rel}")
            continue
        record = json.loads(receipt.read_text(encoding="utf-8"))
        if (record.get("source_sha256") != row.get("source_sha256")
                or record.get("source_sha256") != _sha(actual[rel])
                or record.get("output_sha256") != _sha(image)
                or record.get("slide_number") != 1):
            errors.append(f"fallback_preview_hash_mismatch:{rel}")
        else:
            checked_fallback += 1
    resources = list(csv.DictReader((ANALYSIS / "CURRENT-RESOURCE-MANIFEST.tsv").open(encoding="utf-8"), delimiter="\t"))
    resource_paths = {row["path"] for row in resources}
    current_resources = {p.relative_to(LIBRARY).as_posix() for p in COLLECTION.rglob("*")
                         if p.is_file() and ANALYSIS not in p.parents and p.name != ".DS_Store"}
    if resource_paths != current_resources:
        errors.append("current_resource_manifest_path_coverage_mismatch")
    for row in resources:
        source = LIBRARY / row["path"]
        if not source.is_file() or source.stat().st_size != int(row["bytes"]) or _sha(source) != row["sha256"]:
            errors.append(f"resource_hash_mismatch:{row['path']}")
    report = {
        "schema": "aippt.motion-library-verification.v1",
        "status": "passed" if not errors else "failed",
        "source_pptx": len(decks), "source_slides": len(pages),
        "unique_source_hashes": len({row["source_sha256"] for row in decks}),
        "detail_json_checked": len(seen_details), "static_png_checked": checked_png,
        "fallback_first_page_png_checked": checked_fallback,
        "resource_files_checked": len(resources),
        "motion_modes": dict(Counter(mode for row in pages for mode in row.get("motion_modes", []))),
        "errors": errors,
        "boundary": "Source ZIP/OOXML and static-preview integrity only; PowerPoint playback is not globally verified.",
    }
    (ANALYSIS / "QA-REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    result = verify()
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "passed" else 1)
