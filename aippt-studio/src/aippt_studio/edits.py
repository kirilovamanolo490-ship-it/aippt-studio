from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Any

from .extraction import _officecli, _run_officecli
from .models import PptxEditBatch, PptxEditOperation, SourceRecord, utc_now
from .storage import job_dir, load_job, locked_job, read_json, read_model, write_json


class EditError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_command(executable: Path, arguments: list[str]) -> Any:
    try:
        output = _run_officecli(executable, [*arguments, "--json"], timeout=180)
    except Exception as error:
        raise EditError(str(error)) from error
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise EditError(f"OfficeCLI returned invalid JSON for {' '.join(arguments)}") from error
    if payload.get("success") is False:
        detail = payload.get("error") or payload.get("message") or "OfficeCLI command failed"
        raise EditError(str(detail))
    return payload.get("data", payload)


def _property_args(properties: dict[str, str | int | float | bool]) -> list[str]:
    args: list[str] = []
    for key, value in properties.items():
        if isinstance(value, bool):
            rendered = str(value).lower()
        elif isinstance(value, (str, int, float)):
            rendered = str(value)
        else:
            raise EditError(f"unsupported property value for {key}")
        args.extend(["--prop", f"{key}={rendered}"])
    return args


def _stable_shape_to_positional(executable: Path, deck: Path, target: str) -> str:
    match = re.fullmatch(r"/slide\[(\d+)\]/shape\[@id=(\d+)\]", target)
    if not match:
        return target
    slide_index, shape_id = int(match.group(1)), int(match.group(2))
    payload = _json_command(executable, ["get", str(deck), f"/slide[{slide_index}]", "--depth", "1"])
    results = payload.get("results") if isinstance(payload, dict) else None
    children = results[0].get("children", []) if results and isinstance(results[0], dict) else []
    shape_position = 0
    for child in children:
        if not isinstance(child, dict) or child.get("type") != "shape":
            continue
        shape_position += 1
        fmt = child.get("format") if isinstance(child.get("format"), dict) else {}
        if fmt.get("id") == shape_id:
            return f"/slide[{slide_index}]/shape[{shape_position}]"
    raise EditError(f"shape id {shape_id} was not found on slide {slide_index}")


def apply_pptx_operations(
    input_path: Path,
    output_path: Path,
    operations: list[PptxEditOperation],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    executable = _officecli()
    results: list[dict[str, Any]] = []
    validation: dict[str, Any] = {}
    shutil.copy2(input_path, output_path)
    try:
        for operation in operations:
            target = operation.target
            if operation.action == "add" and operation.element == "animation":
                target = _stable_shape_to_positional(executable, output_path, target)
            if operation.action == "add":
                command = ["add", str(output_path), target, "--type", str(operation.element)]
            elif operation.action == "set":
                command = ["set", str(output_path), target]
            else:
                command = ["remove", str(output_path), target]
            command.extend(_property_args(operation.properties))
            response = _json_command(executable, command)
            results.append(
                {
                    "id": operation.id,
                    "action": operation.action,
                    "target": target,
                    "element": operation.element,
                    "response": response,
                }
            )
        validation = {"status": "passed", "response": _json_command(executable, ["validate", str(output_path)])}
    except Exception as error:
        validation = {"status": "failed", "error": str(error)}
        raise
    finally:
        try:
            _run_officecli(executable, ["close", str(output_path)], timeout=30)
        except Exception:
            pass
    return results, validation


def _resolve_input(root: Path, job_source_ids: list[str], source_id: str | None, input_path: str | None) -> Path:
    if input_path:
        candidate = Path(input_path).expanduser().resolve()
        if not candidate.is_file():
            raise FileNotFoundError(f"PPTX input not found: {input_path}")
        return candidate
    records = [
        read_model(root / "normalized" / "sources" / f"{item}.json", SourceRecord)
        for item in job_source_ids
    ]
    if source_id:
        records = [record for record in records if record.id == source_id]
    record = next((item for item in records if item.kind == "powerpoint"), None)
    if not record:
        raise EditError("no PowerPoint input is available")
    return (root / record.stored_path).resolve()


@locked_job
def apply_job_edits(
    job_id: str,
    operations_path: str,
    *,
    source_id: str | None = None,
    input_path: str | None = None,
    output_path: str | None = None,
) -> dict[str, Any]:
    job = load_job(job_id)
    root = job_dir(job_id)
    operations_file = Path(operations_path).expanduser().resolve()
    payload = read_json(operations_file)
    if isinstance(payload, list):
        operation_items = payload
        batch_id = f"edit-{uuid.uuid4().hex[:8]}"
    elif isinstance(payload, dict):
        operation_items = payload.get("operations")
        batch_id = str(payload.get("id") or f"edit-{uuid.uuid4().hex[:8]}")
    else:
        raise EditError("operations file must contain a list or an object with operations")
    if not isinstance(operation_items, list) or not operation_items:
        raise EditError("operations file must contain at least one operation")
    operations = [PptxEditOperation.model_validate(item) for item in operation_items]
    source = _resolve_input(root, job.source_ids, source_id, input_path)
    destination = (root / (output_path or "build/edited-deck.pptx")).resolve() if not Path(output_path or "").is_absolute() else Path(output_path).resolve()
    if root not in destination.parents:
        raise EditError("output path must stay inside the job directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    batch = PptxEditBatch(
        id=batch_id,
        job_id=job_id,
        input_path=str(source.relative_to(root).as_posix()) if root in source.parents else str(source),
        output_path=str(destination.relative_to(root).as_posix()),
        input_sha256=_sha256(source),
        operations=operations,
    )
    artifact = root / "edits" / f"{batch.id}.json"
    try:
        batch.results, batch.validation = apply_pptx_operations(source, destination, operations)
        batch.output_sha256 = _sha256(destination)
        batch.applied_at = utc_now()
    except Exception as error:
        batch.validation = {"status": "failed", "error": str(error)}
        if destination.is_file():
            batch.output_sha256 = _sha256(destination)
        write_json(artifact, batch)
        raise EditError(str(error)) from error
    write_json(artifact, batch)
    return {"batch": batch.model_dump(mode="json"), "artifact": str(artifact.resolve())}
