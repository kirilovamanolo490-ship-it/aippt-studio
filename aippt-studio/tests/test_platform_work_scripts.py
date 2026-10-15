"""Exercise real launchers and allocation on the current host, in isolated Work roots."""
from __future__ import annotations

import concurrent.futures
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
STUDIO_SCRIPTS = REPO_ROOT / "aippt-studio/scripts"
BUNDLED_PWSH = REPO_ROOT / "aippt-studio/.tools/pwsh/pwsh"
PWSH = shutil.which("pwsh") or (str(BUNDLED_PWSH) if BUNDLED_PWSH.is_file() else None)
pytestmark = pytest.mark.skipif(PWSH is None or os.name == "nt", reason="Unix PowerShell launchers require a Unix host with PowerShell 7.3+")


def _run(arguments: list[str | Path], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(item) for item in arguments], env=env, cwd=cwd, text=True, capture_output=True, timeout=90, check=False)


def _environment() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("AIPPT_PYTHON", None)
    env.pop("AIPPT_PWSH", None)
    env.pop("PSNativeCommandUseErrorActionPreference", None)
    return env


def _fake_python(tmp_path: Path) -> Path:
    executable = tmp_path / "Python 运行时"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, platform, sys\n"
        "if sys.argv[1:2] == ['-c']:\n"
        " print(os.environ.get('FAKE_PYTHON_IDENTITY_JSON') or json.dumps({'platform':sys.platform, 'architecture':platform.machine(), 'version':'%s.%s.%s' % sys.version_info[:3]}))\n"
        " sys.exit(0)\n"
        "print(json.dumps({'args': sys.argv[1:], 'jobs': os.environ.get('AIPPT_JOBS_DIR'), 'work': os.environ.get('AIPPT_WORK_DIR'), 'utf8': os.environ.get('PYTHONUTF8'), 'encoding': os.environ.get('PYTHONIOENCODING')}, ensure_ascii=False))\n"
        "sys.exit(int(os.environ.get('FAKE_EXIT_CODE', '0')))\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _fake_pwsh(tmp_path: Path) -> Path:
    """A tiny shell fixture that answers the launcher's PowerShell version probe."""

    executable = tmp_path / "PowerShell fixture"
    executable.write_text(
        "#!/bin/sh\n"
        "for argument in \"$@\"; do\n"
        "  if [ \"$argument\" = \"-Command\" ]; then\n"
        "    printf '%s\\n' \"${FAKE_PWSH_VERSION:-7.4}\"\n"
        "    exit 0\n"
        "  fi\n"
        "done\n"
        "if [ -n \"${FAKE_PWSH_EXECUTED:-}\" ]; then printf executed > \"$FAKE_PWSH_EXECUTED\"; fi\n"
        "printf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _fake_uv(tmp_path: Path) -> Path:
    executable = tmp_path / "uv"
    executable.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then\n"
        "  if [ -n \"${TEST_UV_EXECUTED:-}\" ]; then printf executed > \"$TEST_UV_EXECUTED\"; fi\n"
        "  printf '%s\\n' 'uv 0.0.0-test'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def _new_work(tmp_path: Path, *, name: str = "中文项目", request: str = "", env: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], Path]:
    root = tmp_path / "任务 包 [中文]"
    result = _run(
        [SCRIPTS / "new-work.sh", "-Name", name, "-Request", request, "-WorkRoot", root, "-TaskKnowledgeRoot", tmp_path / "未配置知识库"],
        env=env or _environment(),
        cwd=tmp_path,
    )
    return result, root


def test_new_work_keeps_literal_markdown_and_runs_from_custom_unicode_root(tmp_path: Path) -> None:
    request = '保持 `input/source/` 与 $literal；空格 "引号" {原样}'
    result, root = _new_work(tmp_path, name="中文任务：{模板} `n", request=request)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    work = root / "1"
    assert Path(payload["root"]) == work
    record = json.loads((work / "work.json").read_text(encoding="utf-8-sig"))
    assert record["closure_schema_version"] == 3
    assert record["storage_profile"] == "minimal"
    assert not (work / "README.md").exists()
    assert request in (work / "input/request.md").read_text(encoding="utf-8-sig")
    assert not (work / "workspace/worklog.md").exists()
    assert not list(root.glob(".new-work-*"))
    assert not (work / "workspace/launcher-startup-diagnostics.json").exists(), "isolated roots must not bind to the current conversation"
    shell_wrapper = work / "workspace/aippt.sh"
    assert os.access(shell_wrapper, os.X_OK)
    launched = _run([shell_wrapper, "--help"], env=_environment(), cwd=tmp_path)
    assert launched.returncode == 0, launched.stderr
    assert "doctor" in launched.stdout


def test_work_and_runtime_wrappers_preserve_empty_and_special_arguments_and_exit_code(tmp_path: Path) -> None:
    result, root = _new_work(tmp_path)
    assert result.returncode == 0, result.stderr
    env = _environment()
    env["AIPPT_PYTHON"] = str(_fake_python(tmp_path))
    env["FAKE_EXIT_CODE"] = "23"
    arguments = ["capture", "", "含 空格", '带"引号', "-Verbose", "-ErrorAction", "Stop", "--json", "$literal`text"]
    work = root / "1"
    launched = _run([work / "workspace/aippt.sh", *arguments], env=env, cwd=tmp_path)
    assert launched.returncode == 23, launched.stderr
    payload = json.loads(launched.stdout)
    assert payload["args"] == ["-m", "aippt_studio", *arguments]
    assert payload["jobs"] == str(work / "workspace/studio-jobs")
    assert payload["work"] == str(work)
    assert payload["utf8"] == "1" and payload["encoding"] == "utf-8"
    generic = _run([SCRIPTS / "work-studio.sh", work, *arguments], env=env, cwd=tmp_path)
    assert generic.returncode == 23, generic.stderr
    assert json.loads(generic.stdout) == payload


def test_python_and_jobs_environment_is_restored_for_caller(tmp_path: Path) -> None:
    result, root = _new_work(tmp_path)
    assert result.returncode == 0, result.stderr
    env = _environment()
    env["AIPPT_PYTHON"] = str(_fake_python(tmp_path))
    env["TEST_WRAPPER"] = str(root / "1/workspace/aippt.ps1")
    env["PYTHONUTF8"] = "0"
    env["PYTHONIOENCODING"] = "ascii"
    env["AIPPT_JOBS_DIR"] = "prior-jobs"
    env["AIPPT_WORK_DIR"] = "prior-work"
    command = "& $env:TEST_WRAPPER capture; [ordered]@{utf8=$env:PYTHONUTF8; encoding=$env:PYTHONIOENCODING; jobs=$env:AIPPT_JOBS_DIR; work=$env:AIPPT_WORK_DIR} | ConvertTo-Json -Compress"
    launched = _run([PWSH, "-NoProfile", "-Command", command], env=env)
    assert launched.returncode == 0, launched.stderr
    caller = json.loads(launched.stdout.splitlines()[-1])
    assert caller == {"utf8": "0", "encoding": "ascii", "jobs": "prior-jobs", "work": "prior-work"}


def test_bad_runtime_does_not_break_minimal_allocation_or_create_local_context(tmp_path: Path) -> None:
    env = _environment()
    env["AIPPT_PYTHON"] = str(tmp_path / "missing Windows/python.exe")
    result, root = _new_work(tmp_path, env=env)
    assert result.returncode == 0, result.stderr
    record = json.loads((root / "1/work.json").read_text(encoding="utf-8-sig"))
    assert record["storage_profile"] == "minimal"
    assert record["conversation_retention"] == "disabled"
    assert not (root / "1/workspace/knowledge-context.json").exists()
    assert not (root / "1/workspace/pending-conversation-binding.json").exists()
    assert not (root / "1/workspace/conversations").exists()
    launched = _run([root / "1/workspace/aippt.sh", "--help"], env=env)
    assert launched.returncode != 0
    assert "Python 3.11+" in launched.stderr


def test_windows_executable_is_never_run_on_unix(tmp_path: Path) -> None:
    fake = _fake_python(tmp_path)
    windows = tmp_path / "python.exe"
    fake.rename(windows)
    env = _environment()
    env["AIPPT_PYTHON"] = str(windows)
    result, root = _new_work(tmp_path, env=env)
    assert result.returncode == 0, result.stderr
    record = json.loads((root / "1/work.json").read_text(encoding="utf-8-sig"))
    assert record["storage_profile"] == "minimal"
    assert not (root / "1/workspace/knowledge-context.json").exists()


def test_python_selector_rejects_rosetta_x64_behind_a_benign_symlink(tmp_path: Path) -> None:
    """An Apple-Silicon host must reject an x64 Python even when Rosetta runs it."""

    foreign = _fake_python(tmp_path)
    alias = tmp_path / "studio-python"
    alias.symlink_to(foreign)
    env = _environment()
    env["AIPPT_PYTHON"] = str(alias)
    env["FAKE_PYTHON_IDENTITY_JSON"] = json.dumps(
        {"platform": "darwin", "architecture": "x86_64", "version": "3.13.1"}
    )
    env["TEST_STUDIO_RUNTIME"] = str(SCRIPTS / "studio-runtime.ps1")
    env["TEST_REPO_ROOT"] = str(REPO_ROOT)
    command = (
        ". $env:TEST_STUDIO_RUNTIME; "
        "function Get-StudioPythonHostIdentity { return @{ platform = 'darwin'; architecture = 'arm64' } }; "
        "Get-StudioPython $env:TEST_REPO_ROOT"
    )

    result = _run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command], env=env)

    assert result.returncode != 0
    assert "Python identity darwin/x64 does not match host darwin/arm64" in result.stderr
    assert "Rosetta x64 interpreter" in result.stderr
    assert str(alias) in result.stderr


