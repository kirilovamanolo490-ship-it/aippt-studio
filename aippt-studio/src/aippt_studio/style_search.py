"""Find competition presentation families by source content and measured layout.

This is a discovery layer over full OOXML page/style indexes. It never treats a
folder name, a machine role guess or an unrendered image as visual acceptance.
"""
from __future__ import annotations

from collections import defaultdict
import colorsys
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .library_read_guard import require_library_available


CREATIVE = "创赛"
CAREER = "BB0086 大学生职业生涯规划大赛 PPT"
_CAREER_CUES = ("职业生涯", "职业规划", "职规赛", "就业赛道", "成长赛道", "职业发展")
_CREATIVE_CUES = ("创赛", "创新创业", "创新大赛", "互联网+", "挑战杯", "红旅赛道", "创业计划", "创新项目路演")
_STYLE_COLORS = {"红": "red", "蓝": "blue", "绿": "green", "金": "gold", "紫": "purple", "橙": "orange", "黑": "black"}
_ROLE_ALIASES = {
    "封面": "cover", "目录": "agenda", "背景": "background_problem", "问题": "background_problem",
    "痛点": "background_problem", "方案": "method_approach", "方法": "method_approach",
    "技术路线": "process_architecture", "流程": "process_architecture",
    "证据": "evidence_results", "成果": "evidence_results", "团队": "team_organization",
    "商业模式": "budget_model", "市场": "budget_model", "预算": "budget_model",
    "实施计划": "timeline_plan", "行动计划": "timeline_plan", "总结": "closing_outlook",
    "政策": "policy_context", "职业认知": "career_context", "自我认知": "self_assessment",
    "目标岗位": "goal_positioning", "能力差距": "background_problem", "成长实践": "evidence_results",
}


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_source(library: Path, relative: str) -> Path | None:
    if not isinstance(relative, str) or "\\" in relative:
        return None
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts:
        return None
    source = (library / path).resolve()
    try:
        source.relative_to(library.resolve())
    except ValueError:
        return None
    return source if source.is_file() else None


def _collection_intent(query: str) -> str | None:
    career = any(cue in query for cue in _CAREER_CUES)
    creative = any(cue in query for cue in _CREATIVE_CUES)
    if career != creative:
        return "career" if career else "creative"
    return None


