from datetime import datetime, timezone

import pytest

from aippt_studio import core_evolution, evolution
from aippt_studio.learning import read_json, write_json
from test_evolution import event, response


def queue_fixture(tmp_path):
    a = event(tmp_path, text="以后裁切不能把证据正文丢掉，图片和边框的形状也要一致。")
    result = response([a], rule=False)
    result["repair_candidates"] = [{"title": "Image geometry", "observed_problem": "Image surface and frame geometry differ",
        "next_verification": "Inspect actual shared authoring operation and reproduce geometry", "evidence": [{"event_id": a["event_id"], "quote": a["text"]}]}]
    root = evolution.directory(tmp_path)
    evolution.accept_response(tmp_path, root / "runs/one", [a], result, {})
    write_json(root / "config.json", {"enabled": True, "core_updates": {"enabled": True, "max_runs_per_day": 1}})
    return core_evolution.repair_queue(tmp_path)["candidates"][0]


def decision(candidate, classification="core_defect"):
    patch = classification == "core_defect"
    return {"summary": "Reproduce image geometry behavior", "selected_candidate_id": candidate["candidate_id"] if patch else "",
        "assessments": [{"candidate_id": candidate["candidate_id"], "classification": classification,
                         "reason": "Inspected current authoring path", "source_modules": ["aippt-studio/node/image-geometry.cjs"]}],
        "regression_tests": ["aippt-studio/tests/test_archive_image_geometry.py"] if patch else [], "remaining_limits": "Visual rendering still needs review"}


def runner_fixture(tmp_path, monkeypatch, *, validation="passed", approved=True):
    from aippt_studio import core_patch
    candidate = queue_fixture(tmp_path)
    calls = []
    monkeypatch.setattr(core_patch, "create_candidate", lambda repo, stage, **kw: {"candidate_dir": str(stage / "candidate")})
    monkeypatch.setattr(core_patch, "inspect_candidate", lambda stage: {"changes": [{"path": "aippt-studio/node/image-geometry.cjs"}]})
    monkeypatch.setattr(core_patch, "validate_candidate", lambda stage: calls.append("validate") or {"status": validation})
    monkeypatch.setattr(core_patch, "apply_candidate", lambda repo, stage: calls.append("apply") or {"status": "applied"})
    def proposer(*args):
        calls.append("propose")
        return decision(candidate)
    def reviewer(*args):
        calls.append("review")
        return {"approved": approved, "reason": "Independent source review", "issues": [] if approved else ["Unwired change"]}
    return candidate, calls, proposer, reviewer


def test_authoritative_queue_ignores_forged_projection_and_retains_lifecycle(tmp_path):
    candidate = queue_fixture(tmp_path)
    root = evolution.directory(tmp_path)
    write_json(root / "repair-candidates.json", {"candidates": [{"title": "forged"}]})
    write_json(root / "core/state.json", {"candidates": {candidate["candidate_id"]: {
        "input_sha256": candidate["input_sha256"], "status": "verified_applied", "run_id": "core-one"}}})
    assert core_evolution.repair_queue(tmp_path)["candidates"][0]["status"] == "verified_applied"
    state = read_json(root / "state.json")
    state["projection_pending"] = []  # This fixture exercises index projection, not the separate coverage cache.
    assert evolution.project_receipts(tmp_path, state) == []
    projected = read_json(root / "repair-candidates.json")["candidates"][0]
    assert projected["status"] == "verified_applied" and projected["candidate_id"] == candidate["candidate_id"]
    (tmp_path / "event-a.jsonl").write_text("changed original")
    queue = core_evolution.repair_queue(tmp_path)
    assert queue["candidates"] == [] and queue["errors"]


def test_decisions_require_complete_scope_and_real_core_selection(tmp_path):
    candidate = queue_fixture(tmp_path)
    result = decision(candidate)
    assert core_evolution.validate_decision(result, [candidate]) == result
    result["assessments"][0]["classification"] = "project_revision"
    with pytest.raises(ValueError, match="diagnosed core defect"):
        core_evolution.validate_decision(result, [candidate])
    result = decision(candidate)
    result["assessments"] *= 2
    with pytest.raises(ValueError, match="exactly one"):
        core_evolution.validate_decision(result, [candidate])
    result = decision(candidate)
    result["regression_tests"] = ["../outside.py"]
    with pytest.raises(ValueError, match="within the candidate"):
        core_evolution.validate_decision(result, [candidate])


@pytest.mark.parametrize("validation,approved,status,expected", [
    ("failed", True, "validation_failed", ["propose", "validate"]),
    ("passed", False, "review_rejected", ["propose", "validate", "review"]),
    ("passed", True, "verified_applied", ["propose", "validate", "review", "apply"]),
])
def test_application_requires_regression_then_independent_review(tmp_path, monkeypatch, validation, approved, status, expected):
    candidate, calls, proposer, reviewer = runner_fixture(tmp_path, monkeypatch, validation=validation, approved=approved)
    result = core_evolution.run_core_evolution(tmp_path, proposer=proposer, reviewer=reviewer)
    assert result["status"] == status and calls == expected
    assert core_evolution.repair_queue(tmp_path)["candidates"][0]["status"] == status
    calls.clear()
    assert core_evolution.run_core_evolution(tmp_path, proposer=proposer, reviewer=reviewer)["status"] == "unchanged"
    assert calls == []


