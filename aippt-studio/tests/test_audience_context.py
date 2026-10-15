"""Unknown audiences must not silently become leadership/business decks."""
import json

import pytest

from aippt_studio import participation
from aippt_studio.ingest import ingest_sources
from aippt_studio.models import InputRole, JobBrief
from aippt_studio.planner import create_brief, create_deck_plan
from aippt_studio.planning import planning_decision
from aippt_studio.storage import job_dir, load_job, save_job, write_json
from aippt_studio.workflow import create_job


@pytest.fixture(autouse=True)
def local_reference_stubs(monkeypatch):
    monkeypatch.setattr(participation, "read_resource", lambda provider, path: {
        "provider": provider, "path": path, "sha256": "fixture", "commit_sha": "fixture",
        "content": "A reference resource, not an approved template."})
    monkeypatch.setattr(participation, "tool_catalog", lambda: [])


def source_job(tmp_path, prefix="", name="材料整理"):
    job = create_job(name, job_id="audience-fixture")
    source = tmp_path / "current-material.md"
    source.write_text(prefix + "\n" + "\n".join(
        f"观察记录{i}：当天完成对应材料的核对，完整保留原始结果以及现场观察记录，用于解释该项事实。"
        for i in range(8)), encoding="utf-8")
    ingest_sources(job.id, [str(source)])
    return job


def test_new_unknown_brief_does_not_invent_decision_makers_or_leadership_strategy():
    brief = JobBrief(goal="整理现有材料")
    assert brief.audience == ""
    assert planning_decision(brief).strategy == "general"


def test_unknown_design_context_does_not_pick_consulting_or_executive_resources(tmp_path):
    result = participation.design_context(tmp_path, JobBrief(goal="整理现有材料"))
    assert result["style_candidate"] is None
    assert result["resources"] == []
    assert result["audience_status"] == "unconfirmed"


@pytest.mark.parametrize("brief", [
    JobBrief(goal="儿童科普：认识火箭技术"),
    JobBrief(goal="介绍火箭技术", audience="小学三年级学生"),
    JobBrief(goal="介绍火箭技术", audience="7—10岁儿童及家长"),
    JobBrief(goal="介绍火箭技术", customer_constraints=["给三年级上科普课，用课堂互动说明原理"]),
])
def test_explicit_teaching_occasion_precedes_generic_technical_topic(tmp_path, brief):
    result = participation.design_context(tmp_path, brief)
    assert result["style_candidate"] == "workshop-teaching"
    assert [row["provider"] for row in result["resources"]] == ["ppt-master"]
    assert result["reference_only"] is True


@pytest.mark.parametrize("style,expected", [
    ("academic-research", "academic-research"),
    ("学术研究风格", "academic-research"),
    ("咨询决策风格", "consulting-decision"),
    ("温暖的水彩手绘风", None),
    ("不要使用咨询商业模板", None),
])
def test_explicit_style_is_not_replaced_by_goal_keywords(tmp_path, style, expected):
    brief = JobBrief(goal="儿童科普火箭技术", style_direction=style)
    result = participation.design_context(tmp_path, brief)
    assert result["style_candidate"] == expected
    assert result["selection_basis"] == "style_direction"
    assert planning_decision(brief).visual_direction == style


def test_explicit_leadership_and_legacy_saved_audience_are_preserved(tmp_path):
    for kwargs in ({"audience_profile": "leadership"}, {"audience": "集团管理层"}, {"audience": "决策者与业务相关方"}):
        brief = JobBrief.model_validate({"goal": "经营复盘", **kwargs})
        assert planning_decision(brief).strategy == "leadership"
        result = participation.design_context(tmp_path, brief)
        assert any(row["provider"] == "editable-leadership-pptx" for row in result["resources"])


