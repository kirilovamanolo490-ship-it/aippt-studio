from __future__ import annotations

from pathlib import Path

from aippt_studio.expression_patterns import select_expression_plan
from aippt_studio.html_deck import CSS, render_slide
from aippt_studio.models import PresentationForm, SlideSpec, SourceRecord
from aippt_studio.storage import write_json
from aippt_studio.visual_system import css_variable_block


def test_explicit_cinematic_direction_selects_one_frame_only_with_a_source_visual() -> None:
    with_image = SlideSpec(id="scene", title="实际场景", purpose="展示实景", layout="content",
                           points=["保留一条来源主张"], asset_refs=["source-photo"])
    without_image = SlideSpec(id="plain", title="数据结论", purpose="证据", layout="content",
                              points=["保留来源数据"])
    plan = select_expression_plan([with_image, without_image], PresentationForm.COMPETITION_DEFENSE,
                                  style_text="希望有电影感，依据真实照片表达")
    assert plan["slides"]["scene"]["pattern"] == "cinematic_still"
    assert plan["slides"]["plain"]["pattern"] != "cinematic_still"
    assert select_expression_plan([with_image], PresentationForm.COMPETITION_DEFENSE,
                                  style_text="不要电影感")["slides"]["scene"]["pattern"] != "cinematic_still"


def test_cinematic_direction_keeps_one_hero_and_varies_subsequent_image_pages() -> None:
    slides = [_slide(number, f"场景{number}", [f"事实{number}"], assets=[f"photo-{number}"])
              for number in range(1, 4)]
    plan = select_expression_plan(slides, PresentationForm.CEREMONIAL_STORY,
                                  style_text="希望有电影感的开场和后续证据页")
    assert [plan["slides"][slide.id]["pattern"] for slide in slides] == [
        "cinematic_still", "image_right", "image_left"]
    assert plan["cinematic_hero_count"] == 1
    assert plan["cinematic_visual_gap"] is False


def test_cinematic_hero_waits_for_a_resolved_non_icon_visual() -> None:
    slides = [
        _slide(1, "图标", ["先看主题"], assets=["icon-only"]),
        _slide(2, "现场", ["再看证据"], assets=["source-photo"]),
    ]
    plan = select_expression_plan(slides, PresentationForm.CEREMONIAL_STORY,
                                  style_text="电影感",
                                  cinematic_eligible_refs={"source-photo"})
    assert plan["slides"]["slide-01"]["pattern"] == "image_right"
    assert plan["slides"]["slide-02"]["pattern"] == "cinematic_still"
    assert plan["cinematic_hero_count"] == 1
    missing = select_expression_plan(slides[:1], PresentationForm.CEREMONIAL_STORY,
                                     style_text="电影感", cinematic_eligible_refs=set())
    assert missing["cinematic_visual_gap"] is True


def test_cinematic_frame_uses_one_uncropped_source_image_and_audience_copy(tmp_path: Path) -> None:
    photo = tmp_path / "sources" / "photo.png"
    photo.parent.mkdir()
    photo.write_bytes(b"png")
    write_json(tmp_path / "normalized" / "sources" / "photo.json",
               SourceRecord(id="photo", original="photo.png", stored_path="sources/photo.png",
                            kind="image", sha256="a" * 64, size_bytes=3))
    slide = SlideSpec(id="scene", title="产业场景已经形成", purpose="内部设计说明", layout="content",
                      points=["现场设施已投入使用"], asset_refs=["photo"])
    markup = render_slide(tmp_path, slide, 1, 1, expression_pattern="cinematic_still")
    assert 'class="cinematic-composition"' in markup
    assert 'cinematic-still' in markup
    assert "产业场景已经形成" in markup and "现场设施已投入使用" in markup
    assert "内部设计说明" not in markup
    assert ".cinematic-composition .image-visual img{width:100%;height:100%;object-fit:contain" in CSS
    tokens = css_variable_block({})
    assert "--vs-cover-ink:#ffffff" in tokens
    assert "--vs-accent-soft:#eff2f3" in tokens
    assert "--vs-cover_ink" not in tokens and "--vs-accent_soft" not in tokens


