import json
import pytest

from aippt_studio import context_intelligence, conversations
from aippt_studio.learning import read_json, record_event, write_json
from aippt_studio.maintenance import _active_snapshot_interval_seconds, closure_evidence, initialize_work, run_maintenance


@pytest.fixture(autouse=True)
def isolated_runtime_probe(monkeypatch):
    from aippt_studio import platform_runtime
    monkeypatch.setattr(platform_runtime, "select_artifact_runtime", lambda _: {
        "schema_version": 1, "status": "ready", "environment": {"AIPPT_NODE": "/fixture/node"},
        "host": {"platform": "fixture", "architecture": "fixture"}, "capabilities": [], "optional": True})


def work_fixture(root):
    work = root / "work/1"
    (work / "workspace/qa").mkdir(parents=True)
    (work / "input").mkdir()
    (work / "input/request.md").write_text("原模板只改指定页")
    write_json(work / "work.json", {"work_id": "1", "status": "review", "name": "局部修订"})
    return work


def test_archive_failure_is_not_successful_closure(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    monkeypatch.setattr(conversations, "refresh_conversation_tree", lambda _: {
        "status": "error", "results": [{"error": "bound trace missing"}]})
    result = closure_evidence(work)
    assert result["status"] == "partial"
    assert read_json(work / "workspace/qa/closure-evidence.json")["archive_results"][0]["error"] == "bound trace missing"


def test_bad_generated_cache_is_rebuilt_and_catalog_failure_is_visible(tmp_path, monkeypatch):
    work_fixture(tmp_path)
    cache = tmp_path / "任务知识库/generated/lessons.json"
    cache.parent.mkdir(parents=True)
    cache.write_text("corrupt cache")
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda _: {
        "status": "partial", "errors": [{"source_path": "作品集/broken.pptx", "error": "bad zip"}]})
    result = run_maintenance(tmp_path)
    assert result["status"] == "partial"
    assert read_json(cache)["lessons"] == []
    attention = read_json(tmp_path / "任务知识库/runtime/attention.json")
    assert attention["errors"][0]["stage"] == "catalog"
    unchanged = (tmp_path / "任务知识库/runtime/attention.json").read_bytes()
    run_maintenance(tmp_path)
    assert (tmp_path / "任务知识库/runtime/attention.json").read_bytes() == unchanged
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda _: {"status": "ready", "errors": []})
    assert run_maintenance(tmp_path)["status"] == "ok"
    assert read_json(tmp_path / "任务知识库/runtime/attention.json")["status"] == "ok"


def test_unexpected_catalog_error_updates_last_run_instead_of_leaving_stale_success(tmp_path, monkeypatch):
    work_fixture(tmp_path)
    def fail(_):
        raise RuntimeError("unexpected catalog exception")
    monkeypatch.setattr(context_intelligence, "build_case_index", fail)
    result = run_maintenance(tmp_path)
    assert result["status"] == "partial"
    assert read_json(tmp_path / "任务知识库/runtime/last-run.json")["errors"][0]["error"] == "unexpected catalog exception"


def test_archive_is_independent_of_work_acceptance_and_review_snapshot(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    write_json(work / "workspace/conversations/bindings.json", {})
    write_json(work / "workspace/qa/closure-evidence.json", {"immutable": "review capture"})
    review_bytes = (work / "workspace/qa/closure-evidence.json").read_bytes()
    monkeypatch.setattr(conversations, "refresh_conversation_tree", lambda _, **kwargs: {
        "status": "updated", "results": [{"source_state": "archived_snapshot"}]})
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda _: {"status": "ready", "errors": []})
    assert run_maintenance(tmp_path)["status"] == "ok"
    assert read_json(work / "work.json")["status"] == "review"
    assert (work / "workspace/qa/closure-evidence.json").read_bytes() == review_bytes


def test_maintenance_uses_configurable_active_snapshot_interval_and_accepts_deferred(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    write_json(work / "workspace/conversations/bindings.json", {"bindings": []})
    intervals = []
    def deferred(target, **kwargs):
        intervals.append(kwargs["active_snapshot_interval_seconds"])
        return {"status": "deferred", "results": [{"status": "deferred_active_snapshot"}]}
    monkeypatch.setattr(conversations, "refresh_conversation_tree", deferred)
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda _: {"status": "ready", "errors": []})

    result = run_maintenance(tmp_path, include_model_reviews=False,
                             active_snapshot_interval_seconds=2 * 60 * 60)

    assert result["status"] == "ok"
    assert intervals == [2 * 60 * 60]
    assert result["works"][0]["archive_status"] == "deferred"


