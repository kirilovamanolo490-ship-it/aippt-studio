"""A small, source-bound geometry edit for an already editable PPTX."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from .pptx_edit import inspect_pptx, patch_pptx


def _delta_emu(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a finite number of millimetres")
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{name} must be a finite number of millimetres") from error
    if not number.is_finite():
        raise ValueError(f"{name} must be a finite number of millimetres")
    return int((number * 36000).to_integral_value(rounding=ROUND_HALF_UP))


def nudge_pptx(
    source: Path,
    *,
    source_sha256: str,
    slide: int,
    shape_id: str,
    output: Path,
    dx_mm: object = 0,
    dy_mm: object = 0,
    dwidth_mm: object = 0,
    dheight_mm: object = 0,
    dry_run: bool = False,
) -> dict:
    """Move/resize one inspected object without rebuilding the presentation.

    Positive x/y move right/down. Positive width/height expand right/down,
    keeping the top-left corner fixed. A new candidate and patch receipt are
    produced by the existing byte-preserving patch engine. Visual fit is still
    reviewed on the changed page and at the final native PowerPoint gate.
    """
    if type(slide) is not int or slide < 1:
        raise ValueError("slide must be an existing 1-based slide number")
    if not isinstance(shape_id, str) or not shape_id.isascii() or not shape_id.isdecimal() or int(shape_id) < 1:
        raise ValueError("shape_id must be an exact positive object ID from pptx-inspect")
    deltas = {
        "x": _delta_emu(dx_mm, "dx_mm"),
        "y": _delta_emu(dy_mm, "dy_mm"),
        "width": _delta_emu(dwidth_mm, "dwidth_mm"),
        "height": _delta_emu(dheight_mm, "dheight_mm"),
    }
    if not any(deltas.values()):
        raise ValueError("nudge is smaller than one EMU or has no movement")
    report = inspect_pptx(source, slides=[slide])
    if report["source_sha256"] != source_sha256.lower():
        raise ValueError("source changed; inspect the current original first")
    objects = [obj for obj in report["slides"][0]["objects"] if obj["shape_id"] == shape_id]
    if len(objects) != 1:
        raise ValueError("shape_id is missing or ambiguous on the selected slide")
    obj = objects[0]
    if not obj["editable"] or obj["geometry"] is None:
        raise ValueError(f"shape_id {shape_id} cannot be nudged: {obj['unsupported_reason']}")
    current = obj["geometry"]["emu"]
    target = {key: current[key] + delta for key, delta in deltas.items()}
    if target["width"] <= 0 or target["height"] <= 0:
        raise ValueError("nudge would make the object width or height non-positive")
    size = report["slide_size_emu"]
    if size is None:
        raise ValueError("slide dimensions are unresolved; use an explicit patch and review")
    def overflow(box: dict) -> tuple[int, int, int, int]:
        return (max(0, -box["x"]), max(0, -box["y"]),
                max(0, box["x"] + box["width"] - size["width"]),
                max(0, box["y"] + box["height"] - size["height"]))
    if any(after > before for before, after in zip(overflow(current), overflow(target))):
        raise ValueError("nudge would introduce or worsen off-slide geometry; use an explicit patch and page review")
    geometry = {"unit": "mm"}
    for key, delta in deltas.items():
        if delta:
            geometry[key] = str(Decimal(target[key]) / Decimal(36000))
    receipt = patch_pptx(
        {"source": str(source), "source_sha256": source_sha256,
         "edits": [{"action": "set", "slide": slide, "shape_id": shape_id, "geometry": geometry}],
         "dry_run": dry_run},
        output,
    )
    return {
        "status": receipt["status"], "source_sha256": receipt["source_sha256"],
        "candidate": receipt["candidate"], "candidate_sha256": receipt["candidate_sha256"],
        "receipt": str(Path(output) / "receipt.json"), "slide": slide, "shape_id": shape_id,
        "before_emu": current, "after_emu": target,
        "native_visual_verified": False,
    }