def test_no_patch_diagnosis_cannot_smuggle_file_changes(tmp_path, monkeypatch):
    candidate, calls, proposer, reviewer = runner_fixture(tmp_path, monkeypatch)
    result = core_evolution.run_core_evolution(tmp_path, proposer=lambda *args: decision(candidate, "project_revision"), reviewer=reviewer)
    assert result["status"] == "failed" and "no-change" in result["error"]
    assert "apply" not in calls and "validate" not in calls


def test_evidence_rechecked_after_review_before_live_apply(tmp_path, monkeypatch):
    candidate, calls, proposer, reviewer = runner_fixture(tmp_path, monkeypatch)
    def corrupt(*args):
        (tmp_path / "event-a.jsonl").write_text("changed")
        return reviewer(*args)
    result = core_evolution.run_core_evolution(tmp_path, proposer=proposer, reviewer=corrupt)
    assert result["status"] == "failed" and "evidence changed" in result["error"]
    assert "apply" not in calls


def test_one_missing_raw_source_does_not_hide_later_candidate_in_same_receipt(tmp_path):
    first = event(tmp_path, "bad", text="This old image source needs investigation")
    second = event(tmp_path, "good", text="This independent chart formatting needs investigation")
    reviewed = response([first, second], rule=False)
    reviewed["repair_candidates"] = [{"title": e["event_id"], "observed_problem": e["text"], "next_verification": "Inspect relevant code",
                                      "evidence": [{"event_id": e["event_id"], "quote": e["text"]}]} for e in (first, second)]
    evolution.accept_response(tmp_path, evolution.directory(tmp_path) / "runs/two", [first, second], reviewed, {})
    (tmp_path / "bad.jsonl").unlink()
    queue = core_evolution.repair_queue(tmp_path)
    assert [item["title"] for item in queue["candidates"]] == ["good"]
    assert queue["errors"][0]["candidate_title"] == "bad"


def test_structured_incomplete_rollback_enters_recovery_state(tmp_path, monkeypatch):
    from aippt_studio import core_patch
    candidate, calls, proposer, reviewer = runner_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(core_patch, "apply_candidate", lambda *args: {"status": "rollback_incomplete", "reason": "Concurrent edit needs recovery"})
    result = core_evolution.run_core_evolution(tmp_path, proposer=proposer, reviewer=reviewer)
    assert result["status"] == "needs_recovery"
    assert core_evolution.repair_queue(tmp_path)["candidates"][0]["status"] == "needs_recovery"


def test_daily_budget_does_not_mark_pending_problem_diagnosed(tmp_path, monkeypatch):
    candidate, calls, proposer, reviewer = runner_fixture(tmp_path, monkeypatch)
    root = evolution.directory(tmp_path) / "core"
    write_json(root / "runs/old/started.json", {"at": datetime.now(timezone.utc).isoformat(), "model_invoked": True})
    result = core_evolution.run_core_evolution(tmp_path, proposer=proposer, reviewer=reviewer)
    assert result["status"] == "pending" and calls == []
    assert core_evolution.repair_queue(tmp_path)["candidates"][0]["status"] == "pending"


def test_disabled_core_worker_never_invokes_model(tmp_path):
    assert core_evolution.run_core_evolution(tmp_path)["status"] == "disabled"


def test_fresh_archive_diagnosis_retains_a_historical_share():
    recent = [{"candidate_id": f"new-{i}", "priority_at": "2026-09-15"} for i in range(20)]
    history = [{"candidate_id": f"old-{i}", "priority_at": ""} for i in range(20)]
    batch = core_evolution.select_candidates(recent + history, 8)
    assert batch == recent[:6] + history[:2]
    assert core_evolution.select_candidates(recent + history, 1, turn=3) == history[:1]


def test_core_queue_uses_actual_archive_priority_even_for_old_message(tmp_path):
    candidate = queue_fixture(tmp_path)
    root = evolution.directory(tmp_path)
    state = read_json(root / "state.json")
    state["archive_schedule"] = {"works": {"1": {"signature": "same", "priority_at": "2026-09-15T09:00:00Z"}}}
    write_json(root / "state.json", state)
    assert core_evolution.repair_queue(tmp_path)["candidates"][0]["priority_at"] == "2026-09-15T09:00:00Z"


