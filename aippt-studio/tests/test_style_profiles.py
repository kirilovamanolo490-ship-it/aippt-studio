import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest

from aippt_studio.models import DeckPlan, PresentationForm, SlideSpec
from aippt_studio.native_scene import apply_native_style_roles, native_authoring_settings
from aippt_studio.style_profiles import profile_font_families, style_profile, style_profile_css


PROJECT = Path(__file__).resolve().parents[1]


def test_each_presentation_form_has_concrete_distinct_style_tokens():
    profiles = {form: style_profile(form) for form in PresentationForm}
    assert len({profile["profile_id"] for profile in profiles.values()}) == len(PresentationForm)
    for profile in profiles.values():
        assert profile["page_inset_x"] > 0
        assert profile["page_inset_y"] > 0
        assert profile["title_px"] > profile["body_px"] > profile["caption_px"]
        assert profile["body_line_height"] > 1
        assert len(profile["image_text_ratio"]) == 2
        assert profile["image_fit"] == "contain"
        assert profile["typography"]["heading"]["darwin"]
        assert profile["typography"]["body"]["win32"]


def test_child_audience_gets_its_own_teaching_profile_only_when_explicit():
    ordinary = style_profile(PresentationForm.TEACHING)
    children = style_profile(PresentationForm.TEACHING, "小学四年级学生")
    assert ordinary["profile_id"] == "teaching-learning-v1"
    assert children["profile_id"] == "children-learning-v1"
    assert children["body_px"] > ordinary["body_px"]
    assert children["density"] != ordinary["density"]


def test_child_audience_alone_needs_an_educational_purpose_and_does_not_override_ceremony():
    assert style_profile(PresentationForm.GENERAL, "儿童", "儿童节开幕典礼")["profile_id"] == "general-editorial-v1"
    assert style_profile(PresentationForm.GENERAL, "儿童", "儿童科普课程")["profile_id"] == "children-learning-v1"
    assert style_profile(PresentationForm.CEREMONIAL_STORY, "儿童", "儿童节典礼")["profile_id"] == "ceremonial-narrative-v1"


def test_style_css_emits_valid_grid_fr_units_and_relative_typography_tokens():
    css = style_profile_css(style_profile(PresentationForm.COMPETITION_DEFENSE))
    assert "--page-title-size:68px" in css
    assert "--page-body-leading:1.38" in css
    assert "--page-caption-size:28px" in css
    assert "--page-statement-size:64px" in css
    assert "--page-image-left:0.92fr" in css
    assert "--page-image-right:1.08fr" in css
    assert '--page-heading-font:"Heiti SC","Avenir Next","Hiragino Sans GB"' in css
    assert '--page-body-font:"Hiragino Sans GB","Heiti SC"' in css
    assert profile_font_families(style_profile(PresentationForm.GOVERNMENT_BRIEFING), "win32")["body"] == [
        "Microsoft YaHei UI", "Microsoft YaHei", "Arial"
    ]
    assert profile_font_families(style_profile(PresentationForm.GOVERNMENT_BRIEFING), "win32")["label"][0] == "Microsoft YaHei UI"


def test_native_authoring_defaults_scale_with_page_and_keep_scene_overrides():
    profile = style_profile(PresentationForm.COMPETITION_DEFENSE)
    profile["application"] = "baseline_new_composition"
    plan = DeckPlan(title="test", slides=[SlideSpec(id="slide-01", title="Title", purpose="Test", layout="content")],
                    planning_strategy={"visual_style_profile": profile})
    standard = native_authoring_settings({}, plan)
    narrow = native_authoring_settings({"page_size": {"width": 8.0, "height": 7.5}}, plan)
    custom_override = native_authoring_settings({"authoring_profile": {"body_font_pt": 19}}, plan)
    assert standard["title_font_pt"] == 34
    assert standard["statement_font_pt"] == 32
    assert narrow["title_font_pt"] == 20.4
    assert standard["heading_font_face"] == "Heiti SC"
    assert standard["body_font_face"] == "Hiragino Sans GB"
    assert custom_override["body_font_pt"] == 19
    explicit_scene = native_authoring_settings({"font_family": "User Chosen Face"}, plan)
    assert explicit_scene["heading_font_face"] == "User Chosen Face"
    plan.font_family = "User Chosen Plan Face"
    explicit_plan = native_authoring_settings({}, plan)
    assert explicit_plan["body_font_face"] == "User Chosen Plan Face"

    profile["application"] = "advisory_only"
    plan.planning_strategy["visual_style_profile"] = profile
    assert native_authoring_settings({}, plan) == {}
    untagged_scene = {"slides": [{"id": "slide-01", "elements": [{"type": "text", "text": "Title"}]}]}
    assert apply_native_style_roles(untagged_scene, plan)["slides"][0]["elements"][0].get("style_role") is None


