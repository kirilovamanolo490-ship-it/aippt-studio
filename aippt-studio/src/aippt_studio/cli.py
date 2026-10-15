from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import webbrowser
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from .adapters import capability_report
from .assets import (
    AssetAcquisitionError,
    acquire_asset,
    add_asset_need,
    asset_status,
    load_asset_registry,
    skip_asset_need,
)
from .config import PROJECT_ROOT, WORKSPACE_ROOT
from .edits import EditError, apply_job_edits
from .extraction import ExtractionError, inspect_job_sources
from .ingest import ingest_sources
from .knowledge import recall_task_knowledge
from .models import ChangeMode, InputRole, JobBrief, PresentationForm, ProductionRoute, VisualSystemMode
from .planning import planning_decision
from .native_powerpoint import NativePowerPointError, apply_job_native_plan, probe_powerpoint
from .reconcile import audit_job
from .review_server import create_app
from .renderer import RenderError
from .storage import job_dir, load_job, read_json, read_model, write_json
from . import python_runtime
from .workflow import (
    WorkflowError,
    approve_gate,
    bind_catalog_template,
    confirm_catalog_template,
    complete_revision,
    configure_presentation_intent,
    configure_route,
    create_job,
    export_job,
    initialize_work_intake,
    prepare_catalog_template_selection,
    queue_revisions,
    resume_job,
    run_job,
    status_payload,
)


app = typer.Typer(no_args_is_help=True, add_completion=False, help="AI PPT 接单生产工作台")


def _require_host_native_python() -> None:
    """Stop public CLI work before a translated Python can select runtimes.

    The supported Work and maintenance launchers already choose a verified
    Python.  ``aippt`` and ``python -m aippt_studio`` are public entrypoints as
    well, though, so they must not let an x64 process under Rosetta make the
    rest of runtime selection believe that an Apple-Silicon host is x64.
    """

    reason = python_runtime.validate_identity(python_runtime.current_process_identity())
    if reason is not None:
        _echo_utf8("ERROR: AI PPT Studio requires a host-native Python: " + reason, err=True)
        raise typer.Exit(2)


def _help_requested() -> bool:
    """Keep recovery documentation usable before a translated runtime is fixed.

    Click resolves a top-level ``--help`` before the group callback, but it
    invokes that callback before parsing a subcommand's help option.  The raw
    public command line is the only common signal at this point.  ``--help``
    and ``-h`` are reserved help flags in this CLI, so allowing them cannot
    accidentally authorize normal command work.
    """

    return any(argument in {"--help", "-h"} for argument in sys.argv[1:])


@app.callback()
def runtime_guard() -> None:
    """Apply the shared physical-host guard to every executable CLI command."""

    if _help_requested():
        return
    _require_host_native_python()


