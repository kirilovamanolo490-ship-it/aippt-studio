"""Stage, test and atomically promote bounded core patches, never model commands.

The model may write only ``candidate_dir`` returned by create_candidate. Control
records, baseline and validation trees are outside that writable root. Validation
uses the current trusted Python/pytest and macOS sandbox-exec; unsupported hosts
defer rather than silently running proposed code without process isolation.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone

from filelock import FileLock, Timeout

VERSION = 1
COPY_DIRS = ("aippt-studio/src", "aippt-studio/node", "aippt-studio/scripts",
             "aippt-studio/docs", "aippt-studio/tests",
             "scripts", ".agents/skills/aippt-studio")
COPY_FILES = ("AGENTS.md", "README.md", "aippt-studio/README.md",
              "aippt-studio/pyproject.toml", "aippt-studio/package.json",
              "aippt-studio/package-lock.json", "aippt-studio/uv.lock",
              "aippt-studio/upstreams.lock.json")
DEPENDENCIES = ("aippt-studio/node_modules", "aippt-studio/.tools",
                "aippt-studio/.venv-macos", "aippt-studio/.venv", "github")
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules", ".maintenance"}
PROTECTED_MODULES = {"core_patch.py", "core_evolution.py", "core_learning.py",
                     "core_repair.py", "auto_repair.py", "evolution.py", "maintenance.py",
                     "runtime.py", "platform_runtime.py", "config.py", "repair_context.py",
                     "learning.py", "project_learning.py", "project_repair.py", "lesson_application.py", "conversations.py", "feedback_audit.py", "workflow.py", "storage.py",
                     "models.py", "authoring_policy.py", "expression_review.py", "presentation_quality.py",
                     "reconcile.py", "cli.py"}
PROTECTED_SCRIPTS = {"knowledge-maintenance.py", "studio-runtime.ps1", "run-studio.ps1",
                     "with-studio-runtime.py", "studio-runtime-preload.cjs",
                     "studio-runtime-loader.mjs", "pwsh.sh", "archive-work-conversation.py",
                     "prepare-work-close.ps1", "finish-work.ps1", "reopen-work.ps1",
                     "new-work.ps1", "new-work.sh", "work-studio.sh", "test.ps1"}
PROTECTED_NAMES = {"package.json", "package-lock.json", "pyproject.toml", "uv.lock",
                   "upstreams.lock.json", "pytest.ini", "tox.ini", "setup.cfg",
                   "conftest.py", "requirements.txt", ".env", ".gitmodules", "AGENTS.md", "SKILL.md"}
TEXT_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ps1", ".sh", ".applescript",
                 ".md", ".json", ".html", ".css", ".yaml", ".yml"}
CONTROLLER_SELFTESTS = ("test_real_sandbox_denies_external_writes_and_network",
                        "test_real_before_failure_after_full_pass_and_application",
                        "test_new_test_that_does_not_reproduce_before_failure_is_rejected")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _file(path: Path) -> dict:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Regular files required: {path}")
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": info.st_size, "mode": stat.S_IMODE(info.st_mode)}


def _source_files(repo: Path) -> dict:
    result = {}
    for relative in COPY_FILES:
        path = repo / relative
        if path.exists() or path.is_symlink():
            result[relative] = _file(path)
    for relative in COPY_DIRS:
        base = repo / relative
        if base.is_symlink():
            raise ValueError(f"Source directory cannot be a symlink: {base}")
        if not base.exists():
            continue
        for current, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for name in dirs:
                if (Path(current) / name).is_symlink():
                    raise ValueError("Source trees cannot contain directory symlinks")
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")) or name == ".DS_Store":
                    continue
                path = Path(current) / name
                result[path.relative_to(repo).as_posix()] = _file(path)
    return dict(sorted(result.items()))


def _copy_files(source: Path, destination: Path, files: dict) -> None:
    for relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)


def _dependency_state(repo: Path) -> list[dict]:
    result = []
    for relative in DEPENDENCIES:
        path = repo / relative
        if path.is_dir():
            target = path.resolve()
            info = target.stat()
            result.append({"path": relative, "target": str(target), "device": info.st_dev,
                           "inode": info.st_ino, "mtime_ns": info.st_mtime_ns,
                           "access": "read_only_under_process_sandbox"})
    return result


def _link_dependencies(destination: Path, dependencies: list[dict]) -> None:
    for item in dependencies:
        target = destination / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(item["target"], target_is_directory=True)


def _tree_files(root: Path, dependencies: list[dict]) -> dict:
    """Include unknown files so hidden config, symlinks and test edits cannot hide."""
    links = {item["path"]: item["target"] for item in dependencies}
    result, found = {}, set()
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs) + files:
            path = Path(current) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                if relative not in links or str(path.resolve()) != links[relative]:
                    raise ValueError(f"Unexpected or redirected symlink: {relative}")
                found.add(relative)
                if name in dirs:
                    dirs.remove(name)
            elif name in files:
                result[relative] = _file(path)
    if found != set(links):
        raise ValueError("Readonly dependency links were removed or replaced")
    return dict(sorted(result.items()))


def _registry(repo: Path, stage: Path) -> Path:
    key = hashlib.sha256(str(stage).encode()).hexdigest()
    return repo / "aippt-studio/.maintenance/core-patch-registry" / (key + ".json")


def create_candidate(repo_root: Path, stage_root: Path, *, candidate_id: str | None = None) -> dict:
    repo, stage = Path(repo_root).resolve(), Path(stage_root).absolute()
    if stage.resolve() != stage:
        raise ValueError("Candidate control path cannot traverse symlinks")
    allowed = (repo / "aippt-studio/.maintenance", repo / ".maintenance")
    if not any(stage.is_relative_to(p) and stage != p for p in allowed):
        raise ValueError("Candidate must live under this repository's .maintenance directory")
    if stage.exists():
        raise ValueError("Candidate directory must be new")
    if not (repo / "aippt-studio/src").is_dir() or not (repo / "aippt-studio/tests").is_dir():
        raise ValueError("Expected the AI PPT workspace root")
    files = _source_files(repo)
    dependencies = _dependency_state(repo)
    stage.mkdir(parents=True)
    _copy_files(repo, stage / "before", files)
    _copy_files(repo, stage / "candidate", files)
    _link_dependencies(stage / "before", dependencies)
    _link_dependencies(stage / "candidate", dependencies)
    # Re-read after copy so a parallel edit never produces an incoherent baseline.
    if files != _source_files(repo) or _tree_files(stage / "before", dependencies) != files:
        raise ValueError("Core changed while snapshotting; create a fresh candidate")
    manifest = {"schema_version": VERSION, "candidate_id": candidate_id or uuid.uuid4().hex,
                "created_at": _now(), "repo_root": str(repo), "stage_root": str(stage),
                "candidate_dir": str(stage / "candidate"), "before_dir": str(stage / "before"),
                "source_files": files, "dependencies": dependencies,
                "python": str(Path(sys.executable).absolute()),
                "python_identity": _file(Path(sys.executable).resolve()),
                "model_writable_root": str(stage / "candidate"),
                "limits": "Configure the model's only project writable root as candidate_dir, never stage_root, baseline, dependencies or the main workspace. Codex may additionally allow its own temporary directories; this module does not launch the model."}
    _write(stage / "manifest.json", manifest)
    _write(_registry(repo, stage), {"manifest": manifest, "manifest_sha256": _digest(manifest)})
    return manifest


def _load(stage_root: Path) -> tuple[Path, dict]:
    stage = Path(stage_root).absolute()
    if stage.resolve() != stage:
        raise ValueError("Candidate control path changed")
    manifest = _read(stage / "manifest.json")
    trusted = _read(_registry(Path(manifest["repo_root"]), stage))
    if trusted["manifest"] != manifest or trusted["manifest_sha256"] != _digest(manifest):
        raise ValueError("Candidate control manifest changed")
    if manifest["stage_root"] != str(stage) or manifest["schema_version"] != VERSION:
        raise ValueError("Candidate identity mismatch")
    if _tree_files(stage / "before", manifest["dependencies"]) != manifest["source_files"]:
        raise ValueError("Baseline snapshot changed")
    return stage, manifest


def _allowed_change(relative: str, *, new: bool) -> bool:
    path = Path(relative)
    if path.name == "test_core_patch.py":
        return False
    if relative.startswith("aippt-studio/tests/"):
        return new and path.parent.as_posix() == "aippt-studio/tests" and bool(re.fullmatch(r"test_[a-zA-Z0-9_]+\.py", path.name))
    if path.name in PROTECTED_NAMES or path.name in PROTECTED_MODULES or path.name in PROTECTED_SCRIPTS:
        return False
    if path.name.startswith(("setup-", "install", "update", "core-patch", "core_patch", "core-evolution", "core_evolution")):
        return False
    if any(part.startswith(".") for part in path.parts) and not relative.startswith(".agents/skills/aippt-studio/"):
        return False
    if path.suffix.lower() not in TEXT_SUFFIXES or "lock" in path.name.lower() or "requirements" in path.name.lower():
        return False
    return relative in {"AGENTS.md", "README.md", "aippt-studio/README.md"} or any(
        relative.startswith(prefix + "/") for prefix in COPY_DIRS if prefix != "aippt-studio/tests")


def inspect_candidate(stage_root: Path) -> dict:
    stage, manifest = _load(stage_root)
    original = manifest["source_files"]
    current = _tree_files(stage / "candidate", manifest["dependencies"])
    changes, rejected, diff = [], [], []
    for relative in sorted(original.keys() | current.keys()):
        before, after = original.get(relative), current.get(relative)
        if before == after:
            continue
        reason = None
        if after is None:
            reason = "File deletion is not eligible for automatic promotion"
        elif not _allowed_change(relative, new=before is None):
            reason = "Protected path, existing test, dependency, runtime or updater"
        elif before and before["mode"] != after["mode"] or before is None and after and after["mode"] != 0o644:
            reason = "Permission changes are not eligible"
        try:
            old = (stage / "before" / relative).read_text(encoding="utf-8") if before else ""
            new_text = (stage / "candidate" / relative).read_text(encoding="utf-8") if after else ""
        except UnicodeError:
            reason = "Only UTF-8 source and documentation changes are eligible"
            old = new_text = ""
        entry = {"path": relative, "before": before, "after": after}
        if reason:
            rejected.append({**entry, "reason": reason})
        else:
            changes.append(entry)
        diff.extend(difflib.unified_diff(old.splitlines(True), new_text.splitlines(True), fromfile="a/" + relative, tofile="b/" + relative))
    tests = [c["path"] for c in changes if c["path"].startswith("aippt-studio/tests/")]
    production = [c for c in changes if not c["path"].startswith("aippt-studio/tests/")]
    return {"schema_version": VERSION, "status": "rejected" if rejected else "candidate" if changes else "unchanged",
            "manifest_sha256": _digest(manifest), "candidate_files_sha256": _digest(current),
            "changes": changes, "rejected": rejected, "new_tests": tests,
            "has_production_change": bool(production), "patch": "".join(diff)}


def _profile(writable: list[Path], repo: Path | None = None, readable: list[Path] = (),
             unix_socket_roots: list[Path] = ()) -> str:
    # Every descendant inherits the filesystem/network/AppleEvents restrictions.
    # Native framework discovery and browser child-process IPC still need these
    # measured services; a blanket Mach lookup denial aborts Chrome at startup.
    parts = ["(version 1)", "(allow default)", "(deny network*)", "(deny file-write*)",
             "(deny appleevent-send)", "(deny mach-lookup)", "(deny signal)",
             "(allow signal (target children))"]
    for service in ("com.apple.system.opendirectoryd.libinfo", "com.apple.system.opendirectoryd.membership",
                    "com.apple.hiservices-xpcservice", "com.apple.windowserver.active", "com.apple.CARenderServer",
                    "com.apple.coreservices.launchservicesd", "com.apple.CoreServices.coreservicesd",
                    "com.apple.distributed_notifications@Uv3", "com.apple.system.notification_center", "com.apple.logd",
                    "com.apple.security.syspolicy.exec", "com.apple.trustd.agent"):
        parts.append(f"(allow mach-lookup (global-name {json.dumps(service)}))")
    parts.append('(allow mach-lookup (global-name-prefix "com.google.Chrome.MachPortRendezvousServer."))')
    parts.append('(allow mach-lookup (global-name-prefix "org.chromium.Chromium.MachPortRendezvousServer."))')
    if repo:
        parts.append(f"(deny file-read* (subpath {json.dumps(str(repo))}))")
        # Path.resolve(), dynamic loaders and SQLite need ancestor stat calls;
        # this permits metadata, not reading customer or source file contents.
        parts.append(f"(allow file-read-metadata (subpath {json.dumps(str(repo))}))")
    for path in readable:
        parts.append(f"(allow file-read* (subpath {json.dumps(str(path.resolve()))}))")
    for path in writable:
        parts.append(f"(allow file-read* file-write* (subpath {json.dumps(str(path.resolve()))}))")
    for path in unix_socket_roots:
        # Path-filtered AF_UNIX IPC only: no TCP, UDP, or host socket access.
        parts.append(f"(allow network-bind network-inbound network-outbound (subpath {json.dumps(str(path.resolve()))}))")
    parts.append('(allow file-write* (literal "/dev/null"))')
    return "\n".join(parts)


def _process(command: list[str], *, cwd: Path, env: dict, log: Path, timeout: int) -> dict:
    started = time.monotonic()
    with log.open("wb") as stream:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            code = -1
        finally:
            # Kill only descendants in the process group created for this run.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return {"returncode": code, "timed_out": timed_out, "elapsed_seconds": round(time.monotonic() - started, 3),
            "log": str(log), "log_sha256": _file(log)["sha256"]}


def _sandbox_probe(directory: Path, python: str) -> dict:
    if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
        return {"status": "unavailable", "reason": "Verified macOS sandbox-exec is required; no unrestricted fallback"}
    directory.mkdir(parents=True)
    writable = directory / "writable"
    writable.mkdir()
    sentinel = directory / "outside-sentinel"
    sentinel.write_text("unchanged")
    runtime_tmp = Path(tempfile.mkdtemp(prefix="aippt-probe-", dir=os.environ.get("AIPPT_PATCH_RUNTIME_TMP", "/private/tmp")))
    ipc = runtime_tmp / "ipc"
    ipc.mkdir()
    profile = directory / "probe.sb"
    profile.write_text(_profile([writable, runtime_tmp], unix_socket_roots=[ipc]))
    program = """import json,pathlib,socket,sys,subprocess,os
