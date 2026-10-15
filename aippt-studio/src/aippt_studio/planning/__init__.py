from __future__ import annotations

from ..models import JobBrief, PresentationForm
from .base import PlanningDecision, PlanningStrategy
from .forms import PresentationFormPlanningStrategy
from .general import GeneralPlanningStrategy
from .leadership import LeadershipPlanningStrategy


def select_planning_strategy(brief: JobBrief) -> PlanningStrategy:
    if brief.presentation_intent.presentation_form != PresentationForm.GENERAL:
        return PresentationFormPlanningStrategy(brief.presentation_intent.presentation_form)
    leadership_markers = ("管理层", "领导", "董事会", "高管", "决策者", "executive", "board")
    if brief.audience_profile == "leadership" or any(marker.lower() in brief.audience.lower() for marker in leadership_markers):
        return LeadershipPlanningStrategy()
    return GeneralPlanningStrategy()


def planning_decision(brief: JobBrief) -> PlanningDecision:
    return select_planning_strategy(brief).decide(brief)


__all__ = ["PlanningDecision", "PlanningStrategy", "planning_decision", "select_planning_strategy"]
