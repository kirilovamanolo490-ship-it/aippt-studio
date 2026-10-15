from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from aippt_studio import library_depth as depth


P = depth.P
A = depth.A
R = depth.R
REL = depth.REL


def _shape(shape_id: int, name: str, text: str, x: int, y: int, *, font: int = 1800,
           placeholder: str | None = None) -> str:
    ph = f'<p:ph type="{placeholder}"/>' if placeholder else ""
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{shape_id}" name="{name}"/><p:cNvSpPr/><p:nvPr>{ph}</p:nvPr></p:nvSpPr>'
            f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="2200000" cy="650000"/></a:xfrm></p:spPr>'
            f'<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr sz="{font}" b="1"/><a:t>{text}</a:t></a:r></a:p></p:txBody></p:sp>')


def _make_pptx(path: Path, *, visible_order=(2, 1), svg: bool = False, absolute_targets: bool = False,
               titles=("项目背景与社区需求", "联合行动与成果验证")) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        archive.writestr("ppt/presentation.xml", f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst><p:sldId id="1" r:id="r1"/><p:sldId id="2" r:id="r2"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')
        target1 = f"/ppt/slides/slide{visible_order[0]}.xml" if absolute_targets else f"slides/slide{visible_order[0]}.xml"
        target2 = f"/ppt/slides/slide{visible_order[1]}.xml" if absolute_targets else f"slides/slide{visible_order[1]}.xml"
        archive.writestr("ppt/_rels/presentation.xml.rels", f'<Relationships xmlns="{REL}"><Relationship Id="r1" Type="slide" Target="{target1}"/><Relationship Id="r2" Type="slide" Target="{target2}"/></Relationships>')
        for number, title, body in ((1, titles[0], "走访评估发现老年服务需求，形成问题证据"),
                                    (2, titles[1], "团队协同开展服务，记录覆盖人数与效果数据")):
            media_tag = ('<p:pic><p:nvPicPr><p:cNvPr id="9" name="需求现场照片"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr>'
                         '<p:blipFill><a:blip r:embed="rImg"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
                         '<p:spPr><a:xfrm><a:off x="6500000" y="1900000"/><a:ext cx="4000000" cy="3000000"/></a:xfrm></p:spPr></p:pic>')
            tree = (_shape(2, "标题", title, 500000, 250000, font=3200, placeholder="title")
                    + _shape(3, "论据", body, 500000, 1500000, font=1800) + media_tag)
            archive.writestr(f"ppt/slides/slide{number}.xml", f'<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}"><p:cSld><p:spTree>{tree}</p:spTree></p:cSld></p:sld>')
            media_target = f"/ppt/media/photo.{'svg' if svg else 'png'}" if absolute_targets else f"../media/photo.{'svg' if svg else 'png'}"
            archive.writestr(f"ppt/slides/_rels/slide{number}.xml.rels", f'<Relationships xmlns="{REL}"><Relationship Id="rImg" Type="image" Target="{media_target}"/></Relationships>')
        archive.writestr("ppt/media/photo.svg" if svg else "ppt/media/photo.png", b"<svg/>" if svg else b"fake-png-bytes")


def test_inspect_deck_uses_visible_order_and_returns_structural_hierarchy_and_media(tmp_path: Path):
    deck = tmp_path / "contest.pptx"
    _make_pptx(deck, visible_order=(2, 1), svg=True)
    record = depth.inspect_library_deck(deck, "contest.pptx", "a" * 64)
    assert record["status"] == "parsed"
    assert record["inspection_status"] == "machine_ooxml_unreviewed"
    assert record["parse_status"] == "parsed"
    assert record["review"]["visual_status"] == "machine_ooxml_unreviewed"
    assert record["visual_reviewed"] is False
    assert [slide["part"] for slide in record["slides"]] == ["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"]
    first = record["slides"][0]
    assert first["title"] == "联合行动与成果验证"
    assert "evidence_results" in [candidate["role"] for candidate in first["role_candidates"]]
    assert first["hierarchy"]["levels"][0]["level"] == "title"
    assert first["composition"]["pattern"] == "image_text_mixed"
    assert first["composition"]["object_counts"]["picture"] == 1
    assert first["media_candidates"][0]["media_part"] == "ppt/media/photo.svg"
    assert first["media_candidates"][0]["relationship_id"] == "rImg"
    assert first["media_candidates"][0]["reuse_status"] == "candidate_only"
    assert first["media_candidates"][0]["rights_status"] == "unverified"
    assert record["actual_source_sha256"] is None


def test_build_depth_index_adds_catalog_tags_and_reuses_path_mtime_size_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    library = tmp_path / "模板库"
    deck_path = library / "竞赛与答辩" / "社工创新项目.pptx"
    _make_pptx(deck_path, visible_order=(1, 2))
    catalog = library / "CATALOG.jsonl"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({"path": "竞赛与答辩/社工创新项目.pptx", "asset_type": "template",
                                   "source_sha256": "b" * 64, "occasion_tags": ["竞赛", "公益项目"],
                                   "style_tags": ["温暖", "绿色"], "page_role_tags": ["结果证据"]}, ensure_ascii=False) + "\n", encoding="utf-8")
    index = library / "DEPTH.jsonl"
    first = depth.build_depth_index(catalog, index)
    assert first["statistics"]["indexed"] == 1
    row = json.loads(index.read_text(encoding="utf-8").splitlines()[0])
    assert row["status"] == "parsed"
    assert row["expected_sha256"] == "b" * 64 and row["actual_source_sha256"] is None
    assert row["occasion_tags"] == ["竞赛", "公益项目"]
    assert row["style_tags"] == ["温暖", "绿色"]
    assert "evidence_results" in row["narrative_roles"]
    monkeypatch.setattr(depth, "inspect_library_deck", lambda *args, **kwargs: pytest.fail("unchanged deck should reuse cached row"))
    second = depth.build_depth_index(catalog, index)
    assert second["statistics"]["reused"] == 1


