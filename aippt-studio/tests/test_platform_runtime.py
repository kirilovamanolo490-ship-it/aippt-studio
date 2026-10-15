from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys

from PIL import Image
import pytest

from aippt_studio import platform_runtime as pr
from aippt_studio import runtime


def macho(path, cpu=0x100000C):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xcf\xfa\xed\xfe" + struct.pack("<I", cpu) + b"\x00" * 32)
    return path


def pe(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(256)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 60, 128)
    data[128:132] = b"PE\x00\x00"
    struct.pack_into("<H", data, 132, 0x8664)
    path.write_bytes(data)
    return path


def fake_package(root, *, foreign=False):
    package = root / "@oai/artifact-tool"
    package.mkdir(parents=True)
    (package / "package.json").write_text('{"version":"test"}')
    (package / "dist").mkdir()
    (package / "dist/artifact_tool.mjs").write_text("export const Presentation = {}")
    path = package / "node_modules/skia-canvas/lib/skia.node"
    (pe if foreign else macho)(path)
    return root


@pytest.mark.parametrize("system,arch,expected", [("darwin", "arm64", False), ("linux", "x64", False), ("win32", "x64", True)])
def test_pe_header_is_classified_before_any_execution(tmp_path, system, arch, expected):
    path = pe(tmp_path / "native.node")
    assert runtime.binary_identity(path) == {"format": "pe", "architectures": ["x64"]}
    assert runtime.compatible_binary(path, host_platform=system, architecture=arch) is expected


def test_macho_requires_matching_slice_and_host(tmp_path):
    path = macho(tmp_path / "skia.node")
    assert runtime.compatible_binary(path, host_platform="darwin", architecture="arm64")
    assert not runtime.compatible_binary(path, host_platform="darwin", architecture="x64")
    assert not runtime.compatible_binary(path, host_platform="win32", architecture="arm64")


def test_runtime_identity_and_default_binary_check_use_physical_mac_architecture(tmp_path, monkeypatch):
    """A translated Python must not make an Apple-Silicon Mac look like x64."""

    monkeypatch.setattr(runtime, "physical_host_identity", lambda: {"platform": "darwin", "architecture": "arm64"})
    monkeypatch.setattr(runtime.platform, "machine", lambda: "x86_64")
    x64 = macho(tmp_path / "x64-native", cpu=0x1000007)

    assert runtime.host_identity()["architecture"] == "arm64"
    assert not runtime.compatible_binary(x64)


def test_node_probe_rejects_rosetta_node_against_physical_mac_host(tmp_path, monkeypatch):
    """The direct runtime module must not accept an x64 Node under Rosetta."""

    executable = tmp_path / "node"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setattr(pr, "host_identity", lambda: {"platform": "darwin", "architecture": "arm64"})
    monkeypatch.setattr(pr, "compatible_binary", lambda _path: True)
    monkeypatch.setattr(
        pr,
        "_run",
        lambda *args, **kwargs: {
            "status": "passed",
            "stdout": json.dumps(
                {
                    "platform": "darwin",
                    "architecture": "x64",
                    "version": "v24.20.0",
                    "napi": "10",
                    "executable": str(executable),
                }
            ),
        },
    )

    result = pr.probe_node_runtime(executable)

    assert result["status"] == "failed"
    assert "must match the current platform/architecture" in result["reason"]


def test_platform_runtime_direct_entrypoint_stops_rosetta_python_before_select(tmp_path, monkeypatch, capsys):
    """``python -m aippt_studio.platform_runtime`` has no wrapper to save it."""

    monkeypatch.setattr(pr.sys, "argv", ["platform_runtime", "select", "--output-dir", str(tmp_path / "qa")])
    monkeypatch.setattr(pr, "current_process_identity", lambda: {"platform": "darwin", "architecture": "x64", "version": "3.13.1"})
    monkeypatch.setattr(
        pr,
        "validate_identity",
        lambda _identity: "Python identity darwin/x64 does not match host darwin/arm64. Use an arm64 Python.",
    )
    monkeypatch.setattr(pr, "select_artifact_runtime", lambda _output: pytest.fail("Rosetta entrypoint must stop before runtime selection"))

    with pytest.raises(SystemExit) as exit_status:
        pr.main()

    assert exit_status.value.code == 2
    assert "requires a host-native Python" in capsys.readouterr().err