@pytest.mark.skipif(
    not shutil.which("node") or not (PROJECT / "node_modules/pptxgenjs").exists(),
    reason="Local native PowerPoint authoring runtime is unavailable",
)
def test_native_author_respects_role_scale_and_explicit_element_font_size(tmp_path: Path):
    helper = PROJECT.parent / "github/editable-leadership-pptx/assets/pptxgenjs_helpers/layout.js"
    if not helper.is_file():
        pytest.skip("Verified PptxGenJS layout helper is unavailable")
    request = tmp_path / "request.json"
    output = tmp_path / "candidate.pptx"
    profile = style_profile(PresentationForm.COMPETITION_DEFENSE)
    profile["application"] = "baseline_new_composition"
    plan = DeckPlan(title="Native style role test", slides=[
        SlideSpec(id="slide-01", title="Profile cover", purpose="Cover", layout="cover"),
        SlideSpec(id="slide-02", title="Profile title", purpose="Content", layout="content"),
    ], planning_strategy={"visual_style_profile": profile})
    scene = {"slides": [
        {"id": "slide-01", "elements": [
            {"type": "text", "text": "Profile cover", "options": {"x": .5, "y": .5, "w": 4, "h": .6}},
        ]},
        {"id": "slide-02", "elements": [
            {"type": "text", "text": "Profile title", "options": {"x": .5, "y": .5, "w": 4, "h": .6}},
            {"type": "text", "text": "Explicit size", "options": {"x": .5, "y": 1.5, "w": 4, "h": .6, "fontSize": 19, "fontFace": "Georgia"}},
            {"type": "table", "rows": [["Cell"]], "options": {"x": .5, "y": 2.5, "w": 4, "h": 1}},
        ]},
    ]}
    scene = apply_native_style_roles(scene, plan)
    scene["authoring_profile"] = native_authoring_settings(scene, plan)
    scene["font_family"] = plan.font_family
    assert scene["slides"][0]["elements"][0]["style_role"] == "cover_title"
    assert scene["slides"][1]["elements"][0]["style_role"] == "title"
    request.write_text(json.dumps(scene), encoding="utf-8")
    result = subprocess.run(
        [shutil.which("node"), str(PROJECT / "node/author-native.cjs"), str(request), str(output), str(helper)],
        cwd=PROJECT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    with ZipFile(output) as package:
        first_slide = ElementTree.fromstring(package.read("ppt/slides/slide1.xml"))
        slide = ElementTree.fromstring(package.read("ppt/slides/slide2.xml"))
    actual = {}
    for shape in slide.findall(".//{http://schemas.openxmlformats.org/presentationml/2006/main}sp"):
        text = "".join(shape.itertext())
        run = shape.find(".//a:rPr", ns)
        if run is not None and text:
            actual[text] = (run.get("sz"), run.find("a:latin", ns).get("typeface"))
    for page in [first_slide, slide]:
        for run in page.findall(".//a:r", ns):
            text = run.find("a:t", ns)
            props = run.find("a:rPr", ns)
            if text is not None and props is not None:
                actual[text.text] = (props.get("sz"), props.find("a:latin", ns).get("typeface"))
    assert actual["Profile cover"] == ("4600", "Heiti SC")
    assert actual["Profile title"] == ("3400", "Heiti SC")
    assert actual["Explicit size"] == ("1900", "Georgia")
    assert actual["Cell"] == ("2300", "Hiragino Sans GB")
