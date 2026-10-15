from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aippt_studio.style_search import search_style_library


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_curated_layout_evidence_returns_semantic_slots_and_actual_current_png(tmp_path: Path) -> None:
    from aippt_studio.style_search import _layout_examples
    png = tmp_path / "whole-page.png"
    png.write_bytes(b"test page")
    packet = {"schema": "aippt.reusable-layouts.v1", "entries": [{
        "source_sha256": "a" * 64, "source_slide": 5,
        "slot_map": [{"shape_id": "11", "role": "profile_body"}],
        "capacity_policy": {"limits": "tested copy only"},
        "validation": [{"png": str(png), "png_sha256": hashlib.sha256(png.read_bytes()).hexdigest()}]}]}
    (tmp_path / "REUSABLE-LAYOUTS.json").write_text(json.dumps(packet))
    sample = _layout_examples(tmp_path)["a" * 64][0]
    assert sample["validation"][0]["preview_current"]
    assert sample["slot_map"][0]["role"] == "profile_body"
    assert sample["replaceable_slots"] == sample["slot_map"]
    assert not sample["ready_for_automatic_application"]


def test_page_experiments_are_directly_retrievable_but_never_become_templates(tmp_path: Path) -> None:
    from aippt_studio.style_search import search_layout_examples
    relative, digest = _source(tmp_path, "BB0086 大学生职业生涯规划大赛 PPT/案例/环境.pptx", b"case")
    (tmp_path / "REUSABLE-LAYOUTS.json").write_text(json.dumps({
        "schema": "aippt.reusable-layouts.v1", "entries": [{
            "id": "LAY-25", "source_path": relative, "source_sha256": digest,
            "status": "curated_adapted_candidate", "source_slide": 15,
            "use_case": "校园环境与学习资源", "page_role": "background_problem"}]}))
    result = search_layout_examples(tmp_path, "职业规划 校园环境")
    assert result["examples"][0]["id"] == "LAY-25"
    assert result["examples"][0]["reuse_status"] == "reference_only"
    assert result["examples"][0]["case_content_replacement_required"]
    assert not search_layout_examples(tmp_path, "创赛 校园环境")["examples"]
    (tmp_path / relative).write_bytes(b"changed source")
    stale = search_layout_examples(tmp_path, "职业规划 校园环境")
    assert stale["status"] == "no_match" and stale["stale_examples"] == ["LAY-25"]


def test_example_relevance_precedes_unrelated_review_status(tmp_path: Path) -> None:
    from aippt_studio.style_search import search_layout_examples
    path, digest = _source(tmp_path, "创赛/样例.pptx", b"deck")
    entries = [{"id": "unrelated", "source_path": path, "source_sha256": digest,
                "status": "curated_static_examples_reviewed", "use_case": "团队介绍"},
               {"id": "relevant", "source_path": path, "source_sha256": digest,
                "status": "candidate", "use_case": "市场竞争分析"}]
    (tmp_path / "REUSABLE-LAYOUTS.json").write_text(json.dumps({
        "schema": "aippt.reusable-layouts.v1", "entries": entries}))
    result = search_layout_examples(tmp_path, "竞争分析", limit=1)
    assert result["examples"][0]["id"] == "relevant"
    assert not result["examples"][0]["ready_for_automatic_application"]


def test_user_rejected_design_example_is_not_recommended_again(tmp_path: Path) -> None:
    from aippt_studio.style_search import search_layout_examples, _layout_examples
    path, digest = _source(tmp_path, "创赛/校园环境.pptx", b"deck")
    entry = {"id": "rejected", "source_path": path, "source_sha256": digest,
             "status": "curated_static_examples_reviewed", "use_case": "校园环境",
             "design_review": {"decision": "reject_as_design_reference"}}
    (tmp_path / "REUSABLE-LAYOUTS.json").write_text(json.dumps({
        "schema": "aippt.reusable-layouts.v1", "entries": [entry]}))
    assert search_layout_examples(tmp_path, "校园环境")["examples"] == []
    assert _layout_examples(tmp_path)[digest][0]["design_review"]["decision"] == "reject_as_design_reference"


