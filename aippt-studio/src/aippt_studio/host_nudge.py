"""Optional PowerPoint-for-Mac object nudge through Apple Events.

The adapter stages a copy, binds the request to the source hash and inspected
OOXML object identity, saves through PowerPoint, then inspects the saved copy.
It never writes to the source file. Pixel-level review remains necessary.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Callable

from .pptx_edit import inspect_pptx
from .runtime import system_osascript


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _applescript() -> str:
    return r'''on run argv
    set candidatePath to item 1 of argv
    set slideIndex to (item 2 of argv) as integer
    set shapeName to item 3 of argv
    set expectedLeft to (item 4 of argv) as real
    set expectedTop to (item 5 of argv) as real
    set expectedWidth to (item 6 of argv) as real
    set expectedHeight to (item 7 of argv) as real
    set targetLeft to (item 8 of argv) as real
    set targetTop to (item 9 of argv) as real
    set targetWidth to (item 10 of argv) as real
    set targetHeight to (item 11 of argv) as real
    tell application "Microsoft PowerPoint"
        open POSIX file candidatePath
        set doc to active presentation
        if (full name of doc) is not candidatePath then error "PowerPoint opened a different presentation"
        set targetSlide to slide slideIndex of doc
        set foundCount to 0
        set foundIndex to 0
        repeat with shapeIndex from 1 to (count of shapes of targetSlide)
            if (name of shape shapeIndex of targetSlide) is shapeName then
                set foundCount to foundCount + 1
                set foundIndex to shapeIndex
            end if
        end repeat
        if foundCount is not 1 then error "shape name is missing or ambiguous"
        set targetShape to shape foundIndex of targetSlide
        if (left position of targetShape) is not expectedLeft or (top of targetShape) is not expectedTop or (width of targetShape) is not expectedWidth or (height of targetShape) is not expectedHeight then error "PowerPoint shape geometry differs from inspected source"
        set left position of targetShape to targetLeft
        set top of targetShape to targetTop
        set width of targetShape to targetWidth
        set height of targetShape to targetHeight
        save doc
        close doc saving yes
        return "saved"
    end tell
end run'''


def nudge_with_powerpoint(
    source: Path,
    *,
    source_sha256: str,
    slide: int,
    shape_id: str,
    output: Path,
    dx_pt: float = 0,
    dy_pt: float = 0,
    dwidth_pt: float = 0,
    dheight_pt: float = 0,
    executor: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
    osascript: Path | None = None,
    host_platform: str | None = None,
) -> dict:
    """Apply a point-based nudge to one exact inspected shape on a staged copy."""
    system = host_platform or platform.system()
    if system != "Darwin":
        raise ValueError("host-nudge is an optional macOS PowerPoint operation")
    source = source.resolve()
    output = output.resolve()
    active_work = os.environ.get("AIPPT_WORK_DIR")
    if active_work:
        workspace = Path(active_work).expanduser().resolve(strict=True) / "workspace"
        if not workspace.is_dir() or not output.is_relative_to(workspace.resolve(strict=True)):
            raise ValueError("host-nudge output must be a new directory inside the active Work workspace")
    if not source.is_file():
        raise ValueError("source PPTX is missing")
    if output.exists():
        raise ValueError("output directory already exists; choose a new candidate directory")
    if _sha(source) != source_sha256.lower():
        raise ValueError("source changed; inspect the current original first")
    if type(slide) is not int or slide < 1:
        raise ValueError("slide must be a positive 1-based slide number")
    if not isinstance(shape_id, str) or not shape_id.isascii() or not shape_id.isdecimal():
        raise ValueError("shape_id must be an exact object ID from pptx-inspect")
    shifts = (dx_pt, dy_pt, dwidth_pt, dheight_pt)
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in shifts):
        raise ValueError("point deltas must be finite numbers")
    if any(value != value or abs(value) == float("inf") for value in shifts):
        raise ValueError("point deltas must be finite numbers")
    if not any(shifts):
        raise ValueError("nudge must change at least one point value")

    report = inspect_pptx(source, slides=[slide])
    if report["source_sha256"] != source_sha256.lower():
        raise ValueError("source hash differs from inspection")
    found = [obj for obj in report["slides"][0]["objects"] if obj["shape_id"] == shape_id]
    if len(found) != 1:
        raise ValueError("shape_id is missing or ambiguous on selected slide")
    obj = found[0]
    if not obj["editable"] or obj["geometry"] is None:
        raise ValueError("selected shape has no supported editable geometry")
    if not obj["name"]:
        raise ValueError("PowerPoint object has no stable name for host selection")
    before_emu = obj["geometry"]["emu"]
    before_pt = {key: before_emu[key] / 12700 for key in ("x", "y", "width", "height")}
    after_pt = {
        "x": before_pt["x"] + float(dx_pt),
        "y": before_pt["y"] + float(dy_pt),
        "width": before_pt["width"] + float(dwidth_pt),
        "height": before_pt["height"] + float(dheight_pt),
    }
    if after_pt["width"] <= 0 or after_pt["height"] <= 0:
        raise ValueError("nudge would make object dimensions non-positive")

    bridge = osascript or system_osascript()
    if executor is None and bridge is None:
        raise ValueError("verified system /usr/bin/osascript bridge is unavailable")
    output.mkdir(parents=True)
    # PowerPoint for Mac can refuse or stall opening two presentations with the
    # same basename even when they live in separate candidate directories.
    # Give each immutable candidate a name derived from its unique output path.
    identity = hashlib.sha256(str(output).encode()).hexdigest()[:12]
    attempt = output / (".host-attempt-" + identity + ".pptx")
    candidate = output / ("host-candidate-" + identity + ".pptx")
    attempt.write_bytes(source.read_bytes())
    command = [str(bridge) if bridge else "/usr/bin/osascript", "-e", _applescript(),
               str(attempt), str(slide), obj["name"],
               *(format(before_pt[key], ".12g") for key in ("x", "y", "width", "height")),
               *(format(after_pt[key], ".12g") for key in ("x", "y", "width", "height"))]

    def fail(reason: str) -> None:
        failure = {"schema_version": 1, "status": "host_failed", "source_sha256": source_sha256.lower(),
                   "slide": slide, "shape_id": shape_id, "attempt": str(attempt),
                   "attempt_sha256": _sha(attempt) if attempt.is_file() else None,
                   "reason": reason[:500], "candidate_accepted": False,
                   "visual_review": "not_started"}
        (output / "host-nudge-failure.json").write_text(
            json.dumps(failure, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise RuntimeError(f"PowerPoint host edit failed ({reason[:160]}); see host-nudge-failure.json")

    try:
        if executor is None:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                       timeout=90, check=False)
        else:
            completed = executor(command)
    except subprocess.TimeoutExpired:
        fail("timed out; inspect PowerPoint's file-access dialog for the staged attempt")
    except OSError as error:
        fail(f"bridge unavailable: {type(error).__name__}")
    if completed.returncode != 0:
        fail((completed.stderr or completed.stdout or "PowerPoint returned a failure").strip())

    try:
        after = inspect_pptx(attempt, slides=[slide])
    except (OSError, ValueError, KeyError) as error:
        fail(f"saved file could not be inspected: {type(error).__name__}")
    reread = [item for item in after["slides"][0]["objects"] if item["shape_id"] == shape_id]
    if len(reread) != 1 or reread[0]["name"] != obj["name"]:
        fail("saved file no longer contains the exact target object")
    actual_emu = reread[0]["geometry"]["emu"]
    intended_emu = {key: round(value * 12700) for key, value in after_pt.items()}
    if actual_emu != intended_emu:
        fail("saved geometry differs from the requested target")
    attempt.replace(candidate)
    receipt = {
        "schema_version": 1, "status": "candidate", "backend": "mac-powerpoint-applescript",
        "source": str(source), "source_sha256": source_sha256.lower(),
        "candidate": str(candidate), "candidate_sha256": _sha(candidate),
        "slide": slide, "shape_id": shape_id, "shape_name": obj["name"],
        "before_emu": before_emu, "after_emu": actual_emu,
        "host_stdout": completed.stdout.strip(), "native_save_readback": "passed",
        "visual_review": "required", "native_visual_verified": False,
    }
    (output / "host-nudge-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return receipt
