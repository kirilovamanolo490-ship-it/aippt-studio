"""Optional one-slide PNG preview through the Studio-managed OfficeCLI.

OfficeCLI's macOS HTML renderer is an approximation. The receipt binds the
render to the exact input bytes; it does not certify native PowerPoint layout.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree as ET

from PIL import Image

from .config import PROJECT_ROOT


class OfficeCliPreviewError(RuntimeError):
    """OfficeCLI could not safely produce the requested preview."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _slide_geometry(source: Path) -> tuple[int, int, int]:
    """Read only presentation metadata to bind page range and image aspect ratio."""
    try:
        with ZipFile(source) as package:
            entry = package.getinfo("ppt/presentation.xml")
            if entry.file_size > 2 * 1024 * 1024:
                raise OfficeCliPreviewError("PPTX presentation metadata is unexpectedly large")
            root = ET.fromstring(package.read(entry))
        ns = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
        slides = root.findall("p:sldIdLst/p:sldId", ns)
        size = root.find("p:sldSz", ns)
        if size is None:
            raise OfficeCliPreviewError("PPTX has no explicit slide size")
        slide_width, slide_height = int(size.get("cx", "0")), int(size.get("cy", "0"))
        if not slides or slide_width <= 0 or slide_height <= 0:
            raise OfficeCliPreviewError("PPTX has invalid slide count or dimensions")
        return len(slides), slide_width, slide_height
    except (BadZipFile, KeyError, ET.ParseError, OSError, ValueError) as error:
        raise OfficeCliPreviewError("PPTX presentation metadata cannot be read") from error


