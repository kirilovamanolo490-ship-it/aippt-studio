from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from ..models import ChangeMode, JobBrief, PresentationForm, VisualSystemMode


class PlanningDecision(BaseModel):
    strategy: str
    title_style: str
    visual_direction: str
    principles: list[str] = Field(default_factory=list)
    execution_directives: list[str] = Field(default_factory=list)
    change_mode: ChangeMode = ChangeMode.UNCLASSIFIED
    presentation_form: PresentationForm = PresentationForm.GENERAL
    visual_system: VisualSystemMode = VisualSystemMode.DERIVE_FROM_REFERENCE
    source: str = "studio"


class PlanningStrategy(ABC):
    name: str

    @abstractmethod
    def decide(self, brief: JobBrief) -> PlanningDecision:
        raise NotImplementedError
