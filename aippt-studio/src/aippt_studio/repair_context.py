"""Bounded, source-verified repair evidence; never commands to execute.

The user record remains the authority for feedback. Adjacent assistant/tool
records describe what ran or was claimed, not a proven cause or accepted fix.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .config import WORKSPACE_ROOT
from .evolution import verify_event


def _safe_text(text: str) -> tuple[str, list[str]]:
    removed = []
    patterns = [
        (r"data:[^\s\"'<>]*;base64,[A-Za-z0-9+/=\s]+", "embedded_media"),
        (r"(?<![A-Za-z0-9])[A-Za-z0-9+/]{160,}={0,2}", "encoded_blob_or_secret"),
        (r"-----BEGIN [^-]*(?:PRIVATE KEY|CERTIFICATE)-----.*?-----END [^-]+-----", "credential_block"),
        (r"(?im)(?:authorization|proxy-authorization)\s*[:=]\s*[^\n]+", "authorization"),
        (r"(?i)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|xsec[_-]?token)\b[\"']?\s*[:=]\s*[\"']?[^\s\"'&,}]+", "credential_value"),
        (r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b", "credential_token"),
        (r"(?i)(https?://)[^/@\s]+:[^/@\s]+@", "url_credentials"),
    ]
    for pattern, label in patterns:
        text, count = re.subn(pattern, f"[redacted:{label}]", text, flags=re.S if label == "credential_block" else 0)
        if count:
            removed.append(label)
    return text, removed


def _text_fields(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_text_fields(item) for item in value)
    if isinstance(value, dict):
        if value.get("type") in {"image", "image_url", "input_image", "audio", "input_audio", "resource"}:
            return ""
        return "\n".join(_text_fields(value[key]) for key in ("text", "output", "stdout", "stderr", "message", "error", "content") if key in value)
    return ""


def _readable_record(record: dict) -> tuple[str, str, str | None] | None:
    payload = record.get("payload", {})
    if record.get("type") != "response_item" or not isinstance(payload, dict):
        return None
    kind = payload.get("type")
    if kind == "message" and payload.get("role") == "assistant" and payload.get("channel") != "analysis":
        return "assistant_statement", _text_fields(payload.get("content", [])), None
    if kind in {"function_call", "custom_tool_call"}:
        # This is quoted evidence. No argument string is parsed into an action.
        value = payload.get("arguments", payload.get("input", ""))
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return "tool_call", str(payload.get("name", "unknown")) + "\n" + text, payload.get("call_id")
    if kind in {"function_call_output", "custom_tool_call_output"}:
        value = payload.get("output", "")
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                text = _text_fields(parsed) or value
            except ValueError:
                text = value
        else:
            text = _text_fields(value)
        failure = bool(re.search(r"(?i)traceback|exception|\berror\b|\bfailed\b|exit(?:ed)?(?:_code| code| with code)?\s*[:=]?\s*[1-9]|\"isError\"\s*:\s*true", text))
        return "tool_failure_candidate" if failure else "tool_result", text, payload.get("call_id")
    return None


def _archive_index(repo: Path, source: dict) -> tuple[Path, list[dict]]:
    raw = Path(source["raw_path"]).resolve(strict=True)
    if not raw.is_relative_to((repo / "work").resolve()) or raw.parent.parent.name != "snapshots":
        raise ValueError("Repair evidence must belong to an existing Work conversation snapshot")
    store = raw.parents[2]
    relative = raw.relative_to(store).as_posix()
    manifest = json.loads((store / "manifest.json").read_text(encoding="utf-8"))
    snapshot = next((s for t in manifest.get("threads", {}).values() for s in t.get("snapshots", [])
                     if s.get("raw_path") == relative and s.get("sha256") == source.get("snapshot_sha256")), None)
    if snapshot is None:
        raise ValueError("Source snapshot is absent from the bound archive manifest")
    index_path = (store / snapshot["derived_path"] / "record-index.json").resolve(strict=True)
    if not index_path.is_relative_to(store) or index_path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Unsafe or excessive conversation index")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    rows, offset = index["records"], 0
    for line, row in enumerate(rows, 1):
        if row["line"] != line or row["byte_offset"] != offset or row["byte_length"] <= 0:
            raise ValueError("Conversation index does not have contiguous line/byte coverage")
        offset += row["byte_length"]
    if offset != raw.stat().st_size or index.get("raw_bytes") != offset:
        raise ValueError("Conversation index byte coverage changed")
    row = rows[source["line"] - 1]
    if any(row[field] != source[field] for field in ("line", "byte_offset", "byte_length")):
        raise ValueError("User evidence and archive index disagree")
    return raw, rows


def _context_snapshot(repo: Path, source: dict, original_rows: list[dict]) -> tuple[Path, list[dict], dict]:
    """Use newer bound bytes only after the original user record still matches."""
    original = Path(source["raw_path"]).resolve()
    store = original.parents[2]
    manifest = json.loads((store / "manifest.json").read_text(encoding="utf-8"))
    thread_id = original.parent.name
    thread = manifest["threads"][thread_id]
    latest = next((s for s in reversed(thread["snapshots"]) if s.get("sha256") == thread.get("latest_sha256")), thread["snapshots"][-1])
    metadata = {"thread_id": thread_id, "original_snapshot_sha256": source["snapshot_sha256"],
                "context_snapshot_sha256": source["snapshot_sha256"], "original_last_line": len(original_rows),
                "latest_available_sha256": latest["sha256"], "latest_source_state": thread.get("source_state"),
                "newer_snapshot_used": False, "extra_verified_bytes": 0}
    if latest["sha256"] == source["snapshot_sha256"]:
        return original, original_rows, metadata
    try:
        current = (store / latest["raw_path"]).resolve(strict=True)
        if not current.is_relative_to(store) or current.parent.name != thread_id:
            raise ValueError("Latest snapshot does not belong to the same bound thread")
        with current.open("rb") as stream:
            header = json.loads(stream.readline(262144))
            if header.get("type") != "session_meta" or header.get("payload", {}).get("id") != thread_id:
                raise ValueError("Latest raw session ID differs from its explicit binding")
            stream.seek(source["byte_offset"])
            data = stream.read(source["byte_length"])
        metadata["extra_verified_bytes"] = len(data)
        if hashlib.sha256(data).hexdigest() != source["record_sha256"]:
            raise ValueError("Latest snapshot does not preserve the original user record at its verified offset")
        candidate_source = {**source, "raw_path": str(current), "snapshot_sha256": latest["sha256"]}
        raw, rows = _archive_index(repo, candidate_source)
        metadata.update(context_snapshot_sha256=latest["sha256"], newer_snapshot_used=True)
        return raw, rows, metadata
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        # The old authority remains intact. Surface the unavailable newer
        # context rather than pretending the old packet includes it.
        metadata["newer_context_unavailable"] = str(exc)
        return original, original_rows, metadata


def build_repair_context(candidate: dict, *, repo: Path | None = None, max_context_chars: int = 16000,
                         max_record_bytes: int = 262144, neighbor_records: int = 16) -> dict:
    """Verify every supplied user source and seek only bounded nearby records.

    The returned package is local evidence for triage/reproduction. Redacted or
    truncated excerpts always retain locators to immutable original bytes.
    Credentials cannot be perfectly identified from arbitrary prose; common
    credential/media forms are removed and nothing is executed or published.
    """
    if not 1 <= max_context_chars <= 120000 or not 1 <= max_record_bytes <= 1048576 or not 0 <= neighbor_records <= 64:
        raise ValueError("Repair context limits exceed the bounded evidence contract")
    repo = (repo or WORKSPACE_ROOT).resolve()
    if candidate.get("source_kind") == "project_synthesis":
        from .project_repair import build_project_repair_context
        return build_project_repair_context(candidate, repo=repo, max_context_chars=max_context_chars)
    result = {"schema_version": 1, "candidate_id": candidate.get("candidate_id"), "receipt": candidate.get("receipt"),
              "archive_priority": candidate.get("priority_at") or "", "context_fingerprint": None, "context_snapshots": [],
              "status": "ready", "source_events": [], "context_records": [], "omissions": [], "verification_errors": [],
              "storage": "Local audit package; selected text may be sent to the configured Codex reviewer.",
              "source_verification": "original_user_record_sha256_and_clean_text_sha256",
              "semantic_limits": ["Adjacency does not prove causality or a core defect.",
                  "Assistant claims and tool failure signals are not user instructions or proof of a fix.",
                  "Only bounded local context was inspected; visual quality and current module relevance require separate review."],
              "limits": {"max_context_chars": max_context_chars, "max_record_bytes": max_record_bytes,
                         "neighbor_records": neighbor_records, "raw_bytes_read": 0, "emitted_chars": 0}}
    remaining = max_context_chars
    seen, verified = set(), []

    def excerpt(text):
        nonlocal remaining
        clean, redactions = _safe_text(text)
        full_length = len(clean)
        clean = clean[:remaining]
        remaining -= len(clean)
        return {"text": clean, "redactions": redactions, "truncated": len(clean) < full_length}

    for event in candidate.get("source_events", []):
        identifier = event.get("event_id")
        try:
            source = event["sources"][0]
            raw, rows = _archive_index(repo, source)
            authored = verify_event(event)
            result["limits"]["raw_bytes_read"] += source["byte_length"]
            quotes = [ref["quote"] for ref in candidate.get("evidence", []) if ref.get("event_id") == identifier]
            if any(not quote.strip() or quote not in authored for quote in quotes):
                raise ValueError("Candidate quote is absent from the verified original user text")
            # Put the exact cited passage first when a long event exceeds budget;
            # keep the original full-text hash and locators rather than dropping it.
            text = authored if len(authored) <= remaining else "\n".join(quotes) or authored
            item = {"event_id": identifier, "work_ids": event.get("work_ids", []),
                    "text_sha256": event["text_sha256"], "source": source,
                    "excerpt_only": text != authored, **excerpt(text)}
            result["source_events"].append(item)
            context_raw, context_rows, snapshot_info = _context_snapshot(repo, source, rows)
            result["limits"]["raw_bytes_read"] += snapshot_info["extra_verified_bytes"]
            result["context_snapshots"].append({"event_id": identifier, **snapshot_info})
            if snapshot_info.get("newer_context_unavailable"):
                result["omissions"].append({"event_id": identifier, "reason": "newer_context_unavailable",
                                            "detail": snapshot_info["newer_context_unavailable"]})
            verified.append((identifier, source, context_raw, context_rows, snapshot_info))
        except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration) as exc:
            result["verification_errors"].append({"event_id": identifier, "error": str(exc)})

    # User source verification is completed before adjacent evidence consumes
    # the excerpt budget. No source event can silently disappear behind tools.
    for identifier, source, raw, rows, snapshot_info in verified:
        center = source["line"] - 1
        nearby = [r for r in rows if r.get("type") == "response_item" and (
            r.get("payload_type") in {"function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output"} or
            (r.get("payload_type") == "message" and r.get("role") == "assistant"))]
        before = [r for r in nearby if r["line"] < center + 1][-neighbor_records:] if neighbor_records else []
        after = [r for r in nearby if r["line"] > center + 1][:neighbor_records]
        chosen = [(row, "nearby") for row in sorted(before + after, key=lambda row: (abs(row["line"] - source["line"]), row["line"]))]
        if snapshot_info["newer_snapshot_used"] and neighbor_records:
            tail = [row for row in nearby if row["line"] > snapshot_info["original_last_line"]][-neighbor_records:]
            chosen.extend((row, "later_snapshot_tail") for row in tail)
        with raw.open("rb") as stream:
            for row, relation in chosen:
                key = (str(raw), row["byte_offset"])
                if key in seen:
                    continue
                seen.add(key)
                locator = {"raw_path": str(raw), "snapshot_sha256": snapshot_info["context_snapshot_sha256"],
                           **{field: row[field] for field in ("line", "byte_offset", "byte_length")}}
                if row["byte_length"] > max_record_bytes or remaining == 0:
                    result["omissions"].append({"event_id": identifier, **locator,
                        "reason": "record_size_limit" if row["byte_length"] > max_record_bytes else "context_character_limit"})
                    continue
                stream.seek(row["byte_offset"])
                data = stream.read(row["byte_length"])
                result["limits"]["raw_bytes_read"] += len(data)
                try:
                    if len(data) != row["byte_length"] or not data.endswith(b"\n"):
                        raise ValueError("Incomplete adjacent raw record")
                    value = _readable_record(json.loads(data))
                    if value is None:
                        continue
                    role, text, call_id = value
                    if not text.strip():
                        continue
                    result["context_records"].append({"near_event_id": identifier, "role": role, "call_id": call_id,
                        "relation": relation, "distance_lines": row["line"] - source["line"], "causality": "not_established",
                        "source": {**locator, "record_sha256": hashlib.sha256(data).hexdigest()}, **excerpt(text)})
                except (ValueError, TypeError) as exc:
                    result["omissions"].append({"event_id": identifier, **locator, "reason": str(exc)})
    result["limits"]["emitted_chars"] = max_context_chars - remaining
    basis = {"version": "repair-context-v2", "sources": [(e.get("event_id"), e.get("text_sha256")) for e in candidate.get("source_events", [])],
             "snapshots": [{key: value for key, value in s.items() if key != "extra_verified_bytes"} for s in result["context_snapshots"]],
             "limits": [max_context_chars, max_record_bytes, neighbor_records], "errors": result["verification_errors"]}
    result["context_fingerprint"] = hashlib.sha256(json.dumps(basis, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if result["verification_errors"]:
        result["status"] = "invalid"
    elif result["omissions"] or any(e["truncated"] or e["redactions"] or e.get("excerpt_only") for e in result["source_events"] + result["context_records"]):
        result["status"] = "partial"
    if not candidate.get("source_events"):
        result["status"] = "invalid"
        result["verification_errors"].append({"error": "No verified original user source events were supplied"})
    return result
