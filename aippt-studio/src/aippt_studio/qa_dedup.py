"""Conservative APFS clone deduplication for completed Work QA artifacts.

The tool never changes file bytes or names.  It only supports replacing exact
same-byte files inside one completed Work's ``workspace/qa`` directory with
APFS clones.  Application is opt-in and unavailable on non-macOS hosts.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


class DedupError(ValueError):
    """A safety condition prevents the requested audit or application."""


def _file_identity(path: Path) -> dict[str, int]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise DedupError(f"not a regular file: {path}")
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "nlink": info.st_nlink,
    }


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise DedupError(f"not a regular file: {path}")
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            digest.update(block)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise DedupError(f"file changed while hashing: {path}")
        # Restore the observed access time so a read-only audit does not leave
        # a metadata change on filesystems that update atime during reads.
        if (after.st_atime_ns, after.st_mtime_ns) != (before.st_atime_ns, before.st_mtime_ns):
            os.utime(fd, ns=(before.st_atime_ns, before.st_mtime_ns))
            after = os.fstat(fd)
            if after.st_atime_ns != before.st_atime_ns or after.st_mtime_ns != before.st_mtime_ns:
                raise DedupError(f"could not restore access time after hashing: {path}")
        if path.is_symlink() or (path.stat().st_dev, path.stat().st_ino) != (after.st_dev, after.st_ino):
            raise DedupError(f"file path changed while hashing: {path}")
    finally:
        os.close(fd)
    return digest.hexdigest()


def _metadata(path: Path) -> dict[str, Any]:
    info = path.lstat()
    xattrs: dict[str, str] = {}
    if hasattr(os, "listxattr"):
        try:
            for name in sorted(os.listxattr(path, follow_symlinks=False)):
                value = os.getxattr(path, name, follow_symlinks=False)
                xattrs[name] = value.hex()
        except (OSError, TypeError, NotImplementedError) as error:
            raise DedupError(f"cannot safely read extended attributes for {path}: {error}") from error
    return {
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "atime_ns": info.st_atime_ns,
        "mtime_ns": info.st_mtime_ns,
        "flags": getattr(info, "st_flags", 0),
        "xattrs": xattrs,
    }


def _same_file_snapshot(path: Path, identity: dict[str, int]) -> bool:
    try:
        return _file_identity(path) == identity
    except (OSError, DedupError):
        return False


def _scan_qa(qa: Path) -> list[Path]:
    result: list[Path] = []
    for current, dirs, files in os.walk(qa, topdown=True, followlinks=False):
        base = Path(current)
        for name in list(dirs):
            item = base / name
            if item.is_symlink():
                raise DedupError(f"symlink found inside QA scope: {item}")
        dirs[:] = [name for name in dirs if not (base / name).is_symlink()]
        for name in files:
            item = base / name
            if item.is_symlink():
                raise DedupError(f"symlink found inside QA scope: {item}")
            if not stat.S_ISREG(item.lstat().st_mode):
                raise DedupError(f"non-regular item found inside QA scope: {item}")
            result.append(item)
    return sorted(result, key=lambda path: path.relative_to(qa).as_posix())


def _apfs_clone(source: Path, destination: Path) -> None:
    if os.uname().sysname != "Darwin":
        raise DedupError("--apply requires macOS APFS clonefile support")
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
    clonefile = libc.clonefile
    clonefile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    clonefile.restype = ctypes.c_int
    result = clonefile(os.fsencode(source), os.fsencode(destination), 0)
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), str(destination))


def _copy_metadata(source: Path, destination: Path, expected: dict[str, Any]) -> None:
    """Apply and verify stat/xattrs; use macOS copyfile to retain ACL metadata."""
    if os.uname().sysname == "Darwin":
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        copyfile = libc.copyfile
        copyfile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
        copyfile.restype = ctypes.c_int
        # Darwin COPYFILE_ACL | COPYFILE_STAT | COPYFILE_XATTR; exclude data.
        if copyfile(os.fsencode(source), os.fsencode(destination), None, 0x01 | 0x02 | 0x04) != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number), str(destination))
    try:
        os.chown(destination, expected["uid"], expected["gid"], follow_symlinks=False)
    except PermissionError as error:
        now = destination.stat()
        if (now.st_uid, now.st_gid) != (expected["uid"], expected["gid"]):
            raise DedupError(f"cannot preserve owner/group on clone for {source}") from error
    existing: set[str] = set()
    if hasattr(os, "listxattr"):
        existing = set(os.listxattr(destination, follow_symlinks=False))
        for name in existing - set(expected["xattrs"]):
            os.removexattr(destination, name, follow_symlinks=False)
        for name, value in expected["xattrs"].items():
            os.setxattr(destination, name, bytes.fromhex(value), follow_symlinks=False)
    os.chmod(destination, expected["mode"], follow_symlinks=False)
    if expected["flags"] and hasattr(os, "chflags"):
        os.chflags(destination, expected["flags"], follow_symlinks=False)
    os.utime(destination, ns=(expected["atime_ns"], expected["mtime_ns"]), follow_symlinks=False)
    actual = _metadata(destination)
    for key in ("mode", "uid", "gid", "atime_ns", "mtime_ns", "flags", "xattrs"):
        if actual[key] != expected[key]:
            raise DedupError(f"could not preserve {key} metadata for {source}")


def _record_path(qa: Path, path: Path, identity: dict[str, int], digest: str) -> dict[str, Any]:
    return {"path": path.relative_to(qa).as_posix(), "identity": identity, "sha256": digest}


def _write_receipt(path: Path, report: dict[str, Any]) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".qa-dedup-receipt-", delete=False) as handle:
        temp = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def deduplicate_completed_work_qa(
    work_dir: Path | str,
    *,
    apply: bool = False,
    only_sha256: str | None = None,
    clone: Callable[[Path, Path], None] = _apfs_clone,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Report exact duplicates, optionally replacing them by APFS clones.

    ``work.json`` must declare status ``completed`` before either mode runs.
    The scan is restricted to regular files below ``workspace/qa`` and refuses
    symlinks, existing hard links, and paths that escape that directory.
    """
    work = Path(work_dir).expanduser().resolve(strict=True)
    if not work.is_dir() or not work.name.isdigit():
        raise DedupError("work_dir must be an existing numeric Work directory")
    try:
        metadata = json.loads((work / "work.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DedupError("completed Work work.json is required") from error
    if not isinstance(metadata, dict) or metadata.get("status") != "completed":
        raise DedupError("QA deduplication is limited to Work status=completed")
    qa = work / "workspace" / "qa"
    if qa.is_symlink() or not qa.is_dir():
        raise DedupError("completed Work must contain a real workspace/qa directory")
    qa_resolved = qa.resolve(strict=True)
    if qa_resolved != qa:
        raise DedupError("workspace/qa must not resolve through a symlink")

    files = _scan_qa(qa)
    by_size: dict[int, list[Path]] = defaultdict(list)
    for path in files:
        by_size[_file_identity(path)["size"]].append(path)
    groups: dict[str, list[Path]] = defaultdict(list)
    identities: dict[Path, dict[str, int]] = {}
    digests: dict[Path, str] = {}
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        for path in same_size:
            identity = _file_identity(path)
            if identity["nlink"] != 1:
                raise DedupError(f"existing hard-linked file in duplicate scan scope: {path}")
            digest = _hash_file(path)
            identities[path] = identity
            digests[path] = digest
            groups[digest].append(path)
    duplicate_groups = [sorted(paths, key=lambda item: item.relative_to(qa).as_posix())
                        for paths in groups.values() if len(paths) > 1]
    duplicate_groups.sort(key=lambda group: group[0].relative_to(qa).as_posix())
    if only_sha256 is not None:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", only_sha256):
            raise DedupError("only_sha256 must be a complete SHA-256 digest")
        duplicate_groups = [group for group in duplicate_groups
                            if digests[group[0]] == only_sha256.lower()]
        if not duplicate_groups:
            raise DedupError("requested SHA-256 is not a current exact-duplicate QA group")
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "completed_work_qa_exact_duplicate_clone_audit",
        "status": "dry_run" if not apply else "applied",
        "work_dir": str(work),
        "scope": "workspace/qa only",
        "only_sha256": only_sha256.lower() if only_sha256 else None,
        "method": "SHA-256 of same-size regular files; APFS clonefile with unchanged path and bytes",
        "group_count": len(duplicate_groups),
        "logical_duplicate_bytes_estimate": sum(
            (len(group) - 1) * identities[group[0]]["size"] for group in duplicate_groups
        ),
        "groups": [],
        "operations": [],
        "created_at": now().astimezone(timezone.utc).isoformat(),
        "receipt_id": str(uuid.uuid4()),
    }
    for group in duplicate_groups:
        canonical = group[0]
        report["groups"].append({
            "sha256": digests[canonical],
            "bytes_per_file": identities[canonical]["size"],
            "files": [_record_path(qa, path, identities[path], digests[path]) for path in group],
            "canonical": canonical.relative_to(qa).as_posix(),
        })
    if not apply:
        return report

    receipt = qa / f"qa-dedup-{report['receipt_id']}.json"
    report["receipt_path"] = receipt.relative_to(work).as_posix()
    report["status"] = "applying"
    _write_receipt(receipt, report)

    try:
      for group in duplicate_groups:
        canonical = group[0]
        canonical_identity = identities[canonical]
        canonical_digest = digests[canonical]
        for duplicate in group[1:]:
            duplicate_identity = identities[duplicate]
            expected_metadata = _metadata(duplicate)
            if not _same_file_snapshot(canonical, canonical_identity) or _hash_file(canonical) != canonical_digest:
                raise DedupError(f"canonical file changed before clone: {canonical}")
            if not _same_file_snapshot(duplicate, duplicate_identity) or _hash_file(duplicate) != canonical_digest:
                raise DedupError(f"duplicate changed before clone: {duplicate}")

            new_path: Path | None = None
            old_path: Path | None = None
            retain_rollback = False
            try:
                with tempfile.NamedTemporaryFile(prefix=".qa-clone-", dir=duplicate.parent, delete=False) as handle:
                    new_path = Path(handle.name)
                new_path.unlink()
                clone(canonical, new_path)
                if _hash_file(new_path) != canonical_digest:
                    raise DedupError(f"clone hash does not match verified group: {duplicate}")
                _copy_metadata(duplicate, new_path, expected_metadata)
                if not _same_file_snapshot(canonical, canonical_identity):
                    raise DedupError(f"canonical file changed during clone: {canonical}")
                if not _same_file_snapshot(duplicate, duplicate_identity):
                    raise DedupError(f"duplicate path changed during clone: {duplicate}")
                if _hash_file(duplicate) != canonical_digest:
                    raise DedupError(f"duplicate hash changed before replacement: {duplicate}")

                # Retain an APFS clone of the original until post-replacement
                # verification succeeds so a failure can restore the pathname.
                with tempfile.NamedTemporaryFile(prefix=".qa-rollback-", dir=duplicate.parent, delete=False) as handle:
                    old_path = Path(handle.name)
                old_path.unlink()
                clone(duplicate, old_path)
                _copy_metadata(duplicate, old_path, expected_metadata)
                if _hash_file(old_path) != canonical_digest:
                    raise DedupError(f"rollback clone verification failed: {duplicate}")
                if not _same_file_snapshot(duplicate, duplicate_identity):
                    raise DedupError(f"duplicate path changed before atomic replacement: {duplicate}")
                os.replace(new_path, duplicate)
                new_path = None
                try:
                    if _hash_file(duplicate) != canonical_digest or _metadata(duplicate) != expected_metadata:
                        raise DedupError(f"post-replacement verification failed: {duplicate}")
                except Exception as verification_error:
                    try:
                        os.replace(old_path, duplicate)
                        old_path = None
                    except OSError as rollback_error:
                        # The old bytes are still at this private backup path.
                        # Deleting them in finally would destroy the only
                        # retained recovery copy after a failed restore.
                        retain_rollback = True
                        report.setdefault("recovery_backups", []).append({
                            "path": old_path.relative_to(work).as_posix(),
                            "sha256": canonical_digest,
                            "present": old_path.is_file(),
                        })
                        raise DedupError(
                            f"post-replacement verification and rollback both failed; "
                            f"retain recovery backup {old_path}"
                        ) from rollback_error
                    raise verification_error
                after_identity = _file_identity(duplicate)
                report["operations"].append({
                    "canonical_before": _record_path(qa, canonical, canonical_identity, canonical_digest),
                    "duplicate_before": _record_path(qa, duplicate, duplicate_identity, canonical_digest),
                    "duplicate_after": _record_path(qa, duplicate, after_identity, _hash_file(duplicate)),
                    "path_preserved": True,
                    "bytes_preserved": True,
                    "rollback_copy_removed_after_verification": True,
                })
                _write_receipt(receipt, report)
            finally:
                if new_path is not None:
                    new_path.unlink(missing_ok=True)
                if old_path is not None and not retain_rollback:
                    old_path.unlink(missing_ok=True)

      report["status"] = "applied"
      _write_receipt(receipt, report)
    except Exception as error:
      report["status"] = "partial_failure" if report["operations"] else "failed"
      report["error"] = f"{type(error).__name__}: {error}"
      _write_receipt(receipt, report)
      raise

    return report