def test_current_audience_metadata_is_retained_as_source_candidate_not_confirmed(tmp_path):
    job = source_job(tmp_path, "听众：小学三年级学生")
    brief = create_brief(job.id)
    assert brief.audience == ""
    candidates = brief.model_dump().get("audience_candidates", [])
    assert len(candidates) == 1
    assert candidates[0]["text"] == "小学三年级学生"
    assert candidates[0]["source_text"] == "听众：小学三年级学生"
    assert candidates[0]["source_ref"].endswith("#line:1")
    assert candidates[0]["status"] == "source_candidate"
    result = participation.design_context(job_dir(job.id), brief)
    assert result["audience_status"] == "source_candidate"
    assert result["style_candidate"] == "workshop-teaching"
    assert result["selection_basis"] == "source_audience_candidate"


@pytest.mark.parametrize("text", [
    "研究对象：小学三年级学生", "人物：董事长张先生", "张老师面向儿童开展了科普调查。",
    "这家公司的受众主要是投资者。", "> 听众：集团管理层", "```text\n听众：集团管理层\n```",
    "## 调查正文\n受众：投资者", "## 案例\n听众：董事会\n## 当前说明\n保持完整资料。",
])
def test_people_research_subjects_quotes_and_case_copy_do_not_establish_current_audience(tmp_path, text):
    job = source_job(tmp_path, text)
    brief = create_brief(job.id)
    assert brief.audience == ""
    assert brief.model_dump().get("audience_candidates", []) == []


@pytest.mark.parametrize("role", ["template", "visual_reference", "asset"])
def test_reference_material_audience_does_not_leak_into_current_brief(tmp_path, role):
    job = source_job(tmp_path, "汇报对象：董事会")
    current = load_job(job.id)
    current.input_roles[current.source_ids[0]] = InputRole(role)
    save_job(current)
    brief = create_brief(job.id)
    assert brief.audience == ""
    assert brief.model_dump().get("audience_candidates", []) == []


def test_explicit_brief_fields_survive_reintake(tmp_path):
    job = source_job(tmp_path, "听众：小学三年级学生")
    create_brief(job.id)
    existing = JobBrief(goal="既有需求", audience="集团管理层", audience_profile="leadership",
                        style_direction="温暖的杂志编辑风", customer_constraints=["保留客户指定排版关系"])
    write_json(job_dir(job.id) / "planning/brief.json", existing)
    updated = create_brief(job.id)
    for field in ("audience", "audience_profile", "style_direction", "customer_constraints"):
        assert getattr(updated, field) == getattr(existing, field)


def test_actual_intake_and_planning_keep_unknown_scene_neutral(tmp_path):
    job = source_job(tmp_path)
    create_brief(job.id)
    plan = create_deck_plan(job.id)
    assert plan.planning_strategy["strategy"] == "general"
    context = plan.planning_strategy["upstream_context"]
    assert context["style_candidate"] is None
    assert context["resources"] == []
    brief = json.loads((job_dir(job.id) / "planning/brief.json").read_text())
    assert brief["audience"] == ""


def test_explicit_constraint_can_exclude_a_goal_matched_style(tmp_path):
    brief = JobBrief(goal="咨询方法介绍", customer_constraints=["不要使用咨询风格模板"])
    result = participation.design_context(tmp_path, brief)
    assert result["style_candidate"] is None
    assert result["selection_basis"] == "customer_constraints"


def test_metadata_section_retains_exact_audience_without_using_research_subject(tmp_path):
    job = source_job(tmp_path, "## 制作要求\n听众：小学三年级学生\n## 正文\n研究对象：城市成年居民")
    brief = create_brief(job.id)
    assert brief.audience == ""
    assert [row["text"] for row in brief.audience_candidates] == ["小学三年级学生"]
    assert brief.audience_candidates[0]["source_ref"].endswith("#line:2")


def test_conflicting_source_candidates_do_not_override_an_explicit_audience(tmp_path):
    job = source_job(tmp_path, "听众：小学三年级学生\n受众：集团管理层")
    brief = create_brief(job.id)
    assert len(brief.audience_candidates) == 2
    assert participation.design_context(job_dir(job.id), brief)["style_candidate"] is None
    brief.audience = "集团管理层"
    result = participation.design_context(job_dir(job.id), brief)
    assert result["style_candidate"] == "consulting-decision"
    assert result["selection_basis"] == "audience"
