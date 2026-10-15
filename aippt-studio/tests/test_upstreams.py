from pathlib import Path
import json
import subprocess

import pytest

from aippt_studio.upstreams import upstream_report
from aippt_studio.adapters.registry import reviewed_commit
from aippt_studio.support import workspace_output, render_support
from aippt_studio.assets.registry import load_asset_registry


def test_checkout_verification_detects_source_change_and_wrong_origin(tmp_path):
    repo = tmp_path / "github" / "example"
    repo.mkdir(parents=True)
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()
    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("config", "core.autocrlf", "false")
    (repo / "code.py").write_text("print('original')\n")
    git("add", "code.py")
    git("-c", "core.hooksPath=/dev/null", "commit", "-m", "fixture")
    git("remote", "add", "origin", "https://github.com/example/example.git")
    project = tmp_path / "studio"
    project.mkdir()
    (project / "upstreams.lock.json").write_text(json.dumps({"upstreams": [{"id": "example", "directory": "example", "source_url": "https://github.com/example/example", "commit_sha": git("rev-parse", "HEAD"), "lifecycle": "active"}]}))
    check = lambda: upstream_report(verify=True, project_root=project, source_root=tmp_path / "github")[0]["verification"]
    assert check()["matches_lock"]
    (repo / "code.py").write_text("print('changed')\n")
    assert not check()["matches_lock"]
    git("restore", "code.py")
    git("remote", "set-url", "origin", "https://github.com/other/example.git")
    assert not check()["matches_lock"]


def test_new_source_checkout_does_not_relabel_pinned_package_runtime(tmp_path):
    (tmp_path / "upstreams.lock.json").write_text(json.dumps({"upstreams": [{"id": "dom-to-pptx", "commit_sha": "new-source", "runtime": {"reviewed_commit": "tested-runtime"}, "lifecycle": "active"}]}))
    assert reviewed_commit(tmp_path, "dom-to-pptx") == "tested-runtime"


def test_support_rejects_output_outside_active_work_and_symlink_escape(tmp_path, monkeypatch):
    workspace = tmp_path / "work" / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AIPPT_JOBS_DIR", str(workspace / "studio-jobs"))
    assert workspace_output(workspace / "chart.svg") == workspace / "chart.svg"
    with pytest.raises(ValueError, match="inside"):
        workspace_output(tmp_path / "github" / "chart.svg")
    (workspace / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="inside"):
        workspace_output(workspace / "escape" / "chart.svg")


def test_support_failure_preserves_existing_output(tmp_path, monkeypatch):
    from aippt_studio import support
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("AIPPT_JOBS_DIR", str(workspace / "jobs"))
    target = workspace / "chart.svg"
    target.write_text("previous valid output")
    source = workspace / "bad.json"
    source.write_text("invalid input")
    package = tmp_path / ".tools/extension-runtime/node_modules/echarts"
    package.mkdir(parents=True)
    (package / "package.json").write_text('{"version":"test"}')
    monkeypatch.setattr(support, "PROJECT_ROOT", tmp_path)
    # The renderer now accepts only a host-validated Node probe.  Keep this
    # regression focused on its original contract: a failing staged renderer
    # must leave an already-published SVG untouched.
    monkeypatch.setattr(support, "probe_node_runtime", lambda: {"status": "passed", "path": "/fixture/node"})
    monkeypatch.setattr(support.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess([], 1, "", "invalid JSON"))
    with pytest.raises(ValueError, match="invalid JSON"):
        render_support("chart", source, target)
    assert target.read_text() == "previous valid output"


def test_asset_registry_resolves_paths_relative_to_lock(tmp_path, monkeypatch):
    project = tmp_path / "studio"
    project.mkdir()
    fonts = tmp_path / "github" / "fonts"
    fonts.mkdir(parents=True)
    lock = project / "upstreams.lock.json"
    lock.write_text(json.dumps({"upstreams": [{"id": "font", "integration": "font_asset", "license": {"spdx": "OFL-1.1"}, "acquisition": {"local_path": "../github/fonts", "status": "present_git"}, "lifecycle": "active"}]}))
    monkeypatch.chdir(tmp_path.parent)
    assert load_asset_registry(lock).get("font").path == str(fonts)


def test_retired_upstream_is_reported_but_never_present_or_checked_out(tmp_path):
    from aippt_studio.upstreams import upstream_report

    project = tmp_path / "studio"
    project.mkdir()
    source_root = tmp_path / "github"
    retired = source_root / "old-tool"
    retired.mkdir(parents=True)
    (project / "upstreams.lock.json").write_text(json.dumps({"upstreams": [{
        "id": "old-tool", "directory": "old-tool", "source_url": "https://github.com/example/old-tool",
        "commit_sha": "retired-sha", "lifecycle": "retired",
        "retirement": {"retired_at": "2026-10-02", "reason": "not used", "restore_guidance": "restore source checkout"},
    }]}))

    row = upstream_report(project_root=project, source_root=source_root)[0]
    assert row["lifecycle"] == "retired"
    assert row["source_present"] is False
    assert row["path"] is None
    assert row["retired_path"] == str(retired.resolve())
    assert row["retirement"]["restore_guidance"] == "restore source checkout"

    checked = upstream_report(verify=True, project_root=project, source_root=source_root)[0]
    assert checked["verification"]["status"] == "retired"
    assert checked["verification"]["matches_lock"] is None


def test_upstreams_check_skips_retired_source_but_checks_active_rows(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from aippt_studio import cli
    from aippt_studio import upstreams as upstream_module

    project = tmp_path / "studio"
    project.mkdir()
    (project / "upstreams.lock.json").write_text(json.dumps({"upstreams": [{
        "id": "retired-tool", "directory": "retired-tool", "source_url": "https://github.com/example/retired-tool",
        "commit_sha": "retired-sha", "lifecycle": "retired",
        "retirement": {"retired_at": "2026-10-02", "reason": "unused", "restore_guidance": "restore checkout"},
    }]}))
    retired_row = {
        "id": "retired-tool", "lifecycle": "retired", "source_present": False,
        "acquisition_status": "present_git", "verification": {"status": "retired", "matches_lock": None},
    }
    monkeypatch.setattr(upstream_module, "upstream_report", lambda **kwargs: [retired_row])

    result = CliRunner().invoke(cli.app, ["upstreams", "--check", "--json"])
    assert result.exit_code == 0, result.output
    assert '"lifecycle": "retired"' in result.output

    active_row = {"id": "active-tool", "lifecycle": "active", "acquisition_status": "present_git",
                  "verification": {"status": "verified", "matches_lock": False}}
    monkeypatch.setattr(upstream_module, "upstream_report", lambda **kwargs: [retired_row, active_row])
    active_failure = CliRunner().invoke(cli.app, ["upstreams", "--check", "--json"])
    assert active_failure.exit_code == 1


def test_retired_font_is_not_registered_as_available_asset(tmp_path):
    project = tmp_path / "studio"
    project.mkdir()
    fonts = tmp_path / "github" / "old-font"
    fonts.mkdir(parents=True)
    lock = project / "upstreams.lock.json"
    lock.write_text(json.dumps({"upstreams": [{"id": "old-font", "integration": "font_asset",
        "source_url": "https://github.com/example/old-font", "lifecycle": "retired",
        "retirement": {"retired_at": "2026-10-02", "reason": "unused", "restore_guidance": "restore source"},
        "license": {"spdx": "OFL-1.1", "evidence": "OFL.txt"},
        "acquisition": {"local_path": str(fonts), "status": "present_git"}}]}))

    registry = load_asset_registry(lock)
    assert registry.report() == []