def test_build_depth_index_prefix_processes_only_matching_directory_and_preserves_other_rows(tmp_path: Path):
    library = tmp_path / "模板库"
    scoped = library / "创赛" / "案例.pptx"
    sibling = library / "职业生涯" / "案例.pptx"
    _make_pptx(scoped)
    _make_pptx(sibling)
    catalog = library / "CATALOG.jsonl"
    catalog.write_text("\n".join(json.dumps({"path": path}, ensure_ascii=False)
                              for path in ("创赛/案例.pptx", "职业生涯/案例.pptx")) + "\n", encoding="utf-8")
    index = library / "DEEP-INDEX.jsonl"
    untouched = {"schema": depth.SCHEMA, "path": "职业生涯/案例.pptx", "marker": "preserved"}
    index.write_text(json.dumps(untouched, ensure_ascii=False) + "\n", encoding="utf-8")

    result = depth.build_depth_index(catalog, index, path_prefix="创赛")
    records = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert result["processed_count"] == 1
    assert result["scoped_entry_count"] == 1
    assert {row["path"] for row in records} == {"创赛/案例.pptx", "职业生涯/案例.pptx"}
    assert next(row for row in records if row["path"] == "职业生涯/案例.pptx") == untouched


@pytest.mark.parametrize("prefix", ("../outside", "/absolute", "创赛/../outside", "创赛/案例.pptx"))
def test_build_depth_index_rejects_unsafe_or_file_prefix_without_mutating_index(tmp_path: Path, prefix: str):
    library = tmp_path / "模板库"
    catalog = library / "CATALOG.jsonl"
    catalog.parent.mkdir(parents=True)
    catalog.write_text("", encoding="utf-8")
    index = library / "DEEP-INDEX.jsonl"
    index.write_text("preserve me\n", encoding="utf-8")

    with pytest.raises(ValueError):
        depth.build_depth_index(catalog, index, path_prefix=prefix)
    assert index.read_text(encoding="utf-8") == "preserve me\n"


def test_single_corrupt_pptx_is_marked_unsupported_without_stopping_catalog(tmp_path: Path):
    library = tmp_path / "模板库"
    good = library / "good.pptx"
    bad = library / "bad.pptx"
    _make_pptx(good)
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not a zip file")
    catalog = library / "CATALOG.jsonl"
    catalog.write_text("\n".join(json.dumps({"path": name}) for name in ("good.pptx", "bad.pptx")) + "\n", encoding="utf-8")
    index = library / "DEPTH.jsonl"
    result = depth.build_depth_index(catalog, index)
    assert result["status"] == "partial"
    assert result["statistics"]["indexed"] == 1
    assert result["statistics"]["unsupported"] == 1
    records = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert {row["status"] for row in records} == {"parsed", "unsupported"}
    assert next(row for row in records if row["path"] == "bad.pptx")["unsupported"]
    repeated = depth.build_depth_index(catalog, index)
    assert repeated["status"] == "partial"
    assert repeated["statistics"]["reused_unsupported"] == 1