def test_setup_runtime_rejects_a_windows_node_before_npm_can_run(tmp_path: Path) -> None:
    windows_node = tmp_path / "node.exe"
    windows_node.write_bytes(b"MZ" + b"\0" * 256)
    windows_node.chmod(0o755)
    env = _environment()
    env["AIPPT_NODE"] = str(windows_node)

    result = _run([PWSH, "-NoProfile", "-File", STUDIO_SCRIPTS / "setup-extensions.ps1"], env=env)

    assert result.returncode != 0
    assert "Windows executable cannot run on this host" in result.stderr


@pytest.mark.parametrize(("selector", "environment_name"), [
    ("Get-StudioPython", "AIPPT_PYTHON"),
    ("Get-StudioNode", "AIPPT_NODE"),
    ("Get-StudioUv", "AIPPT_UV"),
])
def test_runtime_selectors_reject_symlinked_historical_work_binaries(
    tmp_path: Path, selector: str, environment_name: str
) -> None:
    old = tmp_path / "work/8/workspace/retired-runtime"
    old.parent.mkdir(parents=True)
    marker = tmp_path / "runtime-was-executed"
    old.write_text(
        "#!/bin/sh\n"
        "printf executed > \"$TEST_RUNTIME_EXECUTED\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    old.chmod(0o755)
    alias = tmp_path / "managed-looking-runtime"
    alias.symlink_to(old)
    env = _environment()
    env[environment_name] = str(alias)
    env["TEST_STUDIO_RUNTIME"] = str(SCRIPTS / "studio-runtime.ps1")
    env["TEST_RUNTIME_SELECTOR"] = selector
    env["TEST_REPO_ROOT"] = str(REPO_ROOT)
    env["TEST_RUNTIME_EXECUTED"] = str(marker)

    result = _run(
        [PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
         ". $env:TEST_STUDIO_RUNTIME; & $env:TEST_RUNTIME_SELECTOR $env:TEST_REPO_ROOT"],
        env=env,
    )

    assert result.returncode != 0
    assert "historical Work" in result.stderr
    assert not marker.exists()


@pytest.mark.parametrize(("architectures", "accepted"), [("x64", False), ("x86", False), ("arm64", True)])
def test_uv_selector_requires_the_physical_macos_architecture_before_version_probe(
    tmp_path: Path, architectures: str, accepted: bool
) -> None:
    """Rosetta x64 and every foreign slice stop before a media installer runs."""

    uv = _fake_uv(tmp_path)
    marker = tmp_path / "uv-version-was-executed"
    env = _environment()
    env["AIPPT_UV"] = str(uv)
    env["TEST_UV_EXECUTED"] = str(marker)
    env["TEST_UV_ARCHITECTURES"] = architectures
    env["TEST_STUDIO_RUNTIME"] = str(SCRIPTS / "studio-runtime.ps1")
    env["TEST_REPO_ROOT"] = str(REPO_ROOT)
    command = (
        ". $env:TEST_STUDIO_RUNTIME; "
        "function Get-StudioPythonHostIdentity { return @{ platform = 'darwin'; architecture = 'arm64' } }; "
        "function Get-StudioMacBinaryArchitectures { param([string]$Path) return ($env:TEST_UV_ARCHITECTURES -split ',' | Where-Object { $_ }) }; "
        "Get-StudioUv $env:TEST_REPO_ROOT"
    )

    result = _run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command], env=env)

    if accepted:
        assert result.returncode == 0, result.stderr
        assert str(uv) in result.stdout
        assert marker.exists()
    else:
        assert result.returncode != 0
        assert "do not match host darwin/arm64" in result.stderr
        assert not marker.exists()


