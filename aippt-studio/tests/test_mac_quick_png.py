from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from aippt_studio import mac_quick_png


def setup_renderer(tmp_path, monkeypatch, *, slide_count=3, fail_page=None):
    source = tmp_path / "finished.pptx"
    source.write_bytes(b"finished presentation")
    monkeypatch.setattr(mac_quick_png, "_slide_geometry", lambda path: (slide_count, 12192000, 6858000))
    monkeypatch.setattr(mac_quick_png, "officecli_executable", lambda: Path("/fake/officecli"))
    commands = []

    def render(command, **kwargs):
        commands.append(command)
        page = int(command[command.index("--page") + 1])
        image = Path(command[command.index("--out") + 1])
        assert Path(command[2]) == source
        assert source.read_bytes() == b"finished presentation"
        assert "--render" in command and command[command.index("--render") + 1] == "html"
        assert kwargs["env"]["OFFICECLI_SKIP_UPDATE"] == "1"
        if page == fail_page:
            return subprocess.CompletedProcess(command, 1, "", "renderer failed")
        image.write_bytes(f"PNG {page}".encode())
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(mac_quick_png.subprocess, "run", render)
    return source, commands


def test_export_full_deck_to_one_folder_without_pdf_or_gui(tmp_path, monkeypatch):
    source, commands = setup_renderer(tmp_path, monkeypatch)

    result = mac_quick_png.export_png(source)
    folder = tmp_path / "finished-png"

    assert result["status"] == "exported"
    assert result["output"] == str(folder)
    assert result["count"] == 3
    assert result["renderer"] == "officecli-html-approximate"
    assert sorted(path.name for path in folder.glob("*.png")) == [
        "slide-001.png", "slide-002.png", "slide-003.png",
    ]
    assert sorted(int(command[command.index("--page") + 1]) for command in commands) == [1, 2, 3]
    assert json.loads((folder / ".aippt-quick-png.json").read_text())["files"] == [
        "slide-001.png", "slide-002.png", "slide-003.png",
    ]
    assert source.read_bytes() == b"finished presentation"
    assert not list(tmp_path.glob(".aippt-quick-png-*"))
    assert not list(tmp_path.glob("*.pdf"))


def test_export_one_slide_replaces_only_its_png(tmp_path, monkeypatch):
    source, commands = setup_renderer(tmp_path, monkeypatch)
    output = tmp_path / "custom" / "page.png"
    output.parent.mkdir()
    output.write_bytes(b"old preview")

    result = mac_quick_png.export_png(source, output, slide=2)

    assert result["output"] == str(output)
    assert result["count"] == 1
    assert output.read_bytes() == b"PNG 2"
    assert len(commands) == 1
    assert source.read_bytes() == b"finished presentation"
    assert list(output.parent.iterdir()) == [output]


def test_failed_render_leaves_existing_pngs_unchanged(tmp_path, monkeypatch):
    source, _ = setup_renderer(tmp_path, monkeypatch, fail_page=2)
    folder = tmp_path / "finished-png"
    folder.mkdir()
    (folder / "slide-001.png").write_bytes(b"old one")
    (folder / ".aippt-quick-png.json").write_text(
        json.dumps({"schema": "aippt-quick-png-v1", "files": ["slide-001.png"]})
    )

    with pytest.raises(RuntimeError, match="slide 2"):
        mac_quick_png.export_png(source)

    assert (folder / "slide-001.png").read_bytes() == b"old one"
    assert not (folder / "slide-002.png").exists()
    assert not list(tmp_path.glob(".aippt-quick-png-*"))


def test_repeat_full_export_removes_only_stale_owned_pages(tmp_path, monkeypatch):
    source, _ = setup_renderer(tmp_path, monkeypatch, slide_count=3)
    mac_quick_png.export_png(source)
    folder = tmp_path / "finished-png"
    (folder / "notes.txt").write_text("keep")
    monkeypatch.setattr(mac_quick_png, "_slide_geometry", lambda path: (2, 12192000, 6858000))

    mac_quick_png.export_png(source)

    assert sorted(path.name for path in folder.glob("*.png")) == ["slide-001.png", "slide-002.png"]
    assert (folder / "notes.txt").read_text() == "keep"


def test_unowned_output_folder_is_not_overwritten(tmp_path, monkeypatch):
    source, _ = setup_renderer(tmp_path, monkeypatch)
    folder = tmp_path / "finished-png"
    folder.mkdir()
    (folder / "slide-001.png").write_bytes(b"unknown image")

    with pytest.raises(ValueError, match="unrelated files"):
        mac_quick_png.export_png(source)

    assert (folder / "slide-001.png").read_bytes() == b"unknown image"
