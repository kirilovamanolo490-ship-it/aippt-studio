"""Select source-bound, visually reviewed template design starting points.

The structural inventory covers the library for discovery, while this small
catalog contains only page observations checked at presentation scale. Its
numeric tokens can seed a *new composition*; text, data, photographs, and
template files are never copied into a client's deck.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any


_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
_SHA = re.compile(r"[0-9a-f]{64}")
_TOKEN_BOUNDS = {
    "page_inset_x": (48, 220),
    "page_inset_y": (40, 170),
    "title_px": (48, 110),
    "cover_title_px": (64, 150),
    "body_px": (30, 62),
    "caption_px": (20, 42),
    "statement_px": (44, 110),
    "title_body_gap": (18, 90),
    "item_gap": (8, 64),
    "grid_gap": (20, 120),
}
_FORM_QUERY = {
    "competition_defense": "竞赛 答辩",
    "academic_defense": "学术 答辩",
    "business_report": "工作汇报",
    "government_briefing": "党政 汇报",
    "teaching": "教学",
    "technical_explanation": "科技 技术",
    "ceremonial_story": "活动 文化",
    "premium_brand_portfolio": "作品集 品牌",
    "general": "通用",
}
_PATTERN_QUERY = {
    "cover": "封面", "comparison": "对比", "sequence": "流程",
    "overview": "目录", "image_left": "图文", "image_right": "图文",
    "statement": "主张", "editorial_columns": "内容", "structured_rows": "内容",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_file(workspace: Path, locator: Any) -> Path | None:
    if not isinstance(locator, str) or not locator.startswith("模板库/"):
        return None
    root = workspace.resolve()
    library_entry = root / "模板库"
    if library_entry.is_symlink():
        return None
    library = library_entry.resolve()
    if library.parent != root:
        return None
    source = (root / locator).resolve()
    if library not in source.parents or not source.is_file() or source.suffix.lower() != ".pptx":
        return None
    return source


def _valid_tokens(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    result: dict[str, Any] = {}
    for key, number in value.items():
        if key == "image_text_ratio":
            if (not isinstance(number, list) or len(number) != 2
                    or any(not isinstance(part, (int, float)) or isinstance(part, bool)
                           or not math.isfinite(part) or part < .45 or part > 1.75 for part in number)):
                return None
            result[key] = [round(float(part), 3) for part in number]
        elif key == "body_line_height":
            if (not isinstance(number, (int, float)) or isinstance(number, bool)
                    or not math.isfinite(number) or number < 1.12 or number > 1.8):
                return None
            result[key] = round(float(number), 3)
        elif key in _TOKEN_BOUNDS:
            lower, upper = _TOKEN_BOUNDS[key]
            if (not isinstance(number, (int, float)) or isinstance(number, bool)
                    or not math.isfinite(number) or number < lower or number > upper):
                return None
            result[key] = int(round(number))
        else:
            return None
    if not result:
        return None
    if all(key in result for key in ("title_px", "body_px", "caption_px")):
        if not result["title_px"] > result["body_px"] > result["caption_px"]:
            return None
    return result


def _read_catalog(path: Path) -> list[dict] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (not isinstance(payload, dict) or payload.get("schema_version") != 1
            or not isinstance(payload.get("designs"), list)):
        return None
    return payload["designs"]


def template_design_candidates(workspace: Path, form: str, expression_plan: dict,
                               *, limit_per_pattern: int = 3) -> dict:
    """Retrieve broad metadata plus OOXML metrics for a Work planning packet.

    These are discovery candidates, not selections or permission to apply a
    template. The separate visually reviewed catalog controls executable
    design tokens.
    """
    from .template_library import search_catalog_many

    catalog = workspace / "模板库/CATALOG.jsonl"
    if not catalog.is_file():
        return {"schema_version": 1, "status": "unavailable", "queries": [], "candidates": []}
    slides = expression_plan.get("slides", {}) if isinstance(expression_plan, dict) else {}
    patterns = list(dict.fromkeys(
        item.get("pattern") for item in slides.values() if isinstance(item, dict) and isinstance(item.get("pattern"), str)
    ))[:4]
    if not patterns:
        patterns = ["cover"]
    occasion = _FORM_QUERY.get(form, _FORM_QUERY["general"])
    queries: list[dict] = []
    candidates: dict[str, dict] = {}
    primary_queries = [f"{occasion} {_PATTERN_QUERY.get(pattern, '')}".strip() for pattern in patterns]
    query_texts = list(dict.fromkeys([*primary_queries, occasion]))
    search_results = dict(zip(
        query_texts, search_catalog_many(catalog, query_texts, limit=limit_per_pattern, form=form), strict=True,
    ))
    for pattern in patterns:
        query = f"{occasion} {_PATTERN_QUERY.get(pattern, '')}".strip()
        found = search_results[query]
        if not found["results"]:
            found = search_results[occasion]
        queries.append({"pattern": pattern, "query": found["query"],
                        "paths": [row["path"] for row in found["results"]]})
        for row in found["results"]:
            if row["is_completed_case"] or row["asset_type"].casefold() != "template":
                continue
            candidates.setdefault(row["path"], {
                "path": row["path"], "title": row["title"],
                "asset_type": row["asset_type"], "review_status": row["review_status"],
                "exists": row["exists"], "design_structure": row["design_structure"],
                "candidate_only": True,
            })
    return {"schema_version": 1, "status": "ready" if candidates else "no_match",
            "form": form, "queries": queries, "candidates": list(candidates.values()),
            "boundary": "Filename, metadata and cached OOXML metrics only. Inspect actual pages and source rights before selecting any theme or content."}


def select_template_design(
    workspace: Path, form: str, expression_plan: dict, style_text: str = "",
    *, catalog_path: Path | None = None,
) -> dict:
    """Choose one verified family by occasion, page pattern, and style intent.

    Only the cited source pages are visually reviewed. Search and planning may
    expose other structural candidates, but those cannot execute design tokens.
    """
    base = {"schema_version": 1, "status": "unavailable", "selected": None,
            "candidates": [], "observations": [], "slide_guidance": {}, "skipped": [], "source_facts_reusable": False,
            "source_media_reusable": False, "application": "advisory_only"}
    if expression_plan.get("application") != "baseline_new_composition":
        return {**base, "status": "out_of_scope"}
    rows = _read_catalog(catalog_path or workspace / "模板库/DESIGN-LANGUAGE.json")
    if rows is None:
        return base
    slides = expression_plan.get("slides", {})
    if not isinstance(form, str) or not isinstance(slides, dict):
        return {**base, "status": "invalid_plan"}
    wanted = {row["pattern"] for row in slides.values()
              if isinstance(row, dict) and isinstance(row.get("pattern"), str)}
    if not wanted:
        return {**base, "status": "invalid_plan"}
    style = style_text.casefold() if isinstance(style_text, str) else ""
    candidates: list[dict] = []
    observations: list[dict] = []
    skipped: list[dict] = []
    hashes: dict[Path, str] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("forms"), list) or form not in row["forms"]:
            continue
        identifier = row.get("id")
        patterns = row.get("patterns")
        pages = row.get("reviewed_pages")
        if (not isinstance(identifier, str) or not _ID.fullmatch(identifier)
                or not isinstance(patterns, list) or not patterns
                or not all(isinstance(pattern, str) for pattern in patterns)
                or not isinstance(pages, list) or not pages
                or not all(isinstance(page, int) and not isinstance(page, bool) and page > 0 for page in pages)):
            skipped.append({"id": identifier, "reason": "invalid_or_unreviewed_design"})
            continue
        review_status = row.get("review_status")
        if review_status not in {"visual_verified", "visual_observed_with_font_limit"}:
            skipped.append({"id": identifier, "reason": "invalid_or_unreviewed_design"})
            continue
        raw_tags = row.get("style_tags", [])
        tags = [tag for tag in raw_tags if isinstance(tag, str)] if isinstance(raw_tags, list) else []
        matched_tags = [tag for tag in tags if tag and tag.casefold() in style]
        matched_patterns = sorted(wanted.intersection(patterns))
        if not matched_patterns:
            continue
        locator, expected = row.get("source_path"), row.get("source_sha256")
        source = _source_file(workspace, locator)
        if source is None or not isinstance(expected, str) or not _SHA.fullmatch(expected):
            skipped.append({"id": identifier, "reason": "invalid_or_missing_source"})
            continue
        if source not in hashes:
            hashes[source] = _sha256(source)
        if hashes[source] != expected:
            skipped.append({"id": identifier, "reason": "source_sha256_mismatch"})
            continue
        observations.append({
            "id": identifier, "source_path": locator, "source_sha256": expected,
            "reviewed_pages": pages, "review_status": review_status,
            "page_variant": row.get("page_variant", ""),
            "matched_patterns": matched_patterns,
            "composition_learning": row.get("composition_learning", ""),
            "language_rules": row.get("language_rules", []),
            "image_roles": row.get("image_roles", ""),
            "measurement_notes": row.get("measurement_notes", ""),
            "page_measurements": row.get("page_measurements", {}),
            "review_note": row.get("review_note", ""),
            "priority": row.get("priority", 0) if isinstance(row.get("priority", 0), int) else 0,
            "matched_style_tags": matched_tags,
            "source_sha256_verified": True,
            "application": "advisory_only",
        })
        tokens = _valid_tokens(row.get("design_tokens"))
        # An unfamiliar style phrase can still retrieve a page-role lesson,
        # but it must not impose an unrequested numeric visual profile.
        if review_status != "visual_verified" or tokens is None or (style.strip() and not matched_tags):
            continue
        # A lone attractive cover is insufficient to impose deck-wide type
        # and spacing on an otherwise different set of page roles.
        minimum_roles = min(2, len(wanted))
        if wanted and (len(matched_patterns) < minimum_roles or len(pages) < minimum_roles):
            continue
        priority = row.get("priority", 0)
        if not isinstance(priority, int) or isinstance(priority, bool) or not -5 <= priority <= 5:
            priority = 0
        score = 5 * len(matched_tags) + 2 * len(matched_patterns) + priority
        candidates.append({
            "id": identifier, "source_path": locator, "source_sha256": expected,
            "reviewed_pages": pages, "forms": row["forms"], "patterns": patterns,
            "matched_patterns": matched_patterns, "matched_style_tags": matched_tags,
            "score": score, "design_tokens": tokens,
            "observed_typefaces": row.get("observed_typefaces", []),
            "language_rules": row.get("language_rules", []),
            "composition_learning": row.get("composition_learning", ""),
            "review_note": row.get("review_note", ""),
        })
    # Expose a page-role-specific lesson even when no measured numeric profile
    # is safe to execute. This is planning guidance, never audience-facing copy.
    guidance: dict[str, dict] = {}
    for slide_id, item in slides.items():
        if not isinstance(item, dict) or not isinstance(item.get("pattern"), str):
            continue
        pattern = item["pattern"]
        matching = [row for row in observations if pattern in row["matched_patterns"]]
        if not matching:
            continue
        matching.sort(key=lambda row: (-len(row["matched_style_tags"]), -row["priority"], row["id"]))
        observed = matching[0]
        guidance[slide_id] = {
            "pattern": pattern,
            "design_id": observed["id"],
            "source_path": observed["source_path"],
            "source_sha256": observed["source_sha256"],
            "reviewed_pages": observed["reviewed_pages"],
            "review_status": observed["review_status"],
            "page_variant": observed["page_variant"],
            "composition_learning": observed["composition_learning"],
            "language_rules": observed["language_rules"],
            "image_roles": observed["image_roles"],
            "measurement_notes": observed["measurement_notes"],
            "page_measurements": observed["page_measurements"],
            "review_note": observed["review_note"],
            "source_sha256_verified": True,
            "application": "planning_guidance_only",
        }
    candidates.sort(key=lambda row: (-row["score"], row["id"]))
    if not candidates:
        return {**base, "status": "advisory_only" if observations else "no_matching_verified_design",
                "observations": observations[:12], "slide_guidance": guidance, "skipped": skipped}
    selected = candidates[0]
    return {**base, "status": "ready", "application": "baseline_new_composition",
            "selected": selected, "candidates": candidates[:5], "observations": observations[:12],
            "slide_guidance": guidance, "skipped": skipped,
            "selection_rule": "Occasion, compatible page patterns and explicit style terms; source bytes and visual review are required."
    }


def apply_template_design_tokens(profile: dict, selection: dict) -> dict:
    """Overlay measured starting values while preserving the base profile."""
    result = deepcopy(profile)
    chosen = selection.get("selected") if isinstance(selection, dict) else None
    if not isinstance(selection, dict) or selection.get("status") != "ready" or not isinstance(chosen, dict):
        return result
    tokens = _valid_tokens(chosen.get("design_tokens"))
    if tokens is None:
        return result
    result.update(tokens)
    result["base_profile_id"] = profile.get("profile_id")
    result["profile_id"] = f"template-design:{chosen['id']}"
    result["design_source"] = {key: chosen[key] for key in ("id", "source_path", "source_sha256", "reviewed_pages")}
    return result


def resolve_planned_template_profile(workspace: Path, plan_strategy: dict, profile: dict) -> tuple[dict, str]:
    """Revalidate planning-time design bytes before renderer consumption."""
    packet = plan_strategy.get("template_design_language")
    if not isinstance(packet, dict) or packet.get("status") != "ready":
        return profile, "not_requested"
    base_profile = packet.get("base_profile")
    selected = packet.get("selected")
    expression = plan_strategy.get("expression_plan", {})
    if not isinstance(base_profile, dict) or not isinstance(selected, dict) or not isinstance(expression, dict):
        return profile, "invalid_plan"
    form = packet.get("form")
    current = select_template_design(workspace, form, expression, packet.get("style_text", ""))
    if (current.get("status") != "ready" or current.get("selected", {}).get("id") != selected.get("id")
            or current["selected"].get("source_sha256") != selected.get("source_sha256")
            or current["selected"].get("design_tokens") != selected.get("design_tokens")):
        return deepcopy(base_profile), "suppressed_stale_or_changed_source"
    return apply_template_design_tokens(base_profile, current), "source_verified"
