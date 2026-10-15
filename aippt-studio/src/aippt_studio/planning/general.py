from __future__ import annotations

from ..models import JobBrief
from .base import PlanningDecision, PlanningStrategy


class GeneralPlanningStrategy(PlanningStrategy):
    name = "general"

    def decide(self, brief: JobBrief) -> PlanningDecision:
        intent = brief.presentation_intent
        return PlanningDecision(
            strategy=self.name,
            title_style="message-led",
            visual_direction=brief.style_direction or "清晰、克制、以一个主要视觉重心组织页面的现代专业风格",
            principles=["one-message-per-slide", "evidence-traceable", "audience-facing-copy"],
            change_mode=intent.change_mode,
            presentation_form=intent.presentation_form,
            visual_system=intent.visual_system,
        )
