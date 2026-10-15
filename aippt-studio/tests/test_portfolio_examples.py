from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aippt_studio.portfolio_examples import select_portfolio_examples
from aippt_studio.html_deck import write_deck_html
from aippt_studio.models import DeckPlan, PresentationForm, SlideSpec, SourceRecord
from aippt_studio.storage import job_dir, write_json
from aippt_studio.style_profiles import style_profile
from aippt_studio.workflow import create_job


def _catalog(root: Path, *, source_path: str, source_sha256: str,
             render_recipe: str | None = None) -> Path:
    path = root / "模板库/作品集/EXPRESSION-EXAMPLES.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    example = {"id": "example-one", "source_path": source_path,
               "source_sha256": source_sha256, "page": 3,
               "forms": ["competition_defense"],
               "patterns": ["cover" if render_recipe else "sequence"],
               "composition_learning": "Show actual order.",
               "inspect_before_use": "Inspect the page and replace facts."}
    if render_recipe:
        example["render_recipe"] = render_recipe
    path.write_text(json.dumps({"schema_version": 1, "review_boundary": "Approximate render only.",
                                "examples": [example]}), encoding="utf-8")
    return path


def _plan(form: str = "competition_defense") -> dict:
    return {"application": "baseline_new_composition", "form": form,
            "slides": {"slide-01": {"pattern": "sequence"},
                       "slide-02": {"pattern": "sequence"}}}


def test_selects_only_sha_verified_local_composition_observation(tmp_path: Path) -> None:
    source = tmp_path / "模板库/作品集/example.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source deck bytes")
    locator = "模板库/作品集/example.pptx"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    _catalog(tmp_path, source_path=locator, source_sha256=digest)

    selected = select_portfolio_examples(tmp_path, _plan())

    assert selected["status"] == "ready"
    assert selected["examples"]["example-one"]["source_sha256"] == digest
    assert selected["slide_candidates"] == {"slide-01": ["example-one"], "slide-02": ["example-one"]}
    assert selected["source_facts_reusable"] is False
    assert selected["source_media_reusable"] is False
    assert selected["inspection"] == "rendered_advisory"
    assert select_portfolio_examples(tmp_path, _plan("government_briefing"))["status"] == "no_matching_example"


def test_root_expression_index_is_preferred_and_legacy_path_remains_supported(tmp_path: Path) -> None:
    source = tmp_path / "模板库/作品集/example.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source deck bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    legacy = _catalog(tmp_path, source_path="模板库/作品集/example.pptx", source_sha256=digest)
    current = tmp_path / "模板库/EXPRESSION-EXAMPLES.json"
    current.write_text(legacy.read_text(encoding="utf-8").replace("Show actual order.", "Root index wins."),
                       encoding="utf-8")
    selected = select_portfolio_examples(tmp_path, _plan())
    assert selected["examples"]["example-one"]["composition_learning"] == "Root index wins."

    current.unlink()
    selected = select_portfolio_examples(tmp_path, _plan())
    assert selected["examples"]["example-one"]["composition_learning"] == "Show actual order."


def test_stale_or_external_example_is_suppressed(tmp_path: Path) -> None:
    source = tmp_path / "模板库/作品集/example.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"changed deck")
    _catalog(tmp_path, source_path="模板库/作品集/example.pptx", source_sha256="0" * 64)
    stale = select_portfolio_examples(tmp_path, _plan())
    assert stale["status"] == "no_matching_example"
    assert stale["skipped"] == [{"id": "example-one", "reason": "source_sha256_mismatch"}]

    outside = tmp_path / "outside.pptx"
    outside.write_bytes(b"outside library")
    _catalog(tmp_path, source_path="模板库/../outside.pptx",
             source_sha256=hashlib.sha256(outside.read_bytes()).hexdigest())
    external = select_portfolio_examples(tmp_path, _plan())
    assert external["examples"] == {}
    assert external["skipped"] == [{"id": "example-one", "reason": "invalid_or_missing_source"}]


def test_reference_selection_does_not_override_template_or_reference_route(tmp_path: Path) -> None:
    result = select_portfolio_examples(tmp_path, {"application": "advisory_only", "form": "competition_defense",
                                                  "slides": {"slide-01": {"pattern": "cover"}}})
    assert result["status"] == "out_of_scope"
    assert result["slide_candidates"] == {}


