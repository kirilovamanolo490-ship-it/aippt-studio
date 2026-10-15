from __future__ import annotations

import os
import re
import uuid
from pathlib import Path
from typing import Any

from .edits import apply_pptx_operations
from .extraction import _first_result, _json_command, _officecli, _run_officecli
from .models import PptxEditBatch, PptxEditOperation, SlideSpec, utc_now
from .renderer import RenderError, sha256
from .storage import job_dir, write_json


class MotionError(RuntimeError):
    pass


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _text_shapes(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        child
        for child in node.get("children", [])
        if isinstance(child, dict)
        and child.get("type") == "shape"
        and _normalized_text(str(child.get("text") or child.get("preview") or ""))
        and child.get("path")
    ]


def _find_text_shape(
    shapes: list[dict[str, Any]],
    text: str,
    used_paths: set[str],
) -> str | None:
    target = _normalized_text(text)
    if not target:
        return None
    for exact in (True, False):
        for shape in shapes:
            path = str(shape["path"])
            if path in used_paths:
                continue
            candidate = _normalized_text(str(shape.get("text") or shape.get("preview") or ""))
            matched = candidate == target if exact else target in candidate or candidate in target
            if matched:
                return path
    return None


def build_motion_operations(
    deck_path: Path,
    slides: list[SlideSpec],
) -> tuple[list[PptxEditOperation], list[str]]:
    executable = _officecli()
    operations: list[PptxEditOperation] = []
    warnings: list[str] = []
    try:
        for index, slide in enumerate(slides, start=1):
            if slide.motion == "none":
                continue
            node = _first_result(
                _json_command(executable, ["get", str(deck_path), f"/slide[{index}]", "--depth", "1"])
            )
            shapes = _text_shapes(node)
            used_paths: set[str] = set()
            title_path = _find_text_shape(shapes, slide.title, used_paths)
            if not title_path:
                raise MotionError(f"could not map title on slide {index}: {slide.title}")
            used_paths.add(title_path)
            operations.append(
                PptxEditOperation(
                    id=f"{slide.id}-transition",
                    action="set",
                    target=f"/slide[{index}]",
                    properties={"transition": "fade-slow", "advanceClick": True},
                )
            )
            operations.append(
                PptxEditOperation(
                    id=f"{slide.id}-title",
                    action="add",
                    target=title_path,
                    element="animation",
                    properties={
                        "effect": "fade",
                        "class": "entrance",
                        "duration": 450,
                        "trigger": "onClick",
                    },
                )
            )
            if slide.motion != "reveal":
                continue
            for point_index, point in enumerate(slide.points, start=1):
                point_path = _find_text_shape(shapes, point, used_paths)
                if not point_path:
                    warnings.append(f"slide {index} point {point_index} was not mapped to a text shape")
                    continue
                used_paths.add(point_path)
                operations.append(
                    PptxEditOperation(
                        id=f"{slide.id}-point-{point_index:02d}",
                        action="add",
                        target=point_path,
                        element="animation",
                        properties={
                            "effect": "wipe",
                            "class": "entrance",
                            "direction": "left",
                            "duration": 350,
                            "trigger": "afterPrevious",
                        },
                    )
                )
    finally:
        try:
            _run_officecli(executable, ["close", str(deck_path)], timeout=30)
        except Exception:
            pass
    return operations, warnings


def apply_motion_plan(
    job_id: str,
    deck_path: Path,
    slides: list[SlideSpec],
    *,
    batch_id: str,
    reported_path: str | None = None,
) -> dict[str, Any]:
    if os.environ.get("AIPPT_FAKE_RENDER") == "1":
        return {"status": "skipped-test-render", "operations": 0}
    root = job_dir(job_id)
    deck_path = deck_path.resolve()
    if root not in deck_path.parents or not deck_path.is_file():
        raise MotionError("motion input must be an existing PPTX inside the job directory")
    artifact = root / "edits" / f"{batch_id}.json"
    artifact.unlink(missing_ok=True)
    operations, warnings = build_motion_operations(deck_path, slides)
    if not operations:
        return {"status": "skipped", "operations": 0, "warnings": warnings}
    temporary = deck_path.with_name(f".{deck_path.stem}.{uuid.uuid4().hex[:8]}.motion.pptx")
    relative_deck = reported_path or deck_path.relative_to(root).as_posix()
    batch = PptxEditBatch(
        id=batch_id,
        job_id=job_id,
        input_path=relative_deck,
        output_path=relative_deck,
        input_sha256=sha256(deck_path),
        operations=operations,
        warnings=warnings,
    )
    try:
        batch.results, batch.validation = apply_pptx_operations(deck_path, temporary, operations)
        batch.output_sha256 = sha256(temporary)
        temporary.replace(deck_path)
        batch.applied_at = utc_now()
        write_json(artifact, batch)
    except Exception as error:
        batch.validation = {"status": "failed", "error": str(error)}
        if temporary.is_file():
            batch.output_sha256 = sha256(temporary)
        write_json(artifact, batch)
        raise RenderError(f"motion post-processing failed: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "status": "applied",
        "operations": len(operations),
        "warnings": warnings,
        "artifact": str(artifact.resolve()),
    }
