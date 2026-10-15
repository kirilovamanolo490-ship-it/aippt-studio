"""Publish a reviewed fast-revision candidate over its current Work-local source."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


PUBLISH_RECEIPT = "publish-receipt.json"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read valid revision metadata: {path.name}") from error
    if not isinstance(value, dict):
        raise ValueError(f"revision metadata must be a JSON object: {path.name}")
    return value


def _inside(path: Path, parent: Path) -> Path:
    """Return a lexical path below parent, rejecting traversal and symlink hops."""
    if ".." in path.parts:
        raise ValueError("path contains traversal components")
    absolute = Path(os.path.abspath(path))
    try:
        relative = absolute.relative_to(parent)
    except ValueError as error:
        raise ValueError("path must remain inside the active Work") from error
    cursor = parent
    for part in relative.parts:
        if part in ("", ".", ".."):
            raise ValueError("path contains traversal components")
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"symlink paths are not allowed: {cursor}")
    return absolute


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


@contextmanager
def _source_publish_lock(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive per-source lock; never wait behind another publisher."""
    token = uuid.uuid4().hex.encode("ascii")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except FileExistsError as error:
        raise ValueError("another publication for this source is already in progress") from error
    lock_stat = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        try:
            current_stat = lock_path.stat(follow_symlinks=False)
            if (current_stat.st_dev == lock_stat.st_dev and current_stat.st_ino == lock_stat.st_ino
                    and not lock_path.is_symlink()):
                lock_path.unlink()
        except FileNotFoundError:
            pass


def publish_revision(work_dir: Path | str, revision_dir: Path | str) -> dict[str, Any]:
    """Atomically update the source path of a scoped fast revision.

    A byte-for-byte backup is retained before replacing the current file. This
    operation only establishes publication and scoped structural QA; it never
    claims that a person has reviewed the page visually.
    """
    work = Path(work_dir).expanduser().resolve(strict=True)
    workspace = work / "workspace"
    output = work / "output"
    if not workspace.is_dir():
        raise ValueError("active Work must contain a workspace/ directory")
    revision = _inside(Path(revision_dir).expanduser(), work)
    try:
        revision.relative_to(workspace)
    except ValueError as error:
        raise ValueError("revision directory must be inside Work workspace/") from error
    if not revision.is_dir():
        raise ValueError("revision directory does not exist")

    receipt_path = _inside(revision / "receipt.json", work)
    fast_path = _inside(revision / "fast-revision.json", work)
    qa_path = _inside(revision / "revision-qa.json", work)
    candidate = _inside(revision / "candidate.pptx", work)
    for path in (receipt_path, fast_path, qa_path, candidate):
        if not path.is_file():
            raise ValueError(f"required fast-revision artifact is missing: {path.name}")
    patch = _json(receipt_path)
    fast = _json(fast_path)
    qa = _json(qa_path)

    if patch.get("status") != "candidate":
        raise ValueError("patch receipt must describe an applied candidate")
    if qa.get("status") != "passed_scoped":
        raise ValueError("revision QA must have status passed_scoped")
    source_value = patch.get("source")
    if not isinstance(source_value, str) or not source_value:
        raise ValueError("patch receipt has no source path")
    source_input = Path(source_value).expanduser()
    if not source_input.is_absolute():
        source_input = revision / source_input
    source = _inside(source_input, work)
    allowed = False
    for root in (workspace, output):
        try:
            rel = source.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] != "input":
            allowed = True
            break
    if not allowed or not source.is_file():
        raise ValueError("source must be an existing file under Work workspace/ or output/, never input/")
    if source == candidate:
        raise ValueError("candidate cannot be the published source")

    source_sha = patch.get("source_sha256")
    candidate_sha = patch.get("candidate_sha256")
    if not isinstance(source_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", source_sha) or not isinstance(candidate_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", candidate_sha):
        raise ValueError("patch receipt has invalid source or candidate SHA-256")
    if fast.get("source") != str(source) or fast.get("source_sha256") != source_sha:
        raise ValueError("fast-revision source identity does not match patch receipt")
    if fast.get("revision_qa_status") != "passed_scoped":
        raise ValueError("fast-revision report does not record passed_scoped QA")
    if fast.get("candidate_sha256") != candidate_sha or qa.get("source_sha256") != source_sha or qa.get("candidate_sha256") != candidate_sha:
        raise ValueError("revision artifacts disagree on source or candidate SHA-256")
    if _sha(candidate) != candidate_sha:
        raise ValueError("candidate SHA-256 changed; refusing to publish")

    backup_dir = _inside(workspace / "history" / "revisions", work)
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = _inside(backup_dir / f"{source_sha}.pptx", work)
    source_key = hashlib.sha256(str(source).encode("utf-8")).hexdigest()
    lock_path = _inside(backup_dir / f".publish-{source_key}.lock", work)
    prior_path = revision / PUBLISH_RECEIPT
    if prior_path.is_symlink():
        raise ValueError("symlink paths are not allowed for the publish receipt")
    with _source_publish_lock(lock_path):
        if prior_path.is_file():
            prior = _json(prior_path)
            if (prior.get("source") == str(source) and prior.get("source_sha256") == source_sha
                    and prior.get("candidate_sha256") == candidate_sha and _sha(source) == candidate_sha
                    and backup.is_file() and _sha(backup) == source_sha):
                return prior | {"idempotent": True}

        if _sha(source) != source_sha:
            raise ValueError("source changed since fast-revise; refusing to overwrite it")

        if backup.exists():
            if _sha(backup) != source_sha:
                raise ValueError("existing source backup does not match its hash name")
        else:
            fd, name = tempfile.mkstemp(prefix=f".{source_sha}.", suffix=".tmp", dir=backup_dir)
            os.close(fd)
            backup_temp = Path(name)
            try:
                shutil.copy2(source, backup_temp)
                if _sha(backup_temp) != source_sha:
                    raise ValueError("source changed while making the recovery backup")
                os.replace(backup_temp, backup)
            finally:
                backup_temp.unlink(missing_ok=True)

        if _sha(source) != source_sha:
            raise ValueError("source changed before publication; backup retained and source left untouched")
        fd, name = tempfile.mkstemp(prefix=f".{source.name}.", suffix=".publish.tmp", dir=source.parent)
        os.close(fd)
        staged = Path(name)
        try:
            shutil.copy2(candidate, staged)
            if _sha(staged) != candidate_sha:
                raise ValueError("staged candidate SHA-256 mismatch; refusing to publish")
            if _sha(source) != source_sha:
                raise ValueError("source changed during publication; refusing to replace it")
            os.replace(staged, source)
        finally:
            staged.unlink(missing_ok=True)

        new_sha = _sha(source)
        if new_sha != candidate_sha:
            raise ValueError("published source SHA-256 does not match the candidate")
        result = {
            "schema_version": 1,
            "status": "published",
            "source": str(source),
            "source_sha256": source_sha,
            "current_sha256": new_sha,
            "candidate_sha256": candidate_sha,
            "backup": str(backup),
            "native_visual_verified": False,
            "visual_review_required": True,
            "next": "Inspect the affected whole page; this receipt does not establish visual or aesthetic approval.",
            "published_at": datetime.now(timezone.utc).isoformat(),
            "idempotent": False,
        }
        _atomic_json(prior_path, result)
        return result
