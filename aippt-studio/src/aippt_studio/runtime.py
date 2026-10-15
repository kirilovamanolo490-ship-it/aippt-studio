"""Host runtime discovery shared by preflight, extraction and rendering."""
from __future__ import annotations
import json
import os
import platform
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from .config import PROJECT_ROOT
from .python_runtime import host_identity as physical_host_identity, normalize_architecture


SYSTEM_OSASCRIPT = Path("/usr/bin/osascript")
SYSTEM_LAUNCHCTL = Path("/bin/launchctl")


def system_osascript() -> Path | None:
    """Return the one macOS AppleScript bridge Studio is allowed to execute.

    PowerPoint automation must not resolve ``osascript`` through an inherited
    PATH: otherwise preflight could inspect the system bridge while a later
    subprocess starts a shadow executable.  Callers already gate their macOS
    branches, so this resolver deliberately checks the canonical host path
    rather than looking up a command name.
    """

    candidate = SYSTEM_OSASCRIPT
    try:
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None
    except OSError:
        return None


def system_launchctl() -> Path | None:
    """Return the launchd control binary without resolving it through PATH.

    Scheduler installation, status and wake-up are local control-plane
    operations. They must not look healthy merely because a project-local
    executable named ``launchctl`` answered a probe. Keeping this alongside
    ``system_osascript`` gives callers one verified system bridge.
    """

    candidate = SYSTEM_LAUNCHCTL
    try:
        return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None
    except OSError:
        return None


def host_identity() -> dict:
    """Describe the physical host rather than a potentially Rosetta process.

    Runtime selection is allowed to run from a Python process, but it must not
    mistake that process's translated ``platform.machine()`` value for the
    architecture of an Apple-Silicon Mac.  ``python_runtime`` uses the
    hardware capability probe on Darwin; keeping the executable/version here
    preserves the richer runtime receipt used by existing callers.
    """

    physical = physical_host_identity()
    return {
        "platform": physical["platform"],
        "architecture": physical["architecture"],
        "python": sys.executable,
        "python_version": platform.python_version(),
    }


def binary_identity(path: Path) -> dict:
    """Inspect native file headers without trying to execute a foreign binary."""
    with Path(path).open("rb") as stream:
        data = stream.read(4096)
    result = {"format": "script_or_data", "architectures": []}
    if data.startswith(b"MZ"):
        result["format"] = "pe"
        if len(data) >= 64:
            offset = struct.unpack_from("<I", data, 60)[0]
            if offset + 6 <= len(data) and data[offset:offset + 4] == b"PE\x00\x00":
                machine = struct.unpack_from("<H", data, offset + 4)[0]
                result["architectures"] = [{0x8664: "x64", 0xAA64: "arm64", 0x14C: "x86"}.get(machine, hex(machine))]
    elif data[:4] in {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce"}:
        if len(data) < 8:
            return {"format": "invalid", "architectures": []}
        result["format"] = "mach-o"
        endian = "<" if data[:1] in {b"\xcf", b"\xce"} else ">"
        cpu = struct.unpack_from(endian + "I", data, 4)[0]
        result["architectures"] = [{0x100000C: "arm64", 0x1000007: "x64", 7: "x86"}.get(cpu, hex(cpu))]
    elif data[:4] in {b"\xca\xfe\xba\xbe", b"\xca\xfe\xba\xbf"}:
        if len(data) < 8:
            return {"format": "invalid", "architectures": []}
        result["format"] = "mach-o"
        count = min(struct.unpack_from(">I", data, 4)[0], 32)
        width = 32 if data[:4] == b"\xca\xfe\xba\xbf" else 20
        for i in range(count):
            if 8 + i * width + 4 <= len(data):
                cpu = struct.unpack_from(">I", data, 8 + i * width)[0]
                result["architectures"].append({0x100000C: "arm64", 0x1000007: "x64", 7: "x86"}.get(cpu, hex(cpu)))
    elif data.startswith(b"\x7fELF"):
        result["format"] = "elf"
        if len(data) >= 20:
            machine = struct.unpack_from(("<" if data[5] == 1 else ">") + "H", data, 18)[0]
            result["architectures"] = [{62: "x64", 183: "arm64", 3: "x86"}.get(machine, hex(machine))]
    return result


def compatible_binary(path: Path, *, host_platform: str | None = None, architecture: str | None = None) -> bool:
    identity = binary_identity(path)
    physical = physical_host_identity()
    system = host_platform or physical["platform"]
    arch = normalize_architecture(architecture or physical["architecture"])
    expected = "pe" if system == "win32" else "mach-o" if system == "darwin" else "elf"
    return (identity["format"] == "script_or_data" or
            (identity["format"] == expected and (not identity["architectures"] or arch in identity["architectures"])))


def historical_runtime_path(path: Path) -> bool:
    """Return whether a binary resolves into a retired Work or legacy job tree.

    Work packages are allowed to contain user scripts and task evidence, but
    never shared host runtimes.  Checking both the written path and its
    resolved target prevents a friendly-looking symlink from reintroducing a
    copied Windows or stale native binary into a new task.
    """

    raw = Path(path).expanduser()
    try:
        candidates = (raw.absolute(), raw.resolve())
    except OSError:
        candidates = (raw.absolute(),)
    for candidate in candidates:
        parts = candidate.parts
        if any(part.lower() == "work" and index + 1 < len(parts) and parts[index + 1].isdigit()
               for index, part in enumerate(parts)):
            return True
        try:
            if candidate.is_relative_to(PROJECT_ROOT.parent / "jobs"):
                return True
        except ValueError:
            continue
    return False


def executable_file(path: Path) -> bool:
    path = Path(path).expanduser()
    if historical_runtime_path(path):
        return False
    if not path.is_file():
        return False
    if os.name != 'nt':
        if not os.access(path, os.X_OK):
            return False
    try:
        if not compatible_binary(path):
            return False
    except (OSError, struct.error):
        return False
    return True


def resolve_executable(name: str, candidates: tuple[Path, ...] = ()) -> Path | None:
    configured = os.environ.get(f'AIPPT_{name.upper()}')
    if configured:
        path = Path(shutil.which(configured) or configured).expanduser()
        return path.resolve() if executable_file(path) else None
    found = shutil.which(name)
    for candidate in ([Path(found)] if found else []) + list(candidates):
        if executable_file(candidate):
            return candidate.resolve()
    return None


def officecli_executable() -> Path | None:
    return resolve_executable('officecli', (PROJECT_ROOT / '.tools' / ('officecli.exe' if os.name == 'nt' else 'officecli'),))


def executable_version(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        result = subprocess.run([str(path), '--version'], capture_output=True, text=True, timeout=10, check=False)
        return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def chromium_executable() -> Path | None:
    configured = os.environ.get('AIPPT_CHROMIUM')
    if configured:
        p = Path(configured).expanduser()
        return p.resolve() if executable_file(p) else None
    config = json.loads((PROJECT_ROOT / 'node/browser-paths.json').read_text())
    for value in config.get(sys.platform, []):
        if value.startswith('$LOCALAPPDATA') and not os.environ.get('LOCALAPPDATA'):
            continue
        p = Path(value.replace('$LOCALAPPDATA', os.environ.get('LOCALAPPDATA', ''))).expanduser()
        if executable_file(p):
            return p.resolve()
    for name in ('google-chrome', 'chromium', 'chromium-browser', 'microsoft-edge'):
        if found := shutil.which(name):
            return Path(found).resolve()
    return None
