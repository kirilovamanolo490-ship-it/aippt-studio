#!/usr/bin/env python3
"""Read-only final accounting and PPTX integrity check for Gaojie downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "aippt-studio/.maintenance/gaojie-downloads"
INVENTORY = ROOT / "模板库/图表与视觉素材/版式与排版参考/高阶文化-待下载清单/资源清单.jsonl"
ARCHIVE_ROOT = ROOT / "模板库/高阶文化"
OUTPUT = STATE / "最终验收.json"
SOURCE_DAMAGE_EVIDENCE = STATE / "源文件损坏证据.json"
EXCLUDED_CATEGORY = "57"
IMAGE_EXTENSIONS = {".bmp", ".gif", ".heic", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp", ".avif", ".ico"}
REQUIRED_OOXML_PARTS = {
    "[Content_Types].xml",
    "_rels/.rels",
    "ppt/presentation.xml",
    "ppt/_rels/presentation.xml.rels",
}


def read_jsonl(path: Path) -> tuple[dict[str, dict], int, list[str]]:
    latest: dict[str, dict] = {}
    count = 0
    errors: list[str] = []
    if not path.exists():
        return latest, count, [f"missing_file:{path}"]
    with path.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            count += 1
            try:
                row = json.loads(line)
                item_id = str(row.get("id", "")).strip()
                if not isinstance(row, dict) or not item_id:
                    raise ValueError("row_must_be_object_with_id")
                latest[item_id] = row
            except Exception as exc:  # Preserve diagnostics for partially written live receipts.
                errors.append(f"{path}:{number}:{type(exc).__name__}:{exc}")
    return latest, count, errors


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_extension(value: object) -> str:
    raw = str(value or "").split("?", 1)[0].split("#", 1)[0]
    return Path(raw).suffix.lower()


def load_inventory() -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    by_id: dict[str, dict] = {}
    by_category: dict[str, dict] = {}
    errors: list[str] = []
    if not INVENTORY.exists():
        return by_id, by_category, [f"missing_file:{INVENTORY}"]
    with INVENTORY.open(encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                item_id = str(row.get("id", "")).strip()
                category_id = str(row.get("category_id", "")).strip()
                if not item_id or not category_id:
                    raise ValueError("row_missing_id_or_category_id")
                if category_id == EXCLUDED_CATEGORY:
                    continue
                if item_id in by_id:
                    errors.append(f"duplicate_inventory_id:{item_id}:line:{number}")
                    continue
                by_id[item_id] = row
                by_category.setdefault(category_id, row)
            except Exception as exc:
                errors.append(f"{INVENTORY}:{number}:{type(exc).__name__}:{exc}")
    return by_id, by_category, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Check receipt counts, IDs, paths, and file existence without hashing or opening PPTX files")
    parser.add_argument("--output", type=Path, default=OUTPUT, help="Machine-readable JSON output path")
    args = parser.parse_args()

    inventory, categories, parse_errors = load_inventory()
    details, detail_rows, errors = read_jsonl(STATE / "详情链接.jsonl")
    parse_errors.extend(errors)
    downloads, download_rows, errors = read_jsonl(STATE / "下载回执.jsonl")
    parse_errors.extend(errors)
    archives, archive_rows, errors = read_jsonl(ARCHIVE_ROOT / "归档回执.jsonl")
    parse_errors.extend(errors)

    source_damage: dict = {}
    if SOURCE_DAMAGE_EVIDENCE.exists():
        try:
            source_damage = json.loads(SOURCE_DAMAGE_EVIDENCE.read_text(encoding="utf-8-sig"))
            if not isinstance(source_damage, dict):
                raise ValueError("evidence_must_be_json_object")
        except Exception as exc:
            parse_errors.append(f"{SOURCE_DAMAGE_EVIDENCE}:{type(exc).__name__}:{exc}")
            source_damage = {}
    elif not args.quick:
        parse_errors.append(f"missing_file:{SOURCE_DAMAGE_EVIDENCE}")

    damage_item = source_damage.get("item", {}) if isinstance(source_damage, dict) else {}
    damage_id = str(damage_item.get("id", "")).strip() if isinstance(damage_item, dict) else ""
    expected_source_corrupt_ids = [damage_id] if damage_id else []
    physically_retained_corrupt_ids: set[str] = set()
    physically_retained_corrupt_paths: dict[str, str] = {}
    integrity_defects: list[dict] = []
    source_damage_report: list[dict] = []
    if damage_id:
        problem_list: list[str] = []
        evidence_item = inventory.get(damage_id)
        file_records = source_damage.get("files", [])
        if evidence_item is None:
            problem_list.append("id_not_in_inventory")
        else:
            if str(evidence_item.get("category_id", "")) != str(damage_item.get("category_id", "")):
                problem_list.append("inventory_category_id_mismatch")
            if str(evidence_item.get("category", "")).strip() != str(damage_item.get("category", "")).strip():
                problem_list.append("inventory_category_name_mismatch")
        if source_damage.get("source_download_reproduced") is not True:
            problem_list.append("source_download_not_confirmed_reproduced")
        if not isinstance(file_records, list) or len(file_records) != 2:
            problem_list.append("expected_original_and_recheck_file_records")
            file_records = file_records if isinstance(file_records, list) else []
        expected_source_paths = {
            str((STATE / "files" / f"{damage_id}.pptx").resolve()),
            str((STATE / "integrity-recheck" / "files" / f"{damage_id}.pptx").resolve()),
        }
        actual_source_paths = {str(Path(str(row.get("path", ""))).expanduser().resolve()) for row in file_records if isinstance(row, dict)}
        if actual_source_paths != expected_source_paths:
            problem_list.append("original_or_recheck_path_mismatch")
        evidence_hashes = {str(row.get("sha256", "")).lower() for row in file_records if isinstance(row, dict)}
        evidence_sizes = {int(row.get("bytes", 0) or 0) for row in file_records if isinstance(row, dict)}
        if len(evidence_hashes) != 1 or "" in evidence_hashes:
            problem_list.append("original_recheck_sha256_mismatch")
        if len(evidence_sizes) != 1 or 0 in evidence_sizes:
            problem_list.append("original_recheck_size_mismatch")
        if source_damage.get("retention_status") != "corrupt_source_preserved_without_modification":
            problem_list.append("retention_status_not_confirmed")
        evidence_retained_hash = str(source_damage.get("retained_sha256", "")).lower()
        evidence_retained_bytes = int(source_damage.get("retained_bytes", 0) or 0)
        if len(evidence_hashes) == 1 and evidence_retained_hash != next(iter(evidence_hashes)):
            problem_list.append("retained_evidence_sha256_mismatch")
        if len(evidence_sizes) == 1 and evidence_retained_bytes != next(iter(evidence_sizes)):
            problem_list.append("retained_evidence_size_mismatch")
        for file_record in file_records:
            if not isinstance(file_record, dict):
                problem_list.append("invalid_source_file_record")
                continue
            recorded_path = Path(str(file_record.get("path", ""))).expanduser()
            if not recorded_path.is_file():
                problem_list.append("source_evidence_file_missing:" + str(recorded_path))
            elif recorded_path.stat().st_size != int(file_record.get("bytes", 0) or 0):
                problem_list.append("source_evidence_size_changed:" + str(recorded_path))
            elif not args.quick and sha256(recorded_path).lower() != str(file_record.get("sha256", "")).lower():
                problem_list.append("source_evidence_sha256_changed:" + str(recorded_path))
        retained_raw = str(source_damage.get("retained_path", "")).strip()
        retained = Path(retained_raw).expanduser() if retained_raw else None
        retained_resolved: Path | None = None
        if retained is None:
            problem_list.append("retained_path_missing")
        else:
            try:
                retained_resolved = retained.resolve(strict=True)
                expected_dir = (ARCHIVE_ROOT / str(evidence_item.get("category", ""))).resolve() if evidence_item else None
                if expected_dir is None or retained_resolved.parent != expected_dir:
                    problem_list.append("retained_path_category_mismatch")
                if not retained_resolved.is_relative_to(ARCHIVE_ROOT.resolve()):
                    problem_list.append("retained_path_outside_archive_root")
                if retained_resolved.suffix.lower() != ".pptx":
                    problem_list.append("retained_file_extension_not_pptx")
                if "网站原文件损坏" not in retained_resolved.name:
                    problem_list.append("retained_filename_missing_damage_label")
                if not retained_resolved.is_file() or retained_resolved.stat().st_size <= 0:
                    problem_list.append("retained_file_missing_or_empty")
                expected_size = next(iter(evidence_sizes)) if len(evidence_sizes) == 1 else 0
                expected_hash = next(iter(evidence_hashes)) if len(evidence_hashes) == 1 else ""
                if retained_resolved.is_file():
                    if retained_resolved.stat().st_size != expected_size:
                        problem_list.append("retained_file_size_mismatch")
                    if retained_resolved.stat().st_size != evidence_retained_bytes:
                        problem_list.append("retained_file_vs_retention_record_size_mismatch")
                    if not args.quick and sha256(retained_resolved).lower() != expected_hash:
                        problem_list.append("retained_file_sha256_mismatch")
                    if not args.quick and sha256(retained_resolved).lower() != evidence_retained_hash:
                        problem_list.append("retained_file_vs_retention_record_sha256_mismatch")
                    if not args.quick:
                        try:
                            with zipfile.ZipFile(retained_resolved) as damaged_package:
                                bad_member = damaged_package.testzip()
                            if bad_member != "ppt/slides/slide15.xml":
                                problem_list.append("retained_damage_member_not_reproduced:" + str(bad_member))
                        except Exception as exc:
                            problem_list.append(f"retained_damage_zip_check_error:{type(exc).__name__}:{exc}")
            except OSError:
                problem_list.append("retained_file_missing")
        if args.quick:
            problem_list.append("quick_mode_skips_source_corruption_integrity_validation")
        retained_valid = not problem_list and retained_resolved is not None
        if retained_valid:
            physically_retained_corrupt_ids.add(damage_id)
            physically_retained_corrupt_paths[damage_id] = str(retained_resolved)
        source_damage_report.append({"id": damage_id, "retained_path": retained_raw, "retained_valid": retained_valid, "problems": problem_list})
        integrity_defects.append({
            "id": damage_id,
            "category_id": damage_item.get("category_id"),
            "type": "reproduced_source_pptx_corruption",
            "member": "ppt/slides/slide15.xml",
            "retained_path": retained_raw,
            "retained_valid": retained_valid,
            "source_download_reproduced": source_damage.get("source_download_reproduced") is True,
            "sha256": next(iter(evidence_hashes)) if len(evidence_hashes) == 1 else "",
            "problems": problem_list,
        })

    inventory_ids = set(inventory)
    category_names = {str(row.get("category", "")).strip() for row in categories.values() if str(row.get("category", "")).strip()}
    category_dirs = {name: (ARCHIVE_ROOT / name).is_dir() for name in sorted(category_names)}
    actual_directories = {path.name for path in ARCHIVE_ROOT.iterdir() if path.is_dir()} if ARCHIVE_ROOT.is_dir() else set()
    extra_category_directories = sorted(actual_directories - category_names)
    category_content_anomalies: list[dict] = []
    actual_pptx_paths: set[str] = set()
    for path in ARCHIVE_ROOT.rglob("*") if ARCHIVE_ROOT.is_dir() else []:
        if path.is_file() and path.suffix.lower() == ".pptx":
            try:
                actual_pptx_paths.add(str(path.resolve()))
            except OSError:
                actual_pptx_paths.add(str(path.absolute()))
    for category_name in category_names:
        folder = ARCHIVE_ROOT / category_name
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if path.is_dir():
                category_content_anomalies.append({"path": str(path), "kind": "nested_directory"})
            elif path.suffix.lower() != ".pptx":
                category_content_anomalies.append({"path": str(path), "kind": "non_pptx_file", "extension": path.suffix.lower()})
    unknown_ids = {
        "details": sorted(set(details) - inventory_ids),
        "downloads": sorted(set(downloads) - inventory_ids),
        "archives": sorted(set(archives) - inventory_ids),
    }

    browser_review: list[dict] = []
    no_attachment: list[dict] = []
    unresolved: list[dict] = []
    download_failures: list[dict] = []
    archive_failures: list[dict] = []
    pending_download: list[dict] = []
    pending_archive: list[dict] = []
    image_attachments: list[dict] = []
    pdf_attachments: list[dict] = []
    non_pptx_extensions: list[dict] = []
    archive_integrity_failures: list[dict] = []
    valid_archive_ids: set[str] = set()
    valid_image_ids: set[str] = set()
    valid_pdf_ids: set[str] = set()
    valid_no_attachment_ids: set[str] = set()
    archive_status_counts: dict[str, int] = {}
    archive_path_ids: dict[str, list[str]] = {}

    for item_id, row in details.items():
        status = str(row.get("status", ""))
        if status in {"needs_browser_verification", "unreviewed_detail_redirect", "download_label_changed"}:
            browser_review.append({"id": item_id, "category_id": row.get("category_id"), "status": status, "evidence_file": row.get("evidence_file", "")})
        elif status == "no_attachment":
            evidence_rel = str(row.get("evidence_file", ""))
            evidence_path = (STATE / evidence_rel).resolve() if evidence_rel else None
            verified = str(row.get("verification", "")).startswith("normal_product_html")
            evidence_exists = bool(evidence_path and evidence_path.is_file() and evidence_path.is_relative_to(STATE.resolve()))
            no_attachment.append({"id": item_id, "category_id": row.get("category_id"), "verification": row.get("verification", ""), "evidence_file": evidence_rel, "evidence_exists": evidence_exists})
            if verified and evidence_exists:
                valid_no_attachment_ids.add(item_id)
        elif status not in {"resolved", "", "stopped"}:
            unresolved.append({"id": item_id, "category_id": row.get("category_id"), "status": status})
        if status == "resolved":
            ext = file_extension(row.get("url", ""))
            if ext and ext != ".pptx":
                non_pptx_extensions.append({"id": item_id, "category_id": row.get("category_id"), "extension": ext, "source": "detail_url_field", "url": row.get("url", "")})

    for item_id, row in downloads.items():
        status = str(row.get("status", ""))
        ext = str(row.get("extension") or file_extension(row.get("download_url") or row.get("url", ""))).lower()
        if ext and ext != ".pptx":
            non_pptx_extensions.append({"id": item_id, "category_id": row.get("category_id"), "extension": ext, "status": status, "source": "download_receipt", "url": row.get("download_url") or row.get("url", "")})
        if status == "excluded_non_pptx" and ext in IMAGE_EXTENSIONS:
            image_attachments.append({"id": item_id, "category_id": row.get("category_id"), "extension": ext, "url": row.get("url", "")})
            valid_image_ids.add(item_id)
        elif status == "excluded_non_pptx" and ext == ".pdf":
            detail = details.get(item_id, {})
            item = inventory.get(item_id, {})
            receipt_url = str(row.get("download_url") or row.get("url", ""))
            detail_url = str(detail.get("url", ""))
            valid_pdf = (
                detail.get("status") == "resolved"
                and bool(receipt_url)
                and receipt_url == detail_url
                and file_extension(receipt_url) == ".pdf"
                and str(row.get("category_id", "")) == str(item.get("category_id", ""))
                and str(detail.get("category_id", "")) == str(item.get("category_id", ""))
            )
            if valid_pdf:
                pdf_attachments.append({"id": item_id, "category_id": row.get("category_id"), "extension": ext, "url": receipt_url, "verification": "resolved_detail_url_equals_excluded_download_receipt_url_pdf"})
                valid_pdf_ids.add(item_id)
            else:
                download_failures.append({"id": item_id, "category_id": row.get("category_id"), "status": "unverified_pdf_exclusion", "url": receipt_url, "detail_url": detail_url})
        elif status not in {"downloaded", ""}:
            download_failures.append({"id": item_id, "category_id": row.get("category_id"), "status": status, "error": row.get("error", ""), "url": row.get("url", "")})
        elif status == "downloaded" and item_id not in archives:
            pending_archive.append({"id": item_id, "category_id": row.get("category_id"), "local_path": row.get("local_path", "")})

    for item_id, row in archives.items():
        status = str(row.get("archive_status", ""))
        archive_status_counts[status] = archive_status_counts.get(status, 0) + 1
        if status not in {"archived", "deduplicated"}:
            archive_failures.append({"id": item_id, "category_id": row.get("category_id"), "archive_status": status, "reason": row.get("reason", "")})
            continue
        item = inventory.get(item_id)
        expected_category = str(item.get("category", "")).strip() if item else ""
        expected_category_id = str(item.get("category_id", "")) if item else ""
        target_raw = str(row.get("archived_path", ""))
        target = Path(target_raw).expanduser()
        if target_raw and target.suffix.lower() == ".pptx":
            try:
                normalized_target = str(target.resolve())
            except OSError:
                normalized_target = str(target.absolute())
            archive_path_ids.setdefault(normalized_target, []).append(item_id)
        problems: list[str] = []
        if item is None:
            problems.append("id_not_in_inventory")
        if str(row.get("category_id", "")) != expected_category_id:
            problems.append("category_id_mismatch")
        if str(row.get("category", "")).strip() != expected_category:
            problems.append("category_name_mismatch")
        if target.suffix.lower() != ".pptx":
            problems.append("archive_extension_not_pptx")
        if not target.name.startswith(f"{item_id} - "):
            problems.append("archive_filename_id_mismatch")
        try:
            resolved = target.resolve(strict=True)
            expected_dir = (ARCHIVE_ROOT / expected_category).resolve()
            if resolved.parent != expected_dir:
                problems.append("archive_path_category_mismatch")
            if not resolved.is_file():
                problems.append("archive_path_not_file")
            elif resolved.stat().st_size <= 0:
                problems.append("archive_file_empty")
            if not resolved.is_relative_to(ARCHIVE_ROOT.resolve()):
                problems.append("archive_path_outside_root")
        except OSError:
            resolved = None
            problems.append("archive_file_missing")
        if resolved is not None and resolved.is_file() and resolved.stat().st_size > 0 and not args.quick:
            try:
                actual_hash = sha256(resolved)
                if actual_hash.lower() != str(row.get("sha256", "")).lower():
                    problems.append("archive_sha256_mismatch")
                if int(row.get("bytes", 0) or 0) != resolved.stat().st_size:
                    problems.append("archive_bytes_mismatch")
                if not zipfile.is_zipfile(resolved):
                    problems.append("not_a_zip_package")
                else:
                    with zipfile.ZipFile(resolved) as package:
                        names = set(package.namelist())
                        missing_parts = sorted(REQUIRED_OOXML_PARTS - names)
                        if missing_parts:
                            problems.append("missing_ooxml_parts:" + ",".join(missing_parts))
                        corrupt_part = package.testzip()
                        if corrupt_part:
                            problems.append("corrupt_zip_member:" + corrupt_part)
            except Exception as exc:
                problems.append(f"integrity_check_error:{type(exc).__name__}:{exc}")
        if problems:
            archive_integrity_failures.append({"id": item_id, "category_id": row.get("category_id"), "archived_path": target_raw, "problems": problems})
        else:
            valid_archive_ids.add(item_id)

    for item_id, row in details.items():
        if item_id not in inventory_ids:
            continue
        status = str(row.get("status", ""))
        if status in {"", "stopped"}:
            unresolved.append({"id": item_id, "category_id": row.get("category_id"), "status": status or "missing_status"})
        if status == "resolved" and item_id not in downloads and item_id not in valid_archive_ids:
            pending_download.append({"id": item_id, "category_id": row.get("category_id"), "url": row.get("url", "")})
    for item_id in sorted(inventory_ids - set(details)):
        item = inventory[item_id]
        unresolved.append({"id": item_id, "category_id": item.get("category_id"), "status": "missing_detail_receipt"})

    for item_id, row in downloads.items():
        if row.get("status") == "downloaded" and item_id not in archives:
            if not any(entry["id"] == item_id for entry in pending_archive):
                pending_archive.append({"id": item_id, "category_id": row.get("category_id"), "local_path": row.get("local_path", "")})

    accounted_ids = valid_archive_ids | valid_image_ids | valid_pdf_ids | valid_no_attachment_ids | physically_retained_corrupt_ids
    unaccounted_ids = sorted(inventory_ids - accounted_ids)
    non_image_non_pptx = [
        x for x in non_pptx_extensions
        if x["extension"] not in IMAGE_EXTENSIONS and not (x["extension"] == ".pdf" and x["id"] in valid_pdf_ids)
    ]
    unexpected_archive_files = []
    for category_name in category_names:
        folder = ARCHIVE_ROOT / category_name
        if folder.is_dir():
            for path in folder.rglob("*"):
                if path.is_file() and path.suffix.lower() in {".ppt", ".zip"}:
                    unexpected_archive_files.append(str(path))
    accounting_path_ids = {path: list(ids) for path, ids in archive_path_ids.items()}
    for item_id, path in physically_retained_corrupt_paths.items():
        accounting_path_ids.setdefault(path, []).append(item_id)
    receipt_pptx_paths = set(accounting_path_ids)
    orphan_pptx_paths = sorted(actual_pptx_paths - receipt_pptx_paths)
    receipt_paths_not_found = sorted(receipt_pptx_paths - actual_pptx_paths)
    duplicate_receipt_paths = [
        {"path": path, "ids": sorted(ids)}
        for path, ids in sorted(accounting_path_ids.items()) if len(set(ids)) > 1
    ]

    full_categories_present = len(categories) == 31 and all(category_dirs.values()) and actual_directories == category_names
    permitted_retained_archive_failure_ids = physically_retained_corrupt_ids
    unaccounted_archive_failures = [row for row in archive_failures if str(row.get("id")) not in permitted_retained_archive_failure_ids]
    unaccounted_integrity_failures = [row for row in archive_integrity_failures if str(row.get("id")) not in permitted_retained_archive_failure_ids]
    path_and_directory_checks_pass = (
        full_categories_present
        and not category_content_anomalies
        and not orphan_pptx_paths
        and not receipt_paths_not_found
        and not duplicate_receipt_paths
        and not unexpected_archive_files
    )
    all_requested_resources_accounted = (
        not args.quick
        and len(inventory_ids) == 6578
        and path_and_directory_checks_pass
        and not parse_errors
        and not any(unknown_ids.values())
        and not unresolved and not browser_review and not download_failures
        and not pending_download and not pending_archive
        and not unaccounted_archive_failures and not unaccounted_integrity_failures
        and not non_image_non_pptx
        and accounted_ids == inventory_ids
    )
    complete = (
        not args.quick
        and len(inventory_ids) == 6578
        and full_categories_present
        and not parse_errors
        and not unknown_ids["details"] and not unknown_ids["downloads"] and not unknown_ids["archives"]
        and not unresolved and not browser_review and not download_failures
        and not pending_download and not pending_archive and not archive_failures
        and not archive_integrity_failures and not integrity_defects and not non_image_non_pptx
        and not unexpected_archive_files
        and not extra_category_directories and not category_content_anomalies
        and not orphan_pptx_paths and not receipt_paths_not_found and not duplicate_receipt_paths
        and accounted_ids == inventory_ids
    )
    result = {
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
        "mode": "quick" if args.quick else "full",
        "available_pptx_complete": complete,
        "all_requested_resources_accounted": all_requested_resources_accounted,
        "completion_note": "quick_mode_skips_pptx_integrity_checks" if args.quick else "",
        "expected": {"resource_ids_excluding_category_57": 6578, "website_categories": 31},
        "counts": {
            "inventory_ids": len(inventory_ids),
            "categories": len(categories),
            "category_folders_present": sum(category_dirs.values()),
            "actual_category_directories": len(actual_directories),
            "actual_pptx_paths": len(actual_pptx_paths),
            "receipt_pptx_paths": len(receipt_pptx_paths),
            "detail_receipt_rows": detail_rows,
            "detail_latest_ids": len(details),
            "download_receipt_rows": download_rows,
            "download_latest_ids": len(downloads),
            "archive_receipt_rows": archive_rows,
            "archive_latest_ids": len(archives),
            "archive_statuses": archive_status_counts,
            "archive_paths_id_category_size_checked": len(valid_archive_ids),
            "pptx_archives_integrity_verified": 0 if args.quick else len(valid_archive_ids),
            "expected_source_corrupt_count": len(expected_source_corrupt_ids),
            "physically_retained_corrupt_count": len(physically_retained_corrupt_ids),
            "verified_image_attachments": len(valid_image_ids),
            "verified_pdf_attachments": len(valid_pdf_ids),
            "verified_normal_page_no_attachment": len(valid_no_attachment_ids),
        },
        "missing_category_folders": sorted(name for name, present in category_dirs.items() if not present),
        "extra_category_directories": extra_category_directories,
        "category_content_anomalies": category_content_anomalies,
        "parse_errors": parse_errors,
        "unknown_receipt_ids": unknown_ids,
        "not_processed_or_unresolved": unresolved,
        "download_failures": download_failures,
        "needs_browser_verification": browser_review,
        "normal_page_no_attachment": no_attachment,
        "verified_image_attachments": image_attachments,
        "verified_pdf_attachments": pdf_attachments,
        "pending_download": pending_download,
        "pending_archive": pending_archive,
        "archive_failures": archive_failures,
        "archive_integrity_failures": archive_integrity_failures,
        "integrity_defects": integrity_defects,
        "source_corruption_evidence_validation": source_damage_report,
        "expected_source_corrupt_ids": expected_source_corrupt_ids,
        "physically_retained_corrupt_ids": sorted(physically_retained_corrupt_ids),
        "unaccounted_archive_failures": unaccounted_archive_failures,
        "unaccounted_integrity_failures": unaccounted_integrity_failures,
        "non_pptx_extensions": non_pptx_extensions,
        "non_image_non_pptx_extensions": non_image_non_pptx,
        "unexpected_archive_ppt_or_zip_files": unexpected_archive_files,
        "orphan_pptx_paths_without_archive_or_retention_evidence": orphan_pptx_paths,
        "archive_or_retention_paths_missing_from_tree": receipt_paths_not_found,
        "multiple_ids_for_same_accounting_path": duplicate_receipt_paths,
        "unaccounted_inventory_ids": unaccounted_ids,
        "archive_paths_checked": "existence_id_category_extension_and_size" if args.quick else "existence_id_category_extension_size_sha256_zip_testzip_required_ooxml_parts",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, args.output)
    print(json.dumps({"available_pptx_complete": complete, "all_requested_resources_accounted": all_requested_resources_accounted, "mode": result["mode"], "counts": result["counts"], "pending_or_unresolved": len(unresolved) + len(pending_download) + len(pending_archive), "download_failures": len(download_failures), "needs_browser_verification": len(browser_review), "archive_failures": len(archive_failures), "integrity_defects": len(integrity_defects), "result": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
