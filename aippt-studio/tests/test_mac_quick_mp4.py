from pathlib import Path
import subprocess

import pytest

from aippt_studio import mac_quick_mp4


def setup_quick_export(tmp_path, monkeypatch, *, copy_fails=False):
    source = tmp_path / "finished.pptx"
    source.write_bytes(b"finished presentation")
    output = tmp_path / "deliver" / "finished.mp4"
    calls = []
    monkeypatch.setattr(mac_quick_mp4.sys, "platform", "darwin")
    monkeypatch.setattr(mac_quick_mp4, "system_osascript", lambda: Path("/usr/bin/osascript"))
    monkeypatch.setattr(mac_quick_mp4, "media_executable", lambda: Path("/fake/ffmpeg"))
    def temporary_root(output_dir, fallback_name):
        root = output_dir / fallback_name
        root.mkdir(parents=True, exist_ok=True)
        return root, "work_output_fallback"

    monkeypatch.setattr(mac_quick_mp4, "_powerpoint_temporary_root", temporary_root)
    monkeypatch.setattr(mac_quick_mp4, "PROJECT_ROOT", tmp_path)

    def run(command, **kwargs):
        calls.append(command)
        if command[0] == "/usr/bin/osascript":
            assert Path(command[2]).is_file()
            assert Path(command[2]).read_bytes() == source.read_bytes()
            Path(command[3]).write_bytes(b"PowerPoint MOV")
            return subprocess.CompletedProcess(command, 0, "", "")
        if "-c" in command and command[command.index("-c") + 1] == "copy" and copy_fails:
            return subprocess.CompletedProcess(command, 1, "", "copy unavailable")
        Path(command[-1]).write_bytes(b"MP4 output")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(mac_quick_mp4.subprocess, "run", run)
    return source, output, calls


def test_quick_mp4_prefers_stream_copy_and_preserves_source(tmp_path, monkeypatch):
    source, output, calls = setup_quick_export(tmp_path, monkeypatch)
    before = source.read_bytes()

    result = mac_quick_mp4.export_mp4(source, output)

    assert result["status"] == "exported"
    assert result["method"] == "stream_copy"
    assert output.read_bytes() == b"MP4 output"
    assert source.read_bytes() == before
    assert len(calls) == 2
    assert Path(calls[0][2]) == source.with_name(".finished.aippt-quick-mp4.pptx")
    assert not Path(calls[0][2]).exists()
    assert not source.with_name(".finished.aippt-quick-mp4.lock").exists()
    assert [item for item in source.parent.iterdir() if item.is_file()] == [source]
    assert list(output.parent.iterdir()) == [output]
    assert "-c" in calls[1] and calls[1][calls[1].index("-c") + 1] == "copy"


def test_quick_mp4_reuses_stable_candidate_path_across_exports(tmp_path, monkeypatch):
    source, output, calls = setup_quick_export(tmp_path, monkeypatch)

    mac_quick_mp4.export_mp4(source, output)
    mac_quick_mp4.export_mp4(source, output)

    movie_inputs = [Path(command[2]) for command in calls if command[0] == "/usr/bin/osascript"]
    assert len(movie_inputs) == 2
    assert movie_inputs[0] == movie_inputs[1]
    assert not movie_inputs[0].exists()


def test_quick_mp4_falls_back_to_fast_reencode(tmp_path, monkeypatch):
    source, output, calls = setup_quick_export(tmp_path, monkeypatch, copy_fails=True)

    result = mac_quick_mp4.export_mp4(source, output)

    assert result["method"] == "fast_reencode"
    assert output.read_bytes() == b"MP4 output"
    assert len(calls) == 3
    assert "ultrafast" in calls[2]


def test_quick_mp4_failure_does_not_replace_existing_output(tmp_path, monkeypatch):
    source, output, calls = setup_quick_export(tmp_path, monkeypatch)
    output.parent.mkdir(parents=True)
    output.write_bytes(b"previous delivery")

    def fail_all(command, **kwargs):
        calls.append(command)
        if command[0] == "/usr/bin/osascript":
            Path(command[3]).write_bytes(b"PowerPoint MOV")
            return subprocess.CompletedProcess(command, 0, "", "")
        return subprocess.CompletedProcess(command, 1, "", "encoding failed")

    monkeypatch.setattr(mac_quick_mp4.subprocess, "run", fail_all)
    with pytest.raises(RuntimeError, match="FFmpeg MP4 export failed"):
        mac_quick_mp4.export_mp4(source, output)

    assert output.read_bytes() == b"previous delivery"
    assert not source.with_name(".finished.aippt-quick-mp4.pptx").exists()
    assert not source.with_name(".finished.aippt-quick-mp4.lock").exists()


def test_quick_mp4_rejects_existing_export_lock(tmp_path, monkeypatch):
    source, output, calls = setup_quick_export(tmp_path, monkeypatch)
    lock = source.with_name(".finished.aippt-quick-mp4.lock")
    lock.mkdir()

    with pytest.raises(RuntimeError, match="already active"):
        mac_quick_mp4.export_mp4(source, output)

    assert calls == []
    assert lock.is_dir()


def test_quick_mp4_preserves_an_existing_staging_file(tmp_path, monkeypatch):
    source, output, calls = setup_quick_export(tmp_path, monkeypatch)
    staged = source.with_name(f".{source.stem}.aippt-quick-mp4.pptx")
    staged.write_bytes(b"unrelated existing file")

    with pytest.raises(RuntimeError, match="left a staged PPTX"):
        mac_quick_mp4.export_mp4(source, output)

    assert staged.read_bytes() == b"unrelated existing file"
    assert source.read_bytes() == b"finished presentation"
    assert calls == []


def test_quick_mp4_rejects_non_mac_and_wrong_extensions(tmp_path, monkeypatch):
    source = tmp_path / "source.pptx"
    source.write_bytes(b"pptx")
    monkeypatch.setattr(mac_quick_mp4.sys, "platform", "linux")
    with pytest.raises(ValueError, match="only on macOS"):
        mac_quick_mp4.export_mp4(source, tmp_path / "out.mp4")

    monkeypatch.setattr(mac_quick_mp4.sys, "platform", "darwin")
    with pytest.raises(ValueError, match=".mp4 extension"):
        mac_quick_mp4.export_mp4(source, tmp_path / "out.mov")
