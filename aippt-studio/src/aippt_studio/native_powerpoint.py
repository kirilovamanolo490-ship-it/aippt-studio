from __future__ import annotations

import hashlib
import json
import math
import sys
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .config import PROJECT_ROOT
from .delivery_artifacts import supersede_delivery_artifacts
from .models import ExternalEngineManifest, JobState, utc_now
from .renderer import sha256
from .storage import job_dir, load_job, locked_job, read_json, read_model, save_job, write_json


SUPPORTED_EFFECTS = {
    "fade",
    "wipe",
    "fly",
    "zoom",
    "swivel",
    "float",
    "bounce",
    "appear-exit",
    "motion-right",
    "growShrink",
}
SUPPORTED_TRIGGERS = {"onClick", "withPrevious", "afterPrevious"}
SUPPORTED_EFFECT_DIRECTIONS = {
    "wipe": {"left", "right", "up", "down"},
    "fly": {"up", "right", "down", "left", "up_left", "up_right", "down_right", "down_left"},
    "zoom": {"in", "out", "in_slightly", "in_center", "out_slightly", "out_bottom"},
    "swivel": {"horizontal", "vertical"},
}
_PREVIEW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_MORPH_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,61}$")
_TOP_LEVEL_KEYS = {
    "version",
    "replace_existing",
    "renames",
    "effects",
    "carousels",
    "morph_links",
    "transitions",
    "previews",
}


class NativePowerPointError(RuntimeError):
    """A native PowerPoint operation failed at a classified execution stage."""

    def __init__(self, stage: str, message: str, report: Mapping[str, Any] | None = None) -> None:
        self.stage = stage
        self.report = dict(report or {})
        super().__init__(f"PowerPoint native stage '{stage}' failed: {message}")


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON array")
    return value


