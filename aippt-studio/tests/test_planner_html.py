from __future__ import annotations

from pathlib import Path

import pytest

from aippt_studio.html_deck import CSS, _asset_uri, render_slide, write_deck_html
from aippt_studio.assets.acquisition import AcquiredAsset, AssetLedger
from aippt_studio.models import (
    SlideSpec,
    DeckPlan,
    SourceAsset,
    SourceDeckManifest,
    SourceInspectionIndex,
    SourceRecord,
)
from aippt_studio.planner import _image_asset_refs, _title_from_line, create_brief, create_deck_plan
from aippt_studio.storage import write_json
from aippt_studio.storage import job_dir
from aippt_studio.workflow import create_job
from aippt_studio.ingest import ingest_sources


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("上半年收入增长 28%，企业客户贡献 72%。后续内容", "上半年收入增长 28%，企业客户贡献 72%"),
        ("交付周期由 21 天缩短至 14 天，效率提升 33%，同时完成模板标准化和质量复盘", "交付周期由 21 天缩短至 14 天，效率提升 33%"),
        ("统一指标口径并打通业务结果与技术投入", "统一指标口径并打通业务结果与技术投入"),
    ],
)
def test_title_from_line_creates_complete_takeaway_without_ellipsis(line: str, expected: str) -> None:
    title = _title_from_line(line, "核心发现")

    assert title == expected
    assert "…" not in title


def test_extracted_images_join_asset_pool_and_resolve_to_file_uri(tmp_path: Path) -> None:
    asset_path = tmp_path / "extraction" / "source-pptx" / "assets" / "asset-001.png"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(b"png")
    manifest_path = tmp_path / "extraction" / "source-pptx" / "source-deck-manifest.json"
    manifest = SourceDeckManifest(
        source_id="source-pptx",
        original="source.pptx",
        stored_path="sources/source.pptx",
        source_sha256="abc",
        size_bytes=3,
        slide_count=1,
        engine="OfficeCLI",
        engine_version="test",
        assets=[
            SourceAsset(
                id="asset-001",
                source_id="source-pptx",
                content_type="image/png",
                stored_path="extraction/source-pptx/assets/asset-001.png",
                extraction_status="extracted",
            )
        ],
    )
    write_json(manifest_path, manifest)
    write_json(
        tmp_path / "extraction" / "index.json",
        SourceInspectionIndex(
            job_id="test",
            manifests=["extraction/source-pptx/source-deck-manifest.json"],
        ),
    )
    record = SourceRecord(
        id="source-pptx",
        original="source.pptx",
        stored_path="sources/source.pptx",
        kind="powerpoint",
        sha256="abc",
        size_bytes=3,
    )

    refs = _image_asset_refs(tmp_path, [record])

    assert refs == ["extracted:source-pptx:asset-001"]
    assert _asset_uri(tmp_path, refs[0]) == asset_path.resolve().as_uri()


def test_acquired_web_asset_resolves_from_asset_ledger(tmp_path: Path) -> None:
    asset_path = tmp_path / "assets" / "acquired" / "asset-web.png"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_bytes(b"png")
    write_json(
        tmp_path / "assets" / "asset-ledger.json",
        AssetLedger(
            job_id="test",
            assets=[
                AcquiredAsset(
                    id="asset-web",
                    need_id="need-web",
                    slide_id="slide-03",
                    kind="icon",
                    query="growth icon",
                    purpose="support conclusion",
                    source_url="https://example.test/license",
                    download_url="https://example.test/icon.png",
                    license="MIT",
                    stored_path="assets/acquired/asset-web.png",
                    sha256="a" * 64,
                    size_bytes=3,
                    media_kind="image",
                )
            ],
        ),
    )

    assert _asset_uri(tmp_path, "acquired:asset-web") == asset_path.resolve().as_uri()

    slide = SlideSpec(
        id="slide-03",
        title="增长结构进一步优化",
        purpose="展示结论",
        layout="content",
        points=["企业客户贡献提升", "续约率保持稳定"],
        asset_refs=["acquired:asset-web"],
    )
    rendered = render_slide(tmp_path, slide, 3, 10)
    assert 'class="icon-layout"' in rendered
    assert 'class="icon-visual"' in rendered


