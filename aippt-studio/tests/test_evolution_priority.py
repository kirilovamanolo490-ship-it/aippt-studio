import hashlib
import json

from aippt_studio import evolution, feedback_audit
from aippt_studio.learning import read_json, write_json


def event(thread, line):
    return {"event_id": f"{thread}-{line}", "thread_id": thread, "work_ids": [thread],
            "text": "完整用户消息", "text_sha256": str(line), "sources": [{"line": line}]}


def test_recent_archive_precedes_backfill_without_losing_thread_order():
    events = [event(thread, line) for thread in ("old", "new") for line in range(10, 0, -1)]
    batch = evolution.select_batch({e["event_id"]: e for e in events}, {}, limit=8, priority_threads={"new"})
    assert [e["thread_id"] for e in batch] == ["new", "new", "new", "old", "new", "new", "new", "old"]
    assert [e["sources"][0]["line"] for e in batch if e["thread_id"] == "new"] == [1, 2, 3, 4, 5, 6]
    assert [e["sources"][0]["line"] for e in batch if e["thread_id"] == "old"] == [1, 2]


def test_one_event_budget_still_gives_history_a_turn():
    events = {e["event_id"]: e for e in (event("old", 1), event("new", 1))}
    assert [evolution.select_batch(events, {}, limit=1, priority_threads={"new"}, priority_turn=i)[0]["thread_id"]
            for i in range(4)] == ["new", "new", "new", "old"]


def test_recent_threads_sort_by_archive_transition_not_uuid():
    events = {e["event_id"]: e for e in (event("a-newest", 1), event("z-older", 1))}
    batch = evolution.select_batch(events, {}, priority_threads={"a-newest": "2026-09-15", "z-older": "2026-09-14"})
    assert batch[0]["thread_id"] == "a-newest"


def test_char_limit_does_not_truncate_and_rotates_history_first():
    events = {e["event_id"]: e for e in (event("old", 1), event("new", 1))}
    for i in range(4):
        batch = evolution.select_batch(events, {}, char_limit=6, priority_threads={"new"}, priority_turn=i)
        assert len(batch) == 1 and batch[0]["text"] == "完整用户消息"
        assert batch[0]["thread_id"] == ("old" if i == 3 else "new")


def test_worker_baselines_old_backfill_and_persists_real_archive_change(tmp_path, monkeypatch):
    root = evolution.directory(tmp_path)
    write_json(root / "config.json", {"enabled": True, "backfill_work_ids": ["1"], "batch_events": 1})
    events = {}
    for work_id, thread in (("1", "a-history"), ("2", "z-recent")):
        work = tmp_path / "work" / work_id
        write_json(work / "work.json", {"status": "review"})
        write_json(work / "workspace/conversations/manifest.json", {"threads": {thread: {
            "source_state": "archived_snapshot", "latest_sha256": "first"}}})
        e = event(thread, 1); e["work_ids"] = [work_id]
        text = e["text"]
        data = (json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}}, ensure_ascii=False) + "\n").encode()
        raw = work / "user.jsonl"; raw.write_bytes(data)
        e["text_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        e["sources"] = [{"raw_path": str(raw), "record_sha256": hashlib.sha256(data).hexdigest(),
                         "line": 1, "byte_offset": 0, "byte_length": len(data)}]
        events[e["event_id"]] = e
    def audit(works, output):
        write_json(output / "feedback-audit.json", {"events": events})
        return {"warnings": []}
    monkeypatch.setattr(feedback_audit, "audit_archived_feedback", audit)
    monkeypatch.setattr(feedback_audit, "record_feedback_review", lambda *a, **kw: None)
    observed = []
    def reviewer(repo, run, batch, *args):
        observed.extend(e["thread_id"] for e in batch)
        return {"summary": "Checked original scope", "rules": [], "repair_candidates": [], "dispositions": [
            {"event_id": e["event_id"], "classification": "not_feedback", "reason": "Fixture", "implementation_gap": ""} for e in batch]}
    evolution.run_evolution(tmp_path, reviewer=reviewer)
    state = read_json(root / "state.json")
    assert observed == ["z-recent"]
    assert state["archive_schedule"]["works"]["1"]["priority_at"] is None
    assert state["archive_schedule"]["works"]["2"]["priority_at"]
    manifest = tmp_path / "work/1/workspace/conversations/manifest.json"
    value = read_json(manifest); value["threads"]["a-history"]["latest_sha256"] = "changed"; write_json(manifest, value)
    evolution.run_evolution(tmp_path, reviewer=reviewer)
    assert read_json(root / "state.json")["archive_schedule"]["works"]["1"]["priority_at"]

