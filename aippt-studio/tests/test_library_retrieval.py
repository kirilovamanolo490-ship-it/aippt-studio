from __future__ import annotations

import json
from pathlib import Path

from aippt_studio.library_retrieval import retrieve_library_reuse


def _jsonl(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    return path


def _deck(path: str, sha: str, pages: list[dict], *, status: str = "machine_ooxml_unreviewed") -> dict:
    return {"schema": "aippt.library-depth.v1", "path": path, "expected_sha256": sha,
            "source_bytes": 123, "mtime_ns": 456, "status": status, "parse_status": "parsed",
            "occasion_tags": ["竞赛与答辩", "竞赛"], "topic_terms": ["社区养老", "社会工作"],
            "review": {"media_rights": "unverified"}, "slides": pages}


def _page(number: int, title: str, text: str, roles: list | str,
          pattern: str = "two_column_evidence", hierarchy: str = "标题 > 核心指标 > 证据说明") -> dict:
    return {"slide": number, "title": title, "text_excerpt": text,
            "role_candidates": roles,
            "composition": {"pattern": pattern, "image_text_relation": "图左文右", "regions": ["视觉区", "说明区"]},
            "hierarchy": {"levels": hierarchy, "reading_order": "左到右", "density": "medium"},
            "media_candidates": [{"kind": "illustration", "description": "社区服务场景插画", "format": "png",
                                  "sha256": "a" * 64}]}


def _fixture(tmp_path: Path, records: list[dict]) -> tuple[Path, Path]:
    catalog = _jsonl(tmp_path / "模板库" / "CATALOG.jsonl", [
        {"path": row["path"], "asset_type": row.get("asset_type", "template"), "title": "不相关文件名",
         "occasion_tags": row.get("occasion_tags", ["竞赛与答辩", "竞赛"]),
         "category": Path(row["path"]).parts[0], "source_sha256": row["expected_sha256"],
         "media_rights": "unverified"} for row in records
    ])
    depth = _jsonl(tmp_path / "模板库" / "DEPTH.jsonl", records)
    return catalog, depth


def test_content_and_structure_win_when_filename_has_no_request_terms(tmp_path: Path) -> None:
    relevant = _deck("竞赛与答辩/01.pptx", "1" * 64, [
        _page(1, "项目愿景", "社区养老服务需求分析，社工链接物业邻里，入户探访和健康支持", "方案设计"),
        _page(2, "项目实施路径", "项目计划、服务点位、合作机制和阶段成果", "实施计划", "roadmap"),
    ])
    topical_but_wrong = _deck("竞赛与答辩/02.pptx", "2" * 64, [
        _page(1, "社区养老", "社区养老社会工作服务方案", "方案设计", "plain_bullet_list", "同级正文"),
        _page(2, "服务内容", "入户探访和邻里支持", "方案设计", "plain_bullet_list", "同级正文"),
    ])
    catalog, depth = _fixture(tmp_path, [relevant, topical_but_wrong])
    result = retrieve_library_reuse(catalog, depth,
        request_text="大学生社工竞赛项目，围绕社区养老，呈现方案设计与实施路径。",
        form="competition_defense", slides=[
            {"title": "项目服务模式", "text": "社工链接物业邻里，开展入户探访和健康支持", "role": "方案设计",
             "composition": "两栏 图左文右", "hierarchy": "标题 核心指标 证据说明"},
            {"title": "实施路线", "text": "服务点位合作机制阶段成果", "role": "实施计划", "composition": "roadmap"},
        ])
    assert result["candidates"][0]["path"] == "竞赛与答辩/01.pptx"
    first = result["candidates"][0]
    assert set(first["scores"]) == {"content", "subject_alignment", "page_function", "composition", "hierarchy", "sequence"}
    assert first["evidence"]["sequence_matches"][0]["slide"] == 1
    assert first["target_pages"][0]["candidates"]
    assert first["visual_review"]["status"] == "not_visually_reviewed"
    assert first["media_rights"] == "unverified"
    assert first["media_reuse"][0]["reuse_status"] == "not_authorized_by_index"


def test_wrong_occasion_is_hard_filtered_even_when_topic_matches(tmp_path: Path) -> None:
    service = _deck("工作汇报/社区养老方案.pptx", "3" * 64, [
        _page(1, "社区养老社工服务方案", "竞赛项目 社区养老 服务计划", "方案设计")
    ])
    service["occasion_tags"] = ["工作汇报", "公共服务项目方案"]
    catalog, depth = _fixture(tmp_path, [service])
    result = retrieve_library_reuse(catalog, depth, request_text="社区养老社工竞赛项目", form="competition_defense")
    assert result["status"] == "no_match"
    assert result["occasion_filtered_count"] == 1


def test_career_growth_track_does_not_return_employment_deck(tmp_path: Path) -> None:
    root = "BB0086 大学生职业生涯规划大赛 PPT"
    growth = _deck(f"{root}/成长赛道/个人成长.pptx", "a" * 64, [
        _page(1, "成长赛道职业规划", "自我认知与能力成长实践", "目标定位"),
    ])
    employment = _deck(f"{root}/就业赛道/岗位规划.pptx", "b" * 64, [
        _page(1, "就业赛道职业规划", "目标岗位与能力证据", "目标定位"),
    ])
    unrelated = _deck("创赛/商业计划/产品路演.pptx", "c" * 64, [
        _page(1, "产品路演", "商业计划和市场成果", "目标定位"),
    ])
    other_profession = _deck(f"{root}/成长赛道/美育实践.pptx", "d" * 64, [
        _page(1, "成长赛道美育规划", "绘画与美术教育实践", "目标定位"),
    ])
    catalog, depth = _fixture(tmp_path, [growth, employment, unrelated, other_profession])
    result = retrieve_library_reuse(catalog, depth,
                                    request_text="大学生职业生涯规划大赛 成长赛道 护理个人能力实践",
                                    form="competition_defense", limit=5)
    paths = {item["path"] for item in result["candidates"]}
    assert growth["path"] in paths
    assert employment["path"] not in paths
    assert unrelated["path"] not in paths
    assert other_profession["path"] not in paths


def test_subject_alignment_demotes_cross_field_contest_and_exposes_source_evidence(tmp_path: Path) -> None:
    social = _deck("竞赛与答辩/social.pptx", "c" * 64, [
        _page(1, "社区社工养老服务创新项目", "竞赛答辩 项目创新与服务模式", "项目定位")
    ])
    medical = _deck("竞赛与答辩/medical.pptx", "d" * 64, [
        _page(1, "中药DNA条形码精准鉴定创新项目", "竞赛答辩 项目创新与服务模式", "项目定位")
    ])
    medical["topic_terms"] = ["中药", "DNA条形码", "药材", "精准鉴定"]
    neutral = _deck("竞赛与答辩/general.pptx", "e" * 64, [
        _page(1, "项目创新展示", "竞赛答辩 项目创新与服务模式", "项目定位")
    ])
    neutral["topic_terms"] = []
    catalog, depth = _fixture(tmp_path, [medical, neutral, social])
    result = retrieve_library_reuse(
        catalog, depth,
        request_text="大学生社工社区养老服务创新竞赛项目答辩",
        form="competition_defense",
        slides=[{"title": "项目定位", "message": "回应社区老人服务需求，形成邻里参与机制",
                 "page_role": "goal_positioning", "composition": "claim_evidence"}],
    )
    ordered = {row["path"]: row for row in result["candidates"]}
    assert ordered["竞赛与答辩/social.pptx"]["score"] > ordered["竞赛与答辩/medical.pptx"]["score"]
    assert ordered["竞赛与答辩/general.pptx"]["score"] > ordered["竞赛与答辩/medical.pptx"]["score"]
    paths = [row["path"] for row in result["candidates"]]
    assert paths.index("竞赛与答辩/general.pptx") < paths.index("竞赛与答辩/medical.pptx")
    evidence = ordered["竞赛与答辩/medical.pptx"]["evidence"]["subject_alignment"]
    assert evidence["status"] == "subject_conflict"
    assert "biomedical" in evidence["conflicting_subjects"]
    assert evidence["basis"].endswith("filenames excluded")
    assert ordered["竞赛与答辩/general.pptx"]["evidence"]["subject_alignment"]["status"] == "source_subject_unresolved"


def test_recognized_engineering_topic_is_not_neutral_for_social_work_request(tmp_path: Path) -> None:
    drone = _deck("竞赛与答辩/drone.pptx", "7" * 64, [
        _page(1, "高空外墙清洁无人机创新项目", "大学生竞赛项目 智能清洁设备产品服务模式", "项目定位")
    ])
    neutral = _deck("竞赛与答辩/general.pptx", "8" * 64, [
        _page(1, "项目创新展示", "大学生竞赛项目 产品服务模式", "项目定位")
    ])
    neutral["topic_terms"] = []
    catalog, depth = _fixture(tmp_path, [drone, neutral])
    result = retrieve_library_reuse(
        catalog, depth,
        request_text="大学生社工社区养老服务创新竞赛项目答辩",
        form="competition_defense",
        slides=[{"title": "项目定位", "message": "回应社区老人服务需求"}],
    )
    ordered = {row["path"]: row for row in result["candidates"]}
    fit = ordered["竞赛与答辩/drone.pptx"]["evidence"]["subject_alignment"]
    assert "robotics_engineering" in fit["source_subjects"]
    assert fit["status"] == "subject_conflict"
    assert ordered["竞赛与答辩/general.pptx"]["score"] > ordered["竞赛与答辩/drone.pptx"]["score"]
    assert "source_subject_conflict" in ordered["竞赛与答辩/drone.pptx"]["reuse_gaps"]
    assert "subject_fit_requires_manual_review" in ordered["竞赛与答辩/general.pptx"]["reuse_gaps"]


def test_community_context_alone_is_weak_for_social_work_and_aging_request(tmp_path: Path) -> None:
    gym = _deck("竞赛与答辩/gym.pptx", "9" * 64, [
        _page(1, "社区健身俱乐部创新项目", "社群会员和线下课程运营", "项目定位")
    ])
    gym["topic_terms"] = ["健身", "俱乐部", "社群", "会员"]
    neutral = _deck("竞赛与答辩/general.pptx", "a" * 64, [
        _page(1, "项目创新展示", "竞赛项目实施与成果", "项目定位")
    ])
    neutral["topic_terms"] = []
    catalog, depth = _fixture(tmp_path, [gym, neutral])
    result = retrieve_library_reuse(
        catalog, depth,
        request_text="社工社区养老服务创新竞赛项目答辩",
        form="competition_defense",
        slides=[{"title": "项目定位", "message": "回应社区老人服务需求"}],
    )
    ordered = {row["path"]: row for row in result["candidates"]}
    fit = ordered["竞赛与答辩/gym.pptx"]["evidence"]["subject_alignment"]
    assert fit["status"] == "weak_context_only"
    assert fit["matched_subjects"] == []
    assert fit["supporting_subjects"] == ["community_context"]
    paths = [row["path"] for row in result["candidates"]]
    assert paths.index("竞赛与答辩/general.pptx") < paths.index("竞赛与答辩/gym.pptx")


def test_partial_subject_overlap_does_not_hide_salient_cross_field_conflict(tmp_path: Path) -> None:
    aligned = _deck("竞赛与答辩/aligned.pptx", "a" * 64, [
        _page(1, "社区社工养老服务创新", "老人探访与邻里参与", "项目定位")
    ])
    mixed = _deck("竞赛与答辩/mixed.pptx", "b" * 64, [
        _page(1, "社工中药鉴定创新项目", "药材识别与社区推广", "项目定位")
    ])
    context = _deck("竞赛与答辩/context.pptx", "c" * 64, [
        _page(1, "社区医疗创新项目", "药物服务与社区推广", "项目定位")
    ])
    catalog, depth = _fixture(tmp_path, [mixed, context, aligned])
    result = retrieve_library_reuse(
        catalog, depth, request_text="社工社区养老服务创新竞赛答辩",
        form="competition_defense", slides=[{"title": "项目定位", "message": "社工回应社区老人养老需求"}],
    )
    ordered = {row["path"]: row for row in result["candidates"]}
    mixed_row = ordered["竞赛与答辩/mixed.pptx"]
    mixed_fit = mixed_row["evidence"]["subject_alignment"]
    assert mixed_fit["status"] == "mixed_subject_conflict"
    assert mixed_fit["matched_subjects"] == ["community_context", "social_work"]
    assert "biomedical" in mixed_fit["conflicting_subjects"]
    assert "source_subject_conflict" in mixed_row["reuse_gaps"]
    context_fit = ordered["竞赛与答辩/context.pptx"]["evidence"]["subject_alignment"]
    assert context_fit["status"] == "subject_conflict"
    assert context_fit["matched_subjects"] == []
    assert "biomedical" in context_fit["conflicting_subjects"]
    assert ordered["竞赛与答辩/aligned.pptx"]["score"] > mixed_row["score"]


def test_one_late_community_sales_mention_does_not_define_fruit_ecommerce_deck(tmp_path: Path) -> None:
    fruit = _deck("竞赛与答辩/fruit.pptx", "f" * 64, [
        _page(1, "鲜果电商供应链创新项目", "水果种植、采收和线上零售", "项目定位"),
        _page(2, "渠道与销售", "线上销售和订单履约", "方案设计"),
        _page(3, "区域团购渠道", "招募社区团长拓展鲜果团购渠道", "实施计划"),
        _page(4, "项目收益", "销售额、复购率和物流成本", "成果证据"),
    ])
    fruit["topic_terms"] = ["鲜果", "水果种植", "电商", "社区团长", "团购"]
    social = _deck("竞赛与答辩/social.pptx", "1" * 64, [
        _page(1, "邻里安养社区社工服务项目", "社区老人需求与邻里互助", "项目定位"),
        _page(2, "走访与服务需求", "社工走访老人，识别社区养老需求", "问题证据"),
        _page(3, "协作服务机制", "社工、物业和邻里持续参与", "方案设计"),
    ])
    catalog, depth = _fixture(tmp_path, [fruit, social])
    result = retrieve_library_reuse(
        catalog, depth,
        request_text="大学生社工社区养老服务创新竞赛项目答辩",
        form="competition_defense",
        slides=[{"title": "项目定位", "message": "回应社区老人服务需求，形成邻里参与机制",
                 "page_role": "goal_positioning"}],
    )
    ordered = {row["path"]: row for row in result["candidates"]}
    fruit_fit = ordered["竞赛与答辩/fruit.pptx"]["evidence"]["subject_alignment"]
    assert ordered["竞赛与答辩/social.pptx"]["score"] > ordered["竞赛与答辩/fruit.pptx"]["score"]
    assert fruit_fit["status"] == "subject_conflict"
    assert "community_context" not in fruit_fit["matched_subjects"]
    assert "community_context" in fruit_fit["incidental_subjects"]
    assert fruit_fit["subject_page_occurrences"]["community_context"] == [3]


def test_conflicting_source_subject_demotes_embedded_asset_without_classifying_its_image(tmp_path: Path) -> None:
    def embedded(shape: int) -> dict:
        return {"kind": "illustration", "description": "项目服务模式示意图", "format": "png",
                "sha256": str(shape) * 64, "source_slide": 1,
                "source_shape": shape, "media_part": f"ppt/media/image{shape}.png",
                "relationship_id": f"rId{shape}", "role_candidates": ["方案设计"],
                "composition": "两栏图文", "hierarchy": "标题 > 说明"}

    medical = _deck("竞赛与答辩/medical.pptx", "3" * 64, [
        _page(1, "中药DNA条形码精准鉴定创新项目", "竞赛项目服务模式与实施路径", "方案设计")
    ])
    social = _deck("竞赛与答辩/social.pptx", "4" * 64, [
        _page(1, "社区社工养老服务创新项目", "竞赛项目服务模式与实施路径", "方案设计")
    ])
    medical["slides"][0]["media_candidates"] = [embedded(1)]
    social["slides"][0]["media_candidates"] = [embedded(2)]
    catalog, depth = _fixture(tmp_path, [medical, social])
    result = retrieve_library_reuse(
        catalog, depth,
        request_text="大学生社工社区养老服务创新竞赛项目答辩",
        form="competition_defense",
        slides=[{"title": "项目服务模式", "message": "社区养老服务模式", "role": "方案设计",
                 "composition": "两栏图文", "hierarchy": "标题 说明"}],
    )
    assets = {Path(row["source_path"]).name: row for row in result["asset_candidates"]}
    conflict = assets["medical.pptx"]
    aligned = assets["social.pptx"]
    assert conflict["score"] < aligned["score"]
    assert conflict["evidence"]["source_subject_alignment"]["status"] == "subject_conflict"
    assert "source_subject_conflict" in conflict["gaps"]
    assert "image_meaning_unverified" in conflict["gaps"]
    assert "image_meaning_unverified" in aligned["gaps"]
    assert conflict["context_is_image_meaning"] is False


def test_social_research_label_does_not_count_as_social_work_alignment(tmp_path: Path) -> None:
    agriculture_research = _deck("竞赛与答辩/社会调研农业项目.pptx", "2" * 64, [
        _page(1, "乡村振兴背景下数字赋能农业产业链转型研究", "基于6省22村的实践探索", "项目定位"),
        _page(2, "社会调研方法", "调查农户、农业企业和产业链数据", "方法路线"),
        _page(3, "调研成果", "构建农业数字化成熟度模型", "成果证据"),
    ])
    agriculture_research["topic_terms"] = ["农业", "产业链", "乡村振兴", "社会调研", "农户"]
    catalog, depth = _fixture(tmp_path, [agriculture_research])
    result = retrieve_library_reuse(
        catalog, depth,
        request_text="社工社区养老服务创新竞赛项目答辩",
        form="competition_defense",
        slides=[{"title": "项目定位", "message": "社工回应社区老人养老服务需求"}],
    )
    fit = result["candidates"][0]["evidence"]["subject_alignment"]
    assert fit["status"] == "subject_conflict"
    assert "agriculture" in fit["source_subjects"]
    assert "social_work" not in fit["matched_subjects"]
    assert "social_work" not in fit["source_subjects"]
    assert "social_research" in fit["incidental_subjects"]


def test_sequence_alignment_prefers_correct_source_page_order(tmp_path: Path) -> None:
    roles = ["问题证据", "方案设计", "实施计划"]
    content = ["社区老人生活不便", "社工链接物业邻里", "安排探访和便民服务"]
    right = _deck("竞赛与答辩/right.pptx", "4" * 64,
        [_page(i + 1, role, content[i], role) for i, role in enumerate(roles)])
    reversed_pages = list(reversed([_page(i + 1, role, content[i], role) for i, role in enumerate(roles)]))
    wrong = _deck("竞赛与答辩/wrong.pptx", "5" * 64, reversed_pages)
    catalog, depth = _fixture(tmp_path, [wrong, right])
    targets = [{"title": role, "text": content[i], "role": role} for i, role in enumerate(roles)]
    result = retrieve_library_reuse(catalog, depth, request_text="社区养老社工竞赛", form="competition_defense", slides=targets)
    ordered = {row["path"]: row for row in result["candidates"]}
    assert ordered["竞赛与答辩/right.pptx"]["scores"]["sequence"] > ordered["竞赛与答辩/wrong.pptx"]["scores"]["sequence"]
    assert result["candidates"][0]["path"] == "竞赛与答辩/right.pptx"
    assert all(target["candidates"][0]["sequence_aligned"] for target in result["candidates"][0]["target_pages"])


def test_machine_ooxml_status_never_claims_visual_review_and_role_confidence_supported(tmp_path: Path) -> None:
    record = _deck("竞赛与答辩/sample.pptx", "6" * 64, [
        _page(1, "核心指标", "社区养老覆盖人数", [{"role": "数据图表", "confidence": .8}])
    ])
    catalog, depth = _fixture(tmp_path, [record])
    result = retrieve_library_reuse(catalog, depth, request_text="竞赛社区养老数据图表", form="competition_defense",
                                    slides=[{"title": "服务覆盖", "text": "覆盖人数", "role": "数据图表"}])
    match = result["candidates"][0]["target_pages"][0]["candidates"][0]
    assert match["scores"]["page_function"] == .8
    assert result["candidates"][0]["visual_review"]["status"] == "not_visually_reviewed"


def test_invalid_limit_rejected(tmp_path: Path) -> None:
    catalog = _jsonl(tmp_path / "CATALOG.jsonl", [])
    depth = _jsonl(tmp_path / "DEPTH.jsonl", [])
    try:
        retrieve_library_reuse(catalog, depth, request_text="x", limit=0)
    except ValueError as error:
        assert "limit" in str(error)
    else:
        raise AssertionError("invalid limit accepted")


def test_chinese_catalog_labels_hard_filter_each_supported_form_and_generic_defense_is_not_competition(tmp_path: Path) -> None:
    cases = [
        ("business_report", "工作汇报/one.pptx", ["工作汇报", "年度总结"]),
        ("government_briefing", "党政党建/two.pptx", ["党政汇报", "政策学习"]),
        ("teaching", "教育教学/three.pptx", ["教学", "课程"]),
        ("technical_explanation", "科技与互联网/four.pptx", ["科技", "技术方案"]),
        ("ceremonial_story", "文化与活动/five.pptx", ["活动", "文化活动"]),
        ("premium_brand_portfolio", "作品集/six.pptx", ["作品集", "品牌展示"]),
        ("competition_defense", "竞赛与答辩/seven.pptx", ["竞赛", "挑战杯"]),
    ]
    records = []
    for number, (_form, path, tags) in enumerate(cases, start=1):
        record = _deck(path, str(number) * 64, [_page(1, "方案介绍", "围绕具体项目的实施与成果", "方案设计")])
        record["occasion_tags"] = tags
        records.append(record)
    generic = _deck("通用模板/eight.pptx", "8" * 64,
                    [_page(1, "答辩方案", "围绕具体项目的实施与成果", "方案设计")])
    generic["occasion_tags"] = ["答辩"]
    records.append(generic)
    catalog, depth = _fixture(tmp_path, records)

    for form, path, _tags in cases:
        result = retrieve_library_reuse(catalog, depth, request_text="具体项目的实施与成果", form=form)
        assert result["status"] == "ready"
        assert {row["path"] for row in result["candidates"]} == {path}
        assert result["occasion_filtered_count"] == len(records) - 1

    contest = retrieve_library_reuse(catalog, depth, request_text="项目答辩", form="competition_defense")
    assert all(row["path"] != "通用模板/eight.pptx" for row in contest["candidates"])


def test_actual_depth_index_page_roles_match_target_page_role(tmp_path: Path) -> None:
    roles = ["cover", "agenda", "background_problem", "goal_positioning", "method_approach",
             "process_architecture", "evidence_results", "team_organization", "budget_model",
             "timeline_plan", "case_example", "closing_outlook"]
    record = _deck("竞赛与答辩/roles.pptx", "9" * 64,
                   [_page(index, role, f"关于{role}的内容", [{"role": role, "confidence": .9}])
                    for index, role in enumerate(roles, 1)])
    catalog, depth = _fixture(tmp_path, [record])
    result = retrieve_library_reuse(catalog, depth, request_text="竞赛项目展示", form="competition_defense",
                                    slides=[{"title": f"目标页{i}", "text": "相关内容", "page_role": role}
                                            for i, role in enumerate(roles)])
    matches = result["candidates"][0]["target_pages"]
    assert len(matches) == len(roles)
    assert all(row["candidates"][0]["scores"]["page_function"] == .9 for row in matches)


def test_audience_facing_page_role_falls_back_to_title_purpose_inference(tmp_path: Path) -> None:
    record = _deck("竞赛与答辩/path.pptx", "a" * 64, [
        _page(1, "三方协作流程", "社工物业邻里共同探访，处理老人出门和生活服务需求", "process_architecture")
    ])
    catalog, depth = _fixture(tmp_path, [record])
    result = retrieve_library_reuse(catalog, depth, request_text="社区养老竞赛项目",
        form="competition_defense", slides=[{"title": "社工与物业如何协作", "purpose": "共同行动流程",
                                              "page_role": "老人出门更方便"}])
    match = result["candidates"][0]["target_pages"][0]["candidates"][0]
    assert match["scores"]["page_function"] == 1.0
    assert match["evidence"]["page_role_basis"] == "title_message_purpose_inference"


def test_diagram_assets_are_separate_ranked_results_with_source_trace_and_no_inferred_license(tmp_path: Path) -> None:
    record = _deck("素材资源/社区协作图标集.pptx", "b" * 64, [])
    record["asset_type"] = "diagram_asset"
    record["occasion_tags"] = ["素材资源"]
    record["media_candidates"] = [{"kind": "vector_diagram", "description": "社区养老社工物业邻里协作流程图",
                                   "format": "svg", "source_slide": 4, "shape_id": "91",
                                   "media_part": "ppt/media/image8.svg", "relationship_id": "rId8",
                                   "media_path": "ppt/media/image8.svg", "sha256": "c" * 64,
                                   "role_candidates": ["process_architecture"],
                                   "composition": {"pattern": "流程图", "regions": ["节点", "连线"]},
                                   "rights_status": "unverified", "reuse_status": "not_authorized_by_index"}]
    catalog, depth = _fixture(tmp_path, [record])
    result = retrieve_library_reuse(catalog, depth, request_text="社区养老社工竞赛中的多方协作流程",
        form="competition_defense", slides=[{"title": "多方服务协作流程", "text": "社工物业邻里协同",
                                              "page_role": "process_architecture", "composition": "流程图节点连线"}])
    assert result["candidates"] == []
    assert len(result["asset_candidates"]) == 1
    asset = result["asset_candidates"][0]
    assert asset["source_pptx"] == "素材资源/社区协作图标集.pptx"
    assert asset["source_slide"] == 4
    assert asset["source_shape"] == 91
    assert asset["media_path"] == "ppt/media/image8.svg"
    assert asset["media_sha256"] == "c" * 64
    assert asset["reuse_permission"] == "reference_only"
    assert asset["rights_status"] == "unverified"
    assert set(asset["scores"]) == {"content", "page_function", "composition", "hierarchy"}


def test_native_shape_diagram_asset_is_a_source_page_reference_not_bindable_media(tmp_path: Path) -> None:
    record = _deck("素材资源/社区协作原生图示.pptx", "f" * 64, [])
    record["asset_type"] = "diagram_asset"
    record["occasion_tags"] = ["素材资源"]
    record["media_candidates"] = [{"kind": "native_diagram", "description": "社区养老社工物业协作流程",
                                   "source_slide": 2, "source_shape_id": "91",
                                   "rights_status": "unverified", "reuse_status": "not_authorized_by_index"}]
    catalog, depth = _fixture(tmp_path, [record])
    result = retrieve_library_reuse(catalog, depth,
        request_text="社区养老社工竞赛中的多方协作流程", form="competition_defense",
        slides=[{"title": "多方服务协作流程", "text": "社工物业邻里协同",
                 "page_role": "process_architecture", "composition": "流程图节点连线"}])

    assert result["asset_candidates"] == []
    assert len(result["source_page_candidates"]) == 1
    candidate = result["source_page_candidates"][0]
    assert candidate["source_pptx"] == "素材资源/社区协作原生图示.pptx"
    assert candidate["reuse_permission"] == "reference_only"
    assert "redraw" in candidate["reason"]


def test_embedded_media_candidate_keeps_depth_index_shape_and_part(tmp_path: Path) -> None:
    page = _page(3, "社工与物业协作机制", "通过联动服务回应社区老人需求", "process_architecture")
    page["media_candidates"] = [{"kind": "svg", "description": "Embedded media candidate; visual meaning has not been reviewed.",
                                 "source_slide": 3, "source_shape_id": "42", "media_part": "ppt/media/image4.svg",
                                 "relationship_id": "rId9", "sha256": "e" * 64, "rights_status": "unverified"}]
    catalog, depth = _fixture(tmp_path, [_deck("竞赛与答辩/样本.pptx", "d" * 64, [page])])
    result = retrieve_library_reuse(catalog, depth, request_text="社区养老竞赛项目中的社工物业协作",
                                    form="competition_defense", slides=[{"title": "协作机制", "message": "社工物业联动", "page_role": "process_architecture"}])
    candidate = result["asset_candidates"][0]
    assert candidate["source_pptx"] == "竞赛与答辩/样本.pptx"
    assert candidate["source_slide"] == 3 and candidate["source_shape"] == 42
    assert candidate["media_path"] == "ppt/media/image4.svg"
    assert candidate["relationship_id"] == "rId9"
    assert candidate["source_page_context"].startswith("社工与物业协作机制")
    assert candidate["context_is_image_meaning"] is False
