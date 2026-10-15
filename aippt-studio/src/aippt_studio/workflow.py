from __future__ import annotations

import json
import hashlib
import shutil
import time
import uuid
import os
import re
import zipfile
import tempfile
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw

from .adapter_contracts import AdapterArtifact, AdapterBuildRequest, AdapterBuildResult, AdapterStatus, EditabilityClass, EngineReceipt
from .assets.acquisition import AssetLedger, pending_required_assets
from .adapters import registry as adapter_registry, route_ready
from .adapters.native import NativeAdapter
from .adapters.registry import reviewed_commit
from .config import PROJECT_ROOT, WORKSPACE_ROOT, jobs_root
from .delivery_artifacts import supersede_delivery_artifacts
from .html_deck import write_deck_html
from .models import (
    BuildManifest,
    DeckPlan,
    EvidenceLedger,
    ExternalEngineManifest,
    JobBrief,
    JobRecord,
    JobState,
    PageCountError,
    ChangeMode,
    PresentationForm,
    PresentationIntent,
    PptxEditBatch,
    ProductionRoute,
    InputRole,
    VisualSystemMode,
    ReviewComment,
    ReviewDecision,
    RevisionBatch,
    SourceRecord,
    utc_now,
    validate_plan_page_count,
)
from .motion import apply_motion_plan
from .notes import audit_pptx_notes, sanitize_pptx_notes, sanitize_speaker_notes_text
from .planner import create_brief, create_deck_plan
from .presentation_intent import (
    PresentationIntentError,
    WorkBindingEnvironmentError,
    apply_intent_overrides,
    bind_work_request,
    canonical_work_id,
    presentation_intent_summary,
    read_bound_work_request,
    requests_bounded_revision,
    requests_template_adaptation,
)
from .routing import RoutePolicy, classify_input_roles, select_route
from .reconcile import audit_job
from .renderer import RenderError, create_montage, natural_sort_paths, officecli_validate, powerpoint_roundtrip, render_html, sha256
from .storage import initialize_job_directories, job_dir, load_job, locked_job, read_json, read_model, safe_job_id, save_job, write_json
from .template_contract import template_request_block_reason
from .template_verification import (
    _template_application_inputs,
    _validate_mapping,
    _verify_template_application,
)


class WorkflowError(RuntimeError):
    pass


def _read_deck_plan(root: Path) -> DeckPlan:
    plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    brief_path = root / "planning" / "brief.json"
    if brief_path.is_file():
        validate_plan_page_count(read_model(brief_path, JobBrief), plan)
    return plan