def test_page_search_does_not_fill_specific_request_with_collection_only_matches(tmp_path: Path) -> None:
    from aippt_studio.style_search import search_layout_examples
    path, digest = _source(tmp_path, "BB0086 大学生职业生涯规划大赛 PPT/获奖案例.pptx", b"deck")
    (tmp_path / "REUSABLE-LAYOUTS.json").write_text(json.dumps({
        "schema": "aippt.reusable-layouts.v1", "entries": [{
            "id": "practice", "source_path": path, "source_sha256": digest,
            "use_case": "职业规划 实践经历", "design_review": {"decision": "use_specific_structure"}}]}))
    assert search_layout_examples(tmp_path, "职业规划 校园环境")["examples"] == []
    assert search_layout_examples(tmp_path, "展品 动态 轮播")["examples"] == []
    assert search_layout_examples(tmp_path, "职业规划")["examples"][0]["id"] == "practice"


def test_reviewed_image_only_content_reference_is_discoverable_and_stale_preview_expires(tmp_path: Path) -> None:
    from aippt_studio.style_search import search_layout_examples
    path, digest = _source(tmp_path, "BB0086 大学生职业生涯规划大赛 PPT/获奖案例.pptx", b"image-only deck")
    preview = tmp_path / "source-page.png"
    preview.write_bytes(b"reviewed whole page")
    common = {"source_path": path, "source_sha256": digest, "page_role": "evidence_results"}
    entries = [{**common, "id": "LAY-01", "use_case": "三张图片", "status": "candidate"},
               {**common, "id": "REF-01", "record_kind": "design_reference",
                "design_review": {"decision": "use_specific_structure"},
                "design_preview": {"path": str(preview), "sha256": hashlib.sha256(preview.read_bytes()).hexdigest()},
                "adaptation_mode": "reconstruct_editable_objects",
                "image_roles": ["三张图片：主现场照和两张细节照片"],
                "layout_relationships": ["场景与任务说明相邻，操作照片支撑实践经历"]}]
    (tmp_path / "REUSABLE-LAYOUTS.json").write_text(json.dumps({
        "schema": "aippt.reusable-layouts.v1", "entries": entries}))
    result = search_layout_examples(tmp_path, "职业规划 三张图片", roles=["evidence_results"])
    reference = result["examples"][0]
    assert reference["id"] == "REF-01"
    assert reference["design_preview"]["current"]
    assert reference["reuse_status"] == "reference_only"
    assert reference["adaptation_mode"] == "reconstruct_editable_objects"
    assert reference["validation"] == [] and not reference["ready_for_automatic_application"]
    preview.write_bytes(b"different page")
    result = search_layout_examples(tmp_path, "职业规划 三张图片", roles=["evidence_results"])
    assert [item["id"] for item in result["examples"]] == ["LAY-01"]
    assert result["stale_examples"] == ["REF-01"]


def _source(library: Path, relative: str, payload: bytes) -> tuple[str, str]:
    path = library / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return relative, hashlib.sha256(payload).hexdigest()


def _deck(path: str, digest: str, color: str) -> dict:
    return {"path": path, "source_sha256": digest, "status": "parsed", "source_format": "pptx",
            "slide_count": 2, "detail_ref": "detail.json",
            "style_profile": {"theme_palette": {"accent1": color},
                              "explicit_fill_colors_top": [[color, 10]],
                              "pictures_per_slide": {"p50": 2},
                              "theme_consistency_ratio": 1,
                              "page_role_sequence": [{"slide": 1, "title": "竞赛封面"},
                                                     {"slide": 2, "title": "调查证据"}]}}


def _page(path: str, digest: str, slide: int, role: str, text: str) -> dict:
    return {"canonical_path": path, "source_sha256": digest, "slide": slide,
            "title": text, "text_excerpt": text, "role_evidence": [{"role": role}],
            "image_count": 2, "chart_count": 0, "table_count": 0,
            "layout_pattern": {"kind": "image_left_text_right"}}


