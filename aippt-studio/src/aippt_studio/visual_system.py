"""Build a traceable visual-token contract from actual direction or references.

Presentation form decides page grammar.  It must not silently choose a colour
system for every assignment: a government briefing, a competition deck and a
portfolio can all have very different source identities.  This module only
uses an explicit visual instruction or an image deliberately marked as a
visual reference.  Otherwise it returns a documented neutral baseline rather
than pretending that a canned Studio palette came from the client.
"""
from __future__ import annotations

import colorsys
import re
from collections import Counter
from pathlib import Path
from typing import Mapping, Sequence

from PIL import Image

from .models import InputRole, JobBrief, SourceRecord


_NEUTRAL_TOKENS = {
    "canvas": "#e7ebee",
    "surface": "#fffefd",
    "ink": "#202a33",
    "heading": "#18232e",
    "accent": "#526373",
    "accent_soft": "#eff2f3",
    "line": "#b9c4ca",
    "cover": "#25313b",
    "cover_ink": "#ffffff",
    "conclusion": "#f3f3f0",
}

# These token families are used only when the instruction itself names a
# chromatic direction.  They are not associated with a presentation form.
_EXPLICIT_FAMILIES: tuple[tuple[str, tuple[str, ...], dict[str, str]], ...] = (
    (
        "navy",
        ("深蓝", "藏蓝", "海军蓝", "蓝色", "蓝调", "blue", "navy"),
        {
            "canvas": "#e7edf5", "surface": "#fbfcff", "ink": "#1b2a3b", "heading": "#102b49",
            "accent": "#3e6f9f", "accent_soft": "#eef4fb", "line": "#aebfd2",
            "cover": "#102d4a", "cover_ink": "#ffffff", "conclusion": "#eff4f8",
        },
    ),
    (
        "red",
        ("深红", "红色", "红金", "红调", "red", "crimson"),
        {
            "canvas": "#f4e9e8", "surface": "#fffdfc", "ink": "#372524", "heading": "#6f2426",
            "accent": "#a84a43", "accent_soft": "#f9efec", "line": "#d6b3ae",
            "cover": "#6c292a", "cover_ink": "#fffaf6", "conclusion": "#f7efeb",
        },
    ),
    (
        "green",
        ("墨绿", "绿色", "绿调", "green", "emerald"),
        {
            "canvas": "#e7efea", "surface": "#fcfefc", "ink": "#20352c", "heading": "#1f513e",
            "accent": "#4c8269", "accent_soft": "#edf5ef", "line": "#b1cabc",
            "cover": "#214939", "cover_ink": "#ffffff", "conclusion": "#eff5f0",
        },
    ),
    (
        "gold",
        ("金色", "金调", "鎏金", "金黑", "gold", "golden"),
        {
            "canvas": "#f2eee5", "surface": "#fffdf8", "ink": "#342e22", "heading": "#55421d",
            "accent": "#a47d2c", "accent_soft": "#f7f1e3", "line": "#d3c39d",
            "cover": "#30291e", "cover_ink": "#fff9e9", "conclusion": "#f5f0e4",
        },
    ),
    (
        "purple",
        ("紫色", "紫调", "purple", "violet"),
        {
            "canvas": "#eeeaf4", "surface": "#fefcff", "ink": "#2d2738", "heading": "#453266",
            "accent": "#765aa2", "accent_soft": "#f3eff9", "line": "#c5b9d9",
            "cover": "#3f315d", "cover_ink": "#ffffff", "conclusion": "#f3f0f7",
        },
    ),
    (
        "monochrome",
        ("黑白", "黑金", "单色", "monochrome", "black and white"),
        {
            "canvas": "#e8e8e7", "surface": "#fcfcfa", "ink": "#232323", "heading": "#111111",
            "accent": "#545454", "accent_soft": "#f0f0ee", "line": "#bdbdb9",
            "cover": "#1c1c1b", "cover_ink": "#ffffff", "conclusion": "#f1f1ee",
        },
    ),
)


def _is_negated(text: str, start: int) -> bool:
    prefix = re.split(r"[，。；,;.!！?？\n]", text[max(0, start - 24):start].lower())[-1]
    return bool(re.search(r"(?:不要|不(?:用|需|要|应|做|走|必|推荐|想要|希望)?|禁止|无需|避免|不是|非|别|not|no|without)[^，。；,;.!！?？\n]{0,12}$", prefix))


def _explicit_family(text: str) -> tuple[str, dict[str, str]] | None:
    lowered = text.lower()
    for name, markers, tokens in _EXPLICIT_FAMILIES:
        for marker in markers:
            for match in re.finditer(re.escape(marker.lower()), lowered):
                if not _is_negated(lowered, match.start()):
                    return name, dict(tokens)
    return None


def _hex_from_hls(hue: float, lightness: float, saturation: float) -> str:
    red, green, blue = colorsys.hls_to_rgb(hue % 1.0, max(0.0, min(1.0, lightness)), max(0.0, min(1.0, saturation)))
    return f"#{round(red * 255):02x}{round(green * 255):02x}{round(blue * 255):02x}"