def _replace_with_retry(source: Path, target: Path, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            source.replace(target)
            return
        except OSError as error:
            if time.monotonic() >= deadline or getattr(error, "winerror", None) not in {5, 32}:
                raise
            time.sleep(0.2)


def _directory_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for item in natural_sort_paths([candidate for candidate in path.rglob("*") if candidate.is_file()]):
        digest.update(item.relative_to(path).as_posix().encode("utf-8"))
        digest.update(sha256(item).encode("ascii"))
    return digest.hexdigest()


def _qa_status(value: object, expected: str) -> bool:
    return isinstance(value, dict) and value.get("status") == expected


def record_final_delivery_learning_qa(root: Path, output: Path, qa: dict[str, object], *, skip_powerpoint: bool) -> dict:
    """Persist a compact final-delivery QA receipt for project archival.

    This is intentionally after atomic delivery promotion.  It never turns a
    skipped Office roundtrip, a not-ready expression review, or a quality report
    for different bytes into a passed archive result.  Legacy/global jobs still
    receive ordinary delivery manifests; only a complete Work binding receives
    the stricter proof below.
    """

    receipt: dict[str, object] = {
        "schema_version": 1,
        "qa_kind": "final_delivery",
        "status": "needs_review",
        "checks": {
            "presentation_quality": (qa.get("presentation_quality") or {}).get("status")
            if isinstance(qa.get("presentation_quality"), dict) else None,
            "expression_review": (qa.get("page_expression_review") or {}).get("status")
            if isinstance(qa.get("page_expression_review"), dict) else None,
            "powerpoint_roundtrip": (qa.get("powerpoint_roundtrip") or {}).get("status")
            if isinstance(qa.get("powerpoint_roundtrip"), dict) else qa.get("powerpoint_roundtrip"),
            "officecli": (qa.get("officecli") or {}).get("status")
            if isinstance(qa.get("officecli"), dict) else qa.get("officecli"),
            "evidence_traceability": (qa.get("evidence_traceability") or {}).get("status")
            if isinstance(qa.get("evidence_traceability"), dict) else qa.get("evidence_traceability"),
        },
    }
    if skip_powerpoint:
        receipt.update(status="not_verified", reason="powerpoint_roundtrip_skipped")
        write_json(root / "qa" / "final-delivery-qa.json", receipt)
        return receipt
    if not _qa_status(qa.get("presentation_quality"), "passed"):
        receipt["reason"] = "presentation_quality_not_passed"
    elif not _qa_status(qa.get("page_expression_review"), "ready"):
        receipt["reason"] = "expression_review_not_ready"
    elif not _qa_status(qa.get("powerpoint_roundtrip"), "passed"):
        receipt["reason"] = "powerpoint_roundtrip_not_passed"
    elif not _qa_status(qa.get("officecli"), "passed"):
        receipt["reason"] = "officecli_not_passed"
    elif not _qa_status(qa.get("evidence_traceability"), "passed"):
        receipt["reason"] = "evidence_traceability_not_passed"
    else:
        try:
            from .project_learning import current_qa_provenance_for_output

            proof = current_qa_provenance_for_output(root, output)
            presentation = qa["presentation_quality"]
            expression = qa["page_expression_review"]
            expression_inputs = expression.get("inputs") if isinstance(expression, dict) else None
            source_deck = root / "build" / "deck.pptx"
            if proof is None:
                receipt["reason"] = "current_work_bound_output_unavailable"
            elif presentation.get("source_sha256") != proof["output_sha256"]:
                receipt["reason"] = "presentation_quality_output_mismatch"
            elif not isinstance(expression_inputs, dict) or expression_inputs.get("build/deck.pptx") != sha256(source_deck):
                receipt["reason"] = "expression_review_input_mismatch"
            else:
                receipt.update(status="passed", project_learning_provenance=proof)
        except (OSError, ValueError, TypeError, AttributeError):
            receipt["reason"] = "current_work_bound_output_unavailable"
    write_json(root / "qa" / "final-delivery-qa.json", receipt)
    return receipt


def _gate_hashes(root: Path, gate: str, route: ProductionRoute) -> dict[str, str]:
    targets: dict[str, Path] = {
        "evidence_ledger": root / "evidence-ledger.json",
        "brief": root / "planning" / "brief.json",
    }
    intake_decision = root / "planning" / "intake-decision.json"
    if intake_decision.is_file():
        targets["intake_decision"] = intake_decision
    if gate in {"prototype", "final"} and (root / "planning/authoring-policy.json").is_file():
        targets["authoring_policy"] = root / "planning/authoring-policy.json"
    if gate in {"prototype", "final"}:
        targets["deck_plan"] = root / "planning" / "deck-plan.json"
        expression_review = root / "reviews" / f"{gate}-expression-review.json"
        if expression_review.is_file():
            targets["expression_review"] = expression_review
        if (root / "planning/native-scene.json").is_file():
            targets["native_scene"] = root / "planning/native-scene.json"
        asset_plan = root / "planning" / "asset-plan.json"
        asset_ledger = root / "assets" / "asset-ledger.json"
        if asset_plan.is_file():
            targets["asset_plan"] = asset_plan
        if asset_ledger.is_file():
            targets["asset_ledger"] = asset_ledger
    if gate == "prototype":
        targets["prototype_pptx"] = root / "build" / "prototype.pptx"
        targets["prototype_html"] = root / "build" / "prototype.html"
        targets["prototype_previews"] = root / "build" / "prototype-previews"
        targets["prototype_montage"] = root / "build" / "prototype-montage.png"
        motion_batch = root / "edits" / "motion-prototype.json"
        if motion_batch.is_file():
            targets["prototype_motion"] = motion_batch
    if gate == "final":
        targets["deck_pptx"] = root / "build" / "deck.pptx"
        targets["previews"] = root / "build" / "previews"
        targets["montage"] = root / "build" / "montage.png"
        native_powerpoint = root / "edits" / "native-powerpoint.json"
        if native_powerpoint.is_file():
            targets["native_powerpoint"] = native_powerpoint
        animated_previews = root / "build" / "animated-previews"
        if animated_previews.is_dir() and any(animated_previews.glob("*.mp4")):
            targets["animated_previews"] = animated_previews
        if route == ProductionRoute.NATIVE:
            targets["deck_html"] = root / "build" / "deck.html"
            native_adapter = root / "build" / "native-adapter-result.json"
            if native_adapter.is_file():
                targets["native_adapter_result"] = native_adapter
            motion_batch = root / "edits" / "motion-final.json"
            if motion_batch.is_file():
                targets["motion"] = motion_batch
        else:
            external_manifest_path = root / "build" / "external-engine-manifest.json"
            targets["external_engine_manifest"] = external_manifest_path
            if external_manifest_path.is_file():
                external_manifest = read_model(external_manifest_path, ExternalEngineManifest)
                if external_manifest.build_source:
                    external_source = (root / external_manifest.build_source).resolve()
                    if root.resolve() not in external_source.parents:
                        raise WorkflowError("external build source is outside the job directory")
                    targets["external_build_source"] = external_source
    missing = [name for name, path in targets.items() if not path.exists()]
    if missing:
        raise WorkflowError(f"approval artifacts are missing: {', '.join(missing)}")
    return {
        name: _directory_hash(path) if path.is_dir() else sha256(path)
        for name, path in targets.items()
    }


def _approval_is_current(job: JobRecord, root: Path, gate: str) -> bool:
    if not job.approvals.get(gate) or gate not in job.approval_hashes:
        return False
    try:
        return job.approval_hashes[gate] == _gate_hashes(root, gate, job.route)
    except WorkflowError:
        return False


def _generic_renderer_block_reason(brief: JobBrief) -> str | None:
    """Return a reason when a generic deck plan would violate the intent.

    The generic HTML/native planner is a new-composition tool.  It must not
    quietly stand in for a source-preserving operation merely because a route
    happens to be executable on this host.
    """

    intent = brief.presentation_intent
    if intent.change_mode == ChangeMode.LOCAL_PATCH:
        scope = intent.preservation.target_slide_numbers
        if scope:
            rendered_scope = "、".join(str(number) for number in scope)
            return (
                f"本任务是第 {rendered_scope} 页的局部修订；通用新稿规划、样稿和模板重拼装都不能替代原稿编辑。"
                "请使用 hash-bound fast-revise / pptx-patch 或 apply-edits，并保留非目标页验证证据。"
            )
        return (
            "本任务被设为局部修订，但尚未记录具体页/对象范围；不能以通用新稿代替原稿编辑。"
            "请先用 pptx-inspect 明确可编辑目标和范围，再以 fast-revise / pptx-patch 作局部修改。"
        )
    if intent.change_mode == ChangeMode.UNCLASSIFIED:
        return (
            "任务意图仍未分类，不能用通用样稿替代尚未确认的修改方式。"
            "请明确这是局部编辑、模板重排、新建、重建还是视觉替代；局部编辑还需指定唯一 edit_target。"
        )
    contract = intent.preservation
    if intent.change_mode == ChangeMode.TEMPLATE_RELAYOUT and (
        contract.preserve_non_target_slides
        or contract.insert_position != "unspecified"
        or contract.page_delta is not None
    ):
        return (
            "当前要求保留原稿范围或在既有演示中前插/后插页面；模板重拼装不能证明非目标页未变。"
            "请使用 hash-bound pptx-patch 或 apply-edits，并记录原稿、目标范围和非目标页验证证据。"
        )
    if intent.change_mode == ChangeMode.TEMPLATE_RELAYOUT and (
        contract.preserve_brand_objects or contract.preserve_media
    ):
        return (
            "当前要求保留既有品牌对象或媒体；在没有逐对象映射和验证的情况下，不能用模板重拼装替代原稿编辑。"
            "请使用 hash-bound pptx-patch 或提供经过核验的对象映射。"
        )
    return None


def _template_requirement_reason(job: JobRecord, root: Path, brief: JobBrief) -> str | None:
    """Check the bound user request against route, source, and occasion."""

    if job.work_request is None:
        return None
    try:
        request_text, _binding = read_bound_work_request(job.id)
    except ValueError as error:
        raise WorkflowError(str(error)) from error
    if not requests_template_adaptation(request_text):
        return None
    sources = {
        source_id: read_model(root / "normalized" / "sources" / f"{source_id}.json", SourceRecord)
        for source_id in job.source_ids
    }
    return template_request_block_reason(
        request_text, job, brief, sources,
        library_catalog=WORKSPACE_ROOT / "模板库" / "CATALOG.jsonl",
        job_root=root,
    )


def _template_selection_candidates(brief: JobBrief) -> dict[str, object]:
    """Expose occasion-compatible library files before anyone binds a source."""

    from .template_library import search_catalog
    form = brief.presentation_intent.presentation_form.value
    query = {
        "competition_defense": "竞赛 答辩",
        "academic_defense": "学术 答辩",
        "business_report": "工作汇报",
        "government_briefing": "政务 汇报",
        "teaching": "教学 培训",
        "technical_explanation": "技术 说明",
    }.get(form, "项目 演示")
    result = search_catalog(
        WORKSPACE_ROOT / "模板库" / "CATALOG.jsonl", query, form=form, limit=30,
    )
    candidates = [
        {"path": row["path"], "title": row["title"], "review_status": row["review_status"],
         "candidate_only": True}
        for row in result["results"]
        if row["asset_type"] == "template" and row["exists"]
    ][:5]
    return {
        "schema_version": 1,
        "status": "candidates_require_page_review" if candidates else "no_compatible_candidate",
        "required_form": form,
        "query": query,
        "candidates": candidates,
        "rule": "Catalog hits are only candidates. Inspect actual pages, bind one verified library PPTX as template, then prove mapped visible output pages.",
    }


def _slide_hash(slide: object) -> str:
    payload = slide.model_dump(mode="json") if hasattr(slide, "model_dump") else slide
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolve_rebuilt_revisions(root: Path, gate: str) -> None:
    resolved_comment_ids: set[str] = set()
    for path in sorted((root / "revisions").glob("revision-*.json")):
        batch = read_model(path, RevisionBatch)
        if batch.status != "applied" or batch.review_gate != gate:
            continue
        batch.status = "rebuilt"
        batch.rebuilt_at = utc_now()
        resolved_comment_ids.update(batch.comment_ids)
        write_json(path, batch)
    if not resolved_comment_ids:
        return
    comments_path = root / "reviews" / "comments.json"
    comments = [ReviewComment.model_validate(item) for item in read_json(comments_path)]
    for comment in comments:
        if comment.id in resolved_comment_ids and comment.status == "applied":
            comment.status = "resolved"
    write_json(comments_path, [comment.model_dump(mode="json") for comment in comments])


def _has_current_applied_revision(root: Path, gate: str) -> bool:
    plan_path = root / "planning" / "deck-plan.json"
    current_hash = sha256(plan_path) if plan_path.is_file() else None
    return any(
        batch.status == "applied"
        and batch.review_gate == gate
        and batch.applied_deck_plan_sha256 == current_hash
        for batch in (
            read_model(path, RevisionBatch)
            for path in sorted((root / "revisions").glob("revision-*.json"))
        )
    )


def _template_application_proof_reason(
    job: JobRecord, root: Path, plan: DeckPlan, proof: dict[str, object], map_sha256: str,
) -> str | None:
    if job.work_request is None or job.route != ProductionRoute.TEMPLATE:
        return None
    request_text, _binding = read_bound_work_request(job.id)
    if not requests_template_adaptation(request_text) or requests_bounded_revision(request_text):
        return None
    selected = [source_id for source_id, role in job.input_roles.items() if role == InputRole.TEMPLATE]
    if len(selected) != 1:
        return "required template application has no unique template source"
    source = read_model(root / "normalized" / "sources" / f"{selected[0]}.json", SourceRecord)
    pages = proof.get("pages")
    if (proof.get("status") != "verified" or proof.get("source_sha256") != source.sha256
            or proof.get("page_map_sha256") != map_sha256
            or proof.get("verified_slide_count") != len(plan.slides)
            or not isinstance(pages, list)
            or [row.get("deck_slide_id") for row in pages if isinstance(row, dict)]
            != [slide.id for slide in plan.slides]
            or len(pages) != len(plan.slides)):
        return "required template application lacks a source-bound, page-complete verification receipt"
    return None


def _template_application_artifact_reason(
    job: JobRecord, root: Path, plan: DeckPlan, proof: dict[str, object],
    map_sha256: str, deck: Path,
) -> str | None:
    """Recheck the actual PPTX; an external manifest cannot attest to itself."""

    from .hybrid_job import BINDING, ENGINE, verify_hybrid_output
    if job.route == ProductionRoute.TEMPLATE and (
            (root / BINDING).exists() or (root / BINDING).is_symlink() or proof.get("kind") == ENGINE):
        return verify_hybrid_output(job, root, plan, proof, map_sha256, deck)
    if reason := _template_application_proof_reason(job, root, plan, proof, map_sha256):
        return reason
    if job.work_request is None or job.route != ProductionRoute.TEMPLATE:
        return None
    request_text, _binding = read_bound_work_request(job.id)
    if not requests_template_adaptation(request_text) or requests_bounded_revision(request_text):
        return None
    selected = [source_id for source_id, role in job.input_roles.items() if role == InputRole.TEMPLATE]
    source = read_model(root / "normalized" / "sources" / f"{selected[0]}.json", SourceRecord)
    template = (root / source.stored_path).resolve()
    mapping_path = root / "planning" / "template-map.json"
    if (root.resolve() not in template.parents or not template.is_file()
            or sha256(template) != source.sha256):
        return "required template application source bytes are missing or changed"
    if not mapping_path.is_file() or sha256(mapping_path) != map_sha256:
        return "required template application map differs from the reviewed build source"
    try:
        mapping = read_json(mapping_path)
        if not isinstance(mapping, dict):
            return "required template application map is not an object"
        with zipfile.ZipFile(template) as archive:
            source_slide_count = sum(
                1 for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
        plan_data = plan.model_dump(mode="json")
        if reason := _validate_mapping(
            mapping, record={"id": source.id}, source_hash=source.sha256,
            slide_count=source_slide_count, plan=plan_data,
            plan_sha256=sha256(root / "planning" / "deck-plan.json"),
        ):
            return reason
        expected_pages, reason = _template_application_inputs(template, mapping, plan_data)
        if reason:
            return reason
        if proof.get("pages") != expected_pages:
            return "required template application page receipt differs from the source-bound map"
        return _verify_template_application(template, deck, mapping, expected_pages)
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as error:
        return f"required template application could not verify the actual PPTX: {error}"


def _validate_external_output(job: JobRecord, root: Path, plan: DeckPlan) -> tuple[ExternalEngineManifest, list[Path]]:
    manifest_path = root / "build" / "external-engine-manifest.json"
    manifest = read_model(manifest_path, ExternalEngineManifest)
    expected_class = {
        ProductionRoute.TEMPLATE: "native_editable",
        ProductionRoute.REBUILD: "reconstructed_editable",
        ProductionRoute.VISUAL: "image_based",
    }[job.route]
    if manifest.job_id != job.id or manifest.route != job.route or manifest.editable_class != expected_class:
        raise WorkflowError("external engine manifest does not match this job route")
    plan_path = root / "planning" / "deck-plan.json"
    if manifest.deck_plan_sha256 != sha256(plan_path):
        raise WorkflowError("external output was built from an outdated deck plan")
    deck = root / "build" / "deck.pptx"
    if not deck.is_file() or manifest.pptx_sha256 != sha256(deck):
        raise WorkflowError("external PPTX is missing or does not match its manifest")
    note_issues = audit_pptx_notes(deck)
    if note_issues:
        raise WorkflowError(
            "external PPTX speaker notes contain [Sources] blocks or local filesystem paths; "
            "keep provenance in evidence-ledger.json, source-notes.txt, and build manifests"
        )
    previews = natural_sort_paths(list((root / "build" / "previews").glob("*.png")))
    expected_ids = {slide.id for slide in plan.slides}
    actual_hashes = {path.stem: sha256(path) for path in previews}
    if set(actual_hashes) != expected_ids or manifest.preview_hashes != actual_hashes:
        raise WorkflowError("external previews are incomplete or do not match their manifest")
    if not manifest.build_source or not manifest.build_source_sha256:
        raise WorkflowError("external engine manifest must name and hash a rebuild source")
    source = (root / manifest.build_source).resolve()
    if root.resolve() not in source.parents or not source.is_file():
        raise WorkflowError("external build source is missing or outside the job directory")
    if manifest.build_source_sha256 != sha256(source):
        raise WorkflowError("external build source does not match its manifest")
    if reason := _template_application_artifact_reason(
        job, root, plan, manifest.template_application, manifest.build_source_sha256, deck,
    ):
        raise WorkflowError(reason)
    return manifest, previews


def _adapter_request(job: JobRecord, root: Path) -> AdapterBuildRequest:
    brief = read_model(root / "planning" / "brief.json", JobBrief)
    validate_plan_page_count(brief, read_model(root / "planning" / "deck-plan.json", DeckPlan))
    request_text = read_bound_work_request(job.id)[0] if job.work_request is not None else ""
    return AdapterBuildRequest(
        job_id=job.id,
        route=job.route,
        brief="planning/brief.json",
        deck_plan="planning/deck-plan.json",
        evidence_ledger="evidence-ledger.json",
        asset_plan="planning/asset-plan.json" if (root / "planning" / "asset-plan.json").is_file() else None,
        asset_ledger="assets/asset-ledger.json" if (root / "assets" / "asset-ledger.json").is_file() else None,
        input_roles={source_id: role.value for source_id, role in job.input_roles.items()},
        cloud_allowed=brief.cloud_allowed,
        template_application_required=(
            requests_template_adaptation(request_text) and not requests_bounded_revision(request_text)
        ),
    )


def _promote_adapter_result(job: JobRecord, root: Path, result: AdapterBuildResult) -> None:
    if result.status != AdapterStatus.SUCCEEDED or result.receipt is None or result.editability is None:
        raise WorkflowError("adapter did not return a successful build result")
    expected = {
        ProductionRoute.TEMPLATE: EditabilityClass.NATIVE_EDITABLE,
        ProductionRoute.REBUILD: EditabilityClass.RECONSTRUCTED_EDITABLE,
        ProductionRoute.VISUAL: EditabilityClass.IMAGE_BASED,
    }[job.route]
    if result.editability != expected:
        raise WorkflowError(f"adapter editability must be {expected.value} for {job.route.value}")
    artifact_paths: dict[str, list[Path]] = {}
    for artifact in result.artifacts:
        path = (root / artifact.path).resolve()
        if root.resolve() not in path.parents or not path.is_file():
            raise WorkflowError(f"adapter artifact is missing or outside the job: {artifact.path}")
        if sha256(path) != artifact.sha256:
            raise WorkflowError(f"adapter artifact hash mismatch: {artifact.path}")
        artifact_paths.setdefault(artifact.kind, []).append(path)
    if len(artifact_paths.get("pptx", [])) != 1:
        raise WorkflowError("adapter result must contain exactly one PPTX")
    if len(artifact_paths.get("build_source", [])) != 1:
        raise WorkflowError("adapter result must contain exactly one rebuild source")
    previews = artifact_paths.get("preview", [])
    plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    if {path.stem for path in previews} != {slide.id for slide in plan.slides}:
        raise WorkflowError("adapter result previews do not match the deck plan")
    template_application = result.metadata.get("template_application", {})
    if not isinstance(template_application, dict):
        raise WorkflowError("adapter template application receipt must be an object")
    if reason := _template_application_artifact_reason(
        job, root, plan, template_application, sha256(artifact_paths["build_source"][0]),
        artifact_paths["pptx"][0],
    ):
        raise WorkflowError(reason)

    promotion = root / "build" / f"adapter-promotion-{uuid.uuid4().hex[:8]}"
    promoted_previews = promotion / "previews"
    promoted_source_dir = promotion / "source"
    promoted_previews.mkdir(parents=True)
    promoted_source_dir.mkdir()
    promoted_deck = promotion / "deck.pptx"
    shutil.copy2(artifact_paths["pptx"][0], promoted_deck)
    for preview in previews:
        shutil.copy2(preview, promoted_previews / preview.name)
    source = artifact_paths["build_source"][0]
    promoted_source = promoted_source_dir / source.name
    shutil.copy2(source, promoted_source)
    manifest = ExternalEngineManifest(
        job_id=job.id,
        route=job.route,
        engine=result.receipt.engine,
        version=result.receipt.version,
        editable_class=result.editability.value,
        deck_plan_sha256=sha256(root / "planning" / "deck-plan.json"),
        input_hashes={source.source_id: source.sha256 for source in read_model(root / "evidence-ledger.json", EvidenceLedger).sources},
        pptx_sha256=sha256(promoted_deck),
        preview_hashes={path.stem: sha256(path) for path in promoted_previews.glob("*.png")},
        build_source=f"build/adapter-source/{promoted_source.name}",
        build_source_sha256=sha256(promoted_source),
        providers=result.providers,
        warnings=result.warnings,
        adapter_receipt=result.receipt.model_dump(mode="json"),
        template_application=template_application,
    )
    write_json(promotion / "external-engine-manifest.json", manifest)
    canonical_previews = root / "build" / "previews"
    canonical_source = root / "build" / "adapter-source"
    backup = root / "build" / f"adapter-backup-{uuid.uuid4().hex[:8]}"
    backup.mkdir()
    targets = [root / "build" / "deck.pptx", canonical_previews, canonical_source, root / "build" / "external-engine-manifest.json"]
    try:
        for target in targets:
            if target.exists():
                target.replace(backup / target.name)
        promoted_deck.replace(root / "build" / "deck.pptx")
        promoted_previews.replace(canonical_previews)
        promoted_source_dir.replace(canonical_source)
        (promotion / "external-engine-manifest.json").replace(root / "build" / "external-engine-manifest.json")
    except Exception:
        for target in targets:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        for item in backup.iterdir():
            item.replace(root / "build" / item.name)
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)
        shutil.rmtree(promotion, ignore_errors=True)
    from .hybrid_job import clean_promoted_hybrid_staging
    clean_promoted_hybrid_staging(root, result)


def _ensure_adapter_previews(job: JobRecord, root: Path, result: AdapterBuildResult) -> AdapterBuildResult:
    if result.status != AdapterStatus.SUCCEEDED:
        return result
    plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    expected_ids = [slide.id for slide in plan.slides]
    actual = {Path(artifact.path).stem for artifact in result.artifacts if artifact.kind == "preview"}
    if actual == set(expected_ids):
        return result
    pptx_artifacts = [artifact for artifact in result.artifacts if artifact.kind == "pptx"]
    if len(pptx_artifacts) != 1:
        raise WorkflowError("adapter cannot generate previews without exactly one PPTX artifact")
    pptx = (root / pptx_artifacts[0].path).resolve()
    preview_dir = root / "build" / "adapter-staging" / "previews"
    from .hybrid_job import ENGINE
    if result.metadata.get("template_application", {}).get("kind") == ENGINE:
        if (not pptx.is_relative_to((root / "build/adapter-staging").resolve())
                or not re.fullmatch(r"hybrid-[0-9a-f]{12}", pptx.parent.name)):
            raise WorkflowError("hybrid previews require a contained engine staging directory")
        preview_dir = pptx.parent / "previews"
    if preview_dir.is_dir():
        shutil.rmtree(preview_dir)
    preview_dir.mkdir(parents=True)
    if os.environ.get("AIPPT_FAKE_RENDER") == "1":
        for slide_id in expected_ids:
            image = Image.new("RGB", (640, 360), "#0b2639")
            ImageDraw.Draw(image).text((30, 30), slide_id, fill="white")
            image.save(preview_dir / f"{slide_id}.png")
    else:
        from .mac_quick_png import export_png
        with tempfile.TemporaryDirectory(prefix="adapter-preview-", dir=preview_dir.parent) as temporary:
            image_dir = Path(temporary) / "slides"
            export_png(pptx, image_dir)
            exported = natural_sort_paths(list(image_dir.glob("*.png")))
            if len(exported) != len(expected_ids):
                raise WorkflowError("adapter PPTX slide count does not match the deck plan")
            for slide_id, source in zip(expected_ids, exported, strict=True):
                shutil.copy2(source, preview_dir / f"{slide_id}.png")
    result.artifacts = [artifact for artifact in result.artifacts if artifact.kind != "preview"]
    result.artifacts.extend(
        AdapterArtifact(
            kind="preview",
            path=path.relative_to(root).as_posix(),
            sha256=sha256(path),
            size_bytes=path.stat().st_size,
        )
        for path in natural_sort_paths(list(preview_dir.glob("*.png")))
    )
    return result


def _run_native_adapter(request: AdapterBuildRequest, root: Path) -> AdapterBuildResult:
    job = load_job(request.job_id)
    dirty = set(job.dirty_slides) if job.dirty_slides else None
    plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    html_path = write_deck_html(job.id, dirty_only=dirty)
    staging = root / "build" / f"native-adapter-{uuid.uuid4().hex[:8]}"
    staged_deck = staging / "deck.pptx"
    staged_previews = staging / "previews"
    staged_montage = staging / "montage.png"
    staging.mkdir(parents=True)
    native_scene = (root / "planning/native-scene.json").is_file()
    try:
        if native_scene:
            from .native_scene import render_scene
            render_scene(root, staged_deck, staged_previews)
        else:
            render_html(html_path, staged_deck, staged_previews)
        officecli_validate(staged_deck)
        apply_motion_plan(job.id, staged_deck, plan.slides, batch_id="motion-final")
        from .authoring_policy import check_job_presentation
        check_job_presentation(root, staged_deck, "full-build", enforce=bool(job.authoring_policy_id))
        create_montage(natural_sort_paths(list(staged_previews.glob("*.png"))), staged_montage)
        target_deck = root / "build" / "deck.pptx"
        target_previews = root / "build" / "previews"
        target_montage = root / "build" / "montage.png"
        backup = root / "build" / f"native-adapter-backup-{uuid.uuid4().hex[:8]}"
        backup.mkdir()
        targets = [target_deck, target_previews, target_montage]
        try:
            for target in targets:
                if target.exists():
                    target.replace(backup / target.name)
            _replace_with_retry(staged_deck, target_deck)
            staged_previews.replace(target_previews)
            staged_montage.replace(target_montage)
        except Exception:
            for target in targets:
                if target.is_dir():
                    shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            for item in backup.iterdir():
                item.replace(root / "build" / item.name)
            raise
        finally:
            shutil.rmtree(backup, ignore_errors=True)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    artifacts = [
        AdapterArtifact(kind="pptx", path="build/deck.pptx", sha256=sha256(root / "build" / "deck.pptx")),
        AdapterArtifact(kind="build_source", path="planning/native-scene.json" if native_scene else "build/deck.html",
                        sha256=sha256(root / ("planning/native-scene.json" if native_scene else "build/deck.html"))),
    ]
    artifacts.extend(
        AdapterArtifact(kind="preview", path=path.relative_to(root).as_posix(), sha256=sha256(path), size_bytes=path.stat().st_size)
        for path in natural_sort_paths(list((root / "build" / "previews").glob("*.png")))
    )
    return AdapterBuildResult(
        status=AdapterStatus.SUCCEEDED,
        receipt=EngineReceipt(engine="PptxGenJS" if native_scene else "dom-to-pptx",
                              version="4.0.1" if native_scene else "2.0.3",
                              command=["node", "author-native.cjs" if native_scene else "render.mjs"]),
        editability=EditabilityClass.NATIVE_EDITABLE,
        artifacts=artifacts,
    )


def _refresh_automatic_route(job: JobRecord, root: Path) -> None:
    if job.route_source == "user_override":
        return
    records = [
        read_model(root / "normalized" / "sources" / f"{source_id}.json", SourceRecord)
        for source_id in job.source_ids
        if (root / "normalized" / "sources" / f"{source_id}.json").is_file()
    ]
    roles = classify_input_roles(records)
    roles.update(job.input_roles)
    decision = select_route(roles, RoutePolicy())
    if (
        job.input_roles == decision.input_roles
        and job.route == decision.route
        and job.route_source == decision.source
        and job.route_reason == decision.reason
    ):
        return
    job.input_roles = decision.input_roles
    job.route = decision.route
    job.route_source = decision.source
    job.route_reason = decision.reason
    save_job(job)


def _validate_native_powerpoint(job: JobRecord, root: Path, deck: Path) -> dict[str, object] | None:
    artifact_path = root / "edits" / "native-powerpoint.json"
    preview_dir = root / "build" / "animated-previews"
    actual_previews = {
        path.stem: path
        for path in preview_dir.glob("*.mp4")
        if path.is_file()
    } if preview_dir.is_dir() else {}
    if not artifact_path.is_file():
        if actual_previews:
            raise WorkflowError("animated previews exist without a native PowerPoint audit")
        return None
    payload = read_json(artifact_path)
    if not isinstance(payload, dict) or payload.get("job_id") != job.id:
        raise WorkflowError("native PowerPoint audit does not belong to this job")
    if payload.get("output_sha256") != sha256(deck):
        raise WorkflowError("native PowerPoint deck changed after its audit")
    report = payload.get("report")
    if not isinstance(report, dict) or report.get("status") != "passed":
        raise WorkflowError("native PowerPoint audit did not pass")
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        raise WorkflowError("native PowerPoint plan is missing from its audit")
    preview_items = report.get("previews", [])
    if not isinstance(preview_items, list):
        raise WorkflowError("native PowerPoint preview report is invalid")
    expected_ids: set[str] = set()
    for item in preview_items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise WorkflowError("native PowerPoint preview report is invalid")
        preview_id = item["id"]
        if Path(preview_id).name != preview_id or preview_id in expected_ids:
            raise WorkflowError("native PowerPoint preview id is unsafe or duplicated")
        expected_ids.add(preview_id)
        preview = actual_previews.get(preview_id)
        if preview is None or item.get("sha256") != sha256(preview):
            raise WorkflowError(f"native PowerPoint preview is missing or changed: {preview_id}")
    if set(actual_previews) != expected_ids:
        raise WorkflowError("native PowerPoint animated previews do not match their audit")
    return payload


def _portable_deck_html(job: JobRecord, root: Path) -> str:
    content = (root / "build" / "deck.html").read_text(encoding="utf-8")
    for source_id in job.source_ids:
        record = read_model(root / "normalized" / "sources" / f"{source_id}.json", SourceRecord)
        if record.kind != "image":
            continue
        source = (root / record.stored_path).resolve()
        content = content.replace(source.as_uri(), f"assets/{quote(source.name)}")
    asset_ledger_path = root / "assets" / "asset-ledger.json"
    if asset_ledger_path.is_file():
        asset_ledger = read_model(asset_ledger_path, AssetLedger)
        for asset in asset_ledger.assets:
            source = (root / asset.stored_path).resolve()
            if root.resolve() in source.parents and source.is_file():
                content = content.replace(source.as_uri(), f"assets/acquired/{quote(source.name)}")
    return content


def _reference_powerpoint_source(job: JobRecord, root: Path) -> Path | None:
    for source_id in job.source_ids:
        record_path = root / "normalized" / "sources" / f"{source_id}.json"
        if not record_path.is_file():
            continue
        record = read_model(record_path, SourceRecord)
        source = (root / record.stored_path).resolve()
        if record.kind == "powerpoint" and root.resolve() in source.parents and source.is_file():
            return source
    return None


def _validate_evidence_traceability(root: Path, plan: DeckPlan, ledger: EvidenceLedger) -> dict[str, object]:
    known_sources = {source.source_id: source for source in ledger.sources}
    located_refs = 0
    for slide in plan.slides:
        if not slide.evidence_refs:
            raise WorkflowError(f"slide has no evidence references: {slide.id}")
        has_precise_evidence = False
        for reference in slide.evidence_refs:
            source_id, separator, locator = reference.partition("#")
            if source_id not in known_sources:
                raise WorkflowError(f"unknown evidence source on {slide.id}: {source_id}")
            source_record = read_model(root / "normalized" / "sources" / f"{source_id}.json", SourceRecord)
            if separator:
                if not locator.startswith("line:") or not source_record.normalized_path:
                    raise WorkflowError(f"invalid evidence locator on {slide.id}: {reference}")
                try:
                    line_number = int(locator.split(":", 1)[1])
                except ValueError as error:
                    raise WorkflowError(f"invalid evidence locator on {slide.id}: {reference}") from error
                lines = (root / source_record.normalized_path).read_text(encoding="utf-8").splitlines()
                if line_number < 1 or line_number > len(lines) or not lines[line_number - 1].strip():
                    raise WorkflowError(f"evidence locator is out of range on {slide.id}: {reference}")
                located_refs += 1
                has_precise_evidence = True
            elif source_record.kind == "image":
                has_precise_evidence = True
            elif source_record.normalized_path:
                source_lines = (root / source_record.normalized_path).read_text(encoding="utf-8").splitlines()
                if sum(1 for line in source_lines if line.strip()) == 1:
                    located_refs += 1
                    has_precise_evidence = True
        if slide.layout in {"content", "evidence"} and not has_precise_evidence:
            raise WorkflowError(f"factual slide requires a line locator or image source: {slide.id}")
    return {"status": "passed", "slides": len(plan.slides), "located_references": located_refs}


def create_job(
    name: str,
    client: str = "",
    job_id: str | None = None,
    route: ProductionRoute | None = None,
    *,
    bind_current_work: bool = True,
) -> JobRecord:
    identifier = safe_job_id(job_id or f"{name}-{uuid.uuid4().hex[:6]}")
    root = job_dir(identifier)
    try:
        root.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise FileExistsError(f"job already exists: {identifier}") from error
    initialize_job_directories(root)
    selected_route = route or ProductionRoute.NATIVE
    job = JobRecord(
        id=identifier,
        name=name,
        client=client,
        route=selected_route,
        route_source="user_override" if route is not None else "automatic",
        route_reason="route selected when the job was created" if route is not None else "awaiting input classification",
        authoring_policy_id="presentation-expression-v3",
    )
    save_job(job)
    # Work wrappers set AIPPT_WORK_DIR.  Binding the request here keeps the
    # normal `new → ingest → run` path compatible while making the Work's
    # original requirements available before a brief is created.
    if bind_current_work:
        try:
            bind_work_request(job.id)
        except PresentationIntentError:
            shutil.rmtree(root, ignore_errors=True)
            raise
    from .authoring_policy import initialize_authoring_policy
    initialize_authoring_policy(root)
    write_json(root / "evidence-ledger.json", EvidenceLedger())
    write_json(root / "reviews" / "decisions.json", [])
    write_json(root / "reviews" / "comments.json", [])
    return load_job(job.id)


def _work_input_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (item for item in directory.rglob("*") if item.is_file() and not any(part.startswith(".") for part in item.relative_to(directory).parts)),
        key=lambda item: item.as_posix(),
    )