def test_career_request_hard_routes_and_uses_measured_color(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    career = "BB0086 大学生职业生涯规划大赛 PPT"
    creative = "创赛"
    career_path, career_sha = _source(library, f"{career}/模板/蓝色就业赛道.pptx", b"career")
    creative_path, creative_sha = _source(library, f"{creative}/模板/蓝色创新.pptx", b"creative")
    _jsonl(library / career / "分析索引/STYLE-CATALOG.jsonl", [_deck(career_path, career_sha, "1355AA")])
    _jsonl(library / career / "分析索引/STYLE-PAGES.jsonl", [
        _page(career_path, career_sha, 1, "cover", "就业赛道职业规划封面"),
        _page(career_path, career_sha, 2, "self_assessment", "就业赛道自我认知"),
    ])
    _jsonl(library / creative / "分析索引/STYLE-CATALOG.jsonl", [_deck(creative_path, creative_sha, "1355AA")])
    _jsonl(library / creative / "分析索引/STYLE-PAGES.jsonl", [_page(creative_path, creative_sha, 1, "cover", "创新创业封面")])
    _jsonl(library / career / "分析索引/SOURCE-TAXONOMY.jsonl", [
        {"source_path": "模板/蓝色就业赛道.pptx", "sha256": career_sha, "refined_taxonomy": {
            "track": "employment", "asset_type": "blank_template", "asset_type_confidence": "high"}},
    ])
    preview = tmp_path / "observed-page.png"
    preview.write_bytes(b"test evidence")
    (library / "REUSABLE-LAYOUTS.json").write_text(json.dumps({
        "schema": "aippt.reusable-layouts.v1", "entries": [
            {"id": "sample-1", "source_sha256": career_sha, "source_slide": 2,
             "status": "verified_rendered_candidate", "validation": [{
                 "preview": str(preview), "preview_sha256": hashlib.sha256(preview.read_bytes()).hexdigest(),
                 "visual_status": "needs_human_whole_page_observation"}]},
            {"id": "stale-sample", "source_sha256": "0" * 64, "source_slide": 1}]}), encoding="utf-8")
    result = search_style_library(library, "大学生职业生涯规划 就业赛道 蓝色 封面 自我认知", limit=3)
    assert result["collection_intent"] == "career"
    assert result["status"] == "ready"
    assert [item["source_path"] for item in result["candidates"]] == [career_path]
    assert result["candidates"][0]["evidence"]["measured_color_match"] == ["blue"]
    assert result["candidates"][0]["reuse_status"] == "source_template_candidate"
    example = result["candidates"][0]["layout_examples"][0]
    assert example["id"] == "sample-1" and example["validation"][0]["preview_current"]
    assert not example["ready_for_automatic_application"]
    preview.write_bytes(b"changed")
    changed = search_style_library(library, "职业规划 就业赛道 蓝色", limit=3)
    assert changed["candidates"][0]["layout_examples"][0]["validation"][0]["visual_status"] == "evidence_missing_or_changed"
    (library / career_path).write_bytes(b"changed")
    assert search_style_library(library, "职业规划 就业赛道 蓝色", limit=3)["status"] == "no_match"


def test_challenge_cup_survey_does_not_select_unrelated_heritage_or_guide(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    root = "创赛"
    good, good_sha = _source(library, f"{root}/挑战杯/社会调查项目.pptx", b"survey")
    wrong, wrong_sha = _source(library, f"{root}/非遗/寻迹传遗.pptx", b"heritage")
    guide, guide_sha = _source(library, f"{root}/挑战杯/申报填写指南.pptx", b"guide")
    _jsonl(library / root / "分析索引/STYLE-CATALOG.jsonl", [
        _deck(good, good_sha, "AA1122"), _deck(wrong, wrong_sha, "AA1122"), _deck(guide, guide_sha, "AA1122")])
    _jsonl(library / root / "分析索引/STYLE-PAGES.jsonl", [
        _page(good, good_sha, 1, "cover", "挑战杯社会调查项目"),
        _page(good, good_sha, 2, "evidence_results", "社会调查结果"),
        _page(wrong, wrong_sha, 1, "cover", "非遗文创"),
        _page(guide, guide_sha, 1, "cover", "挑战杯社会调查申报填写指南"),
    ])
    _jsonl(library / root / "分析索引/creativecup-curation.jsonl", [
        {"path": "模板库/" + good, "sha256": good_sha, "material_type": "模板/框架（占位仍显著）",
         "category": "社会调查/社会治理", "confidence": "高"},
        {"path": "模板库/" + wrong, "sha256": wrong_sha, "material_type": "模板/框架（占位仍显著）",
         "category": "非遗/文化传承", "confidence": "高"},
        {"path": "模板库/" + guide, "sha256": guide_sha, "material_type": "指南/教程/备赛资料",
         "category": "社会调查/社会治理", "confidence": "高"},
    ])
    result = search_style_library(library, "挑战杯 社会调查 红色 封面 证据", limit=5)
    assert [item["source_path"] for item in result["candidates"]] == [good]
    assert result["candidates"][0]["evidence"]["occasion_guard"] == "source_context_evidenced"


def test_career_cover_does_not_hide_work_summary_agenda(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    career = "BB0086 大学生职业生涯规划大赛 PPT"
    path, digest = _source(library, f"{career}/模板/职业规划封面加年终总结.pptx", b"off-topic")
    _jsonl(library / career / "分析索引/STYLE-CATALOG.jsonl", [_deck(path, digest, "1355AA")])
    _jsonl(library / career / "分析索引/STYLE-PAGES.jsonl", [
        _page(path, digest, 1, "cover", "大学生职业生涯规划"),
        _page(path, digest, 2, "agenda", "年度工作概述 工作完成情况 成功项目展示 明年工作计划"),
    ])
    _jsonl(library / career / "分析索引/SOURCE-TAXONOMY.jsonl", [
        {"source_path": "模板/职业规划封面加年终总结.pptx", "sha256": digest, "refined_taxonomy": {
            "track": "unspecified", "asset_type": "blank_template", "asset_type_confidence": "high"}},
    ])
    result = search_style_library(library, "大学生职业生涯规划大赛 蓝色 封面", limit=3)
    assert result["status"] == "no_match"
    assert result["candidates"] == []


def test_full_deck_flattened_curation_overrides_raw_template_label(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    career = "BB0086 大学生职业生涯规划大赛 PPT"
    path, digest = _source(library, f"{career}/作品/整页图片作品.pptx", b"image-only")
    _jsonl(library / career / "分析索引/STYLE-CATALOG.jsonl", [_deck(path, digest, "1355AA")])
    _jsonl(library / career / "分析索引/STYLE-PAGES.jsonl", [
        _page(path, digest, 1, "cover", "职业生涯规划"),
        _page(path, digest, 2, "self_assessment", "能力展示"),
    ])
    _jsonl(library / career / "分析索引/SOURCE-TAXONOMY.jsonl", [
        {"source_path": "作品/整页图片作品.pptx", "sha256": digest, "refined_taxonomy": {
            "track": "unspecified", "asset_type": "general_presentation_template",
            "asset_type_confidence": "medium"}},
    ])
    _jsonl(library / "CONTENT-CURATION.jsonl", [
        {"path": path, "sha256": digest, "category": "人物与职业", "subcategory": "整页图片案例参考",
         "asset_type": "reference", "confidence": "high",
         "classification_observation": {"source_collection": "career", "flattened_page_structure": True}},
    ])
    result = search_style_library(library, "大学生职业生涯规划大赛", limit=3)
    assert result["status"] == "reference_only"
    assert result["candidates"][0]["asset_type"] == "reference"
    assert result["candidates"][0]["reuse_status"] == "reference_only"
    _jsonl(library / "CONTENT-CURATION.jsonl", [
        {"path": path, "sha256": "0" * 64, "category": "人物与职业",
         "asset_type": "template", "classification_observation": {"source_collection": "career"}},
    ])
    assert search_style_library(library, "大学生职业生涯规划大赛", limit=3)["status"] == "no_match"


def test_named_career_profession_conflict_filters_art_source_for_nursing_request(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    career = "BB0086 大学生职业生涯规划大赛 PPT"
    path, digest = _source(library, f"{career}/成长赛道/美育实践.pptx", b"art")
    _jsonl(library / career / "分析索引/STYLE-CATALOG.jsonl", [_deck(path, digest, "1355AA")])
    _jsonl(library / career / "分析索引/STYLE-PAGES.jsonl", [
        _page(path, digest, 1, "cover", "成长赛道美育规划"),
        _page(path, digest, 2, "evidence_results", "美术教育实践"),
    ])
    _jsonl(library / career / "分析索引/SOURCE-TAXONOMY.jsonl", [
        {"source_path": "成长赛道/美育实践.pptx", "sha256": digest, "refined_taxonomy": {
            "track": "growth", "asset_type": "blank_template", "asset_type_confidence": "high"}},
    ])
    result = search_style_library(library, "职业生涯规划大赛 成长赛道 护理实践", limit=3)
    assert result["status"] == "no_match"
