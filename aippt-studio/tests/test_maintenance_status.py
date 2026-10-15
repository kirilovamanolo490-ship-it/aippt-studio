"""Maintenance health reflects real stage outcomes, without invoking models."""
import copy

import pytest

from aippt_studio import context_intelligence, core_evolution, evolution, maintenance, project_learning
from aippt_studio.learning import read_json, write_json


STAGES = ("project_review", "semantic_learning", "core_evolution")
SUCCEEDED = {"project_review": "review_complete", "semantic_learning": "reviewed", "core_evolution": "verified_applied"}


@pytest.fixture
def simulated_stages(monkeypatch):
    values = {stage: {"status": "pending", "reason": "fixture budget wait"} for stage in STAGES}
    monkeypatch.setattr(maintenance, "compile_lessons", lambda _: {"lessons": [], "pending": [], "errors": []})
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda _: {"status": "ready", "errors": []})
    monkeypatch.setattr(project_learning, "run_project_learning", lambda _: copy.deepcopy(values["project_review"]))
    monkeypatch.setattr(evolution, "run_evolution", lambda _: copy.deepcopy(values["semantic_learning"]))
    monkeypatch.setattr(core_evolution, "run_core_evolution", lambda _: copy.deepcopy(values["core_evolution"]))
    monkeypatch.delenv("AIPPT_MAINTENANCE_INSTANCE", raising=False)
    monkeypatch.setenv("XPC_SERVICE_NAME", "0")
    return values


def seed_attention(repo, stages=STAGES):
    errors = [{"stage": stage, "error": f"{stage}: earlier model failure"} for stage in stages]
    write_json(repo / "任务知识库/runtime/attention.json", {"at": "prior", "status": "partial", "errors": errors})
    return errors


@pytest.mark.parametrize("stage", STAGES)
@pytest.mark.parametrize("status", ["queued", "already_running", "pending", "disabled", "unchanged", "retry_wait", "future_unknown_status"])
def test_waiting_stage_cannot_clear_earlier_failure(tmp_path, simulated_stages, stage, status):
    errors = seed_attention(tmp_path, [stage])
    simulated_stages[stage] = {"status": status}
    before = (tmp_path / "任务知识库/runtime/attention.json").read_bytes()
    for _ in range(2):
        result = maintenance.run_maintenance(tmp_path)
        assert result["status"] == "partial"
        assert result["errors"] == errors
        assert result["preserved_model_errors"] == errors
        assert read_json(tmp_path / "任务知识库/runtime/last-run.json")["status"] == "partial"
    assert (tmp_path / "任务知识库/runtime/attention.json").read_bytes() == before


@pytest.mark.parametrize("stage,status", [
    ("project_review", "progress"), ("project_review", "review_complete"),
    ("semantic_learning", "learned"), ("semantic_learning", "reviewed"),
    ("core_evolution", "diagnosed"), ("core_evolution", "verified_applied"),
])
def test_successful_retry_only_clears_its_own_stage(tmp_path, simulated_stages, stage, status):
    previous = seed_attention(tmp_path)
    simulated_stages[stage] = {"status": status}
    result = maintenance.run_maintenance(tmp_path)
    expected = [row for row in previous if row["stage"] != stage]
    assert result["status"] == "partial"
    assert result["errors"] == expected
    assert read_json(tmp_path / "任务知识库/runtime/attention.json")["errors"] == expected


def test_all_clean_actual_retries_clear_attention(tmp_path, simulated_stages):
    seed_attention(tmp_path)
    for stage in STAGES:
        simulated_stages[stage] = {"status": SUCCEEDED[stage]}
    result = maintenance.run_maintenance(tmp_path)
    assert result["status"] == result["attention_status"] == "ok"
    assert result["errors"] == result["preserved_model_errors"] == []
    assert read_json(tmp_path / "任务知识库/runtime/attention.json")["status"] == "ok"


@pytest.mark.parametrize("stage,value", [
    ("project_review", {"status": "progress", "errors": [{"work_id": "2", "error": "Another original source is missing"}]}),
    ("project_review", {"status": "review_complete", "report_status": "pending", "report_error": "Report write failed"}),
    ("semantic_learning", {"status": "learned", "projection_errors": [{"error": "Projection unavailable"}]}),
    ("core_evolution", {"status": "diagnosed", "errors": [{"candidate_id": "fixture", "error": "Other source validation failed"}]}),
])
def test_progress_with_errors_is_not_a_clean_stage(tmp_path, simulated_stages, stage, value):
    seed_attention(tmp_path, [stage])
    simulated_stages[stage] = value
    result = maintenance.run_maintenance(tmp_path)
    assert result["status"] == "partial"
    assert result["preserved_model_errors"] == []  # meaningful newer failure replaces old details
    assert result["errors"] == [{"stage": stage, "result": value}]
    assert read_json(tmp_path / "任务知识库/runtime/attention.json")["errors"] == result["errors"]


