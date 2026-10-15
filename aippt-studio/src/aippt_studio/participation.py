"""Task-specific resource retrieval, separate from verified execution receipts."""
from __future__ import annotations

import re
from pathlib import Path

from .models import JobBrief, PresentationForm
from .storage import write_json
from .upstream_tools import read_resource, tool_catalog


_STYLES = [
    # A teaching occasion outranks the technical topic being taught. This only
    # retrieves a reference; it does not select a mandatory visual template.
    ("workshop-teaching", ("教学", "培训", "课程", "课堂", "科普", "workshop", "teaching", "classroom")),
    ("academic-research", ("学术", "论文", "答辩", "academic", "research")),
    ("creative-pitch", ("创意", "广告", "creative", "campaign")),
    ("investor-pitch", ("融资", "路演", "fundraising", "investor")),
    ("product-launch", ("发布", "新品", "launch")),
    ("technical-deepdive", ("架构", "技术", "technical", "architecture")),
    ("consulting-decision", ("咨询", "决策汇报", "咨询决策", "consulting", "management consulting")),
]
_LEADERSHIP = ("管理层", "决策者", "领导", "董事会", "高管", "executive", "board")
_CHILD_AUDIENCE = ("儿童", "小学生", "小学", "幼儿", "中学生", "青少年", "children", "schoolchildren", "kids", "kindergarten")
_FORM_RESOURCE_STYLES: dict[PresentationForm, str] = {
    # These are the only form-to-resource mappings backed by a matching,
    # available reference family.  The rest deliberately remain resource-free
    # until a real provider exists; government, competition, portfolio, and
    # ceremonial work must not be silently relabelled as consulting or
    # academic just because their subject matter contains overlapping words.
    PresentationForm.TEACHING: "workshop-teaching",
    PresentationForm.ACADEMIC_DEFENSE: "academic-research",
    PresentationForm.TECHNICAL_EXPLANATION: "technical-deepdive",
}


def _mentions(text: str, markers: tuple[str, ...]) -> bool:
    text = text.lower()
    for marker in markers:
        for match in re.finditer(re.escape(marker), text):
            prefix = re.split(r"[，。；,;.!\n]", text[max(0, match.start() - 16):match.start()])[-1]
            if not re.search(r"(?:不要|不采用|不使用|不走|避免|不是|无需|非|\bno\b|\bnot\b|\bwithout\b).{0,10}$", prefix):
                return True
    return False


def _style(text: str) -> str | None:
    return next((name for name, markers in _STYLES if _mentions(text, (name, *markers))), None)


def _style_selection(brief: JobBrief) -> tuple[str | None, str]:
    if brief.style_direction.strip():
        # An unmapped explicit style remains the user's style. Do not fill its
        # missing catalog match with an unrelated keyword from the source topic.
        return _style(brief.style_direction), "style_direction"
    constraints = " ".join(brief.customer_constraints)
    if style := _style(constraints):
        return style, "customer_constraints"
    excluded = {name for name, markers in _STYLES
                if any(marker in constraints.lower() for marker in (name, *markers))
                and not _mentions(constraints, (name, *markers))}

    def candidate(style: str | None, basis: str) -> tuple[str | None, str]:
        return (None, "customer_constraints") if style in excluded else (style, basis)

    form = brief.presentation_intent.presentation_form
    if form != PresentationForm.GENERAL:
        return _FORM_RESOURCE_STYLES.get(form), f"presentation_intent:{form.value}"

    if brief.audience_profile == "leadership":
        return candidate("consulting-decision", "audience_profile")
    if brief.audience.strip():
        if _mentions(brief.audience, _CHILD_AUDIENCE) or _mentions(brief.audience, _STYLES[0][1]):
            return candidate("workshop-teaching", "audience")
        if _mentions(brief.audience, _LEADERSHIP):
            return candidate("consulting-decision", "audience")
    else:
        candidates = [row["text"] for row in brief.audience_candidates if row.get("text")]
        # Conflicting candidate audiences do not vote for one template. The
        # original labels remain available for interpretation in the brief.
        if candidates and all(_mentions(value, _CHILD_AUDIENCE) or _mentions(value, _STYLES[0][1]) for value in candidates):
            return candidate("workshop-teaching", "source_audience_candidate")
    style = _style(brief.goal)
    return candidate(style, "goal" if style else "unconfirmed")


def design_context(root: Path, brief: JobBrief) -> dict:
    style, basis = _style_selection(brief)
    selected = [("ppt-master", f"skills/ppt-master/templates/styles/{style}/templates/design_spec.md")] if style else []
    if brief.audience_profile == "leadership" or _mentions(brief.audience, _LEADERSHIP):
        selected.append(("editable-leadership-pptx", "references/executive-pptx-rules.md"))
    resources, unavailable = [], []
    for provider, path in selected:
        try:
            resources.append(read_resource(provider, path))
        except (OSError, ValueError) as error:
            unavailable.append({"provider": provider, "path": path, "reason": str(error)})
    modes = []
    for item in resources:
        match = re.search(r"\*\*Preferred Mode\*\*:\s*([^\n]+)", item["content"])
        if match:
            modes.append(match.group(1).strip())
    audience_status = "explicit" if brief.audience.strip() or brief.audience_profile == "leadership" else "source_candidate" if brief.audience_candidates else "unconfirmed"
    packet = {"schema_version": 1, "style_candidate": style, "selection_basis": basis,
              "audience_status": audience_status, "reference_only": True, "preferred_modes": modes,
              "resources": resources, "unavailable": unavailable,
              "providers": tool_catalog(),
              "execution": {
                  "selected_references": [{key: item[key] for key in ("provider", "path", "sha256", "commit_sha")} for item in resources],
                  "executed_operations": [],
                  "claim_boundary": "Selecting or reading these references does not execute an upstream engine. Execution is recorded only by a Work-local receipt or the route adapter manifest.",
              },
              "selection_rule": "Explicit customer direction wins; retrieved references require task-specific interpretation.",
              "execution_rule": "A selected resource is not an executed engine; only successful call receipts prove execution."}
    write_json(root / "planning" / "upstream-context.json", packet)
    # This compact record enters the approved deck plan; full resources stay out
    # of speaker notes and user-facing slides.
    return {"style_candidate": style, "selection_basis": basis, "audience_status": audience_status,
            "reference_only": True, "preferred_modes": modes,
            "resources": [{k: item[k] for k in ("provider", "path", "sha256", "commit_sha")} for item in resources],
            "unavailable": unavailable, "executed_operations": [], "context_path": "planning/upstream-context.json"}
