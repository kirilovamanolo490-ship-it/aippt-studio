"""Read-only metadata and OOXML-design search for a curated PPT library.

The catalog is JSON Lines. Each row should contain a relative ``path`` and may
contain ``asset_type``, ``occasion_tags``, ``style_tags``, ``page_role_tags``,
``review_status``, and a human-readable ``title``. The optional companion
DESIGN-STRUCTURE.jsonl adds full-deck OOXML measurements without treating them
as visual review. Search never opens PPTX files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from .library_read_guard import require_library_available


TAG_FIELDS = ("occasion_tags", "page_role_tags", "style_tags")
CASE_TYPES = {"case", "portfolio", "portfolio_example", "completed_example", "作品集", "成品案例", "完成案例"}
_COMPETITION_MARKERS = ("竞赛", "大赛", "比赛", "参赛", "竞聘", "路演")
_ACADEMIC_MARKERS = ("学术答辩", "论文答辩", "毕业答辩")


def _as_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _tokens(text: str) -> list[str]:
    """Keep Chinese phrases whole and split whitespace/punctuation-delimited tags."""
    return [part.casefold() for part in re.split(r"[\s,，、/|;；:：]+", text.strip()) if part]


def required_form_from_query(query: str, form: str | None = None) -> str | None:
    """Identify an explicit occasion before topic/style words are ranked."""

    if form is not None:
        return form
    lowered = query.casefold()
    if re.search(r"(?:学术|论文|毕业)\s*答辩", lowered):
        return "academic_defense"
    if any(marker in lowered for marker in _COMPETITION_MARKERS):
        return "competition_defense"
    return None


def occasion_compatible(item: dict[str, Any], form: str | None) -> bool:
    """Use cataloged occasion/category, never a topic or attractive filename."""

    if form is None:
        return True
    labels = " ".join([*_as_values(item.get("occasion_tags")), str(item.get("category") or "")]).casefold()
    if form == "competition_defense":
        return any(marker in labels for marker in _COMPETITION_MARKERS)
    if form == "academic_defense":
        return any(marker in labels for marker in _ACADEMIC_MARKERS)
    # No hard occasion taxonomy has been validated for the remaining forms.
    return True


def _library_root(catalog: Path) -> Path:
    """Use the enclosing 模板库 as the path base; standalone catalogs use their parent."""
    parent = catalog.parent.resolve()
    for candidate in (parent, *parent.parents):
        if candidate.name == "模板库":
            return candidate
    return parent


def _safe_asset_path(library_root: Path, raw: Any) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    try:
        resolved = (library_root / candidate).resolve(strict=False)
        resolved.relative_to(library_root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _load_rows(catalog: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    root = _library_root(catalog)
    require_library_available(root)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    try:
        stream = catalog.open("r", encoding="utf-8-sig")
    except OSError as exc:
        return [], [{"line": 0, "error": f"catalog_unavailable: {exc}"}]
    with stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                errors.append({"line": line_no, "error": f"invalid_json: {exc.msg}"})
                continue
            if not isinstance(item, dict):
                errors.append({"line": line_no, "error": "row_must_be_object"})
                continue
            raw_path = item.get("path", item.get("file_path", item.get("source_path")))
            asset_path = _safe_asset_path(root, raw_path)
            if asset_path is None:
                errors.append({"line": line_no, "error": "unsafe_or_missing_path"})
                continue
            # Persist the catalog-relative path; do not require the asset to exist
            # so an offline catalog can still explain stale/missing entries.
            item["path"] = asset_path.relative_to(root).as_posix()
            rows.append(item)
    return rows, errors


def cataloged_template_by_sha256(catalog: Path, source_sha256: str) -> dict[str, Any] | None:
    """Resolve a copied Work input to its current library asset bytes."""

    if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
        return None
    rows, _errors = _load_rows(catalog)
    library = _library_root(catalog)
    structure = _load_design_structure(catalog.parent / "DESIGN-STRUCTURE.jsonl")
    for row in rows:
        expected, basis = _expected_deck_hash(row, structure)
        if row.get("asset_type") != "template" or expected != source_sha256:
            continue
        source = library / row["path"]
        if not source.is_file() or source.suffix.lower() != ".pptx":
            continue
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() == source_sha256:
            return {**row, "source_sha256": expected, "hash_basis": basis}
    return None


def cataloged_template_by_path(catalog: Path, relative_path: str) -> dict[str, Any] | None:
    """Select a cataloged PPTX by path only when its current bytes still match."""

    rows, _errors = _load_rows(catalog)
    structure = _load_design_structure(catalog.parent / "DESIGN-STRUCTURE.jsonl")
    alias_hash: str | None = None
    if not any(row.get("path") == relative_path for row in rows):
        alias_file = catalog.parent / "RENAME-ALIASES.jsonl"
        if alias_file.is_file():
            try:
                for line in alias_file.read_text(encoding="utf-8-sig").splitlines():
                    item = json.loads(line)
                    if isinstance(item, dict) and item.get("old_path") == relative_path:
                        current = item.get("current_path")
                        expected = item.get("source_sha256")
                        if isinstance(current, str) and isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected):
                            relative_path, alias_hash = current, expected
                        break
            except (OSError, ValueError):
                return None
    for row in rows:
        if row.get("path") != relative_path or row.get("asset_type") != "template":
            continue
        expected, basis = _expected_deck_hash(row, structure)
        if expected is None or (alias_hash is not None and alias_hash != expected):
            return None
        source = _library_root(catalog) / row["path"]
        if not source.is_file() or source.suffix.lower() != ".pptx":
            return None
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return {**row, "source_sha256": expected, "hash_basis": basis} if digest.hexdigest() == expected else None
    return None


def _load_design_structure(path: Path) -> dict[str, dict[str, Any]]:
    """Join cached structural evidence by the exact library-relative path."""
    if not path.is_file():
        return {}
    result: dict[str, dict[str, Any]] = {}
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if (not isinstance(row, dict) or row.get("schema") != "aippt.template-design-index.v1"
                        or row.get("status") != "parsed" or not isinstance(row.get("source_path"), str)
                        or not isinstance(row.get("coverage"), dict)
                        or row["coverage"].get("full_slide_coverage") is not True):
                    continue
                result[row["source_path"]] = row
    except OSError:
        return {}
    return result


def _expected_deck_hash(
    row: dict[str, Any], structure: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Use the full-deck structural inventory for older catalog rows without hashes."""

    catalog_hash = row.get("source_sha256")
    if isinstance(catalog_hash, str) and re.fullmatch(r"[0-9a-f]{64}", catalog_hash):
        return catalog_hash, "catalog"
    evidence = structure.get(str(row.get("path") or ""), {})
    structural_hash = evidence.get("source_sha256")
    if isinstance(structural_hash, str) and re.fullmatch(r"[0-9a-f]{64}", structural_hash):
        return structural_hash, "full_deck_ooxml_index"
    return None, None