@pytest.mark.parametrize(
    ("points", "expected_class"),
    [
        (["续约率达到 93%"], "statement-focus"),
        (["统一指标口径并建立责任闭环"], "statement-focus"),
        (["先统一模板标准", "再建设质量看板"], "sequence-list"),
        (["确认优先级", "锁定里程碑", "持续量化复盘"], "fact-list"),
    ],
)
def test_render_slide_selects_layout_from_content(points: list[str], expected_class: str, tmp_path: Path) -> None:
    slide = SlideSpec(
        id="slide-03",
        title="核心结论",
        purpose="说明结论",
        layout="content",
        points=points,
    )

    rendered = render_slide(tmp_path, slide, 3, 10)

    assert f'class="{expected_class}"' in rendered
    assert all(point in rendered for point in points)
    assert "…" not in rendered


def test_default_removes_decorative_microcopy_and_preserves_every_cover_point(tmp_path: Path) -> None:
    slide = SlideSpec(id="slide-01", title="项目研究结果", purpose="internal planning note", layout="cover",
                      points=["调查覆盖三个地区", "访谈记录全部留存", "下一阶段开展复核"], evidence_refs=["source-id#line:18"])
    rendered = render_slide(tmp_path, slide, 1, 10, deck_title="Repeated brand")
    assert all(point in rendered for point in slide.points)
    assert all(text not in rendered for text in ("internal planning note", "Repeated brand", "source-id", "来源：客户材料", "NEXT STEP", "STRUCTURE", "KEY EVIDENCE"))
    assert "overflow:hidden" not in CSS.replace(" ", "")
    assert "very-long-title" not in CSS
    assert "font-size:var(--page-body-size,48px)" in CSS


def test_two_points_use_comparison_only_when_content_requests_comparison(tmp_path: Path) -> None:
    slide = SlideSpec(id="slide-02", title="两种交付方案对比", purpose="比较成本与时间", layout="content",
                      points=["集中交付便于统一校验", "分批交付便于及早反馈"])
    assert 'class="dual"' in render_slide(tmp_path, slide, 2, 4)


def test_long_text_is_never_truncated_or_shrunk_by_html(tmp_path: Path) -> None:
    long_fact = "每个原始事实必须保留并在空间不足时返回需要拆页的结果。" * 30
    slide = SlideSpec(id="slide-02", title="完整标题" * 20, purpose="完整证据", layout="content", points=[long_fact])
    rendered = render_slide(tmp_path, slide, 2, 4)
    assert long_fact in rendered and slide.title in rendered
    assert "long-title" not in rendered


def test_a_year_is_not_automatically_promoted_to_a_metric(tmp_path: Path) -> None:
    slide = SlideSpec(id="slide-02", title="研究背景", purpose="说明时间", layout="content", points=["2025年团队开始整理访谈记录"])
    assert 'class="statement-focus"' in render_slide(tmp_path, slide, 2, 4)


@pytest.mark.parametrize("fact", ["续约率达到93%，对应已完成签约的客户。", "交付周期由21天缩短至14天。", "本项目已完成20个业务环节的核查。"])
def test_number_stays_with_its_complete_explanation_without_automatic_giant_metric(tmp_path: Path, fact: str) -> None:
    slide = SlideSpec(id="slide-01", title="阶段结果", purpose="突出数据", layout="evidence", points=[fact])
    rendered = render_slide(tmp_path, slide, 1, 1)
    assert rendered.count(fact) == 1
    assert 'class="statement-focus"' in rendered
    assert "metric" not in rendered and ".metric" not in CSS


def test_internal_plan_fields_neither_appear_nor_create_a_false_content_comparison(tmp_path: Path) -> None:
    slide = SlideSpec(id="slide-01", title="业务进展", purpose="制作提醒：用两种方案对比，先给听众一个路线图",
                      layout="content", points=["本项目已完成现场验收。", "客户要求在两周内完成下一批交付。"],
                      revision_notes=["请重新排版并提醒用户确认"], speaker_notes="现场验收已完成，下一批交付正在推进。")
    rendered = render_slide(tmp_path, slide, 1, 1)
    assert all(point in rendered for point in slide.points)
    assert slide.purpose not in rendered and slide.revision_notes[0] not in rendered
    assert 'class="fact-list"' in rendered and 'class="dual"' not in rendered


