"""Public archive writers share the macOS host-native runtime boundary."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest

from aippt_studio import context_intelligence, conversations, tool_evidence


_ROSETTA_REASON = (
    "Python identity darwin/x64 does not match host darwin/arm64. "
    "Use an arm64 Python 3.11+ environment instead of a Rosetta x64 interpreter."
)


def _translated_runtime(monkeypatch, module) -> None:
    monkeypatch.setattr(module, "current_process_identity", lambda: {
        "platform": "darwin", "architecture": "x64", "version": "3.13.1",
    })
    monkeypatch.setattr(module, "validate_identity", lambda _identity: _ROSETTA_REASON)


def test_conversation_writer_stops_rosetta_before_archive_refresh(monkeypatch, tmp_path, capsys):
    _translated_runtime(monkeypatch, conversations)
    monkeypatch.setattr(conversations, "refresh_work_conversations", lambda *_args, **_kwargs: pytest.fail("archive refresh must not run"))

    with pytest.raises(SystemExit) as exit_status:
        conversations.main(["refresh", "--work-dir", str(tmp_path)])

    assert exit_status.value.code == 2
    assert "host-native Python" in capsys.readouterr().err


def test_archive_wrapper_delegates_to_the_guarded_conversation_main(monkeypatch, tmp_path):
    """The documented script imports the guarded module entrypoint rather than copying logic."""

    called = []

    def guarded_main():
        called.append(True)
        raise SystemExit(2)

    monkeypatch.setattr(conversations, "main", guarded_main)
    monkeypatch.setattr(sys, "argv", ["archive-work-conversation.py", "refresh", "--work-dir", str(tmp_path)])

    with pytest.raises(SystemExit) as exit_status:
        runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/archive-work-conversation.py"), run_name="__main__")

    assert exit_status.value.code == 2
    assert called == [True]


def test_context_writer_stops_rosetta_before_rebuilding_shared_index(monkeypatch, tmp_path, capsys):
    _translated_runtime(monkeypatch, context_intelligence)
    monkeypatch.setattr(context_intelligence, "build_case_index", lambda *_args, **_kwargs: pytest.fail("case index must not be written"))

    with pytest.raises(SystemExit) as exit_status:
        context_intelligence.main(["build", "--project-root", str(tmp_path)])

    assert exit_status.value.code == 2
    assert "host-native Python" in capsys.readouterr().err


def test_tool_evidence_writer_stops_rosetta_before_collecting_or_writing(monkeypatch, tmp_path, capsys):
    _translated_runtime(monkeypatch, tool_evidence)
    monkeypatch.setattr(tool_evidence, "collect_tool_evidence", lambda *_args, **_kwargs: pytest.fail("closure summary must not run"))
    summary = tmp_path / "workspace" / "tool-evidence.json"

    with pytest.raises(SystemExit) as exit_status:
        tool_evidence.main(["--work-dir", str(tmp_path), "--summary", str(summary)])

    assert exit_status.value.code == 2
    assert "host-native Python" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("module", "argv"),
    [
        (conversations, ["--help"]),
        (context_intelligence, ["--help"]),
        (tool_evidence, ["--help"]),
    ],
)
def test_archive_entrypoint_help_remains_available_before_runtime_gate(monkeypatch, module, argv, capsys):
    _translated_runtime(monkeypatch, module)
    monkeypatch.setattr(module, "validate_identity", lambda _identity: pytest.fail("--help must parse before runtime validation"))

    with pytest.raises(SystemExit) as exit_status:
        module.main(argv)

    assert exit_status.value.code == 0
    assert "usage:" in capsys.readouterr().out
