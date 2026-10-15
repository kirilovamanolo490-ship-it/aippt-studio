from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aippt_studio.motion_search import motion_requested, search_motion_library


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def test_search_prefers_encoded_morph_and_diverse_current_decks(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    library.mkdir()
    a = library / "A.pptx"
    b = library / "B.pptx"
    a.write_bytes(b"source A")
    b.write_bytes(b"source B")
    sha_a = hashlib.sha256(a.read_bytes()).hexdigest()
    sha_b = hashlib.sha256(b.read_bytes()).hexdigest()
    catalog = library / "CATALOG.jsonl"
    decks = library / "MOTION-CATALOG.jsonl"
    slides = library / "MOTION-INDEX.jsonl"
    _jsonl(catalog, [{"path": "A.pptx", "title": "图集平滑切换", "asset_type": "template"},
                     {"path": "B.pptx", "title": "照片轮播", "asset_type": "template"}])
    _jsonl(decks, [{"path": "A.pptx", "source_sha256": sha_a, "visible_slide_count": 3},
                   {"path": "B.pptx", "source_sha256": sha_b, "visible_slide_count": 2}])
    _jsonl(slides, [
        {"path": "A.pptx", "source_sha256": sha_a, "presentation_order": 1, "visible_order": 1,
         "motion_modes": ["morph_transition"], "behavior_counts": {}, "media_types": {"image": 4}},
        {"path": "A.pptx", "source_sha256": sha_a, "presentation_order": 2, "visible_order": 2,
         "motion_modes": ["morph_transition"], "behavior_counts": {}, "media_types": {"image": 4}},
        {"path": "A.pptx", "source_sha256": sha_a, "presentation_order": 3, "visible_order": 3,
         "motion_modes": ["morph_transition"], "behavior_counts": {}, "media_types": {"image": 4}},
        {"path": "B.pptx", "source_sha256": sha_b, "presentation_order": 2, "visible_order": 2,
         "motion_modes": ["object_timeline"], "behavior_counts": {"animMotion": 3}, "media_types": {"image": 4}},
    ])
    result = search_motion_library(slides, decks, catalog, "图片轮播 平滑切换", limit=5)
    assert result["candidate_count"] == 2
    assert result["candidates"][0]["source_path"] == "A.pptx"
    assert result["candidates"][0]["recommended_context_slides"] == [1, 2]
    assert result["candidates"][0]["other_matching_slides"] == [3]
    assert all(row["source_slide"] != 1 for row in result["candidates"])
    a.write_bytes(b"changed")
    stale = search_motion_library(slides, decks, catalog, "图片轮播 平滑切换", limit=5)
    assert stale["candidate_count"] == 1
    assert stale["stale_source_paths"] == ["A.pptx"]


def test_real_collection_has_hash_bound_dynamic_candidates() -> None:
    library = Path(__file__).resolve().parents[2] / "模板库"
    index = library / "镶金边高端动态ppt/分析索引"
    if not (index / "MOTION-INDEX.jsonl").is_file():
        return
    result = search_motion_library(index / "MOTION-INDEX.jsonl", index / "MOTION-CATALOG.jsonl",
                                   library / "CATALOG.jsonl", "平滑切换 图片轮播", limit=3)
    assert result["status"] == "ready"
    assert len({row["source_sha256"] for row in result["candidates"]}) == result["candidate_count"]
    assert all(row["playback_status"] == "not_verified" for row in result["candidates"])
    inherited = search_motion_library(index / "MOTION-INDEX.jsonl", index / "MOTION-CATALOG.jsonl",
                                      library / "CATALOG.jsonl", "循环滚动 路径相册", limit=10)
    assert any("SS57 图片循环轮播" in row["source_path"]
               and "layout_timeline" in row["motion_modes"]
               and row["behavior_counts"].get("animMotion", 0) >= 15
               for row in inherited["candidates"])


def test_explicit_motion_request_does_not_turn_a_negation_into_requirement() -> None:
    assert motion_requested("做一套带平滑切换的动态 PPT")
    assert motion_requested("不要常规淡入，但要图片轮播")
    assert not motion_requested("做静态 PPT，不要动画和转场")
    assert not motion_requested("不要动态图片轮播")
    assert motion_requested("不要动画但是要平滑切换")