def test_title_equal_to_a_source_point_is_not_printed_again_as_empty_body_copy(tmp_path: Path) -> None:
    fact = "现场验收已完成，关键记录可以复核。"
    support = "抽检结果将与客户反馈共同进入下一轮复核。"
    slide = SlideSpec(
        id="slide-01",
        title=fact,
        purpose="来源事实的页面表达",
        layout="content",
        points=[fact, support],
    )

    rendered = render_slide(tmp_path, slide, 1, 1)

    assert rendered.count(fact) == 1
    assert support in rendered
    # The remaining supporting fact stays at normal body scale instead of
    # becoming a second isolated headline-sized statement.
    assert 'class="fact-list"' in rendered
    assert 'class="statement-focus"' not in rendered


def test_plan_model_has_no_generic_unverified_visual_direction() -> None:
    plan = DeckPlan(title="没有视觉材料", slides=[
        SlideSpec(id="slide-01", title="真实主题", purpose="说明", layout="content", points=["来源事实"])
    ])

    assert plan.visual_direction == ""


def test_font_family_uses_css_hex_escapes_without_control_characters(tmp_path: Path) -> None:
    job = create_job("字体转义", job_id="css-font-escape")
    plan = DeckPlan(title="字体转义", subtitle="", font_family="字体<测试>", slides=[
        SlideSpec(id="slide-01", title="正确字体", purpose="验证转义", layout="content", points=["正文保留完整内容。"])
    ])
    write_json(job_dir(job.id) / "planning/deck-plan.json", plan)
    output = write_deck_html(job.id).read_text()
    assert r"\3c " in output and r"\3e " in output
    assert "\x03" not in output


@pytest.mark.parametrize("page_count", [1, 2])
def test_new_composition_expression_plan_reaches_single_and_two_page_html_without_filler(
    tmp_path: Path, page_count: int
) -> None:
    job = create_job(f"{page_count}页表达链路", job_id=f"expression-integration-{page_count}")
    facts = [
        f"第{index}项现场核验工作已经按计划全部完成，完整记录已交由项目责任团队复核并归档保存。"
        for index in range(1, page_count * 2 + 1)
    ]
    source = tmp_path / f"expression-{page_count}.md"
    source.write_text(f"制作{page_count}页PPT\n" + "\n".join(facts), encoding="utf-8")
    ingest_sources(job.id, [str(source)])

    create_brief(job.id)
    plan = create_deck_plan(job.id)
    persisted = DeckPlan.model_validate_json((job_dir(job.id) / "planning/deck-plan.json").read_text(encoding="utf-8"))
    expression_plan = persisted.planning_strategy["expression_plan"]

    assert len(plan.slides) == len(persisted.slides) == page_count
    assert expression_plan["deck_scope"] == ("single_page" if page_count == 1 else "two_page")
    assert expression_plan["application"] == "baseline_new_composition"
    assert all(expression_plan["slides"][slide.id]["pattern"] == "editorial_columns" for slide in plan.slides)
    reference_packet = persisted.planning_strategy["portfolio_expression_examples"]
    assert reference_packet["path"] == "planning/portfolio-expression-examples.json"
    assert (job_dir(job.id) / reference_packet["path"]).is_file()

    markup = write_deck_html(job.id).read_text(encoding="utf-8")

    assert 'data-composition-baseline="new_composition"' in markup
    assert markup.count('data-slide-id="') == page_count
    assert markup.count('data-expression-pattern="editorial_columns"') == page_count
    assert markup.count('class="editorial-columns"') == page_count
    assert all(fact in markup for fact in facts)
    assert not any(slide.layout in {"cover", "summary", "conclusion"} for slide in persisted.slides)
    prototype_markup = write_deck_html(job.id, prototype=True).read_text(encoding="utf-8")
    assert 'data-composition-baseline="new_composition"' not in prototype_markup
