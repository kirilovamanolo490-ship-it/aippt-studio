"""Source-bound discovery of actually encoded motion in the template library.

The concise index is generated from every source page by motion_library.py.
This search favors encoded timing/transition evidence over marketing filenames.
It does not claim that a transition was played or is suited to a new story.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .template_library import required_form_from_query


_EFFECT_CUES = {
    "平滑": ("morph_transition", None), "morph": ("morph_transition", None),
    "变形": ("morph_transition", None), "路径": ("object_timeline", "animMotion"),
    "平移": ("object_timeline", "animMotion"), "移动": ("object_timeline", "animMotion"),
    "滚动": ("object_timeline", "animMotion"), "旋转": ("object_timeline", "animRot"),
    "缩放": ("object_timeline", "animScale"), "放大": ("object_timeline", "animScale"),
    "淡入": ("object_timeline", "animEffect"), "擦除": ("object_timeline", "animEffect"),
    "视频": ("media_playback", "video"), "配乐": ("media_playback", "audio"),
}
_PAGE_CUES = ("封面", "目录", "结尾", "时间轴", "轮播", "相册", "人物", "品牌", "图文", "转盘", "齿轮")
_GENERIC_MOTION = ("动态", "动画", "动效", "motion", "切换", "转场")
_REQUEST_MARKERS = (*_GENERIC_MOTION, "morph", "轮播", "滚动", "路径动画", "旋转动画")
_NEGATED_MOTION = re.compile(r"(?:不要|无需|不需要|禁止|避免|不做|不加|不使用|不用|无须)\s*(?:任何|所有|这些)?\s*"
                             r"(?:动态|动画|动效|平滑切换|转场|轮播|motion|morph)"
                             r"(?:\s*(?:和|及|与|、|或)\s*(?:动态|动画|动效|平滑切换|转场|轮播|motion|morph))*", re.I)


def motion_requested(request_text: str) -> bool:
    """Detect an explicit positive motion request, ignoring simple negations."""
    for clause in re.split(r"[。；;!?！？\n，,]|(?:但是|但要|改为|转而)", request_text.casefold()):
        if not any(marker in clause for marker in _REQUEST_MARKERS):
            continue
        if re.match(r"\s*(?:不要|无需|不需要|禁止|避免|不做|不加|不使用|不用|无须)", clause):
            continue
        remainder = _NEGATED_MOTION.sub("", clause)
        if any(marker in remainder for marker in _REQUEST_MARKERS):
            return True
    return False


def _rows(path: Path) -> list[dict[str, Any]]:
    result = []
    with path.open("r", encoding="utf-8-sig") as stream:
        for number, line in enumerate(stream, 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"invalid motion index row {number}")
                result.append(value)
    return result


def _preview_lookup(analysis_root: Path, collection_prefix: str) -> dict[str, dict[str, Any]]:
    """Join optional approximate PNG receipts without claiming visual review."""
    preview_root = analysis_root / "分析预览"
    result: dict[str, dict[str, Any]] = {}
    manifest = preview_root / "PREVIEW-MANIFEST.json"
    if manifest.is_file():
        packet = json.loads(manifest.read_text(encoding="utf-8"))
        for row in packet.get("rendered_decks", []):
            path = f"{collection_prefix}/{row['relative_path']}"
            previews = {page["slide"]: f"{collection_prefix}/分析索引/分析预览/{row['output_directory']}/{page['file']}"
                        for page in row.get("rendered_slides", [])}
            result[path] = {"source_sha256": row.get("source_sha256"),
                            "status": "approximate_first_middle_last_pngs", "pages": previews}
    fallback = preview_root / "fallback-single/FALLBACK-MANIFEST.json"
    if fallback.is_file():
        packet = json.loads(fallback.read_text(encoding="utf-8"))
        for row in packet.get("results", []):
            if row.get("status") != "rendered_first_page":
                continue
            path = f"{collection_prefix}/{row['source']}"
            result[path] = {"source_sha256": row.get("source_sha256"),
                            "status": "approximate_first_page_png_only",
                            "pages": {1: f"{collection_prefix}/分析索引/分析预览/{row['output']}"}}
    return result


def _safe_source(root: Path, raw: object) -> Path | None:
    if not isinstance(raw, str) or not raw or "\\" in raw:
        return None
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    source = (root / relative).resolve()
    try:
        source.relative_to(root.resolve())
    except ValueError:
        return None
    return source if source.is_file() and source.suffix.lower() == ".pptx" else None


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _score(query: str, slide: dict[str, Any], deck: dict[str, Any], catalog: dict[str, Any]) -> tuple[int, list[str]]:
    modes = set(slide.get("motion_modes") or [])
    if not modes.intersection({"object_timeline", "morph_transition", "slide_transition", "media_playback"}):
        return 0, []
    if modes == {"morph_transition"} and slide.get("presentation_order") == 1:
        # Morph describes the move *into* this page. With no predecessor in
        # the source deck, this is not a self-contained motion example.
        return 0, []
    counts = slide.get("behavior_counts") or {}
    score, evidence = 2, ["source_page_has_encoded_motion"]
    score += min(3, sum(bool(counts.get(kind)) for kind in ("animMotion", "animScale", "animRot", "animEffect")))
    if "morph_transition" in modes:
        score += 1
    for cue, (mode, behavior) in _EFFECT_CUES.items():
        if cue not in query:
            continue
        if mode in modes and (behavior is None or counts.get(behavior, 0)):
            score += 7
            evidence.append(f"{cue}:encoded_{behavior or mode}")
        elif mode in modes:
            score += 2
            evidence.append(f"{cue}:related_motion_mode")
    title = str(catalog.get("title") or Path(deck["path"]).stem).casefold()
    context = " ".join((title, deck["path"].casefold(), " ".join(str(t) for t in slide.get("all_visible_text", []))[:500].casefold()))
    for cue in _PAGE_CUES:
        if cue in query and cue in context:
            score += 2
            evidence.append(f"{cue}:source_page_or_title")
    if "封面" in query and slide.get("visible_order") == 1:
        score += 1
    if "结尾" in query and slide.get("visible_order") == deck.get("visible_slide_count"):
        score += 1
    if any(cue in query for cue in ("轮播", "相册", "图片")):
        image_count = (slide.get("media_types") or {}).get("image", 0)
        if image_count >= 3:
            score += 3
            evidence.append("multiple_embedded_image_relationships")
        if "morph_transition" in modes or counts.get("animMotion", 0):
            score += 2
            evidence.append("image_sequence_motion_structure")
    if not any(cue in query for cue in (*_PAGE_CUES, *_GENERIC_MOTION, *_EFFECT_CUES)):
        words = [part for part in re.split(r"[\s,，、/|;；:：]+", query) if len(part) >= 2]
        score += min(3, sum(word in context for word in words))
    if slide.get("warnings") or slide.get("parse_error"):
        score -= 3
        evidence.append("source_page_has_inspection_warning")
    return max(score, 0), evidence


def search_motion_library(
    slide_index: Path,
    deck_index: Path,
    template_catalog: Path,
    query: str,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    """Return current-hash-verified page candidates for a dynamic brief."""
    if not query.strip() or not 1 <= limit <= 50:
        raise ValueError("query is required and limit must be 1..50")
    library = template_catalog.parent.resolve()
    requested_form = required_form_from_query(query)
    previews = _preview_lookup(slide_index.parent, slide_index.parent.parent.name)
    decks = {row["path"]: row for row in _rows(deck_index)}
    catalog = {row["path"]: row for row in _rows(template_catalog)}
    candidates: list[dict[str, Any]] = []
    by_identity: dict[tuple[str, int], dict[str, Any]] = {}
    for slide in _rows(slide_index):
        path = slide.get("path")
        deck = decks.get(path)
        if not deck or deck.get("source_sha256") != slide.get("source_sha256"):
            continue
        if catalog.get(path, {}).get("asset_type") != "template":
            continue
        score, evidence = _score(query.casefold(), slide, deck, catalog.get(path, {}))
        if score <= 0:
            continue
        identity = (deck["source_sha256"], slide["presentation_order"])
        preview = previews.get(path, {})
        if preview.get("source_sha256") != deck["source_sha256"]:
            preview = {}
        value = {
            "source_path": path,
            "source_sha256": deck["source_sha256"],
            "source_slide": slide["presentation_order"],
            "visible_order": slide.get("visible_order"),
            "title": catalog.get(path, {}).get("title") or Path(path).stem,
            "category": catalog.get(path, {}).get("category"),
            "occasion_fit": (
                "page_motion_component_only"
                if catalog.get(path, {}).get("category") == "图表与视觉素材"
                else "requires_source_page_and_occasion_review"
            ),
            "score": score,
            "evidence": evidence,
            "motion_modes": slide.get("motion_modes", []),
            "behavior_counts": slide.get("behavior_counts", {}),
            "transition_types": slide.get("transition_types", []),
            "effect_target_shapes": slide.get("effect_target_shapes", []),
            "detail_ref": slide.get("detail_ref"),
            "readable_ref": deck.get("readable_ref"),
            "duplicate_paths": [],
            "recommended_context_slides": ([slide["presentation_order"] - 1, slide["presentation_order"]]
                                           if "morph_transition" in slide.get("motion_modes", [])
                                           and slide["presentation_order"] > 1
                                           else [slide["presentation_order"]]),
            "visual_status": deck.get("review", {}).get("visual", "not_reviewed"),
            "static_preview_status": preview.get("status", "not_rendered"),
            "source_page_png": preview.get("pages", {}).get(slide["presentation_order"]),
            "sampled_deck_pngs": list(preview.get("pages", {}).values()),
            "playback_status": deck.get("review", {}).get("native_playback", "not_verified"),
            "media_rights": catalog.get(path, {}).get("media_rights", "unverified"),
        }
        existing = by_identity.get(identity)
        if existing is None or value["score"] > existing["score"]:
            if existing is not None:
                value["duplicate_paths"] = [existing["source_path"], *existing["duplicate_paths"]]
            by_identity[identity] = value
        else:
            existing["duplicate_paths"].append(path)
    candidates = sorted(by_identity.values(), key=lambda row: (-row["score"], row["source_path"], row["source_slide"]))
    # Show distinct source decks first. A 28-page Morph album should not fill
    # every result slot with its nearly identical adjacent pages.
    diverse: list[dict[str, Any]] = []
    deck_matches: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        digest = candidate["source_sha256"]
        if digest not in deck_matches:
            candidate["other_matching_slides"] = []
            deck_matches[digest] = candidate
            diverse.append(candidate)
        elif len(deck_matches[digest]["other_matching_slides"]) < 12:
            deck_matches[digest]["other_matching_slides"].append(candidate["source_slide"])
    fresh, stale = [], []
    checked: dict[str, bool] = {}
    # Validate only ranked candidates that may be shown to the user; scanning
    # every 4.8 GB source on each query would make discovery needlessly slow.
    for candidate in diverse:
        path = candidate["source_path"]
        if path not in checked:
            source = _safe_source(library, path)
            checked[path] = bool(source and _sha(source) == candidate["source_sha256"])
        if not checked[path]:
            stale.append(path)
            continue
        fresh.append(candidate)
        if len(fresh) >= limit:
            break
    return {
        "schema": "aippt.motion-library-search.v1",
        "query": query,
        "requested_form": requested_form,
        "status": "ready" if fresh else "no_match",
        "candidate_count": len(fresh),
        "candidates": fresh,
        "stale_source_paths": sorted(set(stale)),
        "boundary": "Candidates match encoded motion only. Generic page assets are not a whole-deck occasion match; actual page suitability, rights and PowerPoint playback require review.",
    }


__all__ = ["motion_requested", "search_motion_library"]
