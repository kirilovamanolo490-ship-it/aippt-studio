"""Small, dependency-free trust check for the shared Studio Python runtime.

This module deliberately lives below the normal Studio package surface and
uses only the standard library.  Startup wrappers need to inspect a candidate
interpreter *before* they hand it any Studio work, including launchd's archive
worker.  In particular, ``platform.machine()`` on a Rosetta Python reports
``x86_64`` while the physical Mac is Apple Silicon, so platform/version checks
alone are not sufficient.
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional


_IDENTITY_PROBE = (
    "import json,platform,sys;"
    "print(json.dumps({'platform':sys.platform,'architecture':platform.machine(),"
    "'version':'%s.%s.%s' % sys.version_info[:3]}))"
)


# ``sysctl`` is a system trust boundary on macOS.  Do not resolve it through
# PATH: a Rosetta Python can otherwise make a shadow command report a result
# that causes its own x64 process architecture to be mistaken for the host.
SYSTEM_SYSCTL = Path("/usr/sbin/sysctl")
_UNVERIFIED_DARWIN_ARCHITECTURE = "unverified"


def normalize_architecture(value: object) -> str:
    """Normalize process/OS architecture spellings without guessing slices."""

    architecture = str(value or "").strip().lower()
    return {
        "x86_64": "x64",
        "amd64": "x64",
        "aarch64": "arm64",
        "i386": "x86",
        "i686": "x86",
    }.get(architecture, architecture)


def host_identity() -> Dict[str, str]:
    """Return physical host identity, including Apple Silicon under Rosetta.

    ``sysctl -in hw.optional.arm64`` reports the hardware capability without
    failing on Intel Macs.  It lets an x64 launcher running through Rosetta
    still require the arm64 Studio runtime on the Apple-Silicon host.
    """

    process_architecture = normalize_architecture(platform.machine())
    identity = {"platform": sys.platform, "architecture": process_architecture}
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                [str(SYSTEM_SYSCTL), "-in", "hw.optional.arm64"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
            )
            capability = result.stdout.strip() if result.returncode == 0 else ""
            if capability == "1":
                identity["architecture"] = "arm64"
            elif capability == "0":
                # This is a verified physical Intel host, rather than an x64
                # process translated on Apple Silicon.
                identity["architecture"] = "x64"
            elif process_architecture != "arm64":
                # An x64 process cannot prove whether it is native Intel or
                # translated on Apple Silicon without the trusted hardware
                # probe.  Refuse to let it self-identify as a native host.
                identity["architecture"] = _UNVERIFIED_DARWIN_ARCHITECTURE
        except (OSError, subprocess.TimeoutExpired):
            # A native arm64 process is already a safe host signal.  For every
            # other Darwin process, an unavailable trusted probe must fail
            # closed instead of treating a potentially Rosetta x64 process as
            # a native Intel host.
            if process_architecture != "arm64":
                identity["architecture"] = _UNVERIFIED_DARWIN_ARCHITECTURE
    return identity


def current_process_identity() -> Dict[str, str]:
    """Describe this Python process in the same format as a candidate probe."""

    return {
        "platform": sys.platform,
        "architecture": normalize_architecture(platform.machine()),
        "version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }


def validate_identity(identity: object, *, host: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return an actionable failure reason, or ``None`` when identity is safe."""

    if not isinstance(identity, dict):
        return "Python identity probe returned an invalid JSON object"
    expected = host or host_identity()
    expected_platform = str(expected.get("platform") or "")
    expected_architecture = normalize_architecture(expected.get("architecture"))
    actual_platform = str(identity.get("platform") or "")
    actual_architecture = normalize_architecture(identity.get("architecture"))
    if expected_platform == "darwin" and expected_architecture == _UNVERIFIED_DARWIN_ARCHITECTURE:
        return (
            "Physical macOS architecture could not be verified with the trusted "
            f"{SYSTEM_SYSCTL}; refusing to treat a Darwin/{actual_architecture} Python as host-native."
        )
    if actual_platform != expected_platform or actual_architecture != expected_architecture:
        remedy = "Use a Python 3.11+ executable built for this host architecture."
        if expected_platform == "darwin" and expected_architecture == "arm64" and actual_architecture == "x64":
            remedy = "Use an arm64 Python 3.11+ environment instead of a Rosetta x64 interpreter."
        return (
            f"Python identity {actual_platform}/{actual_architecture} does not match host "
            f"{expected_platform}/{expected_architecture}. {remedy}"
        )
    version = str(identity.get("version") or "")
    match = re.fullmatch(r"(\d+)\.(\d+)(?:\.\d+)?", version)
    if match is None:
        return "Python identity version is invalid"
    if (int(match.group(1)), int(match.group(2))) < (3, 11):
        return f"Python {version} is below the required 3.11"
    return None


def probe_python_runtime(executable: Path, *, host: Optional[Dict[str, str]] = None, timeout: int = 20) -> Dict[str, Any]:
    """Probe a candidate through its written launcher, never a resolved target.

    Venv launcher symlinks carry ``pyvenv.cfg`` semantics.  Callers may resolve
    a path separately to reject a hostile/historical target, but this function
    must execute the original launcher path so a valid venv stays a venv.
    """

    launcher = Path(executable).expanduser()
    expected = host or host_identity()
    result = {"status": "failed", "path": str(launcher), "host": expected}
    try:
        completed = subprocess.run(
            [str(launcher), "-c", _IDENTITY_PROBE],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        result["reason"] = f"Python identity probe could not run: {error}"
        return result
    if completed.returncode != 0:
        result["reason"] = (
            f"Python identity probe failed (exit {completed.returncode}): "
            f"{(completed.stderr or completed.stdout).strip()[-1000:]}"
        )
        return result
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    try:
        identity = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        result["reason"] = f"Python identity probe returned invalid JSON: {completed.stdout.strip()[-1000:]}"
        return result
    result["identity"] = identity
    reason = validate_identity(identity, host=expected)
    if reason is None:
        result["status"] = "passed"
    else:
        result["reason"] = reason
    return result
