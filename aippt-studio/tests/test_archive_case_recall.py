"""Archive readback from natural requests, without real customer files or models."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aippt_studio import context_intelligence as ci
from aippt_studio.knowledge import context_concepts, match_context_text, matched_case_history, recall_task_knowledge


FLOOD_QUERY = "我要做一份防汛避险知识的图文讲解，给学生看，图片和文字要对应，不要重复照片，整页清楚。"
HOTEL_QUERY = "酒店吉祥物IP介绍，照片和角色图放圆角容器，图片实际边缘要与底形一致，别露直角或黑白边，避免裁掉角色。"
WORD_QUERY = "将读书分享整理成一页A4演讲稿，控制Word分页并核验导出的PDF。"


@pytest.mark.parametrize("query,title,body,concept", [
    (FLOOD_QUERY, "乡镇防汛工作交流", "积水点排查与应急避险。", "flood_response"),
    ("洪涝避险的图文讲解", "防汛工作交流", "积水点排查。", "flood_response"),
    (HOTEL_QUERY, "滨湖酒店月芽IP形象提案", "保持角色原貌。", "character_identity"),
    ("照片的直角边缘和圆角底板冲突，怎样统一裁切边界？", "故事页面修订", "直角照片后方不应露出尺寸相近的大圆角底板。", "image_frame_geometry"),
])
def test_short_domain_words_and_composed_image_geometry_are_meaningful(query, title, body, concept):
    result = match_context_text(query, title, body)
    assert result and concept in result["matched_concepts"]


@pytest.mark.parametrize("query,title,body", [
    ("安排酒店住宿和周末旅行，比较交通费用", "滨湖酒店月芽IP形象提案", "角色原貌与图片边缘"),
    ("酒店网络的IP地址分配和路由配置", "滨湖酒店月芽IP形象提案", "角色故事与图文排版"),
    ("科普洪水攻击和防火墙的网络安全技术", "乡镇防汛工作交流", "积水点排查与避险"),
    ("按圆角尺寸制作桌面家具，容器用直角底板", "故事页面修订", "直角照片后方不应露出大圆角底板"),
    (WORD_QUERY, "医学路演稿 原生PPTX与PDF导出", "保持证书完整"),
    (WORD_QUERY, "车间进度汇报", "恢复方法是重新读取 Word，建立六部分覆盖表。"),
    (WORD_QUERY, "学生科普演讲稿 8页原生可编辑PPT", "现场讲解"),
])
def test_unrelated_domains_and_incidental_file_formats_do_not_match(query, title, body):
    scope = title.split(" 原生")[0].split(" 8页")[0]
    assert match_context_text(query, title, body, scope_title=scope) is None


def test_actual_word_or_cross_format_task_can_still_match():
    assert match_context_text(WORD_QUERY, "Word演讲稿与PDF导出", "核对分页")
    assert match_context_text("把PPT整理成Word演讲稿并校对分页", "PPT转Word演讲稿", "原生转换")
    assert "character_identity" not in context_concepts("网络IP地址，路由与交换机配置")


def test_case_subject_outranks_generic_metadata_or_body_relations():
    direct = match_context_text(FLOOD_QUERY, "乡镇防汛工作交流", "积水点排查", scope_title="乡镇防汛工作交流")
    related = match_context_text(FLOOD_QUERY, "儿童科普讲座 图文关系", "图片和文字分区", scope_title="儿童科普讲座")
    assert direct and related and direct["score"] > related["score"]
    named = match_context_text("介绍清舒宁，保留工艺流程", "清舒宁项目", "原有工艺流程", scope_title="清舒宁项目")
    broad = match_context_text("介绍清舒宁，保留工艺流程", "原生流程图", "技术路线", scope_title="原生流程图")
    assert named and broad and named["score"] > broad["score"]
    factory_query = "为车间改善整理现场照片与整改过程"
    factory = match_context_text(factory_query, "化妆品车间进度汇报", "现场照片逐一对应")
    photo = match_context_text(factory_query, "个人成长与志愿活动", "现场照片")
    assert factory and photo and factory["score"] > photo["score"]


def test_teaching_deck_does_not_recall_word_only_task_via_shared_child_audience():
    assert match_context_text("给小学生做科普课件", "Word读书分享演讲稿 小学科普", "六年级学生", scope_title="Word读书分享演讲稿") is None


def _case(root: Path, number: int, title: str, sections: str) -> Path:
    entry = root / f"任务知识库/entries/2026/work-{number}.md"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(f"# {title}\n\n- 状态：completed\n\n{sections}", encoding="utf-8")
    work = root / f"work/{number}/work.json"
    work.parent.mkdir(parents=True)
    work.write_text(json.dumps({"work_id": str(number), "name": title, "status": "completed", "user_approved": True,
        "closure_schema_version": 2, "task_knowledge_entry": entry.relative_to(root).as_posix(),
        "task_knowledge_entry_sha256": hashlib.sha256(entry.read_bytes()).hexdigest()}), encoding="utf-8")
    with (root / "任务知识库/INDEX.md").open("a", encoding="utf-8") as stream:
        stream.write(f"| {number} | 2026-09-15 | {title} | completed | [复盘](entries/2026/work-{number}.md) |\n")
    return entry


def _examples(root: Path):
    _case(root, 712, "乡镇防汛工作交流", "## 任务目标与完成结果\n\n解释洪涝避险流程。\n\n## 可复用经验与知识评估\n\n现场照片需对应具体场所和事实。\n")
    _case(root, 823, "滨湖酒店月芽IP形象提案", "## 任务目标与输入\n\n忠于原始角色形象。\n\n## 失败与恢复\n\n透明角色实际带棋盘格，未采用。\n\n## 最终验证与交付\n\n### 排版修订\n\n直角照片后方不应露出尺寸相近的大圆角底板。\n\n## 可复用经验与边界\n\n先检查真实导出效果。角色故事不跨任务复用。\n\n## 证据位置\n\nDO_NOT_INDEX_PRIVATE_EVIDENCE_PATH\n")
    _case(root, 934, "Word读书分享演讲稿", "## 任务目标\n\n一页A4演讲稿。\n\n## 可复用经验\n\n检查Word分页。\n\n## 失败、诊断与恢复\n\n标题需原生居中。\n")


@pytest.fixture(autouse=True)
def no_external_catalog(monkeypatch):
    monkeypatch.setattr(ci, "recommend_upstream_tools", lambda query: [])


def test_recall_routes_keep_completed_history_as_navigation_until_original_acceptance(tmp_path):
    _examples(tmp_path)
    for query, expected in [(FLOOD_QUERY, "712"), (HOTEL_QUERY, "823"), (WORD_QUERY, "934")]:
        current = ci.recall_context(query, tmp_path)
        legacy = recall_task_knowledge(query, tmp_path / "任务知识库")
        assert current["completed_lessons"] == []
        current_case = next(row for row in current["unfinished_cases"] if row["work_id"] == expected)
        assert legacy["matches"][0]["work_id"] == expected
        assert current_case["trust"] == legacy["matches"][0]["trust"] == "confirmed_completed"
        assert current_case["reuse_status"] == legacy["matches"][0]["reuse_status"] == "completion_metadata_history"
        assert "reusable_experience" not in current_case and "failure_lessons" not in current_case
        assert "reusable_experience" not in legacy["matches"][0] and "failure_lessons" not in legacy["matches"][0]
    catalog = ci.build_case_index(tmp_path)
    hotel = next(row for row in catalog["documents"] if row["kind"] == "retrospective" and row["work_id"] == "823")
    assert "忠于原始角色" in hotel["summary"]
    assert "棋盘格，未采用" in hotel["failure_lessons"]
    assert "角色故事不跨任务复用" in hotel["reusable_experience"]
    assert "大圆角底板" in hotel["case_history"]
    assert "大圆角底板" not in hotel["reusable_experience"]
    assert "DO_NOT_INDEX_PRIVATE_EVIDENCE_PATH" not in json.dumps(hotel, ensure_ascii=False)
    geometry = "照片的直角边缘和圆角底板冲突，怎样统一裁切边界？"
    current_geometry = ci.recall_context(geometry, tmp_path)
    legacy_geometry = recall_task_knowledge(geometry, tmp_path / "任务知识库")
    assert next(row for row in current_geometry["unfinished_cases"] if row["work_id"] == "823")
    assert legacy_geometry["matches"][0]["work_id"] == "823"
    assert "case_history_context" not in next(row for row in current_geometry["unfinished_cases"] if row["work_id"] == "823")
    assert "case_history_context" not in legacy_geometry["matches"][0]


def test_history_excerpt_retains_rejection_and_reports_truncation_limits():
    short = "直角照片后方露出圆角底板，这一版被用户拒绝，尚未恢复。"
    long = "照片圆角边界需要核对。" * 180
    history = f"## 用户反馈与修改\n\n{short}\n\n### 后续提案\n\n{long}\n\n{long}\n\n{long}"
    result = matched_case_history("照片的圆角边界", history, source_path="entry.md", source_sha256="a" * 64)
    assert result and result["limits"]["max_total_chars"] == 2400
    assert len(result["excerpts"]) <= 3
    assert sum(len(row["text"]) for row in result["excerpts"]) <= 2400
    assert any(row["truncated"] for row in result["excerpts"])
    rejection = matched_case_history("直角照片圆角底板", f"## 用户反馈与修改\n\n{short}", source_path="entry.md", source_sha256="a" * 64)
    assert rejection["excerpts"][0]["text"] == short
    assert rejection["kind"] == "historical_case_narrative"


def test_parser_change_invalidates_only_cached_retrospectives(tmp_path):
    _examples(tmp_path)
    catalog = ci.build_case_index(tmp_path)
    index = tmp_path / "任务知识库/catalog/case-index.json"
    for row in catalog["documents"]:
        if row["kind"] == "retrospective":
            row["retrospective_reader_version"] = 0
            row["summary"] = row["reusable_experience"] = row["failure_lessons"] = ""
            row.pop("case_history", None)
    index.write_text(json.dumps(catalog), encoding="utf-8")
    refreshed = ci.build_case_index(tmp_path)
    assert refreshed["statistics"] == {"reused": 3, "reparsed": 3, "removed": 0}
    assert all(row["summary"] for row in refreshed["documents"] if row["kind"] == "retrospective")
    assert ci.build_case_index(tmp_path)["statistics"] == {"reused": 6, "reparsed": 0, "removed": 0}