def search_catalog(catalog: Path, query: str, *, limit: int = 10,
                   form: str | None = None,
                   design_index: Path | None = None,
                   _loaded: tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]] | None = None) -> dict[str, Any]:
    """Rank metadata matches across occasion, page role, and style tags."""
    require_library_available(_library_root(catalog))
    query_tokens = _tokens(query)
    if not query_tokens:
        raise ValueError("query must contain at least one term")
    query_tokens = list(dict.fromkeys(query_tokens))
    required_form = required_form_from_query(query, form)
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    if _loaded is None:
        rows, errors = _load_rows(catalog.expanduser())
        structure = _load_design_structure(design_index or catalog.parent / "DESIGN-STRUCTURE.jsonl")
    else:
        rows, errors, structure = _loaded
    results: list[dict[str, Any]] = []
    occasion_mismatches: list[str] = []
    weights = {"occasion_tags": 3, "page_role_tags": 3, "style_tags": 2}
    for item in rows:
        matched: dict[str, list[str]] = {}
        score = 0
        for field in TAG_FIELDS:
            values = _as_values(item.get(field))
            folded_values = [value.casefold() for value in values]
            hits = [term for term in query_tokens if any(term in value for value in folded_values)]
            if hits:
                matched[field] = list(dict.fromkeys(hits))
                score += len(matched[field]) * weights[field]
        # Search useful names as a lower-weight fallback; never inspect file bytes.
        name = str(item.get("title", ""))
        if not item.get("name_mismatch"):
            name += " " + Path(item["path"]).stem
        name_hits = [term for term in query_tokens if term in name.casefold()]
        if name_hits:
            matched["name"] = list(dict.fromkeys(name_hits))
            score += len(matched["name"])
        if not score:
            continue
        if not occasion_compatible(item, required_form):
            occasion_mismatches.append(item["path"])
            continue
        matched_terms = {term for hits in matched.values() for term in hits}
        asset_type = str(item.get("asset_type", "template"))
        is_case = asset_type.casefold() in {kind.casefold() for kind in CASE_TYPES}
        review_status = str(item.get("review_status", "unreviewed"))
        review_rank = {"visual_sampled": 2, "structure_sampled": 1}.get(review_status, 0)
        score += review_rank
        results.append({
            "path": item["path"],
            "asset_type": asset_type,
            "is_completed_case": is_case,
            "title": item.get("title") or Path(item["path"]).stem,
            "category": item.get("category"),
            "page_count": item.get("page_count"),
            "source_sha256": item.get("source_sha256"),
            "occasion_tags": _as_values(item.get("occasion_tags")),
            "style_tags": _as_values(item.get("style_tags")),
            "page_role_tags": _as_values(item.get("page_role_tags")),
            "review_status": review_status,
            "content_note": item.get("content_note"),
            "tag_basis": item.get("tag_basis"),
            "review_rank": review_rank,
            "score": score,
            "matched_term_count": len(matched_terms),
            "total_query_terms": len(query_tokens),
            "matched_fields": matched,
            "exists": (_library_root(catalog) / item["path"]).exists(),
        })
    results.sort(key=lambda row: (-row["matched_term_count"], -row["score"],
                                  -row["review_rank"], not row["exists"],
                                  row["is_completed_case"], row["path"].casefold()))
    top = results[:limit]
    for row in top:
        evidence = structure.get(row["path"])
        if evidence is None:
            row["design_structure"] = None
            continue
        source_path = _library_root(catalog) / row["path"]
        size_matches = source_path.is_file() and source_path.stat().st_size == evidence.get("source_bytes")
        if not size_matches:
            row["design_structure"] = {"status": "stale_or_missing_source_size", "source_sha256": evidence.get("source_sha256")}
            continue
        row["design_structure"] = {
            "evidence_basis": "cached_full_deck_ooxml; not_rendered_or_visually_reviewed",
            "source_sha256": evidence.get("source_sha256"),
            "coverage": evidence.get("coverage"),
            "page": evidence.get("page"),
            "font_typefaces_top": evidence.get("font_typefaces_top"),
            "font_size_pt": evidence.get("font_size_pt"),
            "text_box_width_ratio_quantiles": evidence.get("text_box_width_ratio_quantiles"),
            "heuristic_hierarchy": evidence.get("heuristic_hierarchy"),
            "text_margins_ratio": evidence.get("text_margins_ratio"),
            "picture_area_sum_per_slide": evidence.get("picture_area_sum_per_slide"),
            "theme1_palette": evidence.get("theme1_palette"),
            "run_colors_top": evidence.get("run_colors_top"),
            "shape_fill_colors_top": evidence.get("shape_fill_colors_top"),
            "language_signals": evidence.get("language_signals"),
            "copy_structure": evidence.get("copy_structure"),
            "current_source_size_matches_cache": True,
            "current_source_sha256_verified": False,
        }
    status = "ok" if results else "no_compatible_match" if required_form and occasion_mismatches else "no_match"
    return {"status": status, "catalog": str(catalog),
            "query": query, "result_count": min(len(results), limit),
            "required_form": required_form, "occasion_mismatches": occasion_mismatches,
            "results": top, "catalog_errors": errors,
            "design_structure_records": len(structure)}


