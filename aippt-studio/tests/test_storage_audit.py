from __future__ import annotations

import json
from pathlib import Path

from aippt_studio.storage_audit import audit_work_storage


def test_storage_audit_reports_logical_categories_and_conversation_duplicates(tmp_path: Path) -> None:
    work = tmp_path / "work" / "7"
    (work / "workspace" / "conversations").mkdir(parents=True)
    (work / "output").mkdir()
    snapshot = work / "workspace" / "conversations" / "snap.jsonl"
    snapshot.write_text('{"type":"session_meta"}\n', encoding="utf-8")
    pptx = work / "output" / "final.pptx"
    pptx.write_bytes(b"pptx-data")
    manifest = {
        "threads": {
            "thread-a": {"snapshots": [{"raw_path": "snap-a.jsonl", "bytes": 120,
                                         "sha256": "a" * 64, "append_only_from_previous": False}]},
            "thread-b": {"snapshots": [{"raw_path": "snap-b.jsonl", "bytes": 120,
                                         "sha256": "a" * 64, "append_only_from_previous": False}]},
            "thread-c": {"snapshots": [{"raw_path": "snap-c.jsonl", "bytes": 50,
                                         "sha256": "b" * 64, "append_only_from_previous": True}]},
        }
    }
    manifest_path = work / "workspace" / "conversations" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = {path: path.read_bytes() for path in (snapshot, pptx, manifest_path)}

    report = audit_work_storage(work)

    assert report["status"] == "read_only"
    assert report["measurement"].startswith("logical file lengths")
    assert report["by_area_logical_bytes"]["output"] == len(b"pptx-data")
    assert report["powerpoint_file_count"] == 1
    assert report["powerpoint_logical_bytes"] == len(b"pptx-data")
    archive = report["conversation_archive"]
    assert archive["snapshot_count"] == 3
    assert archive["snapshot_logical_bytes_from_manifest"] == 290
    assert archive["append_only_snapshot_count"] == 1
    assert archive["cross_thread_duplicate_snapshot_count"] == 1
    assert archive["cross_thread_duplicate_reuse_bytes_estimate"] == 120
    assert {path: path.read_bytes() for path in before} == before


def test_storage_audit_skips_symlinks_and_requires_work_workspace(tmp_path: Path) -> None:
    work = tmp_path / "work" / "8"
    (work / "workspace").mkdir(parents=True)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret bytes")
    (work / "workspace" / "linked.bin").symlink_to(outside)

    report = audit_work_storage(work)

    assert report["symlink_count_skipped"] == 1
    assert report["total_logical_bytes"] == 0
