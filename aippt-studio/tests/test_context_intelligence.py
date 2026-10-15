from __future__ import annotations

import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from aippt_studio import context_intelligence as ci
from aippt_studio.knowledge import recall_task_knowledge


def _json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _case(root: Path, work: int, title: str, *, status: str = "review", approved: bool = False) -> Path:
    _json(root / f"work/{work}/work.json", {"work_id": str(work), "name": title,
                                          "status": status, "user_approved": approved})
    entry = root / f"任务知识库/entries/2026/work-{work}.md"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(f"# {title}\n\n- 状态：{status}\n\n## 任务目标\n\n{title}\n\n"
                     "## 可复用经验\n\n保留母版、版式及主题。先做样稿。\n\n"
                     "## 失败、诊断与恢复\n\nStudio脚本修复、完整保留、建立工具验证。\n", encoding="utf-8")
    index = root / "任务知识库/INDEX.md"
    with index.open("a", encoding="utf-8") as stream:
        stream.write(f"| {work} | 2026-09-15 | {title} | {status} | [复盘](entries/2026/work-{work}.md) |\n")
    return entry


def _pptx(path: Path, *, chart: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    p = ci._NS["p"]
    a = ci._NS["a"]
    r = ci._NS["r"]
    with ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", f'<p:presentation xmlns:p="{p}" xmlns:r="{r}"><p:sldIdLst><p:sldId id="1" r:id="r1"/><p:sldId id="2" r:id="r2"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')
        archive.writestr("ppt/_rels/presentation.xml.rels", f'<Relationships xmlns="{ci._REL}"><Relationship Id="r1" Target="slides/slide2.xml"/><Relationship Id="r2" Target="slides/slide1.xml"/></Relationships>')
        for number, title in [(2, "经营汇报与收入折线图"), (1, "未来发展")]:
            graphic = f'<p:graphicFrame><a:graphic><a:graphicData uri="{a}/chart"/></a:graphic></p:graphicFrame>' if chart else ""
            archive.writestr(f"ppt/slides/slide{number}.xml", f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x="12" y="20"/><a:ext cx="300" cy="400"/></a:xfrm></p:spPr><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p></p:txBody></p:sp><p:pic/>{graphic}</p:spTree></p:cSld></p:sld>')


@pytest.fixture(autouse=True)
def no_global_catalog(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ci, "recommend_upstream_tools", lambda query: [])


def test_operational_request_ranks_pending_mac_and_github_without_promoting_them(tmp_path: Path):
    unrelated = _case(tmp_path, 8, "园区维修工作汇报", status="completed", approved=True)
    with unrelated.open("a", encoding="utf-8") as stream:
        stream.write("\n- 检索关键词：Studio macOS PowerPoint 原生文件修复\n")
    _case(tmp_path, 16, "Windows迁移Mac核心兼容性修复")
    _case(tmp_path, 17, "GitHub上游无侵入接入")
    result = ci.recall_context("整改Studio的macOS跨平台兼容性，GitHub工具接入，完整保留问题修复", tmp_path)
    assert result["completed_lessons"] == []
    assert {item["work_id"] for item in result["unfinished_cases"]} == {"16", "17"}
    assert all(item["trust"] == "pending_or_unverified" for item in result["unfinished_cases"])
    assert all("reusable_experience" not in item for item in result["unfinished_cases"])
    legacy = recall_task_knowledge("整改Studio的macOS跨平台兼容性，GitHub工具接入，完整保留问题修复", tmp_path / "任务知识库")
    assert legacy["matches"] == []


def test_completed_requires_consistent_work_index_entry_and_user_approval(tmp_path: Path):
    _case(tmp_path, 1, "企业模板经营汇报", status="completed", approved=True)
    _case(tmp_path, 2, "企业模板工作汇报", status="completed", approved=False)
    _case(tmp_path, 3, "企业模板成果汇报", status="completed", approved=True)
    _json(tmp_path / "work/3/work.json", {"work_id": "3", "name": "企业模板", "status": "review", "user_approved": True})
    result = ci.recall_context("保留母版的企业模板样张", tmp_path)
    assert result["completed_lessons"] == []
    assert {item["work_id"] for item in result["unfinished_cases"]} == {"1", "2", "3"}
    case = next(item for item in result["unfinished_cases"] if item["work_id"] == "1")
    assert case["source_sha256"]
    assert case["work_metadata_sha256"]
    assert case["trust"] == "legacy_status_only"
    assert case["reuse_status"] == "completion_metadata_history"
    assert case["original_user_acceptance"]["state"] == "project_review_not_started"
    assert "summary" not in case and "reusable_experience" not in case and "failure_lessons" not in case


def test_generic_query_does_not_force_a_case(tmp_path: Path):
    _case(tmp_path, 1, "园区维修汇报", status="completed", approved=True)
    result = ci.recall_context("Studio修复完整保留自动验证，建立工具知识库让项目更智能", tmp_path)
    assert result["status"] == "no_match"


def test_work_code_reuse_guide_is_a_discovery_locator_not_a_lesson(tmp_path: Path):
    guide = tmp_path / "aippt-studio/docs/WORK_CODE_REUSE.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("# Work code reuse\nInspect evidence before reuse.\n", encoding="utf-8")
    result = ci.recall_context("制作新的课堂汇报", tmp_path)
    locator = result["work_code_reuse"]
    assert locator["status"] == "discovery_only"
    assert locator["guide_path"] == str(guide)
    assert locator["guide_sha256"] == hashlib.sha256(guide.read_bytes()).hexdigest()
    assert result["validated_lessons"] == []
    assert result["tool_recommendations"] == []
    guide.unlink()
    missing = ci.recall_context("制作新的课堂汇报", tmp_path)
    assert missing["work_code_reuse"]["status"] == "unavailable"
    assert missing["work_code_reuse"]["guide_path"] is None


def test_portfolio_reads_presentation_order_geometry_and_object_types_not_visual_quality(tmp_path: Path):
    _pptx(tmp_path / "作品集/数字名.pptx")
    catalog = ci.build_case_index(tmp_path)
    document = catalog["documents"][0]
    assert document["title"] == "经营汇报与收入折线图"
    assert document["page_count"] == 2
    assert document["slides"][0]["part"] == "ppt/slides/slide2.xml"
    assert document["slides"][0]["object_counts"] == {"text_shape": 1, "picture": 1, "chart": 1}
    assert document["slides"][0]["objects"][0]["bounds_emu"]["width"] == 300
    result = ci.recall_context("柱状图和折线图的经营汇报", tmp_path)
    reference = result["portfolio_references"][0]
    assert reference["matching_slides"][0]["slide"] == 1
    assert reference["inspection"] == "ooxml_structure_only"
    assert reference["visually_reviewed"] is False


def test_unified_library_keeps_finished_examples_and_templates_distinct(tmp_path: Path):
    _pptx(tmp_path / "模板库/作品集/历史样稿/数字名.pptx")
    _pptx(tmp_path / "模板库/课程模板/商业案例模板.pptx")
    catalog = ci.build_case_index(tmp_path)
    assert {item["kind"] for item in catalog["documents"]} == {"portfolio", "template"}
    result = ci.recall_context("经营汇报收入折线图", tmp_path)
    assert result["portfolio_references"][0]["source_path"].startswith("模板库/作品集/")
    assert result["template_references"][0]["source_path"].startswith("模板库/课程模板/")
    assert result["portfolio_references"][0]["visually_reviewed"] is False
    assert result["template_references"][0]["visually_reviewed"] is False


def test_root_catalog_skips_only_listed_templates_and_keeps_portfolio_indexed(tmp_path: Path):
    template_tree = tmp_path / "模板库"
    _pptx(template_tree / "竞赛/学术答辩/答辩.pptx")
    _pptx(template_tree / "党建/年度总结/报告.pptx")
    _pptx(tmp_path / "模板库/作品集/历史样稿/成品.pptx")
    initial = ci.build_case_index(tmp_path)
    assert {item["kind"] for item in initial["documents"]} == {"template", "portfolio"}
    (tmp_path / "模板库/CATALOG.jsonl").write_text(
        json.dumps({"path": "竞赛/学术答辩/答辩.pptx"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    indexed = ci.build_case_index(tmp_path)
    assert indexed["document_count"] == 2
    assert indexed["statistics"]["removed"] == 1
    assert indexed["template_catalog"] == "模板库/CATALOG.jsonl"
    assert {item["kind"] for item in indexed["documents"]} == {"portfolio", "template"}
    assert {item["source_name"] for item in indexed["documents"]} == {"报告", "成品"}


def test_portfolio_filename_remains_searchable_when_cover_is_ambiguous(tmp_path: Path):
    _pptx(tmp_path / "模板库/作品集/精简版/乡村振兴数字赋能农业.pptx")
    result = ci.recall_context("乡村振兴数字赋能农业", tmp_path)
    assert result["portfolio_references"][0]["source_name"] == "乡村振兴数字赋能农业"


def test_incremental_reuses_unchanged_updates_changed_and_removes_deleted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _case(tmp_path, 1, "模板经营汇报", status="completed", approved=True)
    pptx = tmp_path / "作品集/1.pptx"
    _pptx(pptx)
    first = ci.build_case_index(tmp_path)
    assert first["statistics"]["reparsed"] == 3
    original = ci.extract_pptx_structure
    monkeypatch.setattr(ci, "extract_pptx_structure", lambda path: pytest.fail("unchanged PPTX reparsed"))
    second = ci.build_case_index(tmp_path)
    assert second["statistics"] == {"reused": 3, "reparsed": 0, "removed": 0}
    monkeypatch.setattr(ci, "extract_pptx_structure", original)
    _pptx(pptx, chart=False)
    third = ci.build_case_index(tmp_path)
    assert third["statistics"]["reparsed"] == 1
    pptx.unlink()
    fourth = ci.build_case_index(tmp_path)
    assert fourth["statistics"]["removed"] == 1


def test_status_index_change_applies_even_when_retrospective_bytes_unchanged(tmp_path: Path):
    _case(tmp_path, 1, "模板经营汇报", status="completed", approved=True)
    ci.build_case_index(tmp_path)
    index = tmp_path / "任务知识库/INDEX.md"
    index.write_text(index.read_text().replace("completed", "review"), encoding="utf-8")
    result = ci.recall_context("企业模板样稿", tmp_path)
    assert result["completed_lessons"] == []
    assert result["unfinished_cases"][0]["work_id"] == "1"


def test_corrupt_portfolio_does_not_block_catalog_and_symlink_escape_is_excluded(tmp_path: Path):
    portfolio = tmp_path / "作品集"
    portfolio.mkdir()
    (portfolio / "bad.pptx").write_bytes(b"not a zip")
    _case(tmp_path, 1, "Mac兼容性")
    result = ci.build_case_index(tmp_path)
    assert result["status"] == "partial"
    assert result["document_count"] == 2
    assert len(result["errors"]) == 1


def test_duplicate_retrospective_never_yields_confirmed_lesson(tmp_path: Path):
    _case(tmp_path, 1, "模板经营汇报", status="completed", approved=True)
    index = tmp_path / "任务知识库/INDEX.md"
    index.write_text(index.read_text() * 2, encoding="utf-8")
    result = ci.recall_context("模板经营汇报", tmp_path)
    assert result["completed_lessons"] == []


def test_verified_lesson_requires_valid_source_hash_and_excludes_current_work(tmp_path: Path):
    source = tmp_path / "work/1/workspace/learning-events.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text('{"event":"example"}\n', encoding="utf-8")
    lesson = {"status": "verified", "work_id": "1", "trigger": "Mac跨平台兼容性",
              "lesson": "验证当前平台的实际可执行文件", "source": "work/1/workspace/learning-events.jsonl",
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    result = ci.recall_context("macOS兼容性", tmp_path, validated_lessons=[lesson])
    assert len(result["validated_lessons"]) == 1
    result = ci.recall_context("macOS兼容性", tmp_path, validated_lessons=[lesson], current_work_id="1")
    assert result["validated_lessons"] == []
    source.write_text("changed", encoding="utf-8")
    result = ci.recall_context("macOS兼容性", tmp_path, validated_lessons=[lesson])
    assert result["validated_lessons"] == []


def test_upstream_recommendations_distinguish_resources_runtime_and_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.undo()
    catalog = [{"provider": "echarts", "source_present": True, "operations": {"read": "read resource", "chart": "render chart"}},
               {"provider": "PPTAgent", "source_present": True, "operations": {"read": "read resource"}, "runtime_requirement": "model needed"},
               {"provider": "missing", "source_present": False, "operations": {"author": "author editable shapes"}}]
    result = ci.recommend_upstream_tools("GitHub接入柱状图，原生可编辑图表", catalog=catalog)
    chart = next(item for item in result if item["operation"] == "chart")
    resource = next(item for item in result if item["provider"] == "PPTAgent")
    missing = next(item for item in result if item["provider"] == "missing")
    assert chart["availability"] == "contract_available_runtime_unchecked"
    assert resource["role"] == "resource_read"
    assert resource["availability"] == "source_available"
    assert missing["availability"] == "source_missing"
    assert all(item["executed"] is False for item in result)


@pytest.mark.parametrize("schema", [1, 2])
def test_retrospective_approval_hash_verifies_new_and_legacy_work_then_withdraws_edits(tmp_path: Path, schema: int):
    entry = _case(tmp_path, 1, "企业模板经营汇报", status="completed", approved=True)
    record_path = tmp_path / "work/1/work.json"
    record = json.loads(record_path.read_text())
    record.update({"closure_schema_version": schema,
                   "task_knowledge_entry": entry.relative_to(tmp_path).as_posix(),
                   "task_knowledge_entry_sha256": hashlib.sha256(entry.read_bytes()).hexdigest()})
    _json(record_path, record)
    result = ci.recall_context("企业模板经营汇报", tmp_path)
    assert result["completed_lessons"] == []
    case = result["unfinished_cases"][0]
    assert case["trust"] == "confirmed_completed"
    assert case["retrospective_content_approval"] == "approved_content_hash_matches"
    assert case["reuse_status"] == "completion_metadata_history"
    assert case["original_user_acceptance"]["state"] == "project_review_not_started"
    assert "summary" not in case and "reusable_experience" not in case and "failure_lessons" not in case
    with entry.open("a", encoding="utf-8") as stream:
        stream.write("\n未经再次审阅的新经验：改变原模板全部母版。\n")
    for refresh in (False, True):
        result = ci.recall_context("企业模板经营汇报", tmp_path, refresh=refresh)
        assert result["completed_lessons"] == []
        case = result["unfinished_cases"][0]
        assert case["trust"] == "pending_or_unverified"
        assert case["retrospective_content_approval"] == "approval_hash_mismatch"
        assert "reusable_experience" not in case and "failure_lessons" not in case


def test_hash_bound_completed_retrospective_requires_current_original_user_acceptance_before_reuse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A legacy completion/hash receipt is navigation only until the final proof exists."""

    from aippt_studio import project_learning

    entry = _case(tmp_path, 1, "企业模板经营汇报", status="completed", approved=True)
    record_path = tmp_path / "work/1/work.json"
    record = json.loads(record_path.read_text())
    record.update({"closure_schema_version": 2,
                   "task_knowledge_entry": entry.relative_to(tmp_path).as_posix(),
                   "task_knowledge_entry_sha256": hashlib.sha256(entry.read_bytes()).hexdigest()})
    _json(record_path, record)

    bypass = ci.recall_context("企业模板经营汇报", tmp_path)
    assert bypass["completed_lessons"] == []
    navigation = bypass["unfinished_cases"][0]
    assert navigation["reuse_status"] == "completion_metadata_history"
    assert navigation["original_user_acceptance"]["state"] == "project_review_not_started"
    assert "reusable_experience" not in navigation and "failure_lessons" not in navigation

    calls = []

    def verified_final_review(repo: Path, work: Path) -> dict:
        calls.append((repo, work))
        return {"state": "explicit_original_user_acceptance", "accepted_episode_count": 1,
                "review_phase": "synthesis", "review_receipt_sha256": "a" * 64,
                "untrusted_source_text": "must not be projected into context"}

    monkeypatch.setattr(project_learning, "current_explicit_user_acceptance", verified_final_review)
    accepted = ci.recall_context("企业模板经营汇报", tmp_path)
    assert calls and all(call == (tmp_path.resolve(), (tmp_path / "work/1").resolve()) for call in calls)
    lesson = accepted["completed_lessons"][0]
    assert lesson["reuse_status"] == "accepted_lesson"
    assert lesson["original_user_acceptance"] == {
        "state": "explicit_original_user_acceptance", "accepted_episode_count": 1,
        "review_phase": "synthesis", "review_receipt_sha256": "a" * 64,
    }
    assert "保留母版" in lesson["reusable_experience"]
    assert "Studio脚本修复" in lesson["failure_lessons"]
    assert "untrusted_source_text" not in json.dumps(lesson, ensure_ascii=False)


def test_new_closure_schema_cannot_recall_completed_text_without_approval_hash(tmp_path: Path):
    _case(tmp_path, 1, "企业模板经营汇报", status="completed", approved=True)
    record_path = tmp_path / "work/1/work.json"
    record = json.loads(record_path.read_text())
    record["closure_schema_version"] = 2
    _json(record_path, record)
    result = ci.recall_context("企业模板经营汇报", tmp_path)
    assert result["completed_lessons"] == []
    assert result["unfinished_cases"][0]["retrospective_content_approval"] == "missing_approval_hash"


def test_approval_hash_cannot_be_rebound_to_another_entry_with_identical_bytes(tmp_path: Path):
    entry = _case(tmp_path, 1, "企业模板经营汇报", status="completed", approved=True)
    different = entry.with_name("unindexed-copy.md")
    different.write_bytes(entry.read_bytes())
    record_path = tmp_path / "work/1/work.json"
    record = json.loads(record_path.read_text())
    record.update({"closure_schema_version": 2,
                   "task_knowledge_entry": different.relative_to(tmp_path).as_posix(),
                   "task_knowledge_entry_sha256": hashlib.sha256(entry.read_bytes()).hexdigest()})
    _json(record_path, record)
    result = ci.recall_context("企业模板经营汇报", tmp_path)
    assert result["completed_lessons"] == []
    assert result["unfinished_cases"][0]["retrospective_content_approval"] == "approval_entry_path_mismatch"
