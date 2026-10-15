from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_UNDER_TEST = Path(os.environ.get("CLOSE_SCRIPT", REPO_ROOT / "scripts/prepare-work-close.ps1"))
POWERSHELL = shutil.which("pwsh") or str(REPO_ROOT / "aippt-studio/.tools/pwsh/pwsh")
assert POWERSHELL is not None, "PowerShell is required for this regression"


def make_work(tmp_path: Path, retention: dict) -> Path:
    work = tmp_path / "project/work" / "1"
    for relative in ("input/source", "input/reference", "workspace/qa", "workspace/conversations", "output"):
        (work / relative).mkdir(parents=True, exist_ok=True)
    (work / "input/request.md").write_text("fixture", encoding="utf-8")
    (work / "workspace/worklog.md").write_text("fixture", encoding="utf-8")
    (work / "workspace/knowledge-context.json").write_text(
        '{"status":"no_match","advisory_only":true,"matches":[]}', encoding="utf-8"
    )
    (work / "workspace/conversations/retention-policy.json").write_text(
        json.dumps(retention), encoding="utf-8"
    )
    (work / "workspace/conversations/manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "work_id": "1",
        "threads": {"fixture-thread": {"conversation_role": "user_conversation", "snapshots": []}},
    }), encoding="utf-8")
    (work / "workspace/conversations/bindings.json").write_text(
        '{"bindings":[{"thread_id":"fixture-thread"}]}', encoding="utf-8"
    )
    (work / "output/deliverable.txt").write_text("fixture output", encoding="utf-8")
    (work / "work.json").write_text(
        json.dumps({"id": "1", "work_id": "1", "name": "fixture", "client": "", "status": "in_progress"}),
        encoding="utf-8",
    )
    return work


def run_prepare(tmp_path: Path, script: Path, work_id: str = "1",
                python_executable: Path | None = None) -> subprocess.CompletedProcess[str]:
    project_root = tmp_path / "project"
    scripts_dir = project_root / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for dependency in ("prepare-work-close.ps1", "studio-runtime.ps1", "work-lifecycle-lock.ps1"):
        shutil.copy2(REPO_ROOT / "scripts" / dependency, scripts_dir / dependency)
    shutil.copy2(script, scripts_dir / "prepare-work-close.ps1")
    environment = os.environ.copy()
    environment["AIPPT_PYTHON"] = str(python_executable or REPO_ROOT / "aippt-studio/.venv-macos/bin/python")
    environment["PYTHONPATH"] = str(REPO_ROOT / "aippt-studio/src")
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(scripts_dir / "prepare-work-close.ps1"), "-WorkId", work_id,
         "-WorkRoot", str(project_root / "work")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        check=False, env=environment,
    )


def test_metadata_only_partial_closure_enters_review_and_reports_missing_evidence(tmp_path: Path) -> None:
    work = make_work(tmp_path, {"schema_version": 1, "mode": "metadata_only"})
    historical_request = tmp_path / "project/任务知识库/project_reviews/requests/1.json"
    historical_request.parent.mkdir(parents=True)
    historical_request.write_text('{"status":"requested","closure_generation":{"manifest_sha256":"old"}}', encoding="utf-8")
    historical_request_bytes = historical_request.read_bytes()

    result = run_prepare(tmp_path, SCRIPT_UNDER_TEST)

    assert result.returncode == 0, result.stderr or result.stdout
    closure = json.loads((work / "workspace/qa/closure-evidence.json").read_text(encoding="utf-8"))
    assert closure["status"] == "partial"
    assert closure["archive_status"] == "metadata_only"
    assert json.loads((work / "work.json").read_text(encoding="utf-8-sig"))["status"] == "review"
    report = (work / "workspace/completion-report.md").read_text(encoding="utf-8-sig")
    assert "闭环证据状态：partial" in report
    assert "原始对话快照与反馈原文不可用" in report
    assert "项目复核队列：本轮未提交新请求" in report
    assert "未由本次 partial evidence 验证" in report
    assert "本地原始对话快照不会重建" in report
    assert "自动刷新对话证据" not in report
    assert historical_request.read_bytes() == historical_request_bytes


def test_non_metadata_only_closure_failure_still_blocks_review(tmp_path: Path) -> None:
    work = make_work(tmp_path, {"schema_version": 1, "mode": "invalid-mode"})

    result = run_prepare(tmp_path, REPO_ROOT / "scripts/prepare-work-close.ps1")

    assert result.returncode != 0
    assert "Closure evidence capture failed" in (result.stderr + result.stdout)
    assert json.loads((work / "work.json").read_text(encoding="utf-8-sig"))["status"] == "in_progress"
    assert not (work / "workspace/qa/final-output-manifest.json").exists()


