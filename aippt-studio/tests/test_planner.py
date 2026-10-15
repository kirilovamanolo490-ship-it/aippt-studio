from __future__ import annotations

from pathlib import Path
import json
import re

import pytest

from aippt_studio.ingest import ingest_sources
from aippt_studio.models import DeckPlan, JobState, PageCountError
from aippt_studio.planner import _explicit_target_pages, _partition_source_lines, create_brief, create_deck_plan
from aippt_studio.storage import job_dir, load_job, read_model, write_json
from aippt_studio.workflow import approve_gate, create_job, export_job, resume_job, run_job


def _source(tmp_path: Path, pages: int | None, *, facts: int = 8, name: str = "source", offset: int = 0) -> Path:
    path = tmp_path / f"{name}.md"
    instruction = f"制作{pages}页PPT\n" if pages is not None else ""
    path.write_text(instruction + "\n".join(
        f"第{index}项项目事实：该项目的第{index}个独立工作单元已完成核查，团队保留对应资料用于汇报。"
        for index in range(offset + 1, offset + facts + 1)
    ), encoding="utf-8")
    return path


@pytest.mark.parametrize(("text", "expected"), [
    ("制作1页PPT", 1), ("做成四页演示文稿", 4), ("制作一份八页PPT", 8),
    ("PPT总页数必须为200页", 200), ("页数调整为二百页", 200), ("页数为一百二十八页", 128),
    ("页数为10页。页数改为14页", 14),
    ("总页数为14页，先制作3页样稿", 14),
    ("先制作3页样稿，再制作24页正式稿", 24),
    ("参考文件有30页，只改第4页", None),
])
def test_parse_explicit_total_count_without_confusing_reference_or_prototype(text: str, expected: int | None) -> None:
    assert _explicit_target_pages(text) == expected


@pytest.mark.parametrize("text", ["制作0页PPT", "制作201页PPT", "总页数为1000页", "制作二百零一页PPT", "制作-1页PPT", "制作2.5页PPT"])
def test_bad_explicit_count_is_not_truncated_or_silently_defaulted(text: str) -> None:
    with pytest.raises(PageCountError):
        _explicit_target_pages(text)


@pytest.mark.parametrize("pages", [1, 2, 3, 4, 24, 200])
def test_real_intake_and_planner_honor_explicit_count(tmp_path: Path, pages: int) -> None:
    job = create_job("页数合同", job_id=f"pages-{pages}")
    ingest_sources(job.id, [str(_source(tmp_path, pages, facts=max(8, pages)))])
    brief = create_brief(job.id)
    assert brief.target_pages == pages and brief.target_pages_explicit is True
    plan = create_deck_plan(job.id)
    assert len(plan.slides) == pages
    assert plan.slides[-1].id == f"slide-{pages:02d}"
    if pages <= 2:
        assert all(slide.layout in {"content", "evidence"} for slide in plan.slides)
        assert "项目事实" in plan.slides[0].title
        assert all("#line:" in ref for slide in plan.slides for ref in slide.evidence_refs)
    else:
        # The fixture contains no terminal source-grounded conclusion/action.
        # Page count alone must not manufacture a familiar but empty close.
        assert all(slide.layout != "conclusion" for slide in plan.slides)
        assert plan.planning_strategy["narrative_scaffold"]["source_grounded_closing"]["enabled"] is False


@pytest.mark.parametrize("pages", [1, 4, 24])
def test_explicit_count_survives_prototype_full_build_and_final_approval_with_fake_renderer(tmp_path: Path, pages: int) -> None:
    job = create_job("明确页数完整链", job_id=f"complete-pages-{pages}")
    ingest_sources(job.id, [str(_source(tmp_path, pages, facts=max(8, pages)))])
    assert run_job(job.id)["state"] == "brief_review"
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == "prototype_review"
    root = job_dir(job.id)
    # The suite's renderer produces an empty ZIP plus per-slide previews. Verify
    # the renderer input and output cardinality, without claiming native Office QA.
    assert len(list((root / "build/prototype-previews").glob("*.png"))) == min(3, pages)
    assert len(re.findall(r'data-slide-id="', (root / "build/prototype.html").read_text())) == min(3, pages)
    approve_gate(job.id, "prototype")
    assert run_job(job.id)["state"] == "final_review"
    assert len(list((root / "build/previews").glob("*.png"))) == pages
    assert len(re.findall(r'data-slide-id="', (root / "build/deck.html").read_text())) == pages
    approve_gate(job.id, "final")
    assert export_job(job.id, skip_powerpoint=True).status == "candidate"


