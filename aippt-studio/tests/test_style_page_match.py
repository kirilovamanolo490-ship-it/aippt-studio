from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aippt_studio.style_page_match import retrieve_page_layout_candidates


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _append_jsonl(path: Path, row: dict) -> None:
    previous = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
    _jsonl(path, [*previous, row])


def _add_deck(library: Path, rel: str, body: bytes, *, track: str = "growth",
              asset: str = "blank_template", flattened: bool = False,
              accent: str = "690407", font: str = "思源黑体 CN Regular",
              dimensions: tuple[int, int] = (12192000, 6858000)) -> tuple[str, str]:
    path = library / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    digest = hashlib.sha256(body).hexdigest()
    root = "BB0086 大学生职业生涯规划大赛 PPT"
    analysis = library / root / "分析索引"
    page = {"canonical_path": rel, "source_sha256": digest, "slide": 7,
            "title": "护理实践成果与能力证据", "text_excerpt": "护理实践 病区沟通 老年照护 真实案例 服务成果",
            "role_evidence": [{"role": "evidence_results"}], "image_count": 1,
            "chart_count": 0, "table_count": 1,
            "layout_pattern": {"kind": "text_left_image_right", "basis": "explicit_top_level_geometry_heuristic"},
            "composition": {"text_object_count": 6,
                            "object_counts": {"text_shape": 6, "pic": 1, "graphicFrame": 1}}}
    detail_rel = f"{root}/分析索引/逐份结构/{digest}.json"
    detail_path = library / detail_rel
    detail_path.parent.mkdir(parents=True, exist_ok=True)
    detail_path.write_text(json.dumps({"source_sha256": digest, "presentation_size": {
        "width_emu": dimensions[0], "height_emu": dimensions[1]}}, ensure_ascii=False), encoding="utf-8")
    deck = {"path": rel, "source_sha256": digest, "source_format": "pptx", "status": "parsed",
            "slide_count": 18, "detail_ref": detail_rel,
            "style_profile": {"page_aspect_ratio": dimensions[0] / dimensions[1],
                              "theme_palette": {"accent1": accent, "accent2": "FFC279"},
                              "explicit_fill_colors_top": [[accent, 5]],
                              "theme_fonts": {"majorFont": {"latin": font}, "minorFont": {"latin": font}},
                              "font_typefaces_top": [[font, 10]],
                              "theme_parts": {"ppt/theme/theme1.xml": 1},
                              "layout_usage": {"layout1": 18}, "master_usage": {"master1": 18}}}
    _append_jsonl(analysis / "STYLE-CATALOG.jsonl", deck)
    _append_jsonl(analysis / "STYLE-PAGES.jsonl", page)
    _append_jsonl(analysis / "SOURCE-TAXONOMY.jsonl", {"source_path": rel.removeprefix(root + "/"),
          "sha256": digest, "refined_taxonomy": {"track": track, "asset_type": asset,
          "asset_type_confidence": "high"}})
    if flattened:
        _append_jsonl(library / "CONTENT-CURATION.jsonl", {"path": rel, "sha256": digest,
              "asset_type": "reference", "confidence": "high",
              "classification_observation": {"flattened_page_structure": True}})
    return rel, digest


