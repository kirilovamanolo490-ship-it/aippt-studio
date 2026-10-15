"""Local, explicitly bound Codex conversation evidence for a Work package.

The original JSONL is retained byte for byte, including records that the reader
does not understand. A snapshot of a local trace is never a claim that Codex has
retained the entire conversation, attachments, or every subagent's trace. This
module does not write Codex state, change Work approval, or promote raw chat into
the reusable knowledge base. It deliberately has no non-stdlib dependencies.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from typing import Any, Iterator

from .python_runtime import current_process_identity, validate_identity


class ConversationError(ValueError):
    """An explicit binding cannot be resolved or preserved safely."""


def _require_host_native_python(parser: argparse.ArgumentParser) -> None:
    """Fail before a public CLI can create or update a Work archive."""

    reason = validate_identity(current_process_identity())
    if reason is not None:
        parser.error("Conversation archive requires a host-native Python: " + reason)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _home(value: Path | str | None) -> Path:
    return Path(value).expanduser().resolve() if value is not None else Path.home() / ".codex"


def _load(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _write(path: Path, value: Any, *, text: bool = False) -> None:
    _private_dir(path.parent)
    data = value if text else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        temp = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    try:
        os.replace(temp, path)
        path.chmod(0o600)
    finally:
        temp.unlink(missing_ok=True)


def _store(work_dir: Path | str) -> Path:
    work = Path(work_dir).expanduser().resolve()
    if not work.is_dir() or not (work / "workspace").is_dir():
        raise ConversationError(f"Expected an existing Work with workspace/: {work}")
    return work / "workspace" / "conversations"


def _retention_policy(work_dir: Path | str) -> tuple[str | None, str | None]:
    """Read a present Work-local policy strictly; malformed policy must fail closed."""
    work = Path(work_dir).expanduser().resolve()
    path = work / "workspace" / "conversations" / "retention-policy.json"
    if not path.exists():
        # New Works use a compact metadata flag in work.json, avoiding an
        # otherwise empty conversations/ tree. Older Works retain their prior
        # opt-in behavior unless they carry this explicit setting.
        try:
            record = _load(work / "work.json", {})
        except (OSError, ValueError) as exc:
            return None, f"Cannot read Work record: {type(exc).__name__}: {exc}"
        if isinstance(record, dict) and (record.get("storage_profile") == "minimal" or
                                         record.get("conversation_retention") == "disabled"):
            return "disabled", None
        return None, None
    try:
        policy = _load(path, {})
    except (OSError, ValueError) as exc:
        return None, f"Cannot read retention policy: {type(exc).__name__}: {exc}"
    if not isinstance(policy, dict) or policy.get("mode") != "metadata_only":
        return None, "Present retention policy must be an object with mode='metadata_only'"
    return "metadata_only", None


def _metadata_only_result(work_dir: Path | str, operation: str) -> dict | None:
    mode, error = _retention_policy(work_dir)
    if error is not None:
        return {"status": "retention_policy_invalid", "operation": operation,
                "work_dir": str(Path(work_dir).expanduser().resolve()), "error": error,
                "message": "Conversation operation stopped because the Work retention policy is invalid"}
    if mode in {"metadata_only", "disabled"}:
        status = "disabled" if mode == "disabled" else "metadata_only"
        return {"status": status, "operation": operation,
                "work_dir": str(Path(work_dir).expanduser().resolve()),
                "message": "Conversation retention is disabled; no conversation bindings, snapshots, or derived files were created"}
    return None


@contextmanager
def _lock(store: Path) -> Iterator[None]:
    """A process lock releases on crash; overlapping workers fail without writes."""
    _private_dir(store)
    with (store / ".lock").open("a+b") as stream:
        (store / ".lock").chmod(0o600)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ConversationError(f"Conversation archive is busy: {store}") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                import msvcrt
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _valid_id(thread_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", thread_id) or ".." in thread_id:
        raise ConversationError("Invalid thread_id")
    return thread_id


def _metadata(path: Path) -> dict[str, Any]:
    """Read only the header during discovery, never scan conversation bodies."""
    with path.open("rb") as stream:
        for _ in range(32):
            line = stream.readline()
            if not line:
                break
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(record, dict) and record.get("type") == "session_meta":
                payload = record.get("payload", {})
                thread_id = payload.get("id") or payload.get("session_id")
                if not isinstance(thread_id, str):
                    break
                return {"thread_id": _valid_id(thread_id), "cwd": payload.get("cwd"),
                        "timestamp": payload.get("timestamp") or record.get("timestamp"),
                        "history_mode": payload.get("history_mode"),
                        "source": payload.get("source")}
    raise ConversationError(f"No usable session_meta in JSONL: {path}")


def _parent_thread(metadata: dict) -> str | None:
    source = metadata.get("source")
    if isinstance(source, dict):
        subagent = source.get("subagent")
        if isinstance(subagent, dict):
            spawn = subagent.get("thread_spawn")
            if isinstance(spawn, dict):
                return spawn.get("parent_thread_id")
    return None


def _catalog(codex_home: Path) -> tuple[dict[str, dict], list[str]]:
    rows: dict[str, dict] = {}
    warnings: list[str] = []
    databases = sorted(codex_home.glob("state_*.sqlite"),
                       key=lambda p: int(re.search(r"state_(\d+)", p.name).group(1))
                       if re.search(r"state_(\d+)", p.name) else -1, reverse=True)
    for database in databases:
        try:
            with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as connection:
                connection.execute("PRAGMA query_only=ON")
                columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
                if not {"id", "rollout_path", "cwd"}.issubset(columns):
                    continue
                fields = [field for field in ("id", "rollout_path", "cwd", "title", "archived", "updated_at", "agent_path")
                          if field in columns]
                for row in connection.execute("SELECT " + ", ".join(fields) + " FROM threads"):
                    item = dict(zip(fields, row))
                    item["metadata_source"] = str(database)
                    rows.setdefault(item["id"], item)
        except (OSError, sqlite3.Error) as exc:
            warnings.append(f"Cannot read {database.name}: {type(exc).__name__}: {exc}")
    index = codex_home / "session_index.jsonl"
    if index.is_file():
        try:
            for line in index.read_text(encoding="utf-8-sig").splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    warnings.append("session_index.jsonl contains an unreadable record")
                    continue
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    continue
                entry = rows.setdefault(item["id"], {"id": item["id"]})
                if not entry.get("title"):
                    entry["title"] = item.get("thread_name", "")
                entry.setdefault("updated_at", item.get("updated_at"))
        except OSError as exc:
            warnings.append(f"Cannot read session_index.jsonl: {exc}")
    return rows, warnings


def _rollouts(home: Path) -> Iterator[Path]:
    for folder in (home / "sessions", home / "archived_sessions"):
        if folder.is_dir():
            yield from folder.rglob("*.jsonl")


def _is_under(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def _state(path: Path, row: dict, home: Path) -> tuple[str, str]:
    # Sidebar archiving often changes SQLite only; path is not authoritative.
    if row.get("archived") in (0, 1, False, True):
        return ("archived_snapshot" if bool(row["archived"]) else "active_snapshot", "codex_threads_table")
    if _is_under(path, home / "archived_sessions"):
        return "archived_snapshot", "archived_sessions_directory"
    if _is_under(path, home / "sessions"):
        return "active_snapshot", "sessions_directory_fallback"
    return "unknown_snapshot", "explicit_local_file"


def _resolve(*, thread_id: str | None, rollout_path: Path | str | None,
             home: Path, catalog: dict[str, dict]) -> tuple[Path, dict]:
    if not thread_id and rollout_path is None:
        raise ConversationError("Provide an explicit thread_id or rollout_path; titles are never auto-bound")
    if thread_id:
        _valid_id(thread_id)
    candidates: list[Path] = []
    if rollout_path is not None:
        explicit = Path(rollout_path).expanduser().resolve()
        if explicit.is_file():
            if explicit.suffix.lower() != ".jsonl":
                raise ConversationError("Conversation source must be a JSONL rollout")
            candidates.append(explicit)
        elif not thread_id:
            raise ConversationError(f"Explicit rollout is missing: {explicit}")
    row = catalog.get(thread_id or "", {})
    if row.get("rollout_path"):
        candidate = Path(row["rollout_path"]).expanduser().resolve()
        if candidate.is_file() and candidate not in candidates:
            candidates.append(candidate)
    if not candidates and thread_id:
        # Filenames reduce work only; the metadata ID must still match exactly.
        candidates = [path for path in _rollouts(home) if thread_id in path.name]
        if not candidates:
            candidates = list(_rollouts(home))
    matches: list[tuple[Path, dict]] = []
    for path in candidates:
        try:
            metadata = _metadata(path)
        except (OSError, ConversationError):
            if rollout_path is not None and path == Path(rollout_path).expanduser().resolve():
                raise
            continue
        if thread_id is not None and metadata["thread_id"] != thread_id:
            if rollout_path is not None and path == Path(rollout_path).expanduser().resolve():
                raise ConversationError("Explicit rollout session_meta does not match thread_id")
            continue
        matches.append((path, metadata))
        if rollout_path is not None or row.get("rollout_path") == str(path):
            break
    if not matches:
        raise ConversationError(f"No available original rollout for explicitly bound thread {thread_id}")
    if len(matches) > 1:
        raise ConversationError(f"Multiple original rollouts match {thread_id}; bind an exact rollout_path")
    return matches[0]


def _bound_work_ids(work_dir: Path, thread_id: str) -> list[str]:
    associated = []
    for path in work_dir.parent.glob("*/workspace/conversations/bindings.json"):
        try:
            data = _load(path, {})
            if any(item.get("thread_id") == thread_id for item in data.get("bindings", [])):
                associated.append(path.parents[2].name)
        except (OSError, ValueError, AttributeError):
            continue
    return sorted(set(associated))


def discover_conversations(workspace_root: Path | str, codex_home: Path | str | None = None) -> dict:
    """Report exact-cwd candidates and existing bindings without creating any."""
    root, home = Path(workspace_root).expanduser().resolve(), _home(codex_home)
    catalog, warnings = _catalog(home)
    found: dict[str, dict] = {}
    for path in _rollouts(home):
        try:
            metadata = _metadata(path)
        except (OSError, ConversationError) as exc:
            warnings.append(f"Cannot inspect rollout header {path.name}: {exc}")
            continue
        cwd = metadata.get("cwd")
        if not isinstance(cwd, str) or Path(cwd).expanduser().resolve() != root:
            continue
        thread_id = metadata["thread_id"]
        row = catalog.get(thread_id, {})
        state, state_evidence = _state(path, row, home)
        entry = found.setdefault(thread_id, {"thread_id": thread_id, "cwd": str(root),
            "title": row.get("title", ""), "updated_at": row.get("updated_at"),
            "source_state": state, "state_evidence": state_evidence,
            "rollout_paths": [], "binding_required": True,
            "parent_thread_id": _parent_thread(metadata), "is_subagent_trace": bool(_parent_thread(metadata)),
            "bound_work_ids": _bound_work_ids(root / "work" / "_discovery", thread_id)})
        entry["rollout_paths"].append(str(path))
        entry["available_raw_bytes"] = entry.get("available_raw_bytes", 0) + path.stat().st_size
        entry["binding_required"] = not bool(entry["bound_work_ids"])
    # Metadata can survive after a rollout disappears: report, never fabricate.
    for thread_id, row in catalog.items():
        if row.get("cwd") and Path(row["cwd"]).expanduser().resolve() == root and thread_id not in found:
            found[thread_id] = {"thread_id": thread_id, "cwd": str(root), "title": row.get("title", ""),
                "updated_at": row.get("updated_at"), "source_state": "source_unavailable",
                "rollout_paths": [], "binding_required": True,
                "bound_work_ids": _bound_work_ids(root / "work" / "_discovery", thread_id)}
    return {"schema_version": 1, "generated_at": _now(), "workspace_root": str(root),
            "status": "candidates_found" if found else "no_candidates", "auto_binding": False,
            "matching_policy": "exact session_meta.cwd or threads.cwd only; titles are display hints, never binding evidence",
            "candidates": sorted(found.values(), key=lambda row: str(row.get("updated_at") or ""), reverse=True),
            "warnings": warnings}


def bind_conversation(work_dir: Path | str, *, thread_id: str | None = None,
                      rollout_path: Path | str | None = None, codex_home: Path | str | None = None,
                      binding_reason: str, shared_conversation: bool = False,
                      parent_thread_id: str | None = None) -> dict:
    """Add an explicit thread association. This does not assert Work completion."""
    skipped = _metadata_only_result(work_dir, "bind")
    if skipped is not None:
        return skipped
    if not binding_reason.strip():
        raise ConversationError("binding_reason must record the explicit association evidence")
    store, home = _store(work_dir), _home(codex_home)
    catalog, warnings = _catalog(home)
    path, metadata = _resolve(thread_id=thread_id, rollout_path=rollout_path, home=home, catalog=catalog)
    thread_id = metadata["thread_id"]
    actual_parent = _parent_thread(metadata)
    if parent_thread_id is not None and actual_parent != parent_thread_id:
        raise ConversationError("Explicit parent_thread_id does not match the subagent session_meta")
    with _lock(store):
        data = _load(store / "bindings.json", {"schema_version": 1, "bindings": []})
        associated = _bound_work_ids(store.parents[1], thread_id)
        associated = sorted(set(associated + [store.parents[1].name]))
        existing = next((item for item in data["bindings"] if item["thread_id"] == thread_id), None)
        if existing:
            # An explicit rebind may repair a moved rollout; retain its evidence trail.
            changed = existing.get("rollout_path") != str(path) or (shared_conversation and not existing.get("shared_conversation"))
            if changed:
                existing.setdefault("binding_updates", []).append({"at": _now(), "reason": binding_reason,
                                                                    "previous_rollout_path": existing.get("rollout_path")})
                existing["rollout_path"] = str(path)
                existing["shared_conversation"] = bool(shared_conversation or len(associated) > 1)
                existing["associated_work_ids"] = associated
                _write(store / "bindings.json", data)
            return {"status": "binding_updated" if changed else "already_bound", "binding": existing, "warnings": warnings}
        binding = {"thread_id": thread_id, "rollout_path": str(path), "bound_at": _now(),
                   "binding_reason": binding_reason, "binding_method": "explicit_thread_id" if thread_id and rollout_path is None else "explicit_rollout_path",
                   "source_cwd": metadata.get("cwd"), "shared_conversation": bool(shared_conversation or len(associated) > 1),
                   "associated_work_ids": associated,
                   "conversation_role": "subagent_trace" if actual_parent else "user_conversation",
                   "parent_thread_id": actual_parent,
                   "scope": "work_associated_conversation; may contain other work; not an independent complete project record"}
        data["bindings"].append(binding)
        _write(store / "bindings.json", data)
        return {"status": "bound", "binding": binding, "warnings": warnings}


def _sha_file(path: Path, length: int | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = length
        while remaining is None or remaining > 0:
            block = stream.read(1024 * 1024 if remaining is None else min(remaining, 1024 * 1024))
            if not block:
                break
            digest.update(block)
            if remaining is not None:
                remaining -= len(block)
    return digest.hexdigest()


def _fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
            "ctime_ns": stat.st_ctime_ns, "inode": stat.st_ino, "device": stat.st_dev}


def _active_snapshot_due(previous: dict, interval_seconds: int,
                         *, now: datetime | None = None) -> tuple[bool, str | None]:
    """Return whether an active rollout is due, using the prior full snapshot time."""
    captured = previous.get("last_full_capture_at") or previous.get("captured_at")
    if not isinstance(captured, str):
        return True, None
    try:
        captured_at = datetime.fromisoformat(captured.replace("Z", "+00:00"))
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=timezone.utc)
    except ValueError:
        # Missing/corrupt timing metadata must never postpone an archival capture.
        return True, None
    due_at = captured_at.timestamp() + interval_seconds
    current = now or datetime.now(timezone.utc)
    if current.timestamp() >= due_at:
        return True, datetime.fromtimestamp(due_at, timezone.utc).isoformat()
    return False, datetime.fromtimestamp(due_at, timezone.utc).isoformat()


def _clonefile(source: Path, destination: Path) -> bool:
    """APFS copy-on-write clone; never hard-link or mutate the original rollout."""
    if sys.platform != "darwin":
        return False
    import ctypes
    library = ctypes.CDLL(None, use_errno=True)
    operation = getattr(library, "clonefile", None)
    if operation is None:
        return False
    operation.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    operation.restype = ctypes.c_int
    return operation(os.fsencode(source), os.fsencode(destination), 0) == 0


def _same_prefix(source: Path, snapshot: Path, prefix_bytes: int) -> bool:
    """Compare an immutable snapshot with the exact current source prefix."""
    if prefix_bytes < 0 or not snapshot.is_file() or snapshot.stat().st_size != prefix_bytes:
        return False
    remaining = prefix_bytes
    try:
        with source.open("rb") as source_stream, snapshot.open("rb") as snapshot_stream:
            while remaining:
                size = min(1024 * 1024, remaining)
                left, right = source_stream.read(size), snapshot_stream.read(size)
                if len(left) != size or left != right:
                    return False
                remaining -= size
            return True
    except OSError:
        return False


def _capture(path: Path, directory: Path, *, previous_snapshot: Path | None = None) -> dict:
    _private_dir(directory)
    descriptor, temporary = tempfile.mkstemp(dir=directory)
    os.close(descriptor)
    temp = Path(temporary)
    try:
        before = _fingerprint(path)
        temp.unlink()  # clonefile requires a new destination in our private directory.
        # A prior archive is reused only when its complete bytes are an exact
        # prefix of this capture. Clonefile gives the new immutable file its own
        # inode while APFS shares unchanged extents; appends never touch history.
        try:
            prefix_bytes = previous_snapshot.stat().st_size if previous_snapshot and previous_snapshot.is_file() else -1
            reused_prefix = bool(previous_snapshot and prefix_bytes <= before["bytes"]
                                 and _same_prefix(path, previous_snapshot, prefix_bytes))
        except OSError:
            prefix_bytes, reused_prefix = -1, False
        try:
            # Preserve the established whole-file clone for first captures and
            # non-prefix rewrites; only append growth clones the prior archive.
            cloned = bool(_clonefile(previous_snapshot if reused_prefix else path, temp))
        except Exception:
            cloned = False
        copy_method = ("apfs_prefix_clonefile" if reused_prefix else "apfs_clonefile") if cloned else "bounded_stream_copy"
        if cloned and reused_prefix:
            try:
                with path.open("rb") as source, temp.open("ab") as dest:
                    source.seek(prefix_bytes)
                    remaining = before["bytes"] - prefix_bytes
                    while remaining:
                        block = source.read(min(1024 * 1024, remaining))
                        if not block:
                            raise ConversationError("Rollout shrank during prefix capture; previous evidence is preserved, retry refresh")
                        dest.write(block)
                        remaining -= len(block)
                    dest.flush()
                    os.fsync(dest.fileno())
                if not _same_prefix(path, previous_snapshot, prefix_bytes):
                    raise OSError("rollout prefix changed during capture")
            except OSError:
                # Unsupported clone semantics, concurrent rewrite, or append
                # failure falls back to the established full-copy path.
                temp.unlink(missing_ok=True)
                cloned = False
                copy_method = "bounded_stream_copy"
        if not cloned:
            temp.unlink(missing_ok=True)
            with path.open("rb") as source, temp.open("xb") as dest:
                remaining = before["bytes"]
                while remaining:
                    block = source.read(min(1024 * 1024, remaining))
                    if not block:
                        raise ConversationError("Rollout shrank during capture; previous evidence is preserved, retry refresh")
                    dest.write(block)
                    remaining -= len(block)
                dest.flush()
                os.fsync(dest.fileno())
            after_copy = _fingerprint(path)
            if after_copy["bytes"] < before["bytes"] or (after_copy["bytes"] == before["bytes"] and after_copy != before):
                raise ConversationError("Rollout was rewritten during capture; retry refresh")
        temp.chmod(0o600)
        # Hash the retained destination. An actively appended source is allowed
        # to differ from this atomic clone or bounded stream snapshot.
        sha = _sha_file(temp)
        captured_bytes = temp.stat().st_size
        after = _fingerprint(path)
        target = directory / f"{sha}.jsonl"
        existed = target.is_file()
        if existed and _sha_file(target) != sha:
            raise ConversationError(f"Existing immutable snapshot failed its SHA-256 check: {target}")
        if existed:
            temp.unlink()
        else:
            os.replace(temp, target)
            target.chmod(0o600)
    finally:
        temp.unlink(missing_ok=True)
    return {"sha256": sha, "bytes": captured_bytes, "raw_path": target,
            "source_size_after_capture": after["bytes"],
            "source_changed_during_capture": before != after or captured_bytes != before["bytes"],
            "source_mtime_ns": before["mtime_ns"], "deduplicated": existed,
            "copy_method": copy_method,
            "source_fingerprint": before if before == after and captured_bytes == before["bytes"] else None,
            "snapshot_fingerprint": _fingerprint(target)}


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_text(item) for item in value)))
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if value.get("type") in ("image", "input_image", "image_url", "audio", "input_audio"):
            return "[媒体内容保留在原始 JSONL；本可读视图不展开二进制内容。]"
        return _text(value.get("content", ""))
    return ""


def _failure_signals(output: Any) -> list[str]:
    signals: set[str] = set()

    def inspect(value: Any, depth: int = 0) -> None:
        if depth > 20:
            return
        if isinstance(value, dict):
            if value.get("type") in ("image", "input_image", "audio", "input_audio"):
                return  # Media is retained verbatim in raw; base64 is not a failure message.
            if value.get("isError") is True or value.get("is_error") is True:
                signals.add("structured_tool_error")
            for field in ("exit_code", "returncode"):
                code = value.get(field)
                if isinstance(code, int) and not isinstance(code, bool) and code != 0:
                    signals.add(f"{field}:{code}")
            if value.get("status") in ("error", "failed", "failure", "rejected"):
                signals.add("structured_failure_status")
            for child in value.values():
                inspect(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                inspect(child, depth + 1)
        elif isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith(("{", "[")):
                try:
                    decoded = json.loads(stripped)
                except ValueError:
                    pass
                else:
                    inspect(decoded, depth + 1)
            for match in re.finditer(r"(?:Process exited with code|exit(?:ed)? (?:with )?(?:code|status))\s*[:=]?\s*(-?\d+)", value, re.I):
                if int(match.group(1)) != 0:
                    signals.add(f"exit_code_text:{match.group(1)}")
            if re.search(r"(?:^|\n)(?:Traceback \(most recent call last\):|Error:|[A-Za-z.]+Error:|Exception:)", value):
                signals.add("error_text_candidate")
    inspect(output)
    return sorted(signals)


def _derive(raw: Path, directory: Path) -> dict:
    messages, failures, records, errors, events = [], [], [], [], []
    calls: dict[str, str] = {}
    counts: Counter = Counter()
    offset = 0
    with raw.open("rb") as stream:
        for number, line in enumerate(stream, 1):
            location = {"line": number, "byte_offset": offset, "byte_length": len(line)}
            offset += len(line)
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("record must be an object")
            except (ValueError, UnicodeDecodeError) as exc:
                errors.append({**location, "error": str(exc), "raw_retained": True})
                continue
            payload = record.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            kind = record.get("type", "unknown")
            counts[kind] += 1
            entry = {**location, "timestamp": record.get("timestamp"), "type": kind,
                     "payload_type": payload.get("type"), "role": payload.get("role"), "call_id": payload.get("call_id")}
            records.append(entry)
            if kind == "response_item":
                item_type, role = payload.get("type"), payload.get("role")
                if item_type == "message" and role in ("user", "assistant") and payload.get("channel") not in ("analysis", "summary", "confidence", "justify"):
                    content = _text(payload.get("content", ""))
                    if content:
                        messages.append({**entry, "role": role, "text": content, "channel": payload.get("channel") or payload.get("phase")})
                if item_type in ("function_call", "custom_tool_call"):
                    calls[payload.get("call_id", "")] = payload.get("name", "unknown")
                if item_type in ("function_call_output", "custom_tool_call_output", "tool_result"):
                    signals = _failure_signals(payload.get("output", payload))
                    if signals:
                        failures.append({**entry, "tool": calls.get(payload.get("call_id", ""), "unknown"),
                                         "signals": signals, "classification": "observed_failure_signal; requires_triage",
                                         "excerpt": _text(payload.get("output", ""))[:2400]})
            elif kind == "event_msg":
                event_type = payload.get("type")
                if event_type in ("user_message", "agent_message"):
                    content = _text(payload.get("message", ""))
                    if content:
                        events.append({**entry, "role": "user" if event_type == "user_message" else "assistant", "text": content})
                if event_type in ("error", "error_msg"):
                    failures.append({**entry, "tool": "runtime_event", "signals": ["runtime_error_event"],
                                     "classification": "observed_failure_signal; requires_triage", "excerpt": _text(payload.get("message", ""))[:2400]})
    # Codex may log both event_msg and response_item mirrors; keep actual repeated
    # user messages, deduplicating only equivalent nearby event mirrors.
    for event in events:
        if not any(message["role"] == event["role"] and message["text"] == event["text"]
                   and abs(message["line"] - event["line"]) <= 12 for message in messages):
            messages.append(event)
    messages.sort(key=lambda item: item["line"])
    _write(directory / "record-index.json", {"schema_version": 1, "record_counts": dict(counts),
           "records": records, "parse_errors": errors, "raw_bytes": offset})
    _write(directory / "tool-failures.json", {"schema_version": 1, "automatic_signal_detection": True,
           "not_a_root_cause_or_resolution_claim": True, "failures": failures})
    transcript = ["# 本地对话可读视图", "", "仅提取已保存原始 JSONL 中可读取的用户与助手消息。"
                  "系统指令、内部推理与工具的完整数据仍留在原始文件；媒体不在此展开。"
                  "本文件可能包含项目私密内容，只保存在此 Work 内。", ""]
    for message in messages:
        transcript.extend([f"## {'用户' if message['role'] == 'user' else '助手'} · 原始行 {message['line']}",
                           "", f"时间：{message.get('timestamp') or '未提供'}", "", message["text"], ""])
    _write(directory / "transcript.md", "\n".join(transcript), text=True)
    return {"record_counts": dict(counts), "parsed_records": sum(counts.values()), "parse_error_count": len(errors),
            "message_count": len(messages), "tool_failure_signal_count": len(failures),
            "compaction_record_count": counts.get("compacted", 0),
            "ends_with_newline": raw.stat().st_size == 0 or _last_byte(raw) == b"\n"}


def _last_byte(path: Path) -> bytes:
    with path.open("rb") as stream:
        stream.seek(-1, os.SEEK_END)
        return stream.read(1)


def refresh_work_conversations(work_dir: Path | str, *, codex_home: Path | str | None = None,
                               verify: bool = False,
                               active_snapshot_interval_seconds: int | None = None) -> dict:
    """Refresh only already-bound conversations, preserving old snapshots on error."""
    skipped = _metadata_only_result(work_dir, "refresh")
    if skipped is not None:
        return skipped
    if active_snapshot_interval_seconds is not None and (
            type(active_snapshot_interval_seconds) is not int or active_snapshot_interval_seconds <= 0):
        raise ValueError("active_snapshot_interval_seconds must be a positive integer or None")
    store, home = _store(work_dir), _home(codex_home)
    if not (store / "bindings.json").is_file():
        return {"status": "unbound", "work_dir": str(store.parents[1]), "results": [],
                "message": "No explicit conversation binding; no conversation was guessed or copied"}
    catalog, warnings = _catalog(home)
    results = []
    with _lock(store):
        bindings = _load(store / "bindings.json", {})
        manifest = _load(store / "manifest.json", {"schema_version": 1, "work_id": store.parents[1].name,
            "privacy": "local_only; raw conversation and derived text must not be promoted to general knowledge",
            "coverage": "local_trace_only", "complete_conversation_claim": False,
            "limitations": ["Only explicitly bound, locally available rollout bytes are retained",
                            "Deleted/remote records, external attachments and separate subagent rollouts may be absent",
                            "Archived in Codex does not mean the Work or its deliverables were accepted"], "threads": {}})
        changed = False
        for binding in bindings.get("bindings", []):
            thread_id = _valid_id(binding["thread_id"])
            history = manifest["threads"].setdefault(thread_id, {"snapshots": [], "observations": []})
            try:
                path, metadata = _resolve(thread_id=thread_id, rollout_path=binding.get("rollout_path"), home=home, catalog=catalog)
                previous = next((item for item in reversed(history["snapshots"])
                                 if item["sha256"] == history.get("latest_sha256")), None)
                previous_raw = store / previous["raw_path"] if previous else None
                source_state, evidence = _state(path, catalog.get(thread_id, {}), home)
                previous_raw_valid = False
                if previous and previous_raw and previous_raw.is_file():
                    try:
                        previous_raw_valid = previous.get("snapshot_fingerprint") == _fingerprint(previous_raw)
                    except OSError:
                        previous_raw_valid = False
                fast_unchanged = bool(not verify and previous and previous.get("source_path") == str(path)
                                      and previous.get("source_fingerprint") == _fingerprint(path)
                                      and previous_raw_valid)
                if fast_unchanged:
                    capture = {**previous, "raw_path": previous_raw, "deduplicated": True}
                    deferral = history.get("active_snapshot_deferral")
                    if isinstance(deferral, dict) and deferral.get("status") == "pending":
                        deferral.update(status="source_matches_last_archive", resolved_at=_now(),
                                        resolved_snapshot_sha256=previous["sha256"])
                        changed = True
                else:
                    if (previous and previous_raw_valid and source_state == "active_snapshot"
                            and active_snapshot_interval_seconds is not None):
                        due, next_capture_after = _active_snapshot_due(previous, active_snapshot_interval_seconds)
                        if not due:
                            observed = _fingerprint(path)
                            deferred = {"status": "pending", "observed_at": _now(),
                                        "source_state": source_state, "source_path": str(path),
                                        "source_fingerprint": observed, "observed_source_bytes": observed["bytes"],
                                        "last_archived_sha256": previous["sha256"],
                                        "last_archived_bytes": previous["bytes"],
                                        "next_capture_after": next_capture_after,
                                        "evidence_scope": "metadata_only_current_source_bytes_not_archived"}
                            prior_deferred = history.get("active_snapshot_deferral")
                            # Keep the observation current without appending a 15-minute event log.
                            if not isinstance(prior_deferred, dict) or any(
                                    prior_deferred.get(key) != deferred.get(key)
                                    for key in ("source_fingerprint", "last_archived_sha256", "next_capture_after")
                            ):
                                history["active_snapshot_deferral"] = deferred
                                changed = True
                            history["source_state"] = source_state
                            results.append({"thread_id": thread_id, "status": "deferred_active_snapshot",
                                            "source_state": source_state,
                                            "last_archived_sha256": previous["sha256"],
                                            "snapshot_count": len(history["snapshots"]),
                                            "verification": "metadata_only_current_source_bytes_not_archived",
                                            "deferred_capture": history.get("active_snapshot_deferral"),
                                            "shared_conversation": bool(binding.get("shared_conversation"))})
                            continue
                    capture = _capture(path, store / "snapshots" / thread_id,
                                       previous_snapshot=previous_raw if previous_raw_valid else None)
                sha, raw = capture["sha256"], capture.pop("raw_path")
                known = next((item for item in history["snapshots"] if item["sha256"] == sha), None)
                if known is None:
                    derived = store / "derived" / thread_id / sha
                    summary = _derive(raw, derived)
                    previous = history["snapshots"][-1] if history["snapshots"] else None
                    append_only = bool(previous and capture["bytes"] > previous["bytes"] and
                                       _sha_file(raw, previous["bytes"]) == previous["sha256"])
                    known = {**capture, "captured_at": _now(), "source_path": str(path),
                             "raw_path": raw.relative_to(store).as_posix(), "derived_path": derived.relative_to(store).as_posix(),
                             "previous_sha256": previous["sha256"] if previous else None,
                             "append_only_from_previous": append_only,
                             "last_full_capture_at": _now(),
                             "delta_bytes": capture["bytes"] - previous["bytes"] if append_only else None,
                             "summary": summary, "byte_preserved": True}
                    history["snapshots"].append(known)
                    changed = True
                elif not fast_unchanged:
                    # A file can move, or timestamps can change, without new
                    # conversation bytes; update the cache without duplicating raw.
                    for key in ("source_fingerprint", "snapshot_fingerprint", "source_path"):
                        value = str(path) if key == "source_path" else capture.get(key)
                        if known.get(key) != value:
                            known[key] = value
                            changed = True
                    known["last_full_capture_at"] = _now()
                    changed = True
                deferral = history.get("active_snapshot_deferral")
                if isinstance(deferral, dict) and deferral.get("status") == "pending":
                    deferral.update(status="captured_after_deferral", resolved_at=_now(),
                                    resolved_snapshot_sha256=sha)
                    changed = True
                derived_dir = store / known["derived_path"]
                if not all((derived_dir / name).is_file() for name in ("transcript.md", "tool-failures.json", "record-index.json")):
                    known["summary"] = _derive(raw, derived_dir)
                    changed = True
                associated = sorted(set(_bound_work_ids(store.parents[1], thread_id) + [store.parents[1].name]))
                shared = bool(binding.get("shared_conversation") or len(associated) > 1)
                observation = {"sha256": sha, "source_state": source_state, "state_evidence": evidence,
                               "source_path": str(path), "associated_work_ids": associated, "shared_conversation": shared}
                previous_observation = history["observations"][-1] if history["observations"] else {}
                observation_changed = any(previous_observation.get(key) != value for key, value in observation.items())
                if observation_changed:
                    history["observations"].append({**observation, "observed_at": _now()})
                    changed = True
                if history.pop("last_error", None) is not None:
                    changed = True
                current_metadata = {"latest_sha256": sha, "source_state": source_state, "source_cwd": metadata.get("cwd"),
                                    "shared_conversation": shared, "associated_work_ids": associated, "coverage": "local_trace_only",
                                    "conversation_role": binding.get("conversation_role", "user_conversation"),
                                    "parent_thread_id": binding.get("parent_thread_id")}
                if any(key not in history or history[key] != value for key, value in current_metadata.items()):
                    changed = True
                history.update(current_metadata)
                results.append({"thread_id": thread_id, "status": "updated" if observation_changed or not capture["deduplicated"] else "unchanged",
                                "source_state": source_state, "sha256": sha, "snapshot_count": len(history["snapshots"]),
                                "verification": "metadata_unchanged" if fast_unchanged else "snapshot_sha256_verified",
                                "shared_conversation": shared, "summary": known["summary"]})
            except (OSError, ValueError, sqlite3.Error) as exc:
                error = f"{type(exc).__name__}: {exc}"
                if history.get("last_error", {}).get("message") != error:
                    history["last_error"] = {"at": _now(), "message": error}
                    changed = True
                results.append({"thread_id": thread_id, "status": "error", "source_state": "source_unavailable",
                                "error": error, "previous_snapshots_preserved": len(history["snapshots"])})
        if changed or not (store / "manifest.json").is_file():
            manifest["updated_at"] = _now()
            _write(store / "manifest.json", manifest)
        errors = sum(item["status"] == "error" for item in results)
        deferred = any(item["status"] == "deferred_active_snapshot" for item in results)
        captured_update = any(item["status"] == "updated" for item in results)
        status = (("error" if errors == len(results) else "partial") if errors else
                  "updated" if captured_update or (changed and not deferred) else
                  "deferred" if deferred else "unchanged")
        return {"status": status, "work_dir": str(store.parents[1]), "manifest_path": str(store / "manifest.json"),
                "coverage": "local_trace_only", "complete_conversation_claim": False, "results": results, "warnings": warnings}


def archive_conversation(work_dir: Path | str, *, thread_id: str | None = None,
                         rollout_path: Path | str | None = None, codex_home: Path | str | None = None,
                         binding_reason: str, shared_conversation: bool = False,
                         parent_thread_id: str | None = None) -> dict:
    skipped = _metadata_only_result(work_dir, "archive")
    if skipped is not None:
        skipped["binding_status"] = "metadata_only"
        return skipped
    binding = bind_conversation(work_dir, thread_id=thread_id, rollout_path=rollout_path, codex_home=codex_home,
                                binding_reason=binding_reason, shared_conversation=shared_conversation,
                                parent_thread_id=parent_thread_id)
    result = refresh_work_conversations(work_dir, codex_home=codex_home)
    result["binding_status"] = binding["status"]
    return result


def refresh_conversation_tree(work_dir: Path | str, *, codex_home: Path | str | None = None,
                              verify: bool = False,
                              active_snapshot_interval_seconds: int | None = None) -> dict:
    """Refresh bound roots plus new subagent traces with exact parent metadata.

    This never guesses a root conversation. A new trace must descend from an
    explicit Work binding through session_meta parent IDs; titles and matching
    working directories cannot authorize inclusion. Cached headers avoid reading
    unrelated conversation bodies or re-parsing unchanged headers on each tick.
    """
    skipped = _metadata_only_result(work_dir, "refresh_tree")
    if skipped is not None:
        return skipped
    if active_snapshot_interval_seconds is not None and (
            type(active_snapshot_interval_seconds) is not int or active_snapshot_interval_seconds <= 0):
        raise ValueError("active_snapshot_interval_seconds must be a positive integer or None")
    store, home = _store(work_dir), _home(codex_home)
    if not (store / "bindings.json").is_file():
        return refresh_work_conversations(work_dir, codex_home=home, verify=verify,
                                          active_snapshot_interval_seconds=active_snapshot_interval_seconds)
    with _lock(store):
        bindings = _load(store / "bindings.json", {"bindings": []})
        known = {item["thread_id"] for item in bindings.get("bindings", [])}
        if not known:
            return {"status": "unbound", "work_dir": str(store.parents[1]), "results": [],
                    "message": "No explicit root binding; child discovery did not run"}
        cache_path = store / "tree-discovery-cache.json"
        cache = _load(cache_path, {"schema_version": 1, "sources": {}})
        sources, warnings, header_reads, cache_hits = {}, [], 0, 0
        for path in _rollouts(home):
            try:
                fingerprint = _fingerprint(path)
                prior = cache.get("sources", {}).get(str(path), {})
                if prior.get("fingerprint") == fingerprint:
                    entry = prior
                    cache_hits += 1
                else:
                    metadata = _metadata(path)
                    entry = {"fingerprint": fingerprint, "thread_id": metadata["thread_id"],
                             "parent_thread_id": _parent_thread(metadata)}
                    header_reads += 1
                sources[str(path)] = entry
            except (OSError, ConversationError) as exc:
                warnings.append(f"Unavailable rollout header {path.name}: {exc}")
        if sources != cache.get("sources") or warnings != cache.get("warnings", []):
            _write(cache_path, {"schema_version": 1, "updated_at": _now(), "sources": sources, "warnings": warnings})
    added, errors = [], []
    pending = dict(sources)
    while pending:
        progressed = False
        for path, entry in list(pending.items()):
            thread_id, parent_id = entry["thread_id"], entry.get("parent_thread_id")
            if thread_id in known or not parent_id:
                pending.pop(path)
                continue
            if parent_id not in known:
                continue
            pending.pop(path)
            try:
                # Resolve authoritative current path and recheck its parent. A
                # stale cached header or duplicate filename cannot auto-bind it.
                result = bind_conversation(work_dir, thread_id=thread_id, codex_home=home,
                    parent_thread_id=parent_id,
                    binding_reason=f"Automatically associated execution trace: session_meta identifies parent {parent_id}, already bound to this Work; no title matching")
                known.add(thread_id)
                progressed = True
                if result["status"] == "bound":
                    added.append({"thread_id": thread_id, "parent_thread_id": parent_id, "conversation_role": "subagent_trace"})
            except (OSError, ValueError) as exc:
                errors.append({"thread_id": thread_id, "parent_thread_id": parent_id, "error": str(exc)})
        if not progressed:
            break
    result = refresh_work_conversations(work_dir, codex_home=home, verify=verify,
                                        active_snapshot_interval_seconds=active_snapshot_interval_seconds)
    result["tree_discovery"] = {"policy": "exact_session_meta_parent_lineage", "new_bindings": added,
        "header_reads": header_reads, "header_cache_hits": cache_hits,
        "unrelated_or_unbound_roots_not_imported": True, "errors": errors, "warnings": warnings,
        "coverage": "local_trace_only; deleted or remote child traces may be absent"}
    if errors and result["status"] not in ("error", "partial"):
        result["status"] = "partial"
    elif added and result["status"] == "unchanged":
        result["status"] = "updated"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover", help="Read exact-cwd candidates; never auto-bind")
    discover.add_argument("--workspace-root", required=True, type=Path)
    discover.add_argument("--codex-home", type=Path)
    for name in ("bind", "archive", "refresh", "refresh-tree"):
        command = commands.add_parser(name)
        command.add_argument("--work-dir", required=True, type=Path)
        command.add_argument("--codex-home", type=Path)
        if name in ("refresh", "refresh-tree"):
            command.add_argument("--verify", action="store_true", help="Rehash source snapshot even when file metadata is unchanged")
        else:
            command.add_argument("--thread-id")
            command.add_argument("--rollout-path", type=Path)
            command.add_argument("--reason", required=True)
            command.add_argument("--shared-conversation", action="store_true")
            command.add_argument("--parent-thread-id", help="Validate exact session_meta parent when binding a subagent trace")
    args = parser.parse_args(argv)
    # ``scripts/archive-work-conversation.py`` imports this one main entry.
    # Keep the guard here, after argparse, so that wrapper and module execution
    # run the same check exactly once and ``--help`` remains usable. Discovery
    # is read-only; every other command may create/update Work archive files.
    if args.command != "discover":
        _require_host_native_python(parser)
    try:
        if args.command == "discover":
            result = discover_conversations(args.workspace_root, args.codex_home)
        elif args.command in ("refresh", "refresh-tree"):
            operation = refresh_conversation_tree if args.command == "refresh-tree" else refresh_work_conversations
            result = operation(args.work_dir, codex_home=args.codex_home, verify=args.verify)
        else:
            operation = bind_conversation if args.command == "bind" else archive_conversation
            result = operation(args.work_dir, thread_id=args.thread_id, rollout_path=args.rollout_path,
                               codex_home=args.codex_home, binding_reason=args.reason,
                               shared_conversation=args.shared_conversation, parent_thread_id=args.parent_thread_id)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result.get("status") in ("error", "partial") else 0


if __name__ == "__main__":
    raise SystemExit(main())
