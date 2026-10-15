"""Local user-feedback evidence and review coverage, never automatic learning.

Only explicitly archived primary user conversations are inspected. Keyword
signals are review candidates, not semantic findings, accepted preferences or
proof that an implementation fixed anything. Raw project text stays local.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable

from .conversations import _fingerprint, _lock, _write


SCHEMA_VERSION = 1
SIGNAL_VERSION = "feedback-candidates-v1"
USER_TEXT_VERSION = "user-provenance-v3"
TOPICS = {
    "decorative_numbering": r"编号|页码|(?:0[1-4][、，,\s/]+)+0[1-4]",
    "decorative_microcopy": r"小字|小串|无聊的文字|意义不明|没有意义|没必要|没有必要|废话|大标题下面|英文|眉题",
    "formal_audience_copy": r"正式|汇报|演讲|工程性|对话|口径核对|核心证据|表达|歧义|精简|精炼",
    "visual_relationships": r"遮挡|重叠|裁切|截取|裁剪|比例|压缩|变形|头像|清晰|图文|抠图|扣背景|白边|白色",
    "reference_and_scope": r"参考|原稿|原PPT|原ppt|原来|原本|保留|作品集|版本|不要改|不能改|混了|不一样",
    "page_and_deck_structure": r"框架|层次|重复|太空|空白|饱满|分区|分块|主次|导航|流程图|每个板块|每一页",
    "palette_and_decoration": r"配色|暗沉|灰色|高亮|圆圈|三角|图标|艺术字|边框|背景",
    "facts_and_evidence": r"数据|专利|证书|营业执照|材料|证据|缺失|遗漏|漏了|少了|问卷|证明|没有.*(?:看到|保留)",
    "native_editability": r"编辑|选择删除|选中|可编辑|动画|放映|母版|不能.*(?:选择|删除|打开)",
    "workflow_and_compatibility": r"归档|结项|确认|权限|脚本|兼容|适配|Windows|windows|window项目|Mac|GitHub|本地模型|发给|传到|插件",
}
NEGATIVE = re.compile(r"不要|不需要|不能|不应该|不对|不是|不行|没有|没给|不够|不合适|不清晰|看不清|太小|太空|太窄|太丑|丑|差劲|混乱|莫名其妙|恶心|还是|反复|重复|重新|调整|问题")


def _read(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _within(store: Path, relative: str) -> Path:
    path = (store / relative).resolve()
    if not path.is_relative_to(store.resolve()):
        raise ValueError("Archive artifact escapes its conversation store")
    return path


def user_authored_text(value: str) -> tuple[str, list[str]]:
    """Separate known transport context from user prose, with honest exclusions.

    Questions embedded in a structured UI answer came from the assistant; only
    the actual answer is user authored. Other quoted/pasted material remains a
    candidate requiring contextual semantic review.
    """
    excluded = []
    reply = re.fullmatch(r"\s*<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>\s*", value, re.S)
    if reply:
        try:
            items = json.loads(reply.group(1))
            if not isinstance(items, list):
                raise ValueError("reply is not a list")
            answers = [item["answer"] for item in items if isinstance(item, dict) and isinstance(item.get("answer"), str)]
        except (ValueError, TypeError):
            return "", ["unreadable_structured_user_reply_requires_context"]
        return "\n".join(answers).strip(), ["assistant_question_in_structured_reply"]
    if "## My request:" in value and value.lstrip().startswith("# Files mentioned by the user:"):
        value = value.split("## My request:", 1)[1]
        excluded.append("attachment_path_preamble")
    if value.lstrip().startswith("# AGENTS.md instructions"):
        return "", ["injected_workspace_instructions"]
    if value.lstrip().startswith("<skill>"):
        return "", ["injected_skill_document"]
    for tag in ("codex_internal_context", "in-app-browser-context", "environment_context", "recommended_plugins", "permissions", "permissions instructions", "app-context", "skills_instructions"):
        escaped = re.escape(tag)
        new = re.sub(rf"<{escaped}(?:\s[^>]*)?>.*?</{escaped}>", "", value, flags=re.S)
        if new != value:
            excluded.append(f"injected_{tag}")
            value = new
    if value.lstrip().startswith("# AGENTS.md instructions"):
        return "", excluded + ["injected_workspace_instructions"]
    if value.lstrip().startswith("## My request:"):
        value = value.lstrip().split("## My request:", 1)[1]
        excluded.append("request_transport_heading")
    value, count = re.subn(r"<image\b[^>]*>.*?</image>", "", value, flags=re.S)
    if count:
        excluded.append("attachment_image_markup")
    value, count = re.subn(r"(?:^|\n)Referenced image files:\s*(?:\n- \[Image #[^\n]*\n?)+", "", value)
    if count:
        excluded.append("attachment_image_paths")
    value = re.sub(r"\[\$[^\]]+\]\([^\n)]+/SKILL\.md\)", "", value)
    value = re.sub(r"^\s*/goal\s*", "", value)
    return value.strip(), excluded


def candidate_signals(text: str) -> list[dict]:
    negative = [{"text": m.group(), "start": m.start(), "end": m.end()} for m in NEGATIVE.finditer(text)]
    signals = []
    for category, pattern in TOPICS.items():
        matches = [{"text": m.group(), "start": m.start(), "end": m.end()} for m in re.finditer(pattern, text, re.I)]
        if matches:
            signals.append({"category": category, "matches": matches,
                            "negative_or_revision_cues": negative,
                            "status": "candidate_requires_semantic_review"})
    return signals


def _primary_snapshots(work_dirs: Iterable[Path | str]) -> tuple[dict, list, list]:
    threads, excluded, warnings = {}, [], []
    for work in sorted({Path(item).resolve() for item in work_dirs}):
        store = work / "workspace/conversations"
        from .conversations import _retention_policy
        retention_mode, retention_error = _retention_policy(work)
        if retention_error is not None:
            warnings.append({"work_id": work.name, "kind": "retention_policy_invalid",
                             "detail": retention_error})
            continue
        try:
            manifest = _read(store / "manifest.json", {})
            bindings = _read(store / "bindings.json", {})
            if not isinstance(manifest, dict) or not isinstance(bindings, dict):
                raise ValueError("Archive manifest and bindings must be objects")
        except (OSError, ValueError) as exc:
            warnings.append({"work_id": work.name, "kind": "unreadable_archive_metadata", "detail": str(exc)})
            continue
        bound = {row.get("thread_id") for row in bindings.get("bindings", [])}
        if not manifest:
            warnings.append({"work_id": work.name, "kind": "no_archive_manifest"})
        if retention_mode in {"metadata_only", "disabled"}:
            matched = False
            for thread_id, thread in manifest.get("threads", {}).items():
                if thread_id not in bound:
                    continue
                if thread.get("conversation_role", "user_conversation") != "user_conversation" or thread.get("parent_thread_id"):
                    excluded.append({"work_id": work.name, "thread_id": thread_id,
                                     "reason": "subagent_trace_is_not_user_feedback"})
                    continue
                matched = True
                excluded.append({"work_id": work.name, "thread_id": thread_id,
                                 "reason": "disabled_by_work_policy" if retention_mode == "disabled" else "metadata_only_retention_policy"})
            if not matched:
                excluded.append({"work_id": work.name, "reason": "disabled_by_work_policy" if retention_mode == "disabled" else "metadata_only_retention_policy",
                                 "detail": "No local raw conversation is retained for this Work"})
            # Manifest paths remain provenance only in metadata_only mode. Do
            # not stat, fingerprint, or try to recreate purged snapshot files.
            continue
        for thread_id, thread in manifest.get("threads", {}).items():
            if thread_id not in bound:
                warnings.append({"work_id": work.name, "thread_id": thread_id, "kind": "not_explicitly_bound"})
                continue
            if thread.get("conversation_role", "user_conversation") != "user_conversation" or thread.get("parent_thread_id"):
                excluded.append({"work_id": work.name, "thread_id": thread_id, "reason": "subagent_trace_is_not_user_feedback"})
                continue
            snapshots = thread.get("snapshots", [])
            snapshot = next((s for s in snapshots if s.get("sha256") == thread.get("latest_sha256")), None)
            if not snapshot:
                warnings.append({"work_id": work.name, "thread_id": thread_id, "kind": "missing_latest_snapshot"})
                continue
            entry = threads.setdefault(thread_id, {"work_ids": [], "thread_id": thread_id, "store": store, "snapshot": snapshot})
            entry["work_ids"].append(work.name)
            if snapshot.get("captured_at", "") > entry["snapshot"].get("captured_at", ""):
                entry.update(store=store, snapshot=snapshot)
    return threads, excluded, warnings


def _payload_text(record: dict) -> str | None:
    payload = record.get("payload", {})
    if not isinstance(payload, dict):
        return None
    if record.get("type") == "event_msg" and payload.get("type") == "user_message":
        return payload.get("message") if isinstance(payload.get("message"), str) else None
    if record.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = payload.get("content", [])
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") in {"input_text", "text"} and isinstance(part.get("text"), str))


def _scan_snapshot(entry: dict, previous: dict, events: dict) -> dict:
    snapshot, store = entry["snapshot"], entry["store"]
    raw = _within(store, snapshot["raw_path"])
    index_path = _within(store, snapshot["derived_path"]) / "record-index.json"
    fingerprint, index_fingerprint = _fingerprint(raw), _fingerprint(index_path)
    verification_bytes = 0
    if snapshot.get("snapshot_fingerprint") and fingerprint != snapshot["snapshot_fingerprint"]:
        # A permission change can alter ctime without changing the original
        # bytes. Verify once against the receipt, cache that exact fingerprint,
        # and never accept modified content under an old SHA.
        if previous.get("verified_sha256") != snapshot["sha256"] or previous.get("fingerprint") != fingerprint:
            digest = hashlib.sha256()
            with raw.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
                    verification_bytes += len(block)
            if digest.hexdigest() != snapshot["sha256"] or _fingerprint(raw) != fingerprint:
                raise ValueError("Snapshot bytes differ from the archival hash receipt")
    same_snapshot = previous.get("extraction_version") == USER_TEXT_VERSION and previous.get("snapshot_sha256") == snapshot["sha256"] and previous.get("fingerprint") == fingerprint and previous.get("index_fingerprint") == index_fingerprint
    if same_snapshot:
        for event_id in previous.get("event_ids", []):
            events[event_id]["work_ids"] = sorted(set(events[event_id]["work_ids"] + entry["work_ids"]))
        return {**previous, "cache_hit": True, "raw_bytes_read_this_run": verification_bytes}
    index = _read(index_path, {})
    if index.get("raw_bytes") != raw.stat().st_size:
        raise ValueError("Record index does not describe the complete available snapshot")
    if index.get("parse_errors"):
        raise ValueError("Archive contains unparsed records; feedback coverage requires archive repair")
    offset = 0
    for number, row in enumerate(index.get("records", []), 1):
        if row.get("line") != number or row.get("byte_offset") != offset or row.get("byte_length", 0) <= 0:
            raise ValueError("Record index has inconsistent JSONL line/byte coverage")
        offset += row["byte_length"]
    if offset != index["raw_bytes"]:
        raise ValueError("Record index does not cover every available byte")
    # Trust only the archive's explicitly verified append relation. A replacement
    # is rescanned rather than treating equal sizes or filenames as a continuation.
    append = bool(previous.get("extraction_version") == USER_TEXT_VERSION and snapshot.get("append_only_from_previous") and previous.get("snapshot_sha256") == snapshot.get("previous_sha256"))
    first_line = max(0, previous.get("last_indexed_line", 0) - 12) if append else 0
    selected = [r for r in index.get("records", []) if r["line"] > first_line and (
        (r.get("type") == "response_item" and r.get("payload_type") == "message" and r.get("role") == "user") or
        (r.get("type") == "event_msg" and r.get("payload_type") == "user_message"))]
    parsed, excluded, bytes_read = [], [], verification_bytes
    with raw.open("rb") as stream:
        header = json.loads(stream.readline())
        metadata = header.get("payload", {})
        if header.get("type") != "session_meta" or (metadata.get("id") or metadata.get("session_id")) != entry["thread_id"]:
            raise ValueError("Snapshot session ID does not match its explicit binding")
        if isinstance(metadata.get("source"), dict) and metadata["source"].get("subagent"):
            raise ValueError("Original session metadata identifies a subagent trace, not a user conversation")
        # Read-only seek and exact JSONL validation: an index cannot invent a
        # quoted user record or turn a tool result into user feedback.
        for row in selected:
            stream.seek(row["byte_offset"])
            raw_record = stream.read(row["byte_length"])
            bytes_read += len(raw_record)
            if len(raw_record) != row["byte_length"] or not raw_record.endswith(b"\n"):
                raise ValueError("Indexed user record is incomplete")
            record = json.loads(raw_record)
            text = _payload_text(record)
            if text is None:
                raise ValueError("Index user role disagrees with original JSONL record")
            authored, excluded_parts = user_authored_text(text)
            source = {"raw_path": str(raw), "original_rollout_path": snapshot.get("source_path"),
                      "snapshot_sha256": snapshot["sha256"], "record_sha256": hashlib.sha256(raw_record).hexdigest(),
                      "line": row["line"], "byte_offset": row["byte_offset"], "byte_length": row["byte_length"],
                      "record_type": record["type"], "timestamp": record.get("timestamp"),
                      "hash_basis": "snapshot_sha256_from_archive_receipt; record_sha256_verified_from_original_bytes"}
            if not authored:
                excluded.append({"source": source, "reason": excluded_parts or ["no_user_text"]})
                continue
            parsed.append({"source": source, "text": authored, "text_sha256": _digest(authored), "excluded_context": excluded_parts})
    response_items = [item for item in parsed if item["source"]["record_type"] == "response_item"]
    event_ids = set(previous.get("event_ids", [])) if append else set()
    mirrors = 0
    for item in parsed:
        source = item["source"]
        if source["record_type"] == "event_msg" and any(other["text"] == item["text"] and abs(other["source"]["line"] - source["line"]) <= 12 for other in response_items):
            mirrors += 1
            continue
        event_id = _digest(f"{entry['thread_id']}:{source['record_type']}:{source['line']}:{source['timestamp']}:{item['text_sha256']}")[:32]
        same_original = next((e for e in events.values() if e["thread_id"] == entry["thread_id"]
                              and any(s["record_sha256"] == source["record_sha256"] and s["line"] == source["line"] for s in e["sources"])), None)
        if same_original:
            event_id = same_original["event_id"]
        # A snapshot may end between the event mirror and actual message. Keep
        # the earlier event ID/review stable when its response arrives later.
        if source["record_type"] == "response_item":
            earlier_mirror = next((e for e in events.values() if e["thread_id"] == entry["thread_id"] and e["text"] == item["text"]
                                   and any(s["record_type"] == "event_msg" and abs(s["line"] - source["line"]) <= 12 for s in e["sources"])), None)
            if earlier_mirror:
                event_id = earlier_mirror["event_id"]
        event_ids.add(event_id)
        existing = events.get(event_id)
        if existing:
            if existing["text"] != item["text"]:
                existing.setdefault("prior_text_versions", []).append({"text": existing["text"], "text_sha256": existing["text_sha256"],
                    "excluded_context": existing["excluded_context"], "reason": "user_provenance_extraction_updated"})
                existing.update(text=item["text"], text_sha256=item["text_sha256"], excluded_context=item["excluded_context"],
                                signals=candidate_signals(item["text"]), signal_version=SIGNAL_VERSION,
                                semantic_status="needs_context" if existing["reviews"] else "unreviewed", review_valid_for_current_text=False)
            existing["work_ids"] = sorted(set(existing["work_ids"] + entry["work_ids"]))
            if not any(s["snapshot_sha256"] == source["snapshot_sha256"] for s in existing["sources"]):
                existing["sources"].append(source)
        else:
            signals = candidate_signals(item["text"])
            events[event_id] = {"event_id": event_id, "thread_id": entry["thread_id"], "work_ids": sorted(set(entry["work_ids"])),
                                "text": item["text"], "text_sha256": item["text_sha256"], "sources": [source],
                                "excluded_context": item["excluded_context"], "signals": signals,
                                "signal_version": SIGNAL_VERSION, "semantic_status": "unreviewed", "reviews": []}
    return {"snapshot_sha256": snapshot["sha256"], "fingerprint": fingerprint, "index_fingerprint": index_fingerprint,
            "extraction_version": USER_TEXT_VERSION,
            "verified_sha256": snapshot["sha256"] if verification_bytes else previous.get("verified_sha256"),
            "event_ids": sorted(event_ids), "last_indexed_line": max((r["line"] for r in index.get("records", [])), default=0),
            "raw_bytes_read_this_run": bytes_read, "cache_hit": False, "incremental_append": append,
            "excluded_records": previous.get("excluded_records", []) + excluded if append else excluded,
            "event_mirrors_excluded": previous.get("event_mirrors_excluded", 0) + mirrors if append else mirrors}


def _report(state: dict) -> dict:
    events = list(state["events"].values())
    candidates = [e for e in events if e["signals"]]
    reviewed = [e for e in events if e["reviews"] and e.get("review_valid_for_current_text", True) and e["reviews"][-1]["classification"] != "needs_context"]
    retention_limited = any(item.get("reason") == "metadata_only_retention_policy"
                             for item in state.get("excluded_threads", []))
    return {"schema_version": SCHEMA_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
            "privacy": "local_only; raw feedback must not be promoted to general knowledge",
            "status": "coverage_incomplete" if state.get("warnings") else
                      "retention_limited" if retention_limited else "candidates_ready_for_review",
            "retention_limited": retention_limited,
            "automatic_semantic_approval": False, "automatic_implementation_claim": False,
            "coverage": {"primary_threads": len(state["scans"]), "user_events": len(events),
                         "candidate_events": len(candidates), "reviewed_events": len(reviewed),
                         "unreviewed_events": len(events) - len(reviewed),
                         "reviewed_candidate_events": sum(bool(e["reviews"]) and e.get("review_valid_for_current_text", True) and e["reviews"][-1]["classification"] != "needs_context" for e in candidates),
                         "processed_event_ids": sorted(e["event_id"] for e in reviewed),
                         "raw_bytes_read_this_run": sum(s["raw_bytes_read_this_run"] for s in state["scans"].values()),
                         "snapshot_cache_hits": sum(bool(s.get("cache_hit")) for s in state["scans"].values()),
                         "excluded_subagent_associations": sum(item.get("reason") == "subagent_trace_is_not_user_feedback"
                                                                for item in state.get("excluded_threads", [])),
                         "metadata_only_excluded_threads": sum(item.get("reason") == "metadata_only_retention_policy"
                                                               for item in state.get("excluded_threads", []))},
            "excluded_threads": state.get("excluded_threads", []),
            "warnings": state.get("warnings", []),
            "limitations": ["Coverage concerns locally available, explicitly bound snapshots only.",
                            "User-supplied quotations or customer feedback remain contextual candidates, not automatically the user's universal preference.",
                            "Repeated identical requests at different original record locations are distinct events.",
                            "Reviewed means an explicit semantic disposition exists; implementation evidence remains a recorded review claim, not automatic proof."]}


def _save(directory: Path, state: dict) -> dict:
    report = _report(state)
    _write(directory / "feedback-audit.json", state)
    _write(directory / "coverage.json", report)
    _write(directory / "candidate-signals.json", {"schema_version": SCHEMA_VERSION, "privacy": report["privacy"],
           "automatic_semantic_approval": False, "events": [e for e in state["events"].values() if e["signals"]]})
    return report


def audit_archived_feedback(work_dirs: Iterable[Path | str], output_dir: Path | str) -> dict:
    """Incrementally inspect exact archived user records across specified Works."""
    directory = Path(output_dir).resolve()
    threads, excluded, warnings = _primary_snapshots(work_dirs)
    with _lock(directory):
        state = _read(directory / "feedback-audit.json", {"schema_version": SCHEMA_VERSION, "events": {}, "scans": {}})
        if state.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported feedback audit schema")
        for scan in state["scans"].values():
            scan.update(raw_bytes_read_this_run=0, cache_hit=False)
        # Preserve the original evidence if an older extractor mistook a
        # transport wrapper for user prose, but remove it from user coverage.
        for event_id, event in list(state["events"].items()):
            authored, reasons = user_authored_text(event["text"])
            if not authored and reasons:
                state.setdefault("excluded_prior_events", {})[event_id] = {**event, "exclusion_reasons": reasons}
                del state["events"][event_id]
        state.update(excluded_threads=excluded, warnings=warnings)
        for thread_id, entry in threads.items():
            try:
                state["scans"][thread_id] = _scan_snapshot(entry, state["scans"].get(thread_id, {}), state["events"])
            except (OSError, ValueError, TypeError, KeyError) as exc:
                state["warnings"].append({"thread_id": thread_id, "work_ids": entry["work_ids"], "kind": "snapshot_audit_failed", "detail": str(exc)})
        for event in state["events"].values():
            if event.get("signal_version") != SIGNAL_VERSION:
                event.update(signals=candidate_signals(event["text"]), signal_version=SIGNAL_VERSION)
        return _save(directory, state)


def audit_work_feedback(work_dir: Path | str, *, output_dir: Path | str | None = None) -> dict:
    work = Path(work_dir).resolve()
    return audit_archived_feedback([work], output_dir or work / "workspace/feedback-audit")


def record_feedback_review(output_dir: Path | str, event_id: str, *, classification: str,
                           reviewer: str, reason: str, implementation_status: str = "unmapped",
                           evidence_refs: Iterable[str] = ()) -> dict:
    """Record an explicit editor's disposition; never called by extraction."""
    if classification not in {"confirmed_feedback", "not_feedback", "needs_context"}:
        raise ValueError("classification must be confirmed_feedback, not_feedback or needs_context")
    if implementation_status not in {"unmapped", "gap", "partial", "implemented", "not_applicable"}:
        raise ValueError("Unsupported implementation_status")
    refs = list(evidence_refs)
    if not reviewer.strip() or not reason.strip() or any(not isinstance(ref, str) or not ref.strip() for ref in refs):
        raise ValueError("Reviewer, contextual reason and valid evidence references are required")
    if implementation_status in {"partial", "implemented"} and not refs:
        raise ValueError("An implementation disposition requires concrete evidence references")
    directory = Path(output_dir).resolve()
    with _lock(directory):
        state = _read(directory / "feedback-audit.json", {})
        if event_id not in state.get("events", {}):
            raise ValueError("Unknown feedback event")
        event = state["events"][event_id]
        disposition = {"reviewer": reviewer, "classification": classification, "reason": reason, "text_sha256": event["text_sha256"],
                       "implementation_status": implementation_status, "evidence_refs": refs}
        if event["reviews"] and {k: v for k, v in event["reviews"][-1].items() if k != "reviewed_at"} == disposition:
            return _report(state)
        event["reviews"].append({"reviewed_at": datetime.now(timezone.utc).isoformat(), **disposition})
        event["semantic_status"] = classification
        event["review_valid_for_current_text"] = True
        return _save(directory, state)
