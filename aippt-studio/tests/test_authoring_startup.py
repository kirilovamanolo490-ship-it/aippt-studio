import hashlib
import json
from pathlib import Path

import pytest

from aippt_studio.maintenance import initialize_work


def _minimal_work(root, name, request):
    work = root / name
    (work / "input").mkdir(parents=True)
    (work / "workspace").mkdir()
    (work / "input/request.md").write_text(request, encoding="utf-8")
    (work / "work.json").write_text(json.dumps({
        "work_id": name,
        "name": name,
        "status": "intake",
        "storage_profile": "minimal",
        "conversation_retention": "disabled",
        "task_choice": "keep-this-choice",
    }), encoding="utf-8")
    return work


def test_independent_minimal_works_persist_same_shared_entrypoints_and_request_hashes(tmp_path, monkeypatch):
    from aippt_studio import platform_runtime

    repo = Path(__file__).resolve().parents[2]
    first = _minimal_work(tmp_path / "works", "work-a", "面向管理层说明年度经营结果")
    second = _minimal_work(tmp_path / "works", "work-b", "制作课堂急救培训课件")
    monkeypatch.setattr(platform_runtime, "select_artifact_runtime", lambda *_: {"status": "ready"})

    initialize_work(repo, first)
    initialize_work(repo, second)

    first_context = json.loads((first / "workspace/authoring-context.json").read_text(encoding="utf-8"))
    second_context = json.loads((second / "workspace/authoring-context.json").read_text(encoding="utf-8"))
    expected = [
        ".agents/skills/aippt-studio/SKILL.md",
        ".agents/skills/gaojie-layouts/SKILL.md",
        "aippt-studio/docs/AUTHORING_ENTRYPOINTS.md",
        "aippt-studio/docs/playbook/ppt-production-knowledge-base.md",
    ]
    assert first_context["shared_entrypoints"] == second_context["shared_entrypoints"] == expected
    assert all((repo / entry).is_file() for entry in expected)
    assert first_context["request_sha256"] == hashlib.sha256((first / "input/request.md").read_bytes()).hexdigest()
    assert second_context["request_sha256"] == hashlib.sha256((second / "input/request.md").read_bytes()).hexdigest()
    assert first_context["request_sha256"] != second_context["request_sha256"]
    assert first_context["schema_version"] == 1
    assert first_context["authoring_workflow_version"] == 1
    assert not any("work-a" in value or "work-b" in value for value in first_context["shared_entrypoints"])
    assert not any("work-a" in value or "work-b" in value for value in second_context["shared_entrypoints"])
    assert json.loads((first / "work.json").read_text(encoding="utf-8"))["authoring_workflow_version"] == 1
    assert json.loads((first / "work.json").read_text(encoding="utf-8"))["task_choice"] == "keep-this-choice"


def test_authoring_context_is_saved_before_runtime_failure_and_preserved_on_reinitialize(tmp_path, monkeypatch):
    from aippt_studio import platform_runtime

    repo = Path(__file__).resolve().parents[2]
    work = _minimal_work(tmp_path / "works", "work-c", "只改第三页标题")
    def fail(_):
        raise RuntimeError("optional runtime is unavailable")
    monkeypatch.setattr(platform_runtime, "select_artifact_runtime", fail)

    with pytest.raises(RuntimeError, match="optional runtime is unavailable"):
        initialize_work(repo, work)
    context_path = work / "workspace/authoring-context.json"
    original = context_path.read_bytes()
    changed_request = "另一项当前任务"
    (work / "input/request.md").write_text(changed_request, encoding="utf-8")
    with pytest.raises(RuntimeError, match="optional runtime is unavailable"):
        initialize_work(repo, work)
    assert context_path.read_bytes() == original
    assert json.loads(original)["request_sha256"] == hashlib.sha256("只改第三页标题".encode()).hexdigest()
    assert json.loads((work / "work.json").read_text(encoding="utf-8"))["task_choice"] == "keep-this-choice"