def _tokens_from_rgb(rgb: tuple[int, int, int]) -> dict[str, str]:
    red, green, blue = (value / 255 for value in rgb)
    hue, lightness, saturation = colorsys.rgb_to_hls(red, green, blue)
    # Keep reference colour as an accent, then derive calm readable surfaces
    # around it.  Sampling a photo never gives us permission to crop, mask or
    # otherwise alter that source image.
    accent_saturation = max(0.28, min(0.64, saturation))
    return {
        "canvas": _hex_from_hls(hue, 0.93, min(0.28, accent_saturation * 0.46)),
        "surface": _hex_from_hls(hue, 0.985, min(0.10, accent_saturation * 0.17)),
        "ink": _hex_from_hls(hue, 0.18, min(0.35, accent_saturation * 0.60)),
        "heading": _hex_from_hls(hue, 0.15, min(0.50, accent_saturation * 0.82)),
        "accent": _hex_from_hls(hue, 0.42, accent_saturation),
        "accent_soft": _hex_from_hls(hue, 0.95, min(0.22, accent_saturation * 0.35)),
        "line": _hex_from_hls(hue, 0.74, min(0.32, accent_saturation * 0.55)),
        "cover": _hex_from_hls(hue, 0.20, min(0.68, accent_saturation * 1.08)),
        "cover_ink": "#ffffff",
        "conclusion": _hex_from_hls(hue, 0.96, min(0.14, accent_saturation * 0.22)),
    }


def _reference_palette(path: Path) -> tuple[dict[str, str], tuple[int, int, int]] | None:
    try:
        with Image.open(path) as image:
            rgba = image.convert("RGBA")
            rgba.thumbnail((72, 72))
            candidates = Counter(
                (red // 16 * 16, green // 16 * 16, blue // 16 * 16)
                for red, green, blue, alpha in rgba.getdata()
                if alpha >= 200 and 24 < max(red, green, blue) and min(red, green, blue) < 238
            )
    except (OSError, ValueError):
        return None
    if not candidates:
        return None
    # Prefer a genuinely chromatic sample when a reference contains one; an
    # architectural photo's grey background should not erase its colour cue.
    ranked = sorted(
        candidates.items(),
        key=lambda item: (
            colorsys.rgb_to_hls(*(channel / 255 for channel in item[0]))[2] * item[1],
            item[1],
        ),
        reverse=True,
    )
    rgb = ranked[0][0]
    return _tokens_from_rgb(rgb), rgb


def _role(record: SourceRecord, input_roles: Mapping[str, InputRole]) -> InputRole:
    value = input_roles.get(record.id, record.metadata.get("input_role", InputRole.CONTENT))
    try:
        return value if isinstance(value, InputRole) else InputRole(str(value))
    except ValueError:
        return InputRole.CONTENT


def visual_system_contract(
    root: Path,
    brief: JobBrief,
    records: Sequence[SourceRecord],
    input_roles: Mapping[str, InputRole],
    *,
    request_text: str = "",
    editorial_text: Sequence[str] = (),
) -> dict[str, object]:
    """Return the source and tokens used by the HTML/native visual renderer."""

    directions = [request_text, brief.style_direction, *brief.customer_constraints, *editorial_text]
    for raw in directions:
        if not raw.strip():
            continue
        selected = _explicit_family(raw)
        if selected is not None:
            family, tokens = selected
            return {
                "schema_version": 1,
                "source": "explicit_visual_direction",
                "source_refs": ["work_request" if raw == request_text else "brief_or_editorial_direction"],
                "family": family,
                "tokens": tokens,
                "rule": "Tokens follow an explicit visual instruction; presentation form still controls page grammar.",
            }
    for record in records:
        if _role(record, input_roles) != InputRole.VISUAL_REFERENCE or record.kind != "image" or not record.stored_path:
            continue
        candidate = (root / record.stored_path).resolve()
        if root.resolve() not in candidate.parents or not candidate.is_file():
            continue
        palette = _reference_palette(candidate)
        if palette is not None:
            tokens, rgb = palette
            return {
                "schema_version": 1,
                "source": "visual_reference_image",
                "source_refs": [record.id],
                "sample_rgb": list(rgb),
                "tokens": tokens,
                "rule": "Tokens are sampled from the designated visual reference only; source images retain their own geometry contract.",
            }
    return {
        "schema_version": 1,
        "source": "neutral_no_visual_evidence",
        "source_refs": [],
        "tokens": dict(_NEUTRAL_TOKENS),
        "rule": "No explicit visual direction or designated visual reference was supplied. Use a neutral readable baseline and do not claim a client-derived visual system.",
    }


def css_variable_block(contract: object) -> str:
    """Serialize validated visual tokens into a CSS variable declaration."""

    defaults = dict(_NEUTRAL_TOKENS)
    if isinstance(contract, dict) and isinstance(contract.get("tokens"), dict):
        for key in defaults:
            value = contract["tokens"].get(key)
            if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                defaults[key] = value.lower()
    # Token dictionary keys are Python identifiers (cover_ink, accent_soft),
    # while the renderer consumes CSS names with hyphens. Emitting underscores
    # made cover text inherit dark body ink on a dark cover, and silently
    # disabled soft-accent surfaces in several form-specific layouts.
    return ":root{" + ";".join(f"--vs-{key.replace('_', '-')}:{value}" for key, value in defaults.items()) + "}"
