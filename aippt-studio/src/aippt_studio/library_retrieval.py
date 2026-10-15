"""Content, page-function, composition, hierarchy and sequence retrieval.

This module ranks verified library depth records without treating an OOXML
parse as a visual review.  It is intentionally independent from planner/CLI so
retrieval can be validated before being wired into task production.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .template_library import _library_root, _safe_asset_path, _as_values, required_form_from_query, occasion_compatible


_SCHEMA = "aippt.library-depth.v1"
_ROLE_ALIASES = {
    "封面": ("封面", "cover", "opening"), "cover": ("封面", "cover", "opening"),
    "background_problem": ("background_problem", "背景问题", "问题", "痛点", "problem", "context", "background"),
    "背景问题": ("background_problem", "背景问题", "问题", "痛点", "problem", "context", "background"),
    "问题证据": ("问题证据", "证据", "problem evidence", "evidence"),
    "method_approach": ("method_approach", "方法路线", "方法", "路线", "method", "approach", "roadmap"),
    "方法路线": ("method_approach", "方法路线", "方法", "路线", "method", "approach", "roadmap"),
    "evidence_results": ("evidence_results", "成果证据", "结果", "成果", "成效", "result", "impact", "evidence"),
    "成果证据": ("evidence_results", "成果证据", "结果", "成果", "成效", "result", "impact", "evidence"),
    "数据图表": ("数据图表", "图表", "指标", "统计", "chart", "metrics", "data"),
    "process_architecture": ("process_architecture", "架构流程", "架构", "系统架构", "流程", "architecture", "process"),
    "架构流程": ("process_architecture", "架构流程", "架构", "系统架构", "流程", "architecture", "process"),
    "方案设计": ("方案设计", "方案", "服务方案", "设计", "solution", "service design"),
    "timeline_plan": ("timeline_plan", "实施计划", "时间安排", "实施", "计划", "schedule", "implementation", "roadmap"),
    "实施计划": ("timeline_plan", "实施计划", "时间安排", "实施", "计划", "schedule", "implementation", "roadmap"),
    "budget_model": ("budget_model", "预算财务", "预算", "财务", "成本", "商业模式", "budget", "finance", "cost", "business model"),
    "预算财务": ("budget_model", "预算财务", "预算", "财务", "成本", "商业模式", "budget", "finance", "cost", "business model"),
    "team_organization": ("team_organization", "团队人物", "团队", "组织", "人物", "成员", "team", "organization", "people"),
    "团队人物": ("team_organization", "团队人物", "团队", "组织", "人物", "成员", "team", "organization", "people"),
    "agenda": ("agenda", "目录", "议程", "contents", "outline"),
    "目录": ("agenda", "目录", "议程", "contents", "outline"),
    "closing_outlook": ("closing_outlook", "总结计划", "总结", "展望", "结论", "closing", "conclusion", "outlook"),
    "总结计划": ("closing_outlook", "总结计划", "总结", "展望", "结论", "closing", "conclusion", "outlook"),
    "goal_positioning": ("goal_positioning", "项目定位", "目标定位", "目标", "定位", "愿景", "价值主张", "goal", "positioning", "vision"),
    "项目定位": ("goal_positioning", "项目定位", "目标定位", "目标", "定位", "愿景", "价值主张", "goal", "positioning", "vision"),
    "case_example": ("case_example", "案例作品", "案例", "项目案例", "实践案例", "case", "example"),
    "案例作品": ("case_example", "案例作品", "案例", "项目案例", "实践案例", "case", "example"),
    "比较": ("比较", "对比", "差异", "comparison", "compare"),
}
_CONCEPT_GROUPS = (
    ("社工", "社会工作", "social work", "social worker"),
    ("邻里", "社区", "邻居", "neighborhood", "community"),
    ("养老", "老年", "老人", "安养", "elder care", "aging", "senior care"),
    ("公益", "社会服务", "公共服务", "social service", "public service", "nonprofit"),
    ("竞赛", "比赛", "大赛", "参赛", "挑战杯", "competition", "contest", "challenge cup"),
    ("创新", "创新创业", "创业", "innovation", "startup", "entrepreneurship"),
    ("调研", "调查", "研究", "research", "survey", "study"),
    ("服务", "服务设计", "service", "service design"),
    ("证据", "成果", "成效", "指标", "evidence", "impact", "result", "metrics"),
)
# Subject groups are deliberately separate from page-function and occasion
# vocabulary.  They are extracted only from the request and indexed source
# slide/topic text; filenames, categories, and styles never count as subject
# evidence.  The listed fields are broad enough to expose obvious cross-field
# mismatches without pretending to classify every possible domain.
_SUBJECT_GROUPS = {
    "social_work": ("社工", "社会工作", "社会服务", "social work", "social service"),
    "community_context": ("社区", "邻里", "邻居", "community", "neighborhood"),
    "public_good": ("公益", "非营利", "社会公益", "nonprofit", "charity"),
    "social_research": ("社会调研", "社会研究", "社会科学", "社会调查", "social research", "social science"),
    "aging_care": ("养老", "安养", "老年", "老人", "长者", "elder care", "aging", "senior care"),
    "biomedical": ("中药", "中医药", "药材", "药物", "医药", "医疗", "临床", "医院", "药品", "dna条形码",
                   "医学", "医学赛道", "医创", "biomedical", "medicine", "medical", "healthcare", "pharmaceutical"),
    "agriculture": ("农业", "农产品", "乡村振兴", "种植", "农户", "农田", "水果", "果园", "果品",
                    "agriculture", "farming", "rural revitalization", "fruit", "orchard"),
    "retail_ecommerce": ("电商", "电子商务", "直播带货", "团购", "网店", "零售", "e-commerce", "online retail"),
    "transportation": ("交通", "智慧交通", "道路运输", "公共交通", "交通运输", "transport", "transportation", "mobility"),
    "energy_electricity": ("电力", "电网", "能源", "电力系统", "electricity", "energy", "power grid"),
    "robotics_engineering": ("无人机", "机器人", "高空清洁", "低空飞行", "robotics", "drone", "aerial cleaning"),
    "manufacturing": ("智能制造", "增材制造", "3d打印", "制造业", "鞋厂", "制造工艺", "smart manufacturing", "additive manufacturing"),
    "semiconductors_electronics": ("芯片", "半导体", "射频芯片", "流片", "集成电路", "晶圆", "semiconductor", "integrated circuit"),
    "chemical_industry": ("化工", "卤代", "催化", "化学反应", "化学工艺", "工业废水", "chemical process", "catalysis"),
    "environment_recycling": ("垃圾回收", "垃圾分类", "生态环保", "环境治理", "环境保护", "环保项目", "工业固废", "煤矸石", "循环经济", "废水治理", "recycling", "environmental protection"),
    "fossil_energy": ("煤炭", "煤矿", "煤炭开采", "燃煤", "煤矸石", "coal mining", "coal industry"),
    "education": ("教学", "课堂", "课程", "学生培养", "教育", "teaching", "education", "curriculum"),
    "career_planning": ("职业规划", "就业目标", "就业赛道", "职业发展", "目标岗位", "career planning", "employment track"),
    "driving_training": ("驾培", "驾校", "驾驶培训", "学车", "driving school", "driver training"),
    "tourism": ("旅游", "文旅", "景区", "乡村旅游", "慢游", "慢游者", "旅行", "旅居",
                "tourism", "travel", "traveler", "travel destination"),
    "finance": ("金融", "证券", "投资", "银行", "保险", "finance", "investment", "banking"),
}
_STOP = set("一个一种以及进行通过对于关于其中我们你们他们本项目项目相关使用要求需要制作设计展示介绍说明分析研究工作计划内容进行能够根据因为所以这个那个并且同时已经目前未来主要各项有关" )
_CONTRASTING_SUBJECT_GROUPS = {"social_research", "biomedical", "agriculture", "retail_ecommerce",
                               "transportation", "energy_electricity", "robotics_engineering", "manufacturing", "chemical_industry",
                               "semiconductors_electronics", "environment_recycling", "career_planning", "tourism"}
_CONTRASTING_SUBJECT_GROUPS.update({"fossil_energy", "driving_training"})
_OCCASION_MARKERS = {
    "competition_defense": ("竞赛", "比赛", "大赛", "参赛", "竞聘", "挑战杯", "competition", "contest"),
    "academic_defense": ("学术答辩", "论文答辩", "毕业答辩", "academic defense", "thesis defense"),
    "business_report": ("业务汇报", "工作汇报", "年度总结", "述职", "经营分析", "工作总结",
                        "business report", "annual review", "management report"),
    "government_briefing": ("党政", "政务", "政府汇报", "党政汇报", "政策汇报", "机构汇报",
                            "government briefing", "policy briefing", "public administration"),
    "teaching": ("教学", "培训", "课程", "课堂", "教案", "说课", "教育教学",
                 "teaching", "training", "course", "lesson"),
    "technical_explanation": ("技术说明", "技术汇报", "技术方案", "产品技术", "科技", "系统架构",
                              "technical explanation", "technical report", "engineering", "technology"),
    "ceremonial_story": ("活动", "典礼", "仪式", "庆典", "文化活动", "活动叙事",
                         "ceremonial story", "event", "ceremony", "celebration"),
    "premium_brand_portfolio": ("品牌", "作品集", "品牌展示", "产品发布", "视觉作品集",
                                "premium brand", "brand portfolio", "portfolio", "product launch"),
}


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        stream = path.open("r", encoding="utf-8-sig")
    except OSError as exc:
        return [], [{"line": 0, "error": f"index_unavailable: {exc}"}]
    with stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append({"line": line_no, "error": f"invalid_json: {exc.msg}"})
                continue
            if not isinstance(value, dict):
                errors.append({"line": line_no, "error": "row_must_be_object"})
                continue
            rows.append(value)
    return rows, errors


def _fold(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).casefold().strip()


def _has_subject_term(folded_text: str, term: str) -> bool:
    needle = _fold(term)
    if needle in folded_text:
        return True
    if re.fullmatch(r"[\u4e00-\u9fff]+", needle):
        compact = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", folded_text)
        return needle in compact
    return False


def _concept_expand(text: str) -> set[str]:
    folded = _fold(text)
    result = {folded} if folded else set()
    for group in _CONCEPT_GROUPS:
        if any(_fold(term) in folded for term in group):
            result.update(_fold(term) for term in group)
    return result


def _units(text: str) -> set[str]:
    folded = _fold(text)
    latin = set(re.findall(r"[a-z0-9]+", folded))
    compact = re.sub(r"[^\u4e00-\u9fff]", "", folded)
    chinese = {compact[i:i + 2] for i in range(max(0, len(compact) - 1))}
    chinese.update(char for char in compact if char not in _STOP)
    return latin | chinese | _concept_expand(folded)


def _content_score(query: str, candidate: str) -> tuple[float, list[str]]:
    q = _units(query)
    c = _units(candidate)
    if not q or not c:
        return 0.0, []
    common = sorted(q & c)
    # Query coverage is more useful than Jaccard for concise target messages.
    return min(1.0, len(common) / max(1, len(q)) * 2.0), common[:16]


def _subject_alignment(query: str, source: str, *, pages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compare request subjects with salient source subjects, discounting mentions.

    For a whole deck, a subject is salient when it appears on the cover or on
    at least two distinct body pages. An aggregate topic term or a one-page
    mention is retained as weak evidence, but cannot by itself establish fit.
    Single-page retrieval uses that page as the local context.
    """
    query_folded, source_folded = _fold(query), _fold(source)
    requested = sorted(name for name, terms in _SUBJECT_GROUPS.items()
                       if any(_has_subject_term(query_folded, term) for term in terms))
    observed = sorted(name for name, terms in _SUBJECT_GROUPS.items()
                      if any(_has_subject_term(source_folded, term) for term in terms))
    occurrence_pages: dict[str, list[int]] = {name: [] for name in _SUBJECT_GROUPS}
    cover_subjects: set[str] = set()
    if pages is None:
        salient = set(observed)
    else:
        for index, page in enumerate(pages):
            if not isinstance(page, dict):
                continue
            text = _fold(" ".join(str(page.get(key) or "") for key in ("title", "text_excerpt")))
            try:
                number = int(page.get("slide", index + 1))
            except (TypeError, ValueError):
                number = index + 1
            page_subjects = {name for name, terms in _SUBJECT_GROUPS.items()
                             if any(_has_subject_term(text, term) for term in terms)}
            for name in page_subjects:
                occurrence_pages[name].append(number)
            if number == 1 or index == 0:
                cover_subjects.update(page_subjects)
        repeated = {name for name, numbers in occurrence_pages.items() if len(set(numbers)) >= 2}
        salient = cover_subjects | repeated
    found = sorted(salient)
    incidental = sorted(set(observed) - salient)
    matched = sorted(set(requested) & set(found))
    conflicting = sorted((set(found) & _CONTRASTING_SUBJECT_GROUPS) - set(requested)) if requested else []
    context_only = (matched == ["community_context"]
                    and bool({"social_work", "aging_care"} & set(requested)))
    supporting_subjects = ["community_context"] if context_only else []
    if context_only:
        matched = []
    if not requested:
        score, status = 0.5, "no_request_subject_anchor"
    elif conflicting and matched:
        score, status = len(matched) / len(requested) * 0.25, "mixed_subject_conflict"
    elif conflicting:
        score, status = 0.0, "subject_conflict"
    elif matched:
        score = len(matched) / len(requested)
        status = "aligned" if score >= 1 else "partially_aligned"
    elif context_only:
        score, status = 0.2, "weak_context_only"
    elif found:
        score, status = 0.4, "cross_cutting_subject_only"
    elif incidental:
        score, status = 0.25, "incidental_only"
    else:
        score, status = 0.5, "source_subject_unresolved"
    return {"score": round(score, 4), "conflict_penalty": 0.08 if conflicting else 0.0,
            "status": status,
            "requested_subjects": requested, "source_subjects": found,
            "observed_subjects": observed, "incidental_subjects": incidental,
            "cover_subjects": sorted(cover_subjects),
            "repeated_body_subjects": sorted(name for name, numbers in occurrence_pages.items()
                                               if len(set(numbers)) >= 2),
            "subject_page_occurrences": {name: sorted(set(numbers)) for name, numbers in occurrence_pages.items() if numbers},
            "matched_subjects": matched,
            "supporting_subjects": supporting_subjects,
            "conflicting_subjects": conflicting,
            "basis": "request text; source cover or repeated distinct slide titles/text; filenames excluded"}