@app.command("expression-review")
def expression_review_command(
    job: str,
    gate: Annotated[str, typer.Option("--gate")],
    request: Annotated[Path | None, typer.Option("--request")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Prepare actual-page evidence or record the assistant's editorial judgment."""
    from .expression_review import prepare_expression_review, record_expression_review
    try:
        result = (record_expression_review(job, gate, json.loads(request.read_text(encoding="utf-8-sig")))
                  if request else prepare_expression_review(job, gate))
        emit(result, as_json)
    except (OSError, ValueError, KeyError, TypeError, RenderError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("quality-check")
def quality_check_command(
    source: Annotated[Path, typer.Argument(help="Actual PPTX to inspect")],
    policy: Annotated[Path | None, typer.Option("--policy")] = None,
    output: Annotated[Path | None, typer.Option("--output")] = None,
    presentation_form: Annotated[str, typer.Option("--form", help="Presentation form for contextual local review signals")] = "general",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check actual PPTX font sizes, object bounds and possible obstruction."""
    from .presentation_quality import audit_presentation
    try:
        from .authoring_policy import standalone_audit_policy
        rules = standalone_audit_policy(json.loads(policy.read_text(encoding="utf-8-sig"))) if policy else None
        result = audit_presentation(source, policy=rules)
        if result.get("status") != "errors":
            from .form_quality_profile import audit_form_quality
            form_review = audit_form_quality(source, presentation_form)
            if form_review["source_sha256"] != result.get("source_sha256"):
                raise ValueError("PPTX changed between structural and contextual local review")
            result["form_review"] = form_review
        if output:
            write_json(output, result)
            from .authoring_policy import record_quality_findings
            record_quality_findings(output.parent, result)
        emit(result, as_json)
        if result.get("status") != "passed":
            raise typer.Exit(2 if result.get("status") == "errors" else 1)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("output-preflight")
def output_preflight_command(
    work_dir: Annotated[Path, typer.Option("--work-dir", help="Existing Work package")],
    source: Annotated[Path | None, typer.Option("--source", help="Specific PPTX in this Work output directory")] = None,
    policy: Annotated[Path | None, typer.Option("--policy", help="JSON with source_sha256 and quality_policy")]=None,
    presentation_form: Annotated[str | None, typer.Option("--form", help="Contextual review form; defaults to unanimous Work briefs")]= None,
    output: Annotated[Path | None, typer.Option("--output", help="Local QA report path")]=None,
    record_observation: Annotated[bool, typer.Option("--record-observation", help="Record unresolved local findings in this Work's learning evidence")]=False,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Audit the current output bytes and identify stale final QA evidence."""
    from .output_preflight import output_preflight
    from .form_quality_profile import audit_form_quality, select_work_form
    try:
        policy_data = json.loads(policy.read_text(encoding="utf-8-sig")) if policy else None
        if policy_data is not None and (not isinstance(policy_data, dict)
                                        or set(policy_data) != {"source_sha256", "quality_policy"}):
            raise ValueError("policy must contain only source_sha256 and quality_policy")
        result = output_preflight(work_dir, source, policy=policy_data["quality_policy"] if policy_data else None,
                                  policy_source_sha256=policy_data["source_sha256"] if policy_data else None)
        if result.get("identity") and result.get("status") != "errors":
            form_selection = select_work_form(work_dir, presentation_form)
            form_review = audit_form_quality(Path(result["source"]), form_selection["form"])
            if form_review["source_sha256"] != result["identity"]["sha256"]:
                raise ValueError("PPTX changed between output preflight and contextual review")
            result["form_selection"] = form_selection
            result["form_review"] = form_review
        if output:
            write_json(output, result)
        if record_observation and result.get("quality", {}).get("status") == "needs_review":
            if output is None or not output.resolve().is_relative_to(work_dir.resolve()):
                raise ValueError("record-observation requires a QA output path inside this Work")
            from .authoring_policy import record_quality_findings
            result["observation"] = record_quality_findings(work_dir, result["quality"])
            write_json(output, result)
        emit(result, as_json)
        if result["status"] != "passed":
            raise typer.Exit(1 if result["status"] == "needs_review" else 2)
    except (OSError, ValueError, KeyError, TypeError) as error:
        if output:
            try:
                write_json(output, {"schema_version": 1, "status": "errors", "work": str(work_dir),
                                    "source": str(source) if source else None,
                                    "errors": [{"code": "PREFLIGHT_INPUT_ERROR", "type": type(error).__name__,
                                                "message": str(error)}]})
            except OSError:
                pass
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("quality-policy")
def quality_policy_command(
    job: str,
    request: Annotated[Path, typer.Option("--request")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Set explicit object roles/exceptions and reopen the affected visual review."""
    from .authoring_policy import update_job_policy
    try:
        emit(update_job_policy(job, json.loads(request.read_text(encoding="utf-8-sig"))), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("pptx-inspect")
def pptx_inspect_command(
    source: Path,
    output: Annotated[Path | None, typer.Option("--output")] = None,
    slide: Annotated[list[int] | None, typer.Option("--slide", help="Inspect only these 1-based slides; repeat for several")] = None,
    brief: Annotated[bool, typer.Option("--brief", help="Print compact shape choices; --output still saves the full report")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List stable slide/object IDs and geometry before a selective edit."""
    from .pptx_edit import inspect_pptx
    try:
        result = inspect_pptx(source, slides=slide)
        if output:
            write_json(output, result)
        if brief:
            summary = {"schema_version": 1, "source_sha256": result["source_sha256"],
                       "full_report": str(output) if output else None,
                       "slides": [{"slide": page["slide"], "part": page["part"],
                                   "objects": [{"shape_id": obj["shape_id"], "name": obj["name"],
                                                "type": obj["type"], "text_preview": obj["text"][:120],
                                                "editable": obj["editable"]} for obj in page["objects"]]}
                                  for page in result["slides"]]}
            emit(summary, as_json)
        else:
            emit(result, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("pptx-new")
def pptx_new_command(
    output: Annotated[Path, typer.Argument(help="New blank 16:9 PPTX destination")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Create a blank editable 16:9 PPTX without opening PowerPoint."""
    from .pptx_file_ops import create_blank_pptx
    try:
        emit(create_blank_pptx(output), as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("pptx-save-as")
def pptx_save_as_command(
    source: Annotated[Path, typer.Argument(help="Existing source PPTX")],
    output: Annotated[Path, typer.Option("--output", help="New PPTX destination")],
    replace: Annotated[bool, typer.Option("--replace", help="Atomically update an existing destination")]=False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Copy an existing PPTX to a new path without launching PowerPoint."""
    from .pptx_file_ops import save_as_pptx
    try:
        emit(save_as_pptx(source, output, replace=replace), as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("pptx-open")
def pptx_open_command(
    source: Annotated[Path, typer.Argument(help="Existing PPTX to open in Microsoft PowerPoint")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Request macOS to open a PPTX in PowerPoint; this is not native QA."""
    from .pptx_file_ops import request_powerpoint_open
    try:
        emit(request_powerpoint_open(source), as_json)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("pptx-patch")
def pptx_patch_command(
    request: Annotated[Path, typer.Option("--request")],
    output: Annotated[Path, typer.Option("--output", help="New Work workspace directory")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Apply a hash-bound, explicit object edit and preserve all other PPTX parts."""
    from .pptx_edit import patch_pptx
    try:
        result = patch_pptx(json.loads(request.read_text(encoding="utf-8-sig")), output)
        emit(result, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("fast-revise")
def fast_revise_command(
    request: Annotated[Path, typer.Option("--request", help="Source-hash-bound pptx-patch JSON request")],
    output: Annotated[Path, typer.Option("--output", help="New Work-local revision directory")],
    preview_backend: Annotated[str, typer.Option("--preview", help="officekit, officecli, or none")]= "officekit",
    policy: Annotated[Path | None, typer.Option("--policy", help="Optional local authoring policy JSON")]=None,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Patch an existing PPTX, audit changed pages, and preview just those pages."""
    from .fast_revision import revise_pptx
    try:
        from .authoring_policy import standalone_audit_policy
        rules = standalone_audit_policy(json.loads(policy.read_text(encoding="utf-8-sig"))) if policy else None
        result = revise_pptx(json.loads(request.read_text(encoding="utf-8-sig")), output,
                             preview_backend=preview_backend, policy=rules)
        emit(result, as_json)
        if result["status"] == "needs_structural_review":
            raise typer.Exit(1)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("revision-publish")
def revision_publish_command(
    work_dir: Annotated[Path, typer.Option("--work-dir", help="Active Work directory")],
    revision: Annotated[Path, typer.Option("--revision", help="Existing fast-revision directory")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Publish a scoped candidate to its current Work-local source path."""
    from .current_revision import publish_revision
    try:
        emit(publish_revision(work_dir, revision), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("pptx-text")
def pptx_text_command(
    source: Annotated[Path, typer.Argument(help="Current editable PPTX")],
    source_sha256: Annotated[str, typer.Option("--source-sha256", help="Exact hash from pptx-inspect")],
    slide: Annotated[int, typer.Option("--slide", help="1-based slide number")],
    shape_id: Annotated[str, typer.Option("--shape-id", help="Exact shape ID from pptx-inspect")],
    expected_text: Annotated[str, typer.Option("--expected-text", help="One current ordinary text run")],
    replacement_text: Annotated[str, typer.Option("--replacement-text", help="Replacement for that run")],
    output: Annotated[Path, typer.Option("--output", help="New Work workspace directory")],
    paragraph: Annotated[int | None, typer.Option("--paragraph", help="1-based paragraph when text repeats")] = None,
    run: Annotated[int | None, typer.Option("--run", help="1-based run when text repeats")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Replace one exact formatted text run without writing a patch script."""
    from .pptx_edit import patch_pptx
    try:
        edit = {"action": "replace_text", "slide": slide, "shape_id": shape_id,
                "expected_text": expected_text, "replacement_text": replacement_text}
        if paragraph is not None:
            edit["paragraph"] = paragraph
        if run is not None:
            edit["run"] = run
        receipt = patch_pptx({"source": str(source), "source_sha256": source_sha256,
                              "edits": [edit], "dry_run": dry_run}, output)
        emit({"status": receipt["status"], "source_sha256": receipt["source_sha256"],
              "candidate": receipt["candidate"], "candidate_sha256": receipt["candidate_sha256"],
              "plan_sha256": receipt["plan_sha256"], "changed_parts": receipt["changed_parts"],
              "receipt": str(Path(output) / "receipt.json"), "slide": slide, "shape_id": shape_id,
              "native_visual_verified": False}, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("pptx-nudge")
def pptx_nudge_command(
    source: Annotated[Path, typer.Argument(help="Current editable PPTX")],
    source_sha256: Annotated[str, typer.Option("--source-sha256", help="Exact hash from pptx-inspect")],
    slide: Annotated[int, typer.Option("--slide", help="1-based slide number")],
    shape_id: Annotated[str, typer.Option("--shape-id", help="Exact shape ID from pptx-inspect")],
    output: Annotated[Path, typer.Option("--output", help="New Work workspace directory")],
    dx_mm: Annotated[float, typer.Option("--dx-mm", help="Move right (+) or left (-), millimetres")] = 0,
    dy_mm: Annotated[float, typer.Option("--dy-mm", help="Move down (+) or up (-), millimetres")] = 0,
    dwidth_mm: Annotated[float, typer.Option("--dwidth-mm", help="Expand/shrink right edge, millimetres")] = 0,
    dheight_mm: Annotated[float, typer.Option("--dheight-mm", help="Expand/shrink bottom edge, millimetres")] = 0,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Move or resize one exact object without regenerating the slide."""
    from .quick_nudge import nudge_pptx
    try:
        result = nudge_pptx(source, source_sha256=source_sha256, slide=slide,
                            shape_id=shape_id, output=output, dx_mm=dx_mm, dy_mm=dy_mm,
                            dwidth_mm=dwidth_mm, dheight_mm=dheight_mm, dry_run=dry_run)
        emit(result, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("host-nudge")
def host_nudge_command(
    source: Annotated[Path, typer.Argument(help="Current editable PPTX; a staged copy is edited")],
    source_sha256: Annotated[str, typer.Option("--source-sha256", help="Exact hash from pptx-inspect")],
    slide: Annotated[int, typer.Option("--slide", help="1-based slide number")],
    shape_id: Annotated[str, typer.Option("--shape-id", help="Exact shape ID from pptx-inspect")],
    output: Annotated[Path, typer.Option("--output", help="New Work-local candidate directory")],
    dx_pt: Annotated[float, typer.Option("--dx-pt", help="Move right (+) or left (-), PowerPoint points")] = 0,
    dy_pt: Annotated[float, typer.Option("--dy-pt", help="Move down (+) or up (-), PowerPoint points")] = 0,
    dwidth_pt: Annotated[float, typer.Option("--dwidth-pt", help="Change width in PowerPoint points")] = 0,
    dheight_pt: Annotated[float, typer.Option("--dheight-pt", help="Change height in PowerPoint points")] = 0,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Nudge one named object using open-source-copy PowerPoint automation on macOS."""
    from .host_nudge import nudge_with_powerpoint
    try:
        emit(nudge_with_powerpoint(source, source_sha256=source_sha256, slide=slide,
                                   shape_id=shape_id, output=output, dx_pt=dx_pt, dy_pt=dy_pt,
                                   dwidth_pt=dwidth_pt, dheight_pt=dheight_pt), as_json)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("pptx-add-text")
def pptx_add_text_command(
    source: Annotated[Path, typer.Argument(help="Current editable PPTX")],
    source_sha256: Annotated[str, typer.Option("--source-sha256", help="Exact hash from pptx-inspect")],
    slide: Annotated[int, typer.Option("--slide", help="1-based slide number")],
    content: Annotated[str, typer.Option("--text", help="Audience-facing one-line text")],
    x: Annotated[float, typer.Option("--x")],
    y: Annotated[float, typer.Option("--y")],
    width: Annotated[float, typer.Option("--width")],
    height: Annotated[float, typer.Option("--height")],
    output: Annotated[Path, typer.Option("--output", help="New Work workspace directory")],
    unit: Annotated[str, typer.Option("--unit", help="mm or inch")]= "mm",
    font_size_pt: Annotated[float | None, typer.Option("--font-size-pt")]=None,
    font_name: Annotated[str | None, typer.Option("--font-name")]=None,
    dry_run: Annotated[bool, typer.Option("--dry-run")]=False,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Add one editable text box to a selected page without rebuilding the deck."""
    from .pptx_edit import patch_pptx
    try:
        edit = {"action": "add_textbox", "slide": slide, "text": content,
                "geometry": {"unit": unit, "x": x, "y": y, "width": width, "height": height}}
        if font_size_pt is not None:
            edit["font_size_pt"] = font_size_pt
        if font_name is not None:
            edit["font_name"] = font_name
        receipt = patch_pptx({"source": str(source), "source_sha256": source_sha256,
                              "edits": [edit], "dry_run": dry_run}, output)
        added = receipt["operations"][0]["after"][0]
        emit({"status": receipt["status"], "source_sha256": receipt["source_sha256"],
              "candidate": receipt["candidate"], "candidate_sha256": receipt["candidate_sha256"],
              "plan_sha256": receipt["plan_sha256"], "changed_parts": receipt["changed_parts"],
              "receipt": str(Path(output) / "receipt.json"), "slide": slide,
              "new_shape_id": added["shape_id"], "native_visual_verified": False}, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("pptx-picture")
def pptx_picture_command(
    source: Annotated[Path, typer.Argument(help="Current editable PPTX")],
    source_sha256: Annotated[str, typer.Option("--source-sha256", help="Exact hash from pptx-inspect")],
    slide: Annotated[int, typer.Option("--slide", help="1-based slide number")],
    shape_id: Annotated[str, typer.Option("--shape-id", help="Exact top-level picture ID")],
    replacement_image: Annotated[Path, typer.Option("--replacement-image", help="Local static raster image")],
    output: Annotated[Path, typer.Option("--output", help="New Work workspace directory")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Replace an unshared raster picture without changing its geometry or crop."""
    from .image_preflight import preflight_image
    from .pptx_edit import patch_pptx
    try:
        image_report = preflight_image(replacement_image)
        if image_report["decode_status"] != "decoded":
            raise ValueError(f"replacement image could not be decoded: {image_report.get('error')}")
        edit = {"action": "replace_picture", "slide": slide, "shape_id": shape_id,
                "replacement_image": str(replacement_image)}
        receipt = patch_pptx({"source": str(source), "source_sha256": source_sha256,
                              "edits": [edit], "dry_run": dry_run}, output)
        emit({"status": receipt["status"], "source_sha256": receipt["source_sha256"],
              "candidate": receipt["candidate"], "candidate_sha256": receipt["candidate_sha256"],
              "plan_sha256": receipt["plan_sha256"], "changed_parts": receipt["changed_parts"],
              "receipt": str(Path(output) / "receipt.json"), "slide": slide, "shape_id": shape_id,
              "image_diagnostics": [entry["code"] for entry in image_report["diagnostics"]],
              "native_visual_verified": False}, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("image-check")
def image_check_command(
    source: Annotated[Path, typer.Argument(help="Local raster image to inspect without changing it")],
    width: Annotated[float | None, typer.Option("--width", help="Planned display width")] = None,
    height: Annotated[float | None, typer.Option("--height", help="Planned display height")] = None,
    unit: Annotated[str, typer.Option("--unit", help="px, in, mm or cm")] = "px",
    output: Annotated[Path | None, typer.Option("--output", help="Optional local QA report path")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run local pixel, EXIF, transparency and resolution diagnostics."""
    from .image_preflight import preflight_image
    try:
        if (width is None) != (height is None):
            raise ValueError("--width and --height must be supplied together")
        report = preflight_image(source, (width, height) if width is not None else None, target_unit=unit)
        if output:
            write_json(output, report)
        emit(report, as_json)
        if report["decode_status"] != "decoded":
            raise typer.Exit(2)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("image-prepare")
def image_prepare_command(
    source: Annotated[Path, typer.Argument(help="Existing local raster image")],
    output: Annotated[Path, typer.Option("--output", help="New image file; parent directory must exist")],
    max_edge: Annotated[int, typer.Option("--max-edge", help="Maximum width or height in pixels; never upscales")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Orient and optionally downscale a local image without editing its source."""
    from .image_preflight import prepare_image
    try:
        emit(prepare_image(source, output, max_edge=max_edge), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("preview-slide")
def preview_slide_command(
    source: Annotated[Path, typer.Argument(help="Existing PPTX candidate")],
    slide: Annotated[int, typer.Option("--slide", help="One 1-based slide number")],
    output: Annotated[Path, typer.Option("--output", help="New Work-local PNG path")],
    width: Annotated[int, typer.Option("--width", help="Preview width in pixels")]=1280,
    backend: Annotated[str, typer.Option("--backend", help="officekit or officecli (local HTML preview)")]= "officekit",
    height: Annotated[int | None, typer.Option("--height", help="OfficeCLI preview height in pixels; default follows PPTX aspect ratio")]=None,
    font_file: Annotated[list[Path] | None, typer.Option("--font-file", help="Local TTF/OTF/TTC for glyph rendering; repeat as needed")]=None,
    system_fonts: Annotated[bool, typer.Option("--system-fonts/--no-system-fonts")]=True,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Render one advisory local PNG without a PDF or full-deck raster pass."""
    from .local_preview import LocalPreviewError, render_slide_preview
    from .local_preview_officecli import OfficeCliPreviewError, render_officecli_slide_preview
    try:
        if backend == "officekit":
            receipt = render_slide_preview(source, slide, output, width=width,
                                           font_files=font_file or (), load_system_fonts=system_fonts)
        elif backend == "officecli":
            if font_file:
                raise ValueError("--font-file is supported only with --backend officekit")
            receipt = render_officecli_slide_preview(source, slide, output, width=width, height=height)
        else:
            raise ValueError("--backend must be officekit or officecli")
        emit({"status": receipt["status"], "source_sha256": receipt["source_sha256"],
              "slide_number": receipt["slide_number"], "slide_count": receipt.get("slide_count"),
              "output_png": receipt["output_png"], "output_sha256": receipt["output_sha256"],
              "receipt": str(Path(output).with_suffix(".preview.json")),
              "renderer": receipt["renderer"], "approximate_renderer": True}, as_json)
    except (OSError, ValueError, KeyError, TypeError, LocalPreviewError, OfficeCliPreviewError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("revision-check")
def revision_check_command(
    receipt: Annotated[Path, typer.Option("--receipt", help="Applied pptx-patch/pptx-text receipt.json")],
    output: Annotated[Path, typer.Option("--output", help="New Work-local scoped QA report path")],
    policy: Annotated[Path | None, typer.Option("--policy")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check only source-bound changed pages using local deterministic QA."""
    from .revision_qa import audit_revision
    try:
        if output.exists() or output.is_symlink():
            raise ValueError("output must be a new QA report path")
        patch_receipt = json.loads(receipt.read_text(encoding="utf-8-sig"))
        if not isinstance(patch_receipt, dict):
            raise ValueError("receipt must contain a JSON object")
        from .authoring_policy import standalone_audit_policy
        rules = standalone_audit_policy(json.loads(policy.read_text(encoding="utf-8-sig"))) if policy else None
        result = audit_revision(patch_receipt.get("source"), patch_receipt.get("candidate"), receipt,
                                policy=rules)
        write_json(output, result)
        emit({"status": result["status"], "scope": result["scope"],
              "source_sha256": result["source_sha256"], "candidate_sha256": result["candidate_sha256"],
              "new_issue_count": len(result["quality"]["new_issues"]),
              "worsened_issue_count": len(result["quality"].get("worsened_issues", [])),
              "persistent_issue_count": len(result["quality"]["persistent_issues"]),
              "cleared_issue_count": len(result["quality"]["cleared_issues"]),
              "visual_review": result["visual_review"], "report": str(output)}, as_json)
        if result["status"] != "passed_scoped":
            raise typer.Exit(1)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("visual-diff")
def visual_diff_command(
    request: Annotated[Path, typer.Option("--request")],
    output: Annotated[Path, typer.Option("--output", help="New directory for diff image and evidence receipt")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Compare hash-bound page PNGs from the same declared render environment."""
    from .visual_diff import compare_visuals
    try:
        emit(compare_visuals(json.loads(request.read_text(encoding="utf-8-sig")), output), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("project-review")
def project_review_command(
    work: Annotated[Path | None, typer.Option("--work-dir")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Queue archive retrospection and advance its persisted review cursor."""
    from .project_learning import request_project_review, run_project_learning
    try:
        if work:
            work = work.expanduser().resolve()
            if work.parent != PROJECT_ROOT.parent / "work" or not work.name.isdigit():
                raise ValueError("project-review requires a numeric Work in this workspace")
            request_project_review(PROJECT_ROOT.parent, work, reason="explicit_project_review")
        emit(run_project_learning(PROJECT_ROOT.parent, only_work_ids=[work.name] if work else None), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("work-storage")
def work_storage_command(
    work_dir: Annotated[Path, typer.Option("--work-dir", help="Existing numeric Work package")],
    output: Annotated[Path | None, typer.Option("--output", help="Optional read-only audit report JSON")]=None,
    top_n: Annotated[int, typer.Option("--top", help="Largest files shown in the report")]=20,
    retention_plan: Annotated[bool, typer.Option("--retention-plan", help="Hash files and trace references for a conservative retention preview; never deletes")]=False,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Explain an existing Work's logical storage without deleting evidence."""
    from .storage_audit import audit_work_storage, plan_work_storage_retention
    try:
        work = work_dir.expanduser().resolve(strict=True)
        if work.parent != PROJECT_ROOT.parent / "work" or not work.name.isdigit():
            raise ValueError("work-storage requires a numeric Work in this workspace")
        report = plan_work_storage_retention(work) if retention_plan else audit_work_storage(work, top_n=top_n)
        if output:
            write_json(output, report)
        emit(report, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("work-qa-dedup")
def work_qa_dedup_command(
    work_dir: Annotated[Path, typer.Option("--work-dir", help="Completed numeric Work package")],
    apply_changes: Annotated[bool, typer.Option("--apply", help="Apply exact-duplicate APFS clones; default is dry-run")]=False,
    only_sha256: Annotated[str | None, typer.Option("--only-sha256", help="Limit the audit or apply to one current exact-duplicate group")]=None,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Audit or APFS-clone exact duplicate regular files under workspace/qa only."""
    from .qa_dedup import deduplicate_completed_work_qa
    try:
        work = work_dir.expanduser().resolve(strict=True)
        if work.parent != PROJECT_ROOT.parent / "work" or not work.name.isdigit():
            raise ValueError("work-qa-dedup requires a numeric Work in this workspace")
        emit(deduplicate_completed_work_qa(work, apply=apply_changes, only_sha256=only_sha256), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("context")
def context_command(
    query: Annotated[str, typer.Option("--query")],
    work: Annotated[Path | None, typer.Option("--work-dir")] = None,
    output: Annotated[Path | None, typer.Option("--output")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Recall scoped lessons, historical cases, portfolio pages and actual tools."""
    from .context_intelligence import recall_context
    from .learning import compile_lessons, write_json as write_context
    try:
        payload = recall_context(query, PROJECT_ROOT.parent,
                                 current_work_id=work.name if work else None,
                                 validated_lessons=compile_lessons(PROJECT_ROOT.parent)["lessons"])
        if output:
            write_context(output, payload)
        emit(payload, as_json)
    except (OSError, ValueError, KeyError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("feedback")
def feedback_command(
    work: Annotated[Path, typer.Option("--work-dir")],
    request: Annotated[Path, typer.Option("--request")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Record a correction, its scope and hash-bound validation evidence."""
    from .learning import record_event
    try:
        emit(record_event(work, json.loads(request.read_text(encoding="utf-8-sig"))), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("packet-import")
def packet_import_command(
    work: Annotated[Path, typer.Option("--work-dir", help="接收外部交接包的现有 Work")],
    packet: Annotated[Path, typer.Option("--packet", help="aippt-handoff-bundle-v1 自包含 JSON 文件")],
    origin: Annotated[str, typer.Option("--origin", help="外部交接来源")]="chatgpt-powerpoint",
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Retain a portable Office handoff as unverified external evidence only."""
    from .portable_packets import import_packet
    try:
        emit(import_packet(PROJECT_ROOT.parent, work, packet, origin=origin), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("guidance-export")
def guidance_export_command(
    output: Annotated[Path, typer.Option("--output", help="用户审阅后可附加到 Office 会话的 JSON 文件")],
    include_learned: Annotated[bool, typer.Option("--include-learned/--base-only")]=True,
    overwrite: Annotated[bool, typer.Option("--overwrite", help="覆盖已存在的显式导出文件")]=False,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Export de-identified active guidance; it never sends or installs the file."""
    from .portable_packets import write_guidance_snapshot
    try:
        emit(write_guidance_snapshot(PROJECT_ROOT.parent, output, include_learned=include_learned,
                                     overwrite=overwrite), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("conversation-sync")
def conversation_sync_command(
    work: Annotated[Path, typer.Option("--work-dir")],
    thread_id: Annotated[str | None, typer.Option("--thread-id")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Bind an explicitly identified task or refresh its existing raw snapshots."""
    from .conversations import archive_conversation, refresh_conversation_tree
    try:
        payload = (archive_conversation(work, thread_id=thread_id,
                                       binding_reason="Explicit conversation-sync --thread-id")
                   if thread_id else refresh_conversation_tree(work))
        emit(payload, as_json)
        if payload.get("status") in {"error", "partial", "retention_policy_invalid"}:
            raise typer.Exit(1)
    except (OSError, ValueError, KeyError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("maintain")
def maintain_command(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Refresh local archives and indexes. Never mark Works completed."""
    from .maintenance import run_maintenance
    payload = run_maintenance(PROJECT_ROOT.parent)
    emit(payload, as_json)
    if payload.get("status") in {"error", "partial"}:
        raise typer.Exit(1)


@app.command("upstreams")
def upstreams_command(
    identifier: Annotated[str | None, typer.Option("--id")] = None,
    check: Annotated[bool, typer.Option("--check", help="Verify source HEAD, origin and clean working tree")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    from .upstreams import upstream_report
    try:
        rows = upstream_report(verify=check, identifier=identifier)
        emit(rows, as_json)
        if check and any(not row["verification"]["matches_lock"] for row in rows
                         if row["acquisition_status"] != "pending_snapshot" and row.get("lifecycle", "active") != "retired"):
            raise typer.Exit(1)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("support")
def support_command(
    kind: Annotated[str, typer.Argument(help="chart (ECharts JSON) or diagram (Mermaid text)")],
    source: Annotated[Path, typer.Argument()],
    output: Annotated[Path, typer.Option("--output", "-o")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    from .support import render_support
    try:
        emit(render_support(kind, source, output), as_json)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("upstream-tools")
def upstream_tools_command(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List real operations, existing routes, and unresolved runtime requirements."""
    from .upstream_tools import tool_catalog
    emit(tool_catalog(), as_json)


@app.command("upstream-call")
def upstream_call_command(
    provider: str,
    operation: str,
    request: Annotated[Path, typer.Option("--request", help="JSON input for this operation")],
    output: Annotated[Path, typer.Option("--output", help="New directory inside active Work workspace")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    from .upstream_tools import invoke
    try:
        payload = json.loads(request.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request must be a JSON object")
        emit(invoke(provider, operation, payload, output), as_json)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("asset-import")
def asset_import_command(
    job: str,
    need: Annotated[str, typer.Option("--need")],
    receipt: Annotated[Path, typer.Option("--receipt")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Bind an upstream-call asset to a slide and invalidate stale review approvals."""
    from .assets.acquisition import import_upstream_asset, AssetAcquisitionError
    try:
        emit(import_upstream_asset(job, need_id=need, receipt_path=receipt), as_json)
    except (OSError, ValueError, KeyError, AssetAcquisitionError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("native-scene")
def native_scene_command(
    job: str,
    request: Annotated[Path, typer.Option("--request")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Select explicit PptxGenJS authoring for a native job; existing gates remain."""
    from .native_scene import set_native_scene
    try:
        emit(set_native_scene(job, json.loads(request.read_text(encoding="utf-8"))), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("design-plan")
def design_plan_command(
    job: str,
    request: Annotated[Path | None, typer.Option("--request", help="Task narrative, whole-deck choice and per-page design decisions")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Read or record assistant design planning; never requests user approval."""
    from .design_plan import author_design_plan
    try:
        emit(author_design_plan(job, json.loads(request.read_text(encoding="utf-8")) if request else None), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


def _echo_utf8(value: object, *, err: bool = False) -> None:
    stream = sys.stderr if err else sys.stdout
    text = f"{value}\n"
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
        return
    stream.write(text)
    stream.flush()


def emit(value: object, as_json: bool = False) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if as_json:
        _echo_utf8(json.dumps(value, ensure_ascii=False, indent=2))
    elif isinstance(value, dict):
        for key, item in value.items():
            if item is not None:
                _echo_utf8(f"{key}: {item}")
    else:
        _echo_utf8(value)


@app.command("new")
def new_job(
    name: Annotated[str, typer.Option("--name", help="任务名称")],
    client: Annotated[str, typer.Option("--client", help="客户名称")] = "",
    job_id: Annotated[str | None, typer.Option("--id", help="固定 job id")] = None,
    route: Annotated[ProductionRoute | None, typer.Option("--route")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        emit(create_job(name, client, job_id, route), as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("work-intake")
def work_intake_command(
    work: Annotated[Path | None, typer.Option("--work-dir", help="Work 目录；省略时使用当前 Work wrapper")]=None,
    job_id: Annotated[str | None, typer.Option("--job", help="复用或创建的 Studio job id")]=None,
    name: Annotated[str | None, typer.Option("--name", help="覆盖 Work 名称")]=None,
    client: Annotated[str | None, typer.Option("--client", help="覆盖 Work 客户名称")]=None,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Bind a Work request and import source/reference files through its wrapper."""
    try:
        current_work = os.environ.get("AIPPT_WORK_DIR", "")
        if work is None and not current_work.strip():
            raise WorkflowError("work-intake needs --work-dir or a Work wrapper")
        resolved = work or Path(current_work)
        emit(initialize_work_intake(resolved, job_id=job_id, name=name, client=client), as_json)
    except (OSError, ValueError, WorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-template")
def library_template_command(
    job: Annotated[str, typer.Argument(help="Work-bound Studio job id")],
    path: Annotated[str, typer.Option("--path", help="Path relative to 模板库/CATALOG.jsonl")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Bind one hash-verified, occasion-compatible catalog PPTX to this Work."""
    try:
        emit(bind_catalog_template(job, path), as_json)
    except (OSError, ValueError, WorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-template-review")
def library_template_review_command(
    job: Annotated[str, typer.Argument(help="Work-bound Studio job id")],
    path: Annotated[str, typer.Option("--path", help="Path relative to 模板库/CATALOG.jsonl")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Prepare candidate/source-page metadata for human visual comparison."""
    try:
        emit(prepare_catalog_template_selection(job, path), as_json)
    except (OSError, ValueError, WorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-template-confirm")
def library_template_confirm_command(
    job: Annotated[str, typer.Argument(help="Work-bound Studio job id")],
    request: Annotated[Path, typer.Option("--request", help="JSON with selected template, preview evidence, and adaptation scope")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Record caller-supplied human template choice and source-page observations."""
    try:
        payload = read_json(request)
        if not isinstance(payload, dict):
            raise ValueError("template confirmation request must be a JSON object")
        emit(confirm_catalog_template(job, payload), as_json)
    except (OSError, ValueError, WorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-index")
def library_index_command(
    limit: Annotated[int | None, typer.Option("--limit", help="Bounded incremental scan for diagnostics")]=None,
    path_prefix: Annotated[str | None, typer.Option("--prefix", help="Only process catalog paths under this library-relative directory")]=None,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Build the page/content/structure/media index for catalogued PPTX decks."""
    from .library_depth import build_depth_index
    library = WORKSPACE_ROOT / "模板库"
    try:
        emit(build_depth_index(library / "CATALOG.jsonl", library / "DEEP-INDEX.jsonl", limit=limit,
                               path_prefix=path_prefix), as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-motion-search")
def library_motion_search_command(
    query: Annotated[str, typer.Option("--query", help="Requested page function and motion behavior")],
    limit: Annotated[int, typer.Option("--limit")]=10,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Find source-hash-checked animated pages, not filename-only claims."""
    from .motion_search import search_motion_library
    library = WORKSPACE_ROOT / "模板库"
    index = library / "镶金边高端动态ppt/分析索引"
    try:
        emit(search_motion_library(index / "MOTION-INDEX.jsonl", index / "MOTION-CATALOG.jsonl",
                                   library / "CATALOG.jsonl", query, limit=limit), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-motion-inspect")
def library_motion_inspect_command(
    path: Annotated[str, typer.Option("--path", help="Source PPTX path relative to 模板库")],
    slide: Annotated[int, typer.Option("--slide", help="1-based presentation slide")],
    output: Annotated[Path, typer.Option("--output", help="New JSON path for full object and timing evidence")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Read one indexed page only after verifying its current source bytes."""
    import hashlib
    from .motion_search import _rows, _safe_source, _sha
    library = WORKSPACE_ROOT / "模板库"
    index = library / "镶金边高端动态ppt/分析索引/MOTION-CATALOG.jsonl"
    try:
        row = next((item for item in _rows(index) if item.get("path") == path), None)
        source = _safe_source(library, path)
        if row is None or source is None or _sha(source) != row.get("source_sha256"):
            raise ValueError("motion source is absent, unindexed, or changed")
        detail = (library / str(row["detail_ref"])).resolve()
        detail.relative_to(library.resolve())
        if not detail.is_file() or _sha(detail) != row.get("detail_sha256"):
            raise ValueError("motion detail is absent or changed")
        packet = read_json(detail)
        pages = packet.get("slides", [])
        if not isinstance(slide, int) or slide < 1 or slide > len(pages):
            raise ValueError("slide is outside source presentation order")
        destination = output.expanduser().absolute()
        if destination.exists() or destination.is_symlink():
            raise ValueError("inspection output already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        selected = {"schema": "aippt.motion-page-inspection.v1", "source_path": path,
                    "source_sha256": row["source_sha256"], "source_slide": slide,
                    "page": pages[slide - 1], "playback_status": "not_verified"}
        destination.write_text(json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        page = pages[slide - 1]
        layers = page.get("inherited_motion") or {}
        emit({"status": "ready", "output": str(destination),
              "output_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
              "object_count_by_source_level": {"slide": len(page.get("objects", [])),
                                               **{level: len((layers.get(level) or {}).get("objects", []))
                                                  for level in ("layout", "master")}},
              "behavior_count_by_source_level": {"slide": len(page.get("behaviors", [])),
                                                 **{level: len((layers.get(level) or {}).get("behaviors", []))
                                                    for level in ("layout", "master")}},
              "playback_status": "not_verified"}, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-motion-copy")
def library_motion_copy_command(
    request: Annotated[Path, typer.Option("--request", help="JSON: source_path, source_sha256, contiguous slides, exact text/image replacements")],
    output: Annotated[Path, typer.Option("--output", help="New editable PPTX outside 模板库")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Copy a source slide sequence while retaining native OOXML animation."""
    from .motion_clone import MotionCloneError, clone_motion_deck
    from .motion_search import _safe_source, _rows
    library = WORKSPACE_ROOT / "模板库"
    index = library / "镶金边高端动态ppt/分析索引/MOTION-CATALOG.jsonl"
    try:
        packet = read_json(request)
        if not isinstance(packet, dict):
            raise ValueError("request must be a JSON object")
        path = packet.get("source_path")
        source = _safe_source(library, path)
        row = next((item for item in _rows(index) if item.get("path") == path), None)
        curated = next((item for item in _rows(library / "CATALOG.jsonl") if item.get("path") == path), None)
        if curated is not None and curated.get("asset_type") != "template":
            raise ValueError(f"source is catalogued as {curated.get('asset_type')}; use a curated template for direct copying")
        if (source is None or row is None or curated is None
                or packet.get("source_sha256") != row.get("source_sha256")
                or curated.get("source_sha256") != row.get("source_sha256")):
            raise ValueError("motion source is absent, unindexed, or stale")
        destination = output.expanduser().resolve(strict=False)
        if destination.is_relative_to(library.resolve()):
            raise ValueError("motion copy output must stay outside the template library")
        result = clone_motion_deck(source, destination, source_sha256=row["source_sha256"],
                                   slides=packet.get("slides"), replacements=packet.get("replacements"),
                                   image_replacements=packet.get("image_replacements"))
        emit({"status": "copied", **result}, as_json)
    except (OSError, ValueError, KeyError, TypeError, MotionCloneError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-style-search")
def library_style_search_command(
    query: Annotated[str, typer.Option("--query", help="Competition occasion, track, audience, page roles and visual intent")],
    collection: Annotated[str, typer.Option("--collection", help="auto, creative, career or gaojie")]= "auto",
    role: Annotated[list[str] | None, typer.Option("--role", help="Required page role; repeat for a sequence")]=None,
    limit: Annotated[int, typer.Option("--limit")]=10,
    layout_examples: Annotated[bool, typer.Option("--layout-examples", help="Search the small page experiment set, including mapped case-page references")]=False,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Search complete contest decks or source pages and learned Gaojie layouts."""
    from .style_search import search_layout_examples, search_style_library
    try:
        search = search_layout_examples if layout_examples else search_style_library
        emit(search(WORKSPACE_ROOT / "模板库", query,
                                  collection=collection, roles=role, limit=limit), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-layout-search")
def library_layout_search_command(
    query: Annotated[str, typer.Option("--query", help="Current page message, content relationship and layout intent")],
    category: Annotated[str | None, typer.Option("--category", help="One of the 31 Gaojie website categories")]=None,
    role: Annotated[list[str] | None, typer.Option("--role")]=None,
    limit: Annotated[int, typer.Option("--limit")]=8,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Retrieve measured source pages together with persistent category learning."""
    from .gaojie_layouts import search_gaojie_layouts
    try:
        emit(search_gaojie_layouts(WORKSPACE_ROOT / "模板库", query, category=category, roles=role, limit=limit), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-style-inspect")
def library_style_inspect_command(
    path: Annotated[str, typer.Option("--path", help="PPTX path relative to 模板库")],
    slide: Annotated[int, typer.Option("--slide", help="1-based presentation slide")],
    output: Annotated[Path, typer.Option("--output", help="New JSON path in the current Work workspace")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Read a full source page and style chain after checking current source/detail hashes."""
    import hashlib
    from .style_search import CREATIVE, CAREER, _rows, _safe_source, _sha
    from .gaojie_layouts import GAOJIE, style_analysis_root
    library = WORKSPACE_ROOT / "模板库"
    prefix = next((family for family in (CREATIVE, CAREER, GAOJIE) if path.startswith(family + "/")), None)
    try:
        if prefix is None:
            raise ValueError("source must belong to an indexed contest or Gaojie collection")
        row = next((item for item in _rows(style_analysis_root(library, prefix) / "STYLE-CATALOG.jsonl")
                    if item.get("path") == path), None)
        source = _safe_source(library, path)
        if row is None or source is None or _sha(source) != row.get("source_sha256"):
            raise ValueError("style source is absent, unindexed, or changed")
        detail = (library / str(row["detail_ref"])).resolve()
        detail.relative_to(library.resolve())
        if not detail.is_file() or _sha(detail) != row.get("detail_sha256"):
            raise ValueError("style detail is absent or changed")
        packet = read_json(detail)
        pages = packet.get("slides", [])
        if not isinstance(slide, int) or slide < 1 or slide > len(pages):
            raise ValueError("slide is outside source presentation order")
        destination = output.expanduser().absolute()
        if destination.exists() or destination.is_symlink():
            raise ValueError("inspection output already exists")
        destination.parent.mkdir(parents=True, exist_ok=True)
        selected = {"schema": "aippt.style-page-inspection.v1", "source_path": path,
                    "source_sha256": row["source_sha256"], "source_slide": slide,
                    "page": pages[slide - 1], "whole_deck_style_profile": row["style_profile"],
                    "visual_status": "not_reviewed"}
        destination.write_text(json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        emit({"status": "ready", "output": str(destination),
              "output_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
              "object_count": len(pages[slide - 1].get("objects", [])),
              "layout_part": (pages[slide - 1].get("style_context") or {}).get("slide_layout_part"),
              "master_part": (pages[slide - 1].get("style_context") or {}).get("slide_master_part"),
              "visual_status": "not_reviewed"}, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-layout-learn")
def library_layout_learn_command(
    request: Annotated[Path, typer.Option("--request", help="Observed real adaptation with exact source/output preview receipts")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Persist a concrete layout reuse lesson after inspecting actual source/output pages."""
    from .gaojie_layouts import record_layout_application
    try:
        emit(record_layout_application(WORKSPACE_ROOT / "模板库", read_json(request)), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-style-copy")
def library_style_copy_command(
    request: Annotated[Path, typer.Option("--request", help="JSON source/slide/object map and actual source-page review")],
    output: Annotated[Path, typer.Option("--output", help="New editable PPTX candidate outside 模板库")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Preserve a reviewed source deck's exact style chain in an editable copy."""
    from .motion_clone import MotionCloneError, clone_motion_deck
    from .style_search import CREATIVE, CAREER, _curation, _occasion_guard, _rows, _safe_source, _sha
    from .gaojie_layouts import GAOJIE, style_analysis_root
    library = WORKSPACE_ROOT / "模板库"
    try:
        packet = read_json(request)
        if not isinstance(packet, dict):
            raise ValueError("request must be a JSON object")
        path = packet.get("source_path")
        prefix = next((family for family in (CREATIVE, CAREER, GAOJIE) if isinstance(path, str) and path.startswith(family + "/")), None)
        if prefix is None:
            raise ValueError("style source must belong to an indexed contest or Gaojie collection")
        row = next((item for item in _rows(style_analysis_root(library, prefix) / "STYLE-CATALOG.jsonl")
                    if item.get("path") == path), None)
        source = _safe_source(library, path)
        if source is None or row is None or row.get("status") != "parsed" or source.suffix.lower() != ".pptx":
            raise ValueError("only indexed, parsed PPTX sources can be copied")
        if prefix == GAOJIE:
            from .gaojie_layouts import COMPONENT_CATEGORIES
            if row.get("category") in COMPONENT_CATEGORIES:
                raise ValueError("Gaojie component galleries need library-layout-build with explicit selected_object_ids")
        meta = _curation(library / CREATIVE, library / CAREER).get(path, {})
        if meta.get("curation_stale") is True or meta.get("source_sha256") != row.get("source_sha256"):
            raise ValueError("style source classification is stale for the indexed source bytes")
        if meta.get("asset_type") not in {"template", "template_with_example_content"}:
            raise ValueError("source is not classified as a reusable template; inspect it as a reference instead")
        if any(term in source.name.casefold() for term in ("填写指南", "修改指南", "使用教程", "操作说明", "备赛手册", "评审规则")):
            raise ValueError("instruction documents cannot be directly copied as a contest template")
        if packet.get("source_sha256") != row["source_sha256"]:
            raise ValueError("source hash in copy request is stale")
        detail = (library / str(row.get("detail_ref") or "")).resolve()
        detail.relative_to(library.resolve())
        if (not detail.is_file() or _sha(detail) != row.get("detail_sha256")):
            raise ValueError("style detail is absent or changed")
        detail_packet = read_json(detail)
        if detail_packet.get("source_sha256") != row["source_sha256"]:
            raise ValueError("style detail does not bind the selected source")
        early_pages = [
            {"title": page.get("title") or "",
             "text_excerpt": " ".join(str(text) for text in page.get("content_sequence", []))}
            for page in (detail_packet.get("slides") or [])[:6]
        ]
        suitable, reason = (True, "source_page_function_requires_actual_review") if prefix == GAOJIE else _occasion_guard("职业规划大赛" if prefix == CAREER else "创新创业竞赛",
                                          "career" if prefix == CAREER else "creative",
                                          row, meta, early_pages)
        if not suitable:
            raise ValueError(f"source occasion is incompatible with its collection: {reason}")
        reviewed = packet.get("reviewed_source_pages")
        slides = packet.get("slides")
        if (not isinstance(slides, list) or not slides or not isinstance(reviewed, list)
                or len(reviewed) < min(2, len(slides))):
            raise ValueError("copy requires a selected slide range and actual source-page observations")
        seen_pages = set()
        for item in reviewed:
            if (not isinstance(item, dict) or type(item.get("slide")) is not int
                    or item["slide"] not in slides or item["slide"] in seen_pages
                    or not isinstance(item.get("finding"), str) or len(item["finding"].strip()) < 8):
                raise ValueError("each reviewed page needs a distinct selected source slide and concrete finding")
            seen_pages.add(item["slide"])
            receipt_path = item.get("preview_receipt")
            if not isinstance(receipt_path, str) or not receipt_path:
                raise ValueError("each reviewed source page needs a preview-slide receipt")
            receipt = read_json(Path(receipt_path))
            if not isinstance(receipt, dict):
                raise ValueError("preview receipt must be a JSON object")
            png = Path(str(receipt.get("output_png") or ""))
            if (not png.is_absolute() or receipt.get("source_sha256") != row["source_sha256"]
                    or receipt.get("slide_number") != item["slide"]
                    or not png.is_file() or _sha(png) != receipt.get("output_sha256")):
                raise ValueError("preview receipt does not bind the current source page and PNG")
        for field in ("selection_note", "source_content_replacement_plan", "rights_handling_note"):
            if not isinstance(packet.get(field), str) or len(packet[field].strip()) < 10:
                raise ValueError(f"copy request needs a specific {field}")
        destination = output.expanduser().resolve(strict=False)
        if destination.is_relative_to(library.resolve()):
            raise ValueError("style copy output must stay outside the template library")
        result = clone_motion_deck(source, destination, source_sha256=row["source_sha256"],
                                   slides=slides, replacements=packet.get("replacements"),
                                   image_replacements=packet.get("image_replacements"))
        emit({"status": "candidate_copied", **result,
              "style_source_path": path, "source_page_review": reviewed,
              "source_content_replacement_pending": True,
              "visual_review_status": "candidate_needs_actual_page_review"}, as_json)
    except (OSError, ValueError, KeyError, TypeError, MotionCloneError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-layout-build")
def library_layout_build_command(
    request: Annotated[Path, typer.Option("--request", help="Reviewed Gaojie page/component selection and complete text mapping")],
    output: Annotated[Path, typer.Option("--output", help="New editable candidate outside 模板库")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Apply a reviewed source page or a selected native component group."""
    from .gaojie_layouts import build_gaojie_layout_candidate
    try:
        emit(build_gaojie_layout_candidate(WORKSPACE_ROOT / "模板库", read_json(request), output), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-style-image-inspect")
def library_style_image_inspect_command(
    path: Annotated[str, typer.Option("--path", help="Indexed PPTX path relative to 模板库")],
    slide: Annotated[int, typer.Option("--slide", help="1-based source page")],
    output: Annotated[Path, typer.Option("--output", help="New JSON inventory of slide/layout/master images")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Locate every image-bearing object before choosing a text/image binding."""
    import hashlib
    from .style_search import CAREER, CREATIVE, _rows, _safe_source, _sha
    from .gaojie_layouts import GAOJIE, style_analysis_root
    from .template_content_replace import TemplateContentReplaceError, inspect_page_images
    library = WORKSPACE_ROOT / "模板库"
    family = next((item for item in (CREATIVE, CAREER, GAOJIE) if path.startswith(item + "/")), None)
    try:
        if family is None:
            raise ValueError("source must belong to an indexed contest or Gaojie collection")
        source = _safe_source(library, path)
        row = next((item for item in _rows(style_analysis_root(library, family) / "STYLE-CATALOG.jsonl")
                    if item.get("path") == path), None)
        if source is None or source.suffix.lower() != ".pptx" or row is None or _sha(source) != row.get("source_sha256"):
            raise ValueError("image source is absent, unindexed or changed")
        destination = output.expanduser().absolute()
        if destination.exists() or destination.is_symlink() or destination.resolve().is_relative_to(library.resolve()):
            raise ValueError("image inventory output must be a new file outside 模板库")
        inventory = inspect_page_images(source, slide)
        if inventory["source_sha256"] != row["source_sha256"]:
            raise ValueError("image inventory source identity changed")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(inventory, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        emit({"status": "ready", "output": str(destination),
              "output_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
              "slide": slide, "image_object_count": len(inventory["objects"]),
              "layers": inventory["coverage"]["layers"]}, as_json)
    except (OSError, ValueError, KeyError, TypeError, TemplateContentReplaceError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-style-page-search")
def library_style_page_search_command(
    request: Annotated[Path, typer.Option("--request", help="JSON with current project, full-template SHA and target page message/role")],
    limit: Annotated[int, typer.Option("--limit", help="Distinct source-page layouts to return")]=5,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Find editable source pages with matching content function and visual hierarchy."""
    from .style_page_match import retrieve_page_layout_candidates
    try:
        packet = read_json(request)
        emit(retrieve_page_layout_candidates(WORKSPACE_ROOT / "模板库", packet, limit=limit), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-hybrid-plan")
def library_hybrid_plan_command(
    request: Annotated[Path, typer.Option("--request", help="JSON project brief, optional primary template and up to two pages to re-layout")],
    output: Annotated[Path, typer.Option("--output", help="New plan JSON in the current Work workspace")],
    limit: Annotated[int, typer.Option("--limit")]=5,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Plan a whole-deck template adaptation and scoped alternative page layouts."""
    import hashlib
    from .style_hybrid_workflow import StyleHybridWorkflowError, plan_hybrid_adaptation
    try:
        if output.exists() or output.is_symlink():
            raise ValueError("hybrid plan output already exists")
        packet = read_json(request)
        result = plan_hybrid_adaptation(WORKSPACE_ROOT / "模板库", packet, limit=limit)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        emit({"status": result["status"], "output": str(output.absolute()),
              "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
              "whole_deck_candidate_count": len(result["whole_deck_candidates"]),
              "pages_to_relayout": result["pages_to_relayout"]}, as_json)
    except (OSError, ValueError, KeyError, TypeError, StyleHybridWorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-hybrid-bind")
def library_hybrid_bind_command(
    job_id: str,
    request: Annotated[Path, typer.Option("--request", help="Reviewed hybrid request, plus dispositions for retained source objects")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Bind template adaptation to an existing job; its normal run builds and reviews it."""
    from .hybrid_job import bind_hybrid_template
    try:
        packet = read_json(request)
        if not isinstance(packet, dict):
            raise ValueError("hybrid binding request must be a JSON object")
        emit(bind_hybrid_template(job_id, packet), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-hybrid-build")
def library_hybrid_build_command(
    request: Annotated[Path, typer.Option("--request", help="JSON with reviewed primary/donor pages, exact source hashes and content edits")],
    output: Annotated[Path, typer.Option("--output", help="New editable candidate PPTX outside the template library")],
    receipt: Annotated[Path | None, typer.Option("--receipt", help="New output receipt JSON; defaults beside PPTX")]=None,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Apply an entire template, optionally graft two layouts, and replace mapped content."""
    import hashlib
    import os
    import tempfile
    from .style_hybrid_workflow import StyleHybridWorkflowError, build_hybrid_candidate
    try:
        destination = output.expanduser().absolute()
        record = receipt.expanduser().absolute() if receipt else destination.with_suffix(".hybrid.json")
        if record.exists() or record.is_symlink() or record == destination:
            raise ValueError("hybrid receipt output must be a new path distinct from the PPTX")
        library = (WORKSPACE_ROOT / "模板库").resolve()
        if record.resolve(strict=False).is_relative_to(library):
            raise ValueError("hybrid receipt must stay outside the template library")
        packet = read_json(request)
        result = build_hybrid_candidate(library, packet, destination)
        try:
            payload = json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n"
            record.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{record.name}.", suffix=".tmp", dir=record.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.link(temp_name, record)
            finally:
                os.unlink(temp_name)
        except Exception:
            # The PPTX was created by this invocation; remove only that exact
            # hash if the companion receipt could not be published.
            if destination.is_file():
                with destination.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest == result["output_sha256"]:
                    destination.unlink()
            raise
        emit({"status": result["status"], "output": result["output"],
              "output_sha256": result["output_sha256"], "slide_count": result["slide_count"],
              "page_override_count": len(result["page_overrides"]),
              "content_replacement_receipt": result["content_replacement_receipt"],
              "receipt": str(record)}, as_json)
    except (OSError, ValueError, KeyError, TypeError, StyleHybridWorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-reuse")
def library_reuse_command(
    job: Annotated[str, typer.Argument(help="Studio job with an approved deck plan")],
    limit: Annotated[int, typer.Option("--limit")]=8,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Match a job's current page messages to actual library decks and pages."""
    from .library_retrieval import retrieve_library_reuse
    from .library_reuse_gate import plan_message_signature
    from .motion_search import motion_requested, search_motion_library
    from .style_search import _collection_intent, search_style_library
    from .presentation_intent import read_bound_work_request
    from .models import DeckPlan
    try:
        root = job_dir(job)
        plan = read_model(root / "planning/deck-plan.json", DeckPlan)
        request, _ = read_bound_work_request(job)
        library = WORKSPACE_ROOT / "模板库"
        result = retrieve_library_reuse(
            library / "CATALOG.jsonl", library / "DEEP-INDEX.jsonl",
            request_text="\n".join((request, plan.title, plan.visual_direction)),
            form=plan.planning_strategy.get("presentation_intent", {}).get("presentation_form"),
            slides=[{"id": slide.id, "title": slide.title, "message": " ".join(slide.points),
                     "purpose": slide.purpose, "page_role": slide.page_role,
                     "composition": slide.layout} for slide in plan.slides], limit=limit,
        )
        if motion_requested(request):
            motion_index = library / "镶金边高端动态ppt/分析索引"
            if not (motion_index / "MOTION-INDEX.jsonl").is_file():
                raise ValueError("dynamic request needs the current motion library index; rebuild it before planning")
            motion_query = " ".join((request, plan.title, plan.visual_direction,
                                     *(slide.title for slide in plan.slides)))
            result["motion_required"] = True
            result["motion_library"] = search_motion_library(
                motion_index / "MOTION-INDEX.jsonl", motion_index / "MOTION-CATALOG.jsonl",
                library / "CATALOG.jsonl", motion_query, limit=limit)
            result["motion_candidate_count"] = result["motion_library"]["candidate_count"]
            if result["motion_candidate_count"] and result["status"] == "no_match":
                result["status"] = "ready"
        else:
            result["motion_required"] = False
            result["motion_candidate_count"] = 0
        style_intent = _collection_intent(request)
        if style_intent:
            style_query = " ".join((request, plan.title, plan.visual_direction,
                                    *(slide.title for slide in plan.slides)))
            style = search_style_library(
                library, style_query, collection=style_intent,
                roles=[str(slide.page_role) for slide in plan.slides if slide.page_role], limit=limit)
            result["competition_style_library"] = style
            result["competition_style_candidate_count"] = style["candidate_count"]
            if style["candidate_count"] and result["status"] == "no_match":
                result["status"] = "ready"
        else:
            result["competition_style_candidate_count"] = 0
        result["plan_message_signature"] = plan_message_signature(plan.slides)
        write_json(root / "planning/library-reuse-candidates.json", result)
        emit({"status": result["status"], "candidate_count": result["candidate_count"],
              "path": str(root / "planning/library-reuse-candidates.json"),
              "candidates": result["candidates"],
              "motion_required": result["motion_required"],
              "motion_candidate_count": result["motion_candidate_count"],
              "motion_candidates": result.get("motion_library", {}).get("candidates", []),
              "competition_style_candidate_count": result["competition_style_candidate_count"],
              "competition_style_candidates": result.get("competition_style_library", {}).get("candidates", []),
              "competition_reference_candidates": result.get("competition_style_library", {}).get("reference_candidates", [])}, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-reuse-decision")
def library_reuse_decision_command(
    job: Annotated[str, typer.Argument(help="Studio job id")],
    request: Annotated[Path, typer.Option("--request", help="JSON with reviewed candidate pages and no-fit reason")],
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Record an inspected no-fit outcome instead of silently ignoring matches."""
    from .library_reuse_gate import record_no_suitable_library_candidate
    try:
        payload = read_json(request)
        if not isinstance(payload, dict):
            raise ValueError("library reuse decision request must be a JSON object")
        emit(record_no_suitable_library_candidate(job, payload), as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-media")
def library_media_command(
    job: Annotated[str, typer.Argument(help="Studio job id")],
    path: Annotated[str, typer.Option("--path", help="PPTX path relative to 模板库")],
    source_slide: Annotated[int, typer.Option("--source-slide", help="Visible source page number")],
    shape_id: Annotated[int, typer.Option("--shape-id", help="Source picture shape id")],
    target_slide: Annotated[str, typer.Option("--target-slide", help="Current deck-plan slide id")],
    purpose: Annotated[str, typer.Option("--purpose", help="Why this media belongs on the target page")],
    selection_note: Annotated[str, typer.Option("--selection-note")]= "",
    rights_handling_note: Annotated[str, typer.Option("--rights-handling-note")]= "",
    task_reuse_authorized: Annotated[bool, typer.Option("--task-reuse-authorized")]=False,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Copy one source-bound library picture or SVG into the current page."""
    from .library_assets import LibraryAssetError, bind_library_media
    try:
        emit(bind_library_media(job, path, source_slide=source_slide, shape_id=shape_id,
                                slide_id=target_slide, purpose=purpose, selection_note=selection_note,
                                rights_handling_note=rights_handling_note,
                                task_reuse_authorized=task_reuse_authorized), as_json)
    except (OSError, ValueError, LibraryAssetError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-name-plan")
def library_name_plan_command(
    limit: Annotated[int | None, typer.Option("--limit")]=None,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Prepare content-led names for opaque PPTX filenames without moving files."""
    from .library_naming import propose_library_renames
    try:
        active = os.environ.get("AIPPT_WORK_DIR")
        if not active:
            raise ValueError("run library-name-plan through an active Work wrapper")
        library = WORKSPACE_ROOT / "模板库"
        plan = propose_library_renames(library / "CATALOG.jsonl", library / "DEEP-INDEX.jsonl", limit=limit)
        destination = Path(active) / "workspace/library-rename-plan.json"
        write_json(destination, plan)
        emit({"status": "planned", "path": str(destination), "proposal_count": len(plan["proposals"]),
              "sample": plan["proposals"][:12], "skipped": plan["skipped"]}, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("library-name-apply")
def library_name_apply_command(
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Apply the current Work's reviewed rename plan with an undo manifest."""
    from .library_naming import apply_library_renames
    try:
        active = os.environ.get("AIPPT_WORK_DIR")
        if not active:
            raise ValueError("run library-name-apply through an active Work wrapper")
        workspace = Path(active) / "workspace"
        plan = read_json(workspace / "library-rename-plan.json")
        from datetime import datetime
        backup = workspace / "library-rename-history" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        manifest = apply_library_renames(plan, backup_dir=backup)
        emit({"status": manifest["status"], "move_count": len(manifest["moves"]),
              "manifest": str(backup / "rename-manifest.json")}, as_json)
    except (OSError, ValueError, KeyError, TypeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("route")
def route_command(
    job: Annotated[str, typer.Argument(help="job id")],
    selected: Annotated[ProductionRoute | None, typer.Option("--set", help="强制使用指定生产路线")] = None,
    reason: Annotated[str, typer.Option("--reason", help="路线覆盖原因")] = "",
    role: Annotated[list[str] | None, typer.Option("--role", help="source-id=content|template|edit_target|visual_reference|flattened_input|asset")] = None,
    accept_image_based: Annotated[bool, typer.Option("--accept-image-based", help="允许把视觉参考路由到 image-based alternate")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        roles: dict[str, InputRole] = {}
        for item in role or []:
            source_id, separator, raw_role = item.partition("=")
            if not separator or not source_id.strip():
                raise WorkflowError(f"invalid --role value: {item}")
            roles[source_id.strip()] = InputRole(raw_role.strip())
        emit(
            configure_route(
                job,
                route=selected,
                reason=reason,
                roles=roles,
                image_based_accepted=accept_image_based,
            ),
            as_json,
        )
    except (OSError, ValueError, WorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("presentation-intent")
def presentation_intent_command(
    job: Annotated[str, typer.Argument(help="job id")],
    mode: Annotated[ChangeMode | None, typer.Option("--mode", help="局部修订、模板重排、新作、重建或视觉替代")]=None,
    form: Annotated[PresentationForm | None, typer.Option("--form", help="正式汇报、竞赛、教学、作品集等表达场景")]=None,
    visual_system: Annotated[VisualSystemMode | None, typer.Option("--visual-system", help="保留、派生或新建视觉系统")]=None,
    reason: Annotated[str, typer.Option("--reason", help="本轮明确判断依据")]= "",
    reset_automatic: Annotated[bool, typer.Option("--reset-automatic", help="撤回手动覆盖并按当前要求重新判断")]=False,
    as_json: Annotated[bool, typer.Option("--json")]=False,
) -> None:
    """Set the editorial task intent without changing the technical route."""
    try:
        emit(
            configure_presentation_intent(
                job,
                change_mode=mode,
                presentation_form=form,
                visual_system=visual_system,
                reason=reason,
                reset_automatic=reset_automatic,
            ),
            as_json,
        )
    except (OSError, ValueError, WorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("ingest")
def ingest(
    job: Annotated[str, typer.Argument(help="job id")],
    sources: Annotated[list[str], typer.Argument(help="文件路径或 URL")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        records = ingest_sources(job, sources)
        emit([record.model_dump(mode="json") for record in records], as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("ingest-text")
def ingest_text(
    job: Annotated[str, typer.Argument(help="job id")],
    text: Annotated[str, typer.Option("--text", help="聊天中收到的客户文字")],
    name: Annotated[str, typer.Option("--name", help="落盘文件名")] = "chat.md",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        records = ingest_sources(job, [], inline_texts=[(name, text)])
        emit([record.model_dump(mode="json") for record in records], as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("inspect-source")
def inspect_source(
    job: str,
    source: Annotated[str | None, typer.Option("--source", help="可选的 PowerPoint source id")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Extract a PowerPoint structure, assets, screenshots, and motion paths."""
    try:
        emit(inspect_job_sources(job, source), as_json)
    except (OSError, ValueError, ExtractionError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("apply-edits")
def apply_edits(
    job: str,
    operations: Annotated[str, typer.Option("--operations", help="JSON file containing structured PPTX edits")],
    source: Annotated[str | None, typer.Option("--source", help="optional PowerPoint source id")] = None,
    input_path: Annotated[str | None, typer.Option("--input", help="optional PPTX input path")] = None,
    output_path: Annotated[str | None, typer.Option("--output", help="job-relative output path")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Apply auditable text, layout, transition, and animation edits to a PPTX."""
    try:
        emit(
            apply_job_edits(
                job,
                operations,
                source_id=source,
                input_path=input_path,
                output_path=output_path,
            ),
            as_json,
        )
    except (OSError, ValueError, EditError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("native-effects")
def native_effects(
    job: str,
    plan: Annotated[str, typer.Option("--plan", help="原生动画、Morph 和动态预览 JSON 合同")],
    input_path: Annotated[str, typer.Option("--input", help="job 内的 PPTX 路径")] = "build/deck.pptx",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Atomically apply native PowerPoint effects and generate review videos."""
    try:
        emit(apply_job_native_plan(job, Path(plan), input_path=input_path), as_json)
    except (OSError, ValueError, NativePowerPointError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("run")
def run(job: str, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        emit(run_job(job), as_json)
    except (OSError, ValueError, WorkflowError, RenderError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("resume")
def resume(job: str, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        emit(resume_job(job), as_json)
    except (OSError, ValueError, WorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("status")
def status(job: str, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    try:
        emit(status_payload(load_job(job)), as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("reconcile")
def reconcile(job: str, as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Audit job state, approvals, manifest hashes, and delivery files without changing them."""
    try:
        emit(audit_job(job), as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("approve")
def approve(
    job: str,
    gate: Annotated[str, typer.Option("--gate", help="brief|prototype|final")],
    note: Annotated[str, typer.Option("--note")] = "",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        emit(approve_gate(job, gate, note), as_json)
    except (OSError, ValueError, WorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("revise")
def revise(
    job: str,
    from_review: Annotated[bool, typer.Option("--from-review", help="把开放批注生成修改批次")] = False,
    complete: Annotated[str | None, typer.Option("--complete", help="标记 Codex 已应用的修改批次")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        if complete:
            emit(complete_revision(job, complete), as_json)
        elif from_review:
            emit(queue_revisions(job), as_json)
        else:
            raise WorkflowError("use --from-review or --complete <batch-id>")
    except (OSError, ValueError, WorkflowError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("export")
def export(
    job: str,
    skip_powerpoint: Annotated[bool, typer.Option("--skip-powerpoint", help="暂缓桌面 PowerPoint QA，仅导出 candidate 并保持 final_review")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    try:
        emit(export_job(job, skip_powerpoint=skip_powerpoint), as_json)
    except (OSError, ValueError, WorkflowError, RenderError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("export-pdf")
def export_pdf_command(
    source: Annotated[Path, typer.Argument(help="Already-finished PPTX")],
    output: Annotated[Path | None, typer.Option("--output", help="PDF destination; defaults to the PPTX basename")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Convert a finished PPTX directly to PDF without job or QA steps."""
    from .mac_quick_pdf import export_pdf
    try:
        emit(export_pdf(source, output or source.with_suffix(".pdf")), as_json)
    except (OSError, ValueError, RuntimeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("export-mp4")
def export_mp4_command(
    source: Annotated[Path, typer.Argument(help="Already-finished PPTX")],
    output: Annotated[Path | None, typer.Option("--output", help="MP4 destination; defaults to the PPTX basename")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Convert a finished PPTX directly to MP4 without job or QA steps."""
    from .mac_quick_mp4 import export_mp4
    try:
        emit(export_mp4(source, output or source.with_suffix(".mp4")), as_json)
    except (OSError, ValueError, RuntimeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("export-png")
def export_png_command(
    source: Annotated[Path, typer.Argument(help="Existing PPTX draft or finished deck")],
    slide: Annotated[int | None, typer.Option("--slide", help="Render one 1-based slide; omit to render all slides")] = None,
    output: Annotated[Path | None, typer.Option("--output", help="Single PNG path or full-deck PNG directory")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Render local PNGs for AI page inspection or an explicit image export."""
    from .mac_quick_png import export_png
    try:
        emit(export_png(source, output, slide), as_json)
    except (OSError, ValueError, RuntimeError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("review")
def review(
    job: str,
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    load_job(job)
    if host not in {"127.0.0.1", "localhost"}:
        raise typer.BadParameter("review server must bind to localhost in v1")
    url = f"http://{host}:{port}/?job={job}"
    _echo_utf8(f"review: {url}")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")


@app.command("doctor")
def doctor(as_json: Annotated[bool, typer.Option("--json")] = False,
           deep: Annotated[bool, typer.Option("--deep", help="Run actual build/render/PowerPoint probes")] = False,
           output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
           skip_powerpoint: Annotated[bool, typer.Option("--skip-powerpoint")] = False) -> None:
    if deep:
        import os
        from .platform_runtime import runtime_doctor
        if output_dir is None:
            jobs = os.environ.get("AIPPT_JOBS_DIR")
            if not jobs:
                raise typer.BadParameter("Deep doctor requires a Work wrapper or an explicit --output-dir")
            output_dir = Path(jobs).parent / "qa/runtime-doctor"
        report = runtime_doctor(output_dir, include_powerpoint=not skip_powerpoint)
        emit(report, as_json)
        if not report.get("core_ready") or (not skip_powerpoint and not report.get("export_ready")):
            raise typer.Exit(1)
        return
    report = capability_report()
    emit(report, as_json)
    missing_required = [item for item in report if item.get("required_for") in {"all", "native"} and not item["available"]]
    if missing_required:
        raise typer.Exit(1)


@app.command("runtime-select")
def runtime_select_command(work_dir: Annotated[Path, typer.Option("--work-dir")],
                           as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Refresh host-native capabilities without copying historical dependencies."""
    from .platform_runtime import select_artifact_runtime
    if not (work_dir / "work.json").is_file():
        raise typer.BadParameter("--work-dir must identify an existing Work package")
    record = read_json(work_dir / "work.json")
    minimal = isinstance(record, dict) and record.get("storage_profile") == "minimal"
    context_path = work_dir / "workspace/assistant-context.json"
    context = None
    if not minimal and context_path.is_file():
        try:
            context = read_json(context_path)
            if not isinstance(context, dict):
                raise ValueError("context must be an object")
        except (OSError, ValueError) as error:
            raise typer.BadParameter("assistant-context.json is invalid; rerun Work initialization before refreshing runtime") from error
    result = select_artifact_runtime(work_dir / ("workspace/runtime-selection" if minimal else "workspace/qa/runtime-selection"))
    write_json(work_dir / "workspace/runtime-environment.json", result)
    if context is not None:
        context["runtime_capabilities"] = result
        write_json(context_path, context)
    emit(result, as_json)


@app.command("assets")
def assets_command(as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Report licensed icon and font providers that Studio may use automatically."""
    try:
        emit(load_asset_registry(PROJECT_ROOT / "upstreams.lock.json").report(), as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("asset-need")
def asset_need_command(
    job: Annotated[str, typer.Argument(help="job id")],
    slide: Annotated[str, typer.Option("--slide", help="目标 slide id")],
    kind: Annotated[str, typer.Option("--kind", help="icon|image|illustration|logo|map|texture")],
    query: Annotated[str, typer.Option("--query", help="联网检索关键词")],
    purpose: Annotated[str, typer.Option("--purpose", help="素材在页面中的用途")],
    optional: Annotated[bool, typer.Option("--optional", help="标记为非阻塞素材需求")] = False,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Record a slide-level asset gap before web discovery or download."""
    try:
        emit(
            add_asset_need(
                job,
                slide_id=slide,
                kind=kind,  # type: ignore[arg-type]
                query=query,
                purpose=purpose,
                required=not optional,
            ),
            as_json,
        )
    except (OSError, ValueError, AssetAcquisitionError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("asset-fetch")
def asset_fetch_command(
    job: Annotated[str, typer.Argument(help="job id")],
    need: Annotated[str, typer.Option("--need", help="asset need id")],
    download_url: Annotated[str, typer.Option("--download-url", help="直接素材 URL")],
    source_url: Annotated[str, typer.Option("--source-url", help="来源或授权说明页面 URL")],
    license_id: Annotated[str, typer.Option("--license", help="许可证或明确授权标识")],
    attribution: Annotated[str, typer.Option("--attribution", help="署名要求")] = "",
    selection_note: Annotated[str, typer.Option("--selection-note", help="选择该素材的原因")] = "",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Download an approved web asset into the active Work and record provenance."""
    try:
        emit(
            acquire_asset(
                job,
                need_id=need,
                download_url=download_url,
                source_url=source_url,
                license_id=license_id,
                attribution=attribution,
                selection_note=selection_note,
            ),
            as_json,
        )
    except (OSError, ValueError, AssetAcquisitionError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("asset-search-commons")
def asset_search_commons_command(
    job: Annotated[str, typer.Argument(help="job id")],
    need: Annotated[str, typer.Option("--need", help="asset need id")],
    limit: Annotated[int, typer.Option("--limit", min=1, max=20, help="maximum per-file candidates")] = 8,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Search Commons and save a content-bound candidate ticket in this Work."""
    from .assets.commons import search_commons_assets

    try:
        emit(search_commons_assets(job, need_id=need, limit=limit), as_json)
    except (OSError, ValueError, AssetAcquisitionError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("asset-pick-commons")
def asset_pick_commons_command(
    job: Annotated[str, typer.Argument(help="job id")],
    need: Annotated[str, typer.Option("--need", help="asset need id")],
    search_id: Annotated[str, typer.Option("--search-id", help="asset-search-commons result search_id")],
    candidate: Annotated[int, typer.Option("--candidate", min=1, help="1-based candidate number in that search")],
    license_review_note: Annotated[str, typer.Option("--license-review-note", help="what the current Commons file page says about usage and attribution")],
    selection_note: Annotated[str, typer.Option("--selection-note", help="why this image serves the slide")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Fetch one reviewed Commons result without copying URLs and license fields."""
    from .assets.commons import pick_commons_asset

    try:
        emit(pick_commons_asset(job, need_id=need, search_id=search_id,
                                candidate_number=candidate, license_review_note=license_review_note,
                                selection_note=selection_note), as_json)
    except (OSError, ValueError, AssetAcquisitionError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("asset-skip")
def asset_skip_command(
    job: Annotated[str, typer.Argument(help="job id")],
    need: Annotated[str, typer.Option("--need", help="asset need id")],
    reason: Annotated[str, typer.Option("--reason", help="不再需要该素材的原因")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Resolve an asset need without downloading when the design no longer requires it."""
    try:
        emit(skip_asset_need(job, need_id=need, reason=reason), as_json)
    except (OSError, ValueError, AssetAcquisitionError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("asset-status")
def asset_status_command(
    job: Annotated[str, typer.Argument(help="job id")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Show pending asset gaps and acquired Work-local assets."""
    try:
        emit(asset_status(job), as_json)
    except (OSError, ValueError, AssetAcquisitionError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("strategy")
def strategy_command(
    job: Annotated[str, typer.Argument(help="job id")],
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Explain the planning strategy selected for the approved brief."""
    try:
        emit(planning_decision(read_model(job_dir(job) / "planning" / "brief.json", JobBrief)), as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("knowledge-recall")
def knowledge_recall_command(
    query: Annotated[str, typer.Option("--query", help="当前任务名称和要求")],
    knowledge_root: Annotated[str, typer.Option("--knowledge-root", help="任务知识库根目录")],
    output: Annotated[str | None, typer.Option("--output", help="可选的 knowledge-context.json 输出路径")] = None,
    limit: Annotated[int, typer.Option("--limit", min=1, max=5)] = 3,
    current_work: Annotated[str | None, typer.Option("--current-work", help="从召回中排除的当前 Work id")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Recall evidence-qualified lessons and navigation-only historical cases."""
    try:
        payload = recall_task_knowledge(
            query,
            Path(knowledge_root),
            limit=limit,
            current_work_id=current_work,
        )
        if output:
            write_json(Path(output).expanduser().resolve(), payload)
        emit(payload, as_json)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


@app.command("probe-powerpoint")
def probe_powerpoint_command(
    baseline: Annotated[str, typer.Option("--baseline", help="本机能够正常打开的基准 PPTX")],
    candidate: Annotated[str, typer.Option("--candidate", help="需要验证的生成稿 PPTX")],
    output: Annotated[str | None, typer.Option("--output", help="保留回存稿和逐页 PNG 的目录")] = None,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Classify Office COM, candidate compatibility, and export failures."""

    retained = output is not None
    try:
        if retained:
            report = probe_powerpoint(Path(baseline), Path(candidate), Path(output))
        else:
            with tempfile.TemporaryDirectory(prefix="aippt-powerpoint-probe-") as temporary:
                report = probe_powerpoint(Path(baseline), Path(candidate), Path(temporary))
        payload = dict(report)
        payload["artifacts_retained"] = retained
        if retained:
            report_path = Path(output).resolve() / "probe-report.json"
            payload["report_path"] = str(report_path)
            write_json(report_path, payload)
        emit(payload, as_json)
    except NativePowerPointError as error:
        payload = dict(error.report)
        if not payload:
            payload = {
                "status": "failed",
                "failed_stage": error.stage,
                "error": {"message": str(error)},
            }
        payload["artifacts_retained"] = retained
        if retained:
            report_path = Path(output).resolve() / "probe-report.json"
            payload["report_path"] = str(report_path)
            write_json(report_path, payload)
        emit(payload, as_json)
        if not as_json:
            _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(1)
    except (OSError, ValueError) as error:
        _echo_utf8(f"ERROR: {error}", err=True)
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
