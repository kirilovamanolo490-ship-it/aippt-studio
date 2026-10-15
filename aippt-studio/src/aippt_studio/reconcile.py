from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from .models import BuildManifest, JobRecord, JobState, ProductionRoute, RevisionBatch
from .renderer import natural_sort_paths, sha256
from .storage import job_dir, read_json, read_model


class ReconcileIssue(BaseModel):
    code: str
    message: str
    path: str | None = None


class ReconcileReport(BaseModel):
    job_id: str
    status: Literal["consistent", "inconsistent"]
    state: str | None = None
    manifest_status: str | None = None
    checked_approvals: list[str] = Field(default_factory=list)
    checked_outputs: int = 0
    issues: list[ReconcileIssue] = Field(default_factory=list)


def _directory_hash(path: Path) -> str:
    digest = hashlib.sha256()
    files = natural_sort_paths([candidate for candidate in path.rglob("*") if candidate.is_file()])
    for item in files:
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(sha256(item).encode("ascii"))
    return digest.hexdigest()


def _approval_targets(
    root: Path,
    gate: str,
    route: ProductionRoute,
    recorded: dict[str, str],
) -> dict[str, Path]:
    targets: dict[str, Path] = {
        "evidence_ledger": root / "evidence-ledger.json",
        "brief": root / "planning" / "brief.json",
    }
    intake_decision = root / "planning" / "intake-decision.json"
    if intake_decision.is_file() or "intake_decision" in recorded:
        targets["intake_decision"] = intake_decision
    if (gate in {"prototype", "final"} and (root / "planning/authoring-policy.json").is_file()) or "authoring_policy" in recorded:
        targets["authoring_policy"] = root / "planning/authoring-policy.json"
    if gate in {"prototype", "final"}:
        expression_review = root / "reviews" / f"{gate}-expression-review.json"
        if expression_review.is_file() or "expression_review" in recorded:
            targets["expression_review"] = expression_review
        targets["deck_plan"] = root / "planning" / "deck-plan.json"
        native_scene = root / "planning/native-scene.json"
        if native_scene.is_file() or "native_scene" in recorded:
            targets["native_scene"] = native_scene
        asset_plan = root / "planning" / "asset-plan.json"
        asset_ledger = root / "assets" / "asset-ledger.json"
        if asset_plan.is_file() or "asset_plan" in recorded:
            targets["asset_plan"] = asset_plan
        if asset_ledger.is_file() or "asset_ledger" in recorded:
            targets["asset_ledger"] = asset_ledger
    if gate == "prototype":
        targets.update(
            {
                "prototype_pptx": root / "build" / "prototype.pptx",
                "prototype_html": root / "build" / "prototype.html",
                "prototype_previews": root / "build" / "prototype-previews",
                "prototype_montage": root / "build" / "prototype-montage.png",
            }
        )
        motion = root / "edits" / "motion-prototype.json"
        if motion.is_file() or "prototype_motion" in recorded:
            targets["prototype_motion"] = motion
    if gate == "final":
        targets.update(
            {
                "deck_pptx": root / "build" / "deck.pptx",
                "previews": root / "build" / "previews",
                "montage": root / "build" / "montage.png",
            }
        )
        native_powerpoint = root / "edits" / "native-powerpoint.json"
        if native_powerpoint.is_file() or "native_powerpoint" in recorded:
            targets["native_powerpoint"] = native_powerpoint
        animated_previews = root / "build" / "animated-previews"
        if (
            (animated_previews.is_dir() and any(animated_previews.glob("*.mp4")))
            or "animated_previews" in recorded
        ):
            targets["animated_previews"] = animated_previews
        if route == ProductionRoute.NATIVE:
            targets["deck_html"] = root / "build" / "deck.html"
            native_adapter = root / "build" / "native-adapter-result.json"
            if native_adapter.is_file() or "native_adapter_result" in recorded:
                targets["native_adapter_result"] = native_adapter
            motion = root / "edits" / "motion-final.json"
            if motion.is_file() or "motion" in recorded:
                targets["motion"] = motion
        else:
            external_manifest = root / "build" / "external-engine-manifest.json"
            targets["external_engine_manifest"] = external_manifest
            if external_manifest.is_file():
                try:
                    payload = read_json(external_manifest)
                    build_source = payload.get("build_source") if isinstance(payload, dict) else None
                except (OSError, ValueError, TypeError):
                    build_source = None
                if isinstance(build_source, str) and build_source:
                    source = (root / build_source).resolve()
                    if root.resolve() in source.parents:
                        targets["external_build_source"] = source
    return targets


