"""Explicit PptxGenJS authoring option within the existing native route."""
from __future__ import annotations

import json
import base64
import copy
import math
import sys
import shutil
import tempfile
import uuid
from pathlib import Path

from .models import DeckPlan, JobBrief, JobState, ProductionRoute, select_prototype_slides
from .renderer import RenderError, natural_sort_paths, officecli_validate, sha256
from .storage import job_dir, load_job, locked_job, read_model, save_job, write_json
from .upstream_tools import invoke
from .style_profiles import profile_font_families
from .config import WORKSPACE_ROOT
from .template_designs import resolve_planned_template_profile


def native_authoring_settings(scene: dict, plan: DeckPlan) -> dict:
    """Build relative, overrideable type defaults for the native scene bridge."""
    profile = plan.planning_strategy.get("visual_style_profile")
    if not isinstance(profile, dict):
        return {}
    profile, _design_status = resolve_planned_template_profile(
        WORKSPACE_ROOT, plan.planning_strategy, profile
    )
    if profile.get("application") != "baseline_new_composition":
        return {}
    page_size = scene.get("page_size") or {"width": 40 / 3, "height": 7.5}
    width, height = (page_size.get("width"), page_size.get("height")) if isinstance(page_size, dict) else (None, None)
    scale = 1.0
    if (isinstance(width, (int, float)) and not isinstance(width, bool)
            and isinstance(height, (int, float)) and not isinstance(height, bool)
            and math.isfinite(width) and math.isfinite(height) and width > 0 and height > 0):
        scale = min(width / (40 / 3), height / 7.5)
    defaults = {
        "profile_id": profile.get("profile_id", "general-editorial-v1"),
        "body_font_pt": float(profile.get("body_px", 46)) / 2 * scale,
        "title_font_pt": float(profile.get("title_px", 70)) / 2 * scale,
        "cover_title_font_pt": float(profile.get("cover_title_px", 94)) / 2 * scale,
        "caption_font_pt": float(profile.get("caption_px", 30)) / 2 * scale,
        "statement_font_pt": float(profile.get("statement_px", profile.get("title_px", 70))) / 2 * scale,
        "table_font_pt": float(profile.get("body_px", 46)) / 2 * scale,
        "chart_label_font_pt": float(profile.get("caption_px", 30)) / 2 * scale,
        "chart_legend_font_pt": float(profile.get("caption_px", 30)) / 2 * scale,
    }
    explicit_scene_font = scene.get("font_family")
    platform_default_font = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"
    explicit_plan_font = plan.font_family != platform_default_font
    if explicit_scene_font or explicit_plan_font:
        selected = explicit_scene_font or plan.font_family
        defaults["heading_font_face"] = selected
        defaults["body_font_face"] = selected
        defaults["label_font_face"] = selected
    else:
        roles = profile_font_families(profile)
        defaults["heading_font_face"] = roles["heading"][0]
        defaults["body_font_face"] = roles["body"][0]
        defaults["label_font_face"] = roles["label"][0]
    # Authored overrides can refine the selected profile; absent values inherit
    # the profile and each individual element's options.fontSize wins in JS.
    overrides = scene.get("authoring_profile")
    return {**defaults, **(overrides if isinstance(overrides, dict) else {})}


def apply_native_style_roles(scene: dict, plan: DeckPlan) -> dict:
    """Infer only the approved slide title role; preserve authored role tags."""
    profile = plan.planning_strategy.get("visual_style_profile", {})
    if not isinstance(profile, dict) or profile.get("application") != "baseline_new_composition":
        return scene
    slides = {slide.id: slide for slide in plan.slides}
    for page in scene.get("slides", []):
        slide = slides.get(page.get("id"))
        if slide is None:
            continue
        elements = [item for item in page.get("elements", []) if item.get("type") == "text" and isinstance(item.get("text"), str)]
        titled = [item for item in elements if slide.title.strip() == item["text"].strip()]
        if not titled:
            titled = [item for item in elements if slide.title and slide.title in item["text"]]
        if titled and not titled[0].get("style_role"):
            titled[0]["style_role"] = "cover_title" if slide.layout == "cover" else "title"
    return scene


