"""Advisory, read-only design and reference context for a deck plan."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .models import DeckPlan, JobBrief


# Occasion vocabulary is shared by query construction and conflict checks.
# Keep these labels broad enough for catalog discovery; topic words come from
# the brief or an explicit author query rather than a curated industry list.
_FORM_LABELS: dict[str, tuple[str, ...]] = {
    "general": ("通用演示", "普通演示"),
    "government_briefing": ("政务汇报", "政府汇报", "机关汇报", "机构汇报", "党政工作汇报"),
    "business_report": ("工作汇报", "业务汇报", "经营分析", "述职", "年度总结", "阶段总结"),
    "competition_defense": ("竞赛答辩", "竞赛", "比赛", "大赛", "路演", "竞聘"),
    "academic_defense": ("学术答辩", "论文答辩", "毕业答辩", "学术汇报", "科研汇报"),
    "teaching": ("教学", "课程", "培训", "课堂", "科普"),
    "technical_explanation": ("技术说明", "技术方案", "技术交流", "架构说明", "系统说明"),
    "ceremonial_story": ("仪式", "庆典", "典礼", "发布会", "开场"),
    "premium_brand_portfolio": ("品牌展示", "品牌作品集", "作品集", "产品画册"),
}
_COMPETITION_SUBTYPES = {
    "career": ("职业规划", "生涯规划", "职业发展"),
    "startup": ("创业竞赛", "创业大赛", "创新创业", "商业计划", "创赛"),
}
_COMPATIBLE_FORMS = {"government_briefing": {"business_report"},
                     "business_report": {"government_briefing"}}


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _whole_deck_query(form: str, brief: JobBrief, decision: dict) -> tuple[str, dict[str, Any]]:
    explicit = _text(decision.get("query"))
    form_labels = _FORM_LABELS.get(form, (form,))
    if explicit:
        return explicit, {"source": "explicit_query", "explicit_query": explicit,
                          "form_labels": list(form_labels), "topic_sources": []}
    topics = []
    for value in (brief.goal, brief.audience, *brief.must_include):
        value = _text(value)
        if value and value not in topics:
            topics.append(value)
    return " ".join((*form_labels, *topics)), {
        "source": "presentation_form_and_brief", "form_labels": list(form_labels),
        "topic_sources": ["goal", "audience", "must_include"],
    }


def _occasion_conflicts(row: dict, form: str, query: str) -> list[str]:
    tags = row.get("occasion_tags", [])
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, (tuple, list)):
        tags = []
    labels = " ".join([str(row.get("category") or ""),
                       *(x for x in tags if isinstance(x, str))])
    if form == "general" or not labels.strip():
        return []
    compatible = _COMPATIBLE_FORMS.get(form, set())
    known_forms = {key: values for key, values in _FORM_LABELS.items()
                   if key not in {"general", form, *compatible}}
    conflicts = [other for other, values in known_forms.items() if any(label in labels for label in values)]
    if form != "competition_defense":
        for subtype, words in _COMPETITION_SUBTYPES.items():
            if any(word in labels for word in words):
                conflicts.append(f"competition_{subtype}")
    # Competition subtypes share the broad competition form but represent
    # different judging questions and must not be substituted for one another.
    q = query.casefold()
    requested_subtype = next((name for name, words in _COMPETITION_SUBTYPES.items()
                              if any(word in q for word in words)), None)
    if form == "competition_defense" and requested_subtype:
        competing_subtype = "startup" if requested_subtype == "career" else "career"
        if any(word in labels for word in _COMPETITION_SUBTYPES[competing_subtype]):
            conflicts.append(f"competition_{competing_subtype}")
    # A candidate carrying an explicit incompatible occasion is excluded;
    # absent or ambiguous occasion metadata remains browseable.
    return conflicts


def _candidate_match_status(row: dict, form: str) -> str:
    """State how much occasion metadata supports a candidate without overstating it."""
    tags = row.get("occasion_tags", [])
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, (tuple, list)):
        tags = []
    values = {str(row.get("category") or ""), *(x for x in tags if isinstance(x, str))}
    values.discard("")
    labels = " ".join(values)
    if not labels or form == "general":
        return "unknown"
    own = _FORM_LABELS.get(form, (form,))
    if any(label in values for label in own):
        return "match"
    if any(label in labels for other in _COMPATIBLE_FORMS.get(form, set())
           for label in _FORM_LABELS.get(other, ()) if label in values):
        return "compatible"
    # Without an explicit conflict, incomplete or unfamiliar taxonomy is for
    # a person to judge; it is not evidence of compatibility.
    return "unknown"


def build_design_context(root: Path, plan: DeckPlan, brief: JobBrief, *, workspace_root: Path) -> dict:
    """Summarize available intent and discover relevant references; never claims use."""
    strategy = plan.planning_strategy if isinstance(plan.planning_strategy, dict) else {}
    intent = _mapping(strategy.get("narrative_intent"))
    page_design = _mapping(strategy.get("page_design"))
    missing = [key for key in ("task_specific_type", "audience_need", "core_message", "storyline", "evidence_boundary")
               if not _text(intent.get(key))]
    form = brief.presentation_intent.presentation_form.value
    audience = brief.audience or ("Leadership" if brief.audience_profile == "leadership" else "")
    result: dict[str, Any] = {
        "status": "advisory", "form": form, "audience": audience, "goal": brief.goal,
        "narrative_intent": {key: _text(intent.get(key)) for key in
            ("task_specific_type", "audience_need", "core_message", "storyline", "evidence_boundary")},
        "missing_narrative_fields": missing,
        "whole_deck_reference_decision": _mapping(strategy.get("whole_deck_reference_decision")),
        "whole_deck_references": {"status": "not_searched", "discovery_only": True, "candidates": []},
        "page_references": {},
        "claim_boundary": "Candidates are discovery only. A search hit or metadata declaration does not prove selection, adaptation, or use.",
    }

    library = Path(workspace_root) / "模板库"
    catalog = library / "CATALOG.jsonl"
    if catalog.is_file():
        try:
            from .template_library import search_catalog
            decision = result["whole_deck_reference_decision"]
            query, query_basis = _whole_deck_query(form, brief, decision)
            whole = search_catalog(catalog, query, limit=60, form=form)
            candidates, seen = [], set()
            for row in whole.get("results", []):
                count = row.get("page_count")
                if (str(row.get("path", "")).startswith("高阶文化/") or row.get("exists") is False
                        or row.get("asset_type", "template") in {"component", "组件", "page_component"}
                        or (isinstance(count, int) and count <= 2)):
                    continue
                if _occasion_conflicts(row, form, query):
                    continue
                identity = row.get("source_sha256") or row.get("path")
                if identity not in seen:
                    candidates.append({**row, "match_status": _candidate_match_status(row, form)})
                    seen.add(identity)
            errors = whole.get("catalog_errors", [])
            status = "degraded" if errors and candidates else "unavailable" if errors else "candidates_found" if candidates else "no_match"
            result["whole_deck_references"] = {"status": status, "discovery_only": True,
                                                 "query": query, "candidates": candidates[:4],
                                                 "query_basis": query_basis,
                                                 "catalog_errors": errors[:3],
                                                 "next": "Inspect the actual agenda and substantive pages. Same industry does not establish the same reporting purpose."}
        except Exception as exc:  # library outages must not block composition
            result["whole_deck_references"] = {"status": "unavailable", "discovery_only": True,
                                                "candidates": [], "reason": str(exc)}
    else:
        result["whole_deck_references"]["status"] = "catalog_unavailable"

    ga_path = library / "分析索引" / "高阶文化"
    cache = {}
    for slide in plan.slides:
        meta = _mapping(page_design.get(slide.id))
        categories = meta.get("layout_categories", [])
        if isinstance(categories, str):
            categories = [categories]
        if not isinstance(categories, list):
            categories = []
        categories = list(dict.fromkeys(c for c in categories if isinstance(c, str) and c.strip()))[:2]
        decision = _mapping(meta.get("reference_decision"))
        entry = {"page_job": _text(meta.get("page_job")),
                 "content_relationship": _text(meta.get("content_relationship")),
                 "layout_categories": categories,
                 "reference_decision": decision,
                 "status": "advisory_only", "candidates": []}
        entry["missing_fields"] = [key for key in ("page_job", "content_relationship") if not entry[key]]
        entry["selection_state"] = "declared_not_applied" if decision else "not_decided"
        if not categories:
            entry["status"] = "no_category_specified"
            entry["suggestion"] = "Specify a page-function category only when it helps this page; no category-wide scan was run."
        elif not ga_path.is_dir():
            entry["status"] = "library_unavailable"
        else:
            try:
                from .gaojie_layouts import search_gaojie_layouts
                query = " ".join(filter(None, (slide.title, slide.purpose, entry["page_job"], entry["content_relationship"])))
                for category in categories:
                    key = (category, query)
                    if key not in cache:
                        cache[key] = search_gaojie_layouts(library, query, category=category, limit=2)
                    entry["candidates"].append({"category": category, **cache[key]})
                entry["status"] = "discovered" if any(x.get("candidates") for x in entry["candidates"]) else "no_match"
            except Exception as exc:
                entry["status"] = "unavailable"
                entry["reason"] = str(exc)
        result["page_references"][slide.id] = entry
    return result
