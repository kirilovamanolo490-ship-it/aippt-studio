"""Optional, local single-slide preview using the managed Office Kit renderer.

This is an advisory approximation for quick iteration. It is not a substitute
for PowerPoint/LibreOffice round-trip or final delivery QA.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image

from .config import PROJECT_ROOT
from .platform_runtime import probe_node_runtime


class LocalPreviewError(RuntimeError):
    """A local preview could not be produced safely."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def render_slide_preview(
    source: Path,
    slide_number: int,
    output_png: Path,
    *,
    receipt_path: Path | None = None,
    width: int = 1280,
    font_files: tuple[Path, ...] | list[Path] = (),
    load_system_fonts: bool = False,
) -> dict:
    """Render exactly one PPTX slide to PNG and write a source-bound receipt.

    Resolves only the verified host-native Node binary and Studio's own locked
    ``node_modules``. It intentionally never discovers Work-local caches.
    """
    source = Path(source).expanduser().resolve()
    output_png = Path(output_png).expanduser().absolute()
    if not isinstance(slide_number, int) or isinstance(slide_number, bool) or slide_number < 1:
        raise LocalPreviewError("slide_number must be a positive 1-based integer")
    if not isinstance(width, int) or width < 320 or width > 4096:
        raise LocalPreviewError("width must be an integer from 320 through 4096")
    if not source.is_file():
        raise LocalPreviewError(f"Source presentation does not exist: {source}")
    if source.suffix.lower() != ".pptx":
        raise LocalPreviewError("Local preview accepts .pptx files only")
    destination = Path(receipt_path).expanduser().absolute() if receipt_path else output_png.with_suffix(".preview.json")
    if output_png == destination:
        raise LocalPreviewError("PNG and receipt must use different paths")
    if output_png.exists() or output_png.is_symlink() or destination.exists() or destination.is_symlink():
        raise LocalPreviewError("Output PNG and receipt paths must not already exist")
    preview_package = PROJECT_ROOT / "node_modules/@office-kit/pptx-preview/package.json"
    pptx_package = PROJECT_ROOT / "node_modules/@office-kit/pptx/package.json"
    script = PROJECT_ROOT / "node/preview-slide.mjs"
    for required in (preview_package, pptx_package, script):
        if not required.is_file():
            raise LocalPreviewError(
                "Optional Office Kit preview is not installed in the Studio-managed node_modules; "
                "run npm ci from the aippt-studio directory."
            )
    node = probe_node_runtime()
    if node.get("status") != "passed":
        raise LocalPreviewError(f"A verified host-native Node runtime is unavailable: {node.get('reason', node)}")

    before_hash = _sha256(source)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    checked_fonts = []
    for font in font_files:
        font = Path(font).expanduser().resolve()
        if not font.is_file():
            raise LocalPreviewError(f"Requested font file does not exist: {font}")
        checked_fonts.append(font)
    env = dict(os.environ)
    env.pop("NODE_OPTIONS", None)
    env.pop("NODE_PATH", None)
    with tempfile.TemporaryDirectory(prefix="aippt-officekit-preview-", dir=output_png.parent) as tmp:
        temporary_png = Path(tmp) / "slide.png"
        command = [
            node["path"], str(script), "--source", str(source), "--slide", str(slide_number),
            "--output", str(temporary_png), "--width", str(width), "--system-fonts",
            "true" if load_system_fonts else "false",
        ]
        for font in checked_fonts:
            command.extend(("--font-file", str(font)))
        try:
            result = subprocess.run(command, cwd=PROJECT_ROOT, env=env, capture_output=True,
                                    timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise LocalPreviewError(f"Local preview runner failed: {error}") from error
        if result.returncode:
            stderr = result.stderr.decode("utf-8", errors="replace")[-4000:]
            raise LocalPreviewError(f"Office Kit could not render this deck: {stderr.strip()}")
        try:
            rendered = json.loads(result.stdout.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise LocalPreviewError("Local renderer returned an invalid receipt") from error
        if (rendered.get("status") != "rendered" or rendered.get("source_sha256") != before_hash
                or rendered.get("slide_number") != slide_number or rendered.get("rendered_slide_count") != 1
                or rendered.get("approximate_renderer") is not True):
            raise LocalPreviewError("Local renderer returned inconsistent source/slide metadata")
        if _sha256(source) != before_hash:
            raise LocalPreviewError("Source PPTX changed during rendering; preview discarded")
        if not temporary_png.is_file() or temporary_png.stat().st_size == 0:
            raise LocalPreviewError("Renderer reported success but did not produce a PNG")
        try:
            with Image.open(temporary_png) as image:
                image.verify()
            with Image.open(temporary_png) as image:
                if image.format != "PNG" or image.width != width or image.height < 1:
                    raise LocalPreviewError("Rendered image has unexpected PNG dimensions")
        except (OSError, ValueError) as error:
            raise LocalPreviewError(f"Renderer output is not a valid PNG: {error}") from error
        png_hash = _sha256(temporary_png)
        if rendered.get("output_sha256") != png_hash:
            raise LocalPreviewError("Renderer PNG hash does not match its own receipt")
        try:
            os.link(temporary_png, output_png)
        except FileExistsError as error:
            raise LocalPreviewError("Output PNG appeared during rendering; it was not overwritten") from error
    if _sha256(output_png) != png_hash:
        raise LocalPreviewError("Output PNG changed after installation; preview receipt was not written")

    receipt = {
        "schema_version": 1,
        "status": "rendered_advisory",
        "source_sha256": before_hash,
        "source_name": source.name,
        "slide_number": slide_number,
        "slide_count": rendered["slide_count"],
        "rendered_slide_count": 1,
        "renderer": rendered["renderer"],
        "renderer_version": rendered["renderer_version"],
        "approximate_renderer": True,
        "limitations": [
            "This is an experimental approximation; it is not Office-native rendering or visual acceptance.",
            "CJK and custom-font glyph metrics/wrapping may differ. Registering a font for rasterization does not guarantee matching layout metrics.",
            "Complex OOXML features may be missing or rendered differently; use native Office round-trip for final verification.",
            "No PDF was generated and no other slide was rasterized.",
        ],
        "width": width,
        "font_files": [str(Path(font).expanduser().resolve()) for font in font_files],
        "system_font_fallback": load_system_fonts,
        "output_png": str(output_png),
        "output_sha256": png_hash,
        "output_bytes": output_png.stat().st_size,
        "node": {key: node.get(key) for key in ("path", "version", "platform", "architecture")},
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="aippt-preview-receipt-", dir=destination.parent) as tmp:
        temporary_receipt = Path(tmp) / "receipt.json"
        temporary_receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.link(temporary_receipt, destination)
        except FileExistsError as error:
            raise LocalPreviewError("Preview receipt appeared during rendering; the PNG was left intact") from error
    return receipt
