from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest


BUNDLED_PWSH = Path(__file__).resolve().parents[1] / ".tools/pwsh/pwsh"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell") or (str(BUNDLED_PWSH) if BUNDLED_PWSH.is_file() else None)
from typer.testing import CliRunner

from aippt_studio import cli
from aippt_studio.knowledge import recall_task_knowledge


def _knowledge_root(tmp_path: Path) -> Path:
    root = tmp_path / "任务知识库"
    entries = root / "entries" / "2026"
    entries.mkdir(parents=True)
    (entries / "work-1-template-report.md").write_text(
        """# Work 1：企业模板经营汇报

## 元数据

- Work：1
- 状态：completed
- 任务类型：企业模板继承、经营汇报

## 输入语义

客户秘密项目代号：NEVER-COPY-THIS

## 失败、诊断与恢复

- 模板动画关系未审阅时应阻断，不能静默删除。

## 可复用经验

- 企业模板任务必须保留母版、版式和主题部件。
- 先制作三页样稿确认方向，再扩展整稿。

## 证据位置

- `work/1/workspace/worklog.md`
""",
        encoding="utf-8",
    )
    (entries / "work-2-visual-draft.md").write_text(
        """# Work 2：视觉海报候选

## 元数据

- Work：2
- 状态：review
- 任务类型：视觉候选

## 可复用经验

- 视觉候选必须标记 image_based。
""",
        encoding="utf-8",
    )
    (root / "INDEX.md").write_text(
        """# 任务知识索引

| Work | 日期 | 任务 | 状态 | 条目 |
| ---: | --- | --- | --- | --- |
| 1 | 2026-09-01 | 企业模板经营汇报 | completed | [复盘](entries/2026/work-1-template-report.md) |
| 2 | 2026-09-01 | 视觉海报候选 | review | [复盘](entries/2026/work-2-visual-draft.md) |
""",
        encoding="utf-8",
    )
    return root


def test_recall_returns_only_relevant_completed_entries_without_private_task_facts(tmp_path: Path) -> None:
    result = recall_task_knowledge("制作企业模板经营汇报，先看三页样稿", _knowledge_root(tmp_path), limit=3)

    assert result["status"] == "matched"
    assert result["advisory_only"] is True
    assert result["precedence"][0] == "current_user_request"
    assert [item["work_id"] for item in result["matches"]] == ["1"]
    assert result["schema_version"] == 2
    case = result["matches"][0]
    assert case["trust"] == "legacy_status_only"
    assert case["reuse_status"] == "not_reusable"
    assert case["original_user_acceptance"]["state"] == "explicit_user_acceptance_evidence_unavailable"
    assert "reusable_experience" not in case and "failure_lessons" not in case and "case_history_context" not in case
    assert "NEVER-COPY-THIS" not in json.dumps(result, ensure_ascii=False)
    assert "work-2" not in json.dumps(result, ensure_ascii=False)


def test_recall_records_no_match_instead_of_forcing_an_unrelated_case(tmp_path: Path) -> None:
    result = recall_task_knowledge("量子物理学术答辩", _knowledge_root(tmp_path), limit=3)

    assert result["status"] == "no_match"
    assert result["matches"] == []
    assert result["advisory_only"] is True


def test_recall_ignores_generic_reference_and_priority_words(tmp_path: Path) -> None:
    result = recall_task_knowledge(
        "建立独立任务知识库，让新任务参考历史经验，但保持本轮要求优先",
        _knowledge_root(tmp_path),
        limit=3,
    )

    assert result["status"] == "no_match"
    assert result["matches"] == []


def test_recall_distinguishes_missing_knowledge_base_from_no_match(tmp_path: Path) -> None:
    result = recall_task_knowledge("企业模板经营汇报", tmp_path / "missing", limit=3)

    assert result["status"] == "unavailable"
    assert result["matches"] == []


