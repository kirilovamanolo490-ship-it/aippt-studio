import pytest

from aippt_studio.design_context import build_design_context
from aippt_studio.models import DeckPlan, JobBrief, PresentationForm, PresentationIntent, SlideSpec


def _build(tmp_path, form, *, goal="新材料供应链经营复盘", must_include=None, strategy=None):
    library = tmp_path / "模板库"
    library.mkdir(parents=True, exist_ok=True)
    (library / "CATALOG.jsonl").write_text("{}\n", encoding="utf-8")
    brief = JobBrief(goal=goal, audience="运营团队", must_include=must_include or [],
                     presentation_intent=PresentationIntent(presentation_form=form))
    plan = DeckPlan(title="方案", planning_strategy=strategy or {}, slides=[
        SlideSpec(id="s1", title="主题", purpose="说明", layout="content")])
    return build_design_context(tmp_path, plan, brief, workspace_root=tmp_path)


@pytest.mark.parametrize("form,expected", [
    (PresentationForm.BUSINESS_REPORT, "工作汇报"),
    (PresentationForm.TEACHING, "教学"),
    (PresentationForm.GOVERNMENT_BRIEFING, "政务汇报"),
    (PresentationForm.TECHNICAL_EXPLANATION, "技术说明"),
    (PresentationForm.ACADEMIC_DEFENSE, "学术答辩"),
    (PresentationForm.COMPETITION_DEFENSE, "竞赛答辩"),
    (PresentationForm.PREMIUM_BRAND_PORTFOLIO, "品牌展示"),
    (PresentationForm.CEREMONIAL_STORY, "仪式"),
    (PresentationForm.GENERAL, "通用演示"),
])
def test_every_form_builds_chinese_query_without_defaulting_to_business(tmp_path, monkeypatch, form, expected):
    from aippt_studio import template_library
    calls = []

    def search(_catalog, query, **kwargs):
        calls.append((query, kwargs))
        return {"results": [{"path": "案例/deck.pptx", "page_count": 12,
                             "occasion_tags": [expected], "source_sha256": "a" * 64}]}

    monkeypatch.setattr(template_library, "search_catalog", search)
    context = _build(tmp_path, form, goal="供应链复盘", must_include=["运营改善"])
    refs = context["whole_deck_references"]
    assert expected in calls[0][0]
    assert "供应链复盘" in calls[0][0]
    query_terms = calls[0][0].split()
    assert "工作汇报" in query_terms if form == PresentationForm.BUSINESS_REPORT else "工作汇报" not in query_terms
    assert refs["status"] == "candidates_found"
    assert refs["discovery_only"] is True
    assert refs["query_basis"]["source"] == "presentation_form_and_brief"


def test_explicit_query_takes_priority_and_records_basis(tmp_path, monkeypatch):
    from aippt_studio import template_library
    seen = []
    monkeypatch.setattr(template_library, "search_catalog", lambda _p, q, **k: seen.append(q) or {"results": []})
    context = _build(tmp_path, PresentationForm.BUSINESS_REPORT,
                     strategy={"whole_deck_reference_decision": {"query": "创业路演 供应链平台"}})
    assert seen == ["创业路演 供应链平台"]
    refs = context["whole_deck_references"]
    assert refs["query_basis"] == {
        "source": "explicit_query", "explicit_query": "创业路演 供应链平台",
        "form_labels": ["工作汇报", "业务汇报", "经营分析", "述职", "年度总结", "阶段总结"],
        "topic_sources": [],
    }


def test_business_topic_does_not_guess_medical_industry(tmp_path, monkeypatch):
    from aippt_studio import template_library
    seen = []
    monkeypatch.setattr(template_library, "search_catalog", lambda _p, q, **k: seen.append(q) or {"results": []})
    _build(tmp_path, PresentationForm.BUSINESS_REPORT, goal="新材料企业经营复盘",
           must_include=["产能", "毛利"])
    assert "新材料企业经营复盘" in seen[0]
    assert "产能" in seen[0] and "毛利" in seen[0]
    assert not any(word in seen[0] for word in ("医院", "医疗", "护理"))