def _check_scene(scene: dict, plan: DeckPlan) -> None:
    pages = scene.get("slides", [])
    if [p.get("id") for p in pages] != [s.id for s in plan.slides]:
        raise ValueError("native scene must contain every deck-plan slide ID in order")
    # A native scene bypasses the generic HTML renderer, so it must not become
    # a side door for an assistant reply or an internal production label to
    # appear as editable slide text.  Keep this deliberately narrow: other
    # contextual copy questions still go through the bound actual-page
    # expression review, where a quoted example can be judged in context.
    from .expression_review import copy_signals

    blocked_codes = {"ASSISTANT_REPLY", "INTERNAL_LABEL"}
    for page, slide in zip(pages, plan.slides, strict=True):
        elements = page.get("elements", [])
        if not any(el.get("type") == "text" and slide.title in el.get("text", "") for el in elements):
            raise ValueError(f"native scene must retain the approved title for {slide.id}")
        for element in elements:
            if element.get("type") != "text" or not isinstance(element.get("text"), str):
                continue
            blocked = [signal for signal in copy_signals(element["text"]) if signal["code"] in blocked_codes]
            if blocked:
                codes = ", ".join(sorted({signal["code"] for signal in blocked}))
                raise ValueError(
                    f"native scene contains assistant or internal production copy on {slide.id} ({codes}); "
                    "keep it in the Work record or expression-review evidence, not visible slide text"
                )


def write_native_design_context(root: Path, plan: DeckPlan, scene: dict) -> dict:
    """Expose selection gaps and declared object links without an approval gate."""
    from .design_context import build_design_context
    path = root / "planning/design-context.json"
    brief_path = root / "planning/brief.json"
    binding = {"plan_sha256": sha256(root / "planning/deck-plan.json"),
               "brief_sha256": sha256(brief_path) if brief_path.is_file() else None}
    context = {}
    if path.is_file():
        try:
            prior = json.loads(path.read_text(encoding="utf-8"))
            if prior.get("plan_binding") == binding:
                context = prior
        except (OSError, ValueError):
            pass
    if not context:
        brief = read_model(brief_path, JobBrief) if brief_path.is_file() else JobBrief(goal=plan.title)
        context = build_design_context(root, plan, brief, workspace_root=WORKSPACE_ROOT)
    context["plan_binding"] = binding
    context["scene_sha256"] = sha256(root / "planning/native-scene.json")
    applications = scene.get("reference_applications", [])
    applications = applications if isinstance(applications, list) else []
    links = []
    scene_pages = {page["id"]: page for page in scene.get("slides", [])}
    page_design = plan.planning_strategy.get("page_design", {})
    page_design = page_design if isinstance(page_design, dict) else {}
    for slide_id, meta in page_design.items():
        decision = meta.get("reference_decision", {}) if isinstance(meta, dict) else {}
        if not isinstance(decision, dict) or not decision.get("source_path"):
            continue
        matches = [item for item in applications if isinstance(item, dict)
                   and item.get("slide_id") == slide_id
                   and item.get("source_path") == decision["source_path"]
                   and item.get("source_slide") == decision.get("source_slide")]
        indexes = matches[0].get("target_element_indexes", []) if len(matches) == 1 else []
        elements = scene_pages.get(slide_id, {}).get("elements", [])
        valid = (isinstance(indexes, list) and bool(indexes) and len(set(map(str, indexes))) == len(indexes)
                 and all(isinstance(i, int) and not isinstance(i, bool) and 0 <= i < len(elements) for i in indexes))
        links.append({"slide_id": slide_id, "source_path": decision["source_path"],
                      "source_slide": decision.get("source_slide"),
                      "borrowed_relationship": decision.get("borrowed_relationship", ""),
                      "status": "declared_objects_located_not_visual_approval" if valid else "missing_or_invalid_object_links",
                      "target_elements": [{"index": i, "type": elements[i].get("type"),
                                           "text": elements[i].get("text", "")[:180],
                                           "geometry": {k: elements[i].get("options", {}).get(k) for k in ("x", "y", "w", "h")}}
                                          for i in indexes] if valid else []})
    context["reference_application_links"] = links
    context["review_instruction"] = (
        "Read narrative intent and page relationships before judging the whole deck. Compare selected source pages "
        "with current output images and located objects. Inspect proportions, hierarchy and content fit; a valid link "
        "only locates objects, never proves good design or user acceptance. New composition is valid with a reason."
    )
    write_json(path, context)
    missing_pages = [key for key, page in context.get("page_references", {}).items() if page.get("missing_fields")]
    return {"path": str(path), "status": "advisory_not_approval",
            "missing_narrative_fields": context.get("missing_narrative_fields", []),
            "pages_needing_content_relationship": missing_pages,
            "reference_application_links": links,
            "next": "Read this context, inspect suitable whole-deck and page references, and resolve design choices before full build."}


