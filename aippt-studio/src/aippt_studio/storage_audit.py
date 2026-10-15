"""Read-only size and reuse audit for an existing Work directory.

The report deliberately distinguishes logical file lengths from allocated disk
blocks. APFS clonefiles and hard links can share blocks even when summing file
lengths, so logical totals are not a claim about physical disk use.
"""
from __future__ import annotations

import json
import hashlib
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _read_conversation_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"status": "unreadable"}
    threads = value.get("threads") if isinstance(value, dict) else None
    if not isinstance(threads, dict):
        return {"status": "invalid"}

    snapshots: list[dict[str, Any]] = []
    by_sha: dict[str, int] = defaultdict(int)
    append_only_count = 0
    for thread_id, thread in threads.items():
        if not isinstance(thread, dict) or not isinstance(thread.get("snapshots"), list):
            continue
        for snap in thread["snapshots"]:
            if not isinstance(snap, dict):
                continue
            raw = snap.get("raw_path")
            byte_count = snap.get("bytes")
            digest = snap.get("sha256")
            if isinstance(raw, str) and isinstance(byte_count, int) and byte_count >= 0:
                snapshots.append({"thread_id": str(thread_id), "raw_path": raw,
                                  "bytes": byte_count, "sha256": digest})
                if isinstance(digest, str):
                    by_sha[digest] += 1
                append_only_count += int(snap.get("append_only_from_previous") is True)
    sha_bytes: dict[str, int] = {}
    for snap in snapshots:
        digest = snap["sha256"]
        if isinstance(digest, str):
            sha_bytes.setdefault(digest, snap["bytes"])
    duplicate_logical_bytes = sum(
        (by_sha[digest] - 1) * byte_count for digest, byte_count in sha_bytes.items()
        if by_sha[digest] > 1
    )
    return {
        "status": "ok",
        "snapshot_count": len(snapshots),
        "snapshot_logical_bytes_from_manifest": sum(item["bytes"] for item in snapshots),
        "append_only_snapshot_count": append_only_count,
        "cross_thread_duplicate_snapshot_count": sum(count - 1 for count in by_sha.values() if count > 1),
        "cross_thread_duplicate_reuse_bytes_estimate": max(0, duplicate_logical_bytes),
        "largest_snapshots": sorted(snapshots, key=lambda item: item["bytes"], reverse=True)[:10],
    }


def audit_work_storage(work_dir: Path | str, *, top_n: int = 30) -> dict[str, Any]:
    """Summarize an existing Work without modifying it or following symlinks."""
    root = Path(work_dir).expanduser().resolve(strict=True)
    if not root.is_dir() or not (root / "workspace").is_dir():
        raise ValueError("work_dir must be an existing Work directory containing workspace/")
    if type(top_n) is not int or not 1 <= top_n <= 200:
        raise ValueError("top_n must be an integer from 1 through 200")

    by_area: Counter[str] = Counter()
    by_extension: Counter[str] = Counter()
    top_files: list[tuple[int, str]] = []
    file_count = 0
    symlink_count = 0
    pptx_count = 0
    pptx_bytes = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        current_path = Path(current)
        safe_dirs: list[str] = []
        for name in dirnames:
            item = current_path / name
            if item.is_symlink():
                symlink_count += 1
            else:
                safe_dirs.append(name)
        dirnames[:] = safe_dirs
        for name in filenames:
            path = current_path / name
            try:
                info = path.lstat()
            except OSError:
                continue
            if path.is_symlink():
                symlink_count += 1
                continue
            if not path.is_file():
                continue
            size = info.st_size
            rel = path.relative_to(root).as_posix()
            parts = Path(rel).parts
            directories = parts[:-1]
            area = "/".join(directories[:2]) if directories else "[root]"
            by_area[area] += size
            by_extension[path.suffix.lower() or "[no extension]"] += size
            file_count += 1
            top_files.append((size, rel))
            if path.suffix.lower() in {".ppt", ".pptx"}:
                pptx_count += 1
                pptx_bytes += size
    top_files.sort(reverse=True)
    total = sum(by_area.values())
    return {
        "schema_version": 1,
        "kind": "work_storage_audit",
        "status": "read_only",
        "work_dir": str(root),
        "measurement": "logical file lengths (st_size); APFS clones and hard links may share physical blocks",
        "total_logical_bytes": total,
        "file_count": file_count,
        "symlink_count_skipped": symlink_count,
        "by_area_logical_bytes": dict(sorted(by_area.items(), key=lambda item: item[1], reverse=True)),
        "by_extension_logical_bytes": dict(sorted(by_extension.items(), key=lambda item: item[1], reverse=True)),
        "largest_files": [{"path": path, "bytes": size} for size, path in top_files[:top_n]],
        "powerpoint_file_count": pptx_count,
        "powerpoint_logical_bytes": pptx_bytes,
        "conversation_archive": _read_conversation_manifest(root / "workspace/conversations/manifest.json"),
    }