def test_platform_runtime_help_remains_available_when_native_guard_would_fail(monkeypatch, capsys):
    monkeypatch.setattr(pr.sys, "argv", ["platform_runtime", "--help"])
    monkeypatch.setattr(pr, "validate_identity", lambda _identity: "would reject a translated runtime")

    with pytest.raises(SystemExit) as exit_status:
        pr.main()

    assert exit_status.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_truncated_native_header_is_unavailable_not_an_uncaught_probe_error(tmp_path):
    path = tmp_path / "partial.node"
    path.write_bytes(b"\xcf\xfa\xed\xfe")
    assert runtime.binary_identity(path)["format"] == "invalid"
    assert not runtime.compatible_binary(path)


def test_historical_cache_includes_symlink_aliases(tmp_path):
    old = tmp_path / "work/8/workspace/node_modules"
    old.mkdir(parents=True)
    alias = tmp_path / "managed-looking"
    alias.symlink_to(old)
    assert pr.historical_cache(old) and pr.historical_cache(alias)
    assert not pr.historical_cache(tmp_path / "studio/node_modules")


def test_explicit_historical_node_is_rejected_before_any_runtime_probe(tmp_path, monkeypatch):
    old = tmp_path / "work/8/workspace/legacy-node"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/sh\necho should-not-run\n", encoding="utf-8")
    old.chmod(0o755)
    alias = tmp_path / "managed-looking-node"
    alias.symlink_to(old)
    monkeypatch.setenv("AIPPT_NODE", str(alias))
    monkeypatch.setattr(pr, "_run", lambda *args, **kwargs: pytest.fail("historical Node must not be started"))

    assert runtime.resolve_executable("node") is None
    result = pr.probe_node_runtime()
    assert result["status"] == "failed"
    assert "compatible Node" in result["reason"]


def test_custom_runtime_launcher_detects_a_symlink_to_a_historical_work_python(tmp_path):
    import importlib.util

    entry = Path(__file__).resolve().parents[2] / "scripts/with-studio-runtime.py"
    spec = importlib.util.spec_from_file_location("with_studio_runtime_fixture", entry)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    old = tmp_path / "work/8/workspace/python"
    old.parent.mkdir(parents=True)
    old.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    old.chmod(0o755)
    alias = tmp_path / "managed-looking-python"
    alias.symlink_to(old)

    assert module.historical_runtime_path(alias, tmp_path / "repository") is True


def test_historical_windows_cache_is_rejected_without_loading_it(tmp_path, monkeypatch):
    old = fake_package(tmp_path / "work/8/workspace/node_modules", foreign=True)
    monkeypatch.setattr(pr, "probe_node_runtime", lambda: {"status": "passed", "path": "/native/node", "architecture": "arm64"})
    monkeypatch.setattr(pr, "_run", lambda *a, **k: pytest.fail("must not load a historical dependency"))
    result = pr.select_artifact_runtime(tmp_path / "qa", [old])
    assert result["status"] == "unavailable" and result["core_blocking"] is False
    assert result["rejected_candidates"][0]["reason"] == "historical_work_cache_rejected"
    assert result["rejected_candidates"][0]["native_binary"]["format"] == "pe"


def test_directory_presence_without_package_or_native_probe_is_unavailable(tmp_path, monkeypatch):
    root = tmp_path / "node_modules"
    (root / "@oai/artifact-tool").mkdir(parents=True)
    monkeypatch.setattr(pr, "probe_node_runtime", lambda: {"status": "passed", "path": "/native/node", "architecture": "arm64"})
    result = pr.select_artifact_runtime(tmp_path / "qa", [root])
    assert result["status"] == "unavailable" and not result["capabilities"][0]["available"]
    assert "RUNTIME_NODE_MODULES" not in result["environment"]


def test_import_failure_remains_optional_and_retains_actionable_evidence(tmp_path, monkeypatch):
    root = fake_package(tmp_path / "managed/node_modules")
    monkeypatch.setattr(pr, "probe_node_runtime", lambda: {"status": "passed", "path": "/native/node", "architecture": "arm64"})
    monkeypatch.setattr(pr, "compatible_binary", lambda *a, **k: True)
    monkeypatch.setattr(pr, "_run", lambda *a, **k: {"status": "failed", "stderr": "native ABI could not load", "exit_code": 1})
    result = pr.select_artifact_runtime(tmp_path / "qa", [root])
    assert result["core_blocking"] is False and result["optional"] is True
    assert result["rejected_candidates"][0]["probe"]["stderr"] == "native ABI could not load"


