#!/usr/bin/env python3
"""Audit source preservation, 31 guides, actual observations and Studio entrypoints."""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote
import re

from typer.testing import CliRunner
from aippt_studio.cli import app
from aippt_studio.gaojie_layouts import CATEGORIES, analysis_root, _rows, _sha

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "模板库"
ARCHIVE = LIBRARY / "高阶文化"
ANALYSIS = analysis_root(LIBRARY)
STATE = ROOT / "aippt-studio/.maintenance/gaojie-learning"


def parse_cli(result):
    if result.exit_code:
        raise ValueError(f"Studio CLI failed: {result.output} {result.exception}")
    return json.loads(result.stdout)


def main():
    manifest = _rows(ANALYSIS / "SOURCE-MANIFEST.jsonl")
    receipts = {str(row["id"]): row for row in _rows(ARCHIVE / "归档回执.jsonl")}
    retained = json.loads((ROOT / "aippt-studio/.maintenance/gaojie-downloads/源文件损坏证据.json").read_text())
    assert len(manifest) == 5694 and len({row["path"] for row in manifest}) == 5694
    assert {path.name for path in ARCHIVE.iterdir() if path.is_dir()} == set(CATEGORIES)
    assert {path.relative_to(LIBRARY).as_posix() for path in ARCHIVE.rglob("*.pptx")} == {row["path"] for row in manifest}
    for i, row in enumerate(manifest, 1):
        source = LIBRARY / row["path"]
        assert source.parent.parent == ARCHIVE and source.stat().st_size == row["bytes"]
        expected = receipts[row["id"]].get("sha256") if row["id"] != "3890" else retained["retained_sha256"]
        assert _sha(source) == row["sha256"] == expected
        if i % 1000 == 0:
            print(json.dumps({"source_hashes_rechecked": i}, ensure_ascii=False), flush=True)
    catalog = _rows(ANALYSIS / "STYLE-CATALOG.jsonl")
    pages = _rows(ANALYSIS / "STYLE-PAGES.jsonl")
    assert len(catalog) == 5694 and sum(row["status"] == "parsed" for row in catalog) == 5693
    assert len(pages) == 7244 and len({(row["source_sha256"], row["slide"]) for row in pages}) == len(pages)
    details = {}
    for row in catalog:
        if row["status"] != "parsed":
            continue
        path = LIBRARY / row["detail_ref"]
        if row["source_sha256"] not in details:
            assert _sha(path) == row["detail_sha256"]
            packet = json.loads(path.read_text())
            assert packet["source_sha256"] == row["source_sha256"] and packet["status"] == "parsed"
            details[row["source_sha256"]] = len(packet["slides"])
        assert details[row["source_sha256"]] == row["slide_count"]
    counts = Counter(page["source_sha256"] for page in pages)
    assert counts == details
    guides = list((ANALYSIS / "分类学习").glob("*.md"))
    assert {path.stem for path in guides} == set(CATEGORIES)
    visual = _rows(ANALYSIS / "VISUAL-LEARNING.jsonl")
    assert len(visual) == 180 and {row["category"] for row in visual} == set(CATEGORIES) - {"素材图片", "发布会"}
    for row in visual:
        assert _sha(Path(row["preview"])) == row["preview_sha256"]
        assert _sha(Path(row["preview_receipt"])) == row["preview_receipt_sha256"]
    lessons = _rows(ANALYSIS / "APPLICATION-LEARNING.jsonl")
    assert len(lessons) == 11
    assert Counter(row["decision"] for row in lessons) == {"use_with_constraints": 7, "revise_before_reuse": 4}
    for row in lessons:
        assert _sha(Path(row["output_pptx"])) == row["output_sha256"]
        for evidence in row["preview_evidence"]:
            assert _sha(Path(evidence["receipt"])) == evidence["receipt_sha256"]
            assert _sha(Path(evidence["preview"])) == evidence["preview_sha256"]
    skill = ROOT / ".agents/skills/gaojie-layouts/SKILL.md"
    for markdown in [skill, skill.parent / "references/categories.md"]:
        for target in re.findall(r"\]\(([^)]+)\)", markdown.read_text()):
            if not target.startswith(("http:", "https:")):
                assert (markdown.parent / unquote(target)).resolve().is_file(), target
    validator = Path.home() / ".codex/skills/.system/skill-creator/scripts/quick_validate.py"
    validated = subprocess.run([sys.executable, str(validator), str(skill.parent)], capture_output=True, text=True)
    assert validated.returncode == 0, validated.stdout + validated.stderr
    runner = CliRunner()
    result = parse_cli(runner.invoke(app, ["library-layout-search", "--category", "三段内容", "--query", "社区共创 三项行动", "--limit", "5", "--json"]))
    assert result["candidate_count"] > 0 and any(row.get("design_observation") for row in result["candidates"])
    routed = parse_cli(runner.invoke(app, ["library-style-search", "--collection", "gaojie", "--query", "标题模板", "--limit", "3", "--json"]))
    assert routed["collection_intent"] == "gaojie" and routed["candidates"]
    for category in CATEGORIES:
        packet = parse_cli(runner.invoke(app, ["library-layout-search", "--category", category, "--query", CATEGORIES[category][1], "--limit", "1", "--json"]))
        assert bool(packet["candidates"]) == (category not in {"素材图片", "发布会"}), category
    selected = result["candidates"][0]
    inspect_output = STATE / "cli-page-inspection.json"
    inspected = parse_cli(runner.invoke(app, ["library-style-inspect", "--path", selected["source_path"], "--slide", str(selected["source_slide"]), "--output", str(inspect_output), "--json"]))
    assert inspected["status"] == "ready"
    request_file = STATE / "cli-page-match-request.json"
    request_file.write_text(json.dumps({"current_project": {"occasion": "内部版式验证"},
                                       "current_template_source_sha256": "0" * 64,
                                       "target_page": {"layout_collection": "gaojie", "category": "三段内容", "role": "three_part_content", "message": "三项社区行动"}}, ensure_ascii=False))
    matched = parse_cli(runner.invoke(app, ["library-style-page-search", "--request", str(request_file), "--json"]))
    assert matched["candidates"] and matched["layout_collection"] == "gaojie"
    cli_build = STATE / "cli-candidate.pptx"
    mapping = STATE / "usage/title-component/short/mapping.json"
    built = parse_cli(runner.invoke(app, ["library-layout-build", "--request", str(mapping), "--output", str(cli_build), "--json"]))
    assert built["mapped_text_objects"] == 2 and cli_build.is_file()
    learned = parse_cli(runner.invoke(app, ["library-layout-learn", "--request", str(STATE / "usage/title-component/short/learning-request.json"), "--json"]))
    assert learned["events"] == 11
    summary = {"status": "verified", "source_pptx_preserved": 5694, "parsed_pptx": 5693, "unique_pages": 7244,
               "unique_parsed_sources": len(details), "category_guides": len(guides), "actual_observed_source_pages": len(visual),
               "application_events": 11, "usable_with_constraints": 7, "needs_revision": 4,
               "skill_validated": validated.stdout.strip(), "all_31_category_cli_queries_checked": True,
               "cli_search_inspect_page_match_build_and_learning_verified": True,
               "native_powerpoint_qa": False, "whole_library_visual_qa": False,
               "evidence": {str(path.relative_to(ROOT)): _sha(path) for path in
                            (ANALYSIS / "SOURCE-MANIFEST.jsonl", ANALYSIS / "STYLE-CATALOG.jsonl", ANALYSIS / "STYLE-PAGES.jsonl",
                             ANALYSIS / "VISUAL-LEARNING.jsonl", ANALYSIS / "APPLICATION-LEARNING.jsonl", skill)}}
    (STATE / "FINAL-LEARNING-VERIFICATION.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
