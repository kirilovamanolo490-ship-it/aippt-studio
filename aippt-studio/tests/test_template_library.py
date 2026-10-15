from __future__ import annotations

import json
import hashlib
from pathlib import Path

from aippt_studio.template_library import cataloged_template_by_path, cataloged_template_by_sha256, main, search_catalog


def _catalog(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path


def test_search_ranks_tags_and_marks_completed_cases_separately(tmp_path: Path, capsys):
    catalog = _catalog(tmp_path / "CATALOG.jsonl", [
        {"path": "答辩模板.pptx", "asset_type": "template", "occasion_tags": ["竞赛答辩"],
         "style_tags": ["科技蓝"], "page_role_tags": ["封面", "创新点"], "review_status": "reviewed"},
        {"path": "案例.pptx", "asset_type": "portfolio", "occasion_tags": ["竞赛答辩"],
         "style_tags": ["科技蓝"], "page_role_tags": ["创新点"], "review_status": "reviewed"},
        {"path": "无关.pptx", "asset_type": "template", "occasion_tags": ["教学"],
         "style_tags": ["手绘"], "page_role_tags": ["课程封面"]},
    ])
    result = search_catalog(catalog, "竞赛答辩 创新点 科技蓝", limit=5)
    assert result["result_count"] == 2
    assert result["results"][0]["path"] == "答辩模板.pptx"
    case = next(row for row in result["results"] if row["is_completed_case"])
    assert case["asset_type"] == "portfolio"
    assert case["matched_fields"]["occasion_tags"] == ["竞赛答辩"]

    assert main(["search", "--catalog", str(catalog), "--query", "创新点", "--limit", "1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result_count"] == 1


def test_rejects_path_traversal_and_reports_bad_rows(tmp_path: Path):
    catalog = _catalog(tmp_path / "CATALOG.jsonl", [
        {"path": "../../secret.pptx", "asset_type": "template", "occasion_tags": ["答辩"]},
        {"path": "safe.pptx", "asset_type": "template", "occasion_tags": ["答辩"]},
    ])
    with catalog.open("a", encoding="utf-8") as stream:
        stream.write("not json\n")
    result = search_catalog(catalog, "答辩")
    assert [row["path"] for row in result["results"]] == ["safe.pptx"]
    assert result["catalog_errors"] == [
        {"line": 1, "error": "unsafe_or_missing_path"},
        {"line": 3, "error": "invalid_json: Expecting value"},
    ]


def test_empty_catalog_no_match_and_limits(tmp_path: Path):
    catalog = _catalog(tmp_path / "CATALOG.jsonl", [])
    assert search_catalog(catalog, "科技答辩")["status"] == "no_match"
    try:
        search_catalog(catalog, "答辩", limit=0)
    except ValueError as exc:
        assert "limit" in str(exc)
    else:
        raise AssertionError("invalid limit was accepted")


def test_reviewed_candidate_preferred_and_misnamed_source_not_false_match(tmp_path: Path):
    catalog = _catalog(tmp_path / "CATALOG.jsonl", [
        {"path": "商业案例模板15页.pptx", "title": "工作总结及明年思路",
         "name_mismatch": True, "content_note": "实际为党政工作总结",
         "occasion_tags": ["工作总结"], "review_status": "structure_sampled"},
        {"path": "其他工作总结.pptx", "occasion_tags": ["工作总结"],
         "review_status": "filename_only"},
        {"path": "已看过的工作总结.pptx", "occasion_tags": ["工作总结"],
         "review_status": "visual_sampled"},
    ])
    assert search_catalog(catalog, "商业案例")["status"] == "no_match"
    found = search_catalog(catalog, "工作总结")["results"]
    assert found[0]["path"] == "已看过的工作总结.pptx"
    assert found[1]["content_note"] == "实际为党政工作总结"


def test_nested_catalog_resolves_paths_relative_to_library_root(tmp_path: Path):
    library = tmp_path / "模板库"
    asset = library / "PPT模板/竞赛答辩/答辩封面.pptx"
    asset.parent.mkdir(parents=True)
    asset.touch()
    catalog = _catalog(library / "CATALOG.jsonl", [{
        "path": "PPT模板/竞赛答辩/答辩封面.pptx",
        "asset_type": "portfolio_example",
        "occasion_tags": ["竞赛答辩"], "style_tags": ["科技蓝"],
        "page_role_tags": ["封面"], "review_status": "visual_sampled",
    }])
    result = search_catalog(catalog, "竞赛答辩 封面 科技蓝")
    match = result["results"][0]
    assert match["path"] == "PPT模板/竞赛答辩/答辩封面.pptx"
    assert match["exists"] is True
    assert match["is_completed_case"] is True


def test_query_term_coverage_precedes_repeated_generic_tag_score(tmp_path: Path):
    catalog = _catalog(tmp_path / "CATALOG.jsonl", [
        {"path": "泛化科技.pptx", "asset_type": "template",
         "occasion_tags": ["科技"], "style_tags": ["科技"],
         "page_role_tags": ["科技"], "review_status": "visual_sampled"},
        {"path": "答辩科技.pptx", "asset_type": "template",
         "occasion_tags": ["答辩"], "style_tags": ["科技"],
         "page_role_tags": ["答辩"], "review_status": "filename_only"},
    ])
    result = search_catalog(catalog, "科技 答辩")
    assert result["results"][0]["path"] == "答辩科技.pptx"
    assert result["results"][0]["matched_term_count"] == 2
    assert result["results"][0]["total_query_terms"] == 2
    assert len(result["results"]) == 2
    assert result["required_form"] is None


def test_competition_query_excludes_service_proposal_despite_topic_matches(tmp_path: Path):
    catalog = _catalog(tmp_path / "CATALOG.jsonl", [
        {"path": "工作汇报/公共服务项目方案/4055.pptx", "asset_type": "template",
         "occasion_tags": ["工作汇报", "汇报", "公共服务项目方案"],
         "category": "工作汇报", "review_status": "structure_sampled"},
        {"path": "竞赛与答辩/非遗公益/4215.pptx", "asset_type": "template",
         "occasion_tags": ["竞赛与答辩", "竞赛", "比赛"],
         "category": "竞赛与答辩", "review_status": "structure_sampled"},
    ])
    result = search_catalog(catalog, "竞赛 公共服务 方案")
    assert [row["path"] for row in result["results"]] == ["竞赛与答辩/非遗公益/4215.pptx"]
    assert result["required_form"] == "competition_defense"


def test_generic_defense_label_is_not_enough_for_competition_template(tmp_path: Path):
    catalog = _catalog(tmp_path / "CATALOG.jsonl", [
        {"path": "答辩/通用答辩.pptx", "asset_type": "template", "occasion_tags": ["答辩"]},
        {"path": "竞赛与答辩/创新大赛.pptx", "asset_type": "template", "occasion_tags": ["大赛", "答辩"]},
    ])
    result = search_catalog(catalog, "大赛 答辩", form="competition_defense")
    assert [item["path"] for item in result["results"]] == ["竞赛与答辩/创新大赛.pptx"]
    assert "答辩/通用答辩.pptx" in result["occasion_mismatches"]


def test_search_exposes_full_deck_ooxml_metrics_without_claiming_visual_review(tmp_path: Path):
    library = tmp_path / "模板库"
    source = library / "竞赛与答辩/样本.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"x")
    catalog = _catalog(library / "CATALOG.jsonl", [
        {"path": "竞赛与答辩/样本.pptx", "occasion_tags": ["竞赛"], "review_status": "filename_only"},
    ])
    structure = library / "DESIGN-STRUCTURE.jsonl"
    structure.write_text(json.dumps({
        "schema": "aippt.template-design-index.v1", "source_path": "竞赛与答辩/样本.pptx",
        "source_sha256": "a" * 64, "source_bytes": 1, "status": "parsed",
        "coverage": {"slides_in_deck": 4, "slides_parsed": 4, "full_slide_coverage": True},
        "page": {"aspect_label": "wide_16_9"},
        "font_typefaces_top": [["字体A", 12]],
        "font_size_pt": {"all_runs_quantiles": {"p50": 26}},
        "text_box_width_ratio_quantiles": {"p50": .4},
        "heuristic_hierarchy": {"title_font_max_pt": {"p50": 40}},
        "copy_structure": {"heuristic_title_char_count": {"p50": 8}},
        "text_margins_ratio": {"left": {"p10": .08}},
        "picture_area_sum_per_slide": {"p50": .2},
        "theme1_palette": [{"slot": "accent1", "color": "123456"}],
        "language_signals": {"mean_chinese_ratio": .8},
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    result = search_catalog(catalog, "竞赛")
    item = result["results"][0]
    assert result["design_structure_records"] == 1
    assert item["design_structure"]["font_size_pt"]["all_runs_quantiles"]["p50"] == 26
    assert item["design_structure"]["heuristic_hierarchy"]["title_font_max_pt"]["p50"] == 40
    assert item["design_structure"]["copy_structure"]["heuristic_title_char_count"]["p50"] == 8
    assert item["design_structure"]["evidence_basis"].startswith("cached_full_deck_ooxml")
    assert item["design_structure"]["current_source_sha256_verified"] is False
    assert item["review_status"] == "filename_only"
    source.write_bytes(b"changed")
    stale = search_catalog(catalog, "竞赛")["results"][0]
    assert stale["design_structure"]["status"] == "stale_or_missing_source_size"


def test_hash_verified_template_selection_uses_full_deck_index_when_catalog_lacks_hash(tmp_path: Path):
    library = tmp_path / "模板库"
    source = library / "竞赛与答辩/纯数字编号.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fixture-template-bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    catalog = _catalog(library / "CATALOG.jsonl", [{
        "path": "竞赛与答辩/纯数字编号.pptx", "asset_type": "template",
        "occasion_tags": ["竞赛"], "review_status": "filename_only",
    }])
    (library / "DESIGN-STRUCTURE.jsonl").write_text(json.dumps({
        "schema": "aippt.template-design-index.v1", "source_path": "竞赛与答辩/纯数字编号.pptx",
        "source_sha256": digest, "source_bytes": source.stat().st_size, "status": "parsed",
        "coverage": {"slides_in_deck": 1, "slides_parsed": 1, "full_slide_coverage": True},
    }) + "\n", encoding="utf-8")
    selected = cataloged_template_by_path(catalog, "竞赛与答辩/纯数字编号.pptx")
    assert selected is not None and selected["source_sha256"] == digest
    assert selected["hash_basis"] == "full_deck_ooxml_index"
    assert cataloged_template_by_sha256(catalog, digest) == selected
    source.write_bytes(b"changed-template-bytes")
    assert cataloged_template_by_path(catalog, "竞赛与答辩/纯数字编号.pptx") is None
    assert cataloged_template_by_sha256(catalog, digest) is None


def test_renamed_template_old_path_resolves_only_with_exact_source_hash(tmp_path: Path):
    library = tmp_path / "模板库"
    source = library / "竞赛与答辩/社会治理-封面与正文.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"renamed-template")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    catalog = _catalog(library / "CATALOG.jsonl", [{
        "path": "竞赛与答辩/社会治理-封面与正文.pptx", "asset_type": "template",
        "source_sha256": digest,
    }])
    (library / "RENAME-ALIASES.jsonl").write_text(json.dumps({
        "old_path": "竞赛与答辩/7800-01.pptx",
        "current_path": "竞赛与答辩/社会治理-封面与正文.pptx",
        "source_sha256": digest,
    }) + "\n", encoding="utf-8")
    selected = cataloged_template_by_path(catalog, "竞赛与答辩/7800-01.pptx")
    assert selected is not None and selected["path"] == "竞赛与答辩/社会治理-封面与正文.pptx"
    source.write_bytes(b"changed")
    assert cataloged_template_by_path(catalog, "竞赛与答辩/7800-01.pptx") is None
