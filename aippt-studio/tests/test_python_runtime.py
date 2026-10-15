"""Regression coverage for host-native Studio Python selection."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aippt_studio import python_runtime


def test_apple_silicon_host_identity_overrides_rosetta_process_architecture(monkeypatch) -> None:
    """``platform.machine()`` can be x86_64 inside Rosetta; hardware is arm64."""

    monkeypatch.setattr(python_runtime.sys, "platform", "darwin")
    monkeypatch.setattr(python_runtime.platform, "machine", lambda: "x86_64")

    class Result:
        returncode = 0
        stdout = "1\n"

    calls = []

    def trusted_probe(command, *args, **kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr(python_runtime.subprocess, "run", trusted_probe)
    assert python_runtime.host_identity() == {"platform": "darwin", "architecture": "arm64"}
    assert calls == [[str(python_runtime.SYSTEM_SYSCTL), "-in", "hw.optional.arm64"]]


def test_darwin_host_identity_ignores_a_path_shadow_for_sysctl(monkeypatch, tmp_path) -> None:
    """The physical-host probe must never resolve a user-supplied ``sysctl``."""

    shadow = tmp_path / "sysctl"
    shadow.write_text("#!/bin/sh\nprintf '0\\n'\n", encoding="utf-8")
    shadow.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(python_runtime.sys, "platform", "darwin")
    monkeypatch.setattr(python_runtime.platform, "machine", lambda: "x86_64")

    class Result:
        returncode = 0
        stdout = "1\n"

    def trusted_probe(command, *args, **kwargs):
        assert command[0] == str(python_runtime.SYSTEM_SYSCTL)
        assert command[0] != str(shadow)
        return Result()

    monkeypatch.setattr(python_runtime.subprocess, "run", trusted_probe)
    assert python_runtime.host_identity() == {"platform": "darwin", "architecture": "arm64"}


@pytest.mark.parametrize("failure", ["ambiguous", "unavailable"])
def test_darwin_x64_fails_closed_when_trusted_hardware_probe_is_not_decisive(monkeypatch, failure) -> None:
    """Rosetta x64 cannot become a native host merely because ``sysctl`` is unusable."""

    monkeypatch.setattr(python_runtime.sys, "platform", "darwin")
    monkeypatch.setattr(python_runtime.platform, "machine", lambda: "x86_64")

    if failure == "ambiguous":
        class Result:
            returncode = 0
            stdout = "maybe\n"

        monkeypatch.setattr(python_runtime.subprocess, "run", lambda *args, **kwargs: Result())
    else:
        def unavailable(*args, **kwargs):
            raise FileNotFoundError("trusted sysctl unavailable")

        monkeypatch.setattr(python_runtime.subprocess, "run", unavailable)

    host = python_runtime.host_identity()
    assert host == {"platform": "darwin", "architecture": "unverified"}
    reason = python_runtime.validate_identity(
        {"platform": "darwin", "architecture": "x64", "version": "3.13.1"}, host=host
    )
    assert reason is not None
    assert "could not be verified" in reason
    assert "refusing to treat a Darwin/x64 Python as host-native" in reason


def test_verified_intel_hardware_probe_preserves_native_x64_support(monkeypatch) -> None:
    """A definitive trusted ``0`` remains a valid native Intel-host signal."""

    monkeypatch.setattr(python_runtime.sys, "platform", "darwin")
    monkeypatch.setattr(python_runtime.platform, "machine", lambda: "x86_64")

    class Result:
        returncode = 0
        stdout = "0\n"

    monkeypatch.setattr(python_runtime.subprocess, "run", lambda *args, **kwargs: Result())
    host = python_runtime.host_identity()
    assert host == {"platform": "darwin", "architecture": "x64"}
    assert python_runtime.validate_identity(
        {"platform": "darwin", "architecture": "x64", "version": "3.13.1"}, host=host
    ) is None


def test_native_arm64_remains_usable_if_hardware_probe_is_unavailable(monkeypatch) -> None:
    """An arm64 process is already host-native and does not need an x64 fallback."""

    monkeypatch.setattr(python_runtime.sys, "platform", "darwin")
    monkeypatch.setattr(python_runtime.platform, "machine", lambda: "arm64")

    def unavailable(*args, **kwargs):
        raise FileNotFoundError("trusted sysctl unavailable")

    monkeypatch.setattr(python_runtime.subprocess, "run", unavailable)
    host = python_runtime.host_identity()
    assert host == {"platform": "darwin", "architecture": "arm64"}
    assert python_runtime.validate_identity(
        {"platform": "darwin", "architecture": "arm64", "version": "3.13.1"}, host=host
    ) is None


def test_probe_rejects_rosetta_x64_python_through_its_symlink_launcher(tmp_path, monkeypatch) -> None:
    """The candidate must be rejected, but its written venv-like path stays intact."""

    marker = tmp_path / "launcher-used"
    target = tmp_path / "x64-python"
    target.write_text(
        "#!/bin/sh\n"
        "printf '%s' \"$0\" > \"$PROBE_MARKER\"\n"
        "printf '%s\\n' '{\"platform\":\"darwin\",\"architecture\":\"x86_64\",\"version\":\"3.13.1\"}'\n",
        encoding="utf-8",
    )
    target.chmod(0o755)
    launcher = tmp_path / "Studio Python"
    launcher.symlink_to(target)
    monkeypatch.setenv("PROBE_MARKER", str(marker))

    result = python_runtime.probe_python_runtime(
        launcher, host={"platform": "darwin", "architecture": "arm64"}
    )

    assert result["status"] == "failed"
    assert result["path"] == str(launcher)
    assert "Python identity darwin/x64 does not match host darwin/arm64" in result["reason"]
    assert "Rosetta x64 interpreter" in result["reason"]
    assert marker.read_text(encoding="utf-8") == str(launcher)


def test_work_runtime_wrapper_stops_before_a_rosetta_candidate_can_reexec(tmp_path, monkeypatch, capsys) -> None:
    """The Work wrapper consumes the shared failure before it selects Work state."""

    entry = Path(__file__).resolve().parents[2] / "scripts/with-studio-runtime.py"
    spec = importlib.util.spec_from_file_location("with_studio_runtime_arch_fixture", entry)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidate = tmp_path / "python"
    candidate.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' '{\"platform\":\"darwin\",\"architecture\":\"x86_64\",\"version\":\"3.13.1\"}'\n",
        encoding="utf-8",
    )
    candidate.chmod(0o755)
    monkeypatch.setenv("AIPPT_PYTHON", str(candidate))
    monkeypatch.setattr(python_runtime, "host_identity", lambda: {"platform": "darwin", "architecture": "arm64"})
    monkeypatch.setattr(module.sys, "argv", [str(entry), "--work-dir", str(tmp_path / "work"), "--", "python", str(tmp_path / "never.py")])

    with pytest.raises(SystemExit) as exit_status:
        module.main()

    assert exit_status.value.code == 2
    assert "Rosetta x64 interpreter" in capsys.readouterr().err


def test_public_cli_stops_rosetta_python_before_any_command_work(monkeypatch) -> None:
    """The console-script and ``python -m`` routes share the Typer callback."""

    from aippt_studio import cli, upstream_tools

    monkeypatch.setattr(
        cli.python_runtime,
        "current_process_identity",
        lambda: {"platform": "darwin", "architecture": "x64", "version": "3.13.1"},
    )
    monkeypatch.setattr(
        cli.python_runtime,
        "validate_identity",
        lambda _identity: "Python identity darwin/x64 does not match host darwin/arm64. Use an arm64 Python.",
    )
    monkeypatch.setattr(upstream_tools, "tool_catalog", lambda: pytest.fail("CLI command must not run under Rosetta"))

    result = CliRunner().invoke(cli.app, ["upstream-tools", "--json"])

    assert result.exit_code == 2
    assert "requires a host-native Python" in result.output


def test_public_cli_help_remains_available_when_native_guard_would_fail(monkeypatch) -> None:
    from aippt_studio import cli

    monkeypatch.setattr(
        cli.python_runtime,
        "validate_identity",
        lambda _identity: "would reject a translated runtime",
    )

    result = CliRunner().invoke(cli.app, ["--help"])

    assert result.exit_code == 0
    assert "AI PPT" in result.output


def test_public_subcommand_help_remains_available_when_native_guard_would_fail(monkeypatch) -> None:
    """Click calls the group callback before it parses subcommand help."""

    from aippt_studio import cli

    monkeypatch.setattr(cli.python_runtime, "validate_identity", lambda _identity: "would reject a translated runtime")
    monkeypatch.setattr(cli.sys, "argv", ["aippt", "upstream-tools", "--help"])

    result = CliRunner().invoke(cli.app, ["upstream-tools", "--help"])

    assert result.exit_code == 0
    assert "List real operations" in result.output


def test_evolution_direct_entrypoint_stops_rosetta_before_any_learning_state_or_model_work(monkeypatch, tmp_path, capsys) -> None:
    """The documented semantic-learner module cannot bypass the CLI guard."""

    from aippt_studio import evolution

    marker = tmp_path / "evolution-was-called"
    monkeypatch.setattr(evolution.sys, "argv", ["evolution", "--repo-root", str(tmp_path / "workspace")])
    monkeypatch.setattr(evolution, "current_process_identity", lambda: {
        "platform": "darwin", "architecture": "x64", "version": "3.13.1",
    })
    monkeypatch.setattr(
        evolution,
        "validate_identity",
        lambda _identity: "Python identity darwin/x64 does not match host darwin/arm64. Use an arm64 Python.",
    )
    monkeypatch.setattr(
        evolution,
        "run_evolution",
        lambda *_args, **_kwargs: marker.write_text("called", encoding="utf-8"),
    )

    with pytest.raises(SystemExit) as exit_status:
        evolution.main()

    assert exit_status.value.code == 2
    assert "requires a host-native Python" in capsys.readouterr().err
    assert not marker.exists()


def test_work_corpus_direct_entrypoint_stops_rosetta_before_building_shared_index(monkeypatch, tmp_path, capsys) -> None:
    """A translated direct ``build`` cannot write archive-derived context."""

    from aippt_studio import work_corpus

    marker = tmp_path / "work-corpus-was-called"
    monkeypatch.setattr(work_corpus, "current_process_identity", lambda: {
        "platform": "darwin", "architecture": "x64", "version": "3.13.1",
    })
    monkeypatch.setattr(
        work_corpus,
        "validate_identity",
        lambda _identity: "Python identity darwin/x64 does not match host darwin/arm64. Use an arm64 Python.",
    )
    monkeypatch.setattr(
        work_corpus,
        "build_work_corpus_index",
        lambda *_args, **_kwargs: marker.write_text("called", encoding="utf-8"),
    )

    with pytest.raises(SystemExit) as exit_status:
        work_corpus.main(["build", "--project-root", str(tmp_path / "workspace")])

    assert exit_status.value.code == 2
    assert "requires a host-native Python" in capsys.readouterr().err
    assert not marker.exists()


@pytest.mark.parametrize(
    "module_name, invocation",
    [
        ("evolution", None),
        ("work_corpus", ["--help"]),
    ],
)
def test_standalone_archive_entrypoint_help_remains_available_before_native_guard(monkeypatch, module_name, invocation):
    """Recovery documentation does not require a native runtime to be readable."""

    module = __import__(f"aippt_studio.{module_name}", fromlist=[module_name])
    monkeypatch.setattr(module, "validate_identity", lambda _identity: pytest.fail("--help must parse before the native guard"))
    if module_name == "evolution":
        monkeypatch.setattr(module.sys, "argv", ["evolution", "--help"])
    with pytest.raises(SystemExit) as exit_status:
        module.main() if invocation is None else module.main(invocation)

    assert exit_status.value.code == 0