def search_catalog_many(catalog: Path, queries: list[str], *, limit: int = 10,
                        form: str | None = None,
                        design_index: Path | None = None) -> list[dict[str, Any]]:
    """Run related page-role queries against one catalog and index snapshot."""
    rows, errors = _load_rows(catalog.expanduser())
    structure = _load_design_structure(design_index or catalog.parent / "DESIGN-STRUCTURE.jsonl")
    loaded = (rows, errors, structure)
    return [search_catalog(catalog, query, limit=limit, form=form, _loaded=loaded) for query in queries]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    search = subparsers.add_parser("search", help="search a JSONL PPT metadata catalog")
    search.add_argument("--catalog", required=True, type=Path)
    search.add_argument("--query", required=True)
    search.add_argument("--form", help="Require a compatible presentation occasion, such as competition_defense")
    search.add_argument("--limit", type=int, default=10)
    design = subparsers.add_parser("design-language", help="retrieve source-verified page design lessons")
    design.add_argument("--workspace", required=True, type=Path)
    design.add_argument("--form", required=True)
    design.add_argument("--pattern", action="append", required=True)
    design.add_argument("--style", default="")
    args = parser.parse_args(argv)
    try:
        if args.command == "search":
            result = search_catalog(args.catalog, args.query, limit=args.limit, form=args.form)
        else:
            from .template_designs import select_template_design
            expression = {"application": "baseline_new_composition", "slides": {
                f"slide-{index:02d}": {"pattern": pattern}
                for index, pattern in enumerate(args.pattern, start=1)
            }}
            result = select_template_design(args.workspace, args.form, expression, args.style)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