def _role_values(page: dict[str, Any]) -> list[tuple[str, float]]:
    raw = page.get("role_candidates", [])
    if isinstance(raw, str):
        raw = [raw]
    result = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                result.append((item, 1.0))
            elif isinstance(item, dict) and isinstance(item.get("role"), str):
                confidence = item.get("confidence", 1.0)
                if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                    result.append((item["role"], max(0.0, min(1.0, float(confidence)))))
    return result


def _role_terms(role: str) -> set[str]:
    folded = _fold(role)
    for key, aliases in _ROLE_ALIASES.items():
        if folded == _fold(key) or folded in {_fold(value) for value in aliases}:
            return {_fold(value) for value in aliases}
    return {folded} if folded else set()


def _role_score(target_roles: list[str], page: dict[str, Any]) -> tuple[float, list[str]]:
    candidate_roles = _role_values(page)
    if not target_roles or not candidate_roles:
        return 0.0, []
    matched: list[str] = []
    best = 0.0
    for wanted in target_roles:
        aliases = _role_terms(wanted)
        for candidate, confidence in candidate_roles:
            candidate_aliases = _role_terms(candidate)
            if aliases & candidate_aliases:
                best = max(best, confidence)
                matched.append(candidate)
    return best, list(dict.fromkeys(matched))