def _positive_slide(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _number(value: object, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0) or (not positive and result < 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{label} must be {qualifier}")
    return result


def _non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def validate_native_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and detach the JSON contract consumed by PowerPoint COM.

    The contract deliberately contains only generic object renames, animation
    effects, slide transitions, and animated previews. Task-specific layout or
    styling operations belong in the normal PPTX editing layer.
    """

    source = _require_mapping(plan, "plan")
    unknown = sorted(set(source) - _TOP_LEVEL_KEYS)
    if unknown:
        raise ValueError(f"unsupported native plan fields: {', '.join(unknown)}")
    normalized = json.loads(json.dumps(source, ensure_ascii=False))
    if normalized.get("version", 1) != 1:
        raise ValueError("native plan version must be 1")
    normalized["version"] = 1
    if not isinstance(normalized.get("replace_existing", False), bool):
        raise ValueError("replace_existing must be a boolean")
    normalized.setdefault("replace_existing", False)

    for index, raw in enumerate(_require_list(normalized.get("renames", []), "renames")):
        item = _require_mapping(raw, f"renames[{index}]")
        allowed = {"slide", "from", "to"}
        if set(item) - allowed:
            raise ValueError(f"renames[{index}] contains unsupported fields")
        _positive_slide(item.get("slide"), f"renames[{index}].slide")
        _non_empty_string(item.get("from"), f"renames[{index}].from")
        _non_empty_string(item.get("to"), f"renames[{index}].to")

    for index, raw in enumerate(_require_list(normalized.get("effects", []), "effects")):
        item = _require_mapping(raw, f"effects[{index}]")
        allowed = {
            "slide",
            "shape",
            "effect",
            "trigger",
            "duration",
            "delay",
            "repeat",
            "autoreverse",
            "exit",
            "direction",
            "motion_by_x",
            "scale_percent",
        }
        if set(item) - allowed:
            raise ValueError(f"effects[{index}] contains unsupported fields")
        _positive_slide(item.get("slide"), f"effects[{index}].slide")
        _non_empty_string(item.get("shape"), f"effects[{index}].shape")
        effect = item.get("effect")
        if effect not in SUPPORTED_EFFECTS:
            raise ValueError(f"effects[{index}].effect must be one of {sorted(SUPPORTED_EFFECTS)}")
        trigger = item.get("trigger", "onClick")
        if trigger not in SUPPORTED_TRIGGERS:
            raise ValueError(f"effects[{index}].trigger must be one of {sorted(SUPPORTED_TRIGGERS)}")
        item.setdefault("trigger", trigger)
        _number(item.get("duration", 0.5), f"effects[{index}].duration", positive=True)
        item.setdefault("duration", 0.5)
        _number(item.get("delay", 0), f"effects[{index}].delay")
        item.setdefault("delay", 0)
        if item.get("repeat") is not None:
            _number(item["repeat"], f"effects[{index}].repeat", positive=True)
        if not isinstance(item.get("autoreverse", False), bool):
            raise ValueError(f"effects[{index}].autoreverse must be a boolean")
        item.setdefault("autoreverse", False)
        if not isinstance(item.get("exit", effect == "appear-exit"), bool):
            raise ValueError(f"effects[{index}].exit must be a boolean")
        if effect == "appear-exit" and item.get("exit") is False:
            raise ValueError(f"effects[{index}].exit cannot be false for appear-exit")
        item.setdefault("exit", effect == "appear-exit")
        if item.get("direction") is not None:
            supported_directions = SUPPORTED_EFFECT_DIRECTIONS.get(effect)
            if supported_directions is None:
                raise ValueError(f"effects[{index}].direction is not supported for {effect}")
            if item["direction"] not in supported_directions:
                raise ValueError(
                    f"effects[{index}].direction must be one of {sorted(supported_directions)}"
                )
        if item.get("motion_by_x") is not None:
            _number(item["motion_by_x"], f"effects[{index}].motion_by_x")
        if item.get("scale_percent") is not None:
            _number(item["scale_percent"], f"effects[{index}].scale_percent", positive=True)

    for index, raw in enumerate(_require_list(normalized.get("carousels", []), "carousels")):
        item = _require_mapping(raw, f"carousels[{index}]")
        allowed = {
            "slide",
            "shapes",
            "duration",
            "hold_seconds",
            "loops",
            "return_to_first",
            "start_trigger",
        }
        if set(item) - allowed:
            raise ValueError(f"carousels[{index}] contains unsupported fields")
        _positive_slide(item.get("slide"), f"carousels[{index}].slide")
        shapes = _require_list(item.get("shapes"), f"carousels[{index}].shapes")
        shape_names = [_non_empty_string(value, f"carousels[{index}].shapes") for value in shapes]
        if len(shape_names) < 2:
            raise ValueError(f"carousels[{index}].shapes must contain at least two shape names")
        if len(shape_names) != len(set(shape_names)):
            raise ValueError(f"carousels[{index}].shapes must be unique")
        _number(item.get("duration", 0.5), f"carousels[{index}].duration", positive=True)
        item.setdefault("duration", 0.5)
        _number(item.get("hold_seconds", 1.5), f"carousels[{index}].hold_seconds")
        item.setdefault("hold_seconds", 1.5)
        loops = item.get("loops", 1)
        if isinstance(loops, bool) or not isinstance(loops, int) or not 1 <= loops <= 20:
            raise ValueError(f"carousels[{index}].loops must be an integer from 1 to 20")
        item.setdefault("loops", 1)
        if not isinstance(item.get("return_to_first", True), bool):
            raise ValueError(f"carousels[{index}].return_to_first must be a boolean")
        item.setdefault("return_to_first", True)
        if loops > 1 and not item["return_to_first"]:
            raise ValueError(f"carousels[{index}] needs return_to_first=true when loops is greater than 1")
        trigger = item.get("start_trigger", "onClick")
        if trigger not in SUPPORTED_TRIGGERS:
            raise ValueError(f"carousels[{index}].start_trigger must be one of {sorted(SUPPORTED_TRIGGERS)}")
        item.setdefault("start_trigger", trigger)

    for index, raw in enumerate(_require_list(normalized.get("morph_links", []), "morph_links")):
        item = _require_mapping(raw, f"morph_links[{index}]")
        allowed = {"from_slide", "from_shape", "to_slide", "to_shape", "identity", "duration"}
        if set(item) - allowed:
            raise ValueError(f"morph_links[{index}] contains unsupported fields")
        from_slide = _positive_slide(item.get("from_slide"), f"morph_links[{index}].from_slide")
        to_slide = _positive_slide(item.get("to_slide"), f"morph_links[{index}].to_slide")
        if to_slide != from_slide + 1:
            raise ValueError(f"morph_links[{index}] must connect adjacent slides")
        _non_empty_string(item.get("from_shape"), f"morph_links[{index}].from_shape")
        _non_empty_string(item.get("to_shape"), f"morph_links[{index}].to_shape")
        identity = _non_empty_string(item.get("identity"), f"morph_links[{index}].identity")
        if not _MORPH_IDENTITY.fullmatch(identity):
            raise ValueError(f"morph_links[{index}].identity must contain only ASCII letters, numbers, _ or -")
        _number(item.get("duration", 1.0), f"morph_links[{index}].duration", positive=True)
        item.setdefault("duration", 1.0)

    for index, raw in enumerate(_require_list(normalized.get("transitions", []), "transitions")):
        item = _require_mapping(raw, f"transitions[{index}]")
        allowed = {"slide", "type", "duration", "advance_on_click", "advance_after_seconds"}
        if set(item) - allowed:
            raise ValueError(f"transitions[{index}] contains unsupported fields")
        _positive_slide(item.get("slide"), f"transitions[{index}].slide")
        if item.get("type") != "morph":
            raise ValueError(f"transitions[{index}].type must be morph")
        _number(item.get("duration", 1.0), f"transitions[{index}].duration", positive=True)
        item.setdefault("duration", 1.0)
        if not isinstance(item.get("advance_on_click", True), bool):
            raise ValueError(f"transitions[{index}].advance_on_click must be a boolean")
        item.setdefault("advance_on_click", True)
        if item.get("advance_after_seconds") is not None:
            _number(item["advance_after_seconds"], f"transitions[{index}].advance_after_seconds")

    seen_preview_ids: set[str] = set()
    for index, raw in enumerate(_require_list(normalized.get("previews", []), "previews")):
        item = _require_mapping(raw, f"previews[{index}]")
        allowed = {
            "id",
            "slides",
            "seconds_per_slide",
            "vertical_resolution",
            "frames_per_second",
            "quality",
            "use_timings_and_narrations",
            "timeout_seconds",
        }
        if set(item) - allowed:
            raise ValueError(f"previews[{index}] contains unsupported fields")
        preview_id = _non_empty_string(item.get("id"), f"previews[{index}].id")
        if not _PREVIEW_ID.fullmatch(preview_id):
            raise ValueError(f"previews[{index}].id must contain only ASCII letters, numbers, _ or -")
        if preview_id in seen_preview_ids:
            raise ValueError(f"duplicate preview id: {preview_id}")
        seen_preview_ids.add(preview_id)
        slides = _require_list(item.get("slides"), f"previews[{index}].slides")
        slide_numbers = [_positive_slide(value, f"previews[{index}].slides") for value in slides]
        if not slide_numbers:
            raise ValueError(f"previews[{index}].slides must not be empty")
        if slide_numbers != sorted(set(slide_numbers)):
            raise ValueError(f"previews[{index}].slides must be unique and ascending")
        _number(item.get("seconds_per_slide", 5), f"previews[{index}].seconds_per_slide", positive=True)
        item.setdefault("seconds_per_slide", 5)
        for field, default, minimum, maximum in (
            ("vertical_resolution", 1080, 240, 2160),
            ("frames_per_second", 30, 1, 60),
            ("quality", 85, 1, 100),
            ("timeout_seconds", 300, 1, 3600),
        ):
            value = item.get(field, default)
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"previews[{index}].{field} must be an integer from {minimum} to {maximum}")
            item.setdefault(field, default)
        if not isinstance(item.get("use_timings_and_narrations", False), bool):
            raise ValueError(f"previews[{index}].use_timings_and_narrations must be a boolean")
        item.setdefault("use_timings_and_narrations", False)

    normalized.setdefault("renames", [])
    normalized.setdefault("effects", [])
    normalized.setdefault("carousels", [])
    normalized.setdefault("morph_links", [])
    normalized.setdefault("transitions", [])
    normalized.setdefault("previews", [])
    return normalized


def compile_native_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Compile carousel and Morph identity operators into native COM primitives."""

    normalized = validate_native_plan(plan)
    compiled = json.loads(json.dumps(normalized, ensure_ascii=False))
    carousels = compiled.pop("carousels", [])
    morph_links = compiled.pop("morph_links", [])

    effects = compiled["effects"]
    for carousel in carousels:
        shapes = list(carousel["shapes"])
        pairs = list(zip(shapes, shapes[1:]))
        if carousel["return_to_first"]:
            pairs.append((shapes[-1], shapes[0]))
        for loop_index in range(carousel["loops"]):
            for pair_index, (current_shape, next_shape) in enumerate(pairs):
                first_step = loop_index == 0 and pair_index == 0
                effects.append(
                    {
                        "slide": carousel["slide"],
                        "shape": current_shape,
                        "effect": "fade",
                        "trigger": carousel["start_trigger"] if first_step else "afterPrevious",
                        "duration": carousel["duration"],
                        "delay": carousel["hold_seconds"],
                        "autoreverse": False,
                        "exit": True,
                    }
                )
                effects.append(
                    {
                        "slide": carousel["slide"],
                        "shape": next_shape,
                        "effect": "fade",
                        "trigger": "withPrevious",
                        "duration": carousel["duration"],
                        "delay": 0,
                        "autoreverse": False,
                        "exit": False,
                    }
                )

    renames = compiled["renames"]
    rename_targets: dict[tuple[int, str], str] = {
        (int(item["slide"]), str(item["from"])): str(item["to"]) for item in renames
    }
    transition_slides = {int(item["slide"]) for item in compiled["transitions"]}
    for link in morph_links:
        target = f"!!{link['identity']}"
        for slide_field, shape_field in (("from_slide", "from_shape"), ("to_slide", "to_shape")):
            key = (int(link[slide_field]), str(link[shape_field]))
            existing = rename_targets.get(key)
            if existing is not None and existing != target:
                raise ValueError(
                    f"shape {key[1]!r} on slide {key[0]} has conflicting Morph identities"
                )
            if existing is None:
                renames.append({"slide": key[0], "from": key[1], "to": target})
                rename_targets[key] = target
        to_slide = int(link["to_slide"])
        if to_slide not in transition_slides:
            compiled["transitions"].append(
                {
                    "slide": to_slide,
                    "type": "morph",
                    "duration": link["duration"],
                    "advance_on_click": True,
                }
            )
            transition_slides.add(to_slide)

    return validate_native_plan(compiled)


def build_apply_command(
    powershell: str | Path,
    script: Path,
    input_pptx: Path,
    output_pptx: Path,
    contract_path: Path,
) -> list[str]:
    return [
        str(powershell),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Mode",
        "Apply",
        "-InputPptx",
        str(input_pptx),
        "-OutputPptx",
        str(output_pptx),
        "-ContractPath",
        str(contract_path),
    ]


def build_probe_command(
    powershell: str | Path,
    script: Path,
    baseline_pptx: Path,
    candidate_pptx: Path,
    output_dir: Path,
) -> list[str]:
    return [
        str(powershell),
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-Mode",
        "Probe",
        "-BaselinePptx",
        str(baseline_pptx),
        "-CandidatePptx",
        str(candidate_pptx),
        "-ProbeOutputDir",
        str(output_dir),
    ]


def _powershell_executable() -> str:
    if os.name != "nt":
        raise NativePowerPointError("platform_unsupported", "Native COM animation/video operators require Windows PowerPoint. Mac supports static roundtrip via AppleScript; do not treat this operator as available.")
    executable = os.environ.get("AIPPT_POWERSHELL") or shutil.which("powershell") or shutil.which("pwsh")
    if not executable:
        raise NativePowerPointError("startup", "PowerShell is unavailable; set AIPPT_POWERSHELL")
    return executable


def _parse_report(stdout: str) -> dict[str, Any] | None:
    for line in reversed(stdout.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and "status" in value:
            return value
    return None


def _run_native(command: list[str], timeout: int) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise NativePowerPointError("process_timeout", f"PowerShell exceeded {timeout} seconds") from error
    report = _parse_report(completed.stdout)
    if report is None:
        detail = (completed.stderr or completed.stdout or f"PowerShell exited with {completed.returncode}").strip()
        raise NativePowerPointError("process", detail)
    if completed.returncode != 0 or report.get("status") != "passed":
        stage = str(report.get("failed_stage") or "process")
        error = report.get("error")
        message = str(error.get("message")) if isinstance(error, Mapping) else str(error or "native operation failed")
        raise NativePowerPointError(stage, message, report)
    return report


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _preview_root(output_pptx: Path) -> Path:
    for parent in output_pptx.parents:
        if parent.name.lower() == "build":
            return parent / "animated-previews"
    return output_pptx.parent / "animated-previews"


def _enrich_previews(report: dict[str, Any], output_pptx: Path, expected_ids: list[str]) -> None:
    expected_root = _preview_root(output_pptx).resolve()
    previews = report.get("previews", [])
    if not isinstance(previews, list):
        raise NativePowerPointError("create_video", "PowerPoint returned an invalid previews report", report)
    actual_ids = [preview.get("id") if isinstance(preview, dict) else None for preview in previews]
    if actual_ids != expected_ids:
        raise NativePowerPointError(
            "create_video",
            f"PowerPoint returned previews {actual_ids!r}; expected {expected_ids!r}",
            report,
        )
    for preview in previews:
        if not isinstance(preview, dict):
            raise NativePowerPointError("create_video", "PowerPoint returned an invalid preview item", report)
        path = Path(str(preview.get("path", ""))).resolve()
        if path.parent != expected_root or path.name != f"{preview['id']}.mp4" or not path.is_file():
            raise NativePowerPointError("create_video", f"expected preview was not created: {path}", report)
        preview["path"] = str(path)
        preview["sha256"] = _sha256(path)
        preview["bytes"] = path.stat().st_size


def apply_native_plan(
    input_pptx: Path,
    output_pptx: Path,
    plan: Mapping[str, Any],
    *,
    timeout: int = 1800,
    powershell: str | Path | None = None,
    script: Path | None = None,
) -> dict[str, Any]:
    input_pptx = input_pptx.resolve()
    output_pptx = output_pptx.resolve()
    if not input_pptx.is_file():
        raise FileNotFoundError(input_pptx)
    if input_pptx == output_pptx:
        raise ValueError("native PowerPoint output must not overwrite the input PPTX")
    normalized = validate_native_plan(plan)
    compiled = compile_native_plan(normalized)
    output_pptx.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin" and powershell is None and script is None:
        from .mac_native import apply_mac_native_plan
        return apply_mac_native_plan(input_pptx, output_pptx, normalized)
    script = (script or PROJECT_ROOT / "scripts" / "powerpoint_native.ps1").resolve()
    if not script.is_file():
        raise FileNotFoundError(script)
    executable = str(powershell or _powershell_executable())
    with tempfile.TemporaryDirectory(prefix="aippt-native-") as temporary:
        contract_path = Path(temporary) / "contract.json"
        contract_path.write_text(json.dumps(compiled, ensure_ascii=False, indent=2), encoding="utf-8")
        report = _run_native(
            build_apply_command(executable, script, input_pptx, output_pptx, contract_path),
            timeout,
        )
    if not output_pptx.is_file():
        raise NativePowerPointError("publish", f"PowerPoint did not create {output_pptx}", report)
    _enrich_previews(report, output_pptx, [str(item["id"]) for item in normalized["previews"]])
    report["operators"] = {
        "carousels": len(normalized["carousels"]),
        "morph_links": len(normalized["morph_links"]),
    }
    return report


def probe_powerpoint(
    baseline_pptx: Path,
    candidate_pptx: Path,
    output_dir: Path,
    *,
    timeout: int = 600,
    powershell: str | Path | None = None,
    script: Path | None = None,
) -> dict[str, Any]:
    baseline_pptx = baseline_pptx.resolve()
    candidate_pptx = candidate_pptx.resolve()
    if not baseline_pptx.is_file():
        raise FileNotFoundError(baseline_pptx)
    if not candidate_pptx.is_file():
        raise FileNotFoundError(candidate_pptx)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin" and powershell is None and script is None:
        from .mac_native import probe_mac_powerpoint
        return probe_mac_powerpoint(baseline_pptx, candidate_pptx, output_dir)
    script = (script or PROJECT_ROOT / "scripts" / "powerpoint_native.ps1").resolve()
    if not script.is_file():
        raise FileNotFoundError(script)
    executable = str(powershell or _powershell_executable())
    return _run_native(
        build_probe_command(executable, script, baseline_pptx, candidate_pptx, output_dir),
        timeout,
    )


def _job_path(root: Path, value: str) -> Path:
    candidate = (root / value).resolve()
    if root.resolve() not in candidate.parents:
        raise ValueError("native PowerPoint input must stay inside the job directory")
    return candidate


@locked_job
def apply_job_native_plan(
    job_id: str,
    plan_path: Path,
    *,
    input_path: str = "build/deck.pptx",
) -> dict[str, Any]:
    """Apply a native plan atomically and bind its report to the job.

    The PowerPoint process always writes a separate temporary PPTX. The job's
    current deck and animated previews are replaced only after the native
    operation, save/reopen check, and requested video previews all succeed.
    """

    job = load_job(job_id)
    if job.state == JobState.DELIVERED:
        raise NativePowerPointError("job_state", "delivered jobs must be reopened through review before editing")
    root = job_dir(job_id).resolve()
    deck = _job_path(root, input_path)
    if deck.suffix.lower() != ".pptx" or not deck.is_file():
        raise FileNotFoundError(deck)
    plan = read_json(plan_path.resolve())
    if not isinstance(plan, Mapping):
        raise ValueError("native PowerPoint plan must be a JSON object")
    normalized = validate_native_plan(plan)
    input_hash = sha256(deck)
    token = uuid.uuid4().hex[:8]
    temporary = deck.with_name(f".{deck.stem}.{token}.native.pptx")
    preview_dir = root / "build" / "animated-previews"
    preview_backup = root / "build" / f".animated-previews-backup-{token}"
    artifact = root / "edits" / "native-powerpoint.json"
    if preview_dir.exists():
        preview_dir.replace(preview_backup)
    try:
        report = apply_native_plan(deck, temporary, normalized)
        output_hash = sha256(temporary)
        temporary.replace(deck)
        # The native operation has now changed the bytes behind any exported
        # candidate.  Archive that package before a status call can offer it
        # as the current deck.
        supersede_delivery_artifacts(root, "native PowerPoint plan changed the current deck")
        report["output"] = str(deck)
        payload = {
            "schema_version": 1,
            "job_id": job.id,
            "input_path": deck.relative_to(root).as_posix(),
            "input_sha256": input_hash,
            "output_sha256": output_hash,
            "plan": normalized,
            "report": report,
            "applied_at": utc_now(),
        }
        write_json(artifact, payload)
        external_manifest_path = root / "build" / "external-engine-manifest.json"
        if external_manifest_path.is_file():
            external_manifest = read_model(external_manifest_path, ExternalEngineManifest)
            external_manifest.pptx_sha256 = output_hash
            write_json(external_manifest_path, external_manifest)
        job.approvals["final"] = False
        job.approval_hashes.pop("final", None)
        save_job(job)
        return payload
    except Exception:
        if preview_dir.exists():
            shutil.rmtree(preview_dir)
        if preview_backup.exists():
            preview_backup.replace(preview_dir)
        raise
    finally:
        temporary.unlink(missing_ok=True)
        if preview_backup.exists():
            shutil.rmtree(preview_backup)
