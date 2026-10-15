#!/usr/bin/env python3
"""Run one Work script with validated host dependencies and argument fidelity."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def historical_runtime_path(path: Path, repo: Path) -> bool:
    """Reject a raw path or symlink target beneath a retired Work/job tree.

    Keep the raw venv launcher for execution: resolving it can bypass
    ``pyvenv.cfg`` and silently run the base interpreter.  Resolution here is
    only for the trust decision, so a friendly symlink cannot hide an old Work
    runtime from the launcher.
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
            if candidate.is_relative_to(repo / "jobs"):
                return True
        except ValueError:
            continue
    return False


def main():
    repo = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--work-dir", type=Path)
    mode.add_argument("--maintenance", action="store_true", help="Run shared maintenance code without creating a Work")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if len(command) < 2 or command[0] not in {"node", "python"}:
        parser.error("use -- node|python SCRIPT [arguments...]")
    configured = os.environ.get("AIPPT_PYTHON")
    relative = ".venv/Scripts/python.exe" if os.name == "nt" else ".venv-macos/bin/python" if sys.platform == "darwin" else ".venv/bin/python"
    python = Path(shutil.which(configured) or configured).expanduser() if configured else repo / "aippt-studio" / relative
    if not python.is_file() or historical_runtime_path(python, repo):
        parser.error("a current Studio Python environment is required; old Work venvs cannot be used")
    with python.open("rb") as stream:
        if os.name != "nt" and stream.read(2) == b"MZ":
            parser.error("Windows Python cannot run on this host")
    # This dependency-free module is imported from Studio source before a
    # candidate interpreter gets to run a Work. It probes through the written
    # venv launcher, so symlink resolution used for trust checks above never
    # loses pyvenv.cfg semantics. On Apple Silicon it also rejects Rosetta x64.
    studio_src = repo / "aippt-studio" / "src"
    if str(studio_src) not in sys.path:
        sys.path.insert(0, str(studio_src))
    from aippt_studio.python_runtime import probe_python_runtime
    probe = probe_python_runtime(python)
    if probe["status"] != "passed":
        parser.error("Studio Python does not match this host or Python 3.11+: " + probe.get("reason", "identity probe failed"))
    if Path(sys.executable).absolute() != python.absolute():
        os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])
    from aippt_studio.platform_runtime import validated_environment, historical_cache, select_artifact_runtime
    from aippt_studio.config import PROJECT_ROOT
    script = Path(command[1]).expanduser().resolve(strict=True)
    work = args.work_dir.expanduser().resolve() if args.work_dir else None
    shared = any(script.is_relative_to(p) for p in (repo / "scripts", PROJECT_ROOT / "scripts", PROJECT_ROOT / "examples"))
    if not shared and not (work and script.is_relative_to(work / "workspace")):
        parser.error("script must belong to this Work workspace or the shared Studio scripts")
    temporary = tempfile.TemporaryDirectory(prefix="aippt-maintenance-") if args.maintenance else None
    try:
        if temporary:
            environment = validated_environment(select_artifact_runtime(Path(temporary.name)))
        else:
            report_path = work / "workspace/runtime-environment.json"
            if not (work / "work.json").is_file() or not report_path.is_file():
                parser.error("Work runtime-environment.json is missing; initialize/select this Work runtime first")
            environment = validated_environment(json.loads(report_path.read_text(encoding="utf-8-sig")))
    except BaseException:
        if temporary:
            temporary.cleanup()
        raise
    env = dict(os.environ)
    env.pop("NODE_OPTIONS", None)
    env.pop("NODE_PATH", None)
    env.pop("RUNTIME_NODE_MODULES", None)
    env.update(environment)
    env["AIPPT_JOBS_DIR"] = str(work / "workspace/studio-jobs") if work else str(Path(temporary.name) / "jobs")
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if command[0] == "node":
        roots = [Path(environment["RUNTIME_NODE_MODULES"])] if environment.get("RUNTIME_NODE_MODULES") else []
        core = PROJECT_ROOT / "node_modules"
        if core.is_dir() and not historical_cache(core):
            roots.append(core.resolve())
        env["AIPPT_NODE_MODULE_ROOTS"] = json.dumps([str(p) for p in roots])
        invocation = [environment["AIPPT_NODE"], "--require", str(repo / "scripts/studio-runtime-preload.cjs"),
                      "--experimental-loader", str(repo / "scripts/studio-runtime-loader.mjs"), str(script), *command[2:]]
    else:
        # Ignore user site/PYTHONPATH and run the chosen venv in isolated mode.
        runner = "import os,runpy,sys;sys.argv=sys.argv[1:];sys.path.insert(0,os.path.dirname(sys.argv[0]));runpy.run_path(sys.argv[0],run_name='__main__')"
        invocation = [str(python), "-I", "-c", runner, str(script), *command[2:]]
    if temporary:
        try:
            status = subprocess.run(invocation, env=env).returncode
        finally:
            temporary.cleanup()
        raise SystemExit(status)
    os.execve(invocation[0], invocation, env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        raise SystemExit(2)
