from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from aippt_studio import mac_quick_pdf


def test_export_pdf_runs_only_powerpoint_export_on_a_temporary_copy(tmp_path, monkeypatch):
    source = tmp_path / "finished.pptx"
    source.write_bytes(b"original presentation")
    output = tmp_path / "deliverables" / "finished.pdf"
    bridge = Path("/usr/bin/osascript")
    monkeypatch.setattr(mac_quick_pdf, "system_osascript", lambda: bridge)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        deck = Path(command[2])
        pdf = Path(command[3])
        assert deck.read_bytes() == b"original presentation"
        assert deck != source
        assert deck.parent == output.parent
        assert command[4] in deck.stem
        pdf.write_bytes(b"pdf bytes")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(mac_quick_pdf.subprocess, "run", fake_run)
    result = mac_quick_pdf.export_pdf(source, output)
    first_deck = Path(calls[0][0][2])
    assert not first_deck.exists()
    assert mac_quick_pdf.export_pdf(source, output)["status"] == "passed"

    command, kwargs = calls[0]
    assert command[0] == str(bridge)
    assert command[1].endswith("scripts/powerpoint_quick_pdf.applescript")
    assert kwargs["timeout"] == 300 and kwargs["capture_output"] is True
    assert len(command) == 5
    assert Path(calls[1][0][2]) == first_deck
    assert output.read_bytes() == b"pdf bytes"
    assert source.read_bytes() == b"original presentation"
    assert result == {"status": "passed", "path": str(output.resolve())}
    assert not first_deck.exists()
    assert list(output.parent.iterdir()) == [output]
    assert not Path(command[3]).exists()
    assert not Path(command[2]).parent.joinpath(f".{Path(command[2]).stem}.lock").exists()


def test_export_pdf_failure_preserves_existing_output(tmp_path, monkeypatch):
    source = tmp_path / "finished.pptx"
    source.write_bytes(b"presentation")
    output = tmp_path / "finished.pdf"
    output.write_bytes(b"previous pdf")
    monkeypatch.setattr(mac_quick_pdf, "system_osascript", lambda: Path("/usr/bin/osascript"))
    calls = []

    def failed_run(command, **kwargs):
        calls.append(command)
        Path(command[3]).write_bytes(b"partial")
        return subprocess.CompletedProcess(command, 1, "", "PowerPoint refused export")

    monkeypatch.setattr(mac_quick_pdf.subprocess, "run", failed_run)
    with pytest.raises(RuntimeError, match="PowerPoint refused export"):
        mac_quick_pdf.export_pdf(source, output)

    assert output.read_bytes() == b"previous pdf"
    assert source.read_bytes() == b"presentation"
    assert not Path(calls[0][2]).exists()
    assert not Path(calls[0][3]).exists()
    assert not list(tmp_path.glob(".aippt-quick-pdf-*.lock"))


def test_export_pdf_preserves_an_existing_staging_file(tmp_path, monkeypatch):
    source = tmp_path / "finished.pptx"
    source.write_bytes(b"presentation")
    output = tmp_path / "finished.pdf"
    identity = hashlib.sha256(str(output.resolve()).encode("utf-8")).hexdigest()[:16]
    staged = tmp_path / f".aippt-quick-pdf-{identity}.pptx"
    staged.write_bytes(b"unrelated existing file")
    monkeypatch.setattr(mac_quick_pdf, "system_osascript", lambda: Path("/usr/bin/osascript"))

    with pytest.raises(RuntimeError, match="left staging files"):
        mac_quick_pdf.export_pdf(source, output)

    assert staged.read_bytes() == b"unrelated existing file"
    assert source.read_bytes() == b"presentation"
    assert not (tmp_path / f".aippt-quick-pdf-{identity}.lock").exists()