w=pathlib.Path(sys.argv[1]);s=pathlib.Path(sys.argv[2]);result={}
(w/'allowed').write_text('ok');result['allowed_write']=True
try:s.write_text('changed');result['outside_write_denied']=False
except PermissionError:result['outside_write_denied']=True
sock=socket.socket()
try:sock.bind(('127.0.0.1',0));result['network_denied']=False
except PermissionError:result['network_denied']=True
finally:sock.close()
ipc=pathlib.Path(sys.argv[4]);outside=pathlib.Path(sys.argv[5])
server=socket.socket(socket.AF_UNIX);server.bind(str(ipc/'ok'));server.listen(1)
client=socket.socket(socket.AF_UNIX);client.connect(str(ipc/'ok'))
connection,_=server.accept();client.sendall(b'ok');result['confined_unix_socket_allowed']=connection.recv(2)==b'ok'
client.close();connection.close();server.close()
other=socket.socket(socket.AF_UNIX)
try:other.bind(str(outside/'denied'));result['outside_unix_socket_denied']=False
except PermissionError:result['outside_unix_socket_denied']=True
finally:other.close()
child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(20)'])
child.terminate();child.wait(timeout=5);result['own_child_signal_allowed']=True
try:os.kill(int(sys.argv[3]),0);result['outside_signal_denied']=False
except PermissionError:result['outside_signal_denied']=True
print(json.dumps(result))
"""
    result = _process(["/usr/bin/sandbox-exec", "-f", str(profile), python, "-I", "-c", program,
                       str(writable), str(sentinel), str(os.getpid()), str(ipc), str(runtime_tmp)], cwd=writable, env={"PATH": os.defpath}, log=directory / "probe.log", timeout=30)
    try:
        checks = json.loads((directory / "probe.log").read_text())
    except (ValueError, OSError):
        checks = {}
    passed = result["returncode"] == 0 and checks == {"allowed_write": True, "outside_write_denied": True, "network_denied": True,
        "own_child_signal_allowed": True, "outside_signal_denied": True,
        "confined_unix_socket_allowed": True, "outside_unix_socket_denied": True} and sentinel.read_text() == "unchanged"
    return {"status": "verified" if passed else "failed", "checks": checks, "runtime_temporary_directory": str(runtime_tmp), **result}


def _junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    cases = []
    for case in root.iter("testcase"):
        status = "error" if case.find("error") is not None else "failed" if case.find("failure") is not None else "skipped" if case.find("skipped") is not None else "passed"
        cases.append({"id": case.get("classname", "") + "::" + case.get("name", ""), "status": status,
                      "failure_type": case.find("failure").get("type", "") if case.find("failure") is not None else ""})
    return {"cases": cases, "counts": dict(Counter(c["status"] for c in cases)), "sha256": _file(path)["sha256"]}


def _controller_selftests(stage: Path, manifest: dict, timeout: int) -> dict:
    """Test immutable validator code outside a nested seatbelt, never candidate.

    macOS rejects a second sandbox_apply inside an existing sandbox. These three
    fixed tests exercise their own confined child processes; all tested Python
    and test bytes come from the verified, non-model-writable before snapshot.
    """
    relative = "aippt-studio/tests/test_core_patch.py"
    if relative not in manifest["source_files"]:
        return {"status": "not_applicable", "reason": "Repository has no controller selftests"}
    root = stage / "validation/controller-selftests"
    root.mkdir(parents=True)
    temporary = root / "tmp"
    temporary.mkdir()
    junit = root / "junit.xml"
    before = stage / "before"
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "TZ", "HOME", "USER", "LOGNAME"}}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", TMPDIR=str(temporary))
    bootstrap = "import sys;sys.path.insert(0,sys.argv.pop(1));sys.path.insert(1,sys.argv.pop(1));import pytest;raise SystemExit(pytest.main(sys.argv[1:]))"
    command = [manifest["python"], "-I", "-c", bootstrap, str(before / "aippt-studio/src"), str(before / "aippt-studio/tests"),
               "-q", "-p", "no:cacheprovider", "--import-mode=importlib", "--basetemp", str(temporary / "pytest"),
               "--junitxml", str(junit), *[str(before / relative) + "::" + name for name in CONTROLLER_SELFTESTS]]
    result = _process(command, cwd=before / "aippt-studio", env=env, log=root / "pytest.log", timeout=timeout)
    result.update(command=command, source="verified_before_snapshot_only", candidate_code_executed=False,
                  source_sha256=manifest["source_files"].get("aippt-studio/src/aippt_studio/core_patch.py", {}).get("sha256"),
                  tests_sha256=manifest["source_files"][relative]["sha256"])
    try:
        result["junit"] = _junit(junit)
    except (OSError, ValueError, ET.ParseError) as exc:
        result["junit_error"] = str(exc)
    cases = result.get("junit", {}).get("cases", [])
    expected = {"test_core_patch::" + name for name in CONTROLLER_SELFTESTS}
    passed = {c["id"].split("::")[0].split(".")[-1] + "::" + c["id"].split("::", 1)[1]
              for c in cases if c["status"] == "passed"}
    result["status"] = "passed" if result["returncode"] == 0 and passed == expected and len(cases) == len(expected) else "failed"
    return result


def _run_tests(stage: Path, manifest: dict, label: str, source: Path, files: dict,
               selected: list[str] | None, timeout: int) -> dict:
    root = stage / "validation" / label
    tree, temporary = root / "tree", root / "tmp"
    tree.mkdir(parents=True)
    temporary.mkdir()
    # Chromium on macOS uses MAC_CHROMIUM_TMPDIR and AF_UNIX paths must stay
    # short. Nested validator tests inherit only this dedicated writable root.
    runtime_tmp = Path(tempfile.mkdtemp(prefix="aippt-cp-", dir=os.environ.get("AIPPT_PATCH_RUNTIME_TMP", "/private/tmp")))
    _copy_files(source, tree, files)
    _link_dependencies(tree, manifest["dependencies"])
    expected = _tree_files(tree, manifest["dependencies"])
    junit = temporary / "junit.xml"
    profile = root / "test.sb"
    readable = [tree, Path(manifest["python"]).resolve(), *[Path(d["target"]) for d in manifest["dependencies"]]]
    # Interpreter library trees are read-only even when installed outside repo.
    readable.extend([Path(sys.prefix), Path(sys.base_prefix)])
    # Node follows readonly node_modules links and reads its parent package type.
    package_scope = Path(manifest["repo_root"]) / "aippt-studio/package.json"
    if package_scope.is_file():
        readable.append(package_scope)
    # Trusted harness adaptation, not a source/test edit: DevTools pipe avoids
    # localhost TCP and each launch receives a fresh confined browser profile.
    preload = root / "browser-preload.mjs"
    puppeteer = tree / "aippt-studio/node_modules/puppeteer/lib/puppeteer/puppeteer.js"
    if puppeteer.is_file():
        preload.write_text("import puppeteer from " + json.dumps(puppeteer.resolve().as_uri()) + ";\n"
            "import {mkdtempSync} from 'node:fs';\nimport {join} from 'node:path';\n"
            "const launch=puppeteer.launch.bind(puppeteer);\n"
            "puppeteer.launch=(options={})=>launch({...options,pipe:true,dumpio:true,"
            "userDataDir:mkdtempSync(join(process.env.AIPPT_PATCH_RUNTIME_TMP,'profile-'))});\n")
        readable.append(preload)
    profile.write_text(_profile([temporary, runtime_tmp, root / "pytest.log"], Path(manifest["repo_root"]), readable,
                                unix_socket_roots=[runtime_tmp]))
    # Preserve the caller's home identity without changing or repurposing it.
    # Attempts to write there remain denied by the sandbox.
    env = {key: value for key, value in os.environ.items() if key in {"PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "HOME", "USER", "LOGNAME"}}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "AIPPT_JOBS_DIR": str(temporary / "jobs"), "AIPPT_FAKE_RENDER": "1",
                "AIPPT_CORE_PATCH_VALIDATION": "1", "AIPPT_UPSTREAM_DIR": str(tree / "github"),
                "PYTHONPATH": str(tree / "aippt-studio/src") + os.pathsep + str(tree / "aippt-studio/tests"),
                "AIPPT_PATCH_TMP": str(temporary), "TMPDIR": str(temporary),
                "AIPPT_PATCH_RUNTIME_TMP": str(runtime_tmp), "MAC_CHROMIUM_TMPDIR": str(runtime_tmp),
                "XDG_CACHE_HOME": str(temporary / "cache")})
    if preload.exists():
        env["NODE_OPTIONS"] = "--import " + json.dumps(preload.as_uri())
    bootstrap = "import sys;sys.path.insert(0,sys.argv.pop(1));sys.path.insert(1,sys.argv.pop(1));import pytest;raise SystemExit(pytest.main(sys.argv[1:]))"
    tests = [str(tree / item) for item in selected] if selected is not None else [str(tree / "aippt-studio/tests")]
    command = ["/usr/bin/sandbox-exec", "-f", str(profile), manifest["python"], "-I", "-c", bootstrap,
               str(tree / "aippt-studio/src"), str(tree / "aippt-studio/tests"), "-q", "-p", "no:cacheprovider", "--import-mode=importlib",
               "--basetemp", str(temporary / "pytest"), "--junitxml", str(junit), *tests]
    result = _process(command, cwd=tree / "aippt-studio", env=env, log=root / "pytest.log", timeout=timeout)
    result.update(command=command, source_tree_unchanged=_tree_files(tree, manifest["dependencies"]) == expected,
                  runtime_temporary_directory=str(runtime_tmp), browser_debug_transport="pipe",
                  network_policy="TCP and UDP denied; Unix sockets only within this dedicated temporary directory",
                  browser_preload_sha256=_file(preload)["sha256"] if preload.exists() else None)
    try:
        result["junit"] = _junit(junit)
    except (OSError, ValueError, ET.ParseError) as exc:
        result["junit_error"] = str(exc)
    return result


def _conflicts(manifest: dict) -> list[str]:
    repo = Path(manifest["repo_root"])
    actual = _source_files(repo)
    expected = manifest["source_files"]
    conflicts = [p for p in actual.keys() | expected.keys() if actual.get(p) != expected.get(p)]
    if _dependency_state(repo) != manifest["dependencies"]:
        conflicts.append("readonly_dependency_identity_changed")
    if _file(Path(manifest["python"]).resolve()) != manifest["python_identity"]:
        conflicts.append("python_runtime_changed")
    return sorted(conflicts)


def validate_candidate(stage_root: Path, *, timeout_seconds: int = 900) -> dict:
    stage, manifest = _load(stage_root)
    with FileLock(str(stage / "validation.lock"), timeout=0):
        inspection = inspect_candidate(stage)
        result = {"schema_version": VERSION, "at": _now(), "status": "rejected", "inspection": inspection,
                  "manifest_sha256": _digest(manifest), "candidate_files_sha256": inspection["candidate_files_sha256"],
                  "automatic_code_execution_in_main_workspace": False}
        def finish(status: str, reason: str) -> dict:
            result.update(status=status, reason=reason)
            _write(stage / "validation.json", result)
            # Trusted registry is outside the model's writable candidate tree.
            trusted = _read(_registry(Path(manifest["repo_root"]), stage))
            trusted["validation_sha256"] = _digest(result)
            _write(_registry(Path(manifest["repo_root"]), stage), trusted)
            return result
        if inspection["status"] != "candidate" or not inspection["new_tests"] or not inspection["has_production_change"]:
            return finish("rejected", "Eligible production changes and new regression tests are both required")
        conflicts = _conflicts(manifest)
        if conflicts:
            result["conflicts"] = conflicts
            return finish("deferred", "Source bytes, mode or dependency identity changed")
        if (stage / "validation").exists():
            return finish("deferred", "Validation artifacts already exist; create a fresh candidate to retry")
        result["controller_selftests"] = _controller_selftests(stage, manifest, timeout_seconds)
        if result["controller_selftests"]["status"] not in {"passed", "not_applicable"}:
            return finish("deferred", "Immutable controller selftests failed outside the nested sandbox")
        result["coverage"] = {"controller": "Three fixed immutable controller tests run their own sandboxed child processes outside the suite sandbox",
                              "candidate_suite": "All existing and new tests run under the process sandbox; the same three controller cases explicitly skip unsupported nested sandbox_apply",
                              "skips_are_not_passes": True}
        result["sandbox"] = _sandbox_probe(stage / "validation/sandbox", manifest["python"])
        if result["sandbox"]["status"] != "verified":
            return finish("deferred", "Process isolation is unavailable or failed its real write/network probe")
        baseline = _run_tests(stage, manifest, "baseline", stage / "before", manifest["source_files"], None, timeout_seconds)
        result["baseline"] = baseline
        if baseline["returncode"] != 0 or not baseline["source_tree_unchanged"] or not baseline.get("junit", {}).get("cases"):
            return finish("deferred", "The complete unchanged existing test suite must pass first")
        # A separate baseline plus ONLY the new tests exercises the old bug.
        before_regression = stage / "validation/regression-source"
        _copy_files(stage / "before", before_regression, manifest["source_files"])
        candidate_files = _tree_files(stage / "candidate", manifest["dependencies"])
        new_files = {p: candidate_files[p] for p in inspection["new_tests"]}
        _copy_files(stage / "candidate", before_regression, new_files)
        regression_files = manifest["source_files"] | new_files
        red = _run_tests(stage, manifest, "before-new-tests", before_regression, regression_files, inspection["new_tests"], timeout_seconds)
        result["before_new_tests"] = red
        cases = red.get("junit", {}).get("cases", [])
        failed = [c for c in cases if c["status"] == "failed"]
        expected_modules = {Path(p).stem for p in inspection["new_tests"]}
        failed_modules = {c["id"].split("::")[0].split(".")[-1] for c in failed}
        if red["returncode"] != 1 or not red["source_tree_unchanged"] or not failed or any(c["status"] == "error" for c in cases) or not expected_modules.issubset(failed_modules):
            return finish("rejected", "Every new regression file must demonstrate an actual before failure, not collection/import errors")
        after = _run_tests(stage, manifest, "after", stage / "candidate", candidate_files, None, timeout_seconds)
        result["after"] = after
        after_cases = after.get("junit", {}).get("cases", [])
        before_ids = Counter(c["id"] for c in baseline["junit"]["cases"])
        after_ids = Counter(c["id"] for c in after_cases)
        passed_ids = {c["id"] for c in after_cases if c["status"] == "passed"}
        before_passed = {c["id"] for c in baseline["junit"]["cases"] if c["status"] == "passed"}
        if after["returncode"] != 0 or not after["source_tree_unchanged"] or before_ids - after_ids or not (before_passed | {c["id"] for c in failed}).issubset(passed_ids):
            return finish("rejected", "Full existing and new tests must pass with no missing test cases or source-tree writes")
        if inspect_candidate(stage)["candidate_files_sha256"] != inspection["candidate_files_sha256"]:
            return finish("deferred", "Candidate changed during validation")
        if _conflicts(manifest):
            return finish("deferred", "Main source changed during validation")
        return finish("passed", "Existing suite preserved; added regressions fail before and pass after under verified isolation")


def _replace_bytes(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name("." + path.name + ".core-patch-" + uuid.uuid4().hex)
    try:
        with temporary.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _rollback(repo: Path, stage: Path, changes: list[dict]) -> list[dict]:
    """Recover even after process death between replace and journal update."""
    errors = []
    for change in reversed(changes):
        relative = change["path"]
        path = repo / relative
        try:
            actual = _file(path) if path.exists() else None
            if actual == change["before"]:
                continue
            if actual != change["after"]:
                raise ValueError("Concurrent source change; rollback will not overwrite it")
            if change["before"] is None:
                path.unlink()
            else:
                _replace_bytes(path, (stage / "before" / relative).read_bytes(), change["before"]["mode"])
        except (OSError, ValueError) as exc:
            errors.append({"path": relative, "error": str(exc)})
    return errors


def apply_candidate(repo_root: Path, stage_root: Path) -> dict:
    stage, manifest = _load(stage_root)
    repo = Path(repo_root).resolve()
    if str(repo) != manifest["repo_root"]:
        raise ValueError("Application repository differs from validated source")
    with FileLock(str(repo / "aippt-studio/.maintenance/core-patch-apply.lock"), timeout=0):
        validation = _read(stage / "validation.json")
        trusted = _read(_registry(repo, stage))
        inspection = inspect_candidate(stage)
        receipt = {"schema_version": VERSION, "at": _now(), "status": "deferred", "candidate_id": manifest["candidate_id"],
                   "manifest_sha256": _digest(manifest), "validation_sha256": _digest(validation),
                   "changes": inspection["changes"], "work_acceptance_changed": False,
                   "commands_executed_in_main_workspace": []}
        def finish(reason: str) -> dict:
            receipt["reason"] = reason
            _write(stage / "application.json", receipt)
            return receipt
        if (stage / "applied.json").exists():
            committed = _read(stage / "applied.json")
            bound = not (trusted.get("application_sha256") != _digest(committed)
                    or committed.get("status") != "applied"
                    or committed.get("candidate_id") != manifest["candidate_id"]
                    or committed.get("manifest_sha256") != _digest(manifest)
                    or committed.get("validation_sha256") != _digest(validation)
                    or committed.get("changes") != validation.get("inspection", inspection).get("changes"))
            if bound:
                receipt["status"] = "already_applied"
                return finish("This candidate has already been applied")
            # A crash can occur after writing applied.json but before binding
            # it in the registry. Only a matching in-flight journal permits
            # rollback through the independently validated source snapshots.
            interrupted = stage / "application-journal.json"
            journal = _read(interrupted) if interrupted.exists() else {}
            if journal.get("status") not in {"applying", "rollback_incomplete"} or journal.get("changes") != inspection["changes"]:
                return finish("Application receipt is unbound or altered; cannot recognize it as committed")
        if validation.get("status") != "passed" or trusted.get("validation_sha256") != _digest(validation) or validation.get("manifest_sha256") != _digest(manifest):
            return finish("Missing, failed or altered validation receipt")
        if inspection["status"] != "candidate" or inspection["candidate_files_sha256"] != validation["candidate_files_sha256"]:
            return finish("Candidate differs from the validated patch")
        frozen = stage / "validation/after/tree"
        if _digest(_tree_files(frozen, manifest["dependencies"])) != validation["candidate_files_sha256"]:
            return finish("Validated execution snapshot changed")
        journal_path = stage / "application-journal.json"
        if journal_path.exists():
            journal = _read(journal_path)
            if journal.get("status") in {"applying", "rollback_incomplete"}:
                if journal.get("changes") != inspection["changes"]:
                    return finish("Interrupted application journal differs from the validated patch")
                errors = _rollback(repo, stage, inspection["changes"])
                if not errors:
                    (stage / "applied.json").unlink(missing_ok=True)
                receipt.update(status="rollback_incomplete" if errors else "rolled_back", rollback_errors=errors)
                _write(journal_path, {"status": receipt["status"], "changes": inspection["changes"], "rollback_errors": errors})
                return finish("Recovered an interrupted application; create a fresh candidate before retrying")
        conflicts = _conflicts(manifest)
        if conflicts:
            receipt["conflicts"] = conflicts
            return finish("Source bytes, modes or dependency identity changed; rebase in a fresh candidate")
        applied = []
        try:
            for change in inspection["changes"]:
                relative = change["path"]
                path = repo / relative
                if path.exists() and path.resolve() != path.absolute() or path.parent.resolve() != path.parent.absolute():
                    raise ValueError("Application target traverses a symlink")
                actual = _file(path) if path.exists() else None
                if actual != change["before"]:
                    raise ValueError("Source changed immediately before replacement")
                _write(stage / "application-journal.json", {"status": "applying", "applied": applied, "next": relative, "changes": inspection["changes"]})
                _replace_bytes(path, (frozen / relative).read_bytes(), change["after"]["mode"])
                applied.append(relative)
            if any(_file(repo / c["path"]) != c["after"] for c in inspection["changes"]):
                raise ValueError("Post-application bytes or modes mismatch")
            receipt.update(status="applied", reason="Validated patch applied; file bytes and modes verified")
            # Persist the receipt before declaring commit. Receipt-write failure
            # is a transaction failure and restores the baseline as well.
            _write(stage / "application.json", receipt)
            _write(stage / "applied.json", receipt)
            trusted["application_sha256"] = _digest(receipt)
            _write(_registry(repo, stage), trusted)
        except BaseException as exc:
            rollback_errors = _rollback(repo, stage, inspection["changes"])
            (stage / "applied.json").unlink(missing_ok=True)
            receipt.update(status="rollback_incomplete" if rollback_errors else "rolled_back", error=str(exc), rollback_errors=rollback_errors)
            _write(stage / "application-journal.json", {"status": receipt["status"], "changes": inspection["changes"], "applied": applied, "rollback_errors": rollback_errors})
            return finish("Atomic file replacement failed; rollback outcome recorded")
        try:
            _write(stage / "application-journal.json", {"status": "applied", "applied": applied})
        except OSError as exc:
            # The bound applied receipt is already committed. A final journal
            # housekeeping failure must not mislabel successfully applied code.
            receipt = dict(receipt, journal_warning=str(exc))
        return receipt
