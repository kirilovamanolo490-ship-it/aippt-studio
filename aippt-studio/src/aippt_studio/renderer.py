from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw

from .config import PROJECT_ROOT
from .mastering import audit_master_layout_ids
from .runtime import officecli_executable, chromium_executable
from .platform_runtime import probe_node_runtime
import sys


class RenderError(RuntimeError):
    pass


class PowerPointRoundtripError(RenderError):
    def __init__(self, report: dict[str, object]):
        self.report = report
        phase = report.get("phase", "unknown")
        classification = report.get("classification", "unknown")
        hresult = report.get("hresult") or "no HRESULT"
        message = report.get("message") or "PowerPoint validation failed"
        super().__init__(f"PowerPoint {classification} failure at {phase} ({hresult}): {message}")


def _run_command(command: list[str], *, timeout: int, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except OSError as error:
        raise RenderError(f"cannot start {Path(command[0]).name}: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise RenderError(f"command timed out after {timeout}s: {Path(command[0]).name}") from error


def render_html(html_path: Path, pptx_path: Path, preview_dir: Path) -> dict[str, object]:
    preview_dir.mkdir(parents=True, exist_ok=True)
    pptx_path.parent.mkdir(parents=True, exist_ok=True)
    if os.environ.get("AIPPT_FAKE_RENDER") == "1":
        for preview in preview_dir.glob("*.png"):
            preview.unlink()
        pptx_path.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        html_text = html_path.read_text(encoding="utf-8")
        slide_ids = re.findall(r'data-slide-id="([A-Za-z0-9_-]+)"', html_text) or ["slide-01"]
        for slide_id in slide_ids:
            image = Image.new("RGB", (640, 360), "#0b2639")
            ImageDraw.Draw(image).text((30, 30), slide_id, fill="white")
            image.save(preview_dir / f"{slide_id}.png")
        return {"status": "fake", "slides": len(slide_ids), "pptx": str(pptx_path)}

    node_modules = PROJECT_ROOT / "node_modules"
    if not node_modules.is_dir():
        raise RenderError("Node dependencies are missing; run npm install in aippt-studio")
    node_runtime = probe_node_runtime()
    if node_runtime.get("status") != "passed":
        reason = str(node_runtime.get("reason") or node_runtime.get("probe") or "no compatible executable was found")
        raise RenderError("A compatible host-native Node.js 22.12+ runtime is unavailable for HTML rendering: " + reason)
    command = [str(node_runtime["path"]), str(PROJECT_ROOT / "node" / "render.mjs"), str(html_path), str(pptx_path), str(preview_dir)]
    completed = _run_command(command, cwd=PROJECT_ROOT, timeout=300)
    if completed.returncode != 0:
        raise RenderError((completed.stderr or completed.stdout or "HTML renderer failed").strip())
    normalize_ooxml(pptx_path)
    try:
        return json.loads(completed.stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as error:
        raise RenderError(f"Renderer returned invalid JSON: {completed.stdout}") from error


def normalize_ooxml(pptx_path: Path) -> None:
    """Remove empty generated notes parts before Office validation.

    dom-to-pptx emits an empty notes tree with an invalid presentation child
    order. Speaker notes are delivered separately, so retaining that tree adds
    no user value and prevents clean schema validation. PowerPoint-specific
    master/layout compatibility is audited separately during mastering.
    """

    with zipfile.ZipFile(pptx_path, "r") as source:
        entries = [(info, source.read(info.filename)) for info in source.infolist()]
    pml = "http://schemas.openxmlformats.org/presentationml/2006/main"
    package_relationships = "http://schemas.openxmlformats.org/package/2006/relationships"
    content_types = "http://schemas.openxmlformats.org/package/2006/content-types"
    removed_prefixes = ("ppt/notesMasters/", "ppt/notesSlides/")
    changed = False
    normalized: list[tuple[zipfile.ZipInfo, bytes]] = []
    for info, data in entries:
        if info.filename.startswith(removed_prefixes):
            changed = True
            continue
        if info.filename == "ppt/presentation.xml":
            root = ElementTree.fromstring(data)
            notes = root.find(f"{{{pml}}}notesMasterIdLst")
            if notes is not None:
                root.remove(notes)
                ElementTree.register_namespace("p", pml)
                ElementTree.register_namespace("a", "http://schemas.openxmlformats.org/drawingml/2006/main")
                ElementTree.register_namespace("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships")
                data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
                changed = True
        elif info.filename == "ppt/_rels/presentation.xml.rels" or re.fullmatch(
            r"ppt/slides/_rels/slide\d+\.xml\.rels", info.filename
        ):
            root = ElementTree.fromstring(data)
            notes_relationships = [
                item for item in root if item.get("Type", "").endswith(("/notesMaster", "/notesSlide"))
            ]
            if notes_relationships:
                for item in notes_relationships:
                    root.remove(item)
                ElementTree.register_namespace("", package_relationships)
                data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
                changed = True
        elif info.filename == "[Content_Types].xml":
            root = ElementTree.fromstring(data)
            note_overrides = [
                item
                for item in root
                if item.get("PartName", "").startswith(("/ppt/notesMasters/", "/ppt/notesSlides/"))
            ]
            if note_overrides:
                for item in note_overrides:
                    root.remove(item)
                ElementTree.register_namespace("", content_types)
                data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
                changed = True
        normalized.append((info, data))
    if not changed:
        return
    temporary = pptx_path.with_suffix(".normalized.pptx")
    with zipfile.ZipFile(temporary, "w") as target:
        for info, data in normalized:
            target.writestr(info, data)
    temporary.replace(pptx_path)


def create_montage(images: list[Path], output: Path, columns: int = 3) -> None:
    if not images:
        raise RenderError("No slide previews available for montage")
    opened = [Image.open(path).convert("RGB") for path in images]
    try:
        width = 640
        height = 360
        gap = 18
        rows = (len(opened) + columns - 1) // columns
        canvas = Image.new("RGB", (columns * width + (columns + 1) * gap, rows * height + (rows + 1) * gap), "#07121f")
        for index, image in enumerate(opened):
            thumb = image.copy()
            thumb.thumbnail((width, height))
            x = gap + (index % columns) * (width + gap)
            y = gap + (index // columns) * (height + gap)
            canvas.paste(thumb, (x, y))
        output.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(output)
    finally:
        for image in opened:
            image.close()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def natural_sort_paths(paths: list[Path]) -> list[Path]:
    def key(path: Path) -> list[int | str]:
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]

    return sorted(paths, key=key)


def _is_macos() -> bool:
    return sys.platform == "darwin"


def powerpoint_roundtrip(
    pptx: Path,
    output_dir: Path,
    reference_pptx: Path | None = None,
) -> dict[str, object]:
    if _is_macos():
        from .mac_powerpoint import mac_powerpoint_roundtrip
        return mac_powerpoint_roundtrip(pptx, output_dir, reference_pptx)
    script = PROJECT_ROOT / "scripts" / "powerpoint_roundtrip.ps1"
    command = [os.environ.get("AIPPT_POWERSHELL") or shutil.which("powershell") or shutil.which("pwsh") or "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Pptx", str(pptx), "-OutputDir", str(output_dir)]
    if reference_pptx is not None:
        command.extend(["-ReferencePptx", str(reference_pptx)])
    completed = _run_command(command, timeout=240)
    try:
        result = json.loads(completed.stdout.splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as error:
        if completed.returncode != 0:
            raise RenderError((completed.stderr or completed.stdout or "PowerPoint validation failed").strip()) from error
        raise RenderError(f"PowerPoint validator returned invalid JSON: {completed.stdout}") from error
    if completed.returncode != 0 or result.get("status") != "passed":
        raise PowerPointRoundtripError(result)
    slide_count = result.get("slide_count")
    slides = natural_sort_paths(list((output_dir / "slides").glob("*.png")))
    if not isinstance(slide_count, int) or len(slides) != slide_count:
        raise RenderError(f"PowerPoint exported {len(slides)} PNG files for {slide_count!r} slides")
    return result


def officecli_validate(pptx: Path) -> dict[str, object]:
    if os.environ.get("AIPPT_FAKE_RENDER") == "1":
        return {"status": "skipped-test-render"}
    try:
        audit_master_layout_ids(pptx)
    except (KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise RenderError(f"PowerPoint master/layout audit failed: {error}") from error
    resolved = officecli_executable()
    if resolved is None:
        raise RenderError("A compatible OfficeCLI is unavailable; set AIPPT_OFFICECLI or install the host binary in .tools")
    executable = str(resolved)
    validation: subprocess.CompletedProcess[str] | None = None
    validation_error: RenderError | None = None
    try:
        validation = _run_command([executable, "validate", str(pptx)], timeout=120)
    except RenderError as error:
        validation_error = error
    close = _run_command([executable, "close", str(pptx)], timeout=30)
    if close.returncode != 0:
        detail = (close.stderr or close.stdout or "OfficeCLI failed to release the document").strip()
        raise RenderError(detail)
    if validation_error:
        raise validation_error
    assert validation is not None
    if validation.returncode != 0:
        detail = (validation.stderr or validation.stdout or "OfficeCLI validation failed").strip()
        raise RenderError(detail)
    return {"status": "passed", "version": _run_command([executable, "--version"], timeout=15).stdout.strip()}


def copy_delivery(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
