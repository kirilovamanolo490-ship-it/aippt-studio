#!/usr/bin/env python3
"""Build a reviewable CONTENT-CURATION candidate from Gaojie archive receipts.

This script performs no downloads and never edits CONTENT-CURATION.jsonl or
CATALOG.jsonl. It verifies archived paths and SHA-256 values, preserves all
existing curation rows verbatim, and writes a complete merged candidate to an
explicit output path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile


PROJECT = Path(__file__).resolve().parents[1]
LIBRARY = PROJECT / "模板库"
ARCHIVE = LIBRARY / "高阶文化"
INVENTORY = LIBRARY / "图表与视觉素材" / "版式与排版参考" / "高阶文化-待下载清单" / "资源清单.jsonl"
CURATION = LIBRARY / "CONTENT-CURATION.jsonl"
EXCLUDED_CATEGORY_ID = "57"
ARCHIVED_STATUSES = {"archived", "deduplicated"}


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            row["_line"] = line_number
            rows.append(row)
    return rows


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def relative_library_path(path: Path) -> str:
    resolved = path.resolve(strict=True)
    library = LIBRARY.resolve(strict=True)
    try:
        return resolved.relative_to(library).as_posix()
    except ValueError as exc:
        raise ValueError(f"archived_path escapes 模板库: {path}") from exc


def validate_curation_rows(rows: list[dict], path: Path) -> dict[str, dict]:
    by_path: dict[str, dict] = {}
    for line_number, row in enumerate(rows, 1):
        rel = row.get("path")
        if not isinstance(rel, str) or not rel or rel.startswith(("/", "~")) or "\\" in rel or ":" in rel or ".." in Path(rel).parts:
            raise ValueError(f"invalid curation path on {path}:{line_number}")
        if not all(isinstance(row.get(key), str) and row[key].strip()
                   for key in ("category", "display_name", "asset_type")):
            raise ValueError(f"missing curation semantic fields on {path}:{line_number}")
        if rel in by_path:
            raise ValueError(f"duplicate curation path on {path}:{line_number}: {rel}")
        by_path[rel] = row
    return by_path


def asset_type_for(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in {".pptx", ".potx"}:
        return "template"
    if suffix in {".pdf", ".ppt", ".ppsx", ".pptm", ".potm", ".pps"}:
        return "reference"
    return "diagram_asset"


def source_entry(receipt: dict, item: dict, rel: str, digest: str) -> dict:
    category = str(item.get("category") or "未分类").strip()
    group = str(item.get("group") or "未分组").strip()
    title = str(item.get("title") or Path(rel).stem).strip()
    suffix = Path(rel).suffix.casefold()
    return {
        "path": rel,
        "sha256": digest,
        "display_name": title,
        "category": category,
        "subcategory": group,
        "asset_type": asset_type_for(Path(rel)),
        "confidence": "low",
        "review_status": "not_visually_reviewed",
        "evidence_basis": "website_category_and_verified_archive_receipt",
        "evidence_ref": "高阶文化/归档回执.jsonl",
        "occasion_tags": list(dict.fromkeys([category, group])),
        "search_terms": list(dict.fromkeys([category, group, title])),
        "page_role_tags": [],
        "style_tags": [],
        "evidence": {
            "source_type": "高阶文化网站资源目录",
            "website_resource_id": str(item.get("id", "")),
            "website_category_id": str(item.get("category_id", "")),
            "website_category": category,
            "archive_group": group,
            "source_url": item.get("detail_url") or item.get("source") or receipt.get("url", ""),
            "archive_receipt_url": receipt.get("url", ""),
            "archive_status": receipt.get("archive_status", ""),
            "archive_format_check": receipt.get("format_check") or receipt.get("format_note", ""),
            "visual_review": "not_reviewed",
            "usage_basis": "网站分类与归档目录仅用于检索候选；实际页面内容、模板适配性及素材权利待核实。",
        },
    }


def build_candidate(receipt_path: Path, inventory_path: Path, archive_root: Path,
                    existing_path: Path) -> tuple[list[dict], dict[str, int]]:
    inventory_rows = read_jsonl(inventory_path)
    inventory: dict[str, dict] = {}
    for row in inventory_rows:
        item_id = str(row.get("id", "")).strip()
        if not item_id or item_id in inventory:
            raise ValueError(f"missing or duplicate inventory id: {item_id!r}")
        inventory[item_id] = row

    existing_rows = read_jsonl(existing_path) if existing_path.is_file() else []
    for row in existing_rows:
        row.pop("_line", None)
    existing = validate_curation_rows(existing_rows, existing_path)

    receipts = read_jsonl(receipt_path)
    candidates: dict[str, dict] = {}
    counts = {"receipt_rows": len(receipts), "registered": 0, "already_curated": 0,
              "excluded_category_57": 0, "ignored_unarchived": 0}
    archive_resolved = archive_root.resolve(strict=True)

    for receipt in receipts:
        line_number = receipt.pop("_line")
        item_id = str(receipt.get("id", "")).strip()
        item = inventory.get(item_id)
        category_id = str(receipt.get("category_id", "")).strip()
        if category_id == EXCLUDED_CATEGORY_ID or (item and str(item.get("category_id", "")) == EXCLUDED_CATEGORY_ID):
            counts["excluded_category_57"] += 1
            continue
        if receipt.get("archive_status") not in ARCHIVED_STATUSES:
            counts["ignored_unarchived"] += 1
            continue
        if item is None:
            raise ValueError(f"{receipt_path}:{line_number}: id {item_id!r} is absent from inventory")
        if category_id != str(item.get("category_id", "")):
            raise ValueError(f"{receipt_path}:{line_number}: category_id mismatch for id {item_id}")
        for field in ("group", "category"):
            if str(receipt.get(field, "")) != str(item.get(field, "")):
                raise ValueError(f"{receipt_path}:{line_number}: {field} mismatch for id {item_id}")
        archived_value = receipt.get("archived_path")
        if not isinstance(archived_value, str) or not archived_value:
            raise ValueError(f"{receipt_path}:{line_number}: missing archived_path")
        archived_path = Path(archived_value)
        if not archived_path.is_absolute():
            archived_path = archive_root / archived_path
        resolved = archived_path.resolve(strict=True)
        if not resolved.is_file() or (resolved != archive_resolved and archive_resolved not in resolved.parents):
            raise ValueError(f"{receipt_path}:{line_number}: archived_path is not a file inside archive root")
        expected_parent = archive_resolved / str(item['category'])
        if resolved.parent != expected_parent:
            raise ValueError(f"{receipt_path}:{line_number}: archived file is outside its inventory category")
        format_check = str(receipt.get('format_check') or receipt.get('format_note', ''))
        if not format_check.startswith('valid_'):
            raise ValueError(f"{receipt_path}:{line_number}: successful format check missing")
        digest = str(receipt.get("sha256", "")).lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError(f"{receipt_path}:{line_number}: invalid SHA-256")
        if sha256(resolved) != digest:
            raise ValueError(f"{receipt_path}:{line_number}: SHA-256 mismatch: {resolved}")

        rel = relative_library_path(resolved)
        if rel in existing:
            current_digest = str(existing[rel].get("sha256", "")).lower()
            if current_digest and current_digest != digest:
                raise ValueError(f"existing curation SHA-256 mismatch for {rel}")
            counts["already_curated"] += 1
            continue
        entry = source_entry(receipt, item, rel, digest)
        prior = candidates.get(rel)
        if prior and prior["sha256"] != digest:
            raise ValueError(f"multiple receipts map different bytes to {rel}")
        candidates[rel] = entry

    for rel, entry in candidates.items():
        if rel in existing:
            raise ValueError(f"candidate duplicates existing curation path: {rel}")
        existing_rows.append(entry)
    counts["registered"] = len(candidates)
    return existing_rows, counts


def write_jsonl_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            for row in rows:
                stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="从高阶文化归档回执生成待审核的 CONTENT-CURATION 候选文件，不更新正式 catalog。")
    parser.add_argument("--receipt", type=Path, help="归档回执 JSONL（默认归档根目录/归档回执.jsonl）")
    parser.add_argument("--inventory", type=Path, default=INVENTORY, help="高阶文化网站资源清单 JSONL")
    parser.add_argument("--archive-root", type=Path, default=ARCHIVE, help="已归档文件根目录")
    parser.add_argument("--existing", type=Path, default=CURATION, help="现有 CONTENT-CURATION.jsonl，只读并完整保留")
    parser.add_argument("--output", type=Path, required=True, help="候选 JSONL 输出路径；必须与正式 CONTENT-CURATION 不同")
    args = parser.parse_args()
    receipt_path = args.receipt or args.archive_root / "归档回执.jsonl"
    protected_inputs = [receipt_path, args.inventory, args.existing,
                        LIBRARY / "CONTENT-CURATION.jsonl", LIBRARY / "CATALOG.jsonl"]
    if args.output.resolve() in {path.resolve() for path in protected_inputs}:
        parser.error("--output cannot overwrite an input, CONTENT-CURATION.jsonl, or CATALOG.jsonl")
    try:
        rows, counts = build_candidate(receipt_path, args.inventory, args.archive_root, args.existing)
        write_jsonl_atomic(args.output, rows)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "existing_and_candidate_rows": len(rows), **counts}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
