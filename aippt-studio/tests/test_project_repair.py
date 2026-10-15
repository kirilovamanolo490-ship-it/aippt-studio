"""Whole-project findings enter diagnosis with source authority, not bug labels."""
import copy
import json
from pathlib import Path

import pytest

from aippt_studio import core_evolution as ce, project_learning as pl, project_repair as pr
from aippt_studio.learning import read_json, write_json
from aippt_studio.repair_context import build_repair_context
from test_project_learning import archive, configure, drive, message, review_for


def complete(repo, work_id="1", *, tool_attempt=False):
    records = [message("user", "图片边缘需要保留人物，请比较原图和底板。")]
    if tool_attempt:
        records.extend(message("assistant", f"过程记录 {i}", "commentary") for i in range(40))
        records.append({"type": "response_item", "payload": {"type": "function_call_output",
                        "call_id": "actual-shape-check", "output": "Error: geometry dimensions were inconsistent"}})
    archive(repo, records, identifier=work_id)
    configure(repo)
    if not tool_attempt:
        drive(repo, work_ids=[work_id])
    else:
        def reviewer(_repo, _run, packet, _config):
            value = review_for(packet)
            if packet["phase"] == "segment":
                tool = next(s for s in packet["records"] if s["role"] == "tool_failure_candidate")
                value["episodes"][0]["attempts"].append({"action": "检查实际几何", "result": "工具返回尺寸不一致",
                    "why_rejected": "仍需检查当前核心实现，不能以工具报错推出通用故障。",
                    "evidence": [pl._fragment_reference(packet, tool["source_id"], tool["text"])]})
            return value
        for _ in range(4):
            result = pl.run_project_learning(repo, reviewer=reviewer, work_ids=[work_id])
            assert result["status"] in {"progress", "review_complete"}, result
            if result["status"] == "review_complete": break
        else:
            pytest.fail("Fixture synthesis did not complete")
    return ce.repair_queue(repo)["candidates"]


def change_synthesis(repo, mutation, work_id="1"):
    root = pl.directory(repo); state = read_json(root / "state.json")
    reference = state["works"][work_id]["synthesis"]
    path = root / reference["path"]; receipt = read_json(path)
    mutation(receipt)
    write_json(path, receipt)
    reference["sha256"] = pl._hash(receipt)
    write_json(root / "state.json", state)


def test_complete_project_enters_normal_core_queue_without_user_fault_claim(tmp_path):
    candidates = complete(tmp_path, tool_attempt=True)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["source_kind"] == "project_synthesis"
    assert candidate["status"] == "pending" and candidate["defect_established"] is False
    assert candidate["source_events"] == []
    assert "not a reported software defect" in candidate["diagnostic_basis"]
    # The witnessed attempt is over 40 records after the request, so adjacency
    # zero would drop it on the old feedback-only repair context path.
    context = build_repair_context(candidate, repo=tmp_path, neighbor_records=0)
    assert context["status"] == "ready", context
    assert len(context["episode"]["attempts"]) == 2
    assert {s["role"] for s in context["context_records"]} == {"user", "tool_failure_candidate"}
    assert "dimensions were inconsistent" in json.dumps(context)
    assert context["coverage"]["final_synthesis"] is True
    assert context["segment_receipts_verified"] == 1
    assert context["source_events"] == []
    assert read_json(tmp_path / "work/1/work.json")["status"] == "completed"


def test_incomplete_or_old_uncovered_synthesis_is_not_diagnosed(tmp_path):
    complete(tmp_path)
    root = pl.directory(tmp_path); original = read_json(root / "state.json")
    pending = copy.deepcopy(original); pending["works"]["1"]["status"] = "synthesis_pending"
    write_json(root / "state.json", pending)
    assert not ce.repair_queue(tmp_path)["candidates"]
    write_json(root / "state.json", original)
    change_synthesis(tmp_path, lambda receipt: receipt.pop("synthesis_contract"))
    result = ce.repair_queue(tmp_path)
    assert result["errors"] and not result["candidates"]


def test_input_version_reopens_diagnosis_but_never_repeats_verified_application(tmp_path):
    candidate = complete(tmp_path)[0]
    lifecycle = tmp_path / "任务知识库/evolution/core/state.json"
    write_json(lifecycle, {"candidates": {candidate["candidate_id"]: {
        "status": "needs_context", "input_sha256": candidate["input_sha256"]}}})
    assert ce.repair_queue(tmp_path)["candidates"][0]["status"] == "needs_context"
    change_synthesis(tmp_path, lambda r: r["review"]["episodes"][0].update(why_repeated="新增综合比较说明，仍需诊断。"))
    current = ce.repair_queue(tmp_path)["candidates"][0]
    assert current["candidate_id"] == candidate["candidate_id"]
    assert current["input_sha256"] != candidate["input_sha256"] and current["status"] == "pending"
    assert build_repair_context(candidate, repo=tmp_path)["status"] == "invalid"
    write_json(lifecycle, {"candidates": {candidate["candidate_id"]: {
        "status": "verified_applied", "input_sha256": candidate["input_sha256"], "run_id": "actual-receipt-anchor"}}})
    assert ce.repair_queue(tmp_path)["candidates"][0]["status"] == "verified_applied"


