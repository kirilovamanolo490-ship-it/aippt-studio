import copy
import json

import pytest

from aippt_studio import expression_review as er
from aippt_studio.lesson_application import workspace_for_job, validate_lesson_dispositions
from aippt_studio.learning import read_json, write_json
from test_expression_review import candidate, request_for


def lesson():
    return {"lesson_id": "work-28:curved-image", "title": "图片和底形作为整体构图",
            "applies_when": "需要曲边图片表达关系的页面", "exceptions": "必须完整展示的证据图可使用直边",
            "action": "在图片本体处理轮廓并核对主体", "verify_on_page": "查看边角和可见主体",
            "outcome_level": "assistant_reported", "source_work_id": "28", "receipt_sha256": "source-v1"}


def test_foreign_jobs_do_not_import_user_history(tmp_path):
    assert workspace_for_job(tmp_path / "foreign/jobs/test") == (None, None)
    work = tmp_path / "work/3"
    write_json(work / "work.json", {"status": "review"})
    assert workspace_for_job(work / "workspace/studio-jobs/test") == (tmp_path, "3")


def test_foreign_job_policy_does_not_load_global_learned_rules(tmp_path, monkeypatch):
    from aippt_studio.authoring_policy import initialize_authoring_policy, job_authoring_defaults
    from aippt_studio import evolution
    def forbidden(*args):
        raise AssertionError("Foreign fixture must not read the current user's global learned history")
    monkeypatch.setattr(evolution, "load_guidance", forbidden)
    job = tmp_path / "foreign/jobs/test"
    initialize_authoring_policy(job)
    assert job_authoring_defaults(job)["rules"]
    assert not any(row["id"].startswith("learned:") for row in read_json(job / "planning/authoring-policy.json")["rules"])


def test_recalled_lesson_requires_actual_page_application_and_remains_hash_bound(candidate, monkeypatch):
    job, root = candidate
    guidance = {"status": "ready", "lessons": [lesson()], "errors": []}
    monkeypatch.setattr("aippt_studio.lesson_application.project_lesson_context", lambda *a, **k: copy.deepcopy(guidance))
    er.prepare_expression_review(job.id, "final")
    request = request_for(root)
    with pytest.raises(ValueError, match="every retrieved project lesson"):
        er.record_expression_review(job.id, "final", request)
    disposition = {"lesson_id": lesson()["lesson_id"], "decision": "applied", "slide_ids": ["s1"],
                   "reason": "Unit fixture: this page uses the same image/shape relationship.",
                   "verification": "Unit fixture page inspection: the visible image edge matches its backing contour."}
    request["lesson_dispositions"] = [disposition]
    result = er.record_expression_review(job.id, "final", request)
    assert result["lesson_dispositions"] == [disposition]
    assert er.require_expression_review(root, "final") == result
    # A changed authoritative lesson receipt invalidates the earlier judgment.
    guidance["lessons"][0]["receipt_sha256"] = "source-v2"
    with pytest.raises(ValueError, match="stale"):
        er.require_expression_review(root, "final")


def test_project_guidance_can_be_declined_but_unresolved_application_cannot_pass(candidate, monkeypatch):
    job, root = candidate
    monkeypatch.setattr("aippt_studio.lesson_application.project_lesson_context", lambda *a, **k: {
        "status": "ready", "lessons": [lesson()], "errors": []})
    er.prepare_expression_review(job.id, "final")
    request = request_for(root)
    row = {"lesson_id": lesson()["lesson_id"], "decision": "not_applicable", "slide_ids": [],
           "reason": "This fixture's current source has text only; applying a decorative frame would add unsupported content.",
           "verification": "Unit fixture: inspected the page objects and both page previews; no image frame is present."}
    request["lesson_dispositions"] = [row]
    assert er.record_expression_review(job.id, "final", request)["status"] == "ready"
    row.update(decision="revise", slide_ids=["s1"])
    with pytest.raises(ValueError, match="unresolved project lesson"):
        er.record_expression_review(job.id, "final", request)
    request["slides"][0]["decision"] = "revise"
    assert er.record_expression_review(job.id, "final", request)["status"] == "needs_revision"


def test_cannot_claim_adoption_without_real_review_page():
    packet = {"project_lessons": {"lessons": [lesson()]}, "slides": [{"slide_id": "s1"}]}
    request = {"slides": [{"slide_id": "s1", "decision": "ready"}], "lesson_dispositions": [{
        "lesson_id": lesson()["lesson_id"], "decision": "applied", "slide_ids": ["imagined-page"],
        "reason": "Relevant image geometry", "verification": "Image boundary inspected"}]}
    with pytest.raises(ValueError, match="actual pages"):
        validate_lesson_dispositions(packet, request)


def test_local_archive_refresh_never_starts_models(tmp_path, monkeypatch):
    from aippt_studio import maintenance, context_intelligence, evolution, core_evolution, project_learning
    def unexpected(*args, **kwargs):
        raise AssertionError("Archival command must dispatch model work, not block on it")
    monkeypatch.setattr(evolution, "run_evolution", unexpected)
    monkeypatch.setattr(core_evolution, "run_core_evolution", unexpected)
    monkeypatch.setattr(project_learning, "run_project_learning", unexpected)
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda repo: {"status": "ready", "errors": []})
    result = maintenance.run_maintenance(tmp_path, include_model_reviews=False)
    assert result["status"] == "ok"
    assert all(result[key]["status"] == "queued" for key in ("semantic_learning", "project_review", "core_evolution"))