def plan_work_storage_retention(
    work_dir: Path | str, *, hash_budget_bytes: int = 64 * 1024 * 1024,
    max_file_hash_bytes: int = 16 * 1024 * 1024,
) -> dict[str, Any]:
    """Return a conservative, read-only file retention preview for one Work.

    No file is eligible for automatic deletion. ``review_candidate`` only means
    an operator may consider it after reviewing this evidence; unsafe or
    unresolved references are explicitly ``manual_review``.
    """
    root = Path(work_dir).expanduser().resolve(strict=True)
    if not root.is_dir() or not (root / "workspace").is_dir():
        raise ValueError("work_dir must be an existing Work directory containing workspace/")
    if type(hash_budget_bytes) is not int or hash_budget_bytes < 0:
        raise ValueError("hash_budget_bytes must be a non-negative integer")
    if type(max_file_hash_bytes) is not int or max_file_hash_bytes < 0:
        raise ValueError("max_file_hash_bytes must be a non-negative integer")

    records: list[dict[str, Any]] = []
    unsafe_reasons: list[str] = []
    referenced: dict[str, list[str]] = defaultdict(list)
    receipt_files: list[Path] = []
    symlinks: set[str] = set()

    for current, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(current)
        for name in list(dirnames):
            candidate = base / name
            if candidate.is_symlink():
                symlinks.add(candidate.relative_to(root).as_posix())
                dirnames.remove(name)
        for name in filenames:
            path = base / name
            rel = path.relative_to(root).as_posix()
            try:
                if path.is_symlink():
                    symlinks.add(rel)
                    continue
                if not path.is_file():
                    continue
                info = path.lstat()
            except OSError:
                unsafe_reasons.append(f"无法读取文件元数据: {rel}")
                continue
            lower_name = path.name.lower()
            if path.suffix.lower() == ".json" and (
                rel == "workspace/production-receipt.json" or
                any(token in lower_name for token in ("receipt", "manifest", "revision")) or
                "external-engine" in {part.lower() for part in path.parts}
            ):
                receipt_files.append(path)
            records.append({"path": rel, "bytes": info.st_size, "sha256": None,
                            "hash_status": "not_computed_yet"})

    hash_remaining = hash_budget_bytes
    computed_hashes: dict[str, str] = {}
    reference_expectations: dict[str, set[str]] = defaultdict(set)
    deferred_hash_reasons: list[str] = []
    for receipt in sorted(set(receipt_files)):
        receipt_rel = receipt.relative_to(root).as_posix()
        try:
            payload = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            unsafe_reasons.append(f"收据无法解析，引用状态未知: {receipt_rel} ({type(exc).__name__})")
            continue
        found_paths: list[tuple[str, str | None]] = []
        _collect_receipt_paths(payload, found_paths)
        for raw, expected_sha256 in found_paths:
            ref = Path(raw)
            if ref.is_absolute():
                try:
                    lexical = Path(os.path.normpath(str(ref)))
                    if lexical.is_relative_to(root) and _has_symlink_component(root, lexical.relative_to(root)):
                        unsafe_reasons.append(f"收据引用符号链接，需人工核验: {receipt_rel} -> {raw}")
                        continue
                    resolved = ref.resolve(strict=False)
                    if not resolved.is_relative_to(root):
                        unsafe_reasons.append(f"收据含 Work 外绝对路径，需人工核验: {receipt_rel} -> {raw}")
                        continue
                    target = resolved
                    target_rel = resolved.relative_to(root).as_posix()
                except OSError:
                    unsafe_reasons.append(f"收据绝对路径无法解析，需人工核验: {receipt_rel} -> {raw}")
                    continue
            else:
                # Prefer a Work-relative match; many local QA receipts instead
                # store paths relative to their own receipt directory.
                normalized = Path(os.path.normpath(raw))
                root_target = root / normalized
                local_target = receipt.parent / normalized
                if root_target.is_file() or root_target.is_symlink():
                    target = root_target
                else:
                    target = local_target
                try:
                    target = target.resolve(strict=False)
                    if not target.is_relative_to(root):
                        unsafe_reasons.append(f"收据相对路径解析到 Work 外部，需人工核验: {receipt_rel} -> {raw}")
                        continue
                    target_rel = target.relative_to(root).as_posix()
                except OSError:
                    unsafe_reasons.append(f"收据路径无法安全解析，需人工核验: {receipt_rel} -> {raw}")
                    continue
            if target.is_symlink() or (not ref.is_absolute() and _has_symlink_component(root, Path(os.path.normpath(raw)))):
                unsafe_reasons.append(f"收据引用符号链接，需人工核验: {receipt_rel} -> {target_rel}")
                continue
            try:
                if not target.resolve(strict=False).is_relative_to(root):
                    unsafe_reasons.append(f"收据引用解析到 Work 外部，需人工核验: {receipt_rel} -> {target_rel}")
                    continue
            except OSError:
                unsafe_reasons.append(f"收据引用无法安全解析，需人工核验: {receipt_rel} -> {target_rel}")
                continue
            if not target.is_file():
                unsafe_reasons.append(f"收据引用缺失或非文件路径，需人工核验: {receipt_rel} -> {target_rel}")
                continue
            if expected_sha256:
                reference_expectations[target_rel].add(expected_sha256.lower())
            referenced[target_rel].append(receipt_rel)

    record_by_path = {item["path"]: item for item in records}
    for rel in sorted(set(reference_expectations) | set(record_by_path),
                      key=lambda value: (value not in reference_expectations, value)):
        item = record_by_path.get(rel)
        if item is None:
            continue
        path = root / rel
        expected = reference_expectations.get(rel, set())
        item["receipt_sha256"] = sorted(expected)
        # Input and output trees are protected regardless of an old or stale
        # receipt digest. Avoid hashing them simply to restate that guarantee.
        parts = Path(rel).parts
        protected_area = parts[0] in {"input", "output"}
        executable = (parts[0] == "workspace" and
                      (Path(rel).suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".sh", ".ps1", ".psm1", ".ts", ".tsx", ".jsx"}
                       or (len(parts) > 1 and parts[1] in {"scripts", "code", "src"})))
        candidate_area = any(token in rel.lower() for token in ("cache", "__pycache__", ".cache", "preview", "render"))
        should_hash = (bool(expected) and not (protected_area or executable)) or (candidate_area and not expected)
        if not should_hash:
            item["hash_status"] = "not_required_for_protected_path" if protected_area or executable else "not_computed_not_candidate"
            continue
        if item["bytes"] > max_file_hash_bytes:
            deferred_hash_reasons.append(f"引用文件超过单文件 SHA-256 上限，未验证收据哈希: {rel}")
            item["hash_status"] = "deferred_size_limit"
            continue
        if item["bytes"] > hash_remaining:
            deferred_hash_reasons.append(f"SHA-256 总预算不足，未验证文件: {rel}")
            item["hash_status"] = "deferred_budget"
            continue
        try:
            digest = _file_sha256(path)
        except OSError as exc:
            deferred_hash_reasons.append(f"无法计算文件 SHA-256: {rel} ({type(exc).__name__})")
            item["hash_status"] = "hash_error"
            continue
        hash_remaining -= item["bytes"]
        computed_hashes[rel] = digest
        item["sha256"] = digest
        item["hash_status"] = "computed"
        if expected and any(digest.lower() != expected_digest for expected_digest in expected):
            unsafe_reasons.append(f"收据引用文件哈希与收据不符，需人工核验: {rel}")
        elif expected:
            item["sha256"] = digest
    structural_unsafe = bool(unsafe_reasons)
    unsafe_reasons.extend(deferred_hash_reasons)

    if symlinks:
        unsafe_reasons.extend(f"发现符号链接，计划不判断其目标: {path}" for path in sorted(symlinks))
    global_manual = bool(symlinks or structural_unsafe)

    for item in records:
        rel = item["path"]
        parts = Path(rel).parts
        if parts[0] == "input":
            category, status, reason = "input_source", "protected", "所有提供来源均保留"
        elif parts[0] == "output":
            category, status, reason = "output", "protected", "所有成品输出均保留"
        elif parts[0] == "workspace" and (len(parts) == 1 or parts[1] in {"scripts", "code", "src"} or Path(rel).suffix.lower() in {".py", ".js", ".mjs", ".cjs", ".sh", ".ps1", ".psm1", ".ts", ".tsx", ".jsx"}):
            category, status, reason = "executable_code", "protected", "可执行制作代码均保留"
        elif rel in referenced:
            category, status, reason = "current_receipt_reference", "protected", "被有效/可解析收据路径引用: " + ", ".join(sorted(referenced[rel]))
        elif any(token in rel.lower() for token in ("backup", "archive", "revision", "history", "candidate")):
            category, status, reason = "backup", "manual_review", "备份或历史候选不能仅凭年龄判定退役"
        elif any(token in rel.lower() for token in ("cache", "__pycache__", ".cache", "preview", "render")):
            category, status, reason = "regenerable_cache", "review_candidate", "疑似可再生缓存；需核对用途及外部引用"
        else:
            category, status, reason = "unknown", "manual_review", "用途或引用关系未能确证"
        if global_manual and status == "review_candidate":
            status, reason = "manual_review", reason + "; 同一 Work 存在未解决的符号链接或收据风险"
        item.update({"category": category, "status": status, "reason": reason,
                     "referenced_by": sorted(referenced.get(rel, []))})

    totals: Counter[str] = Counter()
    for item in records:
        totals[item["status"]] += item["bytes"]
    return {"schema_version": 1, "kind": "work_storage_retention_plan", "status": "dry_run_only",
            "work_dir": str(root), "deletion_performed": False,
            "measurement": "logical file lengths (st_size); SHA-256 is computed within the configured per-file and total byte budgets",
            "hash_budget_bytes": hash_budget_bytes, "hash_bytes_used": hash_budget_bytes - hash_remaining,
            "unsafe_reasons": sorted(set(unsafe_reasons)), "logical_bytes_by_status": dict(sorted(totals.items())),
            "files": sorted(records, key=lambda item: item["path"])}


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _has_symlink_component(root: Path, relative: Path) -> bool:
    current = root
    for component in relative.parts:
        if component in {"", ".", ".."}:
            if component == "..":
                current = current.parent
            continue
        current = current / component
        if current.is_symlink():
            return True
    return False


def _collect_receipt_paths(value: Any, output: list[tuple[str, str | None]]) -> None:
    """Collect only strings stored in conventional path fields."""
    if isinstance(value, dict):
        for key, child in value.items():
            if (key.lower() in {"path", "relative_path", "raw_path", "file_path"}
                    and isinstance(child, str)
                    and isinstance(value.get("sha256"), str)
                    and len(value["sha256"]) == 64
                    and all(ch in "0123456789abcdefABCDEF" for ch in value["sha256"])):
                digest = value.get("sha256") if isinstance(value.get("sha256"), str) else None
                output.append((child, digest))
            else:
                _collect_receipt_paths(child, output)
    elif isinstance(value, list):
        for child in value:
            _collect_receipt_paths(child, output)