def test_macos_setup_shell_wrapper_delegates_to_shared_host_launcher(tmp_path: Path) -> None:
    recorder = _fake_pwsh(tmp_path)
    env = _environment()
    env["AIPPT_PWSH"] = str(recorder)

    result = _run([STUDIO_SCRIPTS / "setup-media.sh"], env=env, cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert str(STUDIO_SCRIPTS / "setup-media.ps1") in result.stdout


def test_concurrent_allocation_has_unique_complete_numbers(tmp_path: Path) -> None:
    env = _environment()
    env["AIPPT_PYTHON"] = str(tmp_path / "unavailable-python")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda i: _new_work(tmp_path, name=f"并行 {i}", env=env), range(4)))
    for result, _ in results:
        assert result.returncode == 0, result.stderr
    assert sorted(json.loads(result.stdout)["work_id"] for result, _ in results) == ["1", "2", "3", "4"]
    root = results[0][1]
    assert not list(root.glob(".new-work-*"))
    for number in range(1, 5):
        assert (root / str(number) / "workspace/aippt.sh").is_file()
        record = json.loads((root / str(number) / "work.json").read_text(encoding="utf-8-sig"))
        assert record["storage_profile"] == "minimal"
        assert not (root / str(number) / "workspace/knowledge-context.json").exists()


