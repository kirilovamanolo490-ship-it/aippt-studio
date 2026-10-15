from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from aippt_studio import cli


def test_work_storage_cli_is_read_only_and_rejects_nonwork_path(tmp_path: Path, monkeypatch):
    studio = tmp_path / "aippt-studio"
    studio.mkdir()
    work = tmp_path / "work" / "4"
    (work / "workspace").mkdir(parents=True)
    file = work / "workspace" / "fact.txt"
    file.write_text("source", encoding="utf-8")
    monkeypatch.setattr(cli, "PROJECT_ROOT", studio)

    passed = CliRunner().invoke(cli.app, ["work-storage", "--work-dir", str(work), "--json"])
    assert passed.exit_code == 0, passed.output
    report = json.loads(passed.output)
    assert report["total_logical_bytes"] == file.stat().st_size
    assert file.read_text(encoding="utf-8") == "source"
    retention = CliRunner().invoke(cli.app, ["work-storage", "--work-dir", str(work), "--retention-plan", "--json"])
    assert retention.exit_code == 0, retention.output
    assert json.loads(retention.output)["status"] == "dry_run_only"
    assert json.loads(retention.output)["deletion_performed"] is False
    assert file.read_text(encoding="utf-8") == "source"

    rejected = CliRunner().invoke(cli.app, ["work-storage", "--work-dir", str(studio), "--json"])
    assert rejected.exit_code == 2
