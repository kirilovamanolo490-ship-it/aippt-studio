from __future__ import annotations

import pytest
from pydantic import ValidationError

from aippt_studio.models import (
    DeckPlan, JobBrief, PageCountError, SlideSpec, select_prototype_slides,
    validate_plan_page_count,
)


def _slide(slide_id: str) -> SlideSpec:
    return SlideSpec(id=slide_id, title=slide_id, purpose="schema", layout="content")


def test_deck_plan_requires_unique_slide_ids() -> None:
    slides = [_slide(f"slide-{index:02d}") for index in range(1, 8)] + [_slide("slide-01")]

    with pytest.raises(ValidationError, match="slide ids must be unique"):
        DeckPlan(title="duplicate", slides=slides)


@pytest.mark.parametrize("count", [0, 201])
def test_brief_and_deck_plan_share_bounded_page_contract(count: int) -> None:
    with pytest.raises(ValidationError, match="between 1 and 200"):
        JobBrief(goal="invalid length", target_pages=count)
    with pytest.raises(ValidationError, match="between 1 and 200"):
        DeckPlan(title="invalid length", slides=[_slide(f"slide-{index:02d}") for index in range(1, count + 1)])


@pytest.mark.parametrize("count", [1, 2, 4, 7, 8, 20, 21, 30, 31, 200])
def test_explicit_short_and_long_decks_are_valid_without_changing_prototypes(count: int) -> None:
    brief = JobBrief(goal="explicit length", target_pages=count, target_pages_explicit=True)
    plan = DeckPlan(title="explicit length", slides=[_slide(f"slide-{index:02d}") for index in range(1, count + 1)])
    validate_plan_page_count(brief, plan)
    assert len(select_prototype_slides(plan)) == min(3, count)


@pytest.mark.parametrize("count", [True, False, 1.5])
def test_page_target_rejects_noninteger_input(count: object) -> None:
    with pytest.raises(ValidationError):
        JobBrief(goal="invalid type", target_pages=count)


def test_legacy_default_remains_flexible_but_explicit_ten_is_exact() -> None:
    plan = DeckPlan(title="nine slides", slides=[_slide(f"slide-{index:02d}") for index in range(1, 10)])
    brief = JobBrief(goal="default")
    assert brief.target_pages == 10 and brief.target_pages_explicit is False
    validate_plan_page_count(brief, plan)
    with pytest.raises(PageCountError, match="明确要求 10 页"):
        validate_plan_page_count(JobBrief(goal="exact ten", target_pages=10, target_pages_explicit=True), plan)
    with pytest.raises(PageCountError, match="明确要求 4 页"):
        validate_plan_page_count(JobBrief(goal="older authored brief", target_pages=4), plan)


def test_short_deck_keeps_explicit_prototype_selection() -> None:
    plan = DeckPlan(title="four slides", slides=[_slide(f"s{i}") for i in range(4)], prototype_slide_ids=["s3", "s1"])
    assert [s.id for s in select_prototype_slides(plan)] == ["s3", "s1"]