@pytest.mark.parametrize("mutation", [
    lambda r: r["review"]["episodes"][0]["attempts"][0]["evidence"][0].update(quote="Fabricated attempt quote"),
    lambda r: r.update(work_id="different-work"),
    lambda r: r["coverage"].update(segments_total=999),
    lambda r: r["synthesis_contract"]["input_episodes"][0].update(source_receipt_sha256="0" * 64),
])
def test_rehashed_but_invalid_review_cannot_supply_core_evidence(tmp_path, mutation):
    complete(tmp_path)
    change_synthesis(tmp_path, mutation)
    result = ce.repair_queue(tmp_path)
    assert not result["candidates"] and result["errors"]


def test_changed_segment_receipt_is_rejected_even_when_final_receipt_is_intact(tmp_path):
    complete(tmp_path)
    root = pl.directory(tmp_path); item = read_json(root / "state.json")["works"]["1"]
    path = root / item["segments"][0]["path"]
    receipt = read_json(path); receipt["review"]["summary"] = "changed unbound summary"
    write_json(path, receipt)
    assert not ce.repair_queue(tmp_path)["candidates"]
    assert ce.repair_queue(tmp_path)["errors"]


def test_corrupt_one_project_does_not_hide_another_or_semantic_candidates(tmp_path):
    from test_core_evolution import queue_fixture
    complete(tmp_path, "1"); complete(tmp_path, "2")
    legacy = queue_fixture(tmp_path)
    root = pl.directory(tmp_path); item = read_json(root / "state.json")["works"]["1"]
    source = next(iter(read_json(root / item["synthesis"]["path"])["sources"].values()))
    path = Path(source["raw_path"])
    path.write_bytes(path.read_bytes().replace("人物".encode(), "动物".encode()))
    result = ce.repair_queue(tmp_path)
    assert {c.get("project_work_id") for c in result["candidates"] if c.get("source_kind") == "project_synthesis"} == {"2"}
    assert any(c["candidate_id"] == legacy["candidate_id"] for c in result["candidates"])
    assert any(error.get("work_id") == "1" for error in result["errors"])


def test_corrupt_semantic_state_does_not_hide_valid_project_review(tmp_path):
    complete(tmp_path)
    path = tmp_path / "任务知识库/evolution/state.json"
    path.parent.mkdir(parents=True, exist_ok=True); path.write_text("broken json")
    result = ce.repair_queue(tmp_path)
    assert len(result["candidates"]) == 1 and result["errors"]


def test_changed_archive_signature_waits_for_new_complete_review(tmp_path):
    complete(tmp_path)
    archive(tmp_path, [message("user", "新归档要求保留其他区域。")])
    result = ce.repair_queue(tmp_path)
    assert not result["candidates"] and result["errors"]


@pytest.mark.parametrize("reason,matching,expected", [
    ("closure", True, True), ("explicit_project_review", True, True),
    ("historical_backfill", True, False), ("closure", False, False),
])
def test_current_explicit_request_controls_priority_not_historical_backfill(tmp_path, monkeypatch, reason, matching, expected):
    when = "2026-09-15T16:00:00+00:00"
    root = pl.directory(tmp_path)
    if reason == "closure":
        # A real closure request is sealed to a prepared manifest and completion
        # cycle. Preserve the positive assertion using that actual contract.
        from test_project_review_request_lifecycle import review_package, finish
        work = review_package(tmp_path)
        monkeypatch.setattr(pl, "now", lambda: when)
        pl.request_project_review(tmp_path, work)
        finish(work, "2026-09-15T16:01:00+00:00")
        pl.sync_project_review_request(tmp_path, work)
        configure(tmp_path); drive(tmp_path)
        request = read_json(root / "requests/1.json")
    else:
        complete(tmp_path)
        request = {"work_id": "1", "reason": reason, "requested_at": when}
    item = read_json(root / "state.json")["works"]["1"]
    write_json(root / "requests/1.json", {**request, "signature": item["signature"] if matching else "old"})
    assert ce.repair_queue(tmp_path)["candidates"][0]["priority_at"] == (when if expected else "")


