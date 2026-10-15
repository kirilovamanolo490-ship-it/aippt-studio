from aippt_studio.design_context import build_design_context
from aippt_studio.models import DeckPlan, JobBrief, PresentationForm, PresentationIntent, SlideSpec


def _inputs(strategy=None, form=PresentationForm.BUSINESS_REPORT):
    brief = JobBrief(goal="年度工作汇报", audience="部门负责人",
                     presentation_intent=PresentationIntent(presentation_form=form))
    plan = DeckPlan(title="汇报", planning_strategy=strategy or {}, slides=[
        SlideSpec(id="s1", title="年度进展", purpose="展示结果", layout="content")])
    return brief, plan


def test_missing_narrative_is_reported_and_no_gaojie_scan(tmp_path, monkeypatch):
    # The explicit gap must remain visible, and absent categories must not trigger broad scans.
    from aippt_studio import gaojie_layouts
    monkeypatch.setattr(gaojie_layouts, "search_gaojie_layouts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("scan")))
    brief, plan = _inputs()
    context = build_design_context(tmp_path, plan, brief, workspace_root=tmp_path)
    assert context["missing_narrative_fields"] == ["task_specific_type", "audience_need", "core_message", "storyline", "evidence_boundary"]
    assert context["page_references"]["s1"]["status"] == "no_category_specified"
    assert context["whole_deck_references"]["status"] == "catalog_unavailable"


def test_explicit_metadata_and_references_are_discovery_only(tmp_path, monkeypatch):
    from aippt_studio import template_library, gaojie_layouts
    calls = []
    monkeypatch.setattr(template_library, "search_catalog", lambda *a, **k: {
        "status": "ok", "results": [
            {"path": "高阶文化/component.pptx", "asset_type": "template"},
            {"path": "业务汇报/template.pptx", "asset_type": "template", "occasion_tags": ["工作汇报"]},
        ]})
    monkeypatch.setattr(gaojie_layouts, "search_gaojie_layouts", lambda library, query, **kw: (
        calls.append(kw["category"]) or {"status": "ok", "candidates": [{"source_slide": 4}] }))
    (tmp_path / "模板库" / "分析索引" / "高阶文化").mkdir(parents=True)
    (tmp_path / "模板库" / "CATALOG.jsonl").touch()
    brief, plan = _inputs({
        "narrative_intent": {"task_specific_type": "年度工作汇报", "audience_need": "决定资源", "core_message": "进度改善",
                             "storyline": "目标—行动—结果", "evidence_boundary": "仅使用已核验指标"},
        "page_design": {"s1": {"page_job": "证明改善", "content_relationship": "目标与实际对照",
                                "layout_categories": ["表格图表", "数据基座", "封面模板"],
                                "reference_decision": {"decision": "consider", "reason": "对照关系"}}}})
    context = build_design_context(tmp_path, plan, brief, workspace_root=tmp_path)
    assert context["form"] == "business_report"
    assert "competition" not in context["form"]
    assert context["missing_narrative_fields"] == []
    assert [x["path"] for x in context["whole_deck_references"]["candidates"]] == ["业务汇报/template.pptx"]
    page = context["page_references"]["s1"]
    assert page["layout_categories"] == ["表格图表", "数据基座"]
    assert calls == ["表格图表", "数据基座"]
    assert context["whole_deck_references"]["discovery_only"] is True
    assert page["status"] == "discovered"
    assert "applied" not in page


def test_reference_search_failure_is_advisory(tmp_path, monkeypatch):
    from aippt_studio import gaojie_layouts
    monkeypatch.setattr(gaojie_layouts, "search_gaojie_layouts", lambda *a, **k: (_ for _ in ()).throw(OSError("index offline")))
    (tmp_path / "模板库" / "分析索引" / "高阶文化").mkdir(parents=True)
    brief, plan = _inputs({"page_design": {"s1": {"layout_categories": ["封面模板"]}}})
    context = build_design_context(tmp_path, plan, brief, workspace_root=tmp_path)
    assert context["page_references"]["s1"]["status"] == "unavailable"
    assert context["page_references"]["s1"]["reason"] == "index offline"


def test_native_binding_exposes_gaps_and_object_links_without_claiming_application(tmp_path, monkeypatch):
    from aippt_studio import native_scene
    from aippt_studio.models import JobState, ProductionRoute
    from aippt_studio.storage import job_dir, save_job, write_json, read_json
    from aippt_studio.workflow import create_job
    monkeypatch.setattr(native_scene, "WORKSPACE_ROOT", tmp_path)
    job = create_job("设计链测试", job_id="design-chain-fixture")
    job.route = ProductionRoute.NATIVE
    job.state = JobState.PROTOTYPE_REVIEW
    save_job(job)
    root = job_dir(job.id)
    brief, plan = _inputs({"page_design": {"s1": {"reference_decision": {
        "source_path": "高阶文化/架构流程/source.pptx", "source_slide": 1,
        "borrowed_relationship": "资源沿各自动因汇入同一对象"}}}})
    write_json(root / "planning/deck-plan.json", plan)
    write_json(root / "planning/brief.json", brief)
    scene = {"slides": [{"id": "s1", "elements": [{"type": "text", "text": "年度进展",
              "options": {"x": 1, "y": 1, "w": 8, "h": 1}}]}]}
    first = native_scene.set_native_scene(job.id, scene)
    assert first["design_context"]["missing_narrative_fields"]
    assert first["design_context"]["reference_application_links"][0]["status"] == "missing_or_invalid_object_links"
    scene["reference_applications"] = [{"slide_id": "s1", "source_path": "高阶文化/架构流程/source.pptx",
                                        "source_slide": 1, "target_element_indexes": [0]}]
    second = native_scene.set_native_scene(job.id, scene)
    link = second["design_context"]["reference_application_links"][0]
    assert link["status"] == "declared_objects_located_not_visual_approval"
    assert link["target_elements"][0]["text"] == "年度进展"
    context = read_json(root / "planning/design-context.json")
    assert context["status"] == "advisory"
    assert context["scene_sha256"]
    scene["reference_applications"][0]["target_element_indexes"] = [99]
    third = native_scene.set_native_scene(job.id, scene)
    assert third["design_context"]["reference_application_links"][0]["status"] == "missing_or_invalid_object_links"


def test_real_catalog_routes_business_report_without_using_one_page_gallery(tmp_path):
    import json
    library = tmp_path / "模板库"
    library.mkdir()
    rows = [{"path": path, "title": title, "asset_type": "template", "page_count": count,
             "occasion_tags": tags, "page_role_tags": [], "style_tags": []}
            for path, title, count, tags in [
                ("工作汇报/医院财务.pptx", "医院财务", 15, ["工作汇报"]),
                ("创赛/医疗创新.pptx", "医院财务工作汇报", 30, ["竞赛", "工作汇报"]),
                ("工作汇报/单页.pptx", "医院财务", 1, ["工作汇报"]),
                ("高阶文化/单页.pptx", "医院财务", 1, ["工作汇报"])]]
    (library / "CATALOG.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False)+"\n" for r in rows))
    for row in rows:
        source = library / row['path']
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'discovery fixture')
    brief, plan = _inputs()
    brief.audience = "医院财务负责人"
    context = build_design_context(tmp_path, plan, brief, workspace_root=tmp_path)
    found = context["whole_deck_references"]
    assert found["status"] == "candidates_found", found
    assert [r["path"] for r in found["candidates"]] == ["工作汇报/医院财务.pptx"]


def test_broken_catalog_is_not_reported_as_no_match(tmp_path):
    library = tmp_path / '模板库'
    library.mkdir()
    (library / 'CATALOG.jsonl').write_text('{broken json\n')
    brief, plan = _inputs()
    result = build_design_context(tmp_path, plan, brief, workspace_root=tmp_path)
    assert result['whole_deck_references']['status'] == 'unavailable'
    assert result['whole_deck_references']['catalog_errors']
