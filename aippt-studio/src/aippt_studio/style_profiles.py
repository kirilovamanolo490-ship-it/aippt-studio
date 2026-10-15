"""Executable, occasion-specific visual starting systems for new compositions.

The profiles provide concrete geometry and type scales to the native HTML
authoring renderer. They are contextual starting values: a supplied template,
reference, or explicit user direction remains authoritative and may override
them. Values use CSS pixels on the Studio's 1920 x 1080 canvas (2 CSS px = 1 pt
in the configured 13 1/3 x 7.5 inch PowerPoint export).
"""
from __future__ import annotations

from copy import deepcopy
import sys

from .models import PresentationForm


def _type_roles(mac_heading: list[str], mac_body: list[str], mac_label: list[str]) -> dict:
    return {
        "heading": {"darwin": mac_heading, "win32": ["Microsoft YaHei UI", "Microsoft YaHei", "Arial"], "linux": ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "Noto Sans"]},
        "body": {"darwin": mac_body, "win32": ["Microsoft YaHei UI", "Microsoft YaHei", "Arial"], "linux": ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "Noto Sans"]},
        "label": {"darwin": mac_label, "win32": ["Microsoft YaHei UI", "Microsoft YaHei", "Arial"], "linux": ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "Noto Sans"]},
    }