def test_insufficient_explicit_material_reports_gap_and_recovers_via_intake(tmp_path: Path) -> None:
    job = create_job("缺少正文材料", job_id="insufficient-pages")
    ingest_sources(job.id, [str(_source(tmp_path, 14, facts=6))])
    assert run_job(job.id)["state"] == "brief_review"
    approve_gate(job.id, "brief")
    result = run_job(job.id)
    assert result["state"] == "blocked"
    assert "缺少 6 个" in load_job(job.id).blocked_reason
    assert load_job(job.id).resume_state == JobState.INTAKE
    assert not (job_dir(job.id) / "planning/deck-plan.json").exists()
    supplement = _source(tmp_path, None, facts=6, name="supplement", offset=6)
    ingest_sources(job.id, [str(supplement)])
    assert resume_job(job.id)["state"] == "brief_review"
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == "prototype_review"
    assert len(read_model(job_dir(job.id) / "planning/deck-plan.json", DeckPlan).slides) == 14


@pytest.mark.parametrize("gate", ["prototype", "final"])
def test_approval_rejects_plan_that_drops_an_explicit_page(tmp_path: Path, gate: str) -> None:
    job = create_job("审阅页数", job_id=f"mismatch-{gate}")
    ingest_sources(job.id, [str(_source(tmp_path, 4))])
    run_job(job.id)
    approve_gate(job.id, "brief")
    run_job(job.id)
    if gate == "final":
        approve_gate(job.id, "prototype")
        run_job(job.id)
    path = job_dir(job.id) / "planning/deck-plan.json"
    plan = read_model(path, DeckPlan)
    plan.slides.pop()
    write_json(path, plan)
    with pytest.raises(PageCountError, match="明确要求 4 页"):
        approve_gate(job.id, gate)
    assert load_job(job.id).approvals[gate] is False


def test_default_ten_keeps_existing_material_driven_planning(tmp_path: Path) -> None:
    job = create_job("默认材料规划", job_id="default-pages")
    ingest_sources(job.id, [str(_source(tmp_path, None, facts=6))])
    brief = create_brief(job.id)
    assert brief.target_pages == 10 and brief.target_pages_explicit is False
    # Six source facts plus meaningful cover/outline yield eight pages.  The
    # ninth generic conclusion from the older scaffold would only repeat them.
    assert len(create_deck_plan(job.id).slides) == 8


def test_repeated_facts_in_another_source_do_not_fill_an_explicit_gap(tmp_path: Path) -> None:
    job = create_job("重复材料", job_id="duplicate-facts")
    ingest_sources(job.id, [str(_source(tmp_path, 14, facts=6)), str(_source(tmp_path, None, facts=6, name="duplicate"))])
    create_brief(job.id)
    with pytest.raises(PageCountError, match="缺少 6 个"):
        create_deck_plan(job.id)


def test_planner_preserves_short_long_and_more_than_four_facts_on_a_page(tmp_path: Path) -> None:
    job = create_job("完整事实保留", job_id="all-source-facts")
    facts = ["利润32万元", "本段完整原始材料超过一百六十字也不能因为排版直接丢弃。" * 10]
    facts.extend(f"第{n}项调查结果保留独立的数据与验证事实。" for n in range(8))
    source = tmp_path / "complete-source.md"
    source.write_text("制作1页PPT\n" + "\n".join(facts), encoding="utf-8")
    ingest_sources(job.id, [str(source)])
    create_brief(job.id)
    plan = create_deck_plan(job.id)
    assert plan.slides[0].points == facts
    assert len(plan.slides[0].evidence_refs) == len(facts)
    assert plan.planning_strategy["authoring_defaults"]["typography_mode"] == "advisory"


