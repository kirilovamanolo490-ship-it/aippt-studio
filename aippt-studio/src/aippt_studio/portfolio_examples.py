"""Source-bound visual observations from the local template and portfolio library.

Most observations guide page review. Explicit allowlisted recipes can also
change first-draft geometry when the cited source and current slide both pass
their checks. Neither path supplies source facts, media, or copying rights.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re


_SHA = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,79}")
_RENDER_RECIPES = {
    "competition_photo_cover": {"form": "competition_defense", "pattern": "cover"},
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _source_file(workspace: Path, locator: str) -> Path | None:
    if not isinstance(locator, str) or not locator.startswith("模板库/"):
        return None
    root = workspace.resolve()
    library_entry = root / "模板库"
    if library_entry.is_symlink():
        return None
    library = library_entry.resolve()
    if library.parent != root:
        return None
    candidate = (root / locator).resolve()
    if library not in candidate.parents or not candidate.is_file() or candidate.suffix.lower() != ".pptx":
        return None
    return candidate


def select_portfolio_examples(workspace: Path, expression_plan: dict,
                              *, catalog_path: Path | None = None) -> dict:
    """Return verified page locators matching the plan's *form and structure*.

    The catalog is intentionally authored after visual review rather than
    inferred from OOXML text. A SHA mismatch suppresses a stale observation.
    This function makes no network or model call and does not open a PPT file.
    """
    if catalog_path is not None:
        path = catalog_path
    else:
        current = workspace / "模板库/EXPRESSION-EXAMPLES.json"
        legacy = workspace / "模板库/作品集/EXPRESSION-EXAMPLES.json"
        path = current if current.is_file() else legacy
    base = {"schema_version": 1, "status": "unavailable", "inspection": "rendered_advisory",
            "source_facts_reusable": False, "source_media_reusable": False,
            "examples": {}, "slide_candidates": {}, "slide_recipes": {}, "skipped": []}
    if expression_plan.get("application") != "baseline_new_composition":
        return {**base, "status": "out_of_scope"}
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return base
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 1 or not isinstance(catalog.get("examples"), list):
        return {**base, "status": "invalid_catalog"}
    form = expression_plan.get("form")
    slides = expression_plan.get("slides", {})
    if not isinstance(form, str) or not isinstance(slides, dict):
        return {**base, "status": "invalid_plan"}
    wanted = {item.get("pattern") for item in slides.values() if isinstance(item, dict)}
    examples: dict[str, dict] = {}
    matching: dict[str, list[str]] = {}
    skipped: list[dict] = []
    verified_hashes: dict[Path, str] = {}
    for row in catalog["examples"]:
        if not isinstance(row, dict):
            continue
        forms, patterns = row.get("forms"), row.get("patterns")
        if (not isinstance(forms, list) or not isinstance(patterns, list)
                or not all(isinstance(item, str) for item in forms + patterns)
                or form not in forms or not wanted.intersection(patterns)):
            continue
        identifier, locator, expected = row.get("id"), row.get("source_path"), row.get("source_sha256")
        source = _source_file(workspace, locator)
        if not isinstance(identifier, str) or not _ID.fullmatch(identifier) or not isinstance(expected, str) or not _SHA.fullmatch(expected) or source is None:
            skipped.append({"id": identifier if isinstance(identifier, str) else "unknown", "reason": "invalid_or_missing_source"})
            continue
        if source not in verified_hashes:
            verified_hashes[source] = _sha256(source)
        if verified_hashes[source] != expected:
            skipped.append({"id": identifier, "reason": "source_sha256_mismatch"})
            continue
        page = row.get("page")
        if (not isinstance(page, int) or isinstance(page, bool) or page < 1
                or not all(isinstance(row.get(field), str) and row[field].strip()
                           for field in ("composition_learning", "inspect_before_use"))):
            skipped.append({"id": identifier, "reason": "invalid_observation"})
            continue
        examples[identifier] = {
            "source_path": locator, "source_sha256": expected, "page": page,
            "composition_learning": row["composition_learning"],
            "inspect_before_use": row["inspect_before_use"],
            "inspection": "rendered_advisory", "advisory_only": True,
        }
        recipe = row.get("render_recipe")
        if (isinstance(recipe, str) and recipe in _RENDER_RECIPES
                and _RENDER_RECIPES[recipe]["form"] == form
                and _RENDER_RECIPES[recipe]["pattern"] in patterns):
            examples[identifier]["render_recipe"] = recipe
        for pattern in patterns:
            matching.setdefault(pattern, []).append(identifier)
    slide_candidates: dict[str, list[str]] = {}
    slide_recipes: dict[str, dict[str, str]] = {}
    used: set[str] = set()
    for slide_id, choice in slides.items():
        if not isinstance(choice, dict):
            continue
        candidates = matching.get(choice.get("pattern"), [])
        if candidates:
            picked = next((item for item in candidates if item not in used), candidates[0])
            slide_candidates[slide_id] = [picked]
            used.add(picked)
            recipe = examples[picked].get("render_recipe")
            if (recipe == "competition_photo_cover" and choice.get("pattern") == "cover"
                    and choice.get("source_visual_count") == 1
                    and isinstance(choice.get("visible_source_points"), int)
                    and choice["visible_source_points"] <= 2):
                slide_recipes[slide_id] = {"recipe": recipe, "example_id": picked,
                                           "source_sha256": examples[picked]["source_sha256"]}
    selected = {identifier for ids in slide_candidates.values() for identifier in ids}
    recipe_sources = {item["example_id"] for item in slide_recipes.values()}
    selected_examples = {key: {**value, "advisory_only": key not in recipe_sources}
                         for key, value in examples.items() if key in selected}
    return {**base, "status": "ready" if selected else "no_matching_example",
            "examples": selected_examples,
            "slide_candidates": slide_candidates, "slide_recipes": slide_recipes, "skipped": skipped,
            "review_boundary": catalog.get("review_boundary", "Visual observations require current-page review."),
            "selection_rule": "Same form and expression pattern; only allowlisted recipes with compatible current-slide structure may affect first-draft geometry. Topic fit, license, copy and whole-page visual quality still require review."}