def _curation(creative_root: Path, career_root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for row in _rows(creative_root / "分析索引/creativecup-curation.jsonl"):
        relative = row.get("path", "")
        if relative.startswith("模板库/"):
            relative = relative[len("模板库/"):]
        if relative.startswith(CREATIVE + "/"):
            material = row.get("material_type", "")
            asset = ("template" if "模板/框架" in material else
                     "guide" if "指南/教程" in material else
                     "portfolio" if "已完成" in material or "已填写项目稿" in material else
                     "reference" if "项目内容稿" in material else "uncertain")
            result[relative] = {"category": row.get("category"), "track": row.get("material_role"),
                                "asset_type": asset, "confidence": row.get("confidence"),
                                "source_sha256": row.get("sha256") or row.get("inventory_sha256"),
                                "evidence": row.get("evidence", []),
                                "ambiguity_note": row.get("ambiguity_note"), "profession_candidates": []}
    career_taxonomy = career_root / "分析索引/SOURCE-TAXONOMY.jsonl"
    career_source = career_taxonomy if career_taxonomy.is_file() else career_root / "分析索引/SOURCE-MANIFEST.jsonl"
    for row in _rows(career_source):
        relative = row.get("source_path", "")
        if not relative.startswith(CAREER + "/"):
            relative = CAREER + "/" + relative
        taxonomy = row.get("refined_taxonomy") or row.get("taxonomy") or {}
        material = taxonomy.get("asset_type")
        asset = {"blank_template_or_layout": "template", "blank_template": "template",
                 "template_with_example_content": "template_with_example_content",
                 "general_presentation_template": "template",
                 "completed_case": "portfolio", "completed_example": "portfolio",
                 "career_content_reference": "reference", "career_reference_unclear_completion": "uncertain",
                 "guide_or_competition_material": "guide", "rules_or_manual": "guide",
                 "interview_material": "evidence", "interview_evidence": "evidence",
                 "evidence_or_appendix": "evidence"}.get(material, "uncertain")
        result[relative] = {"category": "人物与职业", "track": taxonomy.get("track"),
                            "asset_type": asset, "confidence": taxonomy.get("asset_type_confidence"),
                            "source_sha256": row.get("sha256") or row.get("inventory_sha256"),
                            "evidence": taxonomy.get("classification_evidence", {}),
                            "profession_candidates": taxonomy.get("profession_candidates", taxonomy.get("profession_industry_candidates", []))}
    # The global source-hash-bound curation resolves duplicate aliases and
    # full-deck structural exceptions such as image-only or off-topic decks.
    # It takes precedence over the raw collection taxonomy for reuse type.
    for row in _rows(creative_root.parent / "CONTENT-CURATION.jsonl"):
        path = row.get("path")
        if path not in result:
            continue
        if row.get("sha256") != result[path].get("source_sha256"):
            result[path].update(asset_type="uncertain", curation_stale=True)
            continue
        observation = row.get("classification_observation") or {}
        result[path].update(asset_type=row.get("asset_type", result[path]["asset_type"]),
                            curated_category="/".join(part for part in (row.get("category"), row.get("subcategory")) if part),
                            confidence=row.get("confidence"),
                            off_topic=observation.get("off_topic") is True,
                            flattened_page_structure=observation.get("flattened_page_structure") is True)
    from .gaojie_layouts import gaojie_curation
    result.update(gaojie_curation(creative_root.parent))
    return result


def _visual_samples(analysis: Path, collection_name: str) -> dict[str, list[dict[str, Any]]]:
    path = analysis / "VISUAL-SAMPLE.json"
    if not path.is_file():
        return {}
    packet = json.loads(path.read_text(encoding="utf-8"))
    by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in packet.get("render_results", []):
        if item.get("status") != "rendered_advisory" or item.get("approximate") is not True:
            continue
        digest = item.get("source_sha256")
        if isinstance(digest, str):
            by_sha[digest].append({"slide": item.get("slide"), "role": item.get("role"),
                                   "png": f"{collection_name}/分析索引/{item.get('png')}",
                                   "receipt": f"{collection_name}/分析索引/{item.get('receipt')}"})
    return by_sha


def _hue_name(color: str | None) -> str | None:
    if not isinstance(color, str) or not re.fullmatch(r"[0-9A-Fa-f]{6}", color):
        return None
    rgb = [int(color[index:index + 2], 16) / 255 for index in (0, 2, 4)]
    hue, saturation, value = colorsys.rgb_to_hsv(*rgb)
    if value < .2:
        return "black"
    if saturation < .12:
        return "neutral"
    degrees = hue * 360
    if degrees < 16 or degrees >= 345:
        return "red"
    if degrees < 48:
        return "orange"
    if degrees < 67:
        return "gold"
    if degrees < 170:
        return "green"
    if degrees < 255:
        return "blue"
    if degrees < 345:
        return "purple"
    return None


def _requested_roles(query: str, explicit: list[str] | None) -> list[str]:
    if explicit:
        return list(dict.fromkeys(_ROLE_ALIASES.get(item, item) for item in explicit if item))
    return list(dict.fromkeys(role for cue, role in _ROLE_ALIASES.items() if cue in query))


def _role_coverage(wanted: list[str], pages: list[dict]) -> tuple[float, list[dict]]:
    if not wanted:
        return 0.0, []
    chosen = []
    seen = set()
    for role in wanted:
        candidates = [page for page in pages
                      if role in {item.get("role") for item in page.get("role_evidence", [])}
                      and page["slide"] not in seen]
        if not candidates:
            continue
        page = candidates[0]
        seen.add(page["slide"])
        chosen.append({"target_role": role, "source_slide": page["slide"],
                       "source_title": page.get("title"), "layout_pattern": page.get("layout_pattern")})
    return len(chosen) / len(wanted), chosen


def _score(query: str, row: dict, meta: dict, pages: list[dict], wanted_roles: list[str]) -> tuple[float, dict]:
    profile = row.get("style_profile") or {}
    title = next((item.get("title", "") for item in profile.get("page_role_sequence", [])
                  if item.get("title")), Path(row["path"]).stem)
    source_content = " ".join((title, *(page.get("title", "") + " " + page.get("text_excerpt", "")[:500]
                                         for page in pages[:20]))).casefold()
    metadata_context = " ".join((row["path"], str(meta.get("category") or ""),
                                 str(meta.get("track") or ""))).casefold()
    role_score, source_pages = _role_coverage(wanted_roles, pages)
    score = 2.0 + role_score * 5
    evidence: dict[str, Any] = {"page_role_coverage": round(role_score, 3),
                                "source_page_map_candidates": source_pages,
                                "theme_consistency_ratio": profile.get("theme_consistency_ratio"),
                                "page_aspect_ratio": profile.get("page_aspect_ratio")}
    if meta.get("asset_type") == "template":
        score += 2
    elif meta.get("asset_type") == "template_with_example_content":
        score += 1
    elif meta.get("asset_type") in {"guide", "evidence"}:
        score -= 5
    elif meta.get("asset_type") == "uncertain":
        score -= 1
    if meta.get("confidence") in {"高", "high"}:
        score += 1
    elif meta.get("confidence") in {"低", "low"}:
        score -= 2
        evidence["curation_confidence_gap"] = "low"
    if meta.get("ambiguity_note"):
        score -= 1
        evidence["source_topic_ambiguity"] = str(meta["ambiguity_note"])[:220]
    for term in ("挑战杯", "红旅", "乡村振兴", "非遗", "科技", "医疗", "农业", "社会调查", "就业", "成长"):
        if term in query and term in source_content:
            score += 3
            evidence.setdefault("source_content_terms", []).append(term)
        elif term in query and term in metadata_context:
            score += .5
            evidence.setdefault("metadata_only_terms", []).append(term)
    profession = [term for term in meta.get("profession_candidates", []) if isinstance(term, str)
                  and len(term) >= 3 and term in query]
    if profession:
        score += 4
        evidence["profession_match"] = profession[:5]
    desired_colors = {name for text, name in _STYLE_COLORS.items() if text in query}
    palette = profile.get("theme_palette") or {}
    fill = profile.get("explicit_fill_colors_top") or []
    # Full Office themes often contain unused red/blue/green accent slots.
    # Accent1 plus frequent explicit fills is a narrower, still advisory cue.
    observed_colors = {_hue_name(palette.get("accent1"))}
    observed_colors.update(_hue_name(item[0]) for item in fill[:3] if isinstance(item, list) and item)
    measured = desired_colors.intersection(observed_colors)
    if measured:
        score += 2 * len(measured)
        evidence["measured_color_match"] = sorted(measured)
    if any(cue in query for cue in ("多图", "图片", "照片", "实景")):
        pictures = (profile.get("pictures_per_slide") or {}).get("p50") or 0
        if pictures >= 2:
            score += 2
            evidence["picture_median_per_slide"] = pictures
    wanted_layouts = set()
    if any(cue in query for cue in ("左图右文", "图片在左", "左图")):
        wanted_layouts.add("image_left_text_right")
    if any(cue in query for cue in ("右图左文", "图片在右", "右图")):
        wanted_layouts.add("text_left_image_right")
    if any(cue in query for cue in ("双图", "多图", "图片墙", "照片墙")):
        wanted_layouts.add("multi_image_gallery")
    if any(cue in query for cue in ("主图", "全幅", "大片")):
        wanted_layouts.add("large_image_canvas")
    if wanted_layouts:
        observed = {page.get("layout_pattern", {}).get("kind") for page in pages}
        matching = wanted_layouts.intersection(observed)
        if matching:
            score += 3
            evidence["measured_page_layout_match"] = sorted(matching)
    if any(cue in query for cue in ("图表", "数据", "指标", "趋势")):
        chart_pages = sum(bool(page.get("chart_count") or page.get("table_count")) for page in pages)
        if chart_pages:
            score += min(3, chart_pages)
            evidence["structured_chart_or_table_pages"] = chart_pages
    page_count = row.get("slide_count") or 0
    match = re.search(r"(?:约|大概)?\s*(\d{1,3})\s*(?:到|至|-)?\s*(?:\d{1,3})?\s*页", query)
    if match and page_count:
        desired = int(match.group(1))
        score += max(0.0, 2.0 - abs(page_count - desired) / max(5, desired))
    consistency = profile.get("theme_consistency_ratio")
    if isinstance(consistency, (float, int)):
        score += max(0.0, min(1.0, consistency))
    if row.get("source_format") != "pptx":
        score -= 1
    evidence["score_basis"] = "occasion-filtered source content, page-role coverage, measured palette/image density and curation"
    return round(score, 3), evidence


def _occasion_guard(query: str, collection: str, row: dict, meta: dict,
                    pages: list[dict]) -> tuple[bool, str]:
    """Keep exact contest systems/tracks separate from attractive page style."""
    filename = Path(row["path"]).name.casefold()
    early = " ".join((page.get("title", "") + " " + page.get("text_excerpt", "")[:600])
                     for page in pages[:3]).casefold()
    path = row["path"].casefold()
    if any(term in filename for term in ("填写指南", "修改指南", "使用教程", "操作说明", "备赛手册", "评审规则")):
        return False, "source_is_an_instruction_document"
    if meta.get("off_topic") is True:
        return False, "full_deck_classified_off_topic"
    if collection == "career":
        # A career cover alone is weak evidence. Some bundled files put a
        # generic career title over a work-summary deck; their agenda and
        # substantive pages reveal the mismatch.
        front = " ".join((page.get("title", "") + " " + page.get("text_excerpt", ""))
                         for page in pages[:min(6, len(pages))]).casefold()
        report_terms = ("年度工作概述", "年度工作总结", "工作完成情况", "明年工作计划",
                        "下一年工作计划", "年终工作总结", "全年工作业绩")
        if sum(term in front for term in report_terms) >= 2:
            return False, "work_summary_agenda_conflicts_with_career_contest"
    if collection == "creative":
        if "挑战杯" in query and not ("挑战杯" in path or "挑战杯" in early):
            return False, "challenge_cup_identity_not_in_source_context"
        if "社会调查" in query and not ("社会调查" in early or "调研报告" in early
                                 or "社会调查" in str(meta.get("category") or "")):
            return False, "social_survey_identity_not_in_source_context"
        if "红旅" in query and not ("红旅" in path or "红旅" in early):
            return False, "red_tourism_track_not_in_source_context"
    else:
        desired_track = "employment" if "就业赛道" in query else "growth" if "成长赛道" in query else None
        if desired_track:
            normalized = str(meta.get("track") or "").casefold()
            matching_text = "就业赛道" if desired_track == "employment" else "成长赛道"
            if normalized != desired_track and matching_text not in early and matching_text not in filename:
                return False, f"{desired_track}_track_not_evidenced"
        professions = {
            "nursing": ("护理", "护士", "临床照护"),
            "accounting": ("会计", "财务", "注册会计"),
            "art_education": ("美育", "美术", "绘画", "艺术教育"),
            "social_work": ("社会工作", "社工"),
            "civil_engineering": ("土木工程", "土木建筑"),
            "electrical_engineering": ("电气工程", "电气学科"),
        }
        requested = {name for name, terms in professions.items() if any(term in query for term in terms)}
        observed_context = " ".join((path, early))
        observed = {name for name, terms in professions.items() if any(term in observed_context for term in terms)}
        if requested and observed and not requested.intersection(observed):
            return False, "explicit_career_profession_conflict"
    return True, "source_context_evidenced"


def _layout_examples(library: Path) -> dict[str, list[dict]]:
    """Keep visual source-page reviews separate from executable slot experiments."""
    path = library / "REUSABLE-LAYOUTS.json"
    if not path.is_file():
        return {}
    try:
        packet = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(packet, dict) or packet.get("schema") != "aippt.reusable-layouts.v1":
        return {}
    found: dict[str, list[dict]] = defaultdict(list)
    for item in packet.get("entries", []):
        if not isinstance(item, dict) or not isinstance(item.get("source_sha256"), str):
            continue
        design_preview = item.get("design_preview") or {}
        design_path = Path(design_preview.get("path") or "")
        design_current = design_path.is_file() and _sha(design_path) == design_preview.get("sha256")
        examples = []
        for variant in item.get("validation", []):
            if not isinstance(variant, dict):
                continue
            preview = Path(variant.get("preview") or variant.get("png") or "")
            current = preview.is_file() and _sha(preview) == (variant.get("preview_sha256") or variant.get("png_sha256"))
            examples.append({**variant, "preview_current": current,
                             "visual_status": variant.get("visual_status", "read_entry_review") if current else "evidence_missing_or_changed"})
        found[item["source_sha256"]].append({
            "id": item.get("id"), "source_slide": item.get("source_slide"),
            "record_kind": item.get("record_kind", "slot_experiment"),
            "page_role": item.get("page_role"), "status": item.get("status"),
            "replaceable_slots": item.get("slot_map") or item.get("replaceable_slots", []),
            "source_title": item.get("source_title"), "use_case": item.get("use_case"),
            "slot_map": item.get("slot_map", []), "capacity_policy": item.get("capacity_policy"),
            "text_capacity_strategy": item.get("text_capacity_strategy"),
            "image_relation": item.get("image_relation"),
            "validation": examples, "validation_error": item.get("validation_error"),
            "review": item.get("review"), "boundaries": item.get("boundaries", []),
            "design_review": item.get("design_review", {"decision": "not_assessed"}),
            "design_preview": {**design_preview, "current": design_current},
            "layout_relationships": item.get("layout_relationships", []),
            "image_roles": item.get("image_roles", []),
            "topic_fit": item.get("topic_fit", []),
            "adaptation_mode": item.get("adaptation_mode"),
            "ready_for_automatic_application": False,
            "scope": "Review applies only to the identified source page, tested content and hash-bound output images; never to unseen pages or native display.",
        })
    return found


def search_style_library(library: Path, query: str, *, collection: str = "auto",
                         roles: list[str] | None = None, limit: int = 10) -> dict:
    """Return distinct current-SHA candidates with measured whole-deck evidence."""
    if not query.strip() or collection not in {"auto", "creative", "career", "gaojie"} or not 1 <= limit <= 30:
        raise ValueError("query, collection=auto|creative|career|gaojie and limit=1..30 are required")
    from .gaojie_layouts import CATEGORIES, search_gaojie_layouts
    if collection == "gaojie" or (collection == "auto" and (
            "高阶文化" in query or (not _collection_intent(query) and any(name in query for name in CATEGORIES)))):
        return search_gaojie_layouts(library, query, roles=roles, limit=limit)
    library = library.resolve()
    require_library_available(library)
    intent = _collection_intent(query) if collection == "auto" else collection
    selected = [intent] if intent else ["creative", "career"]
    roots = {"creative": library / CREATIVE, "career": library / CAREER}
    curations = _curation(roots["creative"], roots["career"])
    wanted_roles = _requested_roles(query, roles)
    rows: list[dict] = []
    for key in selected:
        analysis = roots[key] / "分析索引"
        visual = _visual_samples(analysis, CREATIVE if key == "creative" else CAREER)
        deck_rows = _rows(analysis / "STYLE-CATALOG.jsonl")
        page_by_sha: dict[str, list[dict]] = defaultdict(list)
        for page in _rows(analysis / "STYLE-PAGES.jsonl"):
            page_by_sha[page["source_sha256"]].append(page)
        for row in deck_rows:
            if row.get("status") != "parsed":
                continue
            meta = curations.get(row["path"], {})
            if meta.get("curation_stale") is True or meta.get("source_sha256") != row.get("source_sha256"):
                continue
            source_pages = page_by_sha.get(row["source_sha256"], [])
            compatible, reason = _occasion_guard(query.casefold(), key, row, meta, source_pages)
            if not compatible:
                continue
            score, evidence = _score(query.casefold(), row, meta,
                                     source_pages, wanted_roles)
            evidence["occasion_guard"] = reason
            previews = visual.get(row["source_sha256"], [])
            rows.append({"source_path": row["path"], "source_sha256": row["source_sha256"],
                         "collection": key, "source_format": row.get("source_format"),
                         "slide_count": row["slide_count"], "asset_type": meta.get("asset_type", "uncertain"),
                         "category": meta.get("category"), "track": meta.get("track"),
                         "curation_confidence": meta.get("confidence"),
                         "score": score, "evidence": evidence,
                         "style_profile": row.get("style_profile"),
                         "detail_ref": row["detail_ref"],
                         "reuse_status": ("source_template_candidate" if meta.get("asset_type") in {"template", "template_with_example_content"}
                                          and row.get("source_format") == "pptx" else "reference_only"),
                         "visual_status": ("three_page_approximate_sample_rendered_unreviewed" if previews else "not_reviewed"),
                         "sampled_page_previews": previews, "media_rights": "unverified"})
    grouped: dict[str, dict] = {}
    for row in sorted(rows, key=lambda item: (-item["score"], item["source_path"])):
        digest = row["source_sha256"]
        if digest not in grouped:
            row["duplicate_paths"] = []
            grouped[digest] = row
        else:
            grouped[digest]["duplicate_paths"].append(row["source_path"])
    candidates = sorted(grouped.values(), key=lambda row: (
        row["reuse_status"] != "source_template_candidate", -row["score"], row["source_path"]))
    copy_candidates, references, stale = [], [], []
    reference_limit = min(5, limit)
    for row in candidates:
        is_copyable = row["reuse_status"] == "source_template_candidate"
        if is_copyable and len(copy_candidates) >= limit:
            continue
        if not is_copyable and len(references) >= reference_limit:
            continue
        source = _safe_source(library, row["source_path"])
        if not source or _sha(source) != row["source_sha256"]:
            stale.append(row["source_path"])
            continue
        (copy_candidates if is_copyable else references).append(row)
        if len(copy_candidates) >= limit and len(references) >= reference_limit:
            break
    examples = _layout_examples(library)
    for row in copy_candidates + references:
        row["layout_examples"] = examples.get(row["source_sha256"], [])
    shown = copy_candidates or references
    return {"schema": "aippt.competition-style-search.v1", "query": query,
            "collection_intent": intent or "ambiguous_two_collections",
            "requested_roles": wanted_roles,
            "status": "ready" if copy_candidates else "reference_only" if references else "no_match",
            "candidate_count": len(shown), "copyable_candidate_count": len(copy_candidates),
            "candidates": shown, "reference_candidates": references,
            "stale_source_paths": stale,
            "boundary": "Machine OOXML style/content and lexical curation only; inspect real pages, image meaning, rights and source suitability before applying."}


def search_layout_examples(library: Path, query: str, *, collection: str = "auto",
                           roles: list[str] | None = None, limit: int = 10) -> dict:
    """Find page references without confusing visual review and editability."""
    if not query.strip() or collection not in {"auto", "creative", "career"} or not 1 <= limit <= 30:
        raise ValueError("query, collection=auto|creative|career and limit=1..30 are required")
    library = library.resolve()
    require_library_available(library)
    index = library / "REUSABLE-LAYOUTS.json"
    packet = json.loads(index.read_text(encoding="utf-8")) if index.is_file() else {"entries": []}
    attached = _layout_examples(library)
    evidence = {item["id"]: item for items in attached.values() for item in items}
    intent = _collection_intent(query) if collection == "auto" else collection
    wanted = _requested_roles(query, roles)
    aliases = {"evidence": "evidence_results", "closing_or_plan": "closing_outlook"}
    words = query.casefold().split()
    collection_words = set(_CAREER_CUES + _CREATIVE_CUES) | {"ppt", "pptx", "模板", "版式"}
    content_words = [word for word in words if word not in collection_words]
    rows, stale = [], []
    for entry in packet.get("entries", []):
        if entry.get("id") not in evidence:
            continue
        if entry.get("design_review", {}).get("decision") == "reject_as_design_reference":
            continue
        source_path = entry.get("source_path", "")
        family = "creative" if source_path.startswith(CREATIVE + "/") else "career" if source_path.startswith(CAREER + "/") else None
        if intent and family != intent:
            continue
        role = aliases.get(entry.get("page_role"), entry.get("page_role"))
        if wanted and role not in wanted:
            continue
        if entry.get("record_kind") == "design_reference" and not evidence[entry["id"]]["design_preview"]["current"]:
            stale.append(entry["id"])
            continue
        description = " ".join(str(entry.get(key, "")) for key in
                               ("source_path", "source_title", "use_case", "page_role", "page_role_label", "slot_map",
                                "topic_fit", "layout_relationships", "image_roles"))
        description = description.casefold()
        # Sharing the broad collection is not a match for a specific page task.
        if content_words and not any(word in description for word in content_words):
            continue
        score = sum(word in description for word in words)
        if not score:
            continue
        rows.append({**evidence[entry["id"]], "source_path": source_path,
                     "source_sha256": entry["source_sha256"], "score": score,
                     "reuse_status": "reference_only", "case_content_replacement_required": True})
    # Relevance stays first. For equally relevant pages, prefer an explicit
    # content-layout judgment over an unassessed technical replacement trial.
    rows.sort(key=lambda row: (-row["score"],
                              row["design_review"].get("decision") != "use_specific_structure",
                              not str(row.get("status", "")).startswith("curated"), row["id"]))
    current, checked = [], {}
    for row in rows:
        source = _safe_source(library, row["source_path"])
        digest = checked.setdefault(str(source), None)
        if source and digest is None:
            digest = checked[str(source)] = _sha(source)
        if not source or digest != row["source_sha256"]:
            stale.append(row["id"])
            continue
        current.append(row)
        if len(current) >= limit:
            break
    return {"schema": "aippt.layout-experiment-search.v1", "query": query,
            "status": "examples_found" if current else "no_match", "examples": current,
            "requested_roles": wanted, "stale_examples": stale,
            "boundary": "Design-reference judgments describe specific topic/layout relationships; slot experiments describe tested operations. Neither grants source facts, media rights, editability, native approval or automatic application."}


__all__ = ["search_style_library", "search_layout_examples"]