def test_scaffold_uses_source_content_without_invented_business_actions(tmp_path: Path) -> None:
    job = create_job("事实驱动的演示", job_id="no-generic-actions")
    source = _source(tmp_path, 8, facts=8)
    ingest_sources(job.id, [str(source)])
    create_brief(job.id)
    plan = create_deck_plan(job.id)
    text = " ".join(slide.title + " ".join(slide.points) for slide in plan.slides)
    assert all(fake not in text for fake in ["确认优先级与责任人", "清晰决策链", "用可量化证据持续复盘", "高级、可编辑、可交付"])
    source_facts = source.read_text().splitlines()[1:]
    actual = [point for slide in plan.slides if slide.layout in {"content", "evidence"} for point in slide.points]
    assert actual == source_facts
    assert plan.subtitle == ""
    summary = next(slide for slide in plan.slides if slide.layout == "summary")
    assert summary.speaker_notes.startswith("本次汇报包括：")
    assert "先给听众" not in summary.speaker_notes


def test_planner_uses_a_terminal_source_close_without_repeating_body_facts(tmp_path: Path) -> None:
    job = create_job("来源收束", job_id="source-grounded-close")
    source = tmp_path / "source-grounded-close.md"
    facts = [
        "现场核验已完成，关键记录可以复核。",
        "本轮抽检覆盖三类设备，异常项已经登记。",
        "已与现场负责人确认整改范围和完成时点。",
    ]
    close = "下一阶段将按登记的整改范围复核完成情况。"
    source.write_text("制作4页PPT\n" + "\n".join([*facts, close]), encoding="utf-8")
    ingest_sources(job.id, [str(source)])

    create_brief(job.id)
    plan = create_deck_plan(job.id)

    closing = next(slide for slide in plan.slides if slide.layout == "conclusion")
    body_points = [point for slide in plan.slides if slide.layout in {"content", "evidence"} for point in slide.points]
    assert closing.title == close.rstrip("。")
    assert closing.points == [close]
    assert close not in body_points
    assert all(fact in body_points for fact in facts)
    scaffold = plan.planning_strategy["narrative_scaffold"]["source_grounded_closing"]
    assert scaffold["enabled"] is True
    assert scaffold["source_refs"][0].endswith("#line:5")


def test_unlabeled_presentation_instructions_are_held_out_of_visible_copy_and_notes(tmp_path: Path) -> None:
    job = create_job("现场交付说明", job_id="unlabeled-presentation-instruction")
    source = tmp_path / "mixed-unlabeled-source.md"
    facts = [
        "本月已完成20台设备现场验收，记录可以复核。",
        "抽检显示关键节点的记录完整率持续提升。",
        "下一阶段将按客户反馈复核剩余安装安排。",
    ]
    instructions = [
        "请不要在PPT页面角落添加01、02，也不要用小字说明制作过程。",
        "我会为你调整图片遮挡并重新生成PPT。",
    ]
    source.write_text("\n".join([*facts, *instructions]), encoding="utf-8")
    ingest_sources(job.id, [str(source)])

    brief = create_brief(job.id)
    plan = create_deck_plan(job.id)

    visible_and_notes = "\n".join(
        piece for slide in plan.slides for piece in [slide.title, *slide.points, slide.speaker_notes]
    )
    assert all(fact in visible_and_notes for fact in facts)
    assert not any(instruction in visible_and_notes for instruction in instructions)
    assert not any(instruction in brief.must_include for instruction in instructions)
    editorial = json.loads((job_dir(job.id) / "planning/editorial-inputs.json").read_text(encoding="utf-8"))
    held = [item for item in editorial["items"] if item["input_channel"] == "unlabeled_presentation_instruction"]
    assert [item["text"] for item in held] == instructions
    assert all(item["status"] == "pending_editorial_review" for item in held)