def test_cli_writes_recall_context_to_requested_work_path(tmp_path: Path) -> None:
    output = tmp_path / "work" / "workspace" / "knowledge-context.json"
    result = CliRunner().invoke(
        cli.app,
        [
            "knowledge-recall",
            "--query",
            "企业模板经营汇报",
            "--knowledge-root",
            str(_knowledge_root(tmp_path)),
            "--output",
            str(output),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert output.is_file()
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["status"] == "matched"
    assert payload["matches"][0]["work_id"] == "1"
    assert payload["matches"][0]["reuse_status"] == "not_reusable"
    assert "reusable_experience" not in payload["matches"][0]


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_new_work_uses_minimal_profile_without_local_historical_context(tmp_path: Path) -> None:
    shell = POWERSHELL
    assert shell is not None
    script = Path(__file__).resolve().parents[2] / "scripts" / "new-work.ps1"
    knowledge_root = _knowledge_root(tmp_path)
    work_root = tmp_path / "work"
    (work_root / "1").mkdir(parents=True)
    completed = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Name",
            "企业模板经营汇报",
            "-Request",
            "先做三页样稿并保留原模板",
            "-WorkRoot",
            str(work_root),
            "-TaskKnowledgeRoot",
            str(knowledge_root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    record = json.loads((work_root / "2" / "work.json").read_text(encoding="utf-8-sig"))
    assert record["storage_profile"] == "minimal"
    assert record["conversation_retention"] == "disabled"
    assert not (work_root / "2" / "workspace" / "knowledge-context.json").exists()
    assert not (work_root / "2" / "workspace" / "assistant-context.json").exists()
    assert sorted(path.name for path in (work_root / "2").iterdir()) == ["input", "output", "work.json", "workspace"]


@pytest.mark.parametrize("schema", [1, 2])
def test_direct_and_context_recall_require_current_original_user_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, schema: int,
) -> None:
    from aippt_studio import context_intelligence as ci
    from aippt_studio import project_learning

    monkeypatch.setattr(ci, "recommend_upstream_tools", lambda query: [])
    knowledge = _knowledge_root(tmp_path)
    entry = knowledge / "entries/2026/work-1-template-report.md"
    record = tmp_path / "work/1/work.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"work_id": "1", "name": "企业模板经营汇报", "status": "completed",
                                  "user_approved": True, "closure_schema_version": schema,
                                  "task_knowledge_entry": entry.relative_to(tmp_path).as_posix(),
                                  "task_knowledge_entry_sha256": hashlib.sha256(entry.read_bytes()).hexdigest()}), encoding="utf-8")
    legacy = recall_task_knowledge("企业模板经营汇报", knowledge)
    current = ci.recall_context("企业模板经营汇报", tmp_path)
    assert legacy["matches"][0]["trust"] == "confirmed_completed"
    assert legacy["matches"][0]["reuse_status"] == "completion_metadata_history"
    assert "reusable_experience" not in legacy["matches"][0]
    assert current["completed_lessons"] == []
    navigation = current["unfinished_cases"][0]
    assert navigation["trust"] == "confirmed_completed"
    assert navigation["reuse_status"] == "completion_metadata_history"
    assert "reusable_experience" not in navigation and "failure_lessons" not in navigation

    monkeypatch.setattr(project_learning, "current_explicit_user_acceptance", lambda _repo, _work: {
        "state": "explicit_original_user_acceptance", "accepted_episode_count": 1,
        "review_phase": "synthesis", "review_receipt_sha256": "b" * 64,
    })
    accepted_legacy = recall_task_knowledge("企业模板经营汇报", knowledge)
    accepted_current = ci.recall_context("企业模板经营汇报", tmp_path)
    assert "母版" in accepted_legacy["matches"][0]["reusable_experience"]
    assert accepted_legacy["matches"][0]["reuse_status"] == "accepted_lesson"
    assert "母版" in accepted_current["completed_lessons"][0]["reusable_experience"]
    assert accepted_current["completed_lessons"][0]["reuse_status"] == "accepted_lesson"

    with entry.open("a", encoding="utf-8") as stream:
        stream.write("\n未经再次批准的模板制作经验。\n")
    changed_legacy = recall_task_knowledge("企业模板经营汇报", knowledge)
    assert changed_legacy["matches"][0]["trust"] == "pending_or_unverified"
    assert changed_legacy["matches"][0]["reuse_status"] == "not_reusable"
    assert "reusable_experience" not in changed_legacy["matches"][0]
    changed_current = ci.recall_context("企业模板经营汇报", tmp_path)
    assert changed_current["completed_lessons"] == []
    assert changed_current["unfinished_cases"][0]["reuse_status"] == "not_reusable"


@pytest.mark.parametrize("metadata", [
    {"status": "completed", "user_approved": True, "closure_schema_version": 2},
    {"status": "in_progress", "user_approved": False},
    {"work_id": "99", "status": "completed", "user_approved": True},
])
def test_legacy_recall_never_promotes_unapproved_or_wrong_work_metadata(tmp_path: Path, metadata: dict) -> None:
    knowledge = _knowledge_root(tmp_path)
    record = tmp_path / "work/1/work.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"work_id": "1", "name": "企业模板经营汇报", **metadata}), encoding="utf-8")
    result = recall_task_knowledge("企业模板经营汇报", knowledge)
    if metadata.get("status") == "completed" and metadata.get("user_approved") is True and metadata.get("work_id", "1") == "1":
        assert result["matches"][0]["reuse_status"] == "completion_metadata_history"
        assert "reusable_experience" not in result["matches"][0]
    else:
        assert result["matches"] == []
