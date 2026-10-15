"""Direct PDF export of an existing PowerPoint file on macOS."""
from __future__ import annotations

import shutil
import subprocess
import hashlib
from pathlib import Path

from .config import PROJECT_ROOT
from .runtime import system_osascript


def export_pdf(source: Path, output: Path) -> dict:
    """Export an existing PPTX through PowerPoint without changing the source."""
    source = Path(source).expanduser().resolve()
    requested_output = Path(output).expanduser().absolute()
    if requested_output.is_symlink():
        raise ValueError("PDF output must not be a symlink")
    output = requested_output.parent.resolve() / requested_output.name
    if source.suffix.lower() != ".pptx" or not source.is_file():
        raise ValueError(f"source must be an existing .pptx file: {source}")
    if output.suffix.lower() != ".pdf":
        raise ValueError(f"output must use the .pdf extension: {output}")
    if source == output:
        raise ValueError("PDF output must not replace the PowerPoint input")
    bridge = system_osascript()
    if bridge is None:
        raise RuntimeError("PDF export requires the verified system /usr/bin/osascript bridge")

    output.parent.mkdir(parents=True, exist_ok=True)
    script = PROJECT_ROOT / "scripts" / "powerpoint_quick_pdf.applescript"
    identity = hashlib.sha256(str(output).encode("utf-8")).hexdigest()[:16]
    token = f"aippt-quick-pdf-{identity}"
    deck = output.parent / f".{token}.pptx"
    staged_pdf = output.parent / f".{token}.pdf"
    lock = output.parent / f".{token}.lock"
    if source in {deck, staged_pdf, lock}:
        raise ValueError("PowerPoint input conflicts with the PDF export staging path")
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise RuntimeError(f"A PDF export is already active for {output}") from error
    if any(path.exists() or path.is_symlink() for path in (deck, staged_pdf)):
        lock.rmdir()
        raise RuntimeError("A previous PDF export left staging files; inspect them before retrying")
    try:
        # Keep this sibling pathname stable between runs so PowerPoint can
        # retain macOS file access for the same staged presentation location.
        shutil.copy2(source, deck)
        result = subprocess.run(
            [str(bridge), str(script), str(deck), str(staged_pdf), deck.stem],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError("PowerPoint PDF export failed" + (f": {detail}" if detail else ""))
        if not staged_pdf.is_file() or staged_pdf.stat().st_size == 0:
            raise RuntimeError("PowerPoint did not produce a non-empty PDF")
        staged_pdf.replace(output)
    finally:
        deck.unlink(missing_ok=True)
        staged_pdf.unlink(missing_ok=True)
        lock.rmdir()
    return {"status": "passed", "path": str(output)}