def test_success_requires_nonblank_drawing_and_saved_proof_invalidates_after_change(tmp_path, monkeypatch):
    root = fake_package(tmp_path / "managed/node_modules")
    node = {"status": "passed", "path": "/native/node", "architecture": "arm64"}
    monkeypatch.setattr(pr, "probe_node_runtime", lambda *a: node)
    monkeypatch.setattr(pr, "compatible_binary", lambda *a, **k: True)

    def draw(command, **kwargs):
        image = Image.new("RGB", (64, 48), "white")
        image.putpixel((1, 1), (1, 2, 3))
        image.save(command[-1])
        return {"status": "passed", "stdout": '{"presentation_api":true}', "exit_code": 0}

    monkeypatch.setattr(pr, "_run", draw)
    report = pr.select_artifact_runtime(tmp_path / "qa", [root])
    assert report["status"] == "ready"
    assert pr.validated_environment(report)["RUNTIME_NODE_MODULES"] == str(root)
    (root / "@oai/artifact-tool/dist/artifact_tool.mjs").write_text("changed export")
    with pytest.raises(ValueError, match="changed since"):
        pr.validated_environment(report)


def test_blank_drawing_is_not_runtime_readiness(tmp_path, monkeypatch):
    root = fake_package(tmp_path / "managed/node_modules")
    monkeypatch.setattr(pr, "probe_node_runtime", lambda: {"status": "passed", "path": "/native/node", "architecture": "arm64"})
    monkeypatch.setattr(pr, "compatible_binary", lambda *a, **k: True)
    def blank(command, **kwargs):
        Image.new("RGB", (64, 48), "white").save(command[-1])
        return {"status": "passed", "stdout": '{}'}
    monkeypatch.setattr(pr, "_run", blank)
    result = pr.select_artifact_runtime(tmp_path / "qa", [root])
    assert result["status"] == "unavailable" and "nonblank" in result["rejected_candidates"][0]["reason"]


def test_environment_rejects_foreign_platform_and_unapproved_keys():
    report = {"schema_version": 1, "host": runtime.host_identity() | {"platform": "foreign"}, "environment": {}}
    with pytest.raises(ValueError, match="another platform"):
        pr.validated_environment(report)


def test_node_upgrade_invalidates_saved_dependency_probe(monkeypatch):
    before = {"status": "passed", "path": "/native/node", "platform": sys.platform,
              "architecture": "arm64", "version": "v24.0.0", "napi": "10"}
    report = {"schema_version": 1, "host": runtime.host_identity(), "node": before,
              "environment": {"AIPPT_NODE": "/native/node"}}
    monkeypatch.setattr(pr, "probe_node_runtime", lambda *a: before | {"version": "v24.20.0"})
    with pytest.raises(ValueError, match="Node runtime changed"):
        pr.validated_environment(report)
    report.update({"host": runtime.host_identity(), "environment": {"NODE_OPTIONS": "--require old/cache.js"}})
    with pytest.raises(ValueError, match="unsupported keys"):
        pr.validated_environment(report)


def test_live_node_identity_rejects_old_version_even_if_executable_exists(tmp_path, monkeypatch):
    executable = tmp_path / "node"
    executable.write_text("#!/bin/sh\n")
    monkeypatch.setattr(pr, "_run", lambda *a, **k: {"status": "passed", "stdout": json.dumps({"platform": sys.platform, "architecture": "arm64", "version": "v20.0.0"})})
    result = pr.probe_node_runtime(executable)
    assert result["status"] == "failed" and ">=22.12" in result["reason"]


def test_doctor_never_treats_fake_render_as_real_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("AIPPT_FAKE_RENDER", "1")
    result = pr.runtime_doctor(tmp_path / "qa")
    assert result["status"] == "unavailable" and not result["core_ready"]
    assert "FAKE_RENDER" in result["reason"]


def test_doctor_command_exits_nonzero_when_no_real_smoke_was_run(tmp_path):
    env = dict(os.environ, AIPPT_FAKE_RENDER="1")
    completed = subprocess.run([sys.executable, "-m", "aippt_studio.platform_runtime", "doctor", "--output-dir", str(tmp_path / "qa")],
                               env=env, capture_output=True, text=True)
    assert completed.returncode == 1 and json.loads(completed.stdout)["core_ready"] is False