def _issue(issues: list[ReconcileIssue], code: str, message: str, path: Path | None = None) -> None:
    issues.append(ReconcileIssue(code=code, message=message, path=str(path) if path else None))


def _deck_plan_change_is_revision_authorized(root: Path, gate: str, current_hash: str) -> bool:
    if gate != "prototype":
        return False
    for path in sorted((root / "revisions").glob("revision-*.json")):
        try:
            batch = read_model(path, RevisionBatch)
        except (OSError, ValueError, ValidationError):
            continue
        if (
            batch.review_gate == "final"
            and batch.status in {"applied", "rebuilt"}
            and batch.applied_deck_plan_sha256 == current_hash
        ):
            return True
    return False


def _audit_approvals(root: Path, job: JobRecord, issues: list[ReconcileIssue]) -> list[str]:
    checked: list[str] = []
    for gate in ("brief", "prototype", "final"):
        approved = bool(job.approvals.get(gate))
        recorded = job.approval_hashes.get(gate)
        if not approved:
            if recorded is not None:
                _issue(
                    issues,
                    "approval_hash_without_approval",
                    f"{gate} has recorded hashes but is not approved",
                    root / "job.json",
                )
            continue
        checked.append(gate)
        if recorded is None:
            _issue(
                issues,
                "approved_gate_missing_hashes",
                f"{gate} is approved but has no approval_hashes entry",
                root / "job.json",
            )
            continue

        targets = _approval_targets(root, gate, job.route, recorded)
        for name in sorted(set(recorded) - set(targets)):
            _issue(
                issues,
                "approval_hash_unknown_artifact",
                f"{gate} records an unknown approval artifact: {name}",
                root / "job.json",
            )
        for name, path in targets.items():
            expected = recorded.get(name)
            if expected is None:
                _issue(
                    issues,
                    "approval_hash_missing_artifact",
                    f"{gate} approval does not record {name}",
                    root / "job.json",
                )
                continue
            if not path.exists():
                _issue(
                    issues,
                    "approval_artifact_missing",
                    f"{gate} approval artifact is missing: {name}",
                    path,
                )
                continue
            actual = _directory_hash(path) if path.is_dir() else sha256(path)
            if actual != expected:
                if name == "deck_plan" and _deck_plan_change_is_revision_authorized(root, gate, actual):
                    continue
                _issue(
                    issues,
                    "approval_hash_mismatch",
                    f"{gate} approval artifact changed: {name}",
                    path,
                )
    return checked


