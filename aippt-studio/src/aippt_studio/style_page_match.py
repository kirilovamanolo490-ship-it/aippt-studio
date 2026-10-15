"""Retrieve editable source pages for scoped presentation re-layouts.

This module ranks individual pages by message/function and layout evidence. It
only proposes page mappings; it never copies objects or mutates a presentation.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import re
from pathlib import Path
from typing import Any

from .library_read_guard import require_library_available

from .style_search import (CAREER, CREATIVE, _curation, _occasion_guard, _rows,
                           _safe_source)

_SHA = re.compile(r"^[0-9a-f]{64}$")
_STOP = set("本项目 当前 通过 进行 方面 以及 相关 主要 具体 建立 实现 展示 介绍 分析 研究 方法 情况 过程".split())
_ROLE_SYNONYMS = {
    "problem": ("问题", "痛点", "背景", "需求", "困境"),
    "method": ("方案", "方法", "路径", "技术", "流程", "架构"),
    "evidence": ("成果", "结果", "数据", "证据", "成效", "案例", "实践"),
    "plan": ("计划", "行动", "阶段", "时间", "成长", "实施"),
    "team": ("团队", "成员", "分工", "组织"),
    "market": ("市场", "用户", "商业", "运营", "模式"),
    "career_goal": ("职业目标", "目标岗位", "职业定位", "发展目标"),
    "self_assessment": ("自我认知", "能力", "优势", "兴趣", "测评"),
}
_SYSTEM_COLORS = {"windowtext", "window", "tx1", "bg1", "tx2", "bg2"}
_GENERIC_TOPIC_TERMS = {"实践", "成长", "提升", "服务", "能力", "证据", "成果", "问题", "方案",
                       "发展", "职业", "项目", "计划", "分析", "结果", "工作", "目标", "行动",
                       "通过", "展示", "说明", "学生", "人员", "内容", "建设", "研究",
                       "个人简介", "个人信息", "自我介绍", "团队介绍", "团队成员", "项目介绍"}
_PROFESSION_DOMAINS = {
    "nursing": ("护理", "护士", "临床照护", "护理学", "养老护理"),
    "psychology": ("心理咨询", "心理治疗", "心理健康", "心理咨询师"),
    "accounting": ("会计", "财务", "审计"),
    "arts_education": ("美育", "美术", "绘画", "艺术教育"),
    "mechanical": ("机械制造", "机械工程", "机电", "机械"),
    "electrical": ("电气工程", "电气", "电力工程", "电力"),
    "civil_engineering": ("土木工程", "土木建筑", "建筑工程"),
    "social_work": ("社会工作", "社会工作者", "社工"),
}


def _profession_domain(text: str) -> tuple[str | None, list[str]]:
    normalized = text.casefold()
    matches = [(domain, [term for term in terms if term in normalized])
               for domain, terms in _PROFESSION_DOMAINS.items()]
    matches = [(domain, terms) for domain, terms in matches if terms]
    # A specific multiword cue is more useful than a broad substring. Keep all
    # domains for conflict detection, while returning every observed cue.
    return (matches[0][0] if len(matches) == 1 else None,
            [f"{domain}:{term}" for domain, terms in matches for term in terms])


def _terms(text: str) -> set[str]:
    text = text.casefold()
    terms = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text))
    # Chinese whole-run tokens alone are too strict. Add overlapping bigrams,
    # which capture shared concepts without requiring an NLP installation.
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(run[i:i + 2] for i in range(len(run) - 1))
    return {term for term in terms if term not in _STOP and len(term) > 1}


def _layout_matches(wanted: list[str], observed: dict[str, Any]) -> tuple[float, list[str]]:
    if not wanted:
        return 0.0, []
    kind = str(observed.get("kind", ""))
    aliases = {
        "左图右文": "image_left_text_right", "图片左文右": "image_left_text_right",
        "左文右图": "text_left_image_right", "右图左文": "text_left_image_right",
        "图片在右": "text_left_image_right", "图片在左": "image_left_text_right",
        "双图": "multi_image_gallery", "图片墙": "multi_image_gallery",
        "照片墙": "multi_image_gallery", "图表": "chart_led",
    }
    matched = [item for item in wanted if
               (aliases.get(item.casefold(), item.casefold()) in kind.casefold()) or
               (item.casefold() in {"图文", "图文并列"} and "image" in kind) or
               (item.casefold() in {"图表", "数据图表"} and "chart" in kind)]
    return len(matched) / len(wanted), matched


def _functional_text(page: dict[str, Any]) -> str:
    role_text = " ".join(str(item.get("role", "")) for item in page.get("role_evidence", []))
    return " ".join((str(page.get("title", "")), str(page.get("text_excerpt", "")), role_text))


def _cross_collection_topic_match(project: dict[str, Any], target: dict[str, Any],
                                  page: dict[str, Any], *, strict_role_match: bool) -> bool:
    """Require profession or same-role, evidence-specific support cross-index."""
    source = _functional_text(page).casefold()
    profession = str(project.get("profession", "")).strip().casefold()
    if profession:
        if profession in source:
            return True
        # Without the named profession on the source page, allow only a strict
        # same-role page carrying at least one concrete required-evidence term.
        evidence_text = " ".join(str(target.get(key, "")) for key in ("required_evidence", "content"))
        evidence = {term for term in _terms(evidence_text)
                    if len(term) >= 3 and term not in _GENERIC_TOPIC_TERMS}
        source_terms = _terms(source)
        return strict_role_match and bool(evidence & source_terms)
    explicit = [str(project.get(key, "")).strip().casefold()
                for key in ("topic", "subject", "competition")
                if isinstance(project.get(key), str) and len(project.get(key).strip()) >= 2]
    if explicit:
        return any(anchor in source for anchor in explicit)
    target_text = " ".join(str(target.get(key, "")) for key in ("required_evidence", "content"))
    anchors = {term for term in _terms(target_text)
               if len(term) >= 3 and term not in _GENERIC_TOPIC_TERMS}
    return bool(anchors & _terms(source))


def _page_score(target: dict[str, Any], page: dict[str, Any], role: str) -> tuple[float, dict[str, Any]]:
    target_text = " ".join(str(target.get(key, "")) for key in ("message", "content", "required_evidence"))
    target_terms, page_terms = _terms(target_text), _terms(_functional_text(page))
    overlap = target_terms & page_terms
    content_ratio = len(overlap) / max(1, len(target_terms))
    target_role = role.casefold()
    page_roles = {str(item.get("role", "")).casefold() for item in page.get("role_evidence", [])}
    role_match = target_role in page_roles
    role_concept_match = any(alias in target_role and alias in _functional_text(page).casefold()
                              for aliases in _ROLE_SYNONYMS.values() for alias in aliases)
    layout_score, layout_matches = _layout_matches(
        [str(item) for item in target.get("layout_preferences", [])], page.get("layout_pattern") or {})
    score = content_ratio * 6 + (3 if role_match else 0) + (1 if role_concept_match else 0) + layout_score * 2
    composition = page.get("composition") or {}
    text_objects = int(composition.get("text_object_count") or
                       (composition.get("object_counts") or {}).get("text_shape", 0))
    pictures = int(page.get("image_count") or (composition.get("object_counts") or {}).get("pic", 0))
    if text_objects:
        score += 1
    evidence = {
        "content_term_overlap": sorted(overlap)[:20],
        "content_coverage": round(content_ratio, 3),
        "exact_role_match": role_match,
        "layout_preference_match": layout_matches,
        "layout_preference_coverage": round(layout_score, 3),
        "editable_text_object_count": text_objects,
        "image_object_count": pictures,
        "score_formula": "content coverage*6 + exact role*3 + role concept*1 + layout coverage*2 + editable text*1",
    }
    return round(score, 3), evidence


def _structure(layout: dict[str, Any], evidence: dict[str, Any], deck: dict[str, Any]) -> dict[str, Any]:
    return {
        "layout_kind": layout.get("kind", "unknown"),
        "layout_basis": layout.get("basis"),
        "object_counts": (evidence.get("composition") or {}).get("object_counts", {}),
        "text_objects": evidence.get("editable_text_object_count", 0),
        "image_objects": evidence.get("image_object_count", 0),
        "chart_count": evidence.get("chart_count", 0),
        "table_count": evidence.get("table_count", 0),
        "media_master_dependencies": {
            "theme_parts": (deck.get("style_profile") or {}).get("theme_parts", {}),
            "layout_usage": (deck.get("style_profile") or {}).get("layout_usage", {}),
            "master_usage": (deck.get("style_profile") or {}).get("master_usage", {}),
        },
    }


def _detail_size(library: Path, row: dict[str, Any]) -> dict[str, int] | None:
    detail_ref = row.get("detail_ref")
    if not isinstance(detail_ref, str):
        return None
    path = _safe_source(library, detail_ref)
    if not path:
        return None
    try:
        import json
        payload = json.loads(path.read_text(encoding="utf-8"))
        size = payload.get("presentation_size") or {}
        width, height = size.get("width_emu"), size.get("height_emu")
        if isinstance(width, (int, float)) and isinstance(height, (int, float)) and width > 0 and height > 0:
            return {"width_emu": int(width), "height_emu": int(height)}
    except (OSError, ValueError, TypeError):
        pass
    return None


def _profile_colors(profile: dict[str, Any]) -> set[str]:
    colors = set()
    palette = profile.get("theme_palette") or {}
    for key, value in palette.items():
        if str(key).casefold() in {"accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "dk2", "lt2"}:
            if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{6}", value):
                colors.add(value.upper())
    for item in profile.get("explicit_fill_colors_top") or []:
        if isinstance(item, list) and item and isinstance(item[0], str) and re.fullmatch(r"[0-9a-fA-F]{6}", item[0]):
            colors.add(item[0].upper())
    return colors


def _color_distance(first: str, second: str) -> float:
    a = tuple(int(first[i:i + 2], 16) for i in (0, 2, 4))
    b = tuple(int(second[i:i + 2], 16) for i in (0, 2, 4))
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** .5 / (3 ** .5 * 255)


def _font_names(profile: dict[str, Any]) -> set[str]:
    fonts = set()
    for group in (profile.get("theme_fonts") or {}).values():
        if not isinstance(group, dict):
            continue
        for value in (group.get("latin"), group.get("ea"), group.get("cs")):
            if isinstance(value, str) and value.strip() and value.casefold() not in _SYSTEM_COLORS:
                fonts.add(value.strip().casefold())
    for item in profile.get("font_typefaces_top") or []:
        if isinstance(item, list) and item and isinstance(item[0], str) and item[0].strip():
            fonts.add(item[0].strip().casefold())
    return fonts


def _visual_coherence(main: dict[str, Any] | None, candidate: dict[str, Any],
                      library: Path) -> dict[str, Any]:
    """Compare measurable deck-level theme and canvas cues; advisory only."""
    if not main:
        return {"score": None, "status": "main_template_not_found_by_sha",
                "gap": {"theme": "unknown", "fonts": "unknown", "page_dimensions": "unknown"},
                "adjustments": ["确认当前整稿 SHA 对应的 STYLE-CATALOG 记录后再评估配色、字体与画布。"]}
    main_profile = main.get("style_profile") or {}
    candidate_profile = candidate.get("style_profile") or {}
    main_colors, candidate_colors = _profile_colors(main_profile), _profile_colors(candidate_profile)
    if main_colors and candidate_colors:
        color_similarity = max(0.0, 1 - (sum(min(_color_distance(a, b) for b in candidate_colors) for a in main_colors) /
                                         len(main_colors) +
                                         sum(min(_color_distance(b, a) for a in main_colors) for b in candidate_colors) /
                                         len(candidate_colors)) / 2)
    else:
        color_similarity = None
    main_fonts, candidate_fonts = _font_names(main_profile), _font_names(candidate_profile)
    font_similarity = (len(main_fonts & candidate_fonts) / max(1, len(main_fonts | candidate_fonts))
                       if main_fonts and candidate_fonts else None)
    main_size, candidate_size = _detail_size(library, main), _detail_size(library, candidate)
    main_ratio = main_profile.get("page_aspect_ratio")
    candidate_ratio = candidate_profile.get("page_aspect_ratio")
    if main_size:
        main_ratio = main_size["width_emu"] / main_size["height_emu"]
    if candidate_size:
        candidate_ratio = candidate_size["width_emu"] / candidate_size["height_emu"]
    if isinstance(main_ratio, (int, float)) and isinstance(candidate_ratio, (int, float)) and main_ratio > 0:
        ratio_gap = abs(float(main_ratio) - float(candidate_ratio)) / float(main_ratio)
        dimension_similarity = max(0.0, 1 - min(1.0, ratio_gap * 8))
        dimension_gap = {"main_aspect_ratio": round(float(main_ratio), 5),
                         "candidate_aspect_ratio": round(float(candidate_ratio), 5),
                         "relative_aspect_gap": round(ratio_gap, 5),
                         "main_width_height_emu": main_size,
                         "candidate_width_height_emu": candidate_size}
    else:
        dimension_similarity, dimension_gap = None, {"main_width_height_emu": main_size,
                                                      "candidate_width_height_emu": candidate_size,
                                                      "status": "dimensions_unavailable"}
    values = [(color_similarity, .45), (font_similarity, .25), (dimension_similarity, .30)]
    available = [(value, weight) for value, weight in values if value is not None]
    score = round(100 * sum(value * weight for value, weight in available) / sum(weight for _, weight in available), 1) if available else None
    if main_colors and candidate_colors:
        color_gap = {"main_theme_and_fill_colors": sorted(main_colors), "candidate_theme_and_fill_colors": sorted(candidate_colors),
                     "nearest_palette_distance": round(1 - color_similarity, 3)}
    else:
        color_gap = {"status": "palette_unavailable"}
    font_gap = {"main_font_families": sorted(main_fonts), "candidate_font_families": sorted(candidate_fonts),
                "shared_font_families": sorted(main_fonts & candidate_fonts),
                "similarity": round(font_similarity, 3) if font_similarity is not None else None}
    adjustments = []
    if color_similarity is None or color_similarity < .78:
        adjustments.append("按当前整稿主色与辅色重映射候选页的主题色和显式填充色，再检查对比度。")
    if font_similarity is None or font_similarity < .5:
        adjustments.append("将候选页字体映射到当前整稿可用字体，并复核换字体后的换行与溢出。")
    if dimension_similarity is None or dimension_similarity < .95:
        adjustments.append("按当前整稿画布比例重新排布候选页对象；不要直接沿用源页坐标。")
    else:
        adjustments.append("画布比例接近；仍需按当前整稿母版检查边距、页眉和页脚关系。")
    return {"score": score, "status": "measured_advisory", "components": {
                "palette_similarity": round(color_similarity, 3) if color_similarity is not None else None,
                "font_similarity": round(font_similarity, 3) if font_similarity is not None else None,
                "page_dimension_similarity": round(dimension_similarity, 3) if dimension_similarity is not None else None},
            "gap": {"palette": color_gap, "fonts": font_gap, "page_dimensions": dimension_gap},
            "adjustments": adjustments,
            "basis": "deck-level theme palette and explicit fill colors, theme/typeface family names, and indexed page dimensions; not visual approval"}


def retrieve_page_layout_candidates(library: Path, request: dict[str, Any], *, limit: int = 5) -> dict[str, Any]:
    """Find 2..5 source-page mappings for one replacement page.

    Required request fields:
      ``current_project``: occasion plus optional competition, track,
      profession, and collection (``creative`` or ``career``). The collection
      is inferred from the current template SHA when it exists in one indexed
      collection; the request value is only a fallback or ambiguity resolver.
      ``current_template_source_sha256``: SHA-256 of the current full-deck
      template, used to avoid recommending that same deck as a substitute.
      ``target_page``: message, role, optional content, layout_preferences,
      and required_evidence.

    Candidates must be native PPTX sources with current SHA-bound files and
    editable text objects. Reference decks are eligible only as layout-only
    examples after strict same-role and topic checks. A successful result can contain fewer
    than two candidates when the indexes do not provide enough valid matches.
    """
    if not isinstance(request, dict) or not 1 <= limit <= 5:
        raise ValueError("request must be an object and limit must be 1..5")
    current_sha = request.get("current_template_source_sha256")
    project, target = request.get("current_project") or {}, request.get("target_page") or {}
    if not isinstance(current_sha, str) or not _SHA.fullmatch(current_sha.lower()):
        raise ValueError("current_template_source_sha256 must be a 64-character SHA-256")
    if not isinstance(project, dict) or not isinstance(target, dict) or not target.get("message") or not target.get("role"):
        raise ValueError("current_project and target_page.message/role are required")
    requested_collection = project.get("collection")
    if requested_collection not in {None, "creative", "career", "gaojie"}:
        raise ValueError("current_project.collection must be creative, career or gaojie")
    if requested_collection == "gaojie" or target.get("layout_collection") == "gaojie":
        from .gaojie_layouts import search_gaojie_layouts
        query = " ".join(str(target.get(key) or "") for key in ("category", "message", "content", "layout_preferences"))
        result = search_gaojie_layouts(library, query, category=target.get("category"),
                                      roles=None if target.get("category") else [str(target["role"])], limit=limit)
        candidates = [row for row in result["candidates"] if row["source_sha256"] != current_sha]
        for row in candidates:
            row.update(reuse_mode="source_template_page", copy_action="review_source_and_map_current_content_before_copy",
                       adaptation_plan={"content_and_media": "replace_source_example_facts_with_current_project_evidence",
                                        "palette_and_fonts": "match_current_full_deck_and_review_neighbors",
                                        "canvas_and_master": "verify_size_and_dependencies_before_import"})
        return {**result, "schema": "aippt.gaojie-style-page-match.v1",
                "current_template_source_sha256": current_sha, "current_template_collection": None,
                "layout_collection": "gaojie", "collection_resolution": "explicit_donor_layout_collection", "candidate_count": len(candidates),
                "candidates": candidates, "reference_candidates": [],
                "status": "ready" if candidates else "no_match"}
    library = Path(library).resolve()
    require_library_available(library)
    roots = {"creative": library / CREATIVE, "career": library / CAREER}
    curations = _curation(roots["creative"], roots["career"])
    # Search both indexes. Same-collection entries may be considered for
    # adaptation; cross-collection entries require stricter page-level proof
    # and are emitted only as layout-only references.
    collections = ["creative", "career"]
    context_query = " ".join(str(project.get(key, "")) for key in
                              ("occasion", "competition", "track", "profession"))
    query = (context_query + " " + str(target["message"]) + " " + str(target.get("content", "")) +
             " " + str(target["role"])).strip().casefold()
    candidates = []
    stale = []
    main_decks = []
    for main_collection in ("creative", "career"):
        for row in _rows(roots[main_collection] / "分析索引/STYLE-CATALOG.jsonl"):
            if row.get("source_sha256") == current_sha and row.get("status") == "parsed":
                source = _safe_source(library, row.get("path", ""))
                if source and _sha256(source) == current_sha:
                    main_decks.append((main_collection, row))
    main_collections = {key for key, _ in main_decks}
    if len(main_collections) == 1:
        current_collection = next(iter(main_collections))
        collection_resolution = "current_template_sha256"
    elif requested_collection in main_collections:
        current_collection = requested_collection
        collection_resolution = "sha256_present_in_both_indexes_request_used_to_disambiguate"
    elif len(main_collections) > 1:
        current_collection = None
        collection_resolution = "ambiguous_sha256_in_both_indexes"
    else:
        current_collection = requested_collection
        collection_resolution = "request_fallback_sha256_not_in_catalog"
    main_deck = next((row for key, row in main_decks if key == current_collection), None)
    for key in collections:
        analysis = roots[key] / "分析索引"
        deck_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in _rows(analysis / "STYLE-CATALOG.jsonl"):
            if row.get("status") == "parsed" and row.get("source_format") == "pptx":
                deck_groups[row.get("source_sha256", "")].append(row)
        pages_by_sha: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for page in _rows(analysis / "STYLE-PAGES.jsonl"):
            pages_by_sha[page.get("source_sha256", "")].append(page)
        for digest, deck_rows in deck_groups.items():
            if not isinstance(digest, str) or not _SHA.fullmatch(digest) or digest == current_sha:
                continue
            pages = pages_by_sha.get(digest, [])
            # A SHA may have several paths. Resolve the first alias whose own
            # curation is current, permits reuse, and whose file still hashes
            # to that SHA; an earlier non-template alias must not hide it.
            selected = None
            stale_aliases = []
            for deck in sorted(deck_rows, key=lambda row: row.get("path", "")):
                source_path = deck.get("path", deck.get("canonical_path", ""))
                meta = curations.get(source_path, {})
                if meta.get("source_sha256") != digest or meta.get("curation_stale") is True:
                    continue
                asset_type = meta.get("asset_type")
                # Completed career examples are not copy sources. Permit a
                # narrowly scoped reference route; eligibility is confirmed
                # below per page by explicit profession and exact functional role.
                reference_only = asset_type in {"reference", "portfolio"} and key == current_collection
                if meta.get("flattened_page_structure") is True or meta.get("off_topic") is True:
                    continue
                if asset_type not in {"template", "template_with_example_content"} and not reference_only:
                    continue
                source = _safe_source(library, source_path)
                if not source or _sha256(source) != digest:
                    stale_aliases.append(source_path)
                    continue
                compatible, guard_reason = _occasion_guard(query, key, deck, meta, pages)
                source_domain_cues = _profession_domain(" ".join((source_path, *(str(p.get("title", "")) + " " +
                                                       str(p.get("text_excerpt", "")) for p in pages))))[1]
                requested_domain = _profession_domain(str(project.get("profession", "")))[0]
                if (not compatible and reference_only and guard_reason == "growth_track_not_evidenced" and
                        requested_domain and any(cue.startswith(requested_domain + ":") for cue in source_domain_cues)):
                    # An unspecified-track career example may inform a page
                    # layout when the nursing domain is explicit; track content
                    # still must be replaced and no full-deck reuse is implied.
                    compatible, guard_reason = True, "profession_match_track_unspecified_layout_only"
                if compatible:
                    selected = (deck, meta, source_path, guard_reason)
                    break
            if not selected:
                stale.extend(stale_aliases)
                continue
            deck, meta, selected_path, guard_reason = selected
            for page in pages:
                composition = page.get("composition") or {}
                object_counts = composition.get("object_counts") or {}
                text_count = int(composition.get("text_object_count") or object_counts.get("text_shape", 0))
                total = sum(int(value) for value in object_counts.values() if isinstance(value, (int, float)))
                image_count = int(page.get("image_count") or object_counts.get("pic", 0))
                # Flattened pages and image-dominant pseudo-pages are not
                # useful editable sources for a local layout swap.
                if text_count < 2 or (total and image_count >= total and not text_count):
                    continue
                score, score_evidence = _page_score(target, page, str(target["role"]))
                if not score_evidence["content_term_overlap"] and not score_evidence["exact_role_match"]:
                    continue
                requested_domain, requested_domain_cues = _profession_domain(str(project.get("profession", "")))
                domain_text = " ".join((selected_path, str(page.get("title", "")),
                                        str(page.get("text_excerpt", ""))))
                candidate_domain, candidate_domain_cues = _profession_domain(domain_text)
                candidate_domains = {item.split(":", 1)[0] for item in candidate_domain_cues}
                if requested_domain and candidate_domains and requested_domain not in candidate_domains:
                    continue
                if requested_domain and requested_domain in candidate_domains:
                    domain_status = "explicit_profession_match"
                    score += 2.0
                elif requested_domain:
                    domain_status = "uncertain_no_explicit_profession_evidence"
                    score -= 1.5
                else:
                    domain_status = "not_assessed"
                score_evidence["profession_domain"] = {
                    "requested_domain": requested_domain,
                    "requested_cues": requested_domain_cues,
                    "candidate_domains": sorted(candidate_domains),
                    "candidate_cues": candidate_domain_cues,
                    "status": domain_status,
                    "uncertainty_score_adjustment": -1.5 if domain_status == "uncertain_no_explicit_profession_evidence" else 0,
                }
                cross_collection = current_collection is None or key != current_collection
                reference_only = meta.get("asset_type") in {"reference", "portfolio"}
                if reference_only and (not requested_domain or requested_domain not in candidate_domains or
                                       not score_evidence["exact_role_match"] or
                                       not (requested_domain and requested_domain in candidate_domains and
                                            (requested_domain == _profession_domain(_functional_text(page))[0] or
                                             requested_domain == _profession_domain(selected_path)[0]))):
                    continue
                if cross_collection and (not score_evidence["exact_role_match"] or
                                         score_evidence["content_coverage"] < .15 or
                                         not _cross_collection_topic_match(project, target, page,
                                                                          strict_role_match=score_evidence["exact_role_match"])):
                    continue
                layout = page.get("layout_pattern") or {}
                risks = ["母版/版式/主题与当前整稿可能不兼容，复制前需核对关系和版面。",
                         "页级索引不能证明图片含义、版权或最终视觉效果。"]
                if meta.get("asset_type") == "template_with_example_content":
                    risks.append("来源含示例内容；复制时必须替换姓名、事实、数字和媒体。")
                if reference_only:
                    risks.extend(["该案例仅提供版式参考；旧文案、图片、数据、人物及事实均不可迁用。",
                                  "按当前项目重新映射全部内容对象、媒体对象和主题样式。"])
                if text_count <= 2:
                    risks.append("可编辑文本对象较少，可能需要重新构建内容层级。")
                item = {
                    "source_path": selected_path,
                    "source_sha256": digest,
                    "collection": key,
                    "source_slide": page.get("slide"),
                    "source_title": page.get("title"),
                    "source_text_excerpt": page.get("text_excerpt", "")[:420],
                    "target_page_message": target["message"],
                    "target_page_role": target["role"],
                    "layout_structure": _structure(layout, {**page, "composition": composition,
                                                            "editable_text_object_count": text_count,
                                                            "image_object_count": image_count}, deck),
                    "source_objects": {
                        "text_and_content_objects": [
                            {"id": obj.get("id"), "name": obj.get("name"), "role": obj.get("role"),
                             "text": str(obj.get("text", ""))[:240]}
                            for obj in (composition.get("hierarchy") or [])
                            if obj.get("text") or obj.get("role") == "title"],
                        "image_object_count": image_count,
                        "remapping_estimate": {"text_objects_to_rewrite_or_rebuild": text_count,
                                               "image_objects_to_replace_or_revalidate": image_count,
                                               "total_objects_requiring_full_remap": text_count + image_count},
                    },
                    "score": round(score, 3),
                    "visual_coherence": _visual_coherence(main_deck, deck, library),
                    "score_evidence": {**score_evidence, "occasion_guard": guard_reason,
                                       "curated_asset_type": meta.get("asset_type"),
                                       "curation_confidence": meta.get("confidence")},
                    "risks": risks,
                    "reuse_mode": "layout_only_reference" if (cross_collection or reference_only) else "same_collection_page_candidate",
                    "adaptation_plan": {
                        "content_and_media": "replace_all_source_copy_claims_numbers_people_and_media_with_current_project_evidence",
                        "palette": "remap_to_current_main_template" if cross_collection else "preserve_if_visual_coherence_is_acceptable_else_remap_to_current_main_template",
                        "fonts": "map_to_current_main_template_and_recheck_wrapping",
                        "canvas_and_master": "rebuild_or_realign_on_current_main_template_canvas; do_not_import_source_master_automatically",
                        "dependency_actions": ["retain only the selected page's useful object hierarchy",
                                               "remove source-only master/theme/media dependencies unless explicitly needed",
                                               "bind current-project assets with verified rights"],
                    },
                    "copy_action": "review_only_no_automatic_copy",
                    "media_rights": "unverified",
                }
                candidates.append(item)
    candidates.sort(key=lambda item: (item["reuse_mode"] == "layout_only_reference",
                                     -item["score"],
                                     -(item["visual_coherence"].get("score") or 0),
                                     item["source_path"], item["source_slide"]))
    # Prefer different source decks so the shortlist offers genuinely varied
    # composition options rather than adjacent pages from one template.
    def distinct(items: list[dict], cap: int) -> list[dict]:
        chosen, seen = [], set()
        for item in items:
            if item["source_sha256"] in seen:
                continue
            chosen.append(item)
            seen.add(item["source_sha256"])
            if len(chosen) == cap:
                break
        return chosen

    local = distinct([item for item in candidates if item["reuse_mode"] != "layout_only_reference"], limit)
    references = distinct([item for item in candidates if item["reuse_mode"] == "layout_only_reference"], limit)
    chosen = local or references
    return {"schema": "aippt.competition-style-page-match.v1",
            "status": "ready" if chosen else "no_match",
            "current_template_source_sha256": current_sha,
            "current_template_collection": current_collection,
            "collection_resolution": collection_resolution,
            "target_page": {"message": target["message"], "role": target["role"]},
            "candidate_count": len(chosen), "candidates": chosen,
            "reference_candidate_count": len(references), "reference_candidates": references,
            "stale_source_paths": sorted(set(filter(None, stale))),
            "boundary": "Page-level retrieval only; inspect actual source and target pages before selecting, then use a separately authorized source-preserving copy workflow."}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


__all__ = ["retrieve_page_layout_candidates"]