def test_probe_does_not_inherit_node_path_or_injected_require(monkeypatch):
    monkeypatch.setenv("NODE_OPTIONS", "--require /old-work/cache.js")
    monkeypatch.setenv("NODE_PATH", "/old-work/node_modules")
    def run(command, **kwargs):
        assert "NODE_OPTIONS" not in kwargs["env"] and "NODE_PATH" not in kwargs["env"]
        return subprocess.CompletedProcess(command, 0, "okay", "")
    monkeypatch.setattr(subprocess, "run", run)
    assert pr._run(["node", "-p", "1"])["status"] == "passed"


@pytest.fixture
def live_work(tmp_path, monkeypatch):
    monkeypatch.delenv("AIPPT_NODE", raising=False)
    monkeypatch.delenv("AIPPT_PYTHON", raising=False)
    work = tmp_path / "Work 中文"
    workspace = work / "workspace"
    workspace.mkdir(parents=True)
    (work / "work.json").write_text('{"id":"scratch"}')
    report = pr.select_artifact_runtime(workspace / "probes")
    if report["status"] != "ready":
        pytest.skip("A live managed Artifact runtime is unavailable on this test host")
    (workspace / "runtime-environment.json").write_text(json.dumps(report))
    return work


def launch(work, runtime_name, script, arguments):
    entry = Path(__file__).resolve().parents[2] / "scripts/with-studio-runtime.py"
    return subprocess.run([sys.executable, str(entry), "--work-dir", str(work), "--", runtime_name, str(script), *arguments],
                          capture_output=True, text=True, timeout=30)


def test_custom_node_launcher_imports_managed_esm_preserves_arguments_and_ignores_local_cache(live_work):
    workspace = live_work / "workspace"
    stale = workspace / "node_modules/@oai/artifact-tool"
    stale.mkdir(parents=True)
    (stale / "package.json").write_text('{"type":"module","main":"index.mjs"}')
    (stale / "index.mjs").write_text("throw new Error('DO NOT LOAD OLD WORK CACHE')")
    script = workspace / "中文 验证.mjs"
    script.write_text("import {Presentation} from '@oai/artifact-tool';import PptxGenJS from 'pptxgenjs';console.log(JSON.stringify({args:process.argv.slice(2),api:typeof Presentation.create,pptx:typeof PptxGenJS}));")
    arguments = ["", "含 空格", 'quoted"literal', "$variable`literal", "--flag"]
    result = launch(live_work, "node", script, arguments)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"args": arguments, "api": "function", "pptx": "function"}
    assert (stale / "index.mjs").read_text().startswith("throw new Error"), "existing cache must remain untouched"


def test_custom_python_launcher_uses_current_venv_and_local_helpers(live_work):
    workspace = live_work / "workspace"
    (workspace / "helper.py").write_text("value = 'local helper'\n")
    script = workspace / "参数.py"
    script.write_text("import json,sys,helper;from PIL import Image;print(json.dumps({'args':sys.argv[1:],'helper':helper.value,'image':Image.new('RGB',(2,2)).size}))")
    result = launch(live_work, "python", script, ["", "中文 参数", "--flag"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"args": ["", "中文 参数", "--flag"], "helper": "local helper", "image": [2, 2]}
    assert not (workspace / "node_modules").exists()


def test_launcher_rejects_script_from_another_work(live_work, tmp_path):
    script = tmp_path / "other-work.py"
    script.write_text("raise RuntimeError('must never run')")
    result = launch(live_work, "python", script, [])
    assert result.returncode == 2 and "must belong to this Work" in result.stderr
    assert "RuntimeError" not in result.stderr


def test_commonjs_require_uses_verified_core_instead_of_local_cache(live_work):
    workspace = live_work / "workspace"
    stale = workspace / "node_modules/pptxgenjs"
    stale.mkdir(parents=True)
    (stale / "package.json").write_text('{"main":"index.cjs"}')
    (stale / "index.cjs").write_text("throw new Error('OLD CACHE MUST NOT RUN')")
    script = workspace / "probe.cjs"
    script.write_text("const PptxGenJS=require('pptxgenjs');console.log(JSON.stringify({native:typeof PptxGenJS==='function',args:process.argv.slice(2)}))")
    result = launch(live_work, "node", script, ["", "中文"])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"native": True, "args": ["", "中文"]}
