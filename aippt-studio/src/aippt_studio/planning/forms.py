from __future__ import annotations

from ..models import ChangeMode, JobBrief, PresentationForm
from .base import PlanningDecision, PlanningStrategy


_FORM_RULES: dict[PresentationForm, dict[str, object]] = {
    PresentationForm.GOVERNMENT_BRIEFING: {
        "title_style": "fact-led",
        "visual_direction": "正式、克制，以事实、责任、区域和时序关系组织页面；依据已确认材料决定视觉系统，不自动套用党政色彩或装饰。",
        "principles": ["formal-evidence-first", "responsibility-and-timeline", "source-faithful-copy", "no-sales-or-consulting-clichés"],
    },
    PresentationForm.BUSINESS_REPORT: {
        "title_style": "conclusion-led",
        "visual_direction": "结论、证据、行动和责任关系清楚的正式汇报表达；避免把每页压成相同的咨询卡片。",
        "principles": ["conclusion-first", "evidence-and-action", "operational-context", "no-universal-card-layout"],
    },
    PresentationForm.COMPETITION_DEFENSE: {
        "title_style": "argument-led",
        "visual_direction": "围绕评审问题、方案、证据、比较和可行性形成完整论证；让创新点服务于真实材料。",
        "principles": ["question-method-evidence", "comparative-proof", "feasibility", "no-decorative-metrics"],
    },
    PresentationForm.ACADEMIC_DEFENSE: {
        "title_style": "claim-and-method-led",
        "visual_direction": "问题、方法、证据、贡献和边界清楚对应，保留准确术语、图像和数据口径。",
        "principles": ["problem-method-evidence", "claim-boundary", "source-integrity", "no-invented-conclusion"],
    },
    PresentationForm.TEACHING: {
        "title_style": "learning-step-led",
        "visual_direction": "按理解路径、示例、互动和认知负荷组织；每页只承担一个可被学习者理解的步骤。",
        "principles": ["learning-sequence", "concrete-example", "cognitive-load", "audience-appropriate-copy"],
    },
    PresentationForm.TECHNICAL_EXPLANATION: {
        "title_style": "mechanism-led",
        "visual_direction": "用概念、机制、证据和使用边界解释技术内容，避免用泛化装饰替代真实关系。",
        "principles": ["mechanism-and-evidence", "system-boundaries", "accurate-terms", "no-pseudo-dashboard"],
    },
    PresentationForm.CEREMONIAL_STORY: {
        "title_style": "narrative-led",
        "visual_direction": "通过身份感、情绪递进和视觉节奏建立连续叙事；每个视觉元素都服务于现场表达。",
        "principles": ["narrative-rhythm", "identity-and-occasion", "visual-continuity", "no-empty-decoration"],
    },
    PresentationForm.PREMIUM_BRAND_PORTFOLIO: {
        "title_style": "editorial-led",
        "visual_direction": "以真实作品、主视觉和编辑式层次组织；保留克制留白和素材完整性，不靠渐变、卡片或小字制造高级感。",
        "principles": ["real-work-hero", "editorial-hierarchy", "image-integrity", "no-faux-premium-ui"],
    },
}


class PresentationFormPlanningStrategy(PlanningStrategy):
    """Translate a presentation occasion into expression rules, not a template."""

    def __init__(self, form: PresentationForm) -> None:
        self.form = form
        self.name = form.value

    def decide(self, brief: JobBrief) -> PlanningDecision:
        intent = brief.presentation_intent
        rule = _FORM_RULES[self.form]
        directives = {
            ChangeMode.LOCAL_PATCH: ["preserve-non-target-pages", "use-hash-bound-local-edit", "do-not-reassemble-template-as-a-new-deck"],
            ChangeMode.TEMPLATE_RELAYOUT: ["declare-template-source-and-page-map", "preserve-confirmed-visual-system"],
            ChangeMode.NEW_COMPOSITION: ["create-page-roles-from-current-content", "do-not-claim-template-inheritance"],
            ChangeMode.FAITHFUL_RECONSTRUCTION: ["reconstruct-source-faithfully", "verify-editability-and-page-correspondence"],
            ChangeMode.VISUAL_ALTERNATE: ["record-image-based-acceptance", "do-not-claim-native-editability"],
            ChangeMode.UNCLASSIFIED: ["resolve-intent-before-claiming-a-specific-design-system"],
        }[intent.change_mode]
        return PlanningDecision(
            strategy=self.name,
            title_style=str(rule["title_style"]),
            visual_direction=brief.style_direction or str(rule["visual_direction"]),
            principles=list(rule["principles"]),
            execution_directives=directives,
            change_mode=intent.change_mode,
            presentation_form=self.form,
            visual_system=intent.visual_system,
            source=f"studio:presentation-form:{self.form.value}",
        )
