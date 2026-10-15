"""Closure priority belongs to one reviewed manifest, not every raw append."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from aippt_studio import maintenance, project_learning as pl, project_repair
from aippt_studio.learning import read_json, write_json
from test_project_learning import archive, message


CAPTURED = "2026-09-15T10:00:00+00:00"
PREPARED = "2026-09-15T10:00:30+00:00"
REQUESTED = "2026-09-15T10:00:31+00:00"
COMPLETED = "2026-09-15T10:01:00+00:00"


def review_package(repo, *, legacy=False):
    work, _, _ = archive(repo, [message("user", "归档")])
    record = read_json(work / "work.json")
    record.update(status="review", user_approved=False, review_prepared_at=PREPARED)
    write_json(work / "work.json", record)
    closure = {"schema_version": 1, "work_id": "1", "status": "ready", "generated_at": CAPTURED}
    if legacy:
        old = {"schema_version": 1, "work_id": "1", "reason": "closure", "signature": "old-before-review",
               "requested_at": "2026-09-15T10:00:01+00:00", "status": "requested"}
        closure["project_review"] = old
        write_json(pl.directory(repo) / "requests/1.json", old)
    closure_path = work / "workspace/qa/closure-evidence.json"
    write_json(closure_path, closure)
    write_json(work / "workspace/qa/final-output-manifest.json", {
        "schema_version": 1, "work_id": "1", "status": "review", "generated_at": PREPARED,
        "closure_evidence": [{"path": "workspace/qa/closure-evidence.json", "bytes": closure_path.stat().st_size,
                              "sha256": pl._file_hash(closure_path)}], "outputs": []})
    return work


def finish(work, at=COMPLETED):
    value = read_json(work / "work.json")
    value.update(status="completed", user_approved=True, completed_at=at)
    write_json(work / "work.json", value)


def test_finish_and_raw_append_preserve_review_request_time(tmp_path, monkeypatch):
    work = review_package(tmp_path)
    monkeypatch.setattr(pl, "now", lambda: REQUESTED)
    request = pl.request_project_review(tmp_path, work)
    assert request["closure_generation"]["review_prepared_at"] == PREPARED
    assert pl.request_project_review(tmp_path, work) == request
    finish(work)
    result = pl.sync_project_review_request(tmp_path, work)
    assert result["requested_at"] == REQUESTED and result["completed_at"] == COMPLETED
    assert result["signature"] == pl._signature(work, pl._sources(work))
    assert project_repair._priority(tmp_path, "1", result["signature"]) == REQUESTED
    metadata = read_json(work / "work.json")
    before = pl._sources(work)[0]["snapshot_sha256"]
    archive(tmp_path, [message("user", "归档"), message("assistant", "已归档", "final")], previous=before)
    write_json(work / "work.json", metadata)
    monkeypatch.setattr(pl, "now", lambda: "2026-09-16T10:00:00+00:00")
    refreshed = pl.sync_project_review_request(tmp_path, work)
    assert refreshed["signature"] != result["signature"]
    assert refreshed["requested_at"] == REQUESTED
    assert refreshed["closure_generation"] == result["closure_generation"]
    assert pl.sync_project_review_request(tmp_path, work) == refreshed


def test_legacy_capture_request_migrates_only_through_hashed_closure(tmp_path):
    work = review_package(tmp_path, legacy=True); finish(work)
    old = read_json(pl.directory(tmp_path) / "requests/1.json")
    result = pl.sync_project_review_request(tmp_path, work)
    assert result["requested_at"] == old["requested_at"]
    assert result["completed_at"] == COMPLETED
    assert result["signature"] == pl._signature(work, pl._sources(work))
    assert result["legacy_capture_request"] == old
    assert project_repair._priority(tmp_path, "1", result["signature"]) == old["requested_at"]


@pytest.mark.parametrize("change", ["closure_bytes", "request_time", "manifest_time", "completed_before_review"])
def test_unproven_legacy_requests_are_not_promoted(tmp_path, change):
    work = review_package(tmp_path, legacy=True); finish(work)
    request_path = pl.directory(tmp_path) / "requests/1.json"
    if change == "closure_bytes":
        path = work / "workspace/qa/closure-evidence.json"
        path.write_text(path.read_text() + " ")
    elif change == "request_time":
        request = read_json(request_path); request["requested_at"] = "2026-09-15T09:59:00+00:00"; write_json(request_path, request)
    elif change == "manifest_time":
        path = work / "workspace/qa/final-output-manifest.json"
        manifest = read_json(path); manifest["generated_at"] = "2026-09-15T09:59:00+00:00"; write_json(path, manifest)
    else:
        finish(work, "2026-09-15T10:00:00+00:00")
    original = request_path.read_bytes()
    result = pl.sync_project_review_request(tmp_path, work)
    assert result["status"] == "not_eligible"
    assert request_path.read_bytes() == original
    assert project_repair._priority(tmp_path, "1", pl._signature(work, pl._sources(work))) == ""


def test_reopen_reprepare_and_changed_completion_cannot_reuse_priority(tmp_path, monkeypatch):
    work = review_package(tmp_path); monkeypatch.setattr(pl, "now", lambda: REQUESTED)
    pl.request_project_review(tmp_path, work); finish(work)
    accepted = pl.sync_project_review_request(tmp_path, work)
    finish(work, "2026-09-15T10:02:00+00:00")
    assert pl.sync_project_review_request(tmp_path, work)["status"] == "not_eligible"
    assert project_repair._priority(tmp_path, "1", pl._signature(work, pl._sources(work))) == ""
    value = read_json(work / "work.json"); value.update(status="review", user_approved=False); value.pop("completed_at")
    write_json(work / "work.json", value)
    assert pl.sync_project_review_request(tmp_path, work)["status"] == "not_eligible"
    assert project_repair._priority(tmp_path, "1", pl._signature(work, pl._sources(work))) == ""
    # A second prepare is a new manifest generation and a new explicit request.
    later = "2026-09-15T11:00:00+00:00"
    value["review_prepared_at"] = later; write_json(work / "work.json", value)
    manifest_path = work / "workspace/qa/final-output-manifest.json"
    manifest = read_json(manifest_path); manifest["generated_at"] = later; write_json(manifest_path, manifest)
    monkeypatch.setattr(pl, "now", lambda: "2026-09-15T11:00:01+00:00")
    new = pl.request_project_review(tmp_path, work)
    assert new["requested_at"] != accepted["requested_at"]
    assert new["closure_generation"] != accepted["closure_generation"]


def test_manifest_change_and_history_without_request_get_no_priority(tmp_path, monkeypatch):
    work = review_package(tmp_path); finish(work)
    assert pl.sync_project_review_request(tmp_path, work)["status"] == "none"
    assert not (pl.directory(tmp_path) / "requests/1.json").exists()
    work = review_package(tmp_path); monkeypatch.setattr(pl, "now", lambda: REQUESTED)
    pl.request_project_review(tmp_path, work); finish(work); pl.sync_project_review_request(tmp_path, work)
    path = work / "workspace/qa/final-output-manifest.json"
    manifest = read_json(path); manifest["outputs"] = [{"path": "output/new.pptx", "sha256": "changed"}]; write_json(path, manifest)
    assert pl.sync_project_review_request(tmp_path, work)["status"] == "not_eligible"
    assert project_repair._priority(tmp_path, "1", pl._signature(work, pl._sources(work))) == ""


def test_capture_does_not_request_priority_before_final_manifest(tmp_path, monkeypatch):
    from aippt_studio import conversations, feedback_audit
    work, _, _ = archive(tmp_path, [message("user", "归档")])
    monkeypatch.setattr(conversations, "refresh_conversation_tree", lambda work: {"status": "unchanged"})
    monkeypatch.setattr(feedback_audit, "audit_work_feedback", lambda work: {})
    result = maintenance.closure_evidence(work)
    assert result["project_review"]["status"] == "deferred"
    assert not (pl.directory(tmp_path) / "requests/1.json").exists()


def test_local_maintenance_seals_existing_cycle_without_model_calls_or_customer_edits(tmp_path, monkeypatch):
    from aippt_studio import context_intelligence
    work = review_package(tmp_path, legacy=True); finish(work)
    preserved = {path: path.read_bytes() for path in [work / "work.json", work / "workspace/qa/final-output-manifest.json",
                                                    work / "workspace/qa/closure-evidence.json"]}
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda _: {"status": "ready", "errors": []})
    monkeypatch.setattr(pl, "run_project_learning", lambda *_: pytest.fail("Local maintenance must not invoke models"))
    result = maintenance.run_maintenance(tmp_path, include_model_reviews=False)
    assert result["status"] == "ok"
    assert result["works"][0]["project_review_request"]["completed_at"] == COMPLETED
    assert all(path.read_bytes() == value for path, value in preserved.items())
    request = read_json(pl.directory(tmp_path) / "requests/1.json")
    assert project_repair._priority(tmp_path, "1", request["signature"]) == request["requested_at"]


def test_tool_summary_is_included_ahead_of_large_qa_sets_without_acceptance_claim(tmp_path):
    work, _, _ = archive(tmp_path, [message("user", "归档")])
    for index in range(60):
        write_json(work / f"workspace/studio-jobs/{index:02d}/qa/presentation-quality.json",
                   {"status": "passed", "details": "检查" * 3000})
    summary = work / "workspace/qa/tool-execution-summary.json"
    write_json(summary, {"status": "ready", "calls": [{"status": "succeeded", "review_required": True}],
                         "limits": "调用成功并不代表页面验收。"})
    sources = pl._artifact_sources(work)
    assert sources[0]["path"] == str(summary)
    assert sources[0]["sha256"] == pl._file_hash(summary)
    assert sources[0]["verification_passed"] is False
    assert "review_required" in sources[0]["text"]


def test_prepare_dispatch_is_after_both_review_writes_and_finish_syncs_first():
    repo = Path(__file__).resolve().parents[2]
    prepare = (repo / "scripts/prepare-work-close.ps1").read_text()
    finish_script = (repo / "scripts/finish-work.ps1").read_text()
    assert prepare.index("maintenance request-review") > prepare.rindex("Write-WorkLifecycleRecord")
    assert finish_script.index("maintenance sync-review") > finish_script.rindex("Write-WorkLifecycleRecord")
    assert finish_script.index("maintenance sync-review") < finish_script.index("maintenance run")


def test_real_prepare_finish_canonical_fixture_has_current_request(tmp_path):
    from test_work_closure import POWERSHELL, _make_work
    if POWERSHELL is None or os.name == "nt":
        pytest.skip("Isolated Unix PowerShell integration requires PowerShell")
    repo = Path(__file__).resolve().parents[2]
    scripts = tmp_path / "scripts"; scripts.mkdir()
    for name in ("prepare-work-close.ps1", "finish-work.ps1", "studio-runtime.ps1", "work-lifecycle-lock.ps1"):
        shutil.copy2(repo / "scripts" / name, scripts / name)
    # Prevent this isolated closure check from waking a real machine worker.
    binaries = tmp_path / "bin"; binaries.mkdir()
    launchctl = binaries / "launchctl"; launchctl.write_text("#!/bin/sh\nexit 1\n"); launchctl.chmod(0o755)
    env = {**os.environ, "AIPPT_PYTHON": sys.executable,
           "PYTHONPATH": str(repo / "aippt-studio/src"), "PYTHONDONTWRITEBYTECODE": "1",
           "PATH": str(binaries) + os.pathsep + os.environ.get("PATH", "")}
    work = _make_work(tmp_path)
    def invoke(name, *arguments):
        return subprocess.run([POWERSHELL, "-NoProfile", "-File", str(scripts / name), "-WorkId", "1", *arguments],
                              env=env, cwd=tmp_path, text=True, capture_output=True, timeout=60)
    prepared = invoke("prepare-work-close.ps1")
    assert prepared.returncode == 0, prepared.stderr + prepared.stdout
    request_path = pl.directory(tmp_path) / "requests/1.json"
    original = read_json(request_path)
    assert original["signature"] == pl._signature(work, pl._sources(work))
    assert original["closure_generation"] == pl._closure_generation(work)[0]
    sealed = {path: path.read_bytes() for path in [work / "workspace/qa/final-output-manifest.json", work / "workspace/qa/closure-evidence.json"]}
    (work / "workspace/knowledge-candidate.md").write_text("本次无新的通用知识；现有规则已经覆盖本测试。")
    completed = invoke("finish-work.ps1", "-UserApproved", "-ApprovalNote", "明确同意结束此技术测试", "-KnowledgeStatus", "no-new-knowledge")
    assert completed.returncode == 0, completed.stderr + completed.stdout
    current = read_json(request_path)
    assert current["requested_at"] == original["requested_at"]
    assert current["completed_at"] == read_json(work / "work.json")["completed_at"]
    assert current["signature"] == pl._signature(work, pl._sources(work))
    assert project_repair._priority(tmp_path, "1", current["signature"]) == original["requested_at"]
    assert all(path.read_bytes() == data for path, data in sealed.items())