def render_officecli_slide_preview(
    source: Path,
    slide_number: int,
    output_png: Path,
    *,
    receipt_path: Path | None = None,
    width: int = 1280,
    height: int | None = None,
) -> dict:
    """Render exactly one slide to PNG, without creating a PDF or touching input.

    The renderer is the repository-managed ``.tools/officecli`` executable.
    The output is first written in a private temporary directory, verified,
    then installed with an exclusive hard link so a concurrent file is never
    overwritten.
    """
    source = Path(source).expanduser().resolve()
    output_png = Path(output_png).expanduser().absolute()
    destination = (Path(receipt_path).expanduser().absolute() if receipt_path
                   else output_png.with_suffix(".preview.json"))
    if not isinstance(slide_number, int) or isinstance(slide_number, bool) or slide_number < 1:
        raise OfficeCliPreviewError("slide_number must be a positive 1-based integer")
    if not isinstance(width, int) or isinstance(width, bool) or not 320 <= width <= 4096:
        raise OfficeCliPreviewError("width must be an integer from 320 through 4096")
    if not source.is_file():
        raise OfficeCliPreviewError(f"Source presentation does not exist: {source}")
    if source.suffix.lower() != ".pptx":
        raise OfficeCliPreviewError("OfficeCLI preview accepts .pptx files only")
    slide_count, slide_width, slide_height = _slide_geometry(source)
    if slide_number > slide_count:
        raise OfficeCliPreviewError(f"slide {slide_number} exceeds the {slide_count}-slide presentation")
    if height is None:
        height = round(width * slide_height / slide_width)
    if not isinstance(height, int) or isinstance(height, bool) or not 180 <= height <= 4096:
        raise OfficeCliPreviewError("height must be an integer from 180 through 4096")
    if output_png == destination:
        raise OfficeCliPreviewError("PNG and receipt must use different paths")
    if output_png.exists() or output_png.is_symlink() or destination.exists() or destination.is_symlink():
        raise OfficeCliPreviewError("Output PNG and receipt paths must not already exist")

    officecli = PROJECT_ROOT / ".tools/officecli"
    if not officecli.is_file() or not os.access(officecli, os.X_OK):
        raise OfficeCliPreviewError("Studio-managed .tools/officecli is unavailable or not executable")

    output_png.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    before_hash = _sha256(source)
    with tempfile.TemporaryDirectory(prefix="aippt-officecli-preview-", dir=output_png.parent) as tmp:
        temporary_png = Path(tmp) / "slide.png"
        command = [
            str(officecli), "view", str(source), "screenshot",
            "--page", str(slide_number), "--render", "html",
            "--screenshot-width", str(width), "--screenshot-height", str(height),
            "--out", str(temporary_png), "--json",
        ]
        try:
            result = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True,
                                    timeout=120, check=False,
                                    env={**os.environ, "OFFICECLI_SKIP_UPDATE": "1"})
        except (OSError, subprocess.TimeoutExpired) as error:
            raise OfficeCliPreviewError(f"OfficeCLI preview runner failed: {error}") from error
        try:
            response = json.loads(result.stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            stderr = result.stderr.decode("utf-8", errors="replace")[-2000:]
            raise OfficeCliPreviewError(f"OfficeCLI returned invalid JSON: {stderr}") from error
        if not isinstance(response, dict):
            raise OfficeCliPreviewError("OfficeCLI returned a non-object JSON response")
        if result.returncode != 0 or response.get("success") is not True:
            failure = response.get("error")
            detail = (failure.get("error") if isinstance(failure, dict) else str(failure or ""))
            detail = detail or result.stderr.decode("utf-8", errors="replace")[-2000:]
            raise OfficeCliPreviewError(f"OfficeCLI could not render slide {slide_number}: {detail}")
        if not temporary_png.is_file() or temporary_png.stat().st_size == 0:
            raise OfficeCliPreviewError("OfficeCLI reported success but did not create a PNG")
        try:
            with Image.open(temporary_png) as image:
                image.verify()
            with Image.open(temporary_png) as image:
                if image.format != "PNG" or image.size != (width, height):
                    raise OfficeCliPreviewError("OfficeCLI returned a PNG with unexpected dimensions")
        except (OSError, ValueError) as error:
            raise OfficeCliPreviewError(f"OfficeCLI output is not a valid PNG: {error}") from error
        after_hash = _sha256(source)
        if before_hash != after_hash:
            raise OfficeCliPreviewError("Source PPTX changed during rendering; preview discarded")
        png_hash = _sha256(temporary_png)
        try:
            os.link(temporary_png, output_png)
        except FileExistsError as error:
            raise OfficeCliPreviewError("Output PNG appeared during rendering; it was not overwritten") from error

    if _sha256(output_png) != png_hash:
        raise OfficeCliPreviewError("Output PNG changed after installation; preview receipt was not written")
    receipt = {
        "schema_version": 1,
        "status": "rendered_advisory",
        "source_sha256": before_hash,
        "source_name": source.name,
        "slide_number": slide_number,
        "slide_count": slide_count,
        "rendered_slide_count": 1,
        "renderer": "officecli-html",
        "renderer_version": None,
        "approximate_renderer": True,
        "platform": sys.platform,
        "renderer_mode": "HTML; not native PowerPoint",
        "limitations": [
            "This is an HTML-based approximation, not native PowerPoint rendering or visual acceptance.",
            "Font metrics, text wrapping, and unsupported OOXML effects may differ from PowerPoint.",
            "Use native PowerPoint round-trip and full delivery QA for final verification.",
            "No PDF was generated and exactly one slide was requested for rasterization.",
        ],
        "width": width,
        "height": height,
        "output_png": str(output_png),
        "output_sha256": png_hash,
        "output_bytes": output_png.stat().st_size,
    }
    with tempfile.TemporaryDirectory(prefix="aippt-preview-receipt-", dir=destination.parent) as tmp:
        temporary_receipt = Path(tmp) / "receipt.json"
        temporary_receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.link(temporary_receipt, destination)
        except FileExistsError as error:
            raise OfficeCliPreviewError("Preview receipt appeared during rendering; the PNG was left intact") from error
    return receipt