def _resolve_output_path(root: Path, raw_path: object) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _audit_manifest(
    root: Path,
    job: JobRecord,
    issues: list[ReconcileIssue],
) -> tuple[str | None, int]:
    delivery = root / "deliverables"
    manifest_path = delivery / "build-manifest.json"
    delivery_files = [path for path in delivery.rglob("*") if path.is_file()] if delivery.is_dir() else []
    if not manifest_path.is_file():
        if job.state == JobState.DELIVERED or delivery_files:
            _issue(issues, "build_manifest_missing", "delivery files exist without build-manifest.json", manifest_path)
        return None, 0

    try:
        manifest = read_model(manifest_path, BuildManifest)
    except (OSError, ValueError, ValidationError) as error:
        _issue(issues, "build_manifest_invalid", f"build manifest is invalid: {error}", manifest_path)
        return None, 0

    if manifest.job_id != job.id:
        _issue(issues, "manifest_job_mismatch", "build manifest belongs to a different job", manifest_path)
    if manifest.route != job.route:
        _issue(issues, "manifest_route_mismatch", "build manifest route differs from job.json", manifest_path)
    if job.state == JobState.DELIVERED and manifest.status != "final":
        _issue(issues, "delivered_manifest_not_final", "a delivered job must have a final build manifest", manifest_path)
    if job.state != JobState.DELIVERED and manifest.status == "final":
        _issue(issues, "final_manifest_state_mismatch", "a final build manifest exists but the job is not delivered", manifest_path)

    checked = 0
    delivery_root = delivery.resolve()
    for name, metadata in manifest.outputs.items():
        path = _resolve_output_path(root, metadata.get("path"))
        if path is None:
            _issue(issues, "manifest_output_path_invalid", f"manifest output has no valid path: {name}", manifest_path)
            continue
        if path != delivery_root and delivery_root not in path.parents:
            _issue(issues, "manifest_output_outside_delivery", f"manifest output escapes deliverables: {name}", path)
            continue
        if not path.exists():
            _issue(issues, "manifest_output_missing", f"manifest output is missing: {name}", path)
            continue
        expected = metadata.get("sha256")
        if not isinstance(expected, str) or not expected:
            _issue(issues, "manifest_output_hash_missing", f"manifest output has no SHA-256: {name}", manifest_path)
            continue
        actual = _directory_hash(path) if path.is_dir() else sha256(path)
        checked += 1
        if actual != expected:
            _issue(issues, "manifest_output_hash_mismatch", f"manifest output changed: {name}", path)
    return manifest.status, checked


def audit_job(job_id: str) -> ReconcileReport:
    """Audit a job without changing its state or any on-disk artifact."""

    root = job_dir(job_id)
    job_path = root / "job.json"
    issues: list[ReconcileIssue] = []
    if not job_path.is_file():
        _issue(issues, "job_file_missing", "job.json does not exist", job_path)
        return ReconcileReport(job_id=job_id, status="inconsistent", issues=issues)
    try:
        job = read_model(job_path, JobRecord)
    except (OSError, ValueError, ValidationError) as error:
        _issue(issues, "job_file_invalid", f"job.json is invalid: {error}", job_path)
        return ReconcileReport(job_id=job_id, status="inconsistent", issues=issues)

    checked_approvals = _audit_approvals(root, job, issues)
    if job.state == JobState.DELIVERED and not job.approvals.get("final"):
        _issue(issues, "delivered_without_final_approval", "job is delivered without final approval", job_path)

    delivery = root / "deliverables"
    delivery_artifacts = [path for path in delivery.rglob("*") if path.is_file()] if delivery.is_dir() else []
    final_names = {"final_editable.pptx", "final.pdf", "visual_alternate.pptx"}
    final_artifacts = [path for path in delivery_artifacts if path.name in final_names]
    early_states = {JobState.INTAKE, JobState.BRIEF_REVIEW, JobState.PROTOTYPE_REVIEW, JobState.FULL_BUILD}
    if job.state in early_states and delivery_artifacts:
        _issue(
            issues,
            "delivery_artifacts_before_final_review",
            f"{job.state.value} job already contains live delivery artifacts from a non-current plan",
            delivery_artifacts[0],
        )
    if job.state in early_states and final_artifacts:
        _issue(
            issues,
            "final_artifacts_before_final_review",
            f"{job.state.value} job already contains final delivery artifacts",
            final_artifacts[0],
        )

    manifest_status, checked_outputs = _audit_manifest(root, job, issues)
    return ReconcileReport(
        job_id=job.id,
        status="consistent" if not issues else "inconsistent",
        state=job.state.value,
        manifest_status=manifest_status,
        checked_approvals=checked_approvals,
        checked_outputs=checked_outputs,
        issues=issues,
    )