def _flatten(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_flatten(item) for item in value)
    if isinstance(value, dict):
        return " ".join(_flatten(item) for item in value.values())
    return ""


def _composition_score(target: dict[str, Any], page: dict[str, Any]) -> tuple[float, list[str]]:
    wanted = _flatten(target.get("composition") or target.get("layout") or target.get("structure"))
    actual = _flatten(page.get("composition"))
    score, evidence = _content_score(wanted, actual)
    return score, evidence


def _hierarchy_score(target: dict[str, Any], page: dict[str, Any]) -> tuple[float, list[str]]:
    wanted = _flatten(target.get("hierarchy"))
    actual = _flatten(page.get("hierarchy"))
    score, evidence = _content_score(wanted, actual)
    return score, evidence


def _target_query(target: dict[str, Any]) -> str:
    return " ".join(str(target.get(key) or "") for key in
                     ("title", "message", "text", "content", "purpose", "evidence", "page_role"))


def _target_roles(target: dict[str, Any]) -> tuple[list[str], str]:
    explicit = _as_values(target.get("role_candidates") or target.get("roles")
                          or target.get("page_role") or target.get("role"))
    if explicit:
        return explicit, "explicit_page_role"
    # Planner page_role strings are often audience-facing. Infer only from the
    # page title/message/purpose fields; arbitrary body copy must not create a
    # scene or page-function classification.
    cues = " ".join(str(target.get(key) or "") for key in ("title", "message", "purpose"))
    inferred = _request_roles(cues)
    return inferred, "title_message_purpose_inference" if inferred else "unresolved"


