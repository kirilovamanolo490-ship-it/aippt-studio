"""Fast, local PNG previews of a PPTX without Office GUI automation."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import PROJECT_ROOT
from .local_preview_officecli import _slide_geometry
from .runtime import officecli_executable


_MARKER = ".aippt-quick-png.json"
_PAGE_NAME = re.compile(r"slide-\d+\.png\Z")


def export_png(source: Path, output: Path | None = None, slide: int | None = None) -> dict:
    """Render the requested page(s) directly to PNG and return their location.

    This renders pages without editing slides or running presentation QA.
    OfficeCLI's HTML renderer is an approximation of PowerPoint, so this
    result is a preview rather than a native visual-acceptance record.
    """
    source = Path(source).expanduser().resolve()
    if source.suffix.lower() != ".pptx" or not source.is_file():
        raise ValueError(f"source must be an existing .pptx file: {source}")
    if slide is not None and (isinstance(slide, bool) or not isinstance(slide, int) or slide < 1):
        raise ValueError("slide must be a positive 1-based integer")
    slide_count, slide_width, slide_height = _slide_geometry(source)
    if slide is not None and slide > slide_count:
        raise ValueError(f"slide {slide} exceeds this {slide_count}-slide presentation")

    if output is None:
        output = (source.with_name(f"{source.stem}-slide-{slide:03d}.png") if slide is not None
                  else source.with_name(f"{source.stem}-png"))
    requested_output = Path(output).expanduser().absolute()
    if requested_output.is_symlink():
        raise ValueError("PNG output must not be a symlink")
    output = requested_output.parent.resolve() / requested_output.name
    if slide is not None:
        if output.suffix.lower() != ".png":
            raise ValueError("single-slide output must use the .png extension")
        if output.is_dir():
            raise ValueError("single-slide output must be a PNG file, not a directory")
    else:
        if output.suffix.lower() == ".png":
            raise ValueError("full-deck output must be a directory, not a .png file")
        if output.exists() and not output.is_dir():
            raise ValueError("full-deck output must be a directory")

    officecli = officecli_executable()
    if officecli is None:
        raise RuntimeError("A host-native Studio OfficeCLI executable is unavailable for PNG previews")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.parent / f".{output.name}.aippt-quick-png.lock"
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise RuntimeError(f"A PNG export is already active for {output}") from error

    pages = [slide] if slide is not None else list(range(1, slide_count + 1))
    digits = max(3, len(str(slide_count)))
    width = min(1600, max(320, round(4096 * slide_width / slide_height)))
    height = round(width * slide_height / slide_width)
    if not 180 <= height <= 4096:
        lock.rmdir()
        raise ValueError("presentation aspect ratio cannot be rendered at a practical PNG size")

    try:
        with tempfile.TemporaryDirectory(prefix=".aippt-quick-png-", dir=output.parent) as temporary:
            scratch = Path(temporary)

            def render_page(number: int) -> Path:
                image = scratch / f"slide-{number:0{digits}d}.png"
                command = [
                    str(officecli), "view", str(source), "screenshot",
                    "--page", str(number), "--render", "html",
                    "--screenshot-width", str(width), "--screenshot-height", str(height),
                    "--out", str(image), "--json",
                ]
                try:
                    result = subprocess.run(
                        command, cwd=PROJECT_ROOT, env={**os.environ, "OFFICECLI_SKIP_UPDATE": "1"},
                        capture_output=True, text=True, timeout=120, check=False,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    raise RuntimeError(f"PNG rendering failed for slide {number}: {error}") from error
                if result.returncode != 0 or not image.is_file() or image.stat().st_size == 0:
                    detail = (result.stderr or result.stdout or "no PNG was written").strip()[-1200:]
                    raise RuntimeError(f"PNG rendering failed for slide {number}: {detail}")
                return image

            # Each process renders one page. A small worker pool avoids opening
            # the deck in the GUI and keeps a full-deck request reasonably fast.
            with ThreadPoolExecutor(max_workers=min(3, len(pages))) as pool:
                images = list(pool.map(render_page, pages))

            if slide is not None:
                images[0].replace(output)
            else:
                if output.is_symlink():
                    raise ValueError("PNG output directory must not be a symlink")
                output.mkdir(exist_ok=True)
                marker = output / _MARKER
                if marker.is_symlink():
                    raise ValueError("PNG output marker must not be a symlink")
                if not marker.is_file() and any(output.iterdir()):
                    raise ValueError("PNG output directory already contains unrelated files; choose another --output")
                previous: list[str] = []
                if marker.is_file():
                    try:
                        record = json.loads(marker.read_text(encoding="utf-8"))
                        if record.get("schema") == "aippt-quick-png-v1":
                            previous = [name for name in record.get("files", [])
                                        if isinstance(name, str) and _PAGE_NAME.fullmatch(name)]
                    except (OSError, ValueError, AttributeError, TypeError):
                        pass
                names = [image.name for image in images]
                for name in names:
                    if (output / name).is_symlink():
                        raise ValueError(f"PNG output must not be a symlink: {output / name}")
                for image in images:
                    target = output / image.name
                    image.replace(target)
                for name in set(previous) - set(names):
                    (output / name).unlink(missing_ok=True)
                temporary_marker = scratch / "manifest.json"
                temporary_marker.write_text(
                    json.dumps({"schema": "aippt-quick-png-v1", "files": names}, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                temporary_marker.replace(marker)
    finally:
        lock.rmdir()

    return {
        "status": "exported",
        "source": str(source),
        "output": str(output),
        "count": len(pages),
        "renderer": "officecli-html-approximate",
    }