def test_page_retrieval_maps_function_and_layout_and_filters_invalid_sources(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    root = "BB0086 大学生职业生涯规划大赛 PPT"
    good_path, good_sha = _add_deck(library, f"{root}/成长模板/护理成长模板.pptx", b"editable")
    _add_deck(library, f"{root}/就业模板/护理就业模板.pptx", b"wrong-track", track="employment")
    _add_deck(library, f"{root}/案例/扁平化护理案例.pptx", b"flattened", flattened=True)
    # Same-source pages cannot be offered as a replacement for the current deck.
    current_path, current_sha = _add_deck(library, f"{root}/当前稿/当前模板.pptx", b"current")
    request = {"current_project": {"occasion": "大学生职业生涯规划大赛", "track": "成长赛道",
                                   "profession": "护理", "collection": "career"},
               "current_template_source_sha256": current_sha,
               "target_page": {"message": "呈现护理实践提升老年照护沟通能力的成果证据",
                               "role": "evidence_results", "layout_preferences": ["text_left_image_right"],
                               "required_evidence": "病区沟通真实案例服务成果"}}
    result = retrieve_page_layout_candidates(library, request, limit=5)
    assert result["status"] == "ready"
    assert len(result["candidates"]) == 1
    candidate = result["candidates"][0]
    assert candidate["source_path"] == good_path
    assert candidate["source_sha256"] == good_sha
    assert candidate["source_slide"] == 7
    assert candidate["score_evidence"]["content_term_overlap"]
    assert candidate["score_evidence"]["exact_role_match"] is True
    assert candidate["score_evidence"]["occasion_guard"] == "source_context_evidenced"
    assert candidate["layout_structure"]["layout_kind"] == "text_left_image_right"
    assert candidate["layout_structure"]["media_master_dependencies"]["master_usage"] == {"master1": 18}
    assert candidate["visual_coherence"]["status"] == "measured_advisory"
    assert candidate["visual_coherence"]["score"] == 100
    assert candidate["visual_coherence"]["gap"]["page_dimensions"]["main_width_height_emu"]["width_emu"] == 12192000
    assert candidate["adaptation_plan"]["palette"].startswith("preserve")
    assert candidate["copy_action"] == "review_only_no_automatic_copy"
    assert current_path not in [item["source_path"] for item in result["candidates"]]


def test_page_retrieval_rejects_bad_current_sha_and_changed_source(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    root = "BB0086 大学生职业生涯规划大赛 PPT"
    path, digest = _add_deck(library, f"{root}/成长模板/护理成长模板.pptx", b"before")
    request = {"current_project": {"occasion": "职业生涯规划大赛", "track": "成长赛道",
                                   "profession": "护理", "collection": "career"},
               "current_template_source_sha256": "0" * 64,
               "target_page": {"message": "护理实践成果证据", "role": "evidence_results"}}
    assert retrieve_page_layout_candidates(library, request)["status"] == "ready"
    (library / path).write_bytes(b"after")
    result = retrieve_page_layout_candidates(library, request)
    assert result["status"] == "no_match"
    assert result["stale_source_paths"] == [path]
    request["current_template_source_sha256"] = "invalid"
    try:
        retrieve_page_layout_candidates(library, request)
    except ValueError as exc:
        assert "64-character SHA-256" in str(exc)
    else:
        raise AssertionError("invalid current deck SHA should be rejected")


def test_visual_coherence_reports_theme_font_and_canvas_adjustment_gaps(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    root = "BB0086 大学生职业生涯规划大赛 PPT"
    target = f"{root}/当前稿/当前模板.pptx"
    target_path = library / target
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(b"main")
    current_sha = hashlib.sha256(b"main").hexdigest()
    main_detail = library / root / "分析索引/逐份结构" / f"{current_sha}.json"
    main_detail.parent.mkdir(parents=True, exist_ok=True)
    main_detail.write_text(json.dumps({"source_sha256": current_sha, "presentation_size": {
        "width_emu": 12192000, "height_emu": 6858000}}), encoding="utf-8")
    main_row = {"path": target, "source_sha256": current_sha, "status": "parsed", "source_format": "pptx",
                "detail_ref": str(main_detail.relative_to(library)),
                "style_profile": {"page_aspect_ratio": 16 / 9, "theme_palette": {"accent1": "690407"},
                                  "explicit_fill_colors_top": [["B4070C", 4]],
                                  "theme_fonts": {"majorFont": {"latin": "思源黑体 CN Regular"}}}}
    _jsonl(library / root / "分析索引/STYLE-CATALOG.jsonl", [main_row])
    candidate_path, candidate_sha = _add_deck(library, f"{root}/成长模板/护理成长模板.pptx", b"different-design",
                                               accent="156082", font="Arial", dimensions=(9144000, 6858000))
    request = {"current_project": {"occasion": "职业生涯规划大赛", "track": "成长赛道",
                                   "profession": "护理", "collection": "career"},
               "current_template_source_sha256": current_sha,
               "target_page": {"message": "护理实践成果 服务能力", "role": "evidence_results"}}
    result = retrieve_page_layout_candidates(library, request)
    candidate = next(item for item in result["candidates"] if item["source_path"] == candidate_path)
    coherence = candidate["visual_coherence"]
    assert coherence["score"] < 100
    assert coherence["components"]["palette_similarity"] < 1
    assert coherence["components"]["font_similarity"] < 1
    assert coherence["components"]["page_dimension_similarity"] < 1
    assert coherence["gap"]["page_dimensions"]["candidate_width_height_emu"] == {
        "width_emu": 9144000, "height_emu": 6858000}
    assert any("主题色" in item for item in coherence["adjustments"])
    assert any("字体" in item for item in coherence["adjustments"])
    assert any("画布比例" in item for item in coherence["adjustments"])


def test_sha_alias_uses_curated_usable_path_instead_of_first_catalog_alias(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    root = "BB0086 大学生职业生涯规划大赛 PPT"
    good, digest = _add_deck(library, f"{root}/ZZ可用别名/护理成长模板.pptx", b"same-source")
    alias = f"{root}/AA未分类别名/护理成长模板.pptx"
    alias_file = library / alias
    alias_file.parent.mkdir(parents=True, exist_ok=True)
    alias_file.write_bytes(b"same-source")
    catalog = library / root / "分析索引/STYLE-CATALOG.jsonl"
    rows = [json.loads(line) for line in catalog.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows.append({**rows[0], "path": alias, "canonical_path": alias})
    _jsonl(catalog, rows)
    request = {"current_project": {"occasion": "职业生涯规划大赛", "track": "成长赛道",
                                   "profession": "护理", "collection": "career"},
               "current_template_source_sha256": "0" * 64,
               "target_page": {"message": "护理实践成果 服务能力", "role": "evidence_results"}}
    result = retrieve_page_layout_candidates(library, request)
    assert result["candidates"][0]["source_path"] == good
    assert result["candidates"][0]["source_sha256"] == digest


def test_cross_collection_match_is_explicitly_layout_only_and_requires_replacement(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    career = "BB0086 大学生职业生涯规划大赛 PPT"
    current_path, current_sha = _add_deck(library, f"{career}/当前稿/当前模板.pptx", b"main")
    creative_root = "创赛"
    local_path, _ = _add_deck(library, f"{career}/成长模板/护理成长页面.pptx", b"local-layout")
    source = f"{creative_root}/通用实践模板/护理实践.pptx"
    source_file = library / source
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"creative-layout")
    digest = hashlib.sha256(b"creative-layout").hexdigest()
    analysis = library / creative_root / "分析索引"
    page = {"canonical_path": source, "source_sha256": digest, "slide": 4,
            "title": "实践成果与服务能力", "text_excerpt": "护理实践提升服务能力，形成真实成果证据",
            "role_evidence": [{"role": "evidence_results"}], "image_count": 1,
            "composition": {"text_object_count": 5, "object_counts": {"text_shape": 5, "pic": 1}},
            "layout_pattern": {"kind": "text_left_image_right"}}
    deck = {"path": source, "source_sha256": digest, "source_format": "pptx", "status": "parsed",
            "slide_count": 10, "detail_ref": "missing-detail.json",
            "style_profile": {"page_aspect_ratio": 16 / 9, "theme_palette": {"accent1": "156082"},
                              "theme_fonts": {"majorFont": {"latin": "Arial"}}}}
    _jsonl(analysis / "STYLE-CATALOG.jsonl", [deck])
    _jsonl(analysis / "STYLE-PAGES.jsonl", [page])
    _jsonl(library / creative_root / "分析索引/creativecup-curation.jsonl", [
        {"path": "模板库/" + source, "sha256": digest, "material_type": "模板/框架",
         "category": "社会实践", "confidence": "高"}])
    request = {"current_project": {"occasion": "大学生职业生涯规划大赛", "track": "成长赛道",
                                   "profession": "护理", "collection": "career"},
               "current_template_source_sha256": current_sha,
               "target_page": {"message": "护理实践成果 服务能力", "role": "evidence_results"}}
    result = retrieve_page_layout_candidates(library, request)
    candidate = next(item for item in result["reference_candidates"] if item["collection"] == "creative")
    assert result["current_template_collection"] == "career"
    assert result["collection_resolution"] == "current_template_sha256"
    assert result["candidates"][0]["source_path"] == local_path
    assert result["reference_candidate_count"] >= 1
    assert candidate["reuse_mode"] == "layout_only_reference"
    assert candidate["adaptation_plan"]["content_and_media"].startswith("replace_all")
    assert candidate["adaptation_plan"]["palette"] == "remap_to_current_main_template"
    assert candidate["visual_coherence"]["status"] == "measured_advisory"


def test_cross_collection_without_profession_needs_same_role_and_concrete_evidence(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    career = "BB0086 大学生职业生涯规划大赛 PPT"
    _, current_sha = _add_deck(library, f"{career}/当前稿/护理规划主模板.pptx", b"main")
    creative_root = "创赛"
    source = f"{creative_root}/实践模板/病区实践证据页.pptx"
    source_file = library / source
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_bytes(b"evidence-page")
    digest = hashlib.sha256(b"evidence-page").hexdigest()
    page = {"canonical_path": source, "source_sha256": digest, "slide": 5,
            "title": "实践记录与照护反馈", "text_excerpt": "病区沟通病例 照护记录 服务对象反馈",
            "role_evidence": [{"role": "evidence_results"}], "image_count": 1,
            "composition": {"text_object_count": 5, "object_counts": {"text_shape": 5, "pic": 1}},
            "layout_pattern": {"kind": "text_left_image_right"}}
    deck = {"path": source, "source_sha256": digest, "source_format": "pptx", "status": "parsed",
            "slide_count": 9, "detail_ref": "missing.json", "style_profile": {"page_aspect_ratio": 16 / 9}}
    analysis = library / creative_root / "分析索引"
    _jsonl(analysis / "STYLE-CATALOG.jsonl", [deck])
    _jsonl(analysis / "STYLE-PAGES.jsonl", [page])
    _jsonl(library / creative_root / "分析索引/creativecup-curation.jsonl", [
        {"path": "模板库/" + source, "sha256": digest, "material_type": "模板/框架",
         "category": "实践", "confidence": "高"}])
    request = {"current_project": {"occasion": "大学生职业生涯规划大赛", "track": "成长赛道",
                                   "profession": "护理", "collection": "creative"},
               "current_template_source_sha256": current_sha,
               "target_page": {"message": "护理成长与实践结果", "role": "evidence_results",
                               "required_evidence": "病区沟通病例 照护记录"}}
    result = retrieve_page_layout_candidates(library, request)
    assert result["current_template_collection"] == "career"
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["reuse_mode"] == "layout_only_reference"
    assert result["candidates"][0]["source_path"] == source


def test_current_sha_overrides_wrong_requested_collection_and_blocks_generic_team_page(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    career = "BB0086 大学生职业生涯规划大赛 PPT"
    current_path, current_sha = _add_deck(library, f"{career}/当前稿/护理规划主模板.pptx", b"main-template")
    local_path, local_sha = _add_deck(library, f"{career}/成长模板/护理个人简介模板.pptx", b"local-personal")
    career_pages = library / career / "分析索引/STYLE-PAGES.jsonl"
    pages = [json.loads(line) for line in career_pages.read_text(encoding="utf-8").splitlines() if line.strip()]
    for page in pages:
        if page["source_sha256"] == local_sha:
            page.update(title="护理专业个人简介", text_excerpt="护理专业学习背景，临床实习经历与照护技能证据",
                        role_evidence=[{"role": "personal_intro"}])
    _jsonl(career_pages, pages)

    creative_root = "创赛"
    team_path = f"{creative_root}/科技项目/AI科技团队介绍模板.pptx"
    team_file = library / team_path
    team_file.parent.mkdir(parents=True, exist_ok=True)
    team_file.write_bytes(b"generic-team")
    team_sha = hashlib.sha256(b"generic-team").hexdigest()
    team_page = {"canonical_path": team_path, "source_sha256": team_sha, "slide": 8,
                 "title": "科技团队成员与信息", "text_excerpt": "个人信息 技术团队介绍 成员分工与项目合作",
                 "role_evidence": [{"role": "team_organization"}], "image_count": 1,
                 "composition": {"text_object_count": 7, "object_counts": {"text_shape": 7, "pic": 1}},
                 "layout_pattern": {"kind": "three_column_cards"}}
    team_deck = {"path": team_path, "source_sha256": team_sha, "source_format": "pptx", "status": "parsed",
                 "slide_count": 14, "detail_ref": "missing-team-detail.json",
                 "style_profile": {"page_aspect_ratio": 16 / 9, "theme_palette": {"accent1": "156082"}}}
    analysis = library / creative_root / "分析索引"
    _jsonl(analysis / "STYLE-CATALOG.jsonl", [team_deck])
    _jsonl(analysis / "STYLE-PAGES.jsonl", [team_page])
    _jsonl(library / creative_root / "分析索引/creativecup-curation.jsonl", [
        {"path": "模板库/" + team_path, "sha256": team_sha, "material_type": "模板/框架",
         "category": "科技项目", "confidence": "高"}])

    request = {"current_project": {"occasion": "大学生职业生涯规划大赛", "track": "成长赛道",
                                   "profession": "护理", "collection": "creative"},
               "current_template_source_sha256": current_sha,
               "target_page": {"message": "护理专业个人简介及临床实习经历", "role": "personal_intro",
                               "required_evidence": "护理专业学习背景 临床实习经历 照护技能证据"}}
    result = retrieve_page_layout_candidates(library, request)
    assert result["current_template_collection"] == "career"
    assert result["candidates"]
    assert result["candidates"][0]["source_path"] == local_path
    assert all(item["source_path"] != team_path for item in result["candidates"])
    assert all(item["reuse_mode"] == "same_collection_page_candidate" for item in result["candidates"])


def test_same_collection_explicit_profession_conflict_is_filtered_and_generic_is_uncertain(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    root = "BB0086 大学生职业生涯规划大赛 PPT"
    _, current_sha = _add_deck(library, f"{root}/当前稿/护理规划主模板.pptx", b"nursing-main")
    psychology_path, psychology_sha = _add_deck(library, f"{root}/成长赛道/心理咨询职业发展模板.pptx",
                                                b"psychology-source")
    generic_path, generic_sha = _add_deck(library, f"{root}/通用模板/通用个人信息空白模板.pptx",
                                           b"generic-source")
    pages_path = library / root / "分析索引/STYLE-PAGES.jsonl"
    pages = [json.loads(line) for line in pages_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for page in pages:
        if page["source_sha256"] == psychology_sha:
            page.update(title="心理咨询行业成果展示", text_excerpt="个人信息 团队成员 心理咨询服务成果",
                        role_evidence=[{"role": "team_organization"}])
        if page["source_sha256"] == generic_sha:
            page.update(title="个人信息", text_excerpt="个人信息 姓名 专业介绍",
                        role_evidence=[{"role": "team_organization"}])
    _jsonl(pages_path, pages)
    request = {"current_project": {"occasion": "大学生职业生涯规划大赛", "track": "成长赛道",
                                   "profession": "护理", "collection": "career"},
               "current_template_source_sha256": current_sha,
               "target_page": {"message": "个人信息与团队简介", "role": "team_organization",
                               "required_evidence": "个人信息 姓名 专业介绍"}}
    result = retrieve_page_layout_candidates(library, request)
    paths = [item["source_path"] for item in result["candidates"]]
    assert psychology_path not in paths
    assert generic_path in paths
    generic = next(item for item in result["candidates"] if item["source_path"] == generic_path)
    assert generic["score_evidence"]["profession_domain"]["status"] == "uncertain_no_explicit_profession_evidence"
    assert generic["score_evidence"]["profession_domain"]["uncertainty_score_adjustment"] == -1.5
    assert generic["score"] < 6


def test_same_collection_career_reference_is_layout_only_with_object_remap_inventory(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    career = "BB0086 大学生职业生涯规划大赛 PPT"
    _, current_sha = _add_deck(library, f"{career}/成长模板/当前护理稿.pptx", b"current")
    path, digest = _add_deck(library, f"{career}/案例/护理职业规划案例.pptx", b"reference")
    catalog = library / career / "分析索引/STYLE-PAGES.jsonl"
    pages = [json.loads(line) for line in catalog.read_text(encoding="utf-8").splitlines() if line.strip()]
    for page in pages:
        if page["source_sha256"] == digest:
            page.update(title="护理实践成果", text_excerpt="护理实践成果 病区沟通 老年照护", image_count=2,
                        role_evidence=[{"role": "evidence_results"}],
                        composition={"text_object_count": 4, "object_counts": {"text_shape": 4, "pic": 2},
                                     "hierarchy": [{"id": "1", "name": "标题", "role": "title", "text": "护理实践成果"},
                                                   {"id": "2", "name": "正文", "role": "text_or_visual", "text": "旧案例事实"}]})
    _jsonl(catalog, pages)
    taxonomy = library / career / "分析索引/SOURCE-TAXONOMY.jsonl"
    _append_jsonl(taxonomy, {"source_path": path.removeprefix(career + "/"), "sha256": digest,
                             "refined_taxonomy": {"asset_type": "career_reference_unclear_completion"}})
    _append_jsonl(library / "CONTENT-CURATION.jsonl", {"path": path, "sha256": digest,
                 "asset_type": "reference", "confidence": "high", "source_collection": "career"})
    request = {"current_project": {"occasion": "大学生职业生涯规划大赛", "track": "成长赛道",
                                   "profession": "护理", "collection": "career"},
               "current_template_source_sha256": current_sha,
               "target_page": {"message": "护理实践成果与病区沟通", "role": "evidence_results",
                               "required_evidence": "老年照护 病区沟通"}}
    result = retrieve_page_layout_candidates(library, request)
    candidate = next(item for item in result["candidates"] if item["source_path"] == path)
    assert candidate["reuse_mode"] == "layout_only_reference"
    assert candidate["copy_action"] == "review_only_no_automatic_copy"
    assert candidate["source_objects"]["remapping_estimate"] == {
        "text_objects_to_rewrite_or_rebuild": 4,
        "image_objects_to_replace_or_revalidate": 2,
        "total_objects_requiring_full_remap": 6}
    assert "不可迁用" in "".join(candidate["risks"])