# These tokens govern relative hierarchy and page rhythm. They are not a
# universal minimum-font policy; the form selects an initial scale and the
# actual content/reference can override it.
_PROFILES: dict[PresentationForm, dict] = {
    PresentationForm.GENERAL: {
        "profile_id": "general-editorial-v1", "occasion": "通用叙事演示",
        "typography": _type_roles(["Hiragino Sans GB", "Heiti SC"], ["Hiragino Sans GB", "Heiti SC"], ["Hiragino Sans GB", "DIN Alternate", "Arial"]),
        "page_inset_x": 112, "page_inset_y": 88, "title_px": 70, "cover_title_px": 94,
        "body_px": 46, "body_line_height": 1.40, "caption_px": 30, "statement_px": 62,
        "title_body_gap": 46, "item_gap": 22, "grid_gap": 48, "image_text_ratio": [1.08, 1.0],
        "image_fit": "contain", "image_frame": "none", "density": "balanced",
        "grammar": ["one page claim", "vary evidence and image composition", "consistent page margins"],
    },
    PresentationForm.GOVERNMENT_BRIEFING: {
        "profile_id": "government-briefing-v1", "occasion": "政务及机构正式汇报",
        "typography": _type_roles(["Heiti SC", "Hiragino Sans GB"], ["Hiragino Sans GB", "Heiti SC"], ["Heiti SC", "DIN Alternate", "Arial"]),
        "page_inset_x": 124, "page_inset_y": 82, "title_px": 64, "cover_title_px": 86,
        "body_px": 44, "body_line_height": 1.46, "caption_px": 28, "statement_px": 58,
        "title_body_gap": 38, "item_gap": 18, "grid_gap": 44, "image_text_ratio": [1.0, 1.0],
        "image_fit": "contain", "image_frame": "square-or-none", "density": "measured",
        "grammar": ["fact and responsibility hierarchy", "evidence-led chronology or geography", "restrained formal rhythm"],
    },
    PresentationForm.BUSINESS_REPORT: {
        "profile_id": "business-decision-v1", "occasion": "业务及管理决策汇报",
        "typography": _type_roles(["Hiragino Sans GB", "Avenir Next"], ["Hiragino Sans GB", "Heiti SC"], ["Hiragino Sans GB", "DIN Alternate", "Arial"]),
        "page_inset_x": 112, "page_inset_y": 84, "title_px": 66, "cover_title_px": 88,
        "body_px": 46, "body_line_height": 1.40, "caption_px": 29, "statement_px": 60,
        "title_body_gap": 38, "item_gap": 20, "grid_gap": 52, "image_text_ratio": [1.08, 1.0],
        "image_fit": "contain", "image_frame": "square-or-none", "density": "decision-focused",
        "grammar": ["conclusion then evidence", "action and owner when evidenced", "alternate comparison, trend and decision layouts"],
    },
    PresentationForm.COMPETITION_DEFENSE: {
        "profile_id": "competition-argument-v1", "occasion": "竞赛及项目答辩",
        "typography": _type_roles(["Heiti SC", "Avenir Next", "Hiragino Sans GB"], ["Hiragino Sans GB", "Heiti SC"], ["Heiti SC", "DIN Alternate", "Arial"]),
        "page_inset_x": 104, "page_inset_y": 78, "title_px": 68, "cover_title_px": 92,
        "body_px": 46, "body_line_height": 1.38, "caption_px": 28, "statement_px": 64,
        "title_body_gap": 42, "item_gap": 20, "grid_gap": 48, "image_text_ratio": [0.92, 1.08],
        "image_fit": "contain", "image_frame": "square-or-none", "density": "argument-led",
        "grammar": ["problem to solution to proof", "show comparison and feasibility", "reserve visual emphasis for evidenced differentiators"],
    },
    PresentationForm.ACADEMIC_DEFENSE: {
        "profile_id": "academic-evidence-v1", "occasion": "学术研究答辩",
        "typography": _type_roles(["Songti SC", "Hiragino Sans GB"], ["Hiragino Sans GB", "Songti SC"], ["Songti SC", "Avenir Next", "DIN Alternate"]),
        "page_inset_x": 116, "page_inset_y": 78, "title_px": 64, "cover_title_px": 86,
        "body_px": 42, "body_line_height": 1.42, "caption_px": 27, "statement_px": 58,
        "title_body_gap": 40, "item_gap": 18, "grid_gap": 42, "image_text_ratio": [1.0, 1.05],
        "image_fit": "contain", "image_frame": "none", "density": "evidence-rich",
        "grammar": ["question method results contribution", "keep units, terminology and comparison bases visible", "give charts and source figures room"],
    },
    PresentationForm.TEACHING: {
        "profile_id": "teaching-learning-v1", "occasion": "教学及培训",
        "typography": _type_roles(["Hiragino Sans GB", "Chalkboard SE"], ["Hiragino Sans GB", "Heiti SC"], ["Hiragino Sans GB", "DIN Alternate", "Arial"]),
        "page_inset_x": 116, "page_inset_y": 82, "title_px": 68, "cover_title_px": 92,
        "body_px": 48, "body_line_height": 1.48, "caption_px": 30, "statement_px": 62,
        "title_body_gap": 42, "item_gap": 24, "grid_gap": 44, "image_text_ratio": [0.92, 1.08],
        "image_fit": "contain", "image_frame": "square-or-none", "density": "one-learning-step",
        "grammar": ["one teachable idea per page", "example and explanation remain adjacent", "use repetition only to support learning"],
    },
    PresentationForm.TECHNICAL_EXPLANATION: {
        "profile_id": "technical-mechanism-v1", "occasion": "技术机制说明",
        "typography": _type_roles(["Heiti SC", "Avenir Next", "Hiragino Sans GB"], ["Hiragino Sans GB", "Heiti SC"], ["Heiti SC", "DIN Alternate", "Arial"]),
        "page_inset_x": 108, "page_inset_y": 82, "title_px": 66, "cover_title_px": 90,
        "body_px": 44, "body_line_height": 1.42, "caption_px": 28, "statement_px": 62,
        "title_body_gap": 42, "item_gap": 20, "grid_gap": 44, "image_text_ratio": [0.90, 1.10],
        "image_fit": "contain", "image_frame": "none", "density": "relationship-led",
        "grammar": ["show actual mechanisms and boundaries", "label relationships and units", "do not substitute decorative interface shapes for diagrams"],
    },
    PresentationForm.CEREMONIAL_STORY: {
        "profile_id": "ceremonial-narrative-v1", "occasion": "仪式及叙事表达",
        "typography": _type_roles(["Songti SC", "Avenir Next", "Hiragino Sans GB"], ["Hiragino Sans GB", "Songti SC"], ["Songti SC", "Avenir Next", "Arial"]),
        "page_inset_x": 126, "page_inset_y": 80, "title_px": 70, "cover_title_px": 98,
        "body_px": 44, "body_line_height": 1.44, "caption_px": 28, "statement_px": 68,
        "title_body_gap": 40, "item_gap": 26, "grid_gap": 48, "image_text_ratio": [1.2, 0.8],
        "image_fit": "contain", "image_frame": "square-or-none", "density": "image-and-story-led",
        "grammar": ["vary image scale and narrative pace", "preserve meaningful pauses", "carry identity across the deck"],
    },
    PresentationForm.PREMIUM_BRAND_PORTFOLIO: {
        "profile_id": "portfolio-editorial-v1", "occasion": "品牌及作品集展示",
        "typography": _type_roles(["Songti SC", "Avenir Next"], ["Songti SC", "Hiragino Sans GB"], ["Songti SC", "Avenir Next", "DIN Alternate"]),
        "page_inset_x": 112, "page_inset_y": 72, "title_px": 64, "cover_title_px": 88,
        "body_px": 42, "body_line_height": 1.42, "caption_px": 27, "statement_px": 60,
        "title_body_gap": 34, "item_gap": 22, "grid_gap": 56, "image_text_ratio": [1.25, 0.75],
        "image_fit": "contain", "image_frame": "none", "density": "image-led-editorial",
        "grammar": ["let authentic work carry the page", "use asymmetric editorial layouts", "preserve image corners and intrinsic detail"],
    },
}


