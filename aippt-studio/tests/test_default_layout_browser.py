"""Actual Chromium and PPTX export regressions, independent of fake rendering."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from aippt_studio.html_deck import CSS, render_slide
from aippt_studio.models import PresentationForm, SlideSpec
from aippt_studio.models import SourceRecord
from aippt_studio.storage import write_json


PROJECT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE or not (PROJECT / "node_modules/puppeteer").exists(), reason="Local browser renderer dependencies are unavailable")


def _render(tmp_path: Path, fragment: str, *, audit_only: bool = False, baseline: bool = False) -> tuple[subprocess.CompletedProcess, dict, Path]:
    source = tmp_path / "deck.html"
    marker = ' data-composition-baseline="new_composition"' if baseline else ""
    source.write_text('<!doctype html><html><meta charset="utf-8"><style>' + CSS + '</style><body' + marker + '>' + fragment + '</body></html>')
    output = tmp_path / "candidate.pptx"
    command = [NODE, str(PROJECT / "node/render.mjs"), str(source), str(output), str(tmp_path / "previews")]
    if audit_only:
        command.append("--audit-only")
    result = subprocess.run(command, capture_output=True, text=True, timeout=90, cwd=PROJECT)
    if "Chromium is unavailable" in result.stderr:
        pytest.skip("Configured local Chromium is unavailable")
    report_path = tmp_path / "previews/layout-review.json"
    assert report_path.exists(), result.stderr or result.stdout
    return result, json.loads(report_path.read_text()), output


def _slides(*layouts: str) -> str:
    return "".join(render_slide(Path("."), SlideSpec(
        id=f"slide-{index:02d}", title=f"事实主题 {index}", purpose="说明", layout=layout,
        points=[f"第{index}页的来源事实内容。", "另一条具体事实。"]), index, len(layouts))
        for index, layout in enumerate(layouts, 1))


def test_repeated_measured_layout_is_advisory_only_for_three_consecutive_new_composition_slides(tmp_path: Path) -> None:
    result, report, _ = _render(tmp_path, _slides("content", "content", "content"), audit_only=True, baseline=True)
    assert result.returncode == 0
    assert report["status"] == "passed"
    assert report["layout_repetition"]["runs"] == [["slide-01", "slide-02", "slide-03"]]
    item = next(item for item in report["advisories"] if item["kind"] == "repeated_layout_skeleton")
    assert item["severity"] == "advisory" and item["slide_ids"] == ["slide-01", "slide-02", "slide-03"]
    assert all(slide["advisory_count"] >= 1 for slide in report["slides"])


def test_different_plan_labels_with_same_rendered_skeleton_are_reported(tmp_path: Path) -> None:
    result, report, _ = _render(tmp_path, _slides("content", "evidence", "content"), audit_only=True, baseline=True)
    assert result.returncode == 0
    assert report["layout_repetition"]["runs"] == [["slide-01", "slide-02", "slide-03"]]


@pytest.mark.parametrize("layouts,baseline", [
    (("content", "content"), True),
    (("content", "content", "content"), False),
    (("content",), True),
])
def test_repeated_layout_does_not_flag_short_different_or_unmarked_sources(
    tmp_path: Path, layouts: tuple[str, ...], baseline: bool
) -> None:
    _, report, _ = _render(tmp_path, _slides(*layouts), audit_only=True, baseline=baseline)
    assert not report["layout_repetition"]["runs"]
    assert not any(item["kind"] == "repeated_layout_skeleton" for item in report["advisories"])


def test_same_layout_label_with_different_rendered_body_does_not_repeat(tmp_path: Path) -> None:
    slides = [SlideSpec(id=f"slide-{index:02d}", title=f"事实 {index}", purpose="说明",
                        layout="content", points=["当前来源事实。", "另一条来源事实。"] if index != 3 else ["单一结论。"])
              for index in range(1, 4)]
    fragments = "".join(render_slide(tmp_path, slide, index, 3) for index, slide in enumerate(slides, 1))
    result, report, _ = _render(tmp_path, fragments, audit_only=True, baseline=True)
    assert result.returncode == 0
    assert report["status"] == "passed"
    assert report["layout_repetition"]["runs"] == []


def test_real_export_maps_css_48px_to_pptx_24pt(tmp_path: Path) -> None:
    slide = SlideSpec(id="slide-01", title="明确的正文阅读尺度", purpose="正文清晰", layout="content",
                      points=["保留每条来源事实。", "图文区域保持明确分隔。"])
    result, report, output = _render(tmp_path, render_slide(tmp_path, slide, 1, 1))
    assert result.returncode == 0, result.stderr or result.stdout
    assert report["status"] == "passed" and report["visual_reviewed"] is False
    assert report["slides"][0]["points_per_css_px"] == 0.5
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main", "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
    with ZipFile(output) as package:
        presentation = ElementTree.fromstring(package.read("ppt/presentation.xml"))
        assert presentation.find("p:sldSz", ns).attrib == {"cx": "12192000", "cy": "6858000"}
        page = ElementTree.fromstring(package.read("ppt/slides/slide1.xml"))
        runs = {''.join(run.itertext()): run.find("a:rPr", ns).get("sz") for run in page.findall(".//a:r", ns)}
        for point in slide.points:
            assert runs[point] == "2400"


def test_browser_blocks_clipping_and_overlap_but_treats_small_font_as_advisory(tmp_path: Path) -> None:
    old_output = tmp_path / "candidate.pptx"
    old_output.write_bytes(b"previous-reviewed-candidate")
    fragment = '''<section class="slide" data-slide-id="slide-01">
      <p style="position:absolute;left:100px;top:100px;font-size:12px">Small text receives an advisory</p>
      <div style="position:absolute;left:100px;top:220px;width:800px;font-size:48px;height:20px;overflow:hidden">Clipped text must be rejected</div>
      <img src="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='300' height='300'%3E%3Crect width='300' height='300' fill='red'/%3E%3C/svg%3E" style="position:absolute;left:100px;top:400px;width:300px;height:300px">
      <p style="position:absolute;left:120px;top:450px;font-size:48px">Obscured text</p>
    </section>'''
    result, report, output = _render(tmp_path, fragment)
    assert result.returncode == 3
    assert {"text_clipped_by_container", "text_image_overlap"}.issubset({issue["kind"] for issue in report["issues"]})
    assert not any(issue["kind"].startswith("font_") for issue in report["issues"])
    assert any(item["kind"] == "font_below_reference" for item in report["advisories"])
    assert output.read_bytes() == b"previous-reviewed-candidate"
    assert (tmp_path / "previews/needs-review/slide-01.png").is_file()


def test_overfull_source_is_preserved_and_requires_reflow_instead_of_shrinking(tmp_path: Path) -> None:
    fact = "所有事实均应保留，过长正文应拆分页面或重新排版。" * 70
    slide = SlideSpec(id="slide-01", title="正文不能靠截断通过检查", purpose="保留完整材料", layout="content", points=[fact])
    fragment = render_slide(tmp_path, slide, 1, 1)
    result, report, output = _render(tmp_path, fragment)
    assert fact in (tmp_path / "deck.html").read_text()
    assert result.returncode == 3 and report["status"] == "needs_review"
    assert any(issue["kind"] in {"text_outside_slide", "text_exceeds_content_region", "layout_zone_overlap"} for issue in report["issues"])
    assert not output.exists()
    assert all(item["font_pt"] >= item["reference_pt"] for item in report["slides"][0]["measured_fonts"])


def test_smaller_typography_exports_with_measurements_and_no_aesthetic_approval(tmp_path: Path) -> None:
    fragment = '''<section class="slide" data-slide-id="slide-01">
      <h2 style="position:absolute;left:120px;top:100px;font-size:52px">阶段交付进展</h2>
      <p style="position:absolute;left:120px;top:260px;font-size:40px">本月已交付20台设备，全部完成现场验收。</p>
    </section>'''
    result, report, output = _render(tmp_path, fragment)
    assert result.returncode == 0 and output.is_file(), result.stderr or result.stdout
    assert report["status"] == "passed" and report["issues"] == []
    assert report["typography_mode"] == "advisory"
    assert report["visual_reviewed"] is False and report["expression_reviewed"] is False
    assert {item["font_pt"] for item in report["slides"][0]["measured_fonts"]} == {20, 26}
    assert {item["kind"] for item in report["advisories"]} == {"font_below_reference"}


def test_portfolio_form_does_not_make_core_source_copy_smaller_than_the_general_form(tmp_path: Path) -> None:
    point = "真实项目的材料、责任范围和可复核结果需要以正常阅读关系呈现。"
    support = "相邻证据说明该结果不是装饰性标签，而是本页论点的一部分。"
    slide = SlideSpec(
        id="slide-01",
        title="真实项目的核心结果",
        purpose="测试表达层级",
        layout="content",
        points=[point, support],
    )
    general_dir, portfolio_dir = tmp_path / "general", tmp_path / "portfolio"
    general_dir.mkdir()
    portfolio_dir.mkdir()
    general_result, general_report, _ = _render(general_dir, render_slide(tmp_path, slide, 1, 1), audit_only=True)
    portfolio_result, portfolio_report, _ = _render(
        portfolio_dir,
        render_slide(tmp_path, slide, 1, 1, presentation_form=PresentationForm.PREMIUM_BRAND_PORTFOLIO),
        audit_only=True,
    )

    assert general_result.returncode == 0 and portfolio_result.returncode == 0
    general_font = next(item["font_pt"] for item in general_report["slides"][0]["measured_fonts"] if item["text"] == point)
    portfolio_font = next(item["font_pt"] for item in portfolio_report["slides"][0]["measured_fonts"] if item["text"] == point)
    assert portfolio_font >= general_font


def test_large_number_next_to_narrow_explanation_is_a_whole_slide_review_advisory(tmp_path: Path) -> None:
    explanation = "本月交付的设备已完成现场检验和安装，下一阶段将按照客户订单逐批安排后续交付。" * 2
    fragment = f'''<section class="slide" data-slide-id="slide-01">
      <p data-text-role="body" style="position:absolute;left:120px;top:200px;font-size:160px">20</p>
      <p data-text-role="body" style="position:absolute;left:900px;top:150px;width:300px;font-size:32px;line-height:1.4">{explanation}</p>
    </section>'''
    result, report, _ = _render(tmp_path, fragment, audit_only=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert report["issues"] == []
    assert {"body_scale_contrast", "narrow_dense_text"}.issubset({item["kind"] for item in report["advisories"]})


def test_real_browser_accepts_separate_image_and_body_regions(tmp_path: Path) -> None:
    svg = tmp_path / "diagram.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="600" height="400"><rect width="600" height="400" fill="#e5efea"/><circle cx="300" cy="200" r="120" fill="#367f70"/></svg>')
    write_json(tmp_path / "normalized/sources/qa-image.json", SourceRecord(
        id="qa-image", original="diagram.svg", stored_path="diagram.svg", kind="image", sha256="fixture", size_bytes=svg.stat().st_size))
    slide = SlideSpec(id="slide-01", title="图像与正文各有独立区域", purpose="图文分区", layout="evidence",
                      points=["测试图像完整显示。", "两条正文与图像保持清晰间距。"], asset_refs=["qa-image"])
    result, report, _ = _render(tmp_path, render_slide(tmp_path, slide, 1, 1), audit_only=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert report["status"] == "passed" and report["slides"][0]["image_count"] == 1


def test_portfolio_cover_recipe_has_distinct_copy_and_visual_regions_in_browser(tmp_path: Path) -> None:
    svg = tmp_path / "field.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="600"><rect width="800" height="600" fill="#54785b"/><circle cx="560" cy="300" r="145" fill="#c9dd8c"/></svg>')
    write_json(tmp_path / "normalized/sources/field.json", SourceRecord(
        id="field", original="field.svg", stored_path="field.svg", kind="image",
        sha256="fixture", size_bytes=svg.stat().st_size))
    slide = SlideSpec(id="slide-01", title="乡村产业形成新路径", purpose="内部设计说明",
                      layout="cover", points=["赛事名称与团队信息"], asset_refs=["field"])
    fragment = render_slide(tmp_path, slide, 1, 1, presentation_form=PresentationForm.COMPETITION_DEFENSE,
                            expression_pattern="cover", composition_recipe="competition_photo_cover",
                            composition_source="competition-rural-cover")
    result, report, output = _render(tmp_path, fragment)
    assert result.returncode == 0, result.stderr or result.stdout
    assert report["status"] == "passed" and report["issues"] == []
    assert report["slides"][0]["image_count"] == 1
    assert report["visual_reviewed"] is False and report["expression_reviewed"] is False
    assert output.is_file()
