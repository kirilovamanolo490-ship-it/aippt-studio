import hashlib
import json
from pathlib import Path

import pytest

from test_work_closure import POWERSHELL, _make_work, _run_script


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_call(work):
    job = work / "workspace/studio-jobs/job"
    call = job / "build/upstream-calls/native-fixture"
    call.mkdir(parents=True)
    request = {"slides": [], "label": "原始调用"}
    (call / "request.json").write_text(json.dumps(request), encoding="utf-8")
    (call / "candidate.pptx").write_bytes(b"candidate")
    receipt = {"schema_version": 1, "call_id": "fixture", "provider": "PptxGenJS", "operation": "author",
        "status": "succeeded", "source_commit": "a" * 40,
        "request_sha256": hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest(),
        "artifacts": [{"path": "candidate.pptx", "sha256": digest(call / "candidate.pptx"), "bytes": 9}]}
    (call / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    pointer = job / "build/native-prototype-call.json"
    pointer.write_text(json.dumps({"receipt": "build/upstream-calls/native-fixture/receipt.json",
        "sha256": digest(call / "receipt.json"), "engine": "PptxGenJS"}), encoding="utf-8")
    return call, pointer


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_normal_closure_preserves_native_call_and_its_request_and_artifact(tmp_path):
    work = _make_work(tmp_path)
    call, pointer = fixture_call(work)
    result = _run_script("prepare-work-close.ps1", tmp_path, "-WorkId", "1")
    assert result.returncode == 0, result.stderr
    manifest = json.loads((work / "workspace/qa/final-output-manifest.json").read_text(encoding="utf-8-sig"))
    files = {row["path"] for row in manifest["adapter_evidence"]}
    for path in [pointer, call / "receipt.json", call / "request.json", call / "candidate.pptx"]:
        assert path.relative_to(work).as_posix() in files
    summary = json.loads((work / "workspace/qa/tool-execution-summary.json").read_text())
    assert summary["counts"]["verified_successful_calls"] == 1
    assert summary["calls"][0]["selected_by_native_pointer"] is True
    assert summary["calls"][0]["final_adoption"] == "not_established"


def test_changed_request_and_artifact_are_reported_without_claiming_execution(tmp_path):
    from aippt_studio.tool_evidence import collect_tool_evidence
    work = _make_work(tmp_path)
    call, _ = fixture_call(work)
    (call / "candidate.pptx").write_bytes(b"modified")
    (call / "request.json").write_text("{}")
    result = collect_tool_evidence(work)
    assert result["counts"]["verified_successful_calls"] == 0
    assert result["calls"][0]["evidence_status"] == "mismatch"
    assert {row["kind"] for row in result["calls"][0]["issues"]} >= {"artifact_hash_mismatch", "request_hash_mismatch"}


def test_failed_call_is_retained_and_not_counted_as_success(tmp_path):
    from aippt_studio.tool_evidence import collect_tool_evidence
    work = _make_work(tmp_path)
    call, _ = fixture_call(work)
    receipt = json.loads((call / "receipt.json").read_text())
    receipt.update(status="failed", error="native runtime failed")
    (call / "receipt.json").write_text(json.dumps(receipt))
    result = collect_tool_evidence(work)
    assert result["counts"]["verified_successful_calls"] == 0
    assert result["counts"]["failed_calls"] == 1
    assert result["calls"][0]["selected_by_native_pointer"] is False


def test_receipt_execution_class_is_preserved_separately_from_adoption(tmp_path):
    from aippt_studio.tool_evidence import collect_tool_evidence

    work = _make_work(tmp_path)
    call, _ = fixture_call(work)
    receipt_path = call / "receipt.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["execution"] = {
        "catalog_state": "operation_registered_not_executed",
        "execution_kind": "managed_package_runtime",
        "executed": True,
        "proof": "fixture receipt",
    }
    receipt_path.write_text(json.dumps(receipt))

    result = collect_tool_evidence(work)

    assert result["calls"][0]["execution"]["execution_kind"] == "managed_package_runtime"
    assert result["calls"][0]["final_adoption"] == "not_established"
    assert result["execution_kinds"] == {"managed_package_runtime": 1}


def test_receipt_paths_cannot_collect_outside_work_even_through_symlinks(tmp_path):
    from aippt_studio.tool_evidence import collect_tool_evidence
    work = _make_work(tmp_path)
    call, _ = fixture_call(work)
    outside = tmp_path / "outside.pptx"
    outside.write_bytes(b"outside")
    (call / "linked.pptx").symlink_to(outside)
    receipt = json.loads((call / "receipt.json").read_text())
    receipt["artifacts"].extend({"path": value, "sha256": digest(outside)} for value in [str(outside), "linked.pptx", "../../../../../../outside.pptx"])
    (call / "receipt.json").write_text(json.dumps(receipt))
    result = collect_tool_evidence(work)
    assert all(Path(row["path"]).as_posix().startswith("workspace/") for row in result["records"])
    assert not any(row["sha256"] == digest(outside) for row in result["records"])
    assert result["calls"][0]["evidence_status"] == "mismatch"


@pytest.mark.parametrize("artifacts", [[], None])
def test_success_without_artifact_manifest_is_not_verified(tmp_path, artifacts):
    from aippt_studio.tool_evidence import collect_tool_evidence
    work = _make_work(tmp_path)
    call, _ = fixture_call(work)
    receipt = json.loads((call / "receipt.json").read_text())
    receipt["artifacts"] = artifacts
    receipt["result"] = {"artifact": "candidate.pptx"}
    (call / "receipt.json").write_text(json.dumps(receipt))
    result = collect_tool_evidence(work)
    assert len(result["calls"]) == 1
    assert result["counts"]["verified_successful_calls"] == 0
    assert "principal_artifact_unbound" in {row["kind"] for row in result["calls"][0]["issues"]}