def test_verified_cover_recipe_requires_matching_structure_and_source_bytes(tmp_path: Path) -> None:
    source = tmp_path / "模板库/作品集/example.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source deck bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    _catalog(tmp_path, source_path="模板库/作品集/example.pptx", source_sha256=digest,
             render_recipe="competition_photo_cover")
    plan = {"application": "baseline_new_composition", "form": "competition_defense",
            "slides": {"cover": {"pattern": "cover", "source_visual_count": 1,
                                 "visible_source_points": 2}}}
    selected = select_portfolio_examples(tmp_path, plan)
    assert selected["examples"]["example-one"]["advisory_only"] is False
    assert selected["slide_recipes"] == {"cover": {"recipe": "competition_photo_cover",
                                                  "example_id": "example-one", "source_sha256": digest}}
    plan["slides"]["cover"]["source_visual_count"] = 0
    advisory = select_portfolio_examples(tmp_path, plan)
    assert advisory["slide_recipes"] == {}
    assert advisory["examples"]["example-one"]["advisory_only"] is True
    plan["slides"]["cover"]["source_visual_count"] = 1
    source.write_bytes(b"changed")
    assert select_portfolio_examples(tmp_path, plan)["slide_recipes"] == {}


def test_verified_recipe_reaches_html_and_stale_reference_is_suppressed(tmp_path: Path, monkeypatch) -> None:
    from aippt_studio import config

    monkeypatch.setattr(config, "WORKSPACE_ROOT", tmp_path)
    source = tmp_path / "模板库/作品集/example.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"reviewed source")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    _catalog(tmp_path, source_path="模板库/作品集/example.pptx", source_sha256=digest,
             render_recipe="competition_photo_cover")
    job = create_job("版式案例消费", job_id="portfolio-recipe-integration")
    root = job_dir(job.id)
    image = root / "sources" / "photo.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"png")
    write_json(root / "normalized/sources/photo.json",
               SourceRecord(id="photo", original="photo.png", stored_path="sources/photo.png",
                            kind="image", sha256="a" * 64, size_bytes=3))
    expression = {"schema_version": 1, "application": "baseline_new_composition",
                  "form": "competition_defense", "slides": {
                      "slide-01": {"pattern": "cover", "source_visual_count": 1,
                                   "visible_source_points": 1}}}
    selected = select_portfolio_examples(tmp_path, expression)
    profile = style_profile(PresentationForm.COMPETITION_DEFENSE)
    profile["application"] = "baseline_new_composition"
    plan = DeckPlan(title="真实项目", slides=[SlideSpec(id="slide-01", title="真实项目",
                     purpose="设计说明", layout="cover", points=["真实参赛信息"], asset_refs=["photo"])],
                    planning_strategy={"presentation_intent": {"presentation_form": "competition_defense"},
                                       "expression_plan": expression, "visual_style_profile": profile,
                                       "portfolio_expression_examples": {"slide_recipes": selected["slide_recipes"]}})
    write_json(root / "planning/deck-plan.json", plan)
    markup = write_deck_html(job.id).read_text(encoding="utf-8")
    assert 'data-composition-recipe="competition_photo_cover"' in markup
    assert markup.count("真实参赛信息") == 1
    resolution = json.loads((root / "build/portfolio-recipe-resolution.json").read_text())
    assert resolution["status"] == "applied_to_rendered_slides"
    assert resolution["applied_slide_ids"] == ["slide-01"]
    assert resolution["stage"] == "html_generation" and resolution["pptx_export_verified"] is False
    assert resolution["html_sha256"] == hashlib.sha256((root / "build/deck.html").read_bytes()).hexdigest()

    job_path = root / "job.json"
    original_job = job_path.read_bytes()
    job_path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError):
        write_deck_html(job.id)
    resolution = json.loads((root / "build/portfolio-recipe-resolution.json").read_text())
    assert resolution["status"] == "in_progress" and resolution["applied_slide_ids"] == []
    job_path.write_bytes(original_job)

    plan.slides[0].asset_geometry = {"missing-photo": {"fit": "contain", "mask": "rect"}}
    write_json(root / "planning/deck-plan.json", plan)
    with pytest.raises(ValueError, match="asset_geometry must name an exact asset_ref"):
        write_deck_html(job.id)
    resolution = json.loads((root / "build/portfolio-recipe-resolution.json").read_text())
    assert resolution["status"] == "in_progress" and resolution["applied_slide_ids"] == []
    plan.slides[0].asset_geometry = {}
    write_json(root / "planning/deck-plan.json", plan)

    write_json(root / "normalized/sources/photo.json",
               SourceRecord(id="photo", original="photo.png", stored_path="sources/photo.png",
                            kind="icon", sha256="a" * 64, size_bytes=3))
    markup = write_deck_html(job.id, dirty_only=set()).read_text(encoding="utf-8")
    assert 'data-composition-recipe=""' in markup
    resolution = json.loads((root / "build/portfolio-recipe-resolution.json").read_text())
    assert resolution["status"] == "source_verified_but_slide_incompatible"
    assert resolution["not_applied_slide_ids"] == ["slide-01"]

    source.write_bytes(b"changed source")
    markup = write_deck_html(job.id, dirty_only=set()).read_text(encoding="utf-8")
    assert 'data-composition-recipe=""' in markup
    assert 'data-composition-recipe=""' in (root / "build/slides/slide-01.html").read_text()
    assert json.loads((root / "build/portfolio-recipe-resolution.json").read_text())["status"] == "suppressed_stale_or_changed_source"