def _request_roles(request_text: str) -> list[str]:
    text = _fold(request_text)
    roles = []
    for key, aliases in _ROLE_ALIASES.items():
        if any(_fold(alias) in text for alias in aliases):
            roles.append(key)
    return list(dict.fromkeys(roles))


def _score_page(target: dict[str, Any], page: dict[str, Any]) -> dict[str, Any]:
    query = _target_query(target)
    candidate_text = " ".join((str(page.get("title") or ""), str(page.get("text_excerpt") or "")))
    content, content_evidence = _content_score(query, candidate_text)
    subject = _subject_alignment(query, candidate_text)
    target_roles, role_basis = _target_roles(target)
    role, role_evidence = _role_score(target_roles, page)
    if role == 0 and role_basis == "explicit_page_role":
        inferred = _request_roles(" ".join(str(target.get(key) or "") for key in ("title", "message", "purpose")))
        inferred_score, inferred_evidence = _role_score(inferred, page)
        if inferred_score > 0:
            target_roles, role_basis = inferred, "title_message_purpose_inference"
            role, role_evidence = inferred_score, inferred_evidence
    composition, composition_evidence = _composition_score(target, page)
    hierarchy, hierarchy_evidence = _hierarchy_score(target, page)
    # Keep scores visible and independent; sequence order is scored at deck level.
    total = (.35 * content + .10 * subject["score"] + .25 * role + .18 * composition
             + .12 * hierarchy - subject["conflict_penalty"])
    gaps = [name for name, value in (("content", content), ("page_function", role),
                                     ("composition", composition), ("hierarchy", hierarchy)) if value < .1]
    return {"slide": page.get("slide"), "title": page.get("title"), "score": round(total, 4),
            "scores": {"content": round(content, 4), "subject_alignment": subject["score"],
                       "page_function": round(role, 4),
                       "composition": round(composition, 4), "hierarchy": round(hierarchy, 4),
                       "sequence": 0.0},
            "evidence": {"content_terms": content_evidence, "subject_alignment": subject,
                         "page_roles": role_evidence,
                         "page_role_basis": role_basis, "target_roles": target_roles,
                         "composition_terms": composition_evidence, "hierarchy_terms": hierarchy_evidence},
            "gaps": gaps,
            "media_candidates": page.get("media_candidates", [])}


def _sequence_match(targets: list[dict[str, Any]], pages: list[dict[str, Any]]) -> tuple[float, list[dict[str, Any]]]:
    """Monotonic weighted alignment of target page functions to source pages."""
    if not targets or not pages:
        return 0.0, []
    n, m = len(targets), len(pages)
    scores = [[_score_page(target, page) for page in pages] for target in targets]
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    back: list[list[tuple[int, int, bool] | None]] = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            options = [(dp[i - 1][j], i - 1, j, False), (dp[i][j - 1], i, j - 1, False),
                       (dp[i - 1][j - 1] + scores[i - 1][j - 1]["score"], i - 1, j - 1, True)]
            value, pi, pj, matched = max(options, key=lambda item: item[0])
            dp[i][j] = value
            back[i][j] = (pi, pj, matched)
    matches = []
    i, j = n, m
    while i and j:
        state = back[i][j]
        if state is None:
            break
        pi, pj, matched = state
        if matched and scores[i - 1][j - 1]["score"] > 0:
            row = dict(scores[i - 1][j - 1])
            row["target_index"] = i - 1
            row["sequence_position"] = j - 1
            matches.append(row)
        i, j = pi, pj
    matches.reverse()
    return round(dp[n][m] / max(1, n), 4), matches


def _visual_status(record: dict[str, Any], catalog_row: dict[str, Any]) -> str:
    review = record.get("review") if isinstance(record.get("review"), dict) else {}
    value = review.get("visual_status") or catalog_row.get("visual_status")
    if value:
        return str(value)
    status = str(catalog_row.get("review_status") or record.get("status") or "")
    return status if "visual" in status else "not_visually_reviewed"


def _rights(record: dict[str, Any], catalog_row: dict[str, Any]) -> str:
    review = record.get("review") if isinstance(record.get("review"), dict) else {}
    return str(review.get("media_rights") or catalog_row.get("media_rights") or "unverified")


