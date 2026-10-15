#!/usr/bin/env python3
"""Archive already-downloaded Gaojie files using a JSONL receipt.

No network access is performed. Receipt schema (one JSON object per line)::

    {"id":"7933", "local_path":"/path/file.pptx", "url":"https://...",
     "category_id":"104", "status":"downloaded"}

`status` is optional and defaults to downloaded. Only nonempty, recognized
PPTX files with valid OOXML structure are accepted. Every resource gets a path
in its own category; duplicate bytes use
hard links where available. Re-running is safe: filename conflicts are
reported and never overwritten, and prior receipt hashes avoid a tree scan.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
INVENTORY = PROJECT / "模板库" / "图表与视觉素材" / "版式与排版参考" / "高阶文化-待下载清单" / "资源清单.jsonl"
ARCHIVE = PROJECT / "模板库" / "高阶文化"
RECEIPT_OUT = "归档回执.jsonl"
SUMMARY_OUT = "归档汇总.csv"
EXCLUDED_CATEGORY_ID = "57"


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip().rstrip(".")
    value = re.sub(r"\s+", " ", value)
    return (value[:160].rstrip() or "无标题")


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: JSON 无效: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{number}: 每行必须是 JSON 对象")
            row["_line"] = number
            rows.append(row)
    return rows


def load_inventory(path: Path) -> dict[str, dict]:
    rows = read_jsonl(path)
    by_id: dict[str, dict] = {}
    for row in rows:
        item_id = str(row.get("id", "")).strip()
        if not item_id:
            raise ValueError(f"{path}:{row['_line']}: 清单资源缺少 id")
        if item_id in by_id:
            raise ValueError(f"清单中 id 重复: {item_id}")
        by_id[item_id] = row
    return by_id


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def valid_format(path: Path) -> tuple[bool, str]:
    """Accept only nonempty PPTX files with intact OOXML structure."""
    try:
        if path.stat().st_size <= 0:
            return False, "empty_file"
        with path.open("rb") as stream:
            head = stream.read(16)
        ext = path.suffix.lower()
        if ext != ".pptx":
            return False, f"unsupported_extension:{ext or '(none)'}"
        if ext == ".pptx":
            if not zipfile.is_zipfile(path):
                return False, "invalid_ooxml_zip"
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" not in names or "ppt/presentation.xml" not in names:
                    return False, "zip_missing_powerpoint_parts"
                bad = archive.testzip()
                if bad:
                    return False, f"corrupt_zip_member:{bad}"
            return True, "valid_powerpoint_ooxml"
        if ext in {".ppt", ".pot", ".pps"}:
            return (head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")), "valid_ole_ppt" if head.startswith(bytes.fromhex("D0CF11E0A1B11AE1")) else "invalid_ole_header")
        signatures = {
            ".pdf": lambda b: b.startswith(b"%PDF-"),
            ".png": lambda b: b.startswith(b"\x89PNG\r\n\x1a\n"),
            ".jpg": lambda b: b.startswith(b"\xff\xd8\xff"),
            ".jpeg": lambda b: b.startswith(b"\xff\xd8\xff"),
            ".gif": lambda b: b.startswith((b"GIF87a", b"GIF89a")),
            ".webp": lambda b: len(b) >= 12 and b[:4] == b"RIFF" and b[8:12] == b"WEBP",
            ".bmp": lambda b: b.startswith(b"BM"),
            ".tif": lambda b: b.startswith((b"II*\x00", b"MM\x00*")),
            ".tiff": lambda b: b.startswith((b"II*\x00", b"MM\x00*")),
            ".ico": lambda b: b.startswith(b"\x00\x00\x01\x00"),
            ".zip": lambda b: b.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")),
            ".rar": lambda b: b.startswith((b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")),
            ".avif": lambda b: len(b) >= 12 and b[4:8] == b"ftyp" and b"avif" in b[8:16],
            ".heic": lambda b: len(b) >= 12 and b[4:8] == b"ftyp" and any(x in b[8:16] for x in (b"heic", b"heix", b"hevc", b"hevx")),
        }
        check = signatures.get(ext)
        if check and check(head):
            if ext == ".rar":
                minimum_header_size = 16 if head.startswith(b"Rar!\x1a\x07\x01\x00") else 14
                if path.stat().st_size < minimum_header_size:
                    return False, "truncated_rar_header"
            if ext == ".zip":
                if not zipfile.is_zipfile(path):
                    return False, "invalid_zip_archive"
                with zipfile.ZipFile(path) as archive:
                    bad = archive.testzip()
                    if bad:
                        return False, f"corrupt_zip_member:{bad}"
            return True, f"valid_{ext[1:]}_signature"
        if check:
            return False, f"invalid_{ext[1:]}_signature"
        if ext == ".svg":
            with path.open("rb") as stream:
                data = stream.read(65536).lstrip(b"\xef\xbb\xbf \t\r\n")
            if re.search(rb"<(?:[A-Za-z_][\w.-]*:)?svg(?:\s|>)", data, re.I):
                return True, "valid_svg_root"
            return False, "invalid_svg_root"
        return False, f"unsupported_extension:{ext or '(none)'}"
    except (OSError, zipfile.BadZipFile) as exc:
        return False, f"format_check_error:{exc}"


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Temporary file is placed beside destination so os.replace stays atomic.
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".part", dir=destination.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        shutil.copystat(source, temporary)
        # Exclusive creation protects against a concurrent or pre-existing file.
        try:
            os.link(temporary, destination)
        except FileExistsError:
            raise
        except OSError:
            # On filesystems without hardlink support, reserve destination first.
            fd_dest = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd_dest)
            try:
                shutil.copyfile(temporary, destination)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_link_or_copy(source: Path, destination: Path) -> None:
    """Create a per-resource path, hard-linking identical archived content."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        # Cross-device or unsupported-link filesystems still receive a copy.
        atomic_copy(source, destination)


