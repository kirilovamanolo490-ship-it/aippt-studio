"""Content-bound page-expression choices for an untemplated new deck.

The planner chooses a page silhouette only from visible source content, asset
roles and deck context. It never writes explanatory labels onto slides. A
chosen pattern remains a reviewable part of the deck plan; actual fit and
visual meaning still require page inspection.
"""
from __future__ import annotations

import re

from .models import PresentationForm, SlideSpec
from .presentation_intent import _positive_mentions


_COMPARISON = re.compile(r"对比|比较|差异|前后|优劣|两种|两类|\bvs\b|\bversus\b", re.I)
_SEQUENCE = re.compile(r"步骤|流程|顺序|阶段|第一步|第二步|先.{0,24}(?:再|后)|随后|最终")


def _visible_points(slide: SlideSpec) -> list[str]:
    """Count the lines that are actually distinct from the visible title."""
    def normalized(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip(" \t\r\n。！？!?；;，,：:")

    points = list(slide.points)
    if points and normalized(points[0]) == normalized(slide.title):
        return points[1:]
    return points


def select_expression_plan(slides: list[SlideSpec], form: PresentationForm, *, style_text: str = "",
                           cinematic_eligible_refs: set[str] | None = None) -> dict:
    """Select a small set of distinct, source-compatible visual structures.

    This is a baseline for NEW_COMPOSITION + CREATE_NEW only. It deliberately
    avoids inventing a cover, labels, facts, or comparative relation. A person
    can choose a different pattern when the actual reference or page message
    warrants it, and that choice can be inspected from the persisted plan.
    """
    choices: dict[str, dict] = {}
    cinematic = _positive_mentions(style_text, ("电影感", "电影级", "电影风", "大片感", "cinematic", "film-like"))
    prior_pattern = ""
    repeated = 0
    image_pages = 0
    cinematic_hero_used = False
    for slide in slides:
        visible = _visible_points(slide)
        title_already_states_first_point = len(visible) < len(slide.points)
        has_image = bool(slide.asset_refs)
        if (cinematic and not cinematic_hero_used and len(slide.asset_refs) == 1
                and (cinematic_eligible_refs is None or slide.asset_refs[0] in cinematic_eligible_refs)
                and len(visible) <= 1 and slide.layout in {"cover", "content", "evidence"}):
            image_pages += 1
            cinematic_hero_used = True
            pattern, reason = "cinematic_still", "An explicitly requested cinematic direction and one source visual support a single large frame."
        elif slide.layout == "cover":
            pattern, reason = "cover", "Opening is already an explicit page role."
        elif slide.layout == "summary":
            pattern, reason = "overview", "Overview uses the form-specific page-role grammar."
        elif slide.layout == "conclusion":
            pattern, reason = "closing", "Closing is grounded in terminal source material."
        elif has_image:
            image_pages += 1
            # Only consecutive media pages switch the relative placement. A
            # source/reference-led composition is handled by a different route.
            side = "left" if prior_pattern == "image_right" else "right"
            pattern, reason = f"image_{side}", "A source-bound visual and its explanation share one page."
        elif len(visible) == 1 and not title_already_states_first_point:
            pattern, reason = "statement", "One distinct source statement carries the page."
        elif len(visible) == 2 and _COMPARISON.search(" ".join([slide.title, *visible])):
            pattern, reason = "comparison", "The visible source explicitly asks for a comparison."
        elif len(visible) >= 2 and _SEQUENCE.search(" ".join([slide.title, *visible])):
            pattern, reason = "sequence", "The visible source states an actual order or process."
        elif len(visible) in {2, 3} and slide.layout != "evidence" and form in {
            PresentationForm.GENERAL, PresentationForm.BUSINESS_REPORT,
            PresentationForm.COMPETITION_DEFENSE, PresentationForm.PREMIUM_BRAND_PORTFOLIO,
        }:
            pattern, reason = "editorial_columns", "Two or three source points share a wider editorial field without implying a new relation."
        else:
            pattern, reason = "structured_rows", "Keep independent source facts in reading order."
        repeated = repeated + 1 if pattern == prior_pattern else 1
        choices[slide.id] = {
            "pattern": pattern,
            "basis": reason,
            "visible_source_points": len(visible),
            "source_visual_count": len(slide.asset_refs),
            "review": "Inspect repeated page silhouettes and actual page fit." if repeated >= 3 else "Inspect actual page fit and meaning.",
        }
        prior_pattern = pattern
    scope = "single_page" if len(slides) == 1 else "two_page" if len(slides) == 2 else "multi_page"
    return {
        "schema_version": 1,
        "application": "baseline_new_composition",
        "form": form.value,
        "deck_scope": scope,
        "rule": "A page message, source evidence and image role choose the pattern; no generic cover or closing is inserted to fill a page count.",
        "image_page_count": image_pages,
        "cinematic_direction_requested": cinematic,
        "cinematic_hero_count": int(cinematic_hero_used),
        "cinematic_visual_gap": cinematic and not cinematic_hero_used,
        "slides": choices,
    }
