"""Native renderer revalidates selected template design source bytes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aippt_studio import native_scene
from aippt_studio.models import DeckPlan, PresentationForm, SlideSpec
from aippt_studio.style_profiles import style_profile
from aippt_studio.template_designs import apply_template_design_tokens, select_template_design


def _plan_for_reviewed_design(tmp_path: Path, monkeypatch) -> tuple[DeckPlan, Path]:
    monkeypatch.setattr(native_scene, "WORKSPACE_ROOT", tmp_path)
    source = tmp_path / "模板库" / "竞赛与答辩" / "reviewed.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"reviewed template bytes")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    catalog = {
        "schema_version": 1,
        "designs": [{
            "id": "competition-measured",
            "source_path": "模板库/竞赛与答辩/reviewed.pptx",
            "source_sha256": source_hash,
            "review_status": "visual_verified",
            "reviewed_pages": [2],
            "forms": ["competition_defense"],
            "patterns": ["statement"],
            "style_tags": ["bold"],
            "design_tokens": {
                "page_inset_x": 106,
                "title_px": 74,
                "body_px": 48,
                "caption_px": 27,
            },
        }],
    }
    (tmp_path / "模板库" / "DESIGN-LANGUAGE.json").write_text(
        json.dumps(catalog, ensure_ascii=False), encoding="utf-8"
    )
    expression = {
        "application": "baseline_new_composition",
        "slides": {"slide-01": {"pattern": "statement"}},
    }
    base = style_profile(PresentationForm.COMPETITION_DEFENSE)
    base["application"] = "baseline_new_composition"
    selected = select_template_design(tmp_path, "competition_defense", expression, "bold")
    packet = {
        **selected,
        "form": "competition_defense",
        "style_text": "bold",
        "base_profile": base,
    }
    strategy = {
        "expression_plan": expression,
        "template_design_language": packet,
        "visual_style_profile": apply_template_design_tokens(base, selected),
    }
    plan = DeckPlan(
        title="Competition deck",
        slides=[SlideSpec(id="slide-01", title="Claim", purpose="Opening claim", layout="content")],
        planning_strategy=strategy,
    )
    assert selected["status"] == "ready"
    return plan, source


def test_native_authoring_settings_revalidates_and_applies_current_template_design(tmp_path, monkeypatch):
    plan, _source = _plan_for_reviewed_design(tmp_path, monkeypatch)

    settings = native_scene.native_authoring_settings({}, plan)

    assert settings["profile_id"] == "template-design:competition-measured"
    assert settings["title_font_pt"] == 37
    assert settings["body_font_pt"] == 24


def test_native_authoring_settings_falls_back_when_template_source_hash_changes(tmp_path, monkeypatch):
    plan, source = _plan_for_reviewed_design(tmp_path, monkeypatch)
    source.write_bytes(source.read_bytes() + b" changed")

    settings = native_scene.native_authoring_settings({}, plan)

    assert settings["profile_id"] == "competition-argument-v1"
    assert settings["title_font_pt"] == 34
    assert settings["body_font_pt"] == 23