def test_verified_portfolio_cover_recipe_changes_html_relationship_without_copying_case(tmp_path: Path) -> None:
    photo = tmp_path / "sources" / "photo.png"
    photo.parent.mkdir()
    photo.write_bytes(b"png")
    write_json(tmp_path / "normalized" / "sources" / "photo.json",
               SourceRecord(id="photo", original="photo.png", stored_path="sources/photo.png",
                            kind="image", sha256="a" * 64, size_bytes=3))
    slide = SlideSpec(id="cover", title="当前项目的真实主张", purpose="内部设计说明", layout="cover",
                      points=["当前赛事与团队信息"], asset_refs=["photo"])
    markup = render_slide(tmp_path, slide, 1, 1, presentation_form=PresentationForm.COMPETITION_DEFENSE,
                          expression_pattern="cover", composition_recipe="competition_photo_cover",
                          composition_source="competition-rural-cover")
    assert 'data-composition-recipe="competition_photo_cover"' in markup
    assert 'data-composition-source="competition-rural-cover"' in markup
    assert 'class="portfolio-cover-copy"' in markup
    assert 'class="portfolio-cover-art"' in markup
    assert markup.count("当前项目的真实主张") == 1
    assert markup.count("当前赛事与团队信息") == 1
    assert "内部设计说明" not in markup
    assert ".composition-competition-photo-cover .content" in CSS
    fallback = render_slide(tmp_path, slide, 1, 1, presentation_form=PresentationForm.GOVERNMENT_BRIEFING,
                            expression_pattern="cover", composition_recipe="competition_photo_cover")
    assert 'data-composition-recipe=""' in fallback


def _slide(number: int, title: str, points: list[str], *, assets: list[str] | None = None,
           layout: str = "content") -> SlideSpec:
    return SlideSpec(id=f"slide-{number:02d}", title=title, purpose="来源事实", layout=layout,
                     points=points, asset_refs=assets or [])


def test_single_and_two_page_expression_use_existing_content_without_filler() -> None:
    one = _slide(1, "核验结果", ["抽检记录已归档"])
    single = select_expression_plan([one], PresentationForm.BUSINESS_REPORT)
    assert single["deck_scope"] == "single_page"
    assert list(single["slides"]) == ["slide-01"]
    assert single["slides"][one.id]["pattern"] == "statement"
    assert single["slides"][one.id]["visible_source_points"] == 1

    two = _slide(2, "两项发现", ["现场记录完整", "复核责任已落实"])
    pair = select_expression_plan([one, two], PresentationForm.BUSINESS_REPORT)
    assert pair["deck_scope"] == "two_page"
    assert pair["slides"][two.id]["pattern"] == "editorial_columns"


def test_comparison_and_sequence_require_visible_source_relationship() -> None:
    ordinary = _slide(1, "工作进展", ["现场记录完整", "相关资料已归档"])
    comparison = _slide(2, "两种方案对比", ["集中完成便于统一复核", "分批完成便于及时反馈"])
    sequence = _slide(3, "实施流程", ["先完成核验", "再交付审查"])
    plan = select_expression_plan([ordinary, comparison, sequence], PresentationForm.GENERAL)
    assert [plan["slides"][slide.id]["pattern"] for slide in (ordinary, comparison, sequence)] == [
        "editorial_columns", "comparison", "sequence"]
    for slide in (ordinary, comparison, sequence):
        markup = render_slide(Path("."), slide, 1, 3,
                              expression_pattern=plan["slides"][slide.id]["pattern"])
        assert all(point in markup for point in slide.points)
    assert 'class="editorial-columns"' in render_slide(Path("."), ordinary, 1, 3,
                                                        expression_pattern="editorial_columns")
    assert 'class="dual"' in render_slide(Path("."), comparison, 2, 3,
                                          expression_pattern="comparison")
    assert 'class="sequence-list"' in render_slide(Path("."), sequence, 3, 3,
                                                   expression_pattern="sequence")


def test_duplicate_title_point_does_not_become_a_second_statement() -> None:
    title = "现场验收已完成"
    slide = _slide(1, title, [title, "抽检记录已归档"])
    choice = select_expression_plan([slide], PresentationForm.GENERAL)["slides"][slide.id]
    assert choice["pattern"] == "structured_rows"
    markup = render_slide(Path("."), slide, 1, 1, expression_pattern=choice["pattern"])
    assert markup.count(title) == 1
    assert 'class="statement-focus"' not in markup


def test_consecutive_source_images_alternate_page_sides_without_faking_a_mask() -> None:
    slides = [_slide(number, f"资料图{number}", [f"图{number}说明"], assets=[f"asset-{number}"])
              for number in range(1, 4)]
    plan = select_expression_plan(slides, PresentationForm.COMPETITION_DEFENSE)
    assert [plan["slides"][slide.id]["pattern"] for slide in slides] == [
        "image_right", "image_left", "image_right"]
    assert ".image-layout.image-left" in CSS
    assert "border-radius:0" in CSS