def test_metadata_only_partial_with_pending_binding_error_still_blocks_review(tmp_path: Path) -> None:
    work = make_work(tmp_path, {"schema_version": 1, "mode": "metadata_only"})
    (work / "workspace/pending-conversation-binding.json").write_text('{"invalid":true}', encoding="utf-8")

    result = run_prepare(tmp_path, SCRIPT_UNDER_TEST)

    assert result.returncode != 0
    assert "Closure evidence capture failed" in (result.stderr + result.stdout)
    closure = json.loads((work / "workspace/qa/closure-evidence.json").read_text(encoding="utf-8"))
    assert closure["status"] == "partial"
    assert closure["archive_status"] == "metadata_only"
    assert closure["binding_retry"]["status"] == "pending"
    assert json.loads((work / "work.json").read_text(encoding="utf-8-sig"))["status"] == "in_progress"


def test_previous_valid_partial_receipt_cannot_mask_failure_before_new_write(tmp_path: Path) -> None:
    work = make_work(tmp_path, {"schema_version": 1, "mode": "metadata_only"})
    environment = os.environ.copy()
    environment["AIPPT_PYTHON"] = str(REPO_ROOT / "aippt-studio/.venv-macos/bin/python")
    environment["PYTHONPATH"] = str(REPO_ROOT / "aippt-studio/src")
    initial_capture = subprocess.run(
        [environment["AIPPT_PYTHON"], "-m", "aippt_studio.maintenance", "closure", "--work-dir", str(work)],
        cwd=REPO_ROOT, capture_output=True, text=True, env=environment, check=False,
    )
    assert initial_capture.returncode == 1
    receipt = work / "workspace/qa/closure-evidence.json"
    previous_bytes = receipt.read_bytes()
    previous = json.loads(previous_bytes)
    assert previous["status"] == "partial" and previous["archive_status"] == "metadata_only"

    wrapper = tmp_path / "python-fail-before-closure-write"
    real_python = REPO_ROOT / "aippt-studio/.venv-macos/bin/python"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        f"real = {str(real_python)!r}\n"
        "if sys.argv[1:4] == ['-m', 'aippt_studio.maintenance', 'closure']:\n"
        "    raise SystemExit(23)\n"
        "os.execv(real, [real, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = run_prepare(tmp_path, SCRIPT_UNDER_TEST, python_executable=wrapper)

    assert result.returncode != 0
    assert "Closure evidence capture failed" in (result.stderr + result.stdout)
    assert receipt.read_bytes() == previous_bytes
    assert json.loads((work / "work.json").read_text(encoding="utf-8-sig"))["status"] == "in_progress"


def test_exit_zero_with_partial_receipt_does_not_reach_review_queue(tmp_path: Path) -> None:
    work = make_work(tmp_path, {"schema_version": 1, "mode": "metadata_only"})
    wrapper = tmp_path / "python-return-zero-for-partial-closure"
    real_python = REPO_ROOT / "aippt-studio/.venv-macos/bin/python"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import os, subprocess, sys\n"
        f"real = {str(real_python)!r}\n"
        "if sys.argv[1:4] == ['-m', 'aippt_studio.maintenance', 'closure']:\n"
        "    subprocess.run([real, *sys.argv[1:]], check=False)\n"
        "    raise SystemExit(0)\n"
        "os.execv(real, [real, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = run_prepare(tmp_path, SCRIPT_UNDER_TEST, python_executable=wrapper)

    assert result.returncode != 0
    assert "after a successful exit" in (result.stderr + result.stdout)
    assert json.loads((work / "workspace/qa/closure-evidence.json").read_text(encoding="utf-8"))["status"] == "partial"
    assert json.loads((work / "work.json").read_text(encoding="utf-8-sig"))["status"] == "in_progress"
    assert not (tmp_path / "project/任务知识库/project_reviews/requests/1.json").exists()


@pytest.mark.parametrize("field", ["event_verification_errors", "feedback_coverage.warnings", "archive_results"])
def test_required_error_warning_and_archive_lists_must_be_present_and_empty(tmp_path: Path, field: str) -> None:
    work = make_work(tmp_path, {"schema_version": 1, "mode": "metadata_only"})
    wrapper = tmp_path / "python-remove-required-closure-list"
    real_python = REPO_ROOT / "aippt-studio/.venv-macos/bin/python"
    wrapper.write_text(
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys\n"
        f"real = {str(real_python)!r}\n"
        f"field = {field!r}\n"
        "if sys.argv[1:4] == ['-m', 'aippt_studio.maintenance', 'closure']:\n"
        "    subprocess.run([real, *sys.argv[1:]], check=False)\n"
        "    work = sys.argv[sys.argv.index('--work-dir') + 1]\n"
        "    path = os.path.join(work, 'workspace/qa/closure-evidence.json')\n"
        "    data = json.load(open(path, encoding='utf-8'))\n"
        "    target = data\n"
        "    parts = field.split('.')\n"
        "    for key in parts[:-1]: target = target[key]\n"
        "    target.pop(parts[-1])\n"
        "    json.dump(data, open(path, 'w', encoding='utf-8'))\n"
        "    raise SystemExit(1)\n"
        "os.execv(real, [real, *sys.argv[1:]])\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    result = run_prepare(tmp_path, SCRIPT_UNDER_TEST, python_executable=wrapper)

    assert result.returncode != 0
    assert "Closure evidence capture failed" in (result.stderr + result.stdout)
    assert json.loads((work / "work.json").read_text(encoding="utf-8-sig"))["status"] == "in_progress"