def test_maintenance_active_snapshot_interval_environment_is_configurable(tmp_path, monkeypatch):
    work_fixture(tmp_path)
    monkeypatch.setenv("AIPPT_MAINTENANCE_ACTIVE_SNAPSHOT_INTERVAL_SECONDS", "1200")
    resolved = _active_snapshot_interval_seconds(None)
    assert resolved == 1200
    with pytest.raises(ValueError):
        _active_snapshot_interval_seconds("0")


def test_initialize_keeps_context_available_when_trace_is_unavailable(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    def missing(*args, **kwargs):
        raise ValueError("no local trace")
    monkeypatch.setattr(conversations, "archive_conversation", missing)
    monkeypatch.setattr(context_intelligence, "recall_context", lambda *a, **k: {"status": "no_match"})
    result = initialize_work(tmp_path, work, thread_id="current-thread")
    assert result["status"] == "partial"
    assert read_json(work / "workspace/assistant-context.json")["status"] == "no_match"
    assert result["errors"][0]["stage"] == "conversation"
    assert read_json(work / "workspace/pending-conversation-binding.json")["thread_id"] == "current-thread"


def test_minimal_work_initialization_skips_conversation_and_duplicate_context(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    write_json(work / "work.json", {"work_id": "1", "status": "intake", "name": "精简任务",
                                     "storage_profile": "minimal", "conversation_retention": "disabled"})
    monkeypatch.setattr(context_intelligence, "recall_context", lambda *_a, **_k: pytest.fail("minimal context must not be generated"))
    result = initialize_work(tmp_path, work, thread_id="current-thread")
    assert result["status"] == "ready"
    assert result["archive"]["status"] == "disabled"
    assert read_json(work / "workspace/runtime-environment.json")["status"] == "ready"
    assert not (work / "workspace/conversations").exists()
    assert not (work / "workspace/pending-conversation-binding.json").exists()
    assert not (work / "workspace/assistant-context.json").exists()
    assert not (work / "workspace/startup-diagnostics.json").exists()


def test_returned_archive_error_is_not_successful_initialization(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    monkeypatch.setattr(conversations, "archive_conversation", lambda *a, **k: {"status": "updated"})
    monkeypatch.setattr(conversations, "refresh_conversation_tree", lambda *a, **k: {"status": "partial", "results": [{"error": "missing child trace"}]})
    monkeypatch.setattr(context_intelligence, "recall_context", lambda *a, **k: {"status": "no_match", "catalog_status": "ready"})
    result = initialize_work(tmp_path, work, thread_id="current")
    assert result["status"] == "partial"
    assert result["errors"][0]["stage"] == "conversation"


def test_incomplete_catalog_is_visible_in_initialization(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    monkeypatch.setattr(context_intelligence, "recall_context", lambda *a, **k: {"status": "no_match", "catalog_status": "partial"})
    assert initialize_work(tmp_path, work)["status"] == "partial"


def test_manual_maintenance_does_not_overwrite_background_execution_evidence(tmp_path, monkeypatch):
    work_fixture(tmp_path)
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda _: {"status": "ready", "errors": []})
    monkeypatch.setenv("XPC_SERVICE_NAME", "app.aippt.knowledge-maintenance")
    run_maintenance(tmp_path)
    worker = tmp_path / "任务知识库/runtime/last-worker-run.json"
    evidence = worker.read_bytes()
    monkeypatch.setenv("XPC_SERVICE_NAME", "0")
    monkeypatch.delenv("AIPPT_MAINTENANCE_INSTANCE", raising=False)
    run_maintenance(tmp_path)
    assert worker.read_bytes() == evidence
    assert read_json(tmp_path / "任务知识库/runtime/last-run.json")["worker_service"] == "0"


def test_explicit_pending_binding_recovers_after_trace_appears(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    pending = work / "workspace/pending-conversation-binding.json"
    write_json(pending, {"thread_id": "known-thread", "binding_reason": "explicit current thread"})
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda _: {"status": "ready", "errors": []})
    def unavailable(*a, **k):
        raise ValueError("trace not yet on disk")
    monkeypatch.setattr(conversations, "bind_conversation", unavailable)
    assert run_maintenance(tmp_path)["status"] == "partial"
    assert pending.is_file()
    assert closure_evidence(work)["status"] == "partial"
    calls = []
    def recovered(target, **kwargs):
        calls.append(kwargs["thread_id"])
        write_json(target / "workspace/conversations/bindings.json", {"bindings": []})
        return {"status": "bound"}
    monkeypatch.setattr(conversations, "bind_conversation", recovered)
    monkeypatch.setattr(conversations, "refresh_conversation_tree", lambda _, **kwargs: {"status": "updated", "results": []})
    assert run_maintenance(tmp_path)["status"] == "ok"
    assert calls == ["known-thread"]
    assert not pending.exists()


def test_review_captures_validation_files_and_rejects_stale_passed_events(tmp_path):
    work = work_fixture(tmp_path)
    evidence = work / "workspace/qa/native-check.txt"
    evidence.write_text("native validation passed")
    record_event(work, {"kind": "failure", "trigger": "native export", "scope": "Mac export", "correction": "rerun native check",
                        "verification": "passed", "evidence": ["workspace/qa/native-check.txt"]})
    result = closure_evidence(work)
    assert result["status"] == "ready"
    assert "workspace/qa/native-check.txt" in {item["path"] for item in result["records"]}
    evidence.write_text("validation changed")
    result = closure_evidence(work)
    assert result["status"] == "partial"
    assert result["event_verification_errors"]


def test_metadata_only_closure_reports_missing_local_conversation_without_claiming_ready(tmp_path):
    work = work_fixture(tmp_path)
    store = work / "workspace/conversations"
    store.mkdir(parents=True)
    thread_id = "11111111-1111-4111-8111-111111111111"
    write_json(store / "retention-policy.json", {"schema_version": 1, "mode": "metadata_only"})
    write_json(store / "bindings.json", {"bindings": [{"thread_id": thread_id}]})
    write_json(store / "manifest.json", {"threads": {thread_id: {
        "latest_sha256": "a" * 64,
        "snapshots": [{"sha256": "a" * 64, "raw_path": f"snapshots/{thread_id}/purged.jsonl",
                       "derived_path": f"derived/{thread_id}/purged"}],
    }}})

    result = closure_evidence(work)

    assert result["status"] == "partial"
    assert result["archive_status"] == "metadata_only"
    assert result["conversation_coverage"] == {
        "status": "retention_limited", "local_raw_available": False,
        "reason": "metadata_only_retention_policy", "bound_threads": 1,
    }
    assert result["feedback_coverage"]["status"] == "retention_limited"
    assert not any("workspace/conversations/snapshots/" in item["path"] for item in result["records"])


def test_invalid_retention_policy_binding_status_makes_closure_partial(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    monkeypatch.setattr("aippt_studio.maintenance.retry_pending_binding", lambda _: {
        "status": "retention_policy_invalid", "error": "unsupported mode"})
    result = closure_evidence(work)
    assert result["status"] == "partial"
    assert result["binding_retry"]["status"] == "retention_policy_invalid"


def test_metadata_only_closure_never_hashes_residual_raw_snapshot(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    store = work / "workspace/conversations"
    raw = store / "snapshots/thread/a.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"residual raw conversation")
    write_json(store / "retention-policy.json", {"mode": "metadata_only"})
    from aippt_studio import maintenance
    original = maintenance.evidence_record
    def checked(target, relative):
        if relative.startswith("workspace/conversations/snapshots/"):
            raise AssertionError("closure must not hash residual raw conversation")
        return original(target, relative)
    monkeypatch.setattr(maintenance, "evidence_record", checked)
    result = closure_evidence(work)
    assert result["status"] == "partial"
    assert not any("conversations/snapshots/" in row["path"] for row in result["records"])


def test_invalid_policy_closure_skips_feedback_and_raw_snapshot_reads(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    store = work / "workspace/conversations"
    raw = store / "snapshots/thread/a.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"raw conversation")
    write_json(store / "retention-policy.json", {"mode": "misspelled"})
    monkeypatch.setattr("aippt_studio.feedback_audit.audit_work_feedback", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("invalid policy must not start feedback audit")))
    result = closure_evidence(work)
    assert result["status"] == "partial"
    assert result["archive_status"] == "retention_policy_invalid"
    assert not any("conversations/snapshots/" in row["path"] for row in result["records"])


def test_metadata_only_closure_skips_passed_event_snapshot_evidence(tmp_path, monkeypatch):
    work = work_fixture(tmp_path)
    store = work / "workspace/conversations"
    raw = store / "snapshots/thread/a.jsonl"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"residual raw conversation")
    record_event(work, {"kind": "failure", "trigger": "conversation capture", "scope": "local evidence",
                        "correction": "review exact record", "verification": "passed",
                        "evidence": ["workspace/conversations/snapshots/thread/a.jsonl"]})
    events_path = work / "workspace/learning-events.jsonl"
    event = json.loads(events_path.read_text(encoding="utf-8"))
    event["evidence"][0]["path"] = "workspace/other/../conversations/snapshots/thread/a.jsonl"
    events_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
    write_json(store / "retention-policy.json", {"mode": "metadata_only"})
    monkeypatch.setattr("aippt_studio.maintenance.evidence_valid", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("retained snapshot evidence must not be re-read")))
    result = closure_evidence(work)
    assert result["status"] == "partial"
    assert any(item["error"] == "Conversation snapshot evidence is intentionally unavailable under the retention policy"
               for item in result["event_verification_errors"])