def style_profile(form: PresentationForm | str, audience: str = "", purpose: str = "") -> dict:
    """Return a copy of the concrete authoring profile selected by occasion."""
    selected = form if isinstance(form, PresentationForm) else PresentationForm(form)
    profile = deepcopy(_PROFILES[selected])
    child_audience = any(
        word in audience.lower() for word in ("儿童", "幼儿", "小学", "小学生", "孩子", "少儿", "children", "kids")
    )
    educational_purpose = any(
        word in purpose.lower() for word in ("教学", "课程", "课堂", "科普", "培训", "教育", "启蒙", "学习", "绘本")
    )
    if child_audience and (selected == PresentationForm.TEACHING or (selected == PresentationForm.GENERAL and educational_purpose)):
        profile.update({
            "profile_id": "children-learning-v1",
            "occasion": "儿童及低龄学习者教学",
            "title_px": 74, "cover_title_px": 100, "body_px": 52,
            "body_line_height": 1.48, "caption_px": 32, "statement_px": 70,
            "page_inset_x": 104, "page_inset_y": 72, "title_body_gap": 36,
            "item_gap": 28, "grid_gap": 40, "image_text_ratio": [1.0, 1.0],
            "density": "high-clarity-concrete-learning",
            "grammar": [
                "one short concrete learning message per page",
                "large readable labels with image/example adjacent to explanation",
                "use warm visual rhythm without unrelated stickers or decorative badges",
                "repeat stable placement to support orientation, vary examples to sustain interest",
            ],
            "typography": _type_roles(["Hiragino Sans GB", "Arial Rounded MT Bold", "Chalkboard SE"], ["Hiragino Sans GB", "Heiti SC"], ["Hiragino Sans GB", "Arial Rounded MT Bold", "Arial"]),
        })
    return profile


def profile_font_families(profile: dict, platform: str | None = None) -> dict[str, list[str]]:
    """Return per-role ordered families for this host platform."""
    platform = platform or sys.platform
    platform_key = "darwin" if platform == "darwin" else "win32" if platform.startswith("win") else "linux"
    roles = profile.get("typography", {})
    return {
        role: list((roles.get(role, {}).get(platform_key) or ["Arial"]))
        for role in ("heading", "body", "label")
    }


def _css_stack(families: list[str]) -> str:
    return ",".join('"' + family.replace('"', '') + '"' for family in families)


def style_profile_css(profile: dict, *, include_typography: bool = True, platform: str | None = None) -> str:
    """Translate profile tokens into renderer variables consumed by CSS."""
    rules = {
        "--page-inset-x": f"{int(profile['page_inset_x'])}px",
        "--page-inset-y": f"{int(profile['page_inset_y'])}px",
        "--page-title-size": f"{int(profile['title_px'])}px",
        "--page-cover-title-size": f"{int(profile['cover_title_px'])}px",
        "--page-body-size": f"{int(profile['body_px'])}px",
        "--page-body-leading": str(float(profile['body_line_height'])),
        "--page-caption-size": f"{int(profile['caption_px'])}px",
        "--page-statement-size": f"{int(profile['statement_px'])}px",
        "--page-title-gap": f"{int(profile['title_body_gap'])}px",
        "--page-cover-gap": f"{int(profile['title_body_gap']) + 12}px",
        "--page-item-gap": f"{int(profile['item_gap'])}px",
        "--page-grid-gap": f"{int(profile['grid_gap'])}px",
        "--page-image-left": f"{float(profile['image_text_ratio'][0])}fr",
        "--page-image-right": f"{float(profile['image_text_ratio'][1])}fr",
    }
    if include_typography:
        for role, families in profile_font_families(profile, platform).items():
            rules[f"--page-{role}-font"] = _css_stack(families)
    return ":root{" + ";".join(f"{name}:{value}" for name, value in rules.items()) + "}"
