from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from aippt_studio.qa_dedup import DedupError, _apfs_clone, deduplicate_completed_work_qa


def _completed_work(tmp_path: Path, status: str = "completed") -> Path:
    work = tmp_path / "work" / "32"
    qa = work / "workspace" / "qa"
    qa.mkdir(parents=True)
    (work / "work.json").write_text(json.dumps({"status": status}), encoding="utf-8")
    return work


def test_dry_run_finds_only_exact_duplicates_and_changes_nothing(tmp_path: Path) -> None:
    work = _completed_work(tmp_path)
    qa = work / "workspace" / "qa"
    first, second, different = qa / "a.bin", qa / "b.bin", qa / "c.bin"
    first.write_bytes(b"same bytes")
    second.write_bytes(b"same bytes")
    different.write_bytes(b"diff bytes")
    before = {path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
              for path in (first, second, different)}

    report = deduplicate_completed_work_qa(work)

    assert report["status"] == "dry_run"
    assert report["group_count"] == 1
    assert report["groups"][0]["files"][0]["path"] == "a.bin"
    assert report["groups"][0]["files"][1]["path"] == "b.bin"
    assert not list(qa.glob("qa-dedup-*.json"))
    assert {path.name: (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns)
            for path in (first, second, different)} == before


def test_non_completed_work_is_refused_before_scan_or_receipt(tmp_path: Path) -> None:
    work = _completed_work(tmp_path, "review")
    qa = work / "workspace" / "qa"
    (qa / "a").write_bytes(b"dupe")
    (qa / "b").write_bytes(b"dupe")

    with pytest.raises(DedupError, match="status=completed"):
        deduplicate_completed_work_qa(work, apply=True, clone=shutil.copyfile)
    assert not list(qa.glob("qa-dedup-*.json"))