def test_allocation_io_failure_removes_only_own_staging_and_releases_lock(tmp_path: Path) -> None:
    env = _environment()
    root = tmp_path / "work"
    preserved = root / "existing-evidence"
    preserved.mkdir(parents=True)
    (preserved / "keep.txt").write_text("preserve", encoding="utf-8")
    env["TEST_SCRIPT"] = str(SCRIPTS / "new-work.ps1")
    env["TEST_ROOT"] = str(root)
    command = "function Set-Content { throw 'injected storage failure' }; & $env:TEST_SCRIPT -Name 'failure' -WorkRoot $env:TEST_ROOT"
    failed = _run([PWSH, "-NoProfile", "-Command", command], env=env)
    assert failed.returncode != 0
    assert "injected storage failure" in failed.stderr
    assert not (root / "1").exists()
    assert not list(root.glob(".new-work-*"))
    assert (preserved / "keep.txt").read_text() == "preserve"
    env["AIPPT_PYTHON"] = str(tmp_path / "unavailable-python")
    retry = _run([SCRIPTS / "new-work.sh", "-Name", "retry", "-WorkRoot", root], env=env)
    assert retry.returncode == 0, retry.stderr
    assert json.loads(retry.stdout)["work_id"] == "1"


def test_pwsh_override_is_literal_and_broken_override_is_actionable(tmp_path: Path) -> None:
    host = tmp_path / "PowerShell 运行时"
    host.symlink_to(PWSH)
    env = _environment()
    env["AIPPT_PWSH"] = str(host)
    ok = _run([SCRIPTS / "pwsh.sh", "-NoProfile", "-Command", "Write-Output 'available'"], env=env, cwd=tmp_path)
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip() == "available"
    env["AIPPT_PWSH"] = str(tmp_path / "not installed")
    failed = _run([SCRIPTS / "pwsh.sh", "-NoProfile"], env=env)
    assert failed.returncode == 126
    assert "AIPPT_PWSH is not an executable file" in failed.stderr