def _safe_candidates(catalog: Path, depth_index: Path) -> tuple[list[tuple[dict, dict]], list[dict]]:
    catalog_rows, catalog_errors = _read_jsonl(catalog)
    depth_rows, depth_errors = _read_jsonl(depth_index)
    root = _library_root(catalog)
    by_path = {row.get("path"): row for row in catalog_rows if isinstance(row.get("path"), str)}
    result = []
    for record in depth_rows:
        path = record.get("path")
        if record.get("schema") not in {_SCHEMA, "aippt.template-depth-index.v1"}:
            continue
        # New depth builders separate parse success from human visual review.
        # Older fixtures used status="parsed" for both concepts.
        parse_status = record.get("parse_status")
        if parse_status != "parsed" and record.get("status") != "parsed":
            continue
        safe = _safe_asset_path(root, path)
        if safe is None:
            continue
        rel = safe.relative_to(root.resolve()).as_posix()
        row = by_path.get(rel)
        if row is None:
            continue
        if record.get("expected_sha256") and row.get("source_sha256") and record["expected_sha256"] != row["source_sha256"]:
            continue
        digest = str(record.get("expected_sha256") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            continue
        if safe.is_file():
            stat = safe.stat()
            if record.get("source_bytes") is not None and stat.st_size != record["source_bytes"]:
                continue
            if record.get("mtime_ns") is not None and stat.st_mtime_ns != record["mtime_ns"]:
                continue
        if safe.is_file() and row.get("source_sha256"):
            if record.get("source_bytes") is None or record.get("mtime_ns") is None:
                actual = hashlib.sha256(safe.read_bytes()).hexdigest()
                if actual != digest:
                    continue
        result.append((record, row))
    return result, catalog_errors + depth_errors


def _occasion_filter(record: dict[str, Any], row: dict[str, Any], form: str | None) -> bool:
    if form is None:
        return True
    labels = {value.casefold() for value in _as_values(record.get("occasion_tags")) +
              _as_values(row.get("occasion_tags")) + [str(row.get("category") or "")]}
    if form in _OCCASION_MARKERS:
        markers = _OCCASION_MARKERS[form]
        return any(marker.casefold() in label for marker in markers for label in labels)
    # Explicit forms are hard filters when represented in either record.
    return any(_fold(form) in label for label in labels)


def _specific_contest_context_filter(request_text: str, record: dict[str, Any], row: dict[str, Any]) -> bool:
    """Require literal source evidence when a named competition track is requested."""
    path = str(record.get("path") or row.get("path") or "")
    early = " ".join(str(page.get(key) or "") for page in record.get("slides", [])[:3]
                     if isinstance(page, dict) for key in ("title", "text_excerpt"))
    context = " ".join((path, str(row.get("title") or ""), early,
                        " ".join(_as_values(row.get("occasion_tags"))))).casefold()
    career_request = any(term in request_text for term in ("职业生涯规划", "职业规划", "职规赛"))
    if career_request and not any(term in context for term in ("职业生涯规划", "职业规划", "职规赛", "生涯规划")):
        return False
    if career_request:
        professions = {
            "nursing": ("护理", "护士", "临床照护"),
            "accounting": ("会计", "财务", "注册会计"),
            "art_education": ("美育", "美术", "绘画", "艺术教育"),
            "social_work": ("社会工作", "社工"),
            "civil_engineering": ("土木工程", "土木建筑"),
            "electrical_engineering": ("电气工程", "电气学科"),
        }
        requested = {name for name, terms in professions.items() if any(term in request_text for term in terms)}
        observed = {name for name, terms in professions.items() if any(term in context for term in terms)}
        if requested and observed and not requested.intersection(observed):
            return False
    for marker in ("成长赛道", "就业赛道"):
        if marker in request_text and marker not in context:
            return False
    for marker in ("挑战杯", "社会调查", "红旅"):
        if marker in request_text and marker not in context:
            return False
    return True


def _asset_candidate(request_text: str, targets: list[dict[str, Any]], record: dict[str, Any],
                     row: dict[str, Any], media: dict[str, Any], page: dict[str, Any] | None,
                     *, target_index: int | None = None,
                     source_subject_alignment: dict[str, Any] | None = None) -> dict[str, Any]:
    page = page or {}
    described = " ".join(str(media.get(key) or "") for key in
                         ("description", "alt_text", "title", "purpose", "topic_terms", "keywords", "name"))
    described += " " + " ".join(_as_values(media.get("tags")))
    # For embedded media, surrounding slide copy can locate a promising
    # candidate, but it is not a description of the image itself.
    page_context = " ".join(str(page.get(key) or "") for key in ("title", "text_excerpt"))
    described_for_ranking = " ".join((described, page_context))
    target = targets[target_index] if target_index is not None and target_index < len(targets) else {}
    query = " ".join([request_text, _target_query(target)])
    content, content_evidence = _content_score(query, described_for_ranking)
    role_target, role_basis = _target_roles(target) if target else ([], "request_only")
    role, role_evidence = _role_score(role_target, media)
    composition, composition_evidence = _composition_score(target, media)
    hierarchy, hierarchy_evidence = _hierarchy_score(target, media)
    score = .45 * content + .25 * role + .18 * composition + .12 * hierarchy
    source_subject_alignment = source_subject_alignment or {}
    source_subject_conflict = source_subject_alignment.get("status") in {
        "subject_conflict", "mixed_subject_conflict"
    }
    # Whole-deck text provides a source-level warning only. It does not tell
    # us what this particular embedded image depicts. Keep the structural and
    # textual evidence visible, while preventing it from ranking like a fit.
    if source_subject_conflict:
        score *= .25
    rights = str(media.get("rights_status") or media.get("media_rights") or
                 _rights(record, row) or "unverified")
    allowed = rights == "verified_reusable" and media.get("reuse_status") == "verified_reusable"
    source_path = str(media.get("source_pptx") or record.get("path") or row.get("path") or "")
    source_is_pptx = source_path.casefold().endswith((".pptx", ".ppt"))
    media_path = media.get("media_path") or media.get("media_part") or media.get("path")
    media_hash = media.get("sha256") or media.get("media_sha256")
    source_shape = media.get("source_shape") or media.get("source_shape_id") or media.get("shape_id") or media.get("object_id")
    if isinstance(source_shape, str) and source_shape.isdigit():
        source_shape = int(source_shape)
    missing = []
    if not rights or rights == "unverified":
        missing.append("media_rights_unverified")
    if not media_hash:
        missing.append("media_hash_missing")
    missing.append("image_meaning_unverified")
    if source_subject_conflict:
        missing.append("source_subject_conflict")
    elif source_subject_alignment.get("status") != "aligned":
        missing.append("source_subject_fit_requires_manual_review")
    return {"score": round(score, 4),
            "scores": {"content": round(content, 4), "page_function": round(role, 4),
                       "composition": round(composition, 4), "hierarchy": round(hierarchy, 4)},
            "evidence": {"content_terms": content_evidence,
                         "source_subject_alignment": source_subject_alignment,
                         "page_roles": role_evidence,
                         "page_role_basis": role_basis, "composition_terms": composition_evidence,
                         "hierarchy_terms": hierarchy_evidence},
            "target_index": target_index,
            "source_pptx": source_path if source_is_pptx else media.get("source_pptx"),
            "source_path": source_path, "source_slide": media.get("source_slide", page.get("slide")),
            "source_shape": source_shape, "relationship_id": media.get("relationship_id"),
            "media_path": media_path, "media_sha256": media_hash,
            "kind": media.get("kind") or media.get("media_kind") or "unknown",
            "format": media.get("format") or Path(str(media_path or source_path)).suffix.lstrip("."),
            "description": described.strip(), "source_page_context": page_context[:500],
            "context_is_image_meaning": False, "rights_status": rights,
            "reuse_status": media.get("reuse_status", "not_authorized_by_index"),
            "reuse_permission": "rights_verified" if allowed else "reference_only",
            "visual_review": _visual_status(record, row),
            "gaps": missing}


def _record_media_candidates(record: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    found: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    for item in record.get("media_candidates", []) if isinstance(record.get("media_candidates"), list) else []:
        if isinstance(item, dict):
            found.append((item, None))
    for page in record.get("slides", []) if isinstance(record.get("slides"), list) else []:
        if not isinstance(page, dict):
            continue
        for item in page.get("media_candidates", []) if isinstance(page.get("media_candidates"), list) else []:
            if isinstance(item, dict):
                found.append((item, page))
    return found


def _is_embedded_bindable_media(media: dict[str, Any], record: dict[str, Any]) -> bool:
    """Only return media that library_assets can revalidate and extract.

    Native PowerPoint diagrams remain useful source-page references, but they
    are not an embedded image/SVG and must never be offered to library-media.
    """
    source = media.get("source_pptx") or media.get("source_path") or record.get("path")
    slide = media.get("source_slide")
    shape = (media.get("source_shape_id") or media.get("source_shape") or
             media.get("shape_id") or media.get("object_id"))
    part = media.get("media_part") or media.get("media_path")
    relationship = media.get("relationship_id")
    digest = media.get("sha256") or media.get("media_sha256")
    if isinstance(shape, str) and shape.isdigit():
        shape = int(shape)
    if isinstance(slide, str) and slide.isdigit():
        slide = int(slide)
    return (isinstance(source, str) and source.casefold().endswith(".pptx")
            and isinstance(slide, int) and not isinstance(slide, bool) and slide > 0
            and isinstance(shape, int) and not isinstance(shape, bool) and shape > 0
            and isinstance(part, str) and part.startswith("ppt/media/")
            and isinstance(relationship, str) and bool(relationship)
            and isinstance(digest, str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", digest)))


def _source_page_reference(request_text: str, targets: list[dict[str, Any]], record: dict[str, Any],
                           row: dict[str, Any], page: dict[str, Any], target_index: int) -> dict[str, Any]:
    page_context = " ".join(str(page.get(key) or "") for key in ("title", "text_excerpt"))
    target = targets[target_index] if target_index < len(targets) else {}
    query = " ".join((request_text, _target_query(target)))
    content, evidence = _content_score(query, page_context)
    subject = _subject_alignment(query, page_context)
    role_target, role_basis = _target_roles(target) if target else ([], "request_only")
    role, role_evidence = _role_score(role_target, page)
    composition, composition_evidence = _composition_score(target, page)
    hierarchy, hierarchy_evidence = _hierarchy_score(target, page)
    score = (.35 * content + .10 * subject["score"] + .25 * role + .18 * composition
             + .12 * hierarchy - subject["conflict_penalty"])
    return {"source_pptx": record.get("path"), "source_slide": page.get("slide"),
            "title": page.get("title"), "source_page_context": page_context[:500],
            "target_index": target_index, "score": round(score, 4),
            "scores": {"content": round(content, 4), "subject_alignment": subject["score"],
                       "page_function": round(role, 4),
                       "composition": round(composition, 4), "hierarchy": round(hierarchy, 4)},
            "evidence": {"content_terms": evidence, "subject_alignment": subject,
                         "page_roles": role_evidence,
                         "page_role_basis": role_basis, "composition_terms": composition_evidence,
                         "hierarchy_terms": hierarchy_evidence},
            "reference_only": True,
            "reuse_permission": "reference_only",
            "rights_status": _rights(record, row),
            "reason": "Source page contains no verified embedded image/SVG relationship for direct media binding."}


def retrieve_library_reuse(catalog: Path, depth_index: Path, *, request_text: str,
                           form: str | None = None, slides: list[dict] | None = None,
                           limit: int = 8) -> dict:
    """Find reusable decks/pages by request meaning and design structure.

    Candidate scores remain decomposed. Inferred OOXML hierarchy/composition
    evidence is never promoted to a visual-review claim.
    """
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    resolved_form = required_form_from_query(request_text, form)
    targets = list(slides or [])
    if not targets:
        roles = _request_roles(request_text)
        targets = [{"title": request_text[:100], "text": request_text, "role_candidates": roles}]
    joined, errors = _safe_candidates(catalog, depth_index)
    decks = []
    assets_by_key: dict[tuple, dict[str, Any]] = {}
    source_pages_by_key: dict[tuple, dict[str, Any]] = {}
    filtered = 0
    for record, row in joined:
        asset_type = str(row.get("asset_type", "template")).casefold()
        is_standalone_asset = asset_type in {"diagram_asset", "asset", "素材", "素材资源"}
        if not is_standalone_asset and not _occasion_filter(record, row, resolved_form):
            filtered += 1
            continue
        if not is_standalone_asset and not _specific_contest_context_filter(request_text, record, row):
            filtered += 1
            continue
        pages = [page for page in record.get("slides", []) if isinstance(page, dict)]
        subject_source = " ".join([_flatten(record.get("topic_terms")), *(
            " ".join(str(page.get(key) or "") for key in ("title", "text_excerpt")) for page in pages)])
        subject_query = " ".join((request_text, *(_target_query(target) for target in targets)))
        # Compute source-level subject alignment once per deck and reuse it for
        # both its embedded media and whole-deck ranking evidence.
        subject = _subject_alignment(subject_query, subject_source, pages=pages)
        media_rows = _record_media_candidates(record)
        # Keep only candidates with an indexed embedded part and shape/slide
        # relation. Diagram files made from editable native shapes flow into
        # source_page_candidates below instead of appearing bindable.
        unbindable = [(media, page) for media, page in media_rows
                      if not _is_embedded_bindable_media(media, record)]
        media_rows = [(media, page) for media, page in media_rows
                      if _is_embedded_bindable_media(media, record)]
        if is_standalone_asset or asset_type in {"template", "portfolio", "portfolio_example", "完成案例", "作品集"}:
            for media, source_page in media_rows:
                for target_index in range(len(targets)):
                    candidate = _asset_candidate(request_text, targets, record, row, media, source_page,
                                                 target_index=target_index,
                                                 source_subject_alignment=subject)
                    if candidate["score"] <= 0:
                        continue
                    candidate["source_asset_type"] = asset_type
                    key = (candidate.get("source_pptx") or candidate["source_path"], candidate.get("source_slide"),
                           candidate.get("source_shape"), candidate.get("media_sha256") or candidate.get("media_path"))
                    current = assets_by_key.get(key)
                    if current is None or candidate["score"] > current["score"]:
                        assets_by_key[key] = candidate
        if is_standalone_asset and (unbindable or not media_rows):
            reference_pages = pages or [None]
            for page in reference_pages:
                for target_index in range(len(targets)):
                    if page is None:
                        reference = {"source_pptx": record.get("path"), "source_slide": None,
                                     "title": row.get("title") or Path(record["path"]).stem,
                                     "source_page_context": " ".join(_as_values(record.get("topic_terms")) +
                                         _as_values(row.get("page_role_tags")) +
                                         [str(row.get("content_note") or "")]),
                                     "target_index": target_index, "score": 0.0,
                                     "scores": {"content": 0.0, "page_function": 0.0,
                                                "composition": 0.0, "hierarchy": 0.0},
                                     "evidence": {}, "reference_only": True,
                                     "reuse_permission": "reference_only", "rights_status": _rights(record, row),
                                     "reason": "Diagram source has no embedded image/SVG relationship; inspect or redraw its native shapes."}
                    else:
                        reference = _source_page_reference(request_text, targets, record, row, page, target_index)
                    if reference["score"] <= 0 and len(reference_pages) > 1:
                        continue
                    reference["source_asset_type"] = asset_type
                    key = (reference.get("source_pptx"), reference.get("source_slide"), reference.get("target_index"))
                    current = source_pages_by_key.get(key)
                    if current is None or reference["score"] > current["score"]:
                        source_pages_by_key[key] = reference
        if is_standalone_asset:
            continue
        if asset_type not in {"template", "portfolio", "portfolio_example", "完成案例", "作品集"}:
            continue
        page_scores = [[_score_page(target, page) for page in pages] for target in targets]
        per_target = []
        for i, target in enumerate(targets):
            ordered = sorted(page_scores[i], key=lambda item: (-item["score"], str(item.get("slide"))))[:3]
            for item in ordered:
                item["target_index"] = i
            per_target.append({"target_index": i, "target_message": _target_query(target), "candidates": ordered})
        sequence_score, sequence_matches = _sequence_match(targets, pages)
        aligned = {item["target_index"]: item for item in sequence_matches}
        for target_row in per_target:
            match = aligned.get(target_row["target_index"])
            for candidate in target_row["candidates"]:
                candidate["sequence_aligned"] = bool(match and candidate.get("slide") == match.get("slide"))
                candidate["sequence_position"] = match.get("sequence_position") if candidate["sequence_aligned"] else None
                candidate["scores"]["sequence"] = 1.0 if candidate["sequence_aligned"] else 0.0
        # Aggregate distinct facets; filename terms are deliberately excluded.
        all_page = [score for row_scores in page_scores for score in row_scores]
        content_score = max((item["scores"]["content"] for item in all_page), default=0.0)
        role_score = sum(max((item["scores"]["page_function"] for item in row_scores), default=0.0)
                         for row_scores in page_scores) / max(1, len(targets))
        composition_score = sum(max((item["scores"]["composition"] for item in row_scores), default=0.0)
                                for row_scores in page_scores) / max(1, len(targets))
        hierarchy_score = sum(max((item["scores"]["hierarchy"] for item in row_scores), default=0.0)
                              for row_scores in page_scores) / max(1, len(targets))
        # Keep occasion filtering and the independent page/structure/sequence
        # facets intact while reserving 0.10 for explicit subject fit.  A
        # disjoint, detected subject lowers the score; an unclassified source
        # remains neutral rather than being treated as a conflict.
        total = (.24 * content_score + .10 * subject["score"] + .25 * role_score
                 + .18 * composition_score + .13 * hierarchy_score + .10 * sequence_score
                 - subject["conflict_penalty"])
        visual = _visual_status(record, row)
        subject_gaps = []
        if subject["status"] in {"subject_conflict", "mixed_subject_conflict"}:
            subject_gaps.append("source_subject_conflict")
        elif subject["status"] != "aligned":
            subject_gaps.append("subject_fit_requires_manual_review")
        rights = _rights(record, row)
        media = [asset for page in pages for asset in page.get("media_candidates", []) if isinstance(asset, dict)]
        decks.append({"path": record["path"], "title": row.get("title") or Path(record["path"]).stem,
                      "source_sha256": record.get("expected_sha256") or row.get("source_sha256"),
                      "page_count": len(pages), "asset_type": row.get("asset_type", "template"),
                      "score": round(total, 4),
                      "scores": {"content": round(content_score, 4), "subject_alignment": subject["score"],
                                 "page_function": round(role_score, 4),
                                 "composition": round(composition_score, 4), "hierarchy": round(hierarchy_score, 4),
                                 "sequence": sequence_score},
                      "evidence": {"matched_page_count": sum(bool(item["candidates"]) for item in per_target),
                                   "sequence_matches": sequence_matches,
                                   "occasion_tags": _as_values(row.get("occasion_tags")),
                                   "topic_terms": _as_values(record.get("topic_terms")),
                                   "subject_alignment": subject,
                                   "style_tags": _as_values(record.get("style_tags"))},
                      "target_pages": per_target,
                      "visual_review": {"status": visual, "reviewed_pages": (record.get("review") or {}).get("reviewed_pages", []),
                                        "claim": "Machine OOXML structure does not establish visual review."},
                      "source_evidence": {"depth_status": record.get("status"), "parse_status": record.get("parse_status", "parsed"),
                                          "expected_sha256": record.get("expected_sha256"),
                                          "source_bytes": record.get("source_bytes"), "mtime_ns": record.get("mtime_ns")},
                      "media_rights": rights,
                      "media_reuse": [{"kind": item.get("kind"), "description": item.get("description"),
                                        "format": item.get("format"), "sha256": item.get("sha256"),
                                        "rights_status": item.get("rights_status", "unverified"),
                                        "reuse_status": item.get("reuse_status", "not_authorized_by_index")}
                                       for item in media[:30]],
                      "reuse_gaps": (["visual_review_missing"] if visual == "not_visually_reviewed" else []) + subject_gaps,
                      "reuse_permission": "reference_only" if rights != "verified_reusable" else "rights_verified"})
    subject_rank = {"aligned": 0, "partially_aligned": 1,
                    "cross_cutting_subject_only": 2,
                    "no_request_subject_anchor": 3, "source_subject_unresolved": 3,
                    "weak_context_only": 4, "incidental_only": 4,
                    "mixed_subject_conflict": 5, "subject_conflict": 5}
    decks.sort(key=lambda item: (subject_rank.get(item["evidence"]["subject_alignment"]["status"], 1),
                                 -item["score"], -item["scores"]["sequence"], item["path"].casefold()))
    asset_candidates = sorted(assets_by_key.values(), key=lambda item: (-item["score"], item["source_path"].casefold(),
                                                                          str(item.get("source_slide") or "")))[:limit]
    source_page_candidates = sorted(source_pages_by_key.values(), key=lambda item: (-item["score"],
                                str(item.get("source_pptx") or "").casefold(),
                                str(item.get("source_slide") or "")))[:limit]
    return {"schema_version": 1, "status": "ready" if decks or asset_candidates or source_page_candidates else "no_match", "form": resolved_form,
            "request_text": request_text, "target_count": len(targets), "occasion_filtered_count": filtered,
            "candidate_count": len(decks), "candidates": decks[:limit],
            "asset_candidate_count": len(assets_by_key), "asset_candidates": asset_candidates,
            "source_page_candidate_count": len(source_pages_by_key), "source_page_candidates": source_page_candidates,
            "index_errors": errors,
            "ranking_note": "Content, target page function, composition, hierarchy and sequence are scored separately; filenames do not add relevance.",
            "visual_review_boundary": "OOXML-derived structure is a machine observation, never proof of visual inspection."}