@locked_job
def set_native_scene(job_id: str, scene: dict) -> dict:
    from .assets.acquisition import _invalidate_rendered_artifacts
    job = load_job(job_id)
    if job.work_request is not None:
        from .presentation_intent import read_bound_work_request, requests_template_adaptation
        request_text, _binding = read_bound_work_request(job_id)
        if requests_template_adaptation(request_text):
            raise ValueError("本轮用户要求套用模板；native scene 不能把模板降为仅供参考。")
    blocked_native_repair = (
        job.state == JobState.BLOCKED
        and job.resume_state in {JobState.PROTOTYPE_REVIEW, JobState.FULL_BUILD}
        and (
            (job.blocked_reason or "").startswith("工具执行失败：native layout needs review/repair")
            or (job.blocked_reason or "").startswith("工具执行失败：presentation quality needs repair/review")
            or (
                (job.blocked_reason or "").startswith("工具执行失败：Found ")
                and "validation error(s)" in (job.blocked_reason or "")
            )
            or (job.blocked_reason or "").startswith("工具执行失败：native-author failed")
            or (job.blocked_reason or "").startswith("证据校验失败：")
        )
    )
    if job.route != ProductionRoute.NATIVE or (
        job.state not in {JobState.PROTOTYPE_REVIEW, JobState.FULL_BUILD, JobState.FINAL_REVIEW}
        and not blocked_native_repair
    ):
        raise ValueError("native-scene requires a native job with a deck plan, before delivery")
    root = job_dir(job_id)
    plan = read_model(root / "planning/deck-plan.json", DeckPlan)
    from .design_plan import design_plan_block_reason
    if reason := design_plan_block_reason(root, plan):
        raise ValueError(reason)
    scene = copy.deepcopy(scene)
    _check_scene(scene, plan)
    scene = apply_native_style_roles(scene, plan)
    scene["deck_plan_sha256"] = sha256(root / "planning/deck-plan.json")
    scene["engine"] = "PptxGenJS"
    write_json(root / "planning/native-scene.json", scene)
    design_context = write_native_design_context(root, plan, scene)
    job.approvals.update({"prototype": False, "final": False})
    job.approval_hashes.pop("prototype", None)
    job.approval_hashes.pop("final", None)
    if blocked_native_repair:
        job.state = job.resume_state
        job.resume_state = None
        job.blocked_reason = None
        job.failure = None
    job.state = _invalidate_rendered_artifacts(
        root,
        job.state,
        reason="native scene authoring plan changed",
    )
    save_job(job)
    return {"job_id": job_id, "engine": "PptxGenJS", "slides": len(plan.slides), "state": job.state.value,
            "design_context": design_context,
            "next": "run; inspect actual native prototype and approve through the existing review gate"}