def test_pwsh_launcher_rejects_windows_and_historical_overrides_before_execution(tmp_path: Path) -> None:
    windows = tmp_path / "pwsh.exe"
    windows.write_bytes(b"MZ" + bytes(128))
    windows.chmod(0o755)
    env = _environment()
    env["AIPPT_PWSH"] = str(windows)

    rejected_windows = _run([SCRIPTS / "pwsh.sh", "-NoProfile", "-Command", "Write-Output never"], env=env)

    assert rejected_windows.returncode == 126
    assert "Windows executable" in rejected_windows.stderr

    old = tmp_path / "work/8/workspace/pwsh"
    old.parent.mkdir(parents=True)
    marker = tmp_path / "historical-pwsh-ran"
    old.write_text(
        "#!/bin/sh\n"
        "printf executed > \"$TEST_PWSH_EXECUTED\"\n"
        "exit 0\n",
        encoding="utf-8",
    )
    old.chmod(0o755)
    alias = tmp_path / "managed-looking-pwsh"
    alias.symlink_to(old)
    env["AIPPT_PWSH"] = str(alias)
    env["TEST_PWSH_EXECUTED"] = str(marker)

    rejected_historical = _run([SCRIPTS / "pwsh.sh", "-NoProfile", "-Command", "Write-Output never"], env=env)

    assert rejected_historical.returncode == 126
    assert "historical Work" in rejected_historical.stderr
    assert not marker.exists()

    uppercase_old = tmp_path / "Work/9/workspace/pwsh"
    uppercase_old.parent.mkdir(parents=True)
    uppercase_old.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
    uppercase_old.chmod(0o755)
    uppercase_alias = tmp_path / "managed-looking-uppercase-pwsh"
    uppercase_alias.symlink_to(uppercase_old)
    env["AIPPT_PWSH"] = str(uppercase_alias)

    rejected_uppercase = _run([SCRIPTS / "pwsh.sh", "-NoProfile", "-Command", "Write-Output never"], env=env)

    assert rejected_uppercase.returncode == 126
    assert "historical Work" in rejected_uppercase.stderr
    assert not marker.exists()


def test_pwsh_launcher_requires_version_73_before_forwarding_arguments(tmp_path: Path) -> None:
    fake = _fake_pwsh(tmp_path)
    marker = tmp_path / "old-pwsh-ran"
    env = _environment()
    env["AIPPT_PWSH"] = str(fake)
    env["FAKE_PWSH_VERSION"] = "7.2"
    env["FAKE_PWSH_EXECUTED"] = str(marker)

    result = _run([SCRIPTS / "pwsh.sh", "-NoProfile", "-Command", "Write-Output never"], env=env)

    assert result.returncode == 126
    assert "requires PowerShell 7.3 or newer" in result.stderr
    assert not marker.exists()


