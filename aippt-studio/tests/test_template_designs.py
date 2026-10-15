from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aippt_studio.html_deck import write_deck_html
from aippt_studio.template_library import main as template_library_main
from aippt_studio.models import DeckPlan, PresentationForm, SlideSpec
from aippt_studio.storage import job_dir, write_json
from aippt_studio.style_profiles import style_profile
from aippt_studio.template_designs import (
    apply_template_design_tokens, resolve_planned_template_profile,
    select_template_design,
)
from aippt_studio.workflow import create_job
from aippt_studio.ingest import ingest_sources
from aippt_studio.planner import create_brief, create_deck_plan


def _library(workspace: Path, *, status: str = "visual_verified") -> tuple[Path, dict]:
    source = workspace / "模板库/竞赛与答辩/样本.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source deck only; never a client fact or image")
    row = {
        "id": "competition-argument-grid", "source_path": "模板库/竞赛与答辩/样本.pptx",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "forms": ["competition_defense"], "patterns": ["cover", "editorial_columns"],
        "style_tags": ["清晰"], "reviewed_pages": [1, 3], "review_status": status,
        "priority": 1,
        "design_tokens": {
            "page_inset_x": 118, "page_inset_y": 80,
            "title_px": 76, "cover_title_px": 104, "body_px": 44,
            "caption_px": 28, "title_body_gap": 36,
            "item_gap": 18, "grid_gap": 56,
            "image_text_ratio": [0.85, 1.15],
        },
        "observed_typefaces": ["Source Face"],
        "language_rules": ["先陈述问题，再说明有证据的解决办法"],
        "composition_learning": "Title and proof share a clear two-column hierarchy.",
        "page_variant": "argument_grid",
        "page_measurements": {"evidence_basis": "OOXML measurements only", "title_width_ratio": 0.42},
    }
    catalog = workspace / "模板库/DESIGN-LANGUAGE.json"
    catalog.write_text(json.dumps({"schema_version": 1, "designs": [row]}, ensure_ascii=False), encoding="utf-8")
    return source, row


def _expression(application: str = "baseline_new_composition") -> dict:
    return {"application": application, "form": "competition_defense",
            "slides": {"slide-01": {"pattern": "cover"}, "slide-02": {"pattern": "editorial_columns"}}}


def test_source_verified_design_selects_measured_tokens_without_template_copy(tmp_path: Path) -> None:
    source, row = _library(tmp_path)
    selected = select_template_design(tmp_path, "competition_defense", _expression(), "清晰")
    assert selected["status"] == "ready"
    assert selected["selected"]["id"] == row["id"]
    assert selected["selected"]["matched_patterns"] == ["cover", "editorial_columns"]
    assert selected["source_facts_reusable"] is False
    assert selected["source_media_reusable"] is False
    base = style_profile(PresentationForm.COMPETITION_DEFENSE)
    applied = apply_template_design_tokens(base, selected)
    assert applied["title_px"] == 76 and applied["page_inset_x"] == 118
    assert applied["typography"] == base["typography"]  # An observed font is not an installed/licensed font.
    assert "source deck only" not in json.dumps(applied)
    source.write_bytes(b"changed")
    stale = select_template_design(tmp_path, "competition_defense", _expression(), "清晰")
    assert stale["status"] == "no_matching_verified_design"
    assert stale["skipped"] == [{"id": row["id"], "reason": "source_sha256_mismatch"}]


def test_unreviewed_and_out_of_scope_designs_cannot_execute(tmp_path: Path) -> None:
    _library(tmp_path, status="structure_sampled")
    assert select_template_design(tmp_path, "competition_defense", _expression())["status"] == "no_matching_verified_design"
    assert select_template_design(tmp_path, "competition_defense", _expression("advisory_only"))["status"] == "out_of_scope"
    path = tmp_path / "模板库/DESIGN-LANGUAGE.json"
    payload = json.loads(path.read_text())
    payload["designs"][0]["review_status"] = "visual_verified"
    payload["designs"][0]["design_tokens"]["body_px"] = 10
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert select_template_design(tmp_path, "competition_defense", _expression())["status"] == "advisory_only"


def test_advisory_design_is_callable_by_page_role_without_changing_type_scale(tmp_path: Path, capsys) -> None:
    source, row = _library(tmp_path, status="visual_observed_with_font_limit")
    result = select_template_design(tmp_path, "competition_defense", _expression(), "清晰")
    assert result["status"] == "advisory_only" and result["selected"] is None
    assert set(result["slide_guidance"]) == {"slide-01", "slide-02"}
    assert result["slide_guidance"]["slide-02"]["design_id"] == row["id"]
    assert result["slide_guidance"]["slide-02"]["page_variant"] == "argument_grid"
    assert result["slide_guidance"]["slide-02"]["page_measurements"]["title_width_ratio"] == .42
    assert result["slide_guidance"]["slide-02"]["source_sha256_verified"] is True
    assert apply_template_design_tokens(style_profile(PresentationForm.COMPETITION_DEFENSE), result)["title_px"] == 68
    exit_code = template_library_main([
        "design-language", "--workspace", str(tmp_path), "--form", "competition_defense",
        "--pattern", "cover", "--pattern", "editorial_columns", "--style", "清晰",
    ])
    assert exit_code == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["status"] == "advisory_only"
    assert packet["slide_guidance"]["slide-01"]["source_path"] == row["source_path"]
    source.write_bytes(b"source changed")
    assert select_template_design(tmp_path, "competition_defense", _expression(), "清晰")["slide_guidance"] == {}


