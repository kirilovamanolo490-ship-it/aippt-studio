import json
from pathlib import Path

import pytest

from aippt_studio.learning import compile_lessons, evidence_record, record_event, write_json


def fixture_work(root: Path) -> Path:
    work = root / "work/1"
    (work / "workspace/qa").mkdir(parents=True)
    write_json(work / "work.json", {"work_id": "1", "status": "in_progress", "user_approved": False})
    (work / "workspace/qa/check.txt").write_text("observed native validation passed")
    return work


def event_payload() -> dict:
    return {"kind": "failure", "trigger": "PowerPoint file access", "scope": "macOS native Office export",
            "problem": "export denied", "correction": "Resolve native file access prompt and rerun",
            "verification": "passed", "evidence": ["workspace/qa/check.txt"],
            "reusable": True, "lesson": "Verify native access and successful export before delivery"}


def test_learning_needs_user_completion_review_binding_and_unchanged_validation(tmp_path):
    work = fixture_work(tmp_path)
    event = record_event(work, event_payload())
    assert record_event(work, event_payload())["id"] == event["id"]
    assert len((work / "workspace/learning-events.jsonl").read_text().splitlines()) == 1
    assert not compile_lessons(tmp_path)["lessons"]
    write_json(work / "work.json", {"status": "completed", "user_approved": True})
    assert not compile_lessons(tmp_path)["lessons"]  # Completion alone cannot approve a late event.
    write_json(work / "workspace/qa/final-output-manifest.json", {
        "closure_evidence": [evidence_record(work, "workspace/learning-events.jsonl")]})
    assert len(compile_lessons(tmp_path)["lessons"]) == 1
    (work / "workspace/qa/check.txt").write_text("modified evidence")
    assert not compile_lessons(tmp_path)["lessons"]


def test_reopened_and_superseded_observations_are_not_active_lessons(tmp_path):
    work = fixture_work(tmp_path)
    original = record_event(work, event_payload())
    replacement = {**event_payload(), "lesson": "Updated narrower lesson", "supersedes": original["id"]}
    record_event(work, replacement)
    write_json(work / "workspace/qa/final-output-manifest.json", {
        "closure_evidence": [evidence_record(work, "workspace/learning-events.jsonl")]})
    write_json(work / "work.json", {"status": "completed", "user_approved": True})
    assert [e["lesson"] for e in compile_lessons(tmp_path)["lessons"]] == ["Updated narrower lesson"]
    with pytest.raises(ValueError, match="Reopen"):
        record_event(work, event_payload())
    write_json(work / "work.json", {"status": "in_progress", "user_approved": False})
    assert not compile_lessons(tmp_path)["lessons"]


def test_evidence_cannot_escape_or_pass_without_evidence(tmp_path):
    work = fixture_work(tmp_path)
    (tmp_path / "private.txt").write_text("outside")
    with pytest.raises(ValueError):
        record_event(work, {**event_payload(), "evidence": ["../../private.txt"]})
    with pytest.raises(ValueError, match="requires local"):
        record_event(work, {**event_payload(), "evidence": []})
    with pytest.raises(ValueError, match="boolean"):
        record_event(work, {**event_payload(), "reusable": "yes"})


def test_one_bad_work_does_not_discard_other_work_lessons(tmp_path):
    work = fixture_work(tmp_path)
    record_event(work, event_payload())
    bad = tmp_path / "work/2"
    bad.mkdir()
    (bad / "work.json").write_text("corrupt")
    result = compile_lessons(tmp_path)
    assert len(result["pending"]) == 1
    assert result["errors"][0]["work_id"] == "2"


def test_verified_closure_lesson_is_recalled_and_withdrawn_when_evidence_changes(tmp_path):
    from aippt_studio.context_intelligence import recall_context
    work = fixture_work(tmp_path)
    record_event(work, event_payload())
    write_json(work / "workspace/qa/final-output-manifest.json", {
        "closure_evidence": [evidence_record(work, "workspace/learning-events.jsonl")]})
    write_json(work / "work.json", {"work_id": "1", "status": "completed", "user_approved": True})
    lessons = compile_lessons(tmp_path)
    context = recall_context("PowerPoint file access", tmp_path, current_work_id="2", validated_lessons=lessons["lessons"])
    assert [item["work_id"] for item in context["validated_lessons"]] == ["1"]
    (work / "workspace/qa/check.txt").write_text("validation invalidated")
    context = recall_context("PowerPoint file access", tmp_path, current_work_id="2",
                             validated_lessons=compile_lessons(tmp_path)["lessons"])
    assert context["validated_lessons"] == []


def test_finish_log_append_does_not_invalidate_captured_learning_evidence(tmp_path):
    work = fixture_work(tmp_path)
    log = work / "workspace/worklog.md"
    log.write_text("Verified correction and validation observations before user acceptance")
    payload = {**event_payload(), "evidence": ["workspace/worklog.md"]}
    event = record_event(work, payload)
    assert event["evidence"][0]["path"].startswith("workspace/learning-evidence/")
    assert event["evidence_origins"][0]["original"]["path"] == "workspace/worklog.md"
    assert record_event(work, payload)["id"] == event["id"]
    write_json(work / "workspace/qa/final-output-manifest.json", {
        "closure_evidence": [evidence_record(work, "workspace/learning-events.jsonl")]})
    write_json(work / "work.json", {"work_id": "1", "status": "completed", "user_approved": True})
    log.write_text(log.read_text() + "\nUser accepted; Work completed")
    assert len(compile_lessons(tmp_path)["lessons"]) == 1
    snapshot = work / event["evidence"][0]["path"]
    snapshot.write_text("tampered frozen evidence")
    assert not compile_lessons(tmp_path)["lessons"]
