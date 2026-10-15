from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/build-template-catalog.py"
SPEC = importlib.util.spec_from_file_location("build_template_catalog", SCRIPT)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


def _fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    library = tmp_path / "模板库"
    source = library / "作品集/历史样稿/竞赛项目.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"immutable source")
    monkeypatch.setattr(builder, "ROOT", library)
    monkeypatch.setattr(builder, "CATALOG", library / "CATALOG.jsonl")
    monkeypatch.setattr(builder, "SUMMARY", library / "CATEGORY-SUMMARY.csv")
    monkeypatch.setattr(builder, "REPORT", library / "CATALOG-BUILD.json")
    monkeypatch.setattr(builder, "SAMPLES", library / "REVIEWED-SAMPLES.json")
    monkeypatch.setattr(builder, "CLASSIFICATION", library / "分类记录.csv")
    monkeypatch.setattr(builder, "CONTENT_CURATION", library / "CONTENT-CURATION.jsonl")
    return library, source


def test_manifest_semantics_override_legacy_folder_and_name_hints(tmp_path: Path, monkeypatch) -> None:
    library, source = _fixture(tmp_path, monkeypatch)
    (library / "CONTENT-CURATION.jsonl").write_text(json.dumps({
        "path": "作品集/历史样稿/竞赛项目.pptx",
        "category": "社会治理与公共服务",
        "subcategory": "社区养老服务竞赛",
        "display_name": "社区养老服务创新设计竞赛方案",
        "asset_type": "作品案例",
        "confidence": "high",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "evidence": {"cover": "社区养老服务创新设计大赛"},
        "occasion_tags": ["竞赛", "社会工作"],
        "page_role_tags": ["竞赛封面", "方案路线"],
        "style_tags": ["公共服务", "暖色"],
        "review_status": "content_reviewed",
        "evidence_basis": "cover_and_early_slides",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    (library / "分类记录.csv").write_text(
        "final_path,display_title,sha256,confidence,source_url,reason\n"
        "作品集/历史样稿/竞赛项目.pptx,legacy-detail-title," + "a" * 64 +
        ",low,https://example.invalid/source,legacy note\n", encoding="utf-8")
    (library / "REVIEWED-SAMPLES.json").write_text(json.dumps({"entries": [{
        "path": "作品集/历史样稿/竞赛项目.pptx",
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "occasion_tags": ["legacy occasion"], "page_role_tags": ["legacy role"],
        "inspection": "real contact sheet sample", "visual_status": "preview_contact_sheet",
        "page_count": 12,
    }]}), encoding="utf-8")

    builder.main()

    row = json.loads((library / "CATALOG.jsonl").read_text(encoding="utf-8"))
    assert row["title"] == "社区养老服务创新设计竞赛方案"
    assert row["category"] == "社会治理与公共服务"
    assert row["subcategory"] == "社区养老服务竞赛"
    assert row["asset_type"] == "portfolio"
    assert row["confidence"] == "high"
    assert row["evidence"] == {"cover": "社区养老服务创新设计大赛"}
    assert row["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert row["source_url"] == "https://example.invalid/source"
    assert row["curation_review_status"] == "content_reviewed"
    assert row["review_status"] == "visual_sampled"
    assert row["sample_inspection"] == "real contact sheet sample"
    assert row["sample_visual_status"] == "preview_contact_sheet"
    assert row["sample_review_ref"] == "REVIEWED-SAMPLES.json"
    assert row["page_count"] == 12
    assert row["page_role_tags"] == ["竞赛封面", "方案路线"]
    assert row["style_tags"] == ["公共服务", "暖色"]
    assert "作品集" not in row["occasion_tags"]
    assert source.read_bytes() == b"immutable source"
    assert json.loads((library / "CATALOG-BUILD.json").read_text())["content_curated_count"] == 1


def test_missing_manifest_preserves_legacy_folder_candidate_behavior(tmp_path: Path, monkeypatch) -> None:
    library, _ = _fixture(tmp_path, monkeypatch)
    builder.main()
    row = json.loads((library / "CATALOG.jsonl").read_text(encoding="utf-8"))
    assert row["category"] == "作品集"
    assert row["asset_type"] == "portfolio"
    assert row["review_status"] == "filename_only"


def test_curation_cannot_claim_visual_review_without_a_real_sample_receipt(tmp_path: Path, monkeypatch) -> None:
    library, source = _fixture(tmp_path, monkeypatch)
    (library / "CONTENT-CURATION.jsonl").write_text(json.dumps({
        "path": "作品集/历史样稿/竞赛项目.pptx", "category": "竞赛作品",
        "display_name": "竞赛作品方案", "asset_type": "template",
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "review_status": "visual_sampled",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    builder.main()
    row = json.loads((library / "CATALOG.jsonl").read_text(encoding="utf-8"))
    assert row["review_status"] == "curated_evidence"
    assert row["curation_review_status"] == "visual_sampled"
    assert "sample_review_ref" not in row


def test_manifest_rejects_duplicate_or_unsafe_paths(tmp_path: Path, monkeypatch) -> None:
    library, _ = _fixture(tmp_path, monkeypatch)
    manifest = library / "CONTENT-CURATION.jsonl"
    valid = {"path": "../escape.pptx", "category": "x", "display_name": "x", "asset_type": "template"}
    manifest.write_text(json.dumps(valid) + "\n", encoding="utf-8")
    try:
        builder.load_content_curation(manifest)
    except ValueError as exc:
        assert "invalid curation path" in str(exc)
    else:
        raise AssertionError("unsafe manifest path was accepted")

    valid["path"] = "作品集/历史样稿/竞赛项目.pptx"
    manifest.write_text((json.dumps(valid) + "\n") * 2, encoding="utf-8")
    try:
        builder.load_content_curation(manifest)
    except ValueError as exc:
        assert "duplicate curation path" in str(exc)
    else:
        raise AssertionError("duplicate manifest path was accepted")


def test_career_template_is_competition_candidate_but_off_topic_source_is_not(tmp_path: Path, monkeypatch) -> None:
    library, _ = _fixture(tmp_path, monkeypatch)
    base = library / "BB0086 大学生职业生涯规划大赛 PPT"
    good = base / "职业规划结构.pptx"
    wrong = base / "职业规划封面实际年终总结.pptx"
    good.parent.mkdir(parents=True, exist_ok=True)
    good.write_bytes(b"career-template")
    wrong.write_bytes(b"annual-report")
    entries = []
    for source, off_topic, category, asset in (
        (good, False, "人物与职业", "template"),
        (wrong, True, "工作汇报", "reference"),
    ):
        entries.append({"path": source.relative_to(library).as_posix(),
                        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        "display_name": source.stem, "category": category, "asset_type": asset,
                        "classification_observation": {"source_collection": "career", "off_topic": off_topic}})
    (library / "CONTENT-CURATION.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in entries), encoding="utf-8")
    builder.main()
    from aippt_studio.template_library import occasion_compatible
    rows = {item["path"]: item for item in map(json.loads, (library / "CATALOG.jsonl").read_text().splitlines())}
    good_row = rows[good.relative_to(library).as_posix()]
    wrong_row = rows[wrong.relative_to(library).as_posix()]
    assert occasion_compatible(good_row, "competition_defense")
    assert good_row["occasion_candidate_basis"].startswith("SHA-bound")
    assert not occasion_compatible(wrong_row, "competition_defense")
    assert wrong_row["off_topic"] is True