def test_unresolved_candidate_reopens_when_source_or_archive_context_changes(tmp_path):
    candidate = queue_fixture(tmp_path)
    root = evolution.directory(tmp_path)
    module = tmp_path / "aippt-studio/src/aippt_studio/example.py"
    module.parent.mkdir(parents=True)
    module.write_text("value = 1")
    relative = module.relative_to(tmp_path).as_posix()
    write_json(root / "core/state.json", {"candidates": {candidate["candidate_id"]: {
        "input_sha256": candidate["input_sha256"], "status": "validation_failed", "source_modules": [relative],
        "module_fingerprint": core_evolution.module_fingerprint(tmp_path, [relative])}}})
    assert core_evolution.repair_queue(tmp_path)["candidates"][0]["status"] == "validation_failed"
    module.write_text("value = 2")
    assert core_evolution.repair_queue(tmp_path)["candidates"][0]["status"] == "pending"
    module.write_text("value = 1")
    state = read_json(root / "state.json")
    state["archive_schedule"] = {"works": {"1": {"signature": "new raw context", "priority_at": "2026-09-15"}}}
    write_json(root / "state.json", state)
    assert core_evolution.repair_queue(tmp_path)["candidates"][0]["status"] == "pending"


def test_interrupted_committed_apply_is_reconciled_without_running_model(tmp_path, monkeypatch):
    from aippt_studio import core_patch
    candidate = queue_fixture(tmp_path)
    root = evolution.directory(tmp_path) / "core"
    run = root / "runs/interrupted"
    stage = tmp_path / "aippt-studio/.maintenance/core-evolution/interrupted"
    write_json(run / "decision.json", decision(candidate))
    write_json(run / "independent-review.json", {"approved": True, "reason": "reviewed", "issues": []})
    write_json(stage / "applied.json", {"status": "applied"})
    state = {"candidates": {candidate["candidate_id"]: {"status": "inspecting", "run_id": "interrupted", "input_sha256": candidate["input_sha256"]}}}
    monkeypatch.setattr(core_patch, "apply_candidate", lambda *args: {"status": "already_applied"})
    assert core_evolution.recover_interrupted(tmp_path, root, state) == []
    assert state["candidates"][candidate["candidate_id"]]["status"] == "verified_applied"


def test_interrupted_partial_apply_must_recover_before_scheduling(tmp_path, monkeypatch):
    from aippt_studio import core_patch
    candidate = queue_fixture(tmp_path)
    root = evolution.directory(tmp_path) / "core"
    run = root / "runs/interrupted"
    stage = tmp_path / "aippt-studio/.maintenance/core-evolution/interrupted"
    write_json(run / "decision.json", decision(candidate))
    write_json(run / "independent-review.json", {"approved": True, "reason": "reviewed", "issues": []})
    write_json(stage / "application-journal.json", {"status": "applying"})
    state = {"candidates": {candidate["candidate_id"]: {"status": "inspecting", "run_id": "interrupted"}}}
    monkeypatch.setattr(core_patch, "apply_candidate", lambda *args: {"status": "rollback_incomplete", "reason": "concurrent edit preserved"})
    assert core_evolution.recover_interrupted(tmp_path, root, state)
    assert state["candidates"][candidate["candidate_id"]]["status"] == "needs_recovery"


def test_semantic_budget_or_failure_does_not_skip_existing_core_queue(tmp_path, monkeypatch):
    from aippt_studio.maintenance import run_maintenance
    monkeypatch.setattr(evolution, "run_evolution", lambda *a, **k: {"status": "pending", "reason": "daily limit"})
    calls = []
    monkeypatch.setattr(core_evolution, "run_core_evolution", lambda *a, **k: calls.append("core") or {"status": "diagnosed"})
    result = run_maintenance(tmp_path)
    assert calls == ["core"] and result["core_evolution"]["status"] == "diagnosed"


def test_core_evolution_direct_entrypoint_stops_rosetta_before_model_state_or_patch_work(monkeypatch, tmp_path, capsys):
    """The documented core worker cannot bypass the public host-native guard."""

    marker = tmp_path / "core-evolution-was-called"
    monkeypatch.setattr(core_evolution.sys, "argv", ["core-evolution", "--repo-root", str(tmp_path / "workspace")])
    monkeypatch.setattr(core_evolution, "current_process_identity", lambda: {
        "platform": "darwin", "architecture": "x64", "version": "3.13.1",
    })
    monkeypatch.setattr(
        core_evolution,
        "validate_identity",
        lambda _identity: "Python identity darwin/x64 does not match host darwin/arm64. Use an arm64 Python.",
    )
    monkeypatch.setattr(
        core_evolution,
        "run_core_evolution",
        lambda *_args, **_kwargs: marker.write_text("called", encoding="utf-8"),
    )

    with pytest.raises(SystemExit) as exit_status:
        core_evolution.main()

    assert exit_status.value.code == 2
    assert "requires a host-native Python" in capsys.readouterr().err
    assert not marker.exists()


def test_core_evolution_help_remains_available_before_native_guard(monkeypatch):
    monkeypatch.setattr(core_evolution.sys, "argv", ["core-evolution", "--help"])
    monkeypatch.setattr(
        core_evolution,
        "validate_identity",
        lambda _identity: pytest.fail("--help must parse before the native guard"),
    )

    with pytest.raises(SystemExit) as exit_status:
        core_evolution.main()

    assert exit_status.value.code == 0