def write_outputs(output_root: Path, outcomes: list[dict], inventory: dict[str, dict], prior: list[dict]) -> None:
    output_root.mkdir(parents=True, exist_ok=True)
    receipt_path = output_root / RECEIPT_OUT
    # One current outcome per resource; the download log retains attempt history.
    old: dict[str, dict] = {
        str(row.get("id", "")): row for row in prior
    }
    for row in outcomes:
        old[str(row.get("id", ""))] = row
    with receipt_path.open("w", encoding="utf-8", newline="") as stream:
        for row in old.values():
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    counters: dict[tuple[str, str], dict[str, int]] = {}
    for item in inventory.values():
        if str(item.get("category_id", "")) == EXCLUDED_CATEGORY_ID:
            continue
        key = (str(item.get("group", "未分组")), str(item.get("category", "未分类")))
        counters.setdefault(key, {"inventory_count": 0, "archived_count": 0, "failed_count": 0, "conflict_count": 0})["inventory_count"] += 1
    latest_by_id: dict[str, dict] = {}
    for row in old.values():
        latest_by_id[str(row.get("id", ""))] = row
    for row in latest_by_id.values():
        item = inventory.get(str(row.get("id", "")), {})
        if str(item.get("category_id", "")) == EXCLUDED_CATEGORY_ID:
            continue
        key = (str(item.get("group", "未分组")), str(item.get("category", "未分类")))
        bucket = counters.setdefault(key, {"inventory_count": 0, "archived_count": 0, "failed_count": 0, "conflict_count": 0})
        status = row.get("archive_status")
        if status in {"archived", "deduplicated"}:
            bucket["archived_count"] += 1
        elif status == "conflict":
            bucket["conflict_count"] += 1
        elif status not in {"archived", "deduplicated"}:
            bucket["failed_count"] += 1
    with (output_root / SUMMARY_OUT).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["group", "category", "inventory_count", "archived_count", "failed_count", "conflict_count"])
        writer.writeheader()
        for (group, category), counts in sorted(counters.items()):
            writer.writerow({"group": group, "category": category, **counts})


