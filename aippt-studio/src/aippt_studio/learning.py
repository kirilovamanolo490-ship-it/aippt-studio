"""Evidence-backed feedback memory; generated indexes never alter approved rules."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def evidence_record(work: Path, relative: str) -> dict:
    path = (work / relative).resolve()
    if not path.is_relative_to(work.resolve()) or not path.is_file():
        raise ValueError(f"Evidence must be an existing file inside this Work: {relative}")
    data = path.read_bytes()
    return {"path": path.relative_to(work.resolve()).as_posix(), "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}


_LIFECYCLE_EVIDENCE = {"workspace/worklog.md", "workspace/completion-report.md", "output/工作完成报告.md"}


def event_evidence(work: Path, relative: str) -> tuple[dict, dict | None]:
    """Freeze lifecycle-mutated prose; ordinary QA must still invalidate on change."""
    original = evidence_record(work, relative)
    if original["path"] not in _LIFECYCLE_EVIDENCE:
        return original, None
    data = (work / original["path"]).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != original["sha256"]:
        raise ValueError("Evidence changed during capture; retry recording the event")
    destination = work / "workspace/learning-evidence" / digest / Path(original["path"]).name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if destination.read_bytes() != data:
            raise ValueError("Immutable learning evidence was modified")
    else:
        with tempfile.NamedTemporaryFile("wb", dir=destination.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    captured = evidence_record(work, destination.relative_to(work).as_posix())
    return captured, {"original": original, "snapshot_path": captured["path"],
                      "reason": "Lifecycle appends must not invalidate approved historical evidence"}


def record_event(work: Path, payload: dict) -> dict:
    """Append a scoped, immutable observation. Reuse requires evidence and closure."""
    work = work.resolve()
    if not (work / "work.json").is_file():
        raise ValueError("Not a Work package")
    if read_json(work / "work.json", {}).get("status") == "completed":
        raise ValueError("Reopen a completed Work before adding new learning events")
    allowed = {"kind", "trigger", "user_quote", "problem", "correction", "scope", "lesson",
               "verification", "evidence", "reusable", "tool", "supersedes"}
    if set(payload) - allowed:
        raise ValueError(f"Unknown event fields: {sorted(set(payload) - allowed)}")
    if payload.get("kind") not in {"feedback", "failure", "decision", "tool_use"}:
        raise ValueError("kind must be feedback, failure, decision or tool_use")
    for key in ("trigger", "scope", "correction"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    if payload.get("verification", "unverified") not in {"passed", "failed", "unverified"}:
        raise ValueError("verification must be passed, failed or unverified")
    if "reusable" in payload and not isinstance(payload["reusable"], bool):
        raise ValueError("reusable must be a boolean")
    if not isinstance(payload.get("evidence", []), list):
        raise ValueError("evidence must be a list of Work-relative file paths")
    captured = [event_evidence(work, p) for p in payload.get("evidence", [])]
    evidence = [item[0] for item in captured]
    evidence_origins = [item[1] for item in captured if item[1] is not None]
    if payload.get("verification") == "passed" and not evidence:
        raise ValueError("passed requires local validation evidence")
    if payload.get("reusable") and not str(payload.get("lesson", "")).strip():
        raise ValueError("reusable requires an explicit, scoped lesson")
    event = {**payload, "verification": payload.get("verification", "unverified"), "evidence": evidence}
    if evidence_origins:
        event["evidence_origins"] = evidence_origins
    digest = hashlib.sha256(json.dumps(event, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    path = work / "workspace/learning-events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock"):
        existing = load_events(work)
        if any(e["id"] == digest for e in existing):
            return next(e for e in existing if e["id"] == digest)
        if payload.get("supersedes") and payload["supersedes"] not in {e["id"] for e in existing}:
            raise ValueError("supersedes must reference an existing event in this Work")
        event = {"schema_version": 1, "id": digest, "recorded_at": now(), **event}
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return event


def load_events(work: Path) -> list[dict]:
    path = work / "workspace/learning-events.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evidence_valid(work: Path, records: list[dict]) -> bool:
    try:
        return bool(records) and all(evidence_record(work, r["path"]) == r for r in records)
    except (ValueError, OSError, KeyError, TypeError):
        return False


def compile_lessons(repo: Path) -> dict:
    """Rebuild reversible retrieval data from explicitly recorded, verified events."""
    lessons, pending, errors = [], [], []
    for work in sorted((repo / "work").glob("[0-9]*")):
        if not work.name.isdigit() or not (work / "work.json").is_file():
            continue
        try:
            record = read_json(work / "work.json")
            events = load_events(work)
            review = read_json(work / "workspace/qa/final-output-manifest.json", {})
            event_reviewed = any(r.get("path") == "workspace/learning-events.jsonl"
                                 and evidence_valid(work, [r])
                                 for r in review.get("closure_evidence", []))
            superseded = {e.get("supersedes") for e in events}
            for event in events:
                if not event.get("reusable") or event["id"] in superseded:
                    continue
                eligible = (record.get("status") == "completed" and record.get("user_approved") is True and event_reviewed
                            and event.get("verification") == "passed" and evidence_valid(work, event["evidence"]))
                item = {"work_id": work.name, "event_id": event["id"], "scope": event["scope"],
                        "trigger": event["trigger"], "lesson": event["lesson"],
                        "source": f"work/{work.name}/workspace/learning-events.jsonl",
                        "source_path": f"work/{work.name}/workspace/learning-events.jsonl",
                        "source_sha256": hashlib.sha256((work / "workspace/learning-events.jsonl").read_bytes()).hexdigest(),
                        "evidence": event["evidence"], "status": "verified" if eligible else "pending",
                        "advisory_only": True}
                (lessons if eligible else pending).append(item)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            errors.append({"work_id": work.name, "error": str(exc)})
    result = {"schema_version": 1, "lessons": lessons, "pending": pending, "errors": errors,
              "policy": "Only explicit scoped lessons from approved completed Works with unchanged validation evidence are recalled. No rules or code are self-modified."}
    path = repo / "任务知识库/generated/lessons.json"
    try:
        previous = read_json(path)
    except (ValueError, OSError):
        previous = None  # Derived data is rebuildable; source records remain authoritative.
    if previous != result:
        write_json(path, result)
    return result


def retrospective_draft(work: Path) -> Path:
    record = read_json(work / "work.json", {})
    events = load_events(work)
    lines = [f"# Work {work.name} 复盘证据草稿", "", "此文件为自动汇集，不代表人工语义审阅或用户验收。", "",
             f"- 任务：{record.get('name', '')}", f"- 状态：{record.get('status', 'unknown')}", "",
             "## 原始证据", "", "- 用户要求：input/request.md", "- 操作记录：workspace/worklog.md",
             "- 对话绑定、快照与完整性：workspace/conversations/", "- 验证记录：workspace/qa/", "",
             "## 问题、反馈与修正", ""]
    for event in events:
        lines += [f"### {event['kind']} · {event['trigger']}", "", f"- 原话：{event.get('user_quote', '未记录直接原话')}",
                  f"- 问题：{event.get('problem', '')}", f"- 适用范围：{event['scope']}",
                  f"- 修正：{event['correction']}", f"- 验证：{event['verification']}",
                  f"- 证据：{', '.join(r['path'] for r in event['evidence']) or '无'}", ""]
    if not events:
        lines += ["尚无结构化事件；这不表示本任务未出现问题。请根据原始对话和 QA 写复盘。", ""]
    lines += ["## 知识处理", "", "自动草稿不得直接冒充正式复盘。当前助手应检查失败原因、修正与验证，写独立任务条目；通用经验需保留适用范围和反例。", ""]
    path = work / "workspace/retrospective-draft.md"
    text = "\n".join(lines)
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path
