"""Keep the uploaded style table aligned with executable Studio profiles."""
from pathlib import Path

from aippt_studio.models import PresentationForm
from aippt_studio.style_profiles import style_profile


REFERENCE = Path(__file__).resolve().parents[1] / "integrations/aippt-studio-powerpoint/references/intake-routing.md"


def test_portable_style_baselines_match_core_numeric_profiles():
    prose = REFERENCE.read_text(encoding="utf-8")
    table = prose.split("## Set a concrete visual baseline for a new composition", 1)[1].split("## Page-message map", 1)[0]
    rows = {}
    for line in table.splitlines():
        if line.startswith("| "):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            rows[cells[0]] = cells

    forms = {
        "General editorial": PresentationForm.GENERAL,
        "Formal government": PresentationForm.GOVERNMENT_BRIEFING,
        "Business decision": PresentationForm.BUSINESS_REPORT,
        "Competition defense": PresentationForm.COMPETITION_DEFENSE,
        "Academic defense": PresentationForm.ACADEMIC_DEFENSE,
        "Teaching or training": PresentationForm.TEACHING,
        "Technical explanation": PresentationForm.TECHNICAL_EXPLANATION,
        "Ceremony or public narrative": PresentationForm.CEREMONIAL_STORY,
        "Premium portfolio": PresentationForm.PREMIUM_BRAND_PORTFOLIO,
    }
    profiles = {name: style_profile(form) for name, form in forms.items()}
    profiles["Children learning"] = style_profile(PresentationForm.TEACHING, "小学四年级学生")
    for name, profile in profiles.items():
        row = rows[name]
        assert row[1] == f"{profile['page_inset_x']} / {profile['page_inset_y']}"
        assert row[2] == " / ".join(
            f"{profile[key] / 2:g}" for key in ("cover_title_px", "title_px", "body_px", "caption_px")
        )
        assert row[3] == " / ".join(str(profile[key]) for key in ("title_body_gap", "item_gap", "grid_gap"))
        assert [float(value.strip()) for value in row[4].split(":")] == profile["image_text_ratio"]
