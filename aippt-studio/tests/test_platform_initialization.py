import json
from pathlib import Path

from typer.testing import CliRunner

from aippt_studio import cli, maintenance, platform_runtime
from aippt_studio import context_intelligence
from aippt_studio.learning import read_json, write_json


def fixture_work(tmp_path):
    work = tmp_path / "work/1"
    (work / "input").mkdir(parents=True)
    (work / "input/request.md").write_text("runtime integration")
    write_json(work / "work.json", {"id": "1", "name": "runtime integration", "status": "active"})
    return work


def test_initialize_writes_identical_runtime_to_environment_and_assistant_context(tmp_path, monkeypatch):
    work = fixture_work(tmp_path)
    runtime = {"schema_version": 1, "status": "ready", "environment": {"AIPPT_NODE": "/host/node"}}
    monkeypatch.setattr(platform_runtime, "select_artifact_runtime", lambda output: runtime)
    monkeypatch.setattr(maintenance, "compile_lessons", lambda repo: {"lessons": []})
    monkeypatch.setattr(context_intelligence, "recall_context", lambda *a, **k: {"catalog_status": "ready", "facts": ["retained"]})
    result = maintenance.initialize_work(tmp_path, work)
    assert result["runtime"] == read_json(work / "workspace/runtime-environment.json")
    assert read_json(work / "workspace/assistant-context.json") == {"catalog_status": "ready", "facts": ["retained"], "runtime_capabilities": runtime}
    assert not (work / "workspace/qa/runtime-doctor").exists()


def test_runtime_select_refreshes_saved_environment_and_existing_context(tmp_path, monkeypatch):
    work = fixture_work(tmp_path)
    old = {"status": "unavailable"}
    updated = {"schema_version": 1, "status": "ready", "environment": {"AIPPT_NODE": "/host/node"}}
    write_json(work / "workspace/runtime-environment.json", old)
    write_json(work / "workspace/assistant-context.json", {"query": "keep source scope", "completed_lessons": ["keep"], "runtime_capabilities": old})
    monkeypatch.setattr(platform_runtime, "select_artifact_runtime", lambda output: updated)
    result = CliRunner().invoke(cli.app, ["runtime-select", "--work-dir", str(work), "--json"])
    assert result.exit_code == 0, result.stdout
    assert read_json(work / "workspace/runtime-environment.json") == updated
    assert read_json(work / "workspace/assistant-context.json") == {"query": "keep source scope", "completed_lessons": ["keep"], "runtime_capabilities": updated}


def test_invalid_context_stops_refresh_before_replacing_environment(tmp_path, monkeypatch):
    work = fixture_work(tmp_path)
    old = {"status": "ready", "original": True}
    write_json(work / "workspace/runtime-environment.json", old)
    (work / "workspace/assistant-context.json").write_text("broken json")
    monkeypatch.setattr(platform_runtime, "select_artifact_runtime", lambda output: (_ for _ in ()).throw(AssertionError("must validate context first")))
    result = CliRunner().invoke(cli.app, ["runtime-select", "--work-dir", str(work), "--json"])
    assert result.exit_code == 2
    assert read_json(work / "workspace/runtime-environment.json") == old


def test_deep_doctor_defaults_to_active_work_qa_and_passes_office_flag(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("AIPPT_JOBS_DIR", str(workspace / "studio-jobs"))
    calls = []
    def doctor(output_dir, *, include_powerpoint):
        calls.append((output_dir, include_powerpoint))
        return {"status": "ready", "core_ready": True, "export_ready": True}
    monkeypatch.setattr(platform_runtime, "runtime_doctor", doctor)
    result = CliRunner().invoke(cli.app, ["doctor", "--deep", "--json"])
    assert result.exit_code == 0, result.stdout
    assert calls == [(workspace / "qa/runtime-doctor", True)]


def test_route_readiness_uses_the_same_native_node_contract(monkeypatch):
    from pathlib import Path
    from aippt_studio import adapters, platform_runtime
    selected = Path('/selected/node')
    calls = []
    def probe(node):
        calls.append(node)
        return {'status': 'failed', 'reason': 'Node 20 does not satisfy locked Chromium dependencies'}
    monkeypatch.setattr(platform_runtime, 'probe_node_runtime', probe)
    assert adapters._node_supported(selected) is False
    assert calls == [selected]
    monkeypatch.setattr(platform_runtime, 'probe_node_runtime', lambda node: {'status': 'passed'})
    assert adapters._node_supported(selected) is True
    assert adapters._node_supported(None) is False