def _work_metadata(work: Path) -> dict[str, str]:
    record = work / "work.json"
    if not record.is_file():
        raise WorkflowError("work-intake requires a Work work.json record")
    try:
        payload = json.loads(record.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkflowError("work-intake cannot read Work work.json") from error
    try:
        work_id = canonical_work_id(work)
    except WorkBindingEnvironmentError as error:
        raise WorkflowError("work-intake Work identity is inconsistent: " + str(error)) from error
    return {
        "work_id": work_id,
        "name": str(payload.get("name") or work.name),
        "client": str(payload.get("client") or ""),
    }


def _work_studio_jobs_root(work: Path) -> Path:
    return (work / "workspace" / "studio-jobs").resolve()


def _record_origin(record: SourceRecord) -> str | None:
    if record.original.startswith(("http://", "https://", "chat:")):
        return None
    try:
        return str(Path(record.original).expanduser().resolve())
    except OSError:
        return None


def _infer_work_presentation_roles(
    job: JobRecord,
    records: list[SourceRecord],
    *,
    source_paths: set[str],
    reference_paths: set[str],
    request_text: str,
) -> tuple[dict[str, InputRole], dict[str, object]]:
    """Infer a role only when the Work request makes it unambiguous.

    A PowerPoint in ``input/source`` remains factual content by default.  The
    exception is a direct bounded-edit request paired with exactly one editable
    PPTX, where leaving it as content would silently turn a local correction
    into a new deck.  Multiple candidates remain deliberately unresolved.
    """

    existing_targets = [
        source_id for source_id, role in job.input_roles.items()
        if role in {InputRole.EDIT_TARGET, InputRole.TEMPLATE}
    ]
    if existing_targets:
        return {}, {"status": "already_designated", "source_ids": existing_targets}
    editable = [
        record for record in records
        if record.kind == "powerpoint" and _record_origin(record) in source_paths | reference_paths
    ]
    if requests_bounded_revision(request_text):
        if len(editable) == 1:
            return {editable[0].id: InputRole.EDIT_TARGET}, {
                "status": "inferred_unique_edit_target",
                "source_ids": [editable[0].id],
                "reason": "explicit bounded revision plus one editable PowerPoint",
            }
        return {}, {
            "status": "needs_edit_target_designation",
            "source_ids": [record.id for record in editable],
            "reason": "bounded revision requires exactly one editable PowerPoint; no candidate is selected automatically",
        }
    if requests_template_adaptation(request_text):
        reference_templates = [record for record in editable if _record_origin(record) in reference_paths]
        candidates = reference_templates or editable
        if len(candidates) == 1:
            return {candidates[0].id: InputRole.TEMPLATE}, {
                "status": "inferred_unique_template",
                "source_ids": [candidates[0].id],
                "reason": "explicit template-adaptation request plus one eligible PowerPoint",
            }
        return {}, {
            "status": "needs_template_designation",
            "source_ids": [record.id for record in candidates],
            "reason": "template adaptation requires exactly one template source; no candidate is selected automatically",
        }
    return {}, {"status": "not_requested", "source_ids": []}


def initialize_work_intake(
    work_dir: Path | str,
    *,
    job_id: str | None = None,
    name: str | None = None,
    client: str | None = None,
) -> dict[str, object]:
    """Bind a Work request and import its source/reference directories once.

    Work requests stay outside the source ledger.  ``input/source`` enters as
    factual content; ``input/reference`` enters only as visual reference.  A
    direct bounded-edit or template instruction may designate exactly one PPTX
    automatically; ambiguity is exposed for review rather than guessed.
    """

    work = Path(work_dir).expanduser().resolve()
    if not work.is_dir() or not (work / "workspace").is_dir() or not (work / "input" / "request.md").is_file():
        raise WorkflowError("work-intake requires an existing Work with input/request.md and workspace/")
    metadata = _work_metadata(work)
    expected_jobs_root = _work_studio_jobs_root(work)
    if jobs_root().resolve() != expected_jobs_root:
        raise WorkflowError(
            "work-intake must run through this Work's workspace/aippt.sh or workspace/aippt.ps1 so Studio state stays in workspace/studio-jobs"
        )
    identifier = job_id or f"work-{metadata['work_id']}"
    try:
        job = load_job(identifier)
    except FileNotFoundError:
        job = create_job(
            name or metadata["name"],
            client if client is not None else metadata["client"],
            identifier,
            bind_current_work=False,
        )
    if job.work_request is not None and job.work_request.work_id != metadata["work_id"]:
        raise WorkflowError("work-intake refuses to bind one Studio job to a different Work")
    if job.work_request is None and job.source_ids:
        raise WorkflowError("work-intake refuses to attach a populated legacy job without Work provenance")
    if job.state not in {JobState.INTAKE, JobState.BLOCKED} or (job.state == JobState.BLOCKED and job.resume_state != JobState.INTAKE):
        raise WorkflowError("work-intake can only import new files while the Studio job is in intake")
    root = job_dir(job.id)
    bind_work_request(job.id, work)
    job = load_job(job.id)
    records = [
        read_model(root / "normalized" / "sources" / f"{source_id}.json", SourceRecord)
        for source_id in job.source_ids
        if (root / "normalized" / "sources" / f"{source_id}.json").is_file()
    ]
    known_paths = {
        str(Path(record.original).expanduser().resolve())
        for record in records
        if not record.original.startswith(("http://", "https://", "chat:"))
    }
    all_source_paths = {str(item.resolve()) for item in _work_input_files(work / "input" / "source")}
    all_reference_paths = {str(item.resolve()) for item in _work_input_files(work / "input" / "reference")}
    source_files = [Path(item) for item in sorted(all_source_paths) if item not in known_paths]
    reference_files = [Path(item) for item in sorted(all_reference_paths) if item not in known_paths]
    from .ingest import ingest_sources
    added = ingest_sources(job.id, [str(item) for item in [*source_files, *reference_files]]) if (source_files or reference_files) else []
    job = load_job(job.id)
    records = [
        read_model(root / "normalized" / "sources" / f"{source_id}.json", SourceRecord)
        for source_id in job.source_ids
        if (root / "normalized" / "sources" / f"{source_id}.json").is_file()
    ]
    reference_ids = {
        record.id: InputRole.VISUAL_REFERENCE
        for record in records
        if _record_origin(record) in all_reference_paths
        and job.input_roles.get(record.id) not in {InputRole.TEMPLATE, InputRole.EDIT_TARGET}
    }
    request_text, _ = read_bound_work_request(job.id)
    inferred_roles, inference = _infer_work_presentation_roles(
        job,
        records,
        source_paths=all_source_paths,
        reference_paths=all_reference_paths,
        request_text=request_text,
    )
    if requests_template_adaptation(request_text) and not requests_bounded_revision(request_text):
        from .template_library import cataloged_template_by_sha256
        records_by_id = {record.id: record for record in records}
        unconfirmed_catalog_templates = [
            source_id for source_id, role in inferred_roles.items()
            if role == InputRole.TEMPLATE
            and cataloged_template_by_sha256(
                WORKSPACE_ROOT / "模板库" / "CATALOG.jsonl", records_by_id[source_id].sha256,
            ) is not None
        ]
        if len(unconfirmed_catalog_templates) == 1:
            source_id = unconfirmed_catalog_templates[0]
            inferred_roles[source_id] = InputRole.VISUAL_REFERENCE
            inference = {
                "status": "library_template_confirmation_required",
                "source_ids": [source_id],
                "reason": "catalog template remains a reference until the user confirms the displayed candidate and adaptation scope",
            }
    role_updates = {**reference_ids, **inferred_roles}
    if role_updates:
        configure_route(job.id, roles=role_updates)
    job = load_job(job.id)
    if job.source_ids:
        create_brief(job.id)
        job = load_job(job.id)
        # When no role update occurred, the job is still technically in
        # intake even though work-intake just created the first reviewable
        # brief.  Advance it here so a caller never receives an impossible
        # ``intake + brief.json`` combination and the brief gate is usable.
        if job.state in {JobState.INTAKE, JobState.BLOCKED}:
            job.state = JobState.BRIEF_REVIEW
            job.blocked_reason = None
            job.resume_state = None
            job.failure = None
            save_job(job)
    payload = status_payload(load_job(job.id))
    payload["work_intake"] = {
        "work_id": metadata["work_id"],
        "imported_source_count": len(source_files),
        "imported_reference_count": len(reference_files),
        "request_bound": True,
        "role_inference": inference,
        "rule": "Work request is an instruction channel, not visible presentation content.",
    }
    if job.source_ids and requests_template_adaptation(request_text) and not requests_bounded_revision(request_text):
        brief = read_model(root / "planning" / "brief.json", JobBrief)
        selection = _template_selection_candidates(brief)
        selection["brief_sha256"] = sha256(root / "planning" / "brief.json")
        selection["work_request_sha256"] = hashlib.sha256(request_text.encode("utf-8")).hexdigest()
        write_json(root / "planning" / "template-selection-candidates.json", selection)
        payload["work_intake"]["template_selection"] = selection
    return payload


def bind_catalog_template(job_id: str, relative_path: str) -> dict[str, object]:
    """Turn a reviewed library candidate into this Work's executable template."""

    from .template_contract import requested_presentation_form
    from .template_library import cataloged_template_by_path, occasion_compatible
    from .ingest import ingest_sources

    job = load_job(job_id)
    root = job_dir(job_id)
    active_state = job.resume_state if job.state == JobState.BLOCKED else job.state
    if job.work_request is None or active_state not in {
        JobState.INTAKE, JobState.BRIEF_REVIEW, JobState.PROTOTYPE_REVIEW,
    }:
        raise WorkflowError("library template selection requires a bound Work before its prototype is approved")
    try:
        request_text, _binding = read_bound_work_request(job.id)
    except ValueError as error:
        raise WorkflowError(str(error)) from error
    if requests_bounded_revision(request_text):
        raise WorkflowError("a bounded revision cannot replace its source with a whole library template")
    explicit_template_request = requests_template_adaptation(request_text)
    work_value = os.environ.get("AIPPT_WORK_DIR", "")
    if not work_value:
        raise WorkflowError("select a library template through this Work's wrapper")
    work = Path(work_value).resolve()
    if (canonical_work_id(work) != job.work_request.work_id
            or jobs_root().resolve() != _work_studio_jobs_root(work)):
        raise WorkflowError("the active Work wrapper does not match this job")
    catalog = WORKSPACE_ROOT / "模板库" / "CATALOG.jsonl"
    row = cataloged_template_by_path(catalog, relative_path)
    if row is None:
        raise WorkflowError("selected library PPTX is not a current hash-verified template catalog entry")
    records = {
        source_id: read_model(root / "normalized" / "sources" / f"{source_id}.json", SourceRecord)
        for source_id in job.source_ids
    }
    brief = read_model(root / "planning" / "brief.json", JobBrief)
    if not explicit_template_request:
        if brief.presentation_intent.change_mode != ChangeMode.NEW_COMPOSITION:
            raise WorkflowError("voluntary library template selection requires a new composition")
        if "request:new_visual_system" in brief.presentation_intent.evidence:
            raise WorkflowError("the current request requires a new visual system instead of an inherited template")
        plan_path = root / "planning" / "deck-plan.json"
        candidates_path = root / "planning" / "library-reuse-candidates.json"
        if not plan_path.is_file() or not candidates_path.is_file():
            raise WorkflowError("review the current page-level library candidates before selecting a whole template")
        from .library_reuse_gate import plan_message_signature
        plan = read_model(plan_path, DeckPlan)
        packet = read_json(candidates_path)
        if (not isinstance(packet, dict)
                or packet.get("plan_message_signature") != plan_message_signature(plan.slides)
                or not any(isinstance(candidate, dict) and candidate.get("path") == row["path"]
                           and candidate.get("source_sha256") == row["source_sha256"]
                           for candidate in packet.get("candidates", []))):
            raise WorkflowError("selected whole template is not a current source-bound page-level candidate")
    required_form = requested_presentation_form(request_text, brief, job, records, root)
    if not occasion_compatible(row, required_form.value):
        raise WorkflowError(
            f"selected library template {relative_path} does not match {required_form.value}; "
            "choose a template from the required occasion"
        )
    try:
        from .template_selection import require_current_template_confirmation
        require_current_template_confirmation(
            job_id=job.id, root=root, request_text=request_text, catalog_path=catalog,
            library_root=WORKSPACE_ROOT / "模板库", relative_path=row["path"],
            source_sha256=row["source_sha256"],
        )
    except (OSError, ValueError) as error:
        raise WorkflowError(str(error)) from error
    expected_hash = row["source_sha256"]
    selected_id = next(
        (source_id for source_id, record in records.items()
         if record.kind == "powerpoint" and record.sha256 == expected_hash), None,
    )
    if selected_id is None:
        source = WORKSPACE_ROOT / "模板库" / row["path"]
        destination = work / "input" / "reference" / f"{source.stem}-{expected_hash[:12]}.pptx"
        copied = not destination.exists()
        if destination.exists():
            if not destination.is_file() or sha256(destination) != expected_hash:
                raise WorkflowError("Work reference filename exists with different bytes")
        else:
            shutil.copy2(source, destination)
        prior_state = job.state
        prior_resume_state = job.resume_state
        if prior_state != JobState.INTAKE:
            job.state = JobState.INTAKE
            job.resume_state = None
            save_job(job)
        try:
            added = ingest_sources(job.id, [str(destination)])
        except Exception:
            if prior_state != JobState.INTAKE:
                restored = load_job(job.id)
                restored.state = prior_state
                restored.resume_state = prior_resume_state
                save_job(restored)
            if copied:
                destination.unlink(missing_ok=True)
            raise
        selected_id = added[0].id
        job = load_job(job.id)
    roles = {
        source_id: InputRole.VISUAL_REFERENCE
        for source_id, role in job.input_roles.items()
        if role == InputRole.TEMPLATE and source_id != selected_id
    }
    roles[selected_id] = InputRole.TEMPLATE
    configured = configure_route(job.id, route=ProductionRoute.TEMPLATE,
                                 reason="selected hash-verified occasion-compatible library template",
                                 roles=roles)
    current_brief = read_model(root / "planning" / "brief.json", JobBrief)
    if reason := _template_requirement_reason(configured, root, current_brief):
        raise WorkflowError(reason)
    return {
        "job_id": configured.id,
        "status": "template_bound_for_application",
        "library_path": relative_path,
        "source_sha256": expected_hash,
        "source_id": selected_id,
        "route": configured.route.value,
        "review_status": row.get("review_status"),
        "rule": "Inspect actual template pages and create a page/shape map; binding alone does not prove output application.",
    }


@locked_job
def prepare_catalog_template_selection(job_id: str, relative_path: str) -> dict[str, object]:
    """Prepare source-bound confirmation metadata for a catalog candidate."""
    from .template_selection import prepare_template_selection

    job = load_job(job_id)
    root = job_dir(job_id)
    if job.work_request is None:
        raise WorkflowError("template candidate review requires a bound Work request")
    try:
        request_text, _binding = read_bound_work_request(job_id)
    except ValueError as error:
        raise WorkflowError(str(error)) from error
    try:
        packet = prepare_template_selection(
            library_root=WORKSPACE_ROOT / "模板库",
            catalog_path=WORKSPACE_ROOT / "模板库" / "CATALOG.jsonl",
            relative_path=relative_path,
        )
        from .template_selection import _candidate_binding
        candidate_packet, candidate_kind = _candidate_binding(
            root=root, relative_path=relative_path, source_sha256=str(packet["source_sha256"]),
            request_text=request_text,
        )
    except (OSError, ValueError) as error:
        raise WorkflowError(str(error)) from error
    packet["job_id"] = job.id
    packet["work_request_sha256"] = hashlib.sha256(request_text.encode("utf-8")).hexdigest()
    packet["candidate_packet_path"] = candidate_packet.relative_to(root).as_posix()
    packet["candidate_packet_sha256"] = sha256(candidate_packet)
    packet["candidate_packet_kind"] = candidate_kind
    packet["reviewer_instruction"] = "展示并实际检查所列来源页预览；此元数据包自身不是视觉审阅或用户确认。"
    return packet


@locked_job
def confirm_catalog_template(job_id: str, request: dict[str, object]) -> dict[str, object]:
    """Record the trusted CLI caller's human-confirmation evidence."""
    from .template_selection import record_template_confirmation
    from .template_library import cataloged_template_by_path

    job = load_job(job_id)
    root = job_dir(job_id)
    if job.work_request is None:
        raise WorkflowError("template confirmation requires a bound Work request")
    try:
        request_text, _binding = read_bound_work_request(job_id)
    except ValueError as error:
        raise WorkflowError(str(error)) from error
    work_value = os.environ.get("AIPPT_WORK_DIR", "")
    if not work_value:
        raise WorkflowError("confirm template selection through this Work's wrapper")
    work = Path(work_value).resolve()
    if canonical_work_id(work) != job.work_request.work_id or jobs_root().resolve() != _work_studio_jobs_root(work):
        raise WorkflowError("the active Work wrapper does not match this job")
    selected_path = request.get("selected_catalog_path")
    if not isinstance(selected_path, str) or not selected_path.strip():
        raise WorkflowError("selected_catalog_path is required")
    catalog = WORKSPACE_ROOT / "模板库" / "CATALOG.jsonl"
    row = cataloged_template_by_path(catalog, selected_path)
    if row is None:
        raise WorkflowError("selected library PPTX is not a current hash-verified template catalog entry")
    payload = dict(request)
    # A request that names this exact catalog template is already the human's
    # selection text; previews and the adaptation scope are still mandatory.
    confirmation = payload.get("user_confirmation_text")
    if (not isinstance(confirmation, str) or not confirmation.strip()) and (
        row["path"] in request_text or Path(row["path"]).stem in request_text
    ):
        payload["user_confirmation_text"] = request_text
    try:
        result = record_template_confirmation(
            job_id=job.id, root=root, work=work, request_text=request_text,
            catalog_path=catalog, library_root=WORKSPACE_ROOT / "模板库", payload=payload,
        )
    except (OSError, ValueError) as error:
        raise WorkflowError(str(error)) from error
    return result


def _archive_intent_snapshot(root: Path, brief: JobBrief) -> None:
    """Keep superseded intent evidence without retaining a live stale plan."""

    history = root / "planning" / "intake-history"
    history.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(
        json.dumps(brief.presentation_intent.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    target = history / f"{token}.json"
    if not target.is_file():
        write_json(target, {
            "schema_version": 1,
            "superseded_at": utc_now(),
            "presentation_intent": brief.presentation_intent.model_dump(mode="json"),
            "brief_sha256": sha256(root / "planning" / "brief.json") if (root / "planning" / "brief.json").is_file() else None,
        })


def _supersede_review_state(root: Path, reason: str) -> None:
    """Preserve, but detach, feedback tied to an invalidated deck plan."""

    comments_path = root / "reviews" / "comments.json"
    comments = [
        ReviewComment.model_validate(item)
        for item in read_json(comments_path)
    ] if comments_path.is_file() else []
    superseded_comments: list[str] = []
    for comment in comments:
        if comment.status not in {"resolved", "superseded"}:
            comment.status = "superseded"
            comment.superseded_at = utc_now()
            comment.superseded_reason = reason
            superseded_comments.append(comment.id)
    if superseded_comments:
        write_json(comments_path, [comment.model_dump(mode="json") for comment in comments])

    superseded_batches: list[str] = []
    for path in sorted((root / "revisions").glob("revision-*.json")):
        batch = read_model(path, RevisionBatch)
        if batch.status in {"rebuilt", "superseded"}:
            continue
        batch.status = "superseded"
        batch.superseded_at = utc_now()
        batch.superseded_reason = reason
        write_json(path, batch)
        superseded_batches.append(batch.id)
    if superseded_comments or superseded_batches:
        receipt = {
            "schema_version": 1,
            "superseded_at": utc_now(),
            "reason": reason,
            "comment_ids": superseded_comments,
            "revision_batch_ids": superseded_batches,
            "rule": "Comments and revision batches remain historical evidence but cannot block approval of a newly classified plan.",
        }
        token = hashlib.sha256(json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        write_json(root / "reviews" / "intent-invalidations" / f"{token}.json", receipt)


def _invalidate_intent_dependents(job: JobRecord, root: Path) -> None:
    """Remove only derived plans/builds when an approved intent changes."""

    _supersede_review_state(root, "presentation intent or bound Work request changed")
    supersede_delivery_artifacts(
        root,
        "presentation intent or bound Work request changed",
        receipt_directory="reviews/intent-invalidations",
    )

    for relative in (
        "planning/deck-plan.json",
        "planning/asset-plan.json",
        "planning/native-scene.json",
        "planning/project-lessons.json",
        "planning/upstream-context.json",
        "build/deck.pptx",
        "build/deck.html",
        "build/montage.png",
        "build/prototype.pptx",
        "build/prototype.html",
        "build/prototype-montage.png",
        "build/external-engine-manifest.json",
        "build/adapter-last-result.json",
        "build/native-adapter-result.json",
    ):
        (root / relative).unlink(missing_ok=True)
    for relative in (
        "build/previews",
        "build/slides",
        "build/prototype-previews",
        "build/animated-previews",
        "build/adapter-source",
        "build/adapter-staging",
    ):
        directory = root / relative
        if directory.is_dir():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    job.approvals.update({"brief": False, "prototype": False, "final": False})
    job.approval_hashes.clear()
    job.dirty_slides = []
    job.blocked_reason = None
    job.resume_state = None
    job.failure = None
    job.state = JobState.BRIEF_REVIEW if job.source_ids else JobState.INTAKE


@locked_job
def configure_presentation_intent(
    job_id: str,
    *,
    change_mode: ChangeMode | None = None,
    presentation_form: PresentationForm | None = None,
    visual_system: VisualSystemMode | None = None,
    reason: str = "",
    reset_automatic: bool = False,
) -> JobBrief:
    """Override or recompute a reviewed task-intent decision.

    This is intentionally parallel to ``configure_route``: changing a
    presentation's editorial mode must invalidate affected plans and previews,
    while the technical route remains independent.
    """

    job = load_job(job_id)
    if job.state == JobState.DELIVERED:
        raise WorkflowError("create a new revision job instead of rewriting a delivered job's intent")
    root = job_dir(job_id)
    brief_path = root / "planning" / "brief.json"
    if not brief_path.is_file():
        if not job.source_ids:
            raise WorkflowError("bind or ingest source material before configuring presentation intent")
        create_brief(job_id)
    brief = read_model(brief_path, JobBrief)
    if change_mode is not None and change_mode != ChangeMode.TEMPLATE_RELAYOUT and job.work_request is not None:
        request_text, _binding = read_bound_work_request(job.id)
        if requests_template_adaptation(request_text) and not (
            change_mode == ChangeMode.LOCAL_PATCH and requests_bounded_revision(request_text)
        ):
            raise WorkflowError("本轮用户要求套用模板；请更新 Work 请求后再改为新建表达或其他方式。")
    if presentation_form is not None and job.work_request is not None:
        request_text, _binding = read_bound_work_request(job.id)
        if requests_template_adaptation(request_text):
            from .template_contract import requested_presentation_form
            sources = {
                source_id: read_model(root / "normalized" / "sources" / f"{source_id}.json", SourceRecord)
                for source_id in job.source_ids
            }
            required_form = requested_presentation_form(request_text, brief, job, sources, root)
            if required_form != PresentationForm.GENERAL and presentation_form != required_form:
                raise WorkflowError(f"本轮来源与要求指向 {required_form.value}；不能覆盖为 {presentation_form.value}。")
    _archive_intent_snapshot(root, brief)
    if reset_automatic:
        if any(item is not None for item in (change_mode, presentation_form, visual_system)):
            raise WorkflowError("--reset-automatic cannot be combined with explicit presentation-intent values")
        brief.presentation_intent = PresentationIntent(
            source="automatic",
            change_mode_source="automatic",
            presentation_form_source="automatic",
            visual_system_source="automatic",
            confidence="unresolved",
            reason="Automatic classification was explicitly refreshed.",
        )
        write_json(brief_path, brief)
        brief = create_brief(job_id)
    else:
        brief.presentation_intent = apply_intent_overrides(
            brief.presentation_intent,
            change_mode=change_mode,
            presentation_form=presentation_form,
            visual_system=visual_system,
            reason=reason,
        )
        write_json(brief_path, brief)
        write_json(root / "planning" / "intake-decision.json", {
            "schema_version": 1,
            "brief_path": "planning/brief.json",
            "work_request": job.work_request.model_dump(mode="json") if job.work_request else None,
            "presentation_intent": brief.presentation_intent.model_dump(mode="json"),
            "rule": "Explicit overrides affect only the chosen intent dimensions; unmodified dimensions remain automatic.",
        })
    _invalidate_intent_dependents(job, root)
    save_job(job)
    return brief


@locked_job
def configure_route(
    job_id: str,
    *,
    route: ProductionRoute | None = None,
    reason: str = "",
    roles: dict[str, InputRole] | None = None,
    image_based_accepted: bool = False,
) -> JobRecord:
    job = load_job(job_id)
    if job.state == JobState.DELIVERED:
        raise WorkflowError("create a new revision job instead of changing the route of a delivered job")
    if route is not None and route != ProductionRoute.TEMPLATE and job.work_request is not None:
        request_text, _binding = read_bound_work_request(job.id)
        if requests_template_adaptation(request_text) and not requests_bounded_revision(request_text):
            raise WorkflowError("本轮用户要求套用模板；不能覆盖为非 template 路线。")
    root = job_dir(job_id)
    previous_route = job.route
    previous_roles = dict(job.input_roles)
    if roles:
        unknown = sorted(set(roles) - set(job.source_ids))
        if unknown:
            raise WorkflowError(f"input-role source ids are not part of this job: {', '.join(unknown)}")
        job.input_roles.update(roles)
    records = [
        read_model(root / "normalized" / "sources" / f"{source_id}.json", SourceRecord)
        for source_id in job.source_ids
    ]
    record_by_id = {record.id: record for record in records}
    designated = [
        source_id
        for source_id, source_role in job.input_roles.items()
        if source_role in {InputRole.TEMPLATE, InputRole.EDIT_TARGET}
    ]
    if len(designated) > 1:
        raise WorkflowError(
            "template/edit target must identify exactly one editable PowerPoint; "
            "do not let the adapter choose among multiple candidates"
        )
    invalid_designated = [
        source_id for source_id in designated
        if source_id not in record_by_id or record_by_id[source_id].kind != "powerpoint"
    ]
    if invalid_designated:
        raise WorkflowError(
            "template/edit target roles require a PowerPoint source: " + ", ".join(sorted(invalid_designated))
        )
    inferred = classify_input_roles(records)
    inferred.update(job.input_roles)
    policy = RoutePolicy(override=route, override_reason=reason, image_based_accepted=image_based_accepted)
    decision = select_route(inferred, policy)
    catalog_template_row = None
    selection_request_text = None
    selection_receipt = None
    if decision.route == ProductionRoute.TEMPLATE and job.work_request is not None and len(designated) == 1:
        # Resolve by verified current bytes: older catalog rows may inherit the
        # deck hash from DESIGN-STRUCTURE rather than CATALOG.jsonl.
        selected_record = record_by_id[designated[0]]
        catalog = WORKSPACE_ROOT / "模板库" / "CATALOG.jsonl"
        marker_path = root / "planning/template-selection-adoption.json"
        marker = read_json(marker_path) if marker_path.is_file() else None
        marker_source_match = (isinstance(marker, dict)
                               and marker.get("job_id") == job.id
                               and marker.get("source_id") == designated[0]
                               and marker.get("source_sha256") == selected_record.sha256)
        work_source = (root / selected_record.stored_path).resolve()
        if (root.resolve() not in work_source.parents or not work_source.is_file()
                or sha256(work_source) != selected_record.sha256):
            raise WorkflowError("selected template source file is missing or changed")
        if catalog.is_file():
            from .template_library import cataloged_template_by_sha256
            catalog_template_row = cataloged_template_by_sha256(catalog, selected_record.sha256)
            if marker_source_match and catalog_template_row is None:
                raise WorkflowError("adopted catalog template is missing or changed in the current library")
            if catalog_template_row is not None:
                try:
                    selection_request_text, _binding = read_bound_work_request(job.id)
                except ValueError as error:
                    raise WorkflowError(str(error)) from error
                from .template_selection import require_current_template_confirmation
                receipt_path = root / "planning/template-selection-confirmation.json"
                receipt_hash = sha256(receipt_path) if receipt_path.is_file() else None
                try:
                    current_receipt = read_json(receipt_path) if receipt_path.is_file() else None
                except (OSError, ValueError):
                    current_receipt = None
                adopted = (isinstance(marker, dict)
                           and marker.get("job_id") == job.id
                           and marker.get("source_id") == designated[0]
                           and marker.get("source_sha256") == selected_record.sha256
                           and marker.get("work_request_sha256")
                               == hashlib.sha256(selection_request_text.encode("utf-8")).hexdigest()
                           and marker.get("catalog_path") == catalog_template_row["path"]
                           and marker.get("confirmation_sha256") == receipt_hash
                           and isinstance(current_receipt, dict)
                           and marker.get("confirmation_scope_sha256")
                               == current_receipt.get("adaptation_scope_sha256"))
                try:
                    selection_receipt = require_current_template_confirmation(
                        job_id=job.id, root=root, request_text=selection_request_text,
                        catalog_path=catalog, library_root=WORKSPACE_ROOT / "模板库",
                        relative_path=str(catalog_template_row["path"]),
                        source_sha256=selected_record.sha256, adopted=adopted,
                    )
                except (OSError, ValueError) as error:
                    raise WorkflowError(str(error)) from error
        elif marker_source_match:
            raise WorkflowError("adopted template catalog is missing")
    if image_based_accepted and route is None and decision.route == ProductionRoute.VISUAL:
        decision.source = "user_override"
        decision.reason = "user explicitly accepted an image-based visual alternate"
    changed = decision.route != previous_route or decision.input_roles != previous_roles
    job.route = decision.route
    job.route_source = decision.source
    job.route_reason = decision.reason
    job.input_roles = decision.input_roles
    if changed:
        brief_path = root / "planning" / "brief.json"
        if brief_path.is_file():
            _archive_intent_snapshot(root, read_model(brief_path, JobBrief))
        # Route and input-role changes can alter both the technical contract and
        # automatic intent.  Treat them as a full brief invalidation so a prior
        # visual approval cannot survive a different source role or renderer.
        _invalidate_intent_dependents(job, root)
    save_job(job)
    if (decision.route == ProductionRoute.TEMPLATE and catalog_template_row is not None
            and selection_receipt is not None and selection_request_text is not None):
        marker = {
            "schema": "aippt.template-selection-adoption.v1",
            "status": "adopted_for_current_workflow",
            "job_id": job.id,
            "source_id": designated[0],
            "source_sha256": record_by_id[designated[0]].sha256,
            "catalog_path": catalog_template_row["path"],
            "work_request_sha256": hashlib.sha256(selection_request_text.encode("utf-8")).hexdigest(),
            "confirmation_scope_sha256": selection_receipt["adaptation_scope_sha256"],
            "confirmation_sha256": sha256(root / "planning/template-selection-confirmation.json"),
            "before_adoption_hashes": {
                name: selection_receipt.get(key)
                for name, key in (("brief.json", "brief_sha256"),
                                  ("deck-plan.json", "deck_plan_sha256"),
                                  ("library-reuse-candidates.json", "candidate_packet_sha256"))
                if selection_receipt.get(key)
            },
            "note": "Before hashes are audit evidence only; subsequent same-source route configuration checks current source/request/previews/scope.",
        }
        write_json(root / "planning/template-selection-adoption.json", marker)
    # A route/input-role change can change an automatic intent (for example,
    # a new edit target makes a bounded request a local patch).  Recompute it
    # only when a brief already exists; explicit dimensions remain preserved.
    # A manual route/role decision is also allowed before the first brief was
    # written.  In that case invalidation places the job at brief_review, so
    # immediately create the first current brief instead of leaving a state
    # which has no corresponding artifact to review.
    if changed and job.source_ids:
        create_brief(job.id)
    return load_job(job.id)


def _block(job: JobRecord, reason: str, resume_state: JobState | None = None) -> dict[str, object]:
    job.resume_state = resume_state or job.state
    job.state = JobState.BLOCKED
    job.blocked_reason = reason
    job.failure = None
    save_job(job)
    return status_payload(job)


def _fail(job: JobRecord, error: Exception) -> dict[str, object]:
    job.state = JobState.FAILED
    job.failure = str(error)
    job.blocked_reason = None
    job.resume_state = None
    save_job(job)
    return status_payload(job)


def status_payload(job: JobRecord) -> dict[str, object]:
    root = job_dir(job.id)
    brief_path = root / "planning" / "brief.json"
    brief = None
    brief_error: str | None = None
    if brief_path.is_file():
        # Status reporting is used precisely after reconcile detects a damaged
        # artifact.  Do not turn that recoverable diagnostic into a second
        # unhandled validation exception which hides the original state.
        try:
            brief = read_model(brief_path, JobBrief)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            brief_error = f"brief cannot be read: {error}"
    final_delivery = root / "deliverables" / "final_editable.pptx"
    candidate_delivery = root / "deliverables" / "candidate_editable.pptx"
    visual_delivery = root / "deliverables" / "visual_alternate.pptx"
    candidate_visual_delivery = root / "deliverables" / "candidate_visual_alternate.pptx"
    delivery = next(
        (path for path in (final_delivery, candidate_delivery, visual_delivery, candidate_visual_delivery) if path.is_file()),
        final_delivery,
    )
    pending_assets = pending_required_assets(root)
    from .design_plan import design_plan_status
    try:
        authoring_status = design_plan_status(root)
    except (OSError, ValueError, TypeError) as error:
        authoring_status = {"status": "unavailable", "issues": [str(error)]}
    return {
        **job.model_dump(mode="json"),
        "root": str(root),
        "authoring_status": authoring_status,
        "route_decision": {
            "route": job.route.value,
            "source": job.route_source,
            "reason": job.route_reason,
            "input_roles": {source_id: role.value for source_id, role in job.input_roles.items()},
        },
        "presentation_intent": brief.presentation_intent.model_dump(mode="json") if brief else None,
        # The raw model is retained for APIs and evidence.  The companion is a
        # reviewer-facing statement of what this task is, so people never have
        # to interpret renderer names or filesystem paths as a proxy for the
        # intended presentation.
        "editorial_decision": presentation_intent_summary(brief.presentation_intent) if brief else None,
        "brief_diagnostic": brief_error,
        "work_request": job.work_request.model_dump(mode="json") if job.work_request else None,
        "asset_status": {
            "pending_required": len(pending_assets),
            "pending_need_ids": [need.id for need in pending_assets],
        },
        "artifacts": {
            "brief": str(brief_path) if brief else None,
            "intake_decision": str(root / "planning" / "intake-decision.json") if (root / "planning" / "intake-decision.json").is_file() else None,
            "deck_plan": str(root / "planning" / "deck-plan.json") if (root / "planning" / "deck-plan.json").is_file() else None,
            "design_context": str(root / "planning/design-context.json") if (root / "planning/design-context.json").is_file() else None,
            "source_inspection": str(root / "extraction" / "index.json") if (root / "extraction" / "index.json").is_file() else None,
            "edits": str(root / "edits") if (root / "edits").is_dir() and any((root / "edits").glob("*.json")) else None,
            "prototype": str(root / "build" / "prototype.pptx") if (root / "build" / "prototype.pptx").is_file() else None,
            "deck": str(root / "build" / "deck.pptx") if (root / "build" / "deck.pptx").is_file() else None,
            "native_powerpoint": str(root / "edits" / "native-powerpoint.json") if (root / "edits" / "native-powerpoint.json").is_file() else None,
            "animated_previews": str(root / "build" / "animated-previews") if (root / "build" / "animated-previews").is_dir() else None,
            "delivery": str(delivery) if delivery.is_file() else None,
            "candidate_delivery": str(candidate_delivery) if candidate_delivery.is_file() else None,
            "final_delivery": str(final_delivery) if final_delivery.is_file() else None,
            "visual_alternate": str(visual_delivery) if visual_delivery.is_file() else None,
            "candidate_visual_alternate": str(candidate_visual_delivery) if candidate_visual_delivery.is_file() else None,
            "adapter_result": str(root / "build" / "adapter-last-result.json") if (root / "build" / "adapter-last-result.json").is_file() else None,
            "native_adapter_result": str(root / "build" / "native-adapter-result.json") if (root / "build" / "native-adapter-result.json").is_file() else None,
            "asset_plan": str(root / "planning" / "asset-plan.json") if (root / "planning" / "asset-plan.json").is_file() else None,
            "asset_ledger": str(root / "assets" / "asset-ledger.json") if (root / "assets" / "asset-ledger.json").is_file() else None,
        },
    }


@locked_job
def run_job(job_id: str) -> dict[str, object]:
    job = load_job(job_id)
    root = job_dir(job_id)
    if job.state in {JobState.DELIVERED, JobState.FAILED}:
        return status_payload(job)
    try:
        _, request_changed = bind_work_request(job.id)
    except WorkBindingEnvironmentError as error:
        # A stale wrapper must never change the state of a correctly bound
        # job.  Report this as a caller/setup error, leaving the job bytes and
        # its review state untouched for the proper Work wrapper.
        raise WorkflowError(f"Studio Work wrapper is inconsistent: {error}") from error
    except PresentationIntentError as error:
        return _block(job, f"Work 要求绑定失败：{error}", job.state)
    # ``bind_work_request`` persists a new provenance binding.  Reload before
    # invalidating derived artifacts so the old in-memory record cannot write
    # the stale snapshot back over the correction.
    job = load_job(job.id)
    if request_changed and job.state != JobState.INTAKE:
        brief_path = root / "planning" / "brief.json"
        if brief_path.is_file():
            _archive_intent_snapshot(root, read_model(brief_path, JobBrief))
        _invalidate_intent_dependents(job, root)
        save_job(job)
        if job.source_ids:
            create_brief(job.id)
        return status_payload(load_job(job.id))
    job = load_job(job.id)
    if job.state == JobState.BLOCKED:
        return status_payload(job)

    consistency = audit_job(job_id)
    if consistency.status == "inconsistent":
        issue_codes = ", ".join(issue.code for issue in consistency.issues[:5])
        return _block(job, f"任务状态与产物不一致：{issue_codes}", job.state)

    _refresh_automatic_route(job, root)

    if job.state in {JobState.PROTOTYPE_REVIEW, JobState.FULL_BUILD, JobState.FINAL_REVIEW}:
        brief = read_model(root / "planning" / "brief.json", JobBrief)
        if reason := _template_requirement_reason(job, root, brief):
            job.approvals.update({"brief": False, "prototype": False, "final": False})
            job.approval_hashes.clear()
            return _block(job, reason, JobState.BRIEF_REVIEW)

    ready, reason = route_ready(job.route)
    if not ready:
        return _block(job, f"生产路由 {job.route.value} 不可用：{reason}")

    try:
        if job.state == JobState.INTAKE:
            if not job.source_ids:
                return _block(job, "尚未导入任何客户材料", JobState.INTAKE)
            create_brief(job.id)
            job.state = JobState.BRIEF_REVIEW
            save_job(job)
            return status_payload(job)

        if job.state == JobState.BRIEF_REVIEW:
            return status_payload(job)

        if job.state == JobState.PROTOTYPE_REVIEW:
            if job.route == ProductionRoute.TEMPLATE:
                return _block(
                    job,
                    "template 路由不能用通用 HTML 样稿代替真实模板预览；请重新审批当前 brief 后由模板适配器直接构建。",
                    JobState.BRIEF_REVIEW,
                )
            brief = read_model(root / "planning" / "brief.json", JobBrief)
            if reason := _generic_renderer_block_reason(brief):
                return _block(job, reason, JobState.BRIEF_REVIEW)
            if not _approval_is_current(job, root, "brief"):
                job.approvals.update({"brief": False, "prototype": False, "final": False})
                job.approval_hashes.clear()
                job.state = JobState.BRIEF_REVIEW
                save_job(job)
                return status_payload(job)
            if not (root / "planning" / "deck-plan.json").is_file():
                create_deck_plan(job.id)
            plan = _read_deck_plan(root)
            from .design_plan import design_plan_block_reason
            if reason := design_plan_block_reason(root, plan):
                return _block(job, reason, JobState.PROTOTYPE_REVIEW)
            from .library_reuse_gate import library_reuse_block_reason
            if reason := library_reuse_block_reason(job, root, plan):
                return _block(job, reason, JobState.PROTOTYPE_REVIEW)
            from .models import select_prototype_slides
            prototype_slides = select_prototype_slides(plan)
            expected_ids = {slide.id for slide in prototype_slides}
            prototype_pptx = root / "build" / "prototype.pptx"
            preview_dir = root / "build" / "prototype-previews"
            montage = root / "build" / "prototype-montage.png"
            actual_ids = {path.stem for path in preview_dir.glob("*.png")}
            complete = prototype_pptx.is_file() and montage.is_file() and actual_ids == expected_ids
            if not complete:
                html_path = write_deck_html(job.id, prototype=True)
                build_root = (root / "build").resolve()
                for stale in build_root.glob("prototype-*"):
                    if stale.is_dir() and stale.resolve().parent == build_root:
                        shutil.rmtree(stale, ignore_errors=True)
                staging = root / "build" / f"prototype-{uuid.uuid4().hex[:8]}"
                staged_pptx = staging / "prototype.pptx"
                staged_previews = staging / "previews"
                staged_montage = staging / "prototype-montage.png"
                try:
                    if job.route == ProductionRoute.NATIVE and (root / "planning/native-scene.json").is_file():
                        from .native_scene import render_scene
                        render_scene(root, staged_pptx, staged_previews, prototype=True)
                    else:
                        render_html(html_path, staged_pptx, staged_previews)
                    officecli_validate(staged_pptx)
                    apply_motion_plan(
                        job.id,
                        staged_pptx,
                        prototype_slides,
                        batch_id="motion-prototype",
                        reported_path="build/prototype.pptx",
                    )
                    from .authoring_policy import check_job_presentation
                    check_job_presentation(root, staged_pptx, "prototype-build",
                                           enforce=bool(job.authoring_policy_id) and job.route == ProductionRoute.NATIVE)
                    staged_images = natural_sort_paths(list(staged_previews.glob("*.png")))
                    if {path.stem for path in staged_images} != expected_ids:
                        raise RenderError("prototype renderer did not return the expected slide previews")
                    create_montage(staged_images, staged_montage)
                    prototype_pptx.unlink(missing_ok=True)
                    _replace_with_retry(staged_pptx, prototype_pptx)
                    if preview_dir.exists():
                        shutil.rmtree(preview_dir)
                    staged_previews.replace(preview_dir)
                    montage.unlink(missing_ok=True)
                    staged_montage.replace(montage)
                finally:
                    if staging.exists():
                        shutil.rmtree(staging, ignore_errors=True)
            _resolve_rebuilt_revisions(root, "prototype")
            return status_payload(job)

        if job.state == JobState.FULL_BUILD:
            if job.route == ProductionRoute.NATIVE and (root / "planning/deck-plan.json").is_file():
                from .library_reuse_gate import library_reuse_block_reason
                if reason := library_reuse_block_reason(job, root, _read_deck_plan(root)):
                    return _block(job, reason, JobState.FULL_BUILD)
            pending_assets = pending_required_assets(root)
            if pending_assets:
                return _block(
                    job,
                    "required assets are not acquired: " + ", ".join(need.id for need in pending_assets),
                    JobState.FULL_BUILD,
                )
            if job.route == ProductionRoute.TEMPLATE:
                if not _approval_is_current(job, root, "brief"):
                    job.approvals.update({"brief": False, "prototype": False, "final": False})
                    job.approval_hashes.clear()
                    job.state = JobState.BRIEF_REVIEW
                    save_job(job)
                    return status_payload(job)
            elif not _approval_is_current(job, root, "prototype") and not _has_current_applied_revision(root, "final"):
                job.approvals.update({"prototype": False, "final": False})
                job.approval_hashes.pop("prototype", None)
                job.approval_hashes.pop("final", None)
                for artifact in (
                    root / "build" / "prototype.pptx",
                    root / "build" / "prototype.html",
                    root / "build" / "prototype-montage.png",
                ):
                    artifact.unlink(missing_ok=True)
                job.state = JobState.PROTOTYPE_REVIEW
                save_job(job)
                return status_payload(job)
            brief = read_model(root / "planning" / "brief.json", JobBrief)
            if reason := _generic_renderer_block_reason(brief):
                return _block(
                    job,
                    reason,
                    JobState.FULL_BUILD,
                )
            if job.route == ProductionRoute.TEMPLATE and not (root / "planning" / "deck-plan.json").is_file():
                # Template work intentionally skips the generic HTML prototype:
                # its first render must come from the actual source-bound
                # template adapter.  It still needs a current reviewed plan.
                create_deck_plan(job.id)
            from .design_plan import design_plan_block_reason
            if reason := design_plan_block_reason(root, _read_deck_plan(root)):
                return _block(job, reason, JobState.FULL_BUILD)
            if job.route != ProductionRoute.NATIVE:
                external_plan = _read_deck_plan(root)
                try:
                    external_manifest, external_previews = _validate_external_output(job, root, external_plan)
                except (OSError, ValueError, WorkflowError) as error:
                    external_error = str(error)
                    validation_error = external_error
                    adapter = adapter_registry().get(job.route)
                    adapter_result = adapter.build(_adapter_request(job, root), root)
                    adapter_result = _ensure_adapter_previews(job, root, adapter_result)
                    write_json(root / "build" / "adapter-last-result.json", adapter_result)
                    if adapter_result.status == AdapterStatus.SUCCEEDED:
                        try:
                            _promote_adapter_result(job, root, adapter_result)
                            external_manifest, external_previews = _validate_external_output(job, root, external_plan)
                        except (OSError, ValueError, WorkflowError) as promote_error:
                            external_error = str(promote_error)
                        else:
                            external_error = ""
                    elif adapter_result.failure is not None:
                        external_error = f"{validation_error}; adapter: {adapter_result.failure.message}"
                else:
                    adapter_result = None
                    external_error = ""
                if not external_error:
                    officecli_validate(root / "build" / "deck.pptx")
                    create_montage(external_previews, root / "build" / "montage.png")
                    ledger = read_model(root / "evidence-ledger.json", EvidenceLedger)
                    try:
                        _validate_evidence_traceability(root, external_plan, ledger)
                    except WorkflowError as error:
                        return _block(job, f"证据校验失败：{error}", JobState.FULL_BUILD)
                    _resolve_rebuilt_revisions(root, "final")
                    job.dirty_slides = []
                    job.state = JobState.FINAL_REVIEW
                    job.blocked_reason = None
                    job.resume_state = None
                    save_job(job)
                    return status_payload(job)
                handoff = {
                    "job_id": job.id,
                    "route": job.route.value,
                    "brief": "planning/brief.json",
                    "deck_plan": "planning/deck-plan.json",
                    "sources": [f"normalized/sources/{source_id}.json" for source_id in job.source_ids],
                    "status": "awaiting_external_engine",
                    "execution": "manual_upstream_adapter",
                    "expected_outputs": {
                        "pptx": "build/deck.pptx",
                        "previews": "build/previews/<slide-id>.png",
                        "manifest": "build/external-engine-manifest.json",
                        "build_source": "a relative file path declared by external-engine-manifest.json",
                    },
                    "manifest_schema": {
                        "job_id": job.id,
                        "route": job.route.value,
                        "engine": "<engine-name>",
                        "version": "<engine-version-or-commit>",
                        "editable_class": {
                            "template": "native_editable",
                            "rebuild": "reconstructed_editable",
                            "visual": "image_based",
                        }[job.route.value],
                        "deck_plan_sha256": "<sha256>",
                        "input_hashes": {"<source-id>": "<sha256>"},
                        "pptx_sha256": "<sha256>",
                        "preview_hashes": {"<slide-id>": "<sha256>"},
                        "build_source": "build/<relative-source-file>",
                        "build_source_sha256": "<sha256>",
                        "providers": [{"provider": "<name>", "model": "<model>", "purpose": "<purpose>"}],
                        "warnings": [],
                        "adapter_receipt": {"engine": "<engine-name>", "version": "<version>"},
                    },
                    "resume_command": f"aippt resume {job.id}",
                    "validation_error": external_error,
                }
                write_json(root / "build" / f"{job.route.value}-handoff.json", handoff)
                detail = f"：{external_error}" if external_error else ""
                return _block(
                    job,
                    f"{job.route.value} 路由已生成交接清单，需由对应上游引擎执行{detail}",
                    JobState.FULL_BUILD,
                )
            plan = _read_deck_plan(root)
            native_result = NativeAdapter(
                _run_native_adapter,
                reviewed_commit=reviewed_commit(PROJECT_ROOT, "dom-to-pptx"),
            ).build(_adapter_request(job, root), root)
            write_json(root / "build" / "native-adapter-result.json", native_result)
            ledger = read_model(root / "evidence-ledger.json", EvidenceLedger)
            try:
                _validate_evidence_traceability(root, plan, ledger)
            except WorkflowError as error:
                return _block(job, f"证据校验失败：{error}", JobState.FULL_BUILD)
            _resolve_rebuilt_revisions(root, "final")
            job.dirty_slides = []
            job.state = JobState.FINAL_REVIEW
            save_job(job)
            return status_payload(job)

        if job.state == JobState.FINAL_REVIEW:
            return status_payload(job)
    except RenderError as error:
        return _block(job, f"工具执行失败：{error}", job.state)
    except OSError as error:
        return _block(job, f"文件或工具暂时不可用：{error}", job.state)
    except PageCountError as error:
        if error.needs_material:
            job.approvals = {"brief": False, "prototype": False, "final": False}
            job.approval_hashes.clear()
            return _block(job, str(error), JobState.INTAKE)
        return _block(job, str(error), job.state)
    except ValueError as error:
        if any(marker in str(error) for marker in ("材料", "用于生成大纲", "用于生成")):
            job.approvals = {"brief": False, "prototype": False, "final": False}
            return _block(job, str(error), JobState.INTAKE)
        return _fail(job, error)
    except Exception as error:
        return _fail(job, error)
    return status_payload(job)


@locked_job
def approve_gate(job_id: str, gate: str, note: str = "") -> JobRecord:
    job = load_job(job_id)
    expected = {"brief": JobState.BRIEF_REVIEW, "prototype": JobState.PROTOTYPE_REVIEW, "final": JobState.FINAL_REVIEW}
    if gate not in expected:
        raise WorkflowError(f"unknown gate: {gate}")
    root = job_dir(job_id)
    if gate == "brief" and (root / "planning" / "brief.json").is_file():
        current_brief = read_model(root / "planning" / "brief.json", JobBrief)
        if reason := _template_requirement_reason(job, root, current_brief):
            raise WorkflowError(reason)
    if gate in {"prototype", "final"}:
        from .design_plan import design_plan_block_reason
        if reason := design_plan_block_reason(root):
            raise WorkflowError(reason)
        from .expression_review import require_expression_review
        try:
            require_expression_review(root, gate)
        except (ValueError, OSError) as error:
            raise WorkflowError(str(error)) from error
    if job.approvals.get(gate):
        if _approval_is_current(job, root, gate):
            return job
        job.approvals[gate] = False
        job.approval_hashes.pop(gate, None)
        save_job(job)
    if job.state != expected[gate]:
        raise WorkflowError(f"gate {gate} cannot be approved while job is {job.state.value}")
    if gate == "brief":
        if not (root / "planning" / "brief.json").is_file():
            raise WorkflowError("brief has not been generated")
        brief = read_model(root / "planning" / "brief.json", JobBrief)
        if reason := _template_requirement_reason(job, root, brief):
            raise WorkflowError(reason)
        if reason := _generic_renderer_block_reason(brief):
            raise WorkflowError(reason)
    comments = [ReviewComment.model_validate(item) for item in read_json(root / "reviews" / "comments.json")]
    unresolved = [comment.id for comment in comments if comment.status not in {"resolved", "superseded"}]
    if gate in {"prototype", "final"} and unresolved:
        raise WorkflowError(f"resolve review comments before approval: {', '.join(unresolved)}")
    if gate == "prototype" and not (root / "build" / "prototype.pptx").is_file():
        raise WorkflowError("prototype has not been generated")
    if gate == "final" and not (root / "build" / "deck.pptx").is_file():
        raise WorkflowError("full deck has not been generated")
    if gate in {"prototype", "final"}:
        _read_deck_plan(root)
        from .authoring_policy import check_job_presentation
        check_job_presentation(root, root / "build" / ("prototype.pptx" if gate == "prototype" else "deck.pptx"),
                               f"{gate}-approval", enforce=bool(job.authoring_policy_id) and job.route == ProductionRoute.NATIVE)
    pending_assets = pending_required_assets(root)
    if gate in {"prototype", "final"} and pending_assets:
        raise WorkflowError("required assets are not acquired: " + ", ".join(need.id for need in pending_assets))
    if gate == "final" and job.route != ProductionRoute.NATIVE:
        _validate_external_output(job, root, read_model(root / "planning" / "deck-plan.json", DeckPlan))
    if gate == "final":
        _validate_evidence_traceability(
            root,
            read_model(root / "planning" / "deck-plan.json", DeckPlan),
            read_model(root / "evidence-ledger.json", EvidenceLedger),
        )
    decisions_path = root / "reviews" / "decisions.json"
    decisions = read_json(decisions_path)
    artifact_hashes = _gate_hashes(root, gate, job.route)
    decisions.append(ReviewDecision(gate=gate, approved=True, note=note, artifact_hashes=artifact_hashes).model_dump(mode="json"))
    write_json(decisions_path, decisions)
    job.approvals[gate] = True
    job.approval_hashes[gate] = artifact_hashes
    if gate == "brief":
        job.approvals.update({"prototype": False, "final": False})
        job.approval_hashes.pop("prototype", None)
        job.approval_hashes.pop("final", None)
        # A generic HTML prototype cannot validate a template's actual master,
        # geometry, placeholders or existing objects.  Template work therefore
        # proceeds from a reviewed brief directly to the adapter-backed build;
        # its real PPTX and rendered previews are the final-review artifact.
        job.state = JobState.FULL_BUILD if job.route == ProductionRoute.TEMPLATE else JobState.PROTOTYPE_REVIEW
    elif gate == "prototype":
        job.approvals["final"] = False
        job.approval_hashes.pop("final", None)
        job.state = JobState.FULL_BUILD
    save_job(job)
    return job


@locked_job
def add_comment(job_id: str, slide_id: str, body: str) -> ReviewComment:
    if not body.strip():
        raise WorkflowError("comment body cannot be empty")
    job = load_job(job_id)
    if job.state not in {JobState.PROTOTYPE_REVIEW, JobState.FINAL_REVIEW}:
        raise WorkflowError(f"comments are only accepted during review; job is {job.state.value}")
    root = job_dir(job_id)
    plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    if slide_id not in {slide.id for slide in plan.slides}:
        raise WorkflowError(f"unknown slide id: {slide_id}")
    path = root / "reviews" / "comments.json"
    comments = read_json(path)
    comment = ReviewComment(id=f"comment-{uuid.uuid4().hex[:8]}", slide_id=slide_id, body=body.strip())
    comments.append(comment.model_dump(mode="json"))
    write_json(path, comments)
    if job.state == JobState.FINAL_REVIEW and job.approvals.get("final"):
        job.approvals["final"] = False
        job.approval_hashes.pop("final", None)
        save_job(job)
    return comment


@locked_job
def queue_revisions(job_id: str) -> RevisionBatch:
    job = load_job(job_id)
    if job.state not in {JobState.PROTOTYPE_REVIEW, JobState.FINAL_REVIEW}:
        raise WorkflowError("revisions can only be queued from prototype_review or final_review")
    root = job_dir(job_id)
    comments_path = root / "reviews" / "comments.json"
    comments = [ReviewComment.model_validate(item) for item in read_json(comments_path)]
    open_comments = [comment for comment in comments if comment.status == "open"]
    if not open_comments:
        raise WorkflowError("no open review comments")
    plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    slide_by_id = {slide.id: slide for slide in plan.slides}
    requested_slide_ids = list(dict.fromkeys(comment.slide_id for comment in open_comments))
    batch = RevisionBatch(
        id=f"revision-{uuid.uuid4().hex[:8]}",
        slide_ids=requested_slide_ids,
        comment_ids=[comment.id for comment in open_comments],
        review_gate="prototype" if job.state == JobState.PROTOTYPE_REVIEW else "final",
        slide_hashes={slide_id: _slide_hash(slide) for slide_id, slide in slide_by_id.items()},
    )
    for comment in comments:
        if comment.id in batch.comment_ids:
            comment.status = "queued"
    write_json(comments_path, [comment.model_dump(mode="json") for comment in comments])
    write_json(root / "revisions" / f"{batch.id}.json", batch)
    # A final-review candidate is no longer a current delivery once a bounded
    # revision is queued.  This also covers rejected revision attempts: their
    # manually changed plan cannot leave the pre-revision package live.
    supersede_delivery_artifacts(root, "revision batch queued for deck plan changes")
    job.resume_state = JobState.PROTOTYPE_REVIEW if batch.review_gate == "prototype" else JobState.FULL_BUILD
    job.state = JobState.BLOCKED
    job.blocked_reason = f"Codex must apply {batch.id} to planning/deck-plan.json, then complete the revision batch"
    job.dirty_slides = batch.slide_ids
    job.approvals["final"] = False
    job.approval_hashes.pop("final", None)
    if batch.review_gate == "prototype":
        job.approvals["prototype"] = False
        job.approval_hashes.pop("prototype", None)
    save_job(job)
    return batch


@locked_job
def complete_revision(job_id: str, batch_id: str) -> JobRecord:
    job = load_job(job_id)
    root = job_dir(job_id)
    path = root / "revisions" / f"{batch_id}.json"
    if not path.is_file():
        raise WorkflowError(f"revision batch not found: {batch_id}")
    batch = read_model(path, RevisionBatch)
    expected_reason = f"Codex must apply {batch.id} to planning/deck-plan.json, then complete the revision batch"
    if job.state != JobState.BLOCKED or job.blocked_reason != expected_reason:
        raise WorkflowError(f"job is not waiting for revision batch {batch.id}")
    if batch.status == "superseded":
        raise WorkflowError(f"revision batch {batch.id} was superseded by a newer presentation intent")
    if batch.status in {"applied", "rebuilt"}:
        return job
    plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    slide_ids = {slide.id for slide in plan.slides}
    missing = set(batch.slide_ids) - slide_ids
    if missing:
        raise WorkflowError(f"revision refers to missing slides: {sorted(missing)}")
    slide_by_id = {slide.id: slide for slide in plan.slides}
    if set(slide_by_id) != set(batch.slide_hashes):
        raise WorkflowError("revision changed the slide set; create a new reviewed outline instead")
    unchanged = [
        slide_id
        for slide_id in batch.slide_ids
        if batch.slide_hashes.get(slide_id) == _slide_hash(slide_by_id[slide_id])
    ]
    if unchanged:
        raise WorkflowError(f"revision did not change requested slides: {', '.join(unchanged)}")
    unexpected = [
        slide_id
        for slide_id, original_hash in batch.slide_hashes.items()
        if slide_id not in batch.slide_ids and original_hash != _slide_hash(slide_by_id[slide_id])
    ]
    if unexpected:
        raise WorkflowError(f"revision changed unrequested slides: {', '.join(unexpected)}")
    supersede_delivery_artifacts(
        root,
        "completed revision batch changed the current deck plan",
    )
    batch.status = "applied"
    batch.applied_at = utc_now()
    batch.applied_deck_plan_sha256 = sha256(root / "planning" / "deck-plan.json")
    write_json(path, batch)
    comments_path = root / "reviews" / "comments.json"
    comments = [ReviewComment.model_validate(item) for item in read_json(comments_path)]
    for comment in comments:
        if comment.id in batch.comment_ids:
            comment.status = "applied"
    write_json(comments_path, [comment.model_dump(mode="json") for comment in comments])
    if batch.review_gate == "prototype":
        for artifact in (
            root / "build" / "prototype.pptx",
            root / "build" / "prototype.html",
            root / "build" / "prototype-montage.png",
        ):
            artifact.unlink(missing_ok=True)
        job.state = JobState.PROTOTYPE_REVIEW
    else:
        if job.route != ProductionRoute.NATIVE:
            for artifact in (
                root / "build" / "deck.pptx",
                root / "build" / "montage.png",
                root / "build" / "external-engine-manifest.json",
            ):
                artifact.unlink(missing_ok=True)
            external_previews = root / "build" / "previews"
            if external_previews.exists():
                shutil.rmtree(external_previews)
            external_previews.mkdir(parents=True, exist_ok=True)
        job.state = JobState.FULL_BUILD
    job.resume_state = None
    job.blocked_reason = None
    save_job(job)
    return job


@locked_job
def resume_job(job_id: str) -> dict[str, object]:
    job = load_job(job_id)
    if job.state != JobState.BLOCKED:
        return run_job(job_id)
    if not job.resume_state or (job.blocked_reason or "").startswith("Codex must apply"):
        return status_payload(job)
    if job.resume_state == JobState.INTAKE and not job.source_ids:
        return status_payload(job)
    ready, reason = route_ready(job.route)
    if not ready:
        job.blocked_reason = f"生产路由 {job.route.value} 不可用：{reason}"
        save_job(job)
        return status_payload(job)
    job.state = job.resume_state
    job.blocked_reason = None
    job.resume_state = None
    job.failure = None
    save_job(job)
    return run_job(job_id)


@locked_job
def export_job(job_id: str, *, skip_powerpoint: bool = False) -> BuildManifest:
    job = load_job(job_id)
    root = job_dir(job_id)
    if job.state != JobState.FINAL_REVIEW or not job.approvals.get("final"):
        raise WorkflowError("final gate must be approved before export")
    from .expression_review import require_expression_review
    try:
        expression_qa = require_expression_review(root, "final")
    except (ValueError, OSError) as error:
        raise WorkflowError(str(error)) from error
    if not _approval_is_current(job, root, "final"):
        job.approvals["final"] = False
        job.approval_hashes.pop("final", None)
        save_job(job)
        raise WorkflowError("approved final artifacts changed; review and approve the current deck again")
    comments = [ReviewComment.model_validate(item) for item in read_json(root / "reviews" / "comments.json")]
    unresolved = [comment.id for comment in comments if comment.status not in {"resolved", "superseded"}]
    if unresolved:
        raise WorkflowError(f"cannot export with unresolved review comments: {', '.join(unresolved)}")
    source_deck = root / "build" / "deck.pptx"
    if not source_deck.is_file():
        raise WorkflowError("full deck is missing")
    plan = _read_deck_plan(root)
    evidence_qa = _validate_evidence_traceability(root, plan, read_model(root / "evidence-ledger.json", EvidenceLedger))
    from .authoring_policy import check_job_presentation
    presentation_qa = check_job_presentation(root, source_deck, "before-export",
                                            enforce=bool(job.authoring_policy_id) and job.route == ProductionRoute.NATIVE)
    external_manifest: ExternalEngineManifest | None = None
    external_source: Path | None = None
    motion_batch_path = root / "edits" / "motion-final.json"
    motion_batch = (
        read_model(motion_batch_path, PptxEditBatch)
        if job.route == ProductionRoute.NATIVE and motion_batch_path.is_file()
        else None
    )
    native_powerpoint = _validate_native_powerpoint(job, root, source_deck)
    if job.route != ProductionRoute.NATIVE:
        external_manifest, _ = _validate_external_output(job, root, plan)
        external_source = (root / (external_manifest.build_source or "")).resolve()
    staging = root / "build" / f"export-{uuid.uuid4().hex[:8]}"
    delivery = root / "deliverables"
    backup = root / "build" / f"deliverables-backup-{uuid.uuid4().hex[:8]}"
    staging.mkdir(parents=True)
    (staging / "slides").mkdir()
    if job.route == ProductionRoute.VISUAL:
        editable_name = "candidate_visual_alternate.pptx" if skip_powerpoint else "visual_alternate.pptx"
    else:
        editable_name = "candidate_editable.pptx" if skip_powerpoint else "final_editable.pptx"
    staged_pptx = staging / editable_name
    try:
        shutil.copy2(source_deck, staged_pptx)
        notes_qa = sanitize_pptx_notes(staged_pptx)
        speech = "\n\n".join(
            f"## {slide.id} · {slide.title}\n\n{sanitize_speaker_notes_text(slide.speaker_notes)}"
            for slide in plan.slides
        )
        (staging / "speech.md").write_text(speech, encoding="utf-8")
        if job.route == ProductionRoute.NATIVE:
            (staging / "deck.html").write_text(_portable_deck_html(job, root), encoding="utf-8")
            native_adapter_result = root / "build" / "native-adapter-result.json"
            if native_adapter_result.is_file():
                shutil.copy2(native_adapter_result, staging / "native-adapter-result.json")
            if (root / "planning/native-scene.json").is_file():
                shutil.copy2(root / "planning/native-scene.json", staging / "native-scene.json")
        else:
            shutil.copy2(root / "build" / "external-engine-manifest.json", staging / "external-engine-manifest.json")
            (staging / "build-source").mkdir()
            assert external_source is not None
            shutil.copy2(external_source, staging / "build-source" / external_source.name)
        shutil.copy2(root / "evidence-ledger.json", staging / "evidence-ledger.json")
        asset_plan_path = root / "planning" / "asset-plan.json"
        asset_ledger_path = root / "assets" / "asset-ledger.json"
        if asset_plan_path.is_file():
            shutil.copy2(asset_plan_path, staging / "asset-plan.json")
        if asset_ledger_path.is_file():
            shutil.copy2(asset_ledger_path, staging / "asset-ledger.json")
        if motion_batch is not None:
            shutil.copy2(motion_batch_path, staging / "motion-batch.json")
        if native_powerpoint is not None:
            shutil.copy2(root / "edits" / "native-powerpoint.json", staging / "native-powerpoint.json")
            animated_previews = root / "build" / "animated-previews"
            if animated_previews.is_dir():
                shutil.copytree(animated_previews, staging / "animated-previews")
        shutil.copytree(root / "sources", staging / "assets")
        acquired_assets = root / "assets" / "acquired"
        if acquired_assets.is_dir() and any(acquired_assets.iterdir()):
            shutil.copytree(acquired_assets, staging / "assets" / "acquired")

        qa: dict[str, object] = {
            "html_render": "passed" if job.route == ProductionRoute.NATIVE else "not_applicable",
            "external_engine": "not_applicable" if external_manifest is None else "manifest_verified",
            "adapter": (
                read_json(root / "build" / "native-adapter-result.json").get("receipt", {})
                if job.route == ProductionRoute.NATIVE and (root / "build" / "native-adapter-result.json").is_file()
                else (external_manifest.adapter_receipt if external_manifest is not None else {})
            ),
            "editable_class": "native_editable" if external_manifest is None else external_manifest.editable_class,
            "evidence_traceability": evidence_qa,
            "speaker_notes": notes_qa,
            "presentation_quality": presentation_qa,
            "page_expression_review": expression_qa,
            "motion": "not_applied" if motion_batch is None else {
                "status": motion_batch.validation.get("status", "unknown"),
                "operations": len(motion_batch.operations),
                "warnings": motion_batch.warnings,
            },
            "native_powerpoint": "not_applied" if native_powerpoint is None else {
                "status": "passed",
                "effects": len(native_powerpoint["plan"].get("effects", [])),
                "carousels": len(native_powerpoint["plan"].get("carousels", [])),
                "morph_links": len(native_powerpoint["plan"].get("morph_links", [])),
                "transitions": len(native_powerpoint["plan"].get("transitions", [])),
                "previews": len(native_powerpoint["report"].get("previews", [])),
            },
            "officecli": officecli_validate(staged_pptx),
            "powerpoint_roundtrip": "skipped" if skip_powerpoint else "pending",
        }
        if skip_powerpoint:
            preview_files = natural_sort_paths(list((root / "build" / "previews").glob("*.png")))
            for preview in preview_files:
                shutil.copy2(preview, staging / "slides" / preview.name)
            if preview_files:
                create_montage(preview_files, staging / "montage.png")
        else:
            reference_pptx = _reference_powerpoint_source(job, root)
            qa["powerpoint_roundtrip"] = (
                powerpoint_roundtrip(staged_pptx, staging, reference_pptx)
                if reference_pptx is not None
                else powerpoint_roundtrip(staged_pptx, staging)
            )
            exported = natural_sort_paths([path for path in (staging / "slides").iterdir() if path.suffix.lower() == ".png"])
            if exported:
                create_montage(exported, staging / "montage.png")
        qa["presentation_quality"] = check_job_presentation(
            root, staged_pptx, "exported-pptx", enforce=bool(job.authoring_policy_id) and job.route == ProductionRoute.NATIVE)

        required = [staged_pptx, staging / "montage.png", staging / "speech.md", staging / "evidence-ledger.json"]
        if motion_batch is not None:
            required.append(staging / "motion-batch.json")
        if native_powerpoint is not None:
            required.append(staging / "native-powerpoint.json")
        required.append(staging / "deck.html" if job.route == ProductionRoute.NATIVE else staging / "external-engine-manifest.json")
        if external_source is not None:
            required.append(staging / "build-source" / external_source.name)
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            raise RenderError(f"delivery package is incomplete: {', '.join(missing)}")

        ledger = read_model(root / "evidence-ledger.json", EvidenceLedger)
        pptx_output_name = "visual_alternate_pptx" if job.route == ProductionRoute.VISUAL else "editable_pptx"
        output_paths: dict[str, tuple[Path, Path]] = {
            pptx_output_name: (staging / editable_name, delivery / editable_name),
            "montage": (staging / "montage.png", delivery / "montage.png"),
            "speech": (staging / "speech.md", delivery / "speech.md"),
            "evidence_ledger": (staging / "evidence-ledger.json", delivery / "evidence-ledger.json"),
        }
        if (staging / "asset-plan.json").is_file():
            output_paths["asset_plan"] = (staging / "asset-plan.json", delivery / "asset-plan.json")
        if (staging / "asset-ledger.json").is_file():
            output_paths["asset_ledger"] = (staging / "asset-ledger.json", delivery / "asset-ledger.json")
        if job.route == ProductionRoute.NATIVE:
            output_paths["html_source"] = (staging / "deck.html", delivery / "deck.html")
            if (staging / "native-scene.json").is_file():
                output_paths["native_scene"] = (staging / "native-scene.json", delivery / "native-scene.json")
            if (staging / "native-adapter-result.json").is_file():
                output_paths["native_adapter_result"] = (
                    staging / "native-adapter-result.json",
                    delivery / "native-adapter-result.json",
                )
            if motion_batch is not None:
                output_paths["motion_batch"] = (staging / "motion-batch.json", delivery / "motion-batch.json")
        else:
            assert external_source is not None
            output_paths["external_engine_manifest"] = (
                staging / "external-engine-manifest.json",
                delivery / "external-engine-manifest.json",
            )
            output_paths["build_source"] = (
                staging / "build-source" / external_source.name,
                delivery / "build-source" / external_source.name,
            )
        if native_powerpoint is not None:
            output_paths["native_powerpoint"] = (staging / "native-powerpoint.json", delivery / "native-powerpoint.json")
        outputs: dict[str, dict[str, object]] = {
            name: {
                "path": str(destination),
                "sha256": sha256(staged),
                "size_bytes": staged.stat().st_size,
            }
            for name, (staged, destination) in output_paths.items()
            if staged.is_file()
        }
        for name in ("slides", "assets", "animated-previews"):
            directory = staging / name
            if not directory.is_dir():
                continue
            files = [path for path in directory.rglob("*") if path.is_file()]
            outputs[name] = {
                "path": str(delivery / name),
                "sha256": _directory_hash(directory),
                "size_bytes": sum(path.stat().st_size for path in files),
                "file_count": len(files),
            }
        renderer_name = "dom-to-pptx 2.0.3" if external_manifest is None else f"{external_manifest.engine} {external_manifest.version}"
        if external_manifest is None and (root / "build/native-adapter-result.json").is_file():
            native_receipt = read_json(root / "build/native-adapter-result.json").get("receipt", {})
            renderer_name = f"{native_receipt.get('engine', 'unknown')} {native_receipt.get('version', 'unknown')}"
        manifest_status = "candidate" if skip_powerpoint else ("alternate" if job.route == ProductionRoute.VISUAL else "final")
        manifest = BuildManifest(
            job_id=job.id,
            status=manifest_status,
            route=job.route,
            source_hashes={source.source_id: source.sha256 for source in ledger.sources},
            providers=[] if external_manifest is None else external_manifest.providers,
            engines={
                "orchestrator": "aippt-studio 0.1.0",
                "renderer": renderer_name,
                "postprocessor": " + ".join(
                    name
                    for name, enabled in (
                        ("OfficeCLI motion", motion_batch is not None),
                        ("PowerPoint native", native_powerpoint is not None),
                    )
                    if enabled
                ) or "none",
                "office": "deferred" if skip_powerpoint else qa.get("powerpoint_roundtrip", {}).get("backend", "PowerPoint COM"),
            },
            outputs=outputs,
            qa=qa,
        )
        write_json(staging / "build-manifest.json", manifest)

        if delivery.resolve().parent != root.resolve():
            raise WorkflowError("invalid delivery directory")
        delivery.replace(backup)
        try:
            staging.replace(delivery)
        except Exception:
            backup.replace(delivery)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        # Archival evidence is supplemental to a completed delivery.  A local
        # filesystem problem here cannot roll back the delivered package; the
        # missing receipt simply remains unavailable to project learning.
        try:
            record_final_delivery_learning_qa(root, delivery / editable_name, qa, skip_powerpoint=skip_powerpoint)
        except OSError:
            pass
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    job.state = JobState.FINAL_REVIEW if skip_powerpoint or job.route == ProductionRoute.VISUAL else JobState.DELIVERED
    save_job(job)
    return manifest
