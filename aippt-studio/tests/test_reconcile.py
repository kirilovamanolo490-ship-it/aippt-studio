from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from aippt_studio.cli import app
from aippt_studio.models import JobRecord, JobState
from aippt_studio.reconcile import ReconcileReport, audit_job
from aippt_studio.renderer import sha256
from aippt_studio.storage import initialize_job_directories, job_dir, write_json


def _create_job(job_id: str, state: JobState = JobState.INTAKE) -> tuple[JobRecord, Path]:
    root = job_dir(job_id)
    initialize_job_directories(root)
    job = JobRecord(id=job_id, name="consistency test", state=state)
    write_json(root / "job.json", job)
    return job, root


def _write_manifest(root: Path, job: JobRecord, status: str, outputs: dict[str, dict[str, object]]) -> None:
    write_json(
        root / "deliverables" / "build-manifest.json",
        {
            "job_id": job.id,
            "status": status,
            "route": job.route.value,
            "source_hashes": {},
            "engines": {},
            "outputs": outputs,
            "qa": {},
        },
    )


def _codes(report: ReconcileReport) -> set[str]:
    return {issue.code for issue in report.issues}


def test_audit_is_read_only_for_consistent_intake_job() -> None:
    _, root = _create_job("consistent-intake")
    before = (root / "job.json").read_bytes()

    report = audit_job("consistent-intake")

    assert report.status == "consistent"
    assert report.issues == []
    assert (root / "job.json").read_bytes() == before


def test_audit_reports_missing_manifest_output() -> None:
    job, root = _create_job("missing-output", JobState.FINAL_REVIEW)
    missing = root / "deliverables" / "candidate_editable.pptx"
    _write_manifest(
        root,
        job,
        "candidate",
        {"editable_pptx": {"path": str(missing), "sha256": "0" * 64}},
    )

    report = audit_job(job.id)

    assert report.status == "inconsistent"
    assert "manifest_output_missing" in _codes(report)


def test_audit_reports_manifest_output_hash_mismatch() -> None:
    job, root = _create_job("changed-output", JobState.FINAL_REVIEW)
    output = root / "deliverables" / "candidate_editable.pptx"
    output.write_bytes(b"changed after manifest")
    _write_manifest(
        root,
        job,
        "candidate",
        {"editable_pptx": {"path": str(output), "sha256": "0" * 64}},
    )

    report = audit_job(job.id)

    assert "manifest_output_hash_mismatch" in _codes(report)
    assert report.checked_outputs == 1


def test_audit_reports_changed_approved_artifact() -> None:
    job, root = _create_job("changed-approval", JobState.PROTOTYPE_REVIEW)
    ledger = root / "evidence-ledger.json"
    brief = root / "planning" / "brief.json"
    ledger.write_text("{}", encoding="utf-8")
    brief.write_text("{}", encoding="utf-8")
    job.approvals["brief"] = True
    job.approval_hashes["brief"] = {
        "evidence_ledger": sha256(ledger),
        "brief": "0" * 64,
    }
    write_json(root / "job.json", job)

    report = audit_job(job.id)

    assert "approval_hash_mismatch" in _codes(report)
    assert report.checked_approvals == ["brief"]


def test_audit_reports_delivered_job_without_final_approval() -> None:
    job, root = _create_job("delivered-without-final", JobState.DELIVERED)
    output = root / "deliverables" / "final_editable.pptx"
    output.write_bytes(b"valid final bytes")
    _write_manifest(
        root,
        job,
        "final",
        {"editable_pptx": {"path": str(output), "sha256": sha256(output)}},
    )

    report = audit_job(job.id)

    assert "delivered_without_final_approval" in _codes(report)


def test_audit_reports_final_artifact_in_intake_job() -> None:
    job, root = _create_job("intake-with-final")
    (root / "deliverables" / "final_editable.pptx").write_bytes(b"premature final")

    report = audit_job(job.id)

    assert "final_artifacts_before_final_review" in _codes(report)
    assert "build_manifest_missing" in _codes(report)


def test_reconcile_cli_returns_structured_read_only_report() -> None:
    job, _ = _create_job("reconcile-cli")

    result = CliRunner().invoke(app, ["reconcile", job.id, "--json"])

    assert result.exit_code == 0
    assert '"status": "consistent"' in result.stdout
