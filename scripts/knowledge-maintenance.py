#!/usr/bin/env python3
"""Install, inspect or remove the local macOS archive/index worker."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import uuid
from datetime import datetime, timezone

LABEL = "app.aippt.knowledge-maintenance"
ROOT = Path(__file__).resolve().parents[1]
STUDIO_SOURCE = ROOT / "aippt-studio" / "src"
if str(STUDIO_SOURCE) not in sys.path:
    # The runtime identity checker is standard-library only. Import it from
    # source before selecting the interpreter that will own the launchd job.
    sys.path.insert(0, str(STUDIO_SOURCE))
from aippt_studio.python_runtime import probe_python_runtime
from aippt_studio.runtime import historical_runtime_path, system_launchctl


def worker_health(registration: dict, last_run: dict, *, loaded: bool,
                  now: datetime | None = None) -> dict:
    """Separate historical execution proof from current scheduler health."""
    verified = bool(registration.get("instance") and last_run.get("worker_instance") == registration["instance"])
    if not registration.get("instance"):
        verified = last_run.get("worker_service") == LABEL
    age = None
    if verified:
        try:
            stamp = datetime.fromisoformat(last_run["ran_at"].replace("Z", "+00:00"))
            age = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
        except (ValueError, KeyError, TypeError):
            pass
    grace = max(300, int(registration.get("interval_seconds", 900)) * 2 + 60)
    if not loaded:
        health = "not_loaded"
    elif not verified:
        health = "registered_not_yet_verified"
    elif age is None or age < -60 or age > grace:
        health = "stale_or_unknown_run_time"
    else:
        health = "verified" if last_run.get("status") == "ok" else "needs_attention"
    return {"worker_run_verified": verified, "last_worker_status": last_run.get("status") if verified else None,
            "worker_run_age_seconds": age, "health": health}


def python_runtime() -> Path:
    override = os.environ.get("AIPPT_PYTHON")
    choices = [Path(override).expanduser()] if override else [
        ROOT / "aippt-studio/.venv-macos/bin/python", ROOT / "aippt-studio/.venv/bin/python"]
    diagnostics: list[str] = []
    for path in choices:
        if not path.is_file() or not os.access(path, os.X_OK):
            diagnostics.append(f"{path}: not found or not executable")
            continue
        if historical_runtime_path(path):
            diagnostics.append(f"{path}: historical Work or legacy job runtime is not allowed")
            continue
        # A launchd worker must not be allowed to silently run an x64 Python
        # under Rosetta. ``probe_python_runtime`` keeps the written venv
        # launcher path, which preserves pyvenv.cfg while checking its process
        # identity against the physical macOS host.
        probe = probe_python_runtime(path)
        if probe["status"] != "passed":
            diagnostics.append(f"{path}: {probe.get('reason', 'identity probe failed')}")
            continue
        check = subprocess.run([str(path), "-c", "import aippt_studio.maintenance"],
                               capture_output=True, text=True, timeout=20)
        if check.returncode == 0:
            return path.absolute()
        diagnostics.append(f"{path}: Studio maintenance import failed: {(check.stderr or check.stdout).strip()[-1000:]}")
    detail = "; ".join(diagnostics)
    raise RuntimeError("No usable host-native Studio Python. Run platform setup or set AIPPT_PYTHON. " + detail)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "status", "uninstall"])
    parser.add_argument("--interval", type=int, default=900)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("This installer targets macOS launchd. The maintenance CLI itself is cross-platform.")
    launchctl = system_launchctl()
    if launchctl is None:
        parser.error("Trusted macOS launchctl bridge is unavailable.")
    if not 60 <= args.interval <= 86400:
        parser.error("interval must be between 60 and 86400 seconds")
    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    domain = f"gui/{os.getuid()}"
    service = f"{domain}/{LABEL}"
    runtime = ROOT / "任务知识库/runtime"
    registration_path = runtime / "scheduler-registration.json"
    if args.action == "status":
        status = subprocess.run([str(launchctl), "print", service], capture_output=True, text=True)
        def read_optional(path):
            try:
                return json.loads(path.read_text())
            except (ValueError, OSError):
                return {}
        registration = read_optional(registration_path)
        last_run = read_optional(runtime / "last-worker-run.json") or read_optional(runtime / "last-run.json")
        print(json.dumps({"installed": plist.is_file(), "loaded": status.returncode == 0,
                          **worker_health(registration, last_run, loaded=status.returncode == 0),
                          "plist": str(plist), "details": status.stdout or status.stderr}, ensure_ascii=False, indent=2))
        return
    if args.action == "uninstall":
        subprocess.run([str(launchctl), "bootout", service], capture_output=True)
        if plist.is_file():
            plist.unlink()
        registration_path.unlink(missing_ok=True)
        print(json.dumps({"status": "removed", "label": LABEL}))
        return
    runtime.mkdir(parents=True, exist_ok=True)
    python = python_runtime()
    instance = uuid.uuid4().hex
    payload = {"Label": LABEL,
               "ProgramArguments": [str(python), "-m", "aippt_studio.maintenance", "run", "--repo-root", str(ROOT), "--quiet"],
               "WorkingDirectory": str(ROOT), "StartInterval": args.interval, "RunAtLoad": True,
               "ProcessType": "Background", "LowPriorityIO": True,
               "EnvironmentVariables": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8",
                                        "AIPPT_MAINTENANCE_INSTANCE": instance,
                                        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
               "StandardErrorPath": str(runtime / "worker-error.log"),
               "StandardOutPath": str(runtime / "worker-output.log")}
    plist.parent.mkdir(parents=True, exist_ok=True)
    old_bytes = plist.read_bytes() if plist.exists() else None
    old_registration = registration_path.read_bytes() if registration_path.exists() else None
    subprocess.run([str(launchctl), "bootout", service], capture_output=True)
    plist.write_bytes(plistlib.dumps(payload))
    registration_path.write_text(json.dumps({"instance": instance, "interval_seconds": args.interval,
                                             "label": LABEL}, indent=2) + "\n")
    result = subprocess.run([str(launchctl), "bootstrap", domain, str(plist)], capture_output=True, text=True)
    if result.returncode:
        if old_bytes is not None:
            plist.write_bytes(old_bytes)
            subprocess.run([str(launchctl), "bootstrap", domain, str(plist)], capture_output=True)
        else:
            plist.unlink(missing_ok=True)
        if old_registration is not None:
            registration_path.write_bytes(old_registration)
        else:
            registration_path.unlink(missing_ok=True)
        raise RuntimeError(result.stderr.strip() or "launchctl bootstrap failed")
    print(json.dumps({"status": "registered_not_yet_verified", "label": LABEL, "interval_seconds": args.interval,
                      "plist": str(plist), "runtime": str(runtime),
                      "notification_policy": "quiet; error/change state is available in runtime/attention.json"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