@pytest.mark.parametrize("query,compatible,incompatible", [
    ("职业规划大赛", "职业规划", "创业大赛"),
    ("创业创新创业竞赛", "创新创业", "职业规划大赛"),
])
def test_career_and_startup_competitions_are_not_interchangeable(tmp_path, monkeypatch, query, compatible, incompatible):
    from aippt_studio import template_library
    rows = [
        {"path": "cases/career.pptx", "page_count": 14, "occasion_tags": [compatible]},
        {"path": "cases/other.pptx", "page_count": 14, "occasion_tags": [incompatible]},
    ]
    monkeypatch.setattr(template_library, "search_catalog", lambda *_a, **_k: {"results": rows})
    context = _build(tmp_path, PresentationForm.COMPETITION_DEFENSE,
                     strategy={"whole_deck_reference_decision": {"query": query}})
    assert [x["path"] for x in context["whole_deck_references"]["candidates"]] == ["cases/career.pptx"]


def test_rejects_known_occasion_conflict_but_keeps_unknown_and_deduplicates(tmp_path, monkeypatch):
    from aippt_studio import template_library
    rows = [
        {"path": "cases/competition.pptx", "page_count": 16, "occasion_tags": ["竞赛答辩"]},
        {"path": "cases/unknown-a.pptx", "page_count": 16, "occasion_tags": []},
        {"path": "cases/unknown-b.pptx", "page_count": 16, "occasion_tags": [], "source_sha256": "b" * 64},
        {"path": "cases/duplicate.pptx", "page_count": 16, "occasion_tags": [], "source_sha256": "b" * 64},
    ]
    monkeypatch.setattr(template_library, "search_catalog", lambda *_a, **_k: {"results": rows})
    context = _build(tmp_path, PresentationForm.BUSINESS_REPORT)
    paths = [x["path"] for x in context["whole_deck_references"]["candidates"]]
    assert "cases/competition.pptx" not in paths
    assert "cases/unknown-a.pptx" in paths
    assert paths.count("cases/unknown-b.pptx") + paths.count("cases/duplicate.pptx") == 1
    assert next(x for x in context["whole_deck_references"]["candidates"]
                if x["path"] == "cases/unknown-a.pptx")["match_status"] == "unknown"


def test_real_catalog_party_work_report_label_matches_government_and_is_compatible_with_business(tmp_path, monkeypatch):
    from aippt_studio import template_library
    # Shape and vocabulary sampled read-only from 模板库/CATALOG.jsonl:
    # category=党政党建, occasion_tags include 党政工作汇报.
    row = {"path": "党政党建/党政工作汇报/example.pptx", "page_count": 12,
           "category": "党政党建", "occasion_tags": ["党政", "党政工作汇报"]}
    monkeypatch.setattr(template_library, "search_catalog", lambda *_a, **_k: {"results": [row]})
    government = _build(tmp_path / "gov", PresentationForm.GOVERNMENT_BRIEFING)
    gov_candidate = government["whole_deck_references"]["candidates"][0]
    assert gov_candidate["match_status"] == "match"

    business = _build(tmp_path / "biz", PresentationForm.BUSINESS_REPORT)
    biz_candidate = business["whole_deck_references"]["candidates"][0]
    assert biz_candidate["match_status"] == "compatible"


def test_known_teaching_conflict_is_filtered(tmp_path, monkeypatch):
    from aippt_studio import template_library
    rows = [
        {"path": "cases/course.pptx", "page_count": 12, "category": "教学培训", "occasion_tags": ["课程"]},
        {"path": "cases/report.pptx", "page_count": 12, "category": "工作汇报", "occasion_tags": ["工作汇报"]},
    ]
    monkeypatch.setattr(template_library, "search_catalog", lambda *_a, **_k: {"results": rows})
    context = _build(tmp_path, PresentationForm.GOVERNMENT_BRIEFING)
    assert [x["path"] for x in context["whole_deck_references"]["candidates"]] == ["cases/report.pptx"]


def test_single_page_and_index_errors_remain_handled(tmp_path, monkeypatch):
    from aippt_studio import template_library
    monkeypatch.setattr(template_library, "search_catalog", lambda *_a, **_k: {
        "results": [{"path": "cases/one-page.pptx", "page_count": 1, "occasion_tags": []}],
        "catalog_errors": ["bad row"],
    })
    context = _build(tmp_path, PresentationForm.TEACHING)
    assert context["whole_deck_references"]["status"] == "unavailable"
    assert context["whole_deck_references"]["candidates"] == []
    assert context["whole_deck_references"]["catalog_errors"] == ["bad row"]
