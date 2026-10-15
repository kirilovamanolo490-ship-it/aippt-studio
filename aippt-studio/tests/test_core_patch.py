from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from aippt_studio import core_patch as cp


def workspace(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    source = repo / "aippt-studio/src"
    tests = repo / "aippt-studio/tests"
    source.mkdir(parents=True)
    tests.mkdir()
    (source / "widget.py").write_text("def combine(a, b):\n    return a - b\n")
    (tests / "test_existing.py").write_text("from widget import combine\ndef test_identity():\n    assert combine(4, 0) == 4\n")
    (repo / "aippt-studio/pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
    return repo


def candidate(repo: Path, name="candidate"):
    stage = repo / "aippt-studio/.maintenance" / name
    manifest = cp.create_candidate(repo, stage)
    return stage, Path(manifest["candidate_dir"])


def fix(tree: Path, *, failing=True):
    (tree / "aippt-studio/src/widget.py").write_text("def combine(a, b):\n    return a + b\n")
    (tree / "aippt-studio/tests/test_regression.py").write_text(
        "from widget import combine\ndef test_nonzero_operand():\n    assert combine(4, " + ("2" if failing else "0") + ") == " + ("6" if failing else "4") + "\n")


def test_snapshot_is_independent_and_existing_test_bytes_are_preserved(tmp_path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    original = cp._read(stage / "manifest.json")
    fix(tree)
    assert (repo / "aippt-studio/src/widget.py").read_text().endswith("a - b\n")
    assert cp._tree_files(stage / "before", []) == original["source_files"]
    inspection = cp.inspect_candidate(stage)
    assert inspection["status"] == "candidate"
    assert inspection["new_tests"] == ["aippt-studio/tests/test_regression.py"]
    assert '-    return a - b' in inspection["patch"]
    assert '+    return a + b' in inspection["patch"]
    assert all(c["after"]["sha256"] for c in inspection["changes"])


@pytest.mark.parametrize("path", ["aippt-studio/tests/test_existing.py", "aippt-studio/tests/test_core_patch.py", "aippt-studio/tests/conftest.py",
    "aippt-studio/pyproject.toml", "aippt-studio/src/aippt_studio/core_patch.py",
    "aippt-studio/src/aippt_studio/core_evolution.py", "aippt-studio/src/aippt_studio/repair_context.py",
    "aippt-studio/src/aippt_studio/maintenance.py", "aippt-studio/src/aippt_studio/platform_runtime.py",
    "aippt-studio/src/aippt_studio/conversations.py", "scripts/finish-work.ps1",
    "scripts/with-studio-runtime.py", "work/37/work.json", "github/upstream/source.py",
    "aippt-studio/node/package.json", ".codex/config.toml", "AGENTS.md", ".agents/skills/aippt-studio/SKILL.md",
    "aippt-studio/src/aippt_studio/workflow.py", "aippt-studio/src/aippt_studio/storage.py",
    "aippt-studio/src/aippt_studio/models.py", "aippt-studio/src/aippt_studio/expression_review.py",
    "aippt-studio/src/aippt_studio/presentation_quality.py", "aippt-studio/src/aippt_studio/authoring_policy.py",
    "aippt-studio/src/aippt_studio/reconcile.py", "aippt-studio/src/aippt_studio/cli.py"])
def test_protected_paths_and_existing_tests_cannot_be_changed(tmp_path, path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    target = tree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("changed\n")
    result = cp.inspect_candidate(stage)
    assert result["status"] == "rejected"
    assert result["rejected"][0]["path"] == path


def test_deletion_permission_change_and_symlink_escape_are_rejected(tmp_path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    (tree / "aippt-studio/tests/test_existing.py").unlink()
    assert cp.inspect_candidate(stage)["status"] == "rejected"
    stage2, tree2 = candidate(repo, "mode")
    (tree2 / "aippt-studio/src/widget.py").chmod(0o755)
    assert "Permission" in cp.inspect_candidate(stage2)["rejected"][0]["reason"]
    stage3, tree3 = candidate(repo, "link")
    (tree3 / "aippt-studio/src/escape.py").symlink_to(repo / "aippt-studio/src/widget.py")
    with pytest.raises(ValueError, match="symlink"):
        cp.inspect_candidate(stage3)


def test_control_manifest_or_baseline_tampering_is_detected(tmp_path):
    repo = workspace(tmp_path)
    stage, _ = candidate(repo)
    manifest = cp._read(stage / "manifest.json")
    manifest["source_files"] = {}
    cp._write(stage / "manifest.json", manifest)
    with pytest.raises(ValueError, match="manifest changed"):
        cp.inspect_candidate(stage)
    stage2, _ = candidate(repo, "baseline")
    (stage2 / "before/aippt-studio/src/widget.py").write_text("forged baseline")
    with pytest.raises(ValueError, match="Baseline"):
        cp.inspect_candidate(stage2)


def test_candidate_cannot_live_in_customer_work(tmp_path):
    repo = workspace(tmp_path)
    with pytest.raises(ValueError, match="maintenance"):
        cp.create_candidate(repo, repo / "work/1/workspace/stage")


def test_no_regression_or_production_change_does_not_run_code(tmp_path, monkeypatch):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    (tree / "aippt-studio/src/widget.py").write_text("def combine(a,b): return a+b\n")
    monkeypatch.setattr(cp, "_sandbox_probe", lambda *a: pytest.fail("must not run an ineligible candidate"))
    assert cp.validate_candidate(stage)["status"] == "rejected"


@pytest.mark.parametrize("change_mode", [False, True])
def test_parallel_source_change_defers_before_testing(tmp_path, monkeypatch, change_mode):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    original = repo / "aippt-studio/src/widget.py"
    if change_mode:
        original.chmod(0o755)
    else:
        original.write_text("a parallel user's edit\n")
    monkeypatch.setattr(cp, "_sandbox_probe", lambda *a: pytest.fail("must preserve parallel source changes"))
    assert cp.validate_candidate(stage)["status"] == "deferred"


def test_unavailable_sandbox_never_falls_back_to_unrestricted_tests(tmp_path, monkeypatch):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    monkeypatch.setattr(cp, "_sandbox_probe", lambda *a: {"status": "unavailable"})
    monkeypatch.setattr(cp, "_run_tests", lambda *a: pytest.fail("unrestricted fallback"))
    assert cp.validate_candidate(stage)["status"] == "deferred"


requires_macos = pytest.mark.skipif(
    sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").exists() or os.environ.get("AIPPT_CORE_PATCH_VALIDATION") == "1",
    reason="Actual macOS isolation required; macOS forbids nested sandbox_apply. The immutable controller runs these same cases separately and records JUnit evidence.")


@requires_macos
def test_real_sandbox_denies_external_writes_and_network(tmp_path):
    result = cp._sandbox_probe(tmp_path / "probe", sys.executable)
    assert result["status"] == "verified", result
    assert result["checks"]["outside_write_denied"] and result["checks"]["network_denied"]
    assert result["checks"]["confined_unix_socket_allowed"]
    assert result["checks"]["outside_unix_socket_denied"]


@requires_macos
def test_real_before_failure_after_full_pass_and_application(tmp_path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    original_test = (repo / "aippt-studio/tests/test_existing.py").read_bytes()
    validated = cp.validate_candidate(stage, timeout_seconds=60)
    assert validated["status"] == "passed", validated
    # These fixed controller tests use a minimal widget repository without the
    # controller test module, so validation cannot recursively launch itself.
    assert validated["controller_selftests"]["status"] == "not_applicable"
    assert validated["before_new_tests"]["junit"]["counts"]["failed"] == 1
    assert validated["after"]["junit"]["counts"]["passed"] == 2
    applied = cp.apply_candidate(repo, stage)
    assert applied["status"] == "applied", applied
    assert applied["commands_executed_in_main_workspace"] == []
    assert (repo / "aippt-studio/src/widget.py").read_bytes() == (tree / "aippt-studio/src/widget.py").read_bytes()
    assert (repo / "aippt-studio/tests/test_existing.py").read_bytes() == original_test
    assert cp.apply_candidate(repo, stage)["status"] == "already_applied"


@requires_macos
def test_new_test_that_does_not_reproduce_before_failure_is_rejected(tmp_path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree, failing=False)
    assert cp.validate_candidate(stage, timeout_seconds=60)["status"] == "rejected"


def mark_validated_for_application_test(stage):
    """Application unit tests isolate commit behavior; real runner has its own test."""
    inspection = cp.inspect_candidate(stage)
    manifest = cp._read(stage / "manifest.json")
    files = cp._tree_files(stage / "candidate", manifest["dependencies"])
    cp._copy_files(stage / "candidate", stage / "validation/after/tree", files)
    cp._link_dependencies(stage / "validation/after/tree", manifest["dependencies"])
    validation = {"status": "passed", "manifest_sha256": cp._digest(manifest),
                  "candidate_files_sha256": inspection["candidate_files_sha256"]}
    cp._write(stage / "validation.json", validation)
    registry = cp._registry(Path(manifest["repo_root"]), stage)
    trusted = cp._read(registry)
    trusted["validation_sha256"] = cp._digest(validation)
    cp._write(registry, trusted)


def test_apply_rechecks_source_and_candidate_hashes(tmp_path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    mark_validated_for_application_test(stage)
    source = repo / "aippt-studio/src/widget.py"
    source.write_text("parallel edit")
    assert cp.apply_candidate(repo, stage)["status"] == "deferred"
    assert source.read_text() == "parallel edit"
    stage2, tree2 = candidate(repo, "candidate-two")
    fix(tree2)
    mark_validated_for_application_test(stage2)
    (tree2 / "aippt-studio/src/widget.py").write_text("changed after validation")
    assert cp.apply_candidate(repo, stage2)["status"] == "deferred"


def test_apply_uses_frozen_validated_bytes_and_rejects_tampered_receipt(tmp_path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    mark_validated_for_application_test(stage)
    (stage / "validation/after/tree/aippt-studio/src/widget.py").write_text("tamper")
    assert cp.apply_candidate(repo, stage)["status"] == "deferred"
    stage2, tree2 = candidate(repo, "receipt")
    fix(tree2)
    mark_validated_for_application_test(stage2)
    receipt = cp._read(stage2 / "validation.json")
    receipt["reason"] = "tamper"
    cp._write(stage2 / "validation.json", receipt)
    assert cp.apply_candidate(repo, stage2)["status"] == "deferred"


def test_failed_second_atomic_replace_rolls_back_first_without_touching_old_tests(tmp_path, monkeypatch):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    mark_validated_for_application_test(stage)
    original = (repo / "aippt-studio/src/widget.py").read_bytes()
    original_test = (repo / "aippt-studio/tests/test_existing.py").read_bytes()
    actual_replace = cp._replace_bytes
    calls = []
    def fail_second(path, data, mode):
        calls.append(path)
        if len(calls) == 2:
            raise OSError("injected second-file replacement failure")
        actual_replace(path, data, mode)
    monkeypatch.setattr(cp, "_replace_bytes", fail_second)
    result = cp.apply_candidate(repo, stage)
    assert result["status"] == "rolled_back", result
    assert (repo / "aippt-studio/src/widget.py").read_bytes() == original
    assert (repo / "aippt-studio/tests/test_existing.py").read_bytes() == original_test
    assert not (repo / "aippt-studio/tests/test_regression.py").exists()


def test_readonly_dependency_links_cannot_be_redirected(tmp_path):
    repo = workspace(tmp_path)
    dep = repo / "aippt-studio/node_modules"
    dep.mkdir()
    stage, tree = candidate(repo)
    link = tree / "aippt-studio/node_modules"
    assert link.is_symlink() and link.resolve() == dep
    link.unlink()
    link.symlink_to(tmp_path)
    with pytest.raises(ValueError, match="redirected"):
        cp.inspect_candidate(stage)


def test_interrupted_application_is_rolled_back_from_persistent_baseline(tmp_path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    mark_validated_for_application_test(stage)
    changes = cp.inspect_candidate(stage)["changes"]
    first = changes[0]
    cp._write(stage / "application-journal.json", {"status": "applying", "applied": [], "next": first["path"], "changes": changes})
    # Simulate process death after replace but before the applied list advances.
    cp._replace_bytes(repo / first["path"], (tree / first["path"]).read_bytes(), first["after"]["mode"])
    recovered = cp.apply_candidate(repo, stage)
    assert recovered["status"] == "rolled_back"
    assert cp._source_files(repo) == cp._read(stage / "manifest.json")["source_files"]


def test_interrupted_rollback_does_not_overwrite_parallel_user_edit(tmp_path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    mark_validated_for_application_test(stage)
    changes = cp.inspect_candidate(stage)["changes"]
    cp._write(stage / "application-journal.json", {"status": "applying", "changes": changes})
    source = repo / "aippt-studio/src/widget.py"
    source.write_text("parallel user work")
    recovered = cp.apply_candidate(repo, stage)
    assert recovered["status"] == "rollback_incomplete"
    assert source.read_text() == "parallel user work"


def test_forged_or_modified_applied_receipt_is_not_recognized(tmp_path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    mark_validated_for_application_test(stage)
    cp._write(stage / "applied.json", {"status": "applied"})
    assert cp.apply_candidate(repo, stage)["status"] == "deferred"
    (stage / "applied.json").unlink()
    assert cp.apply_candidate(repo, stage)["status"] == "applied"
    assert cp.apply_candidate(repo, stage)["status"] == "already_applied"
    receipt = cp._read(stage / "applied.json")
    receipt["changes"] = []
    cp._write(stage / "applied.json", receipt)
    assert cp.apply_candidate(repo, stage)["status"] == "deferred"


def test_final_journal_failure_does_not_mislabel_committed_application(tmp_path, monkeypatch):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    mark_validated_for_application_test(stage)
    original = cp._write
    def fail_last_journal(path, value):
        if path.name == "application-journal.json" and value.get("status") == "applied":
            raise OSError("journal disk failure")
        return original(path, value)
    monkeypatch.setattr(cp, "_write", fail_last_journal)
    result = cp.apply_candidate(repo, stage)
    assert result["status"] == "applied" and "journal_warning" in result
    assert cp.apply_candidate(repo, stage)["status"] == "already_applied"


def test_crash_after_applied_file_before_registry_commit_rolls_back(tmp_path):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    mark_validated_for_application_test(stage)
    assert cp.apply_candidate(repo, stage)["status"] == "applied"
    registry = cp._registry(repo, stage)
    trusted = cp._read(registry)
    trusted.pop("application_sha256")
    cp._write(registry, trusted)
    cp._write(stage / "application-journal.json", {"status": "applying", "changes": cp.inspect_candidate(stage)["changes"]})
    assert cp.apply_candidate(repo, stage)["status"] == "rolled_back"
    assert not (stage / "applied.json").exists()
    assert cp._source_files(repo) == cp._read(stage / "manifest.json")["source_files"]


def test_previously_passing_case_cannot_be_skipped_by_candidate(tmp_path, monkeypatch):
    repo = workspace(tmp_path)
    stage, tree = candidate(repo)
    fix(tree)
    monkeypatch.setattr(cp, "_sandbox_probe", lambda *args: {"status": "verified"})
    def runner(stage, manifest, label, source, files, selected, timeout):
        existing = {"id": "test_existing::test_identity", "status": "passed"}
        regression = {"id": "test_regression::test_nonzero_operand", "status": "failed"}
        cases, code = [existing], 0
        if label == "before-new-tests":
            cases, code = [regression], 1
        elif label == "after":
            cases = [dict(existing, status="skipped"), dict(regression, status="passed")]
        return {"returncode": code, "source_tree_unchanged": True, "junit": {"cases": cases}}
    monkeypatch.setattr(cp, "_run_tests", runner)
    assert cp.validate_candidate(stage)["status"] == "rejected"