def render_scene(root: Path, pptx: Path, previews: Path, *, prototype: bool = False) -> dict:
    scene = json.loads((root / "planning/native-scene.json").read_text(encoding="utf-8"))
    plan = read_model(root / "planning/deck-plan.json", DeckPlan)
    if scene.get("deck_plan_sha256") != sha256(root / "planning/deck-plan.json"):
        raise RenderError("deck plan changed after native-scene binding; update and bind the scene again")
    _check_scene(scene, plan)
    scene = apply_native_style_roles(scene, plan)
    if prototype:
        pages_by_id = {page["id"]: page for page in scene["slides"]}
        scene["slides"] = [pages_by_id[slide.id] for slide in select_prototype_slides(plan)]
    policy_file = root / "planning/authoring-policy.json"
    if policy_file.is_file():
        from .authoring_policy import audit_policy
        policy = json.loads(policy_file.read_text(encoding="utf-8"))
        scene["quality_policy"] = audit_policy(root, policy, prototype=prototype)
    # Carry the selected, occasion-specific type scale into native authoring.
    # Scene element options remain the explicit override for authored sizes.
    authoring_settings = native_authoring_settings(scene, plan)
    if authoring_settings:
        scene["authoring_profile"] = authoring_settings
    scene.setdefault("font_family", plan.font_family)
    call = root / "build/upstream-calls" / f"native-{uuid.uuid4().hex}"
    try:
        receipt = invoke("PptxGenJS", "author", scene, call)
        qa = receipt["result"]
        if qa["status"] != "passed":
            quality_path = call / "presentation-quality.json"
            if quality_path.is_file():
                quality = json.loads(quality_path.read_text(encoding="utf-8"))
                if quality.get("status") != "passed":
                    from .authoring_policy import record_quality_findings
                    record_quality_findings(root, quality)
                    raise RenderError(f"presentation quality needs repair/review; see {quality_path}")
            raise RenderError(f"native layout needs review/repair; see {call / 'native-qa.json'}")
        officecli_validate(call / "candidate.pptx")
        from .mac_quick_png import export_png
        with tempfile.TemporaryDirectory(prefix="native-preview-", dir=root / "build") as temporary:
            preview_dir = Path(temporary) / "slides"
            export_png(call / "candidate.pptx", preview_dir)
            images = natural_sort_paths(list(preview_dir.glob("*.png")))
            if len(images) != len(scene["slides"]):
                raise RenderError("PNG preview count differs from native scene")
            pptx.parent.mkdir(parents=True, exist_ok=True)
            previews.mkdir(parents=True, exist_ok=True)
            shutil.copy2(call / "candidate.pptx", pptx)
            for page, source in zip(scene["slides"], images, strict=True):
                shutil.copy2(source, previews / f"{page['id']}.png")
            # Review HTML must show the chosen engine's page images. The local
            # renderer is approximate; native PowerPoint QA remains separate.
            review_html = "<!doctype html><html><meta charset='utf-8'><body style='margin:0;background:#222'>"
            for page, image in zip(scene["slides"], images, strict=True):
                encoded = base64.b64encode(image.read_bytes()).decode("ascii")
                review_html += f'<section data-slide-id="{page["id"]}"><img style="display:block;width:100%" src="data:image/png;base64,{encoded}" alt="slide preview"></section>'
            review_html += "</body></html>"
            (root / ("build/prototype.html" if prototype else "build/deck.html")).write_text(review_html, encoding="utf-8")
        write_json(root / ("build/native-prototype-call.json" if prototype else "build/native-scene-call.json"),
                   {"receipt": str((call / "receipt.json").relative_to(root)), "sha256": sha256(call / "receipt.json"),
                    "engine": "PptxGenJS", "runtime_version": qa["runtime_version"]})
        return qa
    except (ValueError, OSError) as error:
        raise RenderError(str(error)) from error
