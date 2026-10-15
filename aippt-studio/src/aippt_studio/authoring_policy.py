"""Always-on authoring defaults from explicit user direction, outside case search."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path


_DEFAULTS = {
    "schema_version": 1,
    "id": "presentation-expression-v3",
    "authority": "explicit_user_direction_on_page_expression",
    "source": "Work 37 correction; workspace/expression-refinement/request.md",
    "typography_mode": "advisory",
    "minimum_font_sizes": {},
    "mode": "new",
    "rules": [
        {"id": "audience_message", "user_quote": "重点不是说文字的大小，是整体的页面的表达", "action": "Start with what the audience should understand from this page. Every visible unit must contribute a claim, evidence, explanation or navigation; remove decorative filler and unsupported conclusions."},
        {"id": "formal_copy", "user_quote": "好像AI把要跟我对话的内容都放在PPT上面了", "action": "Write as the presenter addressing the audience in this occasion. Keep assistant replies, layout instructions, QA reminders and editing plans outside visible copy and speaker notes. Evaluate complete meaning: 本项目 and genuine technical steps are legitimate when they convey specific facts."},
        {"id": "concise_formal_copy", "action": "For new and template-derived decks, use concise, formal audience-facing page copy: state the page claim and retain the facts needed to support it. Put supporting evidence detail in speaker notes only as a presenter-facing talk track; notes must not contain production instructions, assistant replies or QA reminders."},
        {"id": "semantic_hierarchy", "user_quote": "大字，然后旁边窄一些小字，然后呢意义不明", "action": "Make emphasis proportional to semantic importance and to the whole page. Read headline and supporting content together; reject isolated huge numbers, narrow dense explanations and arbitrary size contrast when they weaken understanding. Font measurements are clues, not universal design rules."},
        {"id": "deck_continuity", "user_quote": "这个怎么和其他页面没有产生关联", "action": "Assess the page before and after in the argument. Repair missing premises, repeated conclusions and disconnected sections in the deck itself; never print a reminder to connect pages in the presentation."},
        {"id": "self_review", "user_quote": "你自己去看的时候，你就应该有这种感知", "action": "Inspect actual pages at presentation scale and adjacent-page overview. Judge wording, purpose, hierarchy, spacing, image relevance and obstruction as a whole. Revise the work before delivery; a clean numeric audit is not editorial or visual approval."},
        {"id": "precise_revision", "action": "Keep accepted content and non-target objects intact during local changes; review the complete affected page after the edit."},
        {"id": "necessary_navigation", "user_quote": "不要01，02的，丑", "action": "Default to no decorative 01/02/03/04 corner labels, empty numbered circles, rules attached to those ornaments, or arbitrary index tags. A numeral must have a real audience purpose; preserve meaningful quantities, complete units and comparisons, explicitly requested page navigation, actual chapters and business steps. Inspect the entire rendered slide, including template/master and image content, rather than relying on text detection."},
        {"id": "coherent_composition", "action": "Create relationships among content, hierarchy and space. Merely adding a frame or scattering text and images does not establish a composition. Let page roles vary with the argument instead of repeating the same block structure. Avoid low-contrast supporting copy and decorative filler; judge the full rendered page."},
        {"id": "image_claim_fidelity", "action": "Map each photograph, diagram and evidence image to this page's exact claim. Check relevance, source identity, prior use and crop before placement. Do not substitute a performance photo for learning, repeat a photo without a reason, or change a named source fact to fit the layout."},
        {"id": "image_surface_geometry", "user_quote": "背景框是一个椭圆，或者说四角它又有一个圆角", "action": "Treat the image, crop, border and backing as one composition. Apply rounded or elliptical geometry to the actual image surface, not only to a shape underneath it. Preserve aspect ratio, subject identity and evidence content; select fit and crop from the image's role. A frame does not authorize cutting away a certificate, screenshot label or person. Inspect the rendered edges for exposed rectangular corners, white/black strips, clipping and mismatched rotation. Use the shared image geometry operation in the authoring route; unrelated overlapping objects need contextual review instead of guessed automatic pairing."},
        {"id": "template_image_geometry", "action": "When deriving from a template, preserve each source picture's native picture geometry, crop, mask, border and transform unless the requested change explicitly targets that image treatment. Do not replace or flatten the picture geometry while retaining only its backing frame; review actual image edges after export."},
        {"id": "complete_evidence", "action": "Preserve source names, entities, units, percentage signs, comparison bases and evidence roles. Highlight the meaningful exception or relationship, not arbitrary words. A headline may claim improvement only when the source establishes the comparison. Native editability and accepted user adjustments are part of the deliverable, not optional decorations."},
        {"id": "shared_authoring_workflow", "action": "For every new task, independently establish its specific presentation nature, audience need, message, evidence boundary and storyline, then each page's content relationship. Use the current shared authoring workflow and record design-plan in versioned new Works before build. This rule does not depend on a matching historical case or this chat. Bounded local edits preserve accepted content without full-deck replanning."},
        {"id": "two_level_reference_judgment", "action": "Compare same-purpose whole-deck organization before selecting page/component references by actual content relationships. Teaching, work reporting, research, competitions, technical explanation, brand and ceremony have different purposes; do not impose a universal story or style. Keep discovery, source observation, chosen relationship, object adaptation and actual-page assessment distinct. A deliberate new composition is valid; a retrieved layout or located object is not visual approval."},
    ],
    "exceptions": [],
    "object_roles": [],
    "precedence": "Current purpose, audience, actual content and accepted reference determine the composition. Record explicit project constraints when supplied; never attribute a tool's numeric defaults to the user.",
    "visual_review_required": True,
    "expression_review_required": True,
}



def authoring_defaults(project_root: Path | None = None, *, include_learned: bool = True) -> dict:
    result = copy.deepcopy(_DEFAULTS)
    if include_learned:
        from .config import WORKSPACE_ROOT
        from .evolution import load_guidance
        guidance = load_guidance(project_root or WORKSPACE_ROOT)
        for rule in guidance["rules"]:
            result["rules"].append({"id": "learned:" + rule["id"], "action": rule["action"],
                "applies_when": rule["applies_when"], "exceptions": rule["exceptions"],
                "source_receipt": rule["source_receipt"], "authority": "source_grounded_semantic_learning"})
    return result


def job_authoring_defaults(root: Path) -> dict:
    from .lesson_application import workspace_for_job
    repo, _ = workspace_for_job(root)
    return authoring_defaults(repo, include_learned=repo is not None)


def initialize_authoring_policy(root: Path) -> None:
    destination = root / "planning/authoring-policy.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(job_authoring_defaults(root), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def standalone_audit_policy(config: dict) -> dict:
    """Accept the audit contract or our explicit versioned job wrapper."""
    audit_keys = {"mode", "typography_mode", "minimum_font_sizes", "object_roles", "exceptions"}
    if not isinstance(config, dict):
        raise ValueError("quality policy must be an object")
    if set(config) <= audit_keys:
        return config
    if (set(config) - (set(_DEFAULTS) | {"typography_pt"}) or config.get("schema_version") != 1
            or config.get("id") not in {_DEFAULTS["id"], "presentation-expression-v2", "readable-presentation-v1"}):
        raise ValueError("Unsupported job quality policy wrapper or unknown fields")
    return {key: copy.deepcopy(config[key]) for key in audit_keys if key in config}


def audit_policy(root: Path, config: dict, *, prototype: bool = False) -> dict:
    policy = {key: copy.deepcopy(config[key]) for key in ("mode", "typography_mode", "minimum_font_sizes", "object_roles", "exceptions") if key in config}
    if prototype and (root / "planning/deck-plan.json").is_file():
        from .models import DeckPlan, select_prototype_slides
        from .storage import read_model
        plan = read_model(root / "planning/deck-plan.json", DeckPlan)
        indexes = {slide.id: i + 1 for i, slide in enumerate(plan.slides)}
        mapping = {indexes[slide.id]: i + 1 for i, slide in enumerate(select_prototype_slides(plan))}
        for field in ("object_roles", "exceptions"):
            if not isinstance(policy.get(field, []), list):
                continue  # The strict audit parser reports malformed policies.
            mapped = []
            for rule in policy.get(field, []):
                if not isinstance(rule, dict) or not isinstance(rule.get("slide"), int):
                    mapped.append(rule)
                elif rule["slide"] in mapping:
                    mapped.append({**rule, "slide": mapping[rule["slide"]]})
            policy[field] = mapped
    return policy


def update_job_policy(job_id: str, request: dict) -> dict:
    """Update bounded object rules through a recoverable, hash-aware entrypoint."""
    from .delivery_artifacts import supersede_delivery_artifacts
    from .presentation_quality import _policy
    from .models import JobState
    from .storage import job_dir, job_lock, load_job, save_job, write_json
    if not isinstance(request, dict) or set(request) - {"typography_mode", "minimum_font_sizes", "object_roles", "exceptions"}:
        raise ValueError("quality-policy accepts typography_mode, minimum_font_sizes, object_roles and exceptions only")
    with job_lock(job_id):
        root = job_dir(job_id)
        job = load_job(job_id)
        if job.state in {JobState.DELIVERED, JobState.FAILED}:
            raise ValueError("Terminal job quality policies are immutable; create a separate revision job")
        path = root / "planning/authoring-policy.json"
        old = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else job_authoring_defaults(root)
        updated = {**old, **request, "mode": "new"}
        if "minimum_font_sizes" in request and "typography_mode" not in request:
            updated["typography_mode"] = "explicit"
        _policy(audit_policy(root, updated))
        if path.is_file() and updated == old:
            return {"job_id": job_id, "status": "unchanged", "state": job.state.value}
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            history = root / "reviews/quality-policies" / f"{digest}.json"
            if not history.is_file():
                write_json(history, old)
        # A candidate delivery encodes the prior quality policy.  Keep it as
        # historical evidence before the new policy becomes active so status
        # cannot offer a deck that has not passed the newly requested rules.
        supersede_delivery_artifacts(root, "authoring policy changed")
        write_json(path, updated)
        job.authoring_policy_id = updated["id"]
        job.approvals.update({"prototype": False, "final": False})
        job.approval_hashes.pop("prototype", None)
        job.approval_hashes.pop("final", None)
        # Earlier refinement builds included this visual rule in the brief
        # hash. Only that known field is removed; brief/source hashes remain.
        job.approval_hashes.get("brief", {}).pop("authoring_policy", None)
        resume = job.resume_state if job.state == JobState.BLOCKED else job.state
        quality_block = any(s in (job.blocked_reason or "") for s in ("presentation quality", "native layout", "authoring_policy"))
        if job.state != JobState.BLOCKED or quality_block:
            if resume in {JobState.PROTOTYPE_REVIEW, JobState.FULL_BUILD, JobState.FINAL_REVIEW}:
                job.state = JobState.PROTOTYPE_REVIEW
                job.blocked_reason = None
                job.resume_state = None
                job.failure = None
        save_job(job)
        return {"job_id": job_id, "status": "updated", "state": job.state.value,
                "invalidated_approvals": ["prototype", "final"],
                "next": "Rebuild and review a fresh candidate under the new object rules."}


def record_quality_findings(root: Path, report: dict) -> dict:
    """Keep observed failures as evidence; do not invent a root cause or lesson."""
    from .learning import read_json, record_event, write_json
    work = next((p for p in (root.resolve(), *root.resolve().parents) if (p / "work.json").is_file()), None)
    if work is None or report.get("status") == "passed" or not report.get("issues"):
        return {"status": "not_applicable"}
    if read_json(work / "work.json", {}).get("status") == "completed":
        return {"status": "requires_work_reopen"}
    # Match the same file bytes and findings across repeated stages/runs. The
    # per-stage QA file retains execution time and temporary candidate path.
    observation = {key: value for key, value in report.items()
                   if key not in {"generated_at", "stage", "enforced", "source"}}
    data = json.dumps(observation, ensure_ascii=False, sort_keys=True).encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    relative = f"workspace/quality-observations/{digest}.json"
    snapshot = work / relative
    if snapshot.is_file():
        if snapshot.read_bytes() != data:
            raise ValueError("Immutable quality observation was changed")
    else:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(data)
    codes = sorted({issue["code"] for issue in report["issues"] if issue.get("severity") != "advisory"})
    event = record_event(work, {
        "kind": "failure", "trigger": "Actual PPTX readability and geometry audit",
        "scope": "Current presentation and named slide/object IDs only",
        "problem": ", ".join(codes),
        "correction": "Repair the identified objects, then rerun the audit and inspect the actual page. A geometric signal does not establish semantic root cause.",
        "verification": "failed", "evidence": [relative], "reusable": False,
    })
    return {"status": "recorded", "event_id": event["id"], "evidence": relative}


def _attach_current_project_learning_provenance(root: Path, pptx: Path, report: dict) -> None:
    """Annotate a passed final QA report only when it has a live Work chain.

    Authoring quality remains useful for legacy/global jobs, but those reports
    must not become archive-verifiable merely because they say ``passed``.  The
    archive module reconstructs the same Work/job/request/plan/source/output
    context later, so this best-effort annotation is deliberately fail-closed.
    """

    if report.get("status") != "passed":
        return
    try:
        from .project_learning import current_qa_provenance_for_output

        proof = current_qa_provenance_for_output(root, pptx)
        # ``audit_presentation`` already protects against a concurrent source
        # change.  Keep that identity when attaching its separate archive proof.
        if proof is not None and report.get("source_sha256") == proof["output_sha256"]:
            report["project_learning_provenance"] = proof
    except (OSError, ValueError, TypeError, AttributeError):
        # A missing/legacy Work binding must leave this ordinary QA historical,
        # never break delivery or guess a provenance record.
        return


def check_job_presentation(root: Path, pptx: Path, stage: str, *, enforce: bool = True) -> dict:
    """Persist evidence before raising; never call a fake renderer a quality pass."""
    from .presentation_quality import audit_presentation
    from .renderer import RenderError
    from .storage import write_json

    policy_path = root / "planning/authoring-policy.json"
    report_path = root / "qa" / f"presentation-quality-{stage}.json"
    if os.environ.get("AIPPT_FAKE_RENDER") == "1":
        report = {"status": "skipped-test-render", "visual_reviewed": False, "reason": "Test placeholder contains no presentation geometry."}
        write_json(report_path, report)
        return report
    if enforce and not policy_path.is_file():
        raise RenderError("authoring policy is missing for a quality-controlled job; restore planning/authoring-policy.json")
    config = json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.is_file() else job_authoring_defaults(root)
    # Only the defined audit contract is forwarded. Defaults/rules remain
    # available to the planner, and cannot become an accidental global skip.
    policy = audit_policy(root, config, prototype=stage.startswith("prototype"))
    report = audit_presentation(pptx, policy=policy)
    report["enforced"] = enforce
    report["stage"] = stage
    _attach_current_project_learning_provenance(root, pptx, report)
    write_json(report_path, report)
    record_quality_findings(root, report)
    if report["enforced"] and report.get("status") != "passed":
        raise RenderError(f"presentation quality needs repair/review; see {report_path}; fix the named objects or record a specific evidence-backed exception")
    return report