def test_unfamiliar_style_retains_page_guidance_without_imposing_tokens(tmp_path: Path) -> None:
    _library(tmp_path)
    result = select_template_design(tmp_path, "competition_defense", _expression(), "科技感、克制")
    assert result["status"] == "advisory_only"
    assert result["selected"] is None
    assert set(result["slide_guidance"]) == {"slide-01", "slide-02"}
    assert result["slide_guidance"]["slide-01"]["design_id"] == "competition-argument-grid"
    assert apply_template_design_tokens(style_profile(PresentationForm.COMPETITION_DEFENSE), result)["title_px"] == 68


def test_malformed_catalog_fields_do_not_break_planning(tmp_path: Path) -> None:
    _library(tmp_path)
    catalog = tmp_path / "模板库/DESIGN-LANGUAGE.json"
    payload = json.loads(catalog.read_text())
    payload["designs"][0]["style_tags"] = None
    catalog.write_text(json.dumps(payload), encoding="utf-8")
    result = select_template_design(tmp_path, "competition_defense", _expression(), "极简黑白")
    assert result["status"] == "advisory_only"
    assert set(result["slide_guidance"]) == {"slide-01", "slide-02"}
    assert select_template_design(tmp_path, "competition_defense", {
        "application": "baseline_new_composition", "slides": {"slide-01": {"pattern": {"bad": "value"}}},
    })["status"] == "invalid_plan"


def test_html_consumes_verified_design_and_reverts_after_source_change(tmp_path: Path, monkeypatch) -> None:
    from aippt_studio import config

    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path)
    source, _ = _library(tmp_path)
    job = create_job("Source-bound library design", job_id="template-design-html")
    base = style_profile(PresentationForm.COMPETITION_DEFENSE)
    base["application"] = "baseline_new_composition"
    expression = _expression()
    design = select_template_design(tmp_path, "competition_defense", expression, "清晰")
    packet = {**design, "form": "competition_defense", "style_text": "清晰", "base_profile": base}
    plan = DeckPlan(title="真实任务主题", slides=[
        SlideSpec(id="slide-01", title="真实任务主题", purpose="介绍", layout="cover", points=["本项目确有的材料"]),
        SlideSpec(id="slide-02", title="有来源的论点", purpose="说明", layout="content", points=["真实来源证据"]),
    ], planning_strategy={
        "presentation_intent": {"presentation_form": "competition_defense"},
        "visual_style_profile": apply_template_design_tokens(base, design),
        "expression_plan": expression, "template_design_language": packet,
    })
    write_json(job_dir(job.id) / "planning/deck-plan.json", plan)
    markup = write_deck_html(job.id).read_text(encoding="utf-8")
    assert "--page-title-size:76px" in markup
    assert "真实来源证据" in markup and "source deck only" not in markup
    receipt = json.loads((job_dir(job.id) / "build/template-design-resolution.json").read_text())
    assert receipt["status"] == "source_verified"

    source.write_bytes(b"updated")
    markup = write_deck_html(job.id, dirty_only=set()).read_text(encoding="utf-8")
    assert "--page-title-size:68px" in markup
    assert "--page-title-size:76px" not in markup
    assert json.loads((job_dir(job.id) / "build/template-design-resolution.json").read_text())["status"] == "suppressed_stale_or_changed_source"
    assert "--page-title-size:68px" in (job_dir(job.id) / "build/slides/slide-01.html").read_text()


def test_resolution_does_not_modify_a_reference_led_profile(tmp_path: Path) -> None:
    _library(tmp_path)
    profile = style_profile(PresentationForm.COMPETITION_DEFENSE)
    profile["application"] = "advisory_only"
    effective, status = resolve_planned_template_profile(tmp_path, {}, profile)
    assert status == "not_requested" and effective == profile


def test_real_planner_writes_advisory_page_guidance_for_new_composition(tmp_path: Path, monkeypatch) -> None:
    from aippt_studio import config

    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path)
    _library(tmp_path, status="visual_observed_with_font_limit")
    catalog = tmp_path / "模板库/DESIGN-LANGUAGE.json"
    payload = json.loads(catalog.read_text(encoding="utf-8"))
    payload["designs"][0]["forms"] = [form.value for form in PresentationForm]
    payload["designs"][0]["patterns"] = ["structured_rows"]
    payload["designs"][0]["reviewed_pages"] = [1]
    catalog.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    job = create_job("真实材料的两页分析", job_id="template-design-planner")
    facts = [
        f"第{index}项现场核验工作已经完成，完整记录已交由项目责任团队复核并归档保存。"
        for index in range(1, 5)
    ]
    source = tmp_path / "source.md"
    source.write_text("制作2页PPT\n" + "\n".join(facts), encoding="utf-8")
    ingest_sources(job.id, [str(source)])
    create_brief(job.id)
    plan = create_deck_plan(job.id)

    packet = plan.planning_strategy["template_design_language"]
    assert packet["status"] == "advisory_only"
    assert packet["source_facts_reusable"] is False and packet["source_media_reusable"] is False
    assert set(packet["slide_guidance"]) == {slide.id for slide in plan.slides}
    assert (job_dir(job.id) / "planning/template-design-language.json").is_file()
    assert plan.planning_strategy["visual_style_profile"]["profile_id"] != "template-design:competition-argument-grid"
