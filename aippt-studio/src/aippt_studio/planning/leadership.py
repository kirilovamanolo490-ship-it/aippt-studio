from __future__ import annotations

from ..models import JobBrief
from .base import PlanningDecision, PlanningStrategy


class LeadershipPlanningStrategy(PlanningStrategy):
    name = "leadership"

    def decide(self, brief: JobBrief) -> PlanningDecision:
        intent = brief.presentation_intent
        return PlanningDecision(
            strategy=self.name,
            title_style="conclusion-led",
            visual_direction=brief.style_direction or "结论先行、证据靠前、适合管理层快速决策的克制商务风格",
            principles=["conclusion-first", "evidence-first", "decision-and-owner", "no-universal-color-style"],
            execution_directives=["conclusion-evidence-action", "do-not-assume-consulting-template"],
            change_mode=intent.change_mode,
            presentation_form=intent.presentation_form,
            visual_system=intent.visual_system,
            source="studio:leadership-reference",
        )
