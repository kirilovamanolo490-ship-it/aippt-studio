from __future__ import annotations

from aippt_studio.models import JobBrief
from aippt_studio.planning import planning_decision


def test_leadership_strategy_is_conclusion_and_evidence_led() -> None:
    decision = planning_decision(JobBrief(goal="经营复盘", audience="集团管理层", audience_profile="leadership"))
    assert decision.strategy == "leadership"
    assert decision.title_style == "conclusion-led"
    assert "evidence-first" in decision.principles
    assert "蓝" not in decision.visual_direction


def test_explicit_style_overrides_profile_default() -> None:
    decision = planning_decision(JobBrief(goal="经营复盘", audience_profile="leadership", style_direction="温暖的杂志编辑风"))
    assert decision.visual_direction == "温暖的杂志编辑风"

