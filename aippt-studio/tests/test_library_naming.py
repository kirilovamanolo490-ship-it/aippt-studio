from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from aippt_studio.library_naming import (apply_library_migration, apply_library_renames,
                                         propose_library_renames, _usable_evidence)


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    library = tmp_path / "模板库"
    source = library / "竞赛与答辩/7798-20240929.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"current deck bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    catalog = library / "CATALOG.jsonl"
    catalog.write_text(json.dumps({"path": "竞赛与答辩/7798-20240929.pptx", "title": "7798-20240929",
                                   "asset_type": "template"}, ensure_ascii=False) + "\n", encoding="utf-8")
    (library / "DESIGN-STRUCTURE.jsonl").write_text(json.dumps({
        "source_path": "竞赛与答辩/7798-20240929.pptx", "source_sha256": digest,
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    (library / "分类记录.csv").write_text("final_path,display_title\n竞赛与答辩/7798-20240929.pptx,7798-20240929\n", encoding="utf-8")
    (library / "DESIGN-LANGUAGE.json").write_text(json.dumps({
        "designs": [{"source_path": "模板库/竞赛与答辩/7798-20240929.pptx"}],
    }, ensure_ascii=False), encoding="utf-8")
    (library / "REVIEWED-SAMPLES.json").write_text(json.dumps({
        "entries": [{"path": "竞赛与答辩/7798-20240929.pptx"}],
    }, ensure_ascii=False), encoding="utf-8")
    depth = library / "DEEP-INDEX.jsonl"
    depth.write_text(json.dumps({
        "schema": "aippt.library-depth.v1", "path": "竞赛与答辩/7798-20240929.pptx",
        "parse_status": "parsed", "source_bytes": source.stat().st_size,
        "mtime_ns": source.stat().st_mtime_ns, "expected_sha256": digest,
        "slides": [{"slide": 1, "title": "社区养老创新实践", "title_source": "title_placeholder",
                    "title_confidence": 0.94, "role_candidates": [{"role": "cover"}], "text_excerpt": "服务对象与实施路径"},
                   {"slide": 2, "title": "需求与解决路径", "role_candidates": [{"role": "method_approach"}]}],
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    return catalog, depth, source


def test_numeric_library_file_gets_content_name_and_all_active_indexes_follow(tmp_path: Path) -> None:
    catalog, depth, source = _fixture(tmp_path)
    planned = propose_library_renames(catalog, depth)
    assert len(planned["proposals"]) == 1
    proposed = planned["proposals"][0]
    assert proposed["new_path"].endswith("社区养老创新实践-封面版式.pptx")
    assert proposed["naming_basis"] == "slide_title"
    manifest = apply_library_renames(planned, backup_dir=tmp_path / "work-backup")
    target = catalog.parent / proposed["new_path"]
    assert not source.exists() and target.read_bytes() == b"current deck bytes"
    assert manifest["status"] == "applied" and (tmp_path / "work-backup/rename-manifest.json").is_file()
    assert json.loads(catalog.read_text())["path"] == proposed["new_path"]
    assert json.loads(catalog.read_text())["title"] == Path(proposed["new_path"]).stem
    assert json.loads(depth.read_text())["path"] == proposed["new_path"]
    assert json.loads((catalog.parent / "DESIGN-STRUCTURE.jsonl").read_text())["source_path"] == proposed["new_path"]
    assert json.loads((catalog.parent / "REVIEWED-SAMPLES.json").read_text())["entries"][0]["path"] == proposed["new_path"]
    assert json.loads((catalog.parent / "DESIGN-LANGUAGE.json").read_text())["designs"][0]["source_path"] == "模板库/" + proposed["new_path"]
    alias = json.loads((catalog.parent / "RENAME-ALIASES.jsonl").read_text())
    assert alias["old_path"] == "竞赛与答辩/7798-20240929.pptx"
    assert alias["current_path"] == proposed["new_path"]
    assert alias["source_sha256"] == proposed["expected_sha256"]
    assert "社区养老创新实践" in (catalog.parent / "分类记录.csv").read_text()


def test_rename_refuses_stale_source_without_mutating_library(tmp_path: Path) -> None:
    catalog, depth, source = _fixture(tmp_path)
    planned = propose_library_renames(catalog, depth)
    before = catalog.read_bytes()
    source.write_bytes(b"changed")
    with pytest.raises(ValueError, match="source changed"):
        apply_library_renames(planned, backup_dir=tmp_path / "backup")
    assert source.exists() and catalog.read_bytes() == before
    assert not (tmp_path / "backup").exists()


def test_non_numeric_descriptive_file_is_not_renamed(tmp_path: Path) -> None:
    catalog, depth, source = _fixture(tmp_path)
    descriptive = source.with_name("社区养老创新服务.pptx")
    source.rename(descriptive)
    row = json.loads(catalog.read_text())
    row["path"] = "竞赛与答辩/社区养老创新服务.pptx"
    catalog.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    record = json.loads(depth.read_text())
    record["path"] = row["path"]
    depth.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    assert propose_library_renames(catalog, depth)["proposals"] == []


def test_weak_heuristic_titles_fall_back_to_explicit_category_name(tmp_path: Path) -> None:
    catalog, depth, _ = _fixture(tmp_path)
    record = json.loads(depth.read_text())
    record["slides"][0].update(title="鼓", title_source="largest_text", title_confidence=0.45)
    depth.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    plan = propose_library_renames(catalog, depth)
    assert len(plan["proposals"]) == 1
    assert plan["proposals"][0]["naming_basis"] == "category_fallback"
    assert "鼓-" not in plan["proposals"][0]["new_path"]


def test_meaningful_largest_text_cover_headline_is_a_safe_secondary_basis(tmp_path: Path) -> None:
    catalog, depth, _ = _fixture(tmp_path)
    record = json.loads(depth.read_text())
    record["slides"][0].update(title="弘扬红船精神", title_source="largest_text", title_confidence=0.45)
    depth.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    proposal = propose_library_renames(catalog, depth)["proposals"][0]
    assert proposal["naming_basis"] == "cover_headline"
    assert proposal["new_path"].endswith("弘扬红船精神-封面版式.pptx")


def test_short_cover_fragment_is_not_used_as_headline(tmp_path: Path) -> None:
    catalog, depth, _ = _fixture(tmp_path)
    record = json.loads(depth.read_text())
    record["slides"][0].update(title="蓉", title_source="largest_text", title_confidence=0.45)
    depth.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    proposal = propose_library_renames(catalog, depth)["proposals"][0]
    assert proposal["naming_basis"] == "category_fallback"
    assert "蓉" not in proposal["new_path"]


@pytest.mark.parametrize("phrase", [
    "网站：www.gaojiewenhua.cn", "建军节晚会-PPT-Add Title", "-POWERPOINT动态模板",
    "-请添加您的副标题", "-DESIGNED BY ALONIC", "-We have many PowerPo",
    "-汇报：豌豆素材", "-0.5 秒延迟符，无", "T-19 两会模版-PART 01 添加文字标题",
    "xiaohongshu", "SALES", "FAN JUAN", "BUSINESS", "工作总结及",
    "一、请插入您的大标题的内容", "相关标题文字", "在此输入副标题文字",
    "汇报壹德设计", "亮亮图文旗舰店", "意义，可删除", "请在此处输入您的文本",
    "◎节标题一", "您的内容打在这里", "汇报部门：市场部", "重庆高阶文化传媒有限公司",
    "负责人：王宇琳", "指导老师：刘阳华", "项目组别：某某赛道", "负责人展示设备", "PPT模版",
])
def test_cover_and_collision_evidence_filters_watermarks_and_fragments(phrase: str) -> None:
    assert _usable_evidence(phrase, maximum=28) == ""


def test_cover_title_is_not_silently_truncated() -> None:
    assert _usable_evidence("非常长的封面标题内容" * 4, maximum=28) == ""


def test_same_headline_uses_distinct_cover_text_before_hash_suffix(tmp_path: Path) -> None:
    catalog, depth, source = _fixture(tmp_path)
    library = catalog.parent
    second = source.with_name("7800.pptx")
    second.write_bytes(b"second deck bytes")
    rows = [json.loads(line) for line in catalog.read_text().splitlines()]
    rows.append({"path": second.relative_to(library).as_posix(), "title": "7800", "asset_type": "template"})
    catalog.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    records = [json.loads(line) for line in depth.read_text().splitlines()]
    second_record = json.loads(json.dumps(records[0]))
    second_record["path"] = second.relative_to(library).as_posix()
    second_record["source_bytes"] = second.stat().st_size
    second_record["mtime_ns"] = second.stat().st_mtime_ns
    second_record["expected_sha256"] = hashlib.sha256(second.read_bytes()).hexdigest()
    records.append(second_record)
    for index, record in enumerate(records):
        record["slides"][0].update(title="我们的队伍向太阳", title_source="largest_text", title_confidence=0.45)
        record["slides"][0]["text_excerpt"] = "我们的队伍向太阳"
        record["slides"][1].update(
            title="红色文化传承与青年实践" if index == 0 else "公益志愿服务项目成果",
            title_source="title_placeholder", title_confidence=0.92,
        )
    depth.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    proposals = propose_library_renames(catalog, depth)["proposals"]
    paths = [proposal["new_path"] for proposal in proposals]
    assert len(paths) == 2
    assert paths[0] == "竞赛与答辩/我们的队伍向太阳-封面版式.pptx"
    assert any("公益志愿服务项目成果" in path for path in paths)
    assert all(not re.search(r"-[0-9a-f]{7}$", Path(path).stem) for path in paths)


def test_duplicate_cover_with_only_personnel_metadata_keeps_hash_fallback(tmp_path: Path) -> None:
    catalog, depth, source = _fixture(tmp_path)
    library = catalog.parent
    second = source.with_name("7800.pptx")
    second.write_bytes(b"another deck")
    rows = [json.loads(line) for line in catalog.read_text().splitlines()]
    rows.append({"path": second.relative_to(library).as_posix(), "title": "7800", "asset_type": "template"})
    catalog.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    records = [json.loads(line) for line in depth.read_text().splitlines()]
    second_record = json.loads(json.dumps(records[0]))
    second_record.update(path=second.relative_to(library).as_posix(), source_bytes=second.stat().st_size,
                         mtime_ns=second.stat().st_mtime_ns,
                         expected_sha256=hashlib.sha256(second.read_bytes()).hexdigest())
    records.append(second_record)
    for index, record in enumerate(records):
        record["slides"][0].update(title="内容清晰的竞赛封面", title_source="largest_text", title_confidence=0.45,
                                    text_excerpt="内容清晰的竞赛封面")
        record["slides"][1]["text_excerpt"] = "负责人：王宇琳" if index == 0 else "指导老师：刘阳华"
    depth.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    proposals = propose_library_renames(catalog, depth)["proposals"]
    assert len(proposals) == 2
    assert any(Path(item["new_path"]).stem.endswith("-" + item["expected_sha256"][:7]) for item in proposals)


def test_category_collision_does_not_adopt_unverified_body_excerpt(tmp_path: Path) -> None:
    catalog, depth, source = _fixture(tmp_path)
    (catalog.parent / "竞赛与答辩/竞赛与答辩-封面版式.pptx").write_bytes(b"existing name")
    record = json.loads(depth.read_text())
    record["slides"][0].update(title="请插入您的大标题", title_source="largest_text", title_confidence=0.45)
    record["slides"][1]["text_excerpt"] = "比赛名称：请在此处填写自己的项目"
    depth.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    proposal = propose_library_renames(catalog, depth)["proposals"][0]
    assert proposal["naming_basis"] == "category_fallback"
    assert "比赛名称" not in proposal["new_path"]
    assert Path(proposal["new_path"]).stem.endswith("-" + proposal["expected_sha256"][:7])


def test_category_packaging_words_do_not_hide_specific_theme(tmp_path: Path) -> None:
    catalog, depth, source = _fixture(tmp_path)
    renamed = catalog.parent / "党政党建/国防军队/建军节卡通可爱军人部队PPT模板/001.pptx"
    renamed.parent.mkdir(parents=True)
    source.rename(renamed)
    row = json.loads(catalog.read_text())
    row["path"] = renamed.relative_to(catalog.parent).as_posix()
    catalog.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    record = json.loads(depth.read_text())
    record["path"] = row["path"]
    record["slides"][0].update(title="装饰单字", title_source="largest_text", title_confidence=0.4)
    record["source_bytes"] = renamed.stat().st_size
    record["mtime_ns"] = renamed.stat().st_mtime_ns
    depth.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    proposal = propose_library_renames(catalog, depth)["proposals"][0]
    assert proposal["naming_basis"] == "category_fallback"
    assert "建军节卡通可爱军人部队" in proposal["new_path"]


def test_body_slide_title_cannot_become_whole_deck_filename(tmp_path: Path) -> None:
    catalog, depth, _ = _fixture(tmp_path)
    record = json.loads(depth.read_text())
    record["slides"][0].update(title="高端PPT", title_source="largest_text", title_confidence=0.45)
    record["slides"][1].update(title="本项目已获16项专利产权", title_source="title_placeholder", title_confidence=0.96)
    depth.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    proposal = propose_library_renames(catalog, depth)["proposals"][0]
    assert proposal["naming_basis"] == "category_fallback"
    assert "专利" not in proposal["new_path"]


def test_failed_index_write_rolls_back_file_moves_and_prior_indexes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog, depth, source = _fixture(tmp_path)
    plan = propose_library_renames(catalog, depth)
    original_catalog = catalog.read_bytes()
    original_depth = depth.read_bytes()
    replace = Path.replace

    def fail_once(path: Path, target: Path) -> Path:
        if path.name == "DESIGN-STRUCTURE.jsonl.rename-tmp":
            raise OSError("simulated index replace failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_once)
    with pytest.raises(OSError, match="simulated"):
        apply_library_renames(plan, backup_dir=tmp_path / "backup")
    assert source.exists()
    assert catalog.read_bytes() == original_catalog
    assert depth.read_bytes() == original_depth
    assert not (catalog.parent / "RENAME-ALIASES.jsonl").exists()


def test_full_migration_handles_swaps_noops_companions_and_flattens_alias_chains(tmp_path: Path) -> None:
    catalog, _depth, source = _fixture(tmp_path)
    library = catalog.parent
    other = library / "人物介绍/介绍.ppt"
    companion = library / "人物介绍/预览.JPG"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"legacy presentation")
    companion.write_bytes(b"paired preview")
    first_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    other_hash = hashlib.sha256(other.read_bytes()).hexdigest()
    companion_hash = hashlib.sha256(companion.read_bytes()).hexdigest()
    (library / "CONTENT-CURATION.jsonl").write_text(
        json.dumps({"path": "竞赛与答辩/7798-20240929.pptx", "content_category": "竞赛"}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    (library / "RENAME-ALIASES.jsonl").write_text("\n".join([
        json.dumps({"schema": "aippt.library-path-alias.v1", "old_path": "oldest/old.pptx",
                    "current_path": "竞赛与答辩/7798-20240929.pptx", "source_sha256": first_hash}),
        json.dumps({"schema": "aippt.library-path-alias.v1", "old_path": "older/old.pptx",
                    "current_path": "竞赛与答辩/7798-20240929.pptx", "source_sha256": first_hash}),
    ]) + "\n", encoding="utf-8")
    plan = {"schema": "aippt.library-migration-plan.v1", "status": "planned", "catalog": str(catalog),
            "total_files": 3, "files": [
                {"old_path": source.relative_to(library).as_posix(), "new_path": "人物介绍/介绍.pptx", "sha256": first_hash},
                {"old_path": other.relative_to(library).as_posix(), "new_path": "竞赛与答辩/7798-20240929.pptx", "sha256": other_hash},
                {"old_path": companion.relative_to(library).as_posix(), "new_path": companion.relative_to(library).as_posix(), "sha256": companion_hash},
            ]}
    manifest = apply_library_migration(plan, backup_dir=tmp_path / "migration-backup")
    assert manifest["moved_count"] == 2
    assert (library / "人物介绍/介绍.pptx").read_bytes() == b"current deck bytes"
    assert (library / "竞赛与答辩/7798-20240929.pptx").read_bytes() == b"legacy presentation"
    assert (library / "人物介绍/预览.JPG").read_bytes() == b"paired preview"
    assert json.loads(catalog.read_text()) ["category"] == "人物介绍"
    structure = json.loads((library / "DESIGN-STRUCTURE.jsonl").read_text())
    assert structure["source_path"] == "人物介绍/介绍.pptx"
    assert structure["category"] == "人物介绍"
    assert structure["category_group"] == "人物介绍"
    curation = json.loads((library / "CONTENT-CURATION.jsonl").read_text())
    assert curation["path"] == "人物介绍/介绍.pptx"
    aliases = [json.loads(line) for line in (library / "RENAME-ALIASES.jsonl").read_text().splitlines()]
    assert {item["current_path"] for item in aliases if item["old_path"].startswith("old")} == {"人物介绍/介绍.pptx"}


def test_full_migration_rolls_back_every_move_and_index_if_commit_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog, _depth, source = _fixture(tmp_path)
    library = catalog.parent
    second = library / "other/second.pdf"
    second.parent.mkdir()
    second.write_bytes(b"pdf bytes")
    rows = [{"old_path": path.relative_to(library).as_posix(), "new_path": dest, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path, dest in [(source, "new/source.pptx"), (second, "new/second.pdf")]]
    plan = {"schema": "aippt.library-migration-plan.v1", "status": "planned", "catalog": str(catalog), "files": rows}
    catalog_before = catalog.read_bytes()
    replace = Path.replace

    def fail_commit(path: Path, target: Path) -> Path:
        if path.name == "DESIGN-STRUCTURE.jsonl.migration-tmp":
            raise OSError("simulated full migration index failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_commit)
    with pytest.raises(OSError, match="full migration index failure"):
        apply_library_migration(plan, backup_dir=tmp_path / "failed-migration")
    assert source.exists() and second.exists()
    assert not (library / "new/source.pptx").exists() and not (library / "new/second.pdf").exists()
    assert catalog.read_bytes() == catalog_before


@pytest.mark.parametrize("suffix", [".migration-tmp", ".migration-rollback-tmp"])
def test_migration_fails_closed_on_preexisting_transaction_temp(tmp_path: Path, suffix: str) -> None:
    catalog, _depth, source = _fixture(tmp_path)
    library = catalog.parent
    plan = {"schema": "aippt.library-migration-plan.v1", "status": "planned", "catalog": str(catalog),
            "files": [{"old_path": source.relative_to(library).as_posix(), "new_path": "new/renamed.pptx",
                       "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}
    orphan = library / f"CATALOG.jsonl{suffix}"
    orphan.write_bytes(b"leave this untouched")
    before_catalog = catalog.read_bytes()
    with pytest.raises(ValueError, match="transaction path already exists"):
        apply_library_migration(plan, backup_dir=tmp_path / "backup")
    assert orphan.read_bytes() == b"leave this untouched"
    assert source.exists() and catalog.read_bytes() == before_catalog
    assert not (tmp_path / "backup").exists()


def test_pure_noop_migration_refuses_index_rewrite(tmp_path: Path) -> None:
    catalog, _depth, source = _fixture(tmp_path)
    library = catalog.parent
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    before = catalog.read_bytes()
    plan = {"schema": "aippt.library-migration-plan.v1", "status": "planned", "catalog": str(catalog),
            "files": [{"old_path": source.relative_to(library).as_posix(),
                       "new_path": source.relative_to(library).as_posix(), "sha256": digest}]}
    with pytest.raises(ValueError, match="contains no moves"):
        apply_library_migration(plan, backup_dir=tmp_path / "noop-backup")
    assert catalog.read_bytes() == before and source.exists()
    assert not (tmp_path / "noop-backup").exists()


def test_migration_rejects_symlinked_source_parent_and_duplicate_real_sources(tmp_path: Path) -> None:
    catalog, _depth, source = _fixture(tmp_path)
    library = catalog.parent
    (library / "linked").symlink_to(source.parent, target_is_directory=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    plan = {"schema": "aippt.library-migration-plan.v1", "status": "planned", "catalog": str(catalog),
            "files": [{"old_path": "linked/7798-20240929.pptx", "new_path": "new/a.pptx", "sha256": digest}]}
    with pytest.raises(ValueError, match="symlink component"):
        apply_library_migration(plan, backup_dir=tmp_path / "symlink-backup")

    plan["files"] = [
        {"old_path": "竞赛与答辩/7798-20240929.pptx", "new_path": "new/a.pptx", "sha256": digest},
        {"old_path": "linked/7798-20240929.pptx", "new_path": "new/b.pptx", "sha256": digest},
    ]
    with pytest.raises(ValueError, match="symlink component"):
        apply_library_migration(plan, backup_dir=tmp_path / "duplicate-backup")
    assert source.exists()


def test_migration_rejects_symlinked_indexes_and_backup_inside_library(tmp_path: Path) -> None:
    catalog, _depth, source = _fixture(tmp_path)
    library = catalog.parent
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    plan = {"schema": "aippt.library-migration-plan.v1", "status": "planned", "catalog": str(catalog),
            "files": [{"old_path": source.relative_to(library).as_posix(), "new_path": "new/a.pptx", "sha256": digest}]}
    outside = tmp_path / "outside-index.jsonl"
    outside.write_text("must stay intact\n", encoding="utf-8")
    design = library / "DESIGN-STRUCTURE.jsonl"
    design.unlink()
    design.symlink_to(outside)
    with pytest.raises(ValueError, match="managed index cannot be a symlink"):
        apply_library_migration(plan, backup_dir=tmp_path / "backup")
    assert outside.read_text(encoding="utf-8") == "must stay intact\n"
    assert source.exists() and not (tmp_path / "backup").exists()

    design.unlink()
    design.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="outside the library tree"):
        apply_library_migration(plan, backup_dir=library / "migration-backup")
    assert source.exists() and not (library / "migration-backup").exists()


def test_migration_rejects_unicode_normalized_target_collision(tmp_path: Path) -> None:
    catalog, _depth, source = _fixture(tmp_path)
    library = catalog.parent
    retained = library / "new/Café.pptx"
    retained.parent.mkdir()
    retained.write_bytes(b"retained")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    plan = {"schema": "aippt.library-migration-plan.v1", "status": "planned", "catalog": str(catalog),
            "files": [{"old_path": source.relative_to(library).as_posix(),
                       "new_path": "new/Cafe\u0301.pptx", "sha256": digest}]}
    with pytest.raises(ValueError, match="occupied migration target"):
        apply_library_migration(plan, backup_dir=tmp_path / "backup")
    assert source.exists() and retained.read_bytes() == b"retained"
    assert not (tmp_path / "backup").exists()


def test_alias_rewrite_applies_simultaneous_mapping_once(tmp_path: Path) -> None:
    catalog, _depth, source_a = _fixture(tmp_path)
    library = catalog.parent
    source_b = library / "second/B.ppt"
    source_b.parent.mkdir()
    source_b.write_bytes(b"second")
    path_a = source_a.relative_to(library).as_posix()
    path_b = source_b.relative_to(library).as_posix()
    digest_a = hashlib.sha256(source_a.read_bytes()).hexdigest()
    digest_b = hashlib.sha256(source_b.read_bytes()).hexdigest()
    aliases_path = library / "RENAME-ALIASES.jsonl"
    aliases_path.write_text("\n".join(json.dumps({"schema": "aippt.library-path-alias.v1", "old_path": old,
                                                    "current_path": current, "source_sha256": digest_a})
                                      for old, current in [("historic/A.pptx", path_a), ("historic/B.ppt", path_b)]) + "\n",
                             encoding="utf-8")
    plan = {"schema": "aippt.library-migration-plan.v1", "status": "planned", "catalog": str(catalog),
            "files": [{"old_path": path_a, "new_path": path_b, "sha256": digest_a},
                      {"old_path": path_b, "new_path": "third/C.ppt", "sha256": digest_b}]}
    apply_library_migration(plan, backup_dir=tmp_path / "alias-backup")
    aliases = [json.loads(line) for line in aliases_path.read_text().splitlines()]
    history = {row["old_path"]: row["current_path"] for row in aliases if row["old_path"].startswith("historic/")}
    assert history == {"historic/A.pptx": path_b, "historic/B.ppt": "third/C.ppt"}


def test_failed_alias_commit_preserves_preexisting_empty_alias_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    catalog, _depth, source = _fixture(tmp_path)
    library = catalog.parent
    aliases = library / "RENAME-ALIASES.jsonl"
    aliases.write_bytes(b"")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    plan = {"schema": "aippt.library-migration-plan.v1", "status": "planned", "catalog": str(catalog),
            "files": [{"old_path": source.relative_to(library).as_posix(), "new_path": "new/a.pptx", "sha256": digest}]}
    replace = Path.replace

    def fail_alias(path: Path, target: Path) -> Path:
        if path.name == "RENAME-ALIASES.jsonl.migration-tmp":
            raise OSError("simulated alias commit failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_alias)
    with pytest.raises(OSError, match="alias commit failure"):
        apply_library_migration(plan, backup_dir=tmp_path / "empty-alias-backup")
    assert aliases.exists() and aliases.read_bytes() == b""
    assert source.exists() and not (library / "new/a.pptx").exists()


def test_migration_baseline_source_set_is_independently_checked(tmp_path: Path) -> None:
    catalog, _depth, source = _fixture(tmp_path)
    library = catalog.parent
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    plan = {"schema": "aippt.library-migration-plan.v1", "status": "planned", "catalog": str(catalog),
            "baseline_sources": [{"path": "extra/missing.pptx", "sha256": "0" * 64}],
            "files": [{"old_path": source.relative_to(library).as_posix(), "new_path": "new/a.pptx", "sha256": digest}]}
    with pytest.raises(ValueError, match="independent baseline source set"):
        apply_library_migration(plan, backup_dir=tmp_path / "baseline-backup")
    assert source.exists() and not (tmp_path / "baseline-backup").exists()