@pytest.mark.skipif(not BUNDLED_PWSH.is_file(), reason="bundled PowerShell is not installed in this checkout")
def test_pwsh_launcher_uses_valid_bundled_fallback_after_an_invalid_path_candidate(tmp_path: Path) -> None:
    """A stale PATH copy must not prevent the managed macOS fallback from working."""

    stale = _fake_pwsh(tmp_path)
    path_candidate = tmp_path / "pwsh"
    stale.rename(path_candidate)
    env = _environment()
    env["FAKE_PWSH_VERSION"] = "7.2"
    env["PATH"] = str(tmp_path) + os.pathsep + "/usr/bin:/bin"

    result = _run(
        [SCRIPTS / "pwsh.sh", "-NoProfile", "-Command", "Write-Output 'bundled fallback'"],
        env=env,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "bundled fallback"
    assert result.stderr == ""


def test_runtime_override_accepts_a_command_name_from_path(tmp_path: Path) -> None:
    fake = _fake_python(tmp_path)
    env = _environment()
    env["PATH"] = str(tmp_path) + os.pathsep + env.get("PATH", "")
    env["AIPPT_PYTHON"] = fake.name
    result = _run([PWSH, "-NoProfile", "-File", SCRIPTS / "run-studio.ps1", "capture", ""], env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["args"] == ["-m", "aippt_studio", "capture", ""]


def test_allocation_lock_timeout_does_not_publish_a_partial_work(tmp_path: Path) -> None:
    root = tmp_path / "work"
    root.mkdir()
    env = _environment()
    env["TEST_LOCK"] = str(root / ".new-work.lock")
    lock_script = "$stream=[IO.File]::Open($env:TEST_LOCK, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None); try { [Console]::WriteLine('locked'); [Console]::Out.Flush(); Start-Sleep -Seconds 20 } finally { $stream.Dispose() }"
    holder = subprocess.Popen([PWSH, "-NoProfile", "-Command", lock_script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout is not None
        ready, _, _ = select.select([holder.stdout], [], [], 10)
        assert ready and holder.stdout.readline().strip() == "locked"
        failed = _run([SCRIPTS / "new-work.sh", "-Name", "locked", "-WorkRoot", root, "-LockTimeoutSeconds", "1"], env=env)
        assert failed.returncode != 0
        assert "Timed out waiting for the Work allocation lock" in failed.stderr
        assert not (root / "1").exists()
        assert not list(root.glob(".new-work-*"))
    finally:
        holder.terminate()
        holder.communicate(timeout=10)


@pytest.mark.parametrize("recall_exit", [0, 9])
def test_new_work_does_not_run_or_retain_historical_recall(tmp_path: Path, recall_exit: int) -> None:
    fake = tmp_path / "bad-recall-python"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, platform, sys\n"
        "if sys.argv[1:2] == ['-c']:\n"
        " print(json.dumps({'platform':sys.platform, 'architecture':platform.machine(), 'version':'%s.%s.%s' % sys.version_info[:3]}))\n"
        " sys.exit(0)\n"
        "pathlib.Path(sys.argv[sys.argv.index('--output')+1]).write_text('{malformed')\n"
        f"sys.exit({recall_exit})\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = _environment()
    env["AIPPT_PYTHON"] = str(fake)
    result, root = _new_work(tmp_path, env=env)
    assert result.returncode == 0, result.stderr
    assert not (root / "1/workspace/knowledge-context.json").exists()
    assert not (root / "1/workspace/assistant-context.json").exists()


@pytest.mark.parametrize("initialize_exit", [0, 12])
def test_standard_root_initializes_after_publish_and_initialization_failure_is_nonblocking(tmp_path: Path, initialize_exit: int) -> None:
    repo = tmp_path / "独立 仓库"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    for name in ("new-work.ps1", "new-work.sh", "pwsh.sh", "studio-runtime.ps1"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    fake = tmp_path / "initialize-python"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import json, pathlib, platform, sys\n"
        "if sys.argv[1:2] == ['-c']:\n"
        " print(json.dumps({'platform':sys.platform, 'architecture':platform.machine(), 'version':'%s.%s.%s' % sys.version_info[:3]}))\n"
        " sys.exit(0)\n"
        "if 'knowledge-recall' in sys.argv:\n"
        " pathlib.Path(sys.argv[sys.argv.index('--output')+1]).write_text(json.dumps({'status':'no_match','matches':[]}))\n"
        " sys.exit(0)\n"
        "work = pathlib.Path(sys.argv[sys.argv.index('--work-dir')+1])\n"
        "(work/'workspace/init-observed.json').write_text(json.dumps({'args':sys.argv[1:], 'published':(work/'work.json').is_file(), 'work_name':work.name}))\n"
        f"sys.exit({initialize_exit})\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = _environment()
    env["AIPPT_PWSH"] = str(PWSH)
    env["AIPPT_PYTHON"] = str(fake)
    env["CODEX_THREAD_ID"] = "00000000-0000-0000-0000-000000000037"
    result = _run([scripts / "new-work.sh", "-Name", "初始化测试"], env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    work = repo / "work/1"
    assert Path(json.loads(result.stdout)["root"]) == work
    observed = json.loads((work / "workspace/init-observed.json").read_text())
    assert observed["published"] is True and observed["work_name"] == "1"
    args = observed["args"]
    assert args[args.index("--repo-root") + 1] == str(repo)
    assert args[args.index("--thread-id") + 1] == env["CODEX_THREAD_ID"]
    assert not (work / "workspace/launcher-startup-diagnostics.json").exists()
    assert not (work / "workspace/pending-conversation-binding.json").exists()
    assert not (work / "workspace/conversations").exists()


def test_minimal_work_does_not_retain_thread_intent_when_python_is_missing(tmp_path: Path) -> None:
    repo = tmp_path / "runtime-unavailable-repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    for name in ("new-work.ps1", "new-work.sh", "pwsh.sh", "studio-runtime.ps1"):
        shutil.copy2(SCRIPTS / name, scripts / name)
    env = _environment()
    env["AIPPT_PWSH"] = str(PWSH)
    env["AIPPT_PYTHON"] = str(tmp_path / "unavailable-python")
    env["CODEX_THREAD_ID"] = "00000000-0000-0000-0000-000000000037"
    result = _run([scripts / "new-work.sh", "-Name", "稍后恢复归档"], env=env, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    work = repo / "work/1"
    record = json.loads((work / "work.json").read_text(encoding="utf-8-sig"))
    assert record["storage_profile"] == "minimal"
    assert record["conversation_retention"] == "disabled"
    assert not (work / "workspace/pending-conversation-binding.json").exists()
    assert not (work / "workspace/conversations/bindings.json").exists()