def test_apply_replaces_only_duplicate_inode_and_records_receipt(tmp_path: Path) -> None:
    work = _completed_work(tmp_path)
    qa = work / "workspace" / "qa"
    canonical, duplicate = qa / "a.bin", qa / "b.bin"
    canonical.write_bytes(b"same bytes" * 200)
    duplicate.write_bytes(canonical.read_bytes())
    duplicate.chmod(0o640)
    original_inode = duplicate.stat().st_ino
    original_mtime = duplicate.stat().st_mtime_ns
    before_names = sorted(path.name for path in qa.iterdir())

    report = deduplicate_completed_work_qa(work, apply=True, clone=shutil.copyfile)

    assert report["status"] == "applied"
    assert duplicate.read_bytes() == canonical.read_bytes()
    assert duplicate.stat().st_ino != original_inode
    assert duplicate.stat().st_mode & 0o777 == 0o640
    assert duplicate.stat().st_mtime_ns == original_mtime
    assert sorted(path.name for path in qa.iterdir()) == sorted(before_names + [Path(report["receipt_path"]).name])
    assert report["operations"][0]["duplicate_before"]["identity"]["inode"] == original_inode
    assert report["operations"][0]["duplicate_after"]["sha256"] == report["groups"][0]["sha256"]
    receipt = json.loads((work / report["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "applied"
    assert len(receipt["operations"]) == 1


def test_apply_refuses_bad_clone_hash_and_keeps_original_path_bytes(tmp_path: Path) -> None:
    work = _completed_work(tmp_path)
    qa = work / "workspace" / "qa"
    canonical, duplicate = qa / "a.bin", qa / "b.bin"
    canonical.write_bytes(b"same bytes")
    duplicate.write_bytes(b"same bytes")
    before = duplicate.read_bytes()
    original_inode = duplicate.stat().st_ino

    def bad_clone(_source: Path, destination: Path) -> None:
        destination.write_bytes(b"bad bytes!")

    with pytest.raises(DedupError, match="clone hash"):
        deduplicate_completed_work_qa(work, apply=True, clone=bad_clone)

    assert duplicate.read_bytes() == before
    assert duplicate.stat().st_ino == original_inode
    receipts = list(qa.glob("qa-dedup-*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text(encoding="utf-8"))["status"] == "failed"


def test_apply_detects_duplicate_path_changed_during_clone(tmp_path: Path) -> None:
    work = _completed_work(tmp_path)
    qa = work / "workspace" / "qa"
    canonical, duplicate = qa / "a.bin", qa / "b.bin"
    canonical.write_bytes(b"same bytes")
    duplicate.write_bytes(b"same bytes")

    def racing_clone(source: Path, destination: Path) -> None:
        shutil.copyfile(source, destination)
        duplicate.write_bytes(b"new bytes!")

    with pytest.raises(DedupError, match="path changed during clone"):
        deduplicate_completed_work_qa(work, apply=True, clone=racing_clone)
    assert duplicate.read_bytes() == b"new bytes!"
    assert not list(qa.glob(".qa-rollback-*"))


def test_apply_retains_old_bytes_when_verification_and_rollback_both_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aippt_studio import qa_dedup

    work = _completed_work(tmp_path)
    qa = work / "workspace" / "qa"
    original, duplicate = qa / "a.bin", qa / "b.bin"
    original.write_bytes(b"original bytes" * 100)
    duplicate.write_bytes(original.read_bytes())
    duplicate_inode = duplicate.stat().st_ino
    real_metadata = qa_dedup._metadata
    real_replace = os.replace

    def changed_metadata(path: Path):
        result = real_metadata(path)
        if path == duplicate and path.stat().st_ino != duplicate_inode:
            return {**result, "mode": result["mode"] ^ 0o100}
        return result

    def failed_rollback(source: Path, destination: Path):
        if source.name.startswith(".qa-rollback-") and destination == duplicate:
            raise OSError("simulated rollback failure")
        return real_replace(source, destination)

    monkeypatch.setattr(qa_dedup, "_metadata", changed_metadata)
    monkeypatch.setattr(qa_dedup.os, "replace", failed_rollback)
    with pytest.raises(DedupError, match="retain recovery backup"):
        deduplicate_completed_work_qa(work, apply=True, clone=shutil.copyfile)

    receipts = list(qa.glob("qa-dedup-*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["status"] == "failed"
    backup = work / receipt["recovery_backups"][0]["path"]
    assert receipt["recovery_backups"][0]["present"] is True
    assert backup.is_file()
    assert backup.read_bytes() == original.read_bytes()


def test_only_sha256_limits_changes_to_one_duplicate_group(tmp_path: Path) -> None:
    work = _completed_work(tmp_path)
    qa = work / "workspace" / "qa"
    for name, payload in (("a", b"A" * 20), ("b", b"A" * 20),
                          ("c", b"B" * 20), ("d", b"B" * 20)):
        (qa / name).write_bytes(payload)
    full = deduplicate_completed_work_qa(work)
    assert full["group_count"] == 2
    chosen = full["groups"][0]["sha256"]
    unchosen_inode = (qa / "d").stat().st_ino

    result = deduplicate_completed_work_qa(work, apply=True, only_sha256=chosen,
                                           clone=shutil.copyfile)
    assert result["status"] == "applied"
    assert result["only_sha256"] == chosen
    assert result["group_count"] == 1
    assert len(result["operations"]) == 1
    assert (qa / "d").stat().st_ino == unchosen_inode


@pytest.mark.skipif(__import__("os").uname().sysname != "Darwin", reason="requires macOS clonefile")
def test_native_apfs_clonefile_works_in_temporary_directory(tmp_path: Path) -> None:
    source, clone = tmp_path / "source", tmp_path / "clone"
    source.write_bytes(b"temporary APFS probe")

    _apfs_clone(source, clone)

    assert clone.read_bytes() == source.read_bytes()
    assert clone.stat().st_ino != source.stat().st_ino