def test_depth_uses_full_structure_hash_fallback_and_exact_partial_review_provenance(tmp_path: Path):
    library = tmp_path / "模板库"
    relative = "竞赛与答辩/社工创新项目.pptx"
    deck = library / relative
    _make_pptx(deck, visible_order=(1, 2))
    digest = depth._sha256(deck)
    catalog = library / "CATALOG.jsonl"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({"path": relative, "asset_type": "template",
                                   "occasion_tags": ["竞赛"], "style_tags": ["叙事"]}, ensure_ascii=False) + "\n", encoding="utf-8")
    structure = {"schema": "aippt.template-design-index.v1", "source_path": relative,
                 "source_sha256": digest, "source_bytes": deck.stat().st_size,
                 "status": "parsed", "coverage": {"full_slide_coverage": True}}
    (library / "DESIGN-STRUCTURE.jsonl").write_text(json.dumps(structure) + "\n", encoding="utf-8")
    language = {"schema_version": 1, "designs": [{
        "id": "competition-slide-two", "source_path": f"模板库/{relative}", "source_sha256": digest,
        "reviewed_pages": [2], "review_status": "visual_observed_with_font_limit",
    }]}
    (library / "DESIGN-LANGUAGE.json").write_text(json.dumps(language, ensure_ascii=False), encoding="utf-8")
    samples = {"entries": [{
        "path": relative, "source_sha256": digest, "visual_status": "rendered_pages_2_3",
        "reviewed_pages": [2, 3], "inspection": "rendered slides 2 and 3",
    }, {
        "path": relative, "source_sha256": "f" * 64, "visual_status": "rendered_pages_2_3",
        "reviewed_pages": [1], "inspection": "must not match stale hash",
    }]}
    (library / "REVIEWED-SAMPLES.json").write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
    index = library / "DEPTH.jsonl"

    result = depth.build_depth_index(catalog, index)
    assert result["statistics"]["indexed"] == 1
    row = json.loads(index.read_text(encoding="utf-8").splitlines()[0])
    assert row["expected_sha256"] == digest
    assert row["expected_sha_source"] == "design_structure_full_coverage_cached_hash"
    assert row["review"]["visual_status"] == "partial_visual_observation"
    assert row["review"]["reviewed_pages"] == [2, 3]
    assert row["visual_reviewed"] is False
    assert row["review"]["provenance"]
    assert all(item["source_sha256"] == digest for item in row["review"]["provenance"])
    assert [slide["visual_review_status"] for slide in row["slides"]] == [
        "unreviewed", "partial_visual_observation",
    ]


def test_large_media_hash_is_skipped_without_loading_part(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    deck = tmp_path / "large-media.pptx"
    _make_pptx(deck)
    monkeypatch.setattr(depth, "_MEDIA_HASH_LIMIT", 4)
    record = depth.inspect_library_deck(deck, deck.name)
    media = record["slides"][0]["media_candidates"][0]
    assert media["hash_status"] == "over_hash_limit"
    assert media["sha256"] is None
    assert media["bytes"] > 4


def test_absolute_opc_part_names_resolve_visible_slides_and_media(tmp_path: Path):
    deck = tmp_path / "absolute-targets.pptx"
    _make_pptx(deck, visible_order=(2, 1), absolute_targets=True)
    record = depth.inspect_library_deck(deck, deck.name)
    assert record["status"] == "parsed"
    assert [slide["part"] for slide in record["slides"]] == [
        "ppt/slides/slide2.xml", "ppt/slides/slide1.xml",
    ]
    assert record["slides"][0]["media_candidates"][0]["media_part"] == "ppt/media/photo.png"


def test_numeric_page_placeholder_is_not_promoted_to_a_content_title(tmp_path: Path):
    deck = tmp_path / "numeric-placeholder.pptx"
    _make_pptx(deck, visible_order=(1, 2), titles=("01", "02"))
    record = depth.inspect_library_deck(deck, deck.name)
    assert record["slides"][0]["title"] != "01"
    assert record["slides"][0]["title_source"] == "largest_text"
    assert record["slides"][0]["title_confidence"] == 0.58


def test_old_unsupported_cache_entry_is_reparsed_after_parser_revision(tmp_path: Path):
    library = tmp_path / "模板库"
    relative = "旧缓存/此前关系解析失败.pptx"
    deck = library / relative
    _make_pptx(deck, absolute_targets=True)
    catalog = library / "CATALOG.jsonl"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({"path": relative}, ensure_ascii=False) + "\n", encoding="utf-8")
    stat = deck.stat()
    old_record = {
        "schema": depth.SCHEMA, "path": relative, "expected_sha256": None,
        "source_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
        "status": "unsupported", "unsupported": ["ValueError:no_visible_slides_resolved"], "slides": [],
    }
    index = library / "DEEP-INDEX.jsonl"
    index.write_text(json.dumps(old_record, ensure_ascii=False) + "\n", encoding="utf-8")

    result = depth.build_depth_index(catalog, index)
    assert result["statistics"]["indexed"] == 1
    record = json.loads(index.read_text(encoding="utf-8").splitlines()[0])
    assert record["status"] == "parsed"
    assert record["parser_version"] == depth.PARSER_VERSION
    assert record["schema_revision"] == depth.SCHEMA_REVISION
    assert record["slide_count"] == 2