def test_project_review_runs_when_other_model_budgets_are_spent(tmp_path, monkeypatch):
    from aippt_studio import maintenance, context_intelligence, evolution, core_evolution, project_learning
    calls = []
    monkeypatch.setattr(evolution, "run_evolution", lambda repo: {"status": "pending", "reason": "daily budget reached"})
    monkeypatch.setattr(core_evolution, "run_core_evolution", lambda repo: {"status": "pending", "reason": "daily budget reached"})
    monkeypatch.setattr(project_learning, "run_project_learning", lambda repo: calls.append(repo) or {"status": "reviewed"})
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda repo: {"status": "ready", "errors": []})
    result = maintenance.run_maintenance(tmp_path)
    assert calls == [tmp_path]
    assert result["project_review"]["status"] == "reviewed"


def test_slow_model_review_does_not_hold_the_archive_writer_lock(tmp_path, monkeypatch):
    from aippt_studio import maintenance, context_intelligence, evolution, core_evolution, project_learning
    observed = []
    def reviewer(repo):
        observed.append(maintenance.run_maintenance(repo, include_model_reviews=False))
        return {"status": "progress"}
    monkeypatch.setattr(project_learning, "run_project_learning", reviewer)
    monkeypatch.setattr(evolution, "run_evolution", lambda repo: {"status": "unchanged"})
    monkeypatch.setattr(core_evolution, "run_core_evolution", lambda repo: {"status": "unchanged"})
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda repo: {"status": "ready", "errors": []})
    assert maintenance.run_maintenance(tmp_path)["status"] == "ok"
    assert observed[0]["status"] == "ok"


def test_busy_local_archive_writer_leaves_a_retry_request(tmp_path):
    from filelock import FileLock
    from aippt_studio.maintenance import run_maintenance
    runtime = tmp_path / "任务知识库/runtime"
    runtime.mkdir(parents=True)
    with FileLock(str(runtime / "maintenance.lock")):
        result = run_maintenance(tmp_path, include_model_reviews=False)
    assert result["status"] == "already_running"
    assert read_json(runtime / "local-refresh-request.json")["reason"]


def test_local_refresh_cannot_clear_unretried_model_errors(tmp_path, monkeypatch):
    from aippt_studio import maintenance, context_intelligence
    runtime = tmp_path / "任务知识库/runtime"
    error = {"stage": "project_review", "error": "Model connection failed; retry pending"}
    write_json(runtime / "attention.json", {"at": "before", "status": "partial", "errors": [error]})
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda repo: {"status": "ready", "errors": []})
    for _ in range(2):
        assert maintenance.run_maintenance(tmp_path, include_model_reviews=False)["status"] == "ok"
        assert read_json(runtime / "attention.json")["errors"] == [error]


def test_custom_work_root_archive_does_not_queue_same_number_canonical_work(tmp_path, monkeypatch):
    from aippt_studio import maintenance, conversations
    from test_maintenance import work_fixture
    canonical = work_fixture(tmp_path)
    custom = tmp_path / "custom-work/1"
    (custom / "workspace/qa").mkdir(parents=True)
    write_json(custom / "work.json", {"status": "review", "name": "Different project"})
    monkeypatch.setattr(conversations, "refresh_conversation_tree", lambda work: {"status": "unbound", "results": []})
    result = maintenance.closure_evidence(custom)
    assert result["project_review"]["status"] == "skipped"
    assert not (tmp_path / "任务知识库/project_reviews/requests/1.json").exists()
    assert read_json(canonical / "work.json")["name"] == "局部修订"


def test_visual_diff_cli_reaches_real_pixelmatch(tmp_path):
    from PIL import Image
    from typer.testing import CliRunner
    from aippt_studio.cli import app
    from test_visual_diff import make_request
    after = Image.new("RGBA", (8, 6), "white")
    after.putpixel((7, 5), (0, 0, 0, 255))
    request = make_request(tmp_path, after=after, allowed_regions=[{"x": 0, "y": 0, "width": 2, "height": 2}])
    path = tmp_path / "request.json"
    write_json(path, request)
    result = CliRunner().invoke(app, ["visual-diff", "--request", str(path), "--output", str(tmp_path / "qa"), "--json"])
    assert result.exit_code == 0, result.output
    receipt = json.loads(result.output)
    assert receipt["status"] == "unexpected_changes"
    assert receipt["summary"]["outside_allowed_changed_pixels"] == 1
    assert receipt["algorithm"]["name"] == "pixelmatch"


def test_local_revision_context_discovers_the_real_visual_comparison_command(tmp_path, monkeypatch):
    from aippt_studio import context_intelligence
    monkeypatch.setattr(context_intelligence, "recommend_upstream_tools", lambda query: [])
    result = context_intelligence.recall_context("局部微调，前两页不要动，检查前后对比", tmp_path)
    tool = next(row for row in result["tool_recommendations"] if row["provider"] == "pixelmatch")
    assert tool["invocation"].startswith("visual-diff --request")
    assert tool["executed"] is False
    assert tool["operation"] == "compare"
