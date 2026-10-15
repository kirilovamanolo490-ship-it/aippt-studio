from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aippt_studio import renderer


def test_officecli_validate_always_closes_resident_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIPPT_FAKE_RENDER")
    executable = tmp_path / "officecli.exe"
    executable.touch()
    deck = tmp_path / "deck.pptx"
    deck.touch()
    monkeypatch.setattr(renderer.shutil, "which", lambda name: str(executable) if name == "officecli" else None)
    monkeypatch.setattr(renderer, "audit_master_layout_ids", lambda path: {"master_count": 1, "layout_count": 1})
    commands: list[list[str]] = []

    def run(command: list[str], *, timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        stdout = "1.0.135" if command[1] == "--version" else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(renderer, "_run_command", run)

    result = renderer.officecli_validate(deck)

    assert result == {"status": "passed", "version": "1.0.135"}
    assert [command[1] for command in commands] == ["validate", "close", "--version"]


def test_officecli_validate_rejects_duplicate_master_layout_ids_before_officecli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("AIPPT_FAKE_RENDER")
    deck = tmp_path / "deck.pptx"
    deck.touch()

    def reject(_: Path) -> dict[str, object]:
        raise ValueError("PowerPoint master/layout IDs must be globally unique: 2147483649")

    monkeypatch.setattr(renderer, "audit_master_layout_ids", reject)
    monkeypatch.setattr(
        renderer,
        "_run_command",
        lambda *args, **kwargs: pytest.fail("OfficeCLI must not run after the package audit fails"),
    )

    with pytest.raises(renderer.RenderError, match=r"master/layout audit failed: .*2147483649"):
        renderer.officecli_validate(deck)


def test_powerpoint_roundtrip_reports_candidate_failure_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renderer, "_is_macos", lambda: False)
    report = {
        "status": "failed",
        "phase": "candidate_open",
        "classification": "candidate",
        "hresult": "0x80070570",
        "message": "The file is damaged",
    }
    monkeypatch.setattr(
        renderer,
        "_run_command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout=json.dumps(report), stderr=""
        ),
    )

    with pytest.raises(renderer.PowerPointRoundtripError, match=r"candidate_open.*0x80070570") as raised:
        renderer.powerpoint_roundtrip(tmp_path / "candidate.pptx", tmp_path / "output")

    assert raised.value.report == report


def test_powerpoint_roundtrip_uses_reference_deck_for_environment_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(renderer, "_is_macos", lambda: False)
    output = tmp_path / "output"
    commands: list[list[str]] = []

    def run(command: list[str], *, timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        (output / "slides").mkdir(parents=True, exist_ok=True)
        (output / "slides" / "slide-1.png").write_bytes(b"png")
        result = {
            "status": "passed",
            "phase": "complete",
            "slide_count": 1,
            "slides": str(output / "slides"),
        }
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(result), stderr="")

    monkeypatch.setattr(renderer, "_run_command", run)
    candidate = tmp_path / "candidate.pptx"
    reference = tmp_path / "reference.pptx"

    result = renderer.powerpoint_roundtrip(candidate, output, reference)

    assert result["status"] == "passed"
    assert result["slide_count"] == 1
    assert (output / "slides" / "slide-1.png").is_file()
    assert not (output / "final.pdf").exists()
    assert "pdf" not in result
    assert commands[0][-2:] == ["-ReferencePptx", str(reference)]


def test_windows_powerpoint_roundtrip_script_has_no_pdf_export():
    script = renderer.PROJECT_ROOT / "scripts" / "powerpoint_roundtrip.ps1"
    text = script.read_text(encoding="utf-8")

    assert "final.pdf" not in text
    assert "pdf_export" not in text
    assert ".SaveAs(" not in text
    assert 'slide_count = $slideCount' in text
    assert 'slides = $slidesPath' in text