def test_image_only_default_does_not_invent_numbered_findings(tmp_path: Path) -> None:
    from PIL import Image
    job = create_job("现场采样照片", job_id="image-without-invented-ordinal")
    sources = []
    for index, color in enumerate(("red", "blue")):
        path = tmp_path / f"source-{index}.png"
        Image.new("RGB", (300, 200), color).save(path)
        sources.append(str(path))
    ingest_sources(job.id, sources)
    brief = create_brief(job.id)
    brief.target_pages = 2
    write_json(job_dir(job.id) / "planning/brief.json", brief)
    plan = create_deck_plan(job.id)
    assert [slide.title for slide in plan.slides] == [job.name, job.name]
    assert all("核心发现" not in slide.title for slide in plan.slides)
    assert all(slide.asset_refs for slide in plan.slides)


def test_real_business_steps_and_words_are_not_treated_as_internal_instructions() -> None:
    facts = ["本项目分三步完成现场核查。", "第一步核对订单，第二步抽检，第三步确认交付。",
             "必须保留温度记录至少三年。", "客户要求在两周内完成20台设备交付。", "希望把现有库存优先供应已签约客户。"]
    content, held = _partition_source_lines("\n".join(facts))
    assert [line for line, _ in content] == facts
    assert held == []


def test_isolated_numeric_fragment_is_held_but_real_steps_and_units_remain_content() -> None:
    content, held = _partition_source_lines(
        "01\n2026\n第01步骤完成现场采样。\n24份原始记录已经归档。\n续约率达到93%。"
    )

    assert [line for line, _ in content] == [
        "第01步骤完成现场采样。",
        "24份原始记录已经归档。",
        "续约率达到93%。",
    ]
    assert [(item["text"], item["input_channel"]) for item in held] == [
        ("01", "isolated_numeric_fragment"),
        ("2026", "isolated_numeric_fragment"),
    ]


def test_explicit_internal_input_channels_keep_mixed_facts_in_editorial_evidence(tmp_path: Path) -> None:
    job = create_job("设备交付结果", job_id="editorial-source-channels")
    facts = ["本项目完成20台设备交付。", "第一步核对订单，第二步检验，第三步安排交付。", "必须保留检验记录三年。"]
    metadata = ["purpose: 先给听众一个路线图", "revision_notes: 助手提醒用户继续确认。",
                "style: 大数字搭配窄栏解释", "客户要求：本月完成20台设备交付，请把数据放大。"]
    source = tmp_path / "mixed-source.md"
    source.write_text("制作4页PPT\n" + "\n".join(metadata + facts), encoding="utf-8")
    ingest_sources(job.id, [str(source)])
    brief = create_brief(job.id)
    plan = create_deck_plan(job.id)
    assert brief.must_include == facts
    visible_and_notes = "\n".join(slide.title + "\n" + "\n".join(slide.points) + "\n" + slide.speaker_notes for slide in plan.slides)
    assert all(fact in visible_and_notes for fact in facts)
    assert not any(line in visible_and_notes for line in metadata)
    held = json.loads((job_dir(job.id) / "planning/editorial-inputs.json").read_text())
    assert held["status"] == "pending_editorial_review" and held["pending_count"] == 4
    pending = [item for item in held["items"] if item["status"] == "pending_editorial_review"]
    assert [item["text"] for item in pending] == metadata
    assert pending[-1]["locator"] == "line:5" and "#line:5" in pending[-1]["source_ref"]
    assert plan.planning_strategy["editorial_inputs"]["pending_count"] == 4


def test_explicit_metadata_sections_end_at_the_next_peer_heading() -> None:
    text = "## 制作说明\n先给听众一个路线图。\n本月交付20台设备，请把20做大。\n## 正文\n本项目已完成20台设备交付。"
    content, held = _partition_source_lines(text)
    assert [item["text"] for item in held] == text.splitlines()[:3]
    assert all(item["status"] == "pending_editorial_review" for item in held)
    assert content[-1] == ("本项目已完成20台设备交付。", "line:5")


def test_plain_editorial_label_holds_its_paragraph_until_blank_line() -> None:
    text = "制作说明：\n先给听众一个路线图。\n\n本项目已完成20台设备交付。"
    content, held = _partition_source_lines(text)
    assert [item["text"] for item in held] == text.splitlines()[:2]
    assert content == [("本项目已完成20台设备交付。", "line:4")]
