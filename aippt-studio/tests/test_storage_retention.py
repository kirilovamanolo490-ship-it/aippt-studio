from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aippt_studio.storage_audit import plan_work_storage_retention


def test_retention_plan_protects_sources_outputs_code_and_receipt_bound_files(tmp_path: Path) -> None:
    work = tmp_path / "work" / "1"
    files = {
        "input/source/source.txt": b"source",
        "output/final.pptx": b"deck",
        "workspace/build.py": b"print('build')",
        "workspace/qa/needed.json": b"{}",
        "workspace/cache/preview.png": b"preview",
        "workspace/mystery.bin": b"?",
    }
    for rel, data in files.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    receipt = {"status": "review", "production_files": [{"path": "workspace/qa/needed.json", "sha256": hashlib.sha256(b"{}").hexdigest()}]}
    (work / "workspace/production-receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    plan = plan_work_storage_retention(work)
    by_path = {item["path"]: item for item in plan["files"]}

    assert plan["status"] == "dry_run_only"
    assert plan["deletion_performed"] is False
    assert by_path["input/source/source.txt"]["status"] == "protected"
    assert by_path["output/final.pptx"]["status"] == "protected"
    assert by_path["workspace/build.py"]["category"] == "executable_code"
    assert by_path["workspace/qa/needed.json"]["category"] == "current_receipt_reference"
    assert by_path["workspace/cache/preview.png"]["status"] == "review_candidate"
    assert by_path["workspace/mystery.bin"]["status"] == "manual_review"


def test_retention_plan_marks_bad_receipt_and_symlink_as_unsafe(tmp_path: Path) -> None:
    work = tmp_path / "work" / "2"
    (work / "workspace").mkdir(parents=True)
    (work / "workspace/production-receipt.json").write_text("{broken", encoding="utf-8")
    (work / "workspace/cache.bin").write_bytes(b"cache")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")
    (work / "workspace/linked.bin").symlink_to(outside)

    plan = plan_work_storage_retention(work)
    by_path = {item["path"]: item for item in plan["files"]}

    assert plan["unsafe_reasons"]
    assert any("收据无法解析" in reason for reason in plan["unsafe_reasons"])
    assert any("符号链接" in reason for reason in plan["unsafe_reasons"])
    assert by_path["workspace/cache.bin"]["status"] == "manual_review"
    assert "workspace/linked.bin" not in by_path


def test_retention_plan_rejects_receipt_paths_outside_work(tmp_path: Path) -> None:
    work = tmp_path / "work" / "3"
    (work / "workspace").mkdir(parents=True)
    (work / "workspace/production-receipt.json").write_text(
        json.dumps({"request": {"path": "../../../outside.txt", "sha256": "a" * 64}}), encoding="utf-8"
    )
    (work / "workspace/cache.bin").write_bytes(b"cache")

    plan = plan_work_storage_retention(work)

    assert any("Work 外" in reason for reason in plan["unsafe_reasons"])
    assert next(item for item in plan["files"] if item["path"] == "workspace/cache.bin")["status"] == "manual_review"


def test_retention_plan_reads_manifest_and_resolves_local_and_work_absolute_paths(tmp_path: Path) -> None:
    work = tmp_path / "work" / "4"
    bundle = work / "workspace/studio-jobs/job/external-engine"
    bundle.mkdir(parents=True)
    local = bundle / "result.pptx"
    local.write_bytes(b"local result")
    absolute = work / "workspace/current.pptx"
    absolute.write_bytes(b"current")
    manifest = {
        "artifacts": [
            {"path": "result.pptx", "sha256": hashlib.sha256(b"local result").hexdigest()},
            {"path": str(absolute), "sha256": hashlib.sha256(b"current").hexdigest()},
        ]
    }
    (bundle / "external-engine-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    plan = plan_work_storage_retention(work)
    by_path = {item["path"]: item for item in plan["files"]}

    assert by_path[local.relative_to(work).as_posix()]["status"] == "protected"
    assert by_path[absolute.relative_to(work).as_posix()]["status"] == "protected"
    assert by_path[local.relative_to(work).as_posix()]["sha256"] == hashlib.sha256(b"local result").hexdigest()
    assert not any("Work 外" in reason for reason in plan["unsafe_reasons"])


def test_retention_plan_hashing_is_bounded_and_reports_deferred_reference(tmp_path: Path) -> None:
    work = tmp_path / "work" / "5"
    (work / "workspace/qa").mkdir(parents=True)
    (work / "workspace/qa/large.bin").write_bytes(b"x" * 32)
    (work / "workspace/qa/manifest.json").write_text(
        json.dumps({"file": {"path": "workspace/qa/large.bin", "sha256": "a" * 64}}), encoding="utf-8"
    )

    plan = plan_work_storage_retention(work, hash_budget_bytes=8, max_file_hash_bytes=16)
    item = next(entry for entry in plan["files"] if entry["path"] == "workspace/qa/large.bin")

    assert plan["hash_bytes_used"] <= 8
    assert item["sha256"] is None
    assert item["hash_status"] == "deferred_size_limit"
    assert any("SHA-256 上限" in reason for reason in plan["unsafe_reasons"])
