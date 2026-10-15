import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aippt_studio import maintenance

spec = importlib.util.spec_from_file_location("scheduler", Path(__file__).resolve().parents[2] / "scripts/knowledge-maintenance.py")
scheduler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scheduler)
NOW = datetime(2026, 9, 15, tzinfo=timezone.utc)


def run_record(**kwargs):
    return {"worker_instance": "current", "ran_at": NOW.isoformat(), "status": "ok", **kwargs}


def test_registered_worker_needs_matching_actual_execution():
    registration = {"instance": "current"}
    assert scheduler.worker_health(registration, {}, loaded=True, now=NOW)["health"] == "registered_not_yet_verified"
    assert not scheduler.worker_health(registration, run_record(worker_instance="old"), loaded=True, now=NOW)["worker_run_verified"]
    assert scheduler.worker_health(registration, run_record(), loaded=True, now=NOW)["health"] == "verified"


def test_stopped_or_stale_worker_is_not_healthy():
    registration = {"instance": "current", "interval_seconds": 900}
    assert scheduler.worker_health(registration, run_record(), loaded=False, now=NOW)["health"] == "not_loaded"
    old = run_record(ran_at=(NOW - timedelta(hours=1)).isoformat())
    assert scheduler.worker_health(registration, old, loaded=True, now=NOW)["health"] == "stale_or_unknown_run_time"


def test_legacy_worker_identity_and_failed_run_are_distinguished():
    record = run_record(worker_service=scheduler.LABEL)
    assert scheduler.worker_health({}, record, loaded=True, now=NOW)["health"] == "verified"
    assert not scheduler.worker_health({}, run_record(worker_service="0"), loaded=True, now=NOW)["worker_run_verified"]
    assert scheduler.worker_health({"instance": "current"}, run_record(status="partial"), loaded=True, now=NOW)["health"] == "needs_attention"


def test_launchd_runtime_selector_rejects_rosetta_python_before_maintenance_import(tmp_path, monkeypatch):
    """Archive automation shares the host-native Python boundary with Work wrappers."""

    candidate = tmp_path / "Studio Python"
    candidate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    candidate.chmod(0o755)
    monkeypatch.setenv("AIPPT_PYTHON", str(candidate))
    monkeypatch.setattr(
        scheduler,
        "probe_python_runtime",
        lambda _path: {
            "status": "failed",
            "reason": "Python identity darwin/x64 does not match host darwin/arm64. Use an arm64 Python 3.11+ environment instead of a Rosetta x64 interpreter.",
        },
    )
    monkeypatch.setattr(scheduler.subprocess, "run", lambda *args, **kwargs: pytest.fail("foreign Python must not import maintenance"))

    with pytest.raises(RuntimeError, match="Rosetta x64 interpreter"):
        scheduler.python_runtime()


def test_launchd_runtime_selector_rejects_a_symlink_to_historical_work_python(tmp_path, monkeypatch):
    """A background archive run cannot borrow an old Work's interpreter either."""

    old = tmp_path / "work" / "8" / "workspace" / "python"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    old.chmod(0o755)
    alias = tmp_path / "managed-looking-python"
    alias.symlink_to(old)
    monkeypatch.setenv("AIPPT_PYTHON", str(alias))
    monkeypatch.setattr(scheduler, "probe_python_runtime", lambda _path: pytest.fail("historical Python must not be probed"))

    with pytest.raises(RuntimeError, match="historical Work or legacy job runtime"):
        scheduler.python_runtime()


def test_running_launchd_worker_rechecks_its_own_python_identity(monkeypatch, capsys):
    """The existing plist invokes maintenance directly, so runtime is checked again there."""

    monkeypatch.setattr(maintenance, "current_process_identity", lambda: {
        "platform": "darwin", "architecture": "x64", "version": "3.13.1",
    })
    monkeypatch.setattr(
        maintenance,
        "validate_identity",
        lambda _identity: "Python identity darwin/x64 does not match host darwin/arm64. Use an arm64 Python 3.11+ environment instead of a Rosetta x64 interpreter.",
    )
    monkeypatch.setattr(maintenance.sys, "argv", ["maintenance", "run", "--repo-root", "/tmp/never"])

    with pytest.raises(SystemExit) as exit_status:
        maintenance.main()

    assert exit_status.value.code == 2
    error = capsys.readouterr().err
    assert "host-native Python" in error
    assert "Rosetta x64 interpreter" in error


def test_translated_maintenance_still_exposes_help_before_runtime_gate(monkeypatch, capsys):
    """A translated interpreter needs a usable recovery/help path."""

    monkeypatch.setattr(maintenance, "current_process_identity", lambda: {
        "platform": "darwin", "architecture": "x64", "version": "3.13.1",
    })
    monkeypatch.setattr(maintenance, "validate_identity", lambda _identity: pytest.fail("--help must parse before runtime validation"))
    monkeypatch.setattr(maintenance.sys, "argv", ["maintenance", "--help"])

    with pytest.raises(SystemExit) as exit_status:
        maintenance.main()

    assert exit_status.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_maintenance_wake_uses_the_trusted_launchctl_path(monkeypatch):
    """A PATH shadow must not receive launchd control-plane calls."""

    calls = []

    class Result:
        returncode = 0

    def fake_run(args, **_kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(maintenance.sys, "platform", "darwin")
    monkeypatch.setattr(maintenance, "system_launchctl", lambda: Path("/bin/launchctl"))
    monkeypatch.setattr(maintenance.subprocess, "run", fake_run)

    result = maintenance.wake_maintenance_worker()

    assert result["status"] == "dispatched"
    assert all(call[0] == "/bin/launchctl" for call in calls)
    assert [call[1] for call in calls] == ["print", "kickstart"]


def test_scheduler_status_uses_the_trusted_launchctl_path(monkeypatch, capsys):
    """The installer/status tool follows the same bridge rule as maintenance."""

    calls = []

    class Result:
        returncode = 0
        stdout = "service"
        stderr = ""

    def fake_run(args, **_kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(scheduler.sys, "platform", "darwin")
    monkeypatch.setattr(scheduler, "system_launchctl", lambda: Path("/bin/launchctl"))
    monkeypatch.setattr(scheduler.subprocess, "run", fake_run)
    monkeypatch.setattr(scheduler.sys, "argv", ["knowledge-maintenance", "status"])

    scheduler.main()

    assert calls == [["/bin/launchctl", "print", f"gui/{scheduler.os.getuid()}/{scheduler.LABEL}"]]
    assert json.loads(capsys.readouterr().out)["loaded"] is True


def test_quiet_worker_records_partial_attention_without_exiting_as_a_crash(tmp_path, monkeypatch):
    """The scheduled worker must retry a recorded backlog rather than die on it."""

    monkeypatch.setattr(maintenance, "run_maintenance", lambda *_args, **_kwargs: {"status": "partial", "errors": [{"stage": "fixture"}]})
    monkeypatch.setattr(maintenance.sys, "argv", [
        "knowledge-maintenance", "run", "--repo-root", str(tmp_path), "--quiet",
    ])

    maintenance.main()


def test_interactive_partial_maintenance_still_returns_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "run_maintenance", lambda *_args, **_kwargs: {"status": "partial", "errors": [{"stage": "fixture"}]})
    monkeypatch.setattr(maintenance.sys, "argv", ["knowledge-maintenance", "run", "--repo-root", str(tmp_path)])

    with pytest.raises(SystemExit) as exit_status:
        maintenance.main()

    assert exit_status.value.code == 1
