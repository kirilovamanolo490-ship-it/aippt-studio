"""Fast, standalone export of an existing PPTX to an MP4 on macOS."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import PROJECT_ROOT
from .mac_video import media_executable
from .mac_powerpoint import _powerpoint_temporary_root
from .runtime import system_osascript


def export_mp4(source: Path, output: Path) -> dict:
    """Export a copy of ``source`` through PowerPoint and publish an MP4.

    This is deliberately a conversion-only path: it does not inspect or modify
    slide contents and does not run media-track, visual, or presentation QA.
    """
    source = Path(source).expanduser().resolve()
    requested_output = Path(output).expanduser().absolute()
    if requested_output.is_symlink():
        raise ValueError("MP4 output must not be a symlink")
    output = requested_output.parent.resolve() / requested_output.name
    if sys.platform != "darwin":
        raise ValueError("Quick PPTX-to-MP4 export is available only on macOS")
    if source.suffix.lower() != ".pptx" or not source.is_file():
        raise ValueError(f"source must be an existing .pptx file: {source}")
    if output.suffix.lower() != ".mp4":
        raise ValueError(f"output must use the .mp4 extension: {output}")

    bridge = system_osascript()
    if bridge is None:
        raise ValueError("Mac video export requires /usr/bin/osascript")
    ffmpeg = media_executable()
    if ffmpeg is None:
        raise ValueError("Mac video export requires the Studio FFmpeg runtime or AIPPT_FFMPEG")

    output.parent.mkdir(parents=True, exist_ok=True)
    # Keep the PowerPoint input at one stable sibling path between runs. A
    # per-source lock prevents concurrent exports from sharing that path.
    staged_pptx = source.with_name(f".{source.stem}.aippt-quick-mp4.pptx")
    lock = source.with_name(f".{source.stem}.aippt-quick-mp4.lock")
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise RuntimeError(f"A quick MP4 export is already active for this PPTX: {lock}") from error
    if staged_pptx.exists() or staged_pptx.is_symlink():
        lock.rmdir()
        raise RuntimeError("A previous MP4 export left a staged PPTX; inspect it before retrying")

    office_root: Path | None = None
    cache_mode = ""
    try:
        shutil.copy2(source, staged_pptx)
        office_root, cache_mode = _powerpoint_temporary_root(
            output.parent, fallback_name=".quick-mp4-cache"
        )
        with tempfile.TemporaryDirectory(prefix="aippt-quick-mp4-", dir=office_root) as office_tmp, \
                tempfile.TemporaryDirectory(prefix=".aippt-mp4-", dir=output.parent) as output_tmp:
            office_tmp = Path(office_tmp)
            output_tmp = Path(output_tmp)
            native_mov = office_tmp / "native.mov"
            staged_mp4 = output_tmp / "result.mp4"

            movie_command = [
                str(bridge),
                str(PROJECT_ROOT / "scripts/powerpoint_movie.applescript"),
                str(staged_pptx),
                str(native_mov),
                staged_pptx.stem,
            ]
            movie = subprocess.run(
                movie_command, capture_output=True, text=True, timeout=1260, check=False
            )
            if movie.returncode != 0 or not native_mov.is_file() or native_mov.stat().st_size == 0:
                detail = (movie.stderr or movie.stdout or "PowerPoint did not create a movie").strip()
                raise RuntimeError(f"PowerPoint movie export failed: {detail}")

            copy_command = [
                str(ffmpeg), "-nostdin", "-y", "-i", str(native_mov),
                "-c", "copy", "-movflags", "+faststart", str(staged_mp4),
            ]
            copy_result = subprocess.run(
                copy_command, capture_output=True, text=True, timeout=1260, check=False
            )
            method = "stream_copy"
            if copy_result.returncode != 0 or not staged_mp4.is_file() or staged_mp4.stat().st_size == 0:
                staged_mp4.unlink(missing_ok=True)
                encode_command = [
                    str(ffmpeg), "-nostdin", "-y", "-i", str(native_mov),
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                    "-c:a", "aac", "-movflags", "+faststart", str(staged_mp4),
                ]
                encoded = subprocess.run(
                    encode_command, capture_output=True, text=True, timeout=1260, check=False
                )
                if encoded.returncode != 0 or not staged_mp4.is_file() or staged_mp4.stat().st_size == 0:
                    detail = (encoded.stderr or encoded.stdout or "FFmpeg did not create an MP4").strip()
                    raise RuntimeError(f"FFmpeg MP4 export failed: {detail}")
                method = "fast_reencode"

            staged_mp4.replace(output)
    finally:
        staged_pptx.unlink(missing_ok=True)
        lock.rmdir()
        if cache_mode == "work_output_fallback" and office_root is not None:
            try:
                office_root.rmdir()
            except OSError:
                pass

    return {
        "status": "exported",
        "source": str(source),
        "output": str(output),
        "bytes": output.stat().st_size,
        "method": method,
    }
