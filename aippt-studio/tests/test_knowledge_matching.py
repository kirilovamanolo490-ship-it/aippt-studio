"""Natural-language recall regressions, with copied lesson prose and local fixtures.

No test reads the real Work18 archive or depends on a live model.
"""
import copy
from pathlib import Path

import pytest

from aippt_studio import context_intelligence as ci
from aippt_studio import project_learning as pl
from aippt_studio.knowledge import context_concepts, match_context_text
from test_project_learning import archive, configure, message, review_for


NATURAL_QUERY = "三年级儿童科普讲解，暗沉图片和叠字被否定，微调图文分区，检查前后页关联与未改区域。"
LESSONS = [
    {
        "title": "儿童化表达要同时校准受众和事实限定",
        "applies_when": "用户要求把已有专业材料美化，并让儿童易懂。",
        "exceptions": "故事化只是可选表达；不能为了短句省略关键限定，也不能把配图选择写成一般事实。本项目内容准确性仍未闭合。",
        "action": "先明确每页希望听众理解的一件事，用熟悉词语解释术语；将正文、讲解提示和来源分开，检查自动简报是否承接受众，修改限定时同步核对正文和备注。",
        "verify_on_page": "连读标题、正文和图注，检查它们是否表达同一意思；对照来源检查数字、绝对化表述和备注中的残留旧说法。",
        "outcome_level": "unresolved",
    },
    {
        "title": "暗沉压图反馈应落实到图文关系",
        "applies_when": "用户明确认为页面暗沉，文字直接压在图片上不合适。",
        "exceptions": "这是本项目偏好，不能推成所有页面禁止叠字或必须分栏；本次分区修正没有获得明确接受。",
        "action": "把主题、问题或行动放在清晰文字区域，让相关插画承载情境；根据内容选择分区、留白或其他关系，避免仅靠整页压暗获得文字对比。",
        "verify_on_page": "查看实际导出页的整体明亮感、标题与人物的主次、文字背景复杂度和插画主体完整性，并审查其他采用同类构图的页面。",
        "outcome_level": "unresolved",
    },
    {
        "title": "修改后重新核对版本和反馈范围",
        "applies_when": "技术检查通过后用户仍否定视觉效果，且只重做部分页面或重新应用动态。",
        "exceptions": "用户指出问题不代表整份否定，助手保留的页面也不代表已获接受；结构检查、视觉效果和播放节奏各有不同范围。",
        "action": "保存用户原话、实际修改页和对应版本的检查结论；新版逐项核对新增警告、同类遗留设计及动态状态，明确记录哪些修改仅由助手报告完成。",
        "verify_on_page": "对照修改前后导出页检查文字层次、边界和裁切，再按讲解顺序播放新版，检查出现顺序及单击推进；旧版零警告不能覆盖新版警告。",
        "outcome_level": "unresolved",
    },
]


def matches(query):
    return [match_context_text(query, lesson["title"], lesson["applies_when"] + "\n" + lesson["action"]) for lesson in LESSONS]


def test_original_natural_query_recalls_all_three_conditional_lessons_without_title_copy():
    result = matches(NATURAL_QUERY)
    assert all(result)
    assert [item["matched_concepts"] for item in result] == [["child_friendly_teaching"], ["image_text_relationship"], ["revision_scope"]]
    # These used to be no_match because only two-character lexical fragments
    # overlapped; do not disguise the regression with an exact title query.
    assert all(not item["matched_terms"] for item in result)
    assert not any(lesson["title"] in NATURAL_QUERY for lesson in LESSONS)


@pytest.mark.parametrize("query", [
    "安排周末旅行，比较酒店价格和交通时间。",
    "亲子旅行预订酒店、儿童票及住宿预算。",
    "核算年度财务预测，收入成本与现金流预算。",
    "整理Python代码模板和单元测试。",
    "调整PPT生成代码模板的修改范围，仅改Python模块。",
    "整改macOS跨平台兼容性和GitHub上游接入，限定修改范围。",
])
def test_unrelated_travel_finance_and_software_queries_do_not_recall_customer_lessons(query):
    assert matches(query) == [None, None, None]


@pytest.mark.parametrize("query,index,concept", [
    ("给小学三年级讲解药物发现，用孩子听得懂的话解释术语。", 0, "child_friendly_teaching"),
    ("照片太暗沉，文字叠在上面读不清，希望换一种表达关系。", 1, "image_text_relationship"),
    ("局部改页后复查未改页面，确认反馈范围是否都落实。", 2, "revision_scope"),
])
def test_meaningful_paraphrases_share_role_concepts(query, index, concept):
    result = matches(query)[index]
    assert result and concept in result["matched_concepts"]


def test_independent_short_words_do_not_become_global_similarity():
    assert "child_friendly_teaching" not in context_concepts("儿童旅行住宿预算。另一个项目讨论专业材料讲解。")
    assert match_context_text("图片，儿童，预算", "儿童票与图片素材", "无关的旅行资料") is None
    assert match_context_text("暗沉，图文，微调", "暗沉色调的旅行图集", "微调旅游预算") is None


def test_operational_scope_still_rejects_mac_words_embedded_in_unrelated_case_body():
    query = "整改Mac兼容性、GitHub接入和代码模板的修改范围"
    assert match_context_text(query, LESSONS[2]["title"], LESSONS[2]["action"] + " macOS GitHub", scope_title="小学科普课件") is None
    assert match_context_text(query, "Mac兼容性与GitHub代码库接入", "维护Python运行时")


def _offline_project(repo: Path):
    intake = "一般动态10p  资料全美化，主要目标是美化+对三年级小朋友友好、易懂"
    feedback = "明显不合适，上方很暗沉，而且文字直接在图片上"
    archive(repo, [message("user", intake), message("assistant", "我会调整图文分区并核对前后页面。", "commentary"), message("user", feedback)])
    configure(repo)
    def reviewer(_repo, _run, packet, _config):
        response = review_for(packet)
        if packet["phase"] == "segment":
            users = [record for record in packet["records"] if record["role"] == "user"]
            response["conditional_lessons"] = copy.deepcopy(LESSONS)
            for index, lesson in enumerate(response["conditional_lessons"]):
                source = users[0 if index == 0 else 1]
                lesson["evidence"] = [pl._fragment_reference(packet, source["source_id"], source["text"])]
        return response
    assert pl.run_project_learning(repo, reviewer=reviewer)["status"] == "progress"
    assert pl.run_project_learning(repo, reviewer=reviewer)["status"] == "review_complete"


def test_source_verified_project_recall_and_context_use_original_query_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "recommend_upstream_tools", lambda query: [])
    _offline_project(tmp_path)
    direct = pl.load_project_lessons(tmp_path, NATURAL_QUERY)
    context = ci.recall_context(NATURAL_QUERY, tmp_path)
    expected = {lesson["title"] for lesson in LESSONS}
    assert {lesson["title"] for lesson in direct["lessons"]} == expected
    assert {lesson["title"] for lesson in context["project_learning"]["lessons"]} == expected
    assert all(lesson["project_review_complete"] and lesson["outcome_level"] == "unresolved" for lesson in direct["lessons"])
    assert all(lesson["receipt_sha256"] and lesson["advisory_only"] for lesson in direct["lessons"])
    for query in ["家庭旅行和儿童票预订", "年度财务预测与成本预算", "Python代码模板与修改范围"]:
        assert not pl.load_project_lessons(tmp_path, query)["lessons"]
        assert not ci.recall_context(query, tmp_path)["project_learning"]["lessons"]