def test_new_failure_replaces_old_details_without_accumulation(tmp_path, simulated_stages):
    seed_attention(tmp_path, ["project_review"])
    simulated_stages["project_review"] = {"status": "needs_attention", "error": "New retry failure"}
    result = maintenance.run_maintenance(tmp_path)
    assert result["status"] == "partial"
    assert result["errors"] == [{"stage": "project_review", "result": simulated_stages["project_review"]}]
    assert maintenance.run_maintenance(tmp_path)["errors"] == result["errors"]


def test_local_refresh_remains_successful_but_keeps_overall_attention(tmp_path, simulated_stages):
    previous = seed_attention(tmp_path)
    result = maintenance.run_maintenance(tmp_path, include_model_reviews=False)
    assert result["status"] == "ok"
    assert result["attention_status"] == "partial"
    assert result["preserved_model_errors"] == previous
    assert result["errors"] == []
    assert read_json(tmp_path / "任务知识库/runtime/attention.json")["errors"] == previous


def test_true_worker_records_pending_failure_and_manual_run_cannot_replace_it(tmp_path, simulated_stages, monkeypatch):
    previous = seed_attention(tmp_path, ["project_review"])
    simulated_stages["project_review"] = {"status": "already_running"}
    monkeypatch.setenv("AIPPT_MAINTENANCE_INSTANCE", "fixture-worker-identity")
    monkeypatch.setenv("XPC_SERVICE_NAME", "app.aippt.knowledge-maintenance")
    maintenance.run_maintenance(tmp_path)
    worker_path = tmp_path / "任务知识库/runtime/last-worker-run.json"
    record = read_json(worker_path)
    assert record["worker_instance"] == "fixture-worker-identity"
    assert record["status"] == "partial"
    assert record["errors"] == previous
    before = worker_path.read_bytes()
    monkeypatch.delenv("AIPPT_MAINTENANCE_INSTANCE")
    monkeypatch.setenv("XPC_SERVICE_NAME", "0")
    maintenance.run_maintenance(tmp_path, include_model_reviews=False)
    assert worker_path.read_bytes() == before


def test_current_model_result_reads_latest_attention_after_other_writer(tmp_path, simulated_stages, monkeypatch):
    seed_attention(tmp_path, ["project_review"])
    updated = [{"stage": "semantic_learning", "error": "Concurrent stage failure"}]

    def project_run(repo):
        write_json(repo / "任务知识库/runtime/attention.json", {"at": "new", "status": "partial", "errors": updated})
        return {"status": "review_complete"}

    monkeypatch.setattr(project_learning, "run_project_learning", project_run)
    result = maintenance.run_maintenance(tmp_path)
    assert result["status"] == "partial"
    assert result["errors"] == updated


def test_old_nonmodel_errors_can_clear_after_actual_local_refresh(tmp_path, simulated_stages):
    previous = seed_attention(tmp_path, ["project_review", "catalog"])
    result = maintenance.run_maintenance(tmp_path)
    assert result["errors"] == previous[:1]


def test_fallback_last_run_error_is_not_lost_without_attention_file(tmp_path, simulated_stages):
    errors = [{"stage": "project_review", "error": "Prior worker model failure"}]
    write_json(tmp_path / "任务知识库/runtime/last-run.json", {"status": "partial", "errors": errors})
    result = maintenance.run_maintenance(tmp_path)
    assert result["status"] == "partial"
    assert result["errors"] == errors
    assert read_json(tmp_path / "任务知识库/runtime/attention.json")["errors"] == errors


def test_local_only_promotes_fallback_error_to_durable_attention(tmp_path, simulated_stages):
    errors = [{"stage": "project_review", "error": "Worker failure predating attention file"}]
    write_json(tmp_path / "任务知识库/runtime/last-run.json", {"status": "partial", "errors": errors})
    for _ in range(2):
        result = maintenance.run_maintenance(tmp_path, include_model_reviews=False)
        assert result["status"] == "ok"
        assert result["preserved_model_errors"] == errors
        assert read_json(tmp_path / "任务知识库/runtime/attention.json")["errors"] == errors