def archive(receipts_path: Path, inventory_path: Path, output_root: Path) -> list[dict]:
    inventory = load_inventory(inventory_path)
    receipts = read_jsonl(receipts_path)
    # Hard exclusion is checked before creating any directory or copying files.
    for row in receipts:
        item_id = str(row.get("id", "")).strip()
        item = inventory.get(item_id)
        category_id = str(row.get("category_id", "")).strip()
        if category_id == EXCLUDED_CATEGORY_ID or (item and str(item.get("category_id")) == EXCLUDED_CATEGORY_ID):
            raise ValueError(f"{receipts_path}:{row['_line']}: 分类 57（优秀作品）为硬性排除项，拒绝执行")

    receipt_path = output_root / RECEIPT_OUT
    prior = read_jsonl(receipt_path) if receipt_path.exists() else []
    for row in prior:
        row.pop("_line", None)
    # Use receipt metadata as an index; verify files lazily only when a matching
    # digest arrives. This avoids a full archive walk/hash on every resume.
    hash_index: dict[str, list[Path]] = {}
    archive_root_resolved = output_root.resolve()
    for row in prior:
        digest = str(row.get("sha256", ""))
        archived_path = row.get("archived_path")
        if digest and archived_path and row.get("archive_status") in {"archived", "deduplicated"}:
            candidate = Path(str(archived_path))
            try:
                resolved = candidate.resolve()
                if resolved == archive_root_resolved or archive_root_resolved in resolved.parents:
                    hash_index.setdefault(digest, []).append(candidate)
            except OSError:
                continue
    verified_hash_paths: dict[str, Path | None] = {}
    results: list[dict] = []
    for receipt in receipts:
        item_id = str(receipt.get("id", "")).strip()
        item = inventory.get(item_id)
        base = {k: receipt.get(k) for k in ("id", "local_path", "url", "category_id")}
        result = {**base, "archive_status": "failed", "reason": "", "format_check": "", "sha256": "", "bytes": 0, "archived_path": ""}
        def fail(reason: str) -> None:
            result["reason"] = reason
        if not item:
            fail("id_not_in_inventory")
        elif str(receipt.get("category_id", "")) != str(item.get("category_id", "")):
            fail("category_id_mismatch")
        elif receipt.get("status", "downloaded") not in {"downloaded", "success", "ok", "complete"}:
            fail(f"download_status:{receipt.get('status')}")
        elif not isinstance(receipt.get("url"), str) or not receipt["url"].strip():
            fail("missing_source_url")
        else:
            source = Path(str(receipt.get("local_path", ""))).expanduser()
            if not source.is_file():
                fail("local_file_missing")
            else:
                result["bytes"] = source.stat().st_size
                valid, reason = valid_format(source)
                result["format_check"] = reason
                if not valid:
                    fail(reason)
                else:
                    digest = sha256(source)
                    result["sha256"] = digest
                    category_name = safe_name(str(item.get("category") or "未分类"))
                    filename = f"{safe_name(item_id)} - {safe_name(str(item.get('title') or '（无标题）'))}{source.suffix.lower()}"
                    destination = output_root / category_name / filename
                    if destination.exists():
                        try:
                            if destination.is_file() and sha256(destination) == digest:
                                result.update(archive_status="deduplicated", reason="same_target_bytes", archived_path=str(destination))
                                verified_hash_paths[digest] = destination
                            else:
                                result.update(archive_status="conflict", reason="destination_exists_with_different_bytes", archived_path=str(destination))
                        except OSError as exc:
                            result["reason"] = f"destination_check_error:{exc}"
                    else:
                        try:
                            canonical = verified_hash_paths.get(digest)
                            if canonical is None and digest in hash_index:
                                for candidate in hash_index[digest]:
                                    try:
                                        if candidate.is_file() and sha256(candidate) == digest:
                                            canonical = candidate
                                            break
                                    except OSError:
                                        continue
                                verified_hash_paths[digest] = canonical
                            if canonical is not None:
                                atomic_link_or_copy(canonical, destination)
                                result.update(archive_status="deduplicated", reason="sha256_match_hardlink_or_copy", archived_path=str(destination))
                            else:
                                atomic_copy(source, destination)
                                result.update(archive_status="archived", reason=reason, archived_path=str(destination))
                            hash_index.setdefault(digest, []).append(destination)
                            verified_hash_paths[digest] = destination
                        except FileExistsError:
                            result.update(archive_status="conflict", reason="destination_created_concurrently", archived_path=str(destination))
                        except OSError as exc:
                            result["reason"] = f"copy_error:{exc}"
        item = item or {}
        result.update(title=item.get("title", ""), category=item.get("category", ""), group=item.get("group", ""))
        results.append(result)
    write_outputs(output_root, results, inventory, prior)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="按下载回执把高阶文化PPTX归档到31个网站分类目录，不执行下载。")
    parser.add_argument("--receipts", required=True, type=Path, help="下载回执 JSONL；每行 id, local_path, url, category_id，status 可选")
    parser.add_argument("--inventory", type=Path, default=INVENTORY, help=f"来源清单 JSONL（默认 {INVENTORY}）")
    parser.add_argument("--output-root", type=Path, default=ARCHIVE, help=f"归档根目录（默认 {ARCHIVE}）")
    args = parser.parse_args()
    try:
        results = archive(args.receipts, args.inventory, args.output_root)
    except (OSError, ValueError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    counts: dict[str, int] = {}
    for row in results:
        counts[row["archive_status"]] = counts.get(row["archive_status"], 0) + 1
    print(json.dumps({"processed": len(results), "counts": counts, "receipt": str(args.output_root / RECEIPT_OUT), "summary": str(args.output_root / SUMMARY_OUT)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