def test_actual_proposal_path_receives_project_attempts_and_classification_boundary(tmp_path, monkeypatch):
    candidate = complete(tmp_path, tool_attempt=True)[0]
    captured = {}
    def codex(run, cwd, prompt, schema, config, **kwargs):
        captured.update(prompt=prompt, schema=schema)
        return {"fixture": "no real model invoked"}
    monkeypatch.setattr(ce, "_run_codex", codex)
    run = tmp_path / "diagnostic-run"
    result = ce.propose_with_codex(tmp_path, run, {"candidate_dir": str(tmp_path / "isolated")}, [candidate], {})
    assert result["fixture"]
    packet = read_json(run / "source-context.json")[0]
    assert packet["source_kind"] == "project_synthesis"
    assert len(packet["source_context"]["episode"]["attempts"]) == 2
    assert "NOT a claim that the user reported a software fault" in captured["prompt"]
    assert set(captured["schema"]["properties"]["assessments"]["items"]["properties"]["classification"]["enum"]) == {
        "core_defect", "project_revision", "needs_context", "already_covered"}


def test_project_revision_diagnosis_cannot_enter_patch_validation_or_apply(tmp_path, monkeypatch):
    from aippt_studio import core_patch
    candidate = complete(tmp_path)[0]
    write_json(tmp_path / "任务知识库/evolution/config.json", {"enabled": True, "core_updates": {"enabled": True}})
    monkeypatch.setattr(core_patch, "create_candidate", lambda *a, **k: {"candidate_dir": str(tmp_path / "isolated")})
    monkeypatch.setattr(core_patch, "inspect_candidate", lambda *a: {"status": "unchanged", "changes": [], "rejected": []})
    monkeypatch.setattr(core_patch, "validate_candidate", lambda *a: pytest.fail("Project revision is not a core patch"))
    monkeypatch.setattr(core_patch, "apply_candidate", lambda *a: pytest.fail("No patch was selected or validated"))
    def proposer(repo, run, manifest, batch, config):
        assert [c["candidate_id"] for c in batch] == [candidate["candidate_id"]]
        assert build_repair_context(batch[0], repo=repo)["status"] == "ready"
        return {"summary": "Only project-specific composition evidence is established.", "selected_candidate_id": "",
            "assessments": [{"candidate_id": candidate["candidate_id"], "classification": "project_revision",
                "reason": "The source calls for a specific image treatment; no reusable code failure is established.", "source_modules": []}],
            "regression_tests": [], "remaining_limits": "This test invokes no model or native Office."}
    assert ce.run_core_evolution(tmp_path, proposer=proposer)["status"] == "diagnosed"
    assert ce.repair_queue(tmp_path)["candidates"][0]["status"] == "project_revision"


def test_empty_rehashed_input_contract_cannot_erase_actual_segment_episodes(tmp_path):
    complete(tmp_path)
    def drop(receipt):
        receipt["synthesis_contract"]["input_episodes"] = []
        receipt["synthesis_contract"]["input_episodes_sha256"] = pl._hash([])
        receipt["review"]["episodes"] = []
        receipt["review"]["episode_dispositions"] = []
    change_synthesis(tmp_path, drop)
    result = ce.repair_queue(tmp_path)
    assert not result["candidates"] and result["errors"]


@pytest.mark.parametrize("empty_prior", [False, True])
def test_partial_synthesis_chain_is_rechecked_not_only_the_final_receipt(tmp_path, empty_prior):
    archive(tmp_path, [message("user", f"第{i}次图片边缘仍有白边。") for i in range(4)])
    configure(tmp_path, records_per_chunk=1)
    results = []
    def reviewer(repo, run, packet, config):
        value = review_for(packet)
        if packet["phase"] == "segment": value["summary"] = "此段仍然需要复核。" * 4000
        elif empty_prior and not packet["coverage"]["final_synthesis"]:
            value["episodes"] = []; value["conditional_lessons"] = []
            for disposition in value["episode_dispositions"]:
                disposition.update(disposition="excluded", target_episode_ids=[], reason="Fixture: these early inputs belong to another project in the shared conversation.")
        return value
    for _ in range(10):
        result = pl.run_project_learning(tmp_path, reviewer=reviewer); results.append(result)
        assert result["status"] in {"progress", "review_complete"}, result
        if result["status"] == "review_complete": break
    syntheses = [r for r in results if r["phase"] == "synthesis"]
    assert len(syntheses) == 2
    result = ce.repair_queue(tmp_path)
    expected = 4 - syntheses[0]["coverage"]["segments_included"] if empty_prior else 4
    assert len(result["candidates"]) == expected and not result["errors"]
    path = Path(syntheses[0]["receipt"]); value = read_json(path)
    value["review"]["summary"] += " altered partial synthesis"
    write_json(path, value)
    result = ce.repair_queue(tmp_path)
    assert not result["candidates"] and result["errors"]


@pytest.mark.parametrize("invalid_sources", [None, [], "not a source map"])
def test_malformed_project_source_map_is_isolated_from_other_project_candidates(tmp_path, invalid_sources):
    complete(tmp_path, "1"); complete(tmp_path, "2")
    change_synthesis(tmp_path, lambda receipt: receipt.update(sources=invalid_sources), work_id="1")
    result = ce.repair_queue(tmp_path)
    assert [c["project_work_id"] for c in result["candidates"]] == ["2"]
    assert result["errors"][0]["work_id"] == "1"
