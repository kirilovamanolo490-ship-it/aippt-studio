from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from pathlib import PurePosixPath
import re
from xml.etree import ElementTree

from adapter_common import artifact, blocked, load_request, parse_contract_args, sha256, source_records, write_result
from aippt_studio.platform_runtime import probe_node_runtime
from aippt_studio.template_verification import (
    _template_application_inputs,
    _validate_mapping,
    _verify_template_application,
)


ENGINE = "pptx-automizer"
# ``VERSION`` is the actual upstream npm engine version.  The Studio bridge has
# its own revision so manifests never mislabel a bridge edit as an upstream
# package upgrade.
VERSION = "0.8.2"
BRIDGE_VERSION = "0.9.1"
# Automatic template maps are Studio-authored working files.  Mark the
# audience-copy behavior so a valid pre-policy map can be safely refreshed
# without touching explicit, source-authoritative mappings.
GENERATED_CONTENT_POLICY = "title_body_deduplicated_v1"


def _file_sha256(path: Path) -> str:
    return sha256(path)


def _runtime_provenance() -> tuple[dict[str, object] | None, str | None]:
    studio = Path(__file__).resolve().parents[2]
    package = studio / "node_modules" / "pptx-automizer" / "package.json"
    lock = studio / "package-lock.json"
    runner = studio / "node" / "template_adapter.mjs"
    try:
        metadata = json.loads(package.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return None, f"pptx-automizer runtime metadata is unavailable: {error}"
    if metadata.get("version") != VERSION:
        return None, f"pptx-automizer runtime version is {metadata.get('version')!r}, expected {VERSION}"
    if not lock.is_file() or not runner.is_file():
        return None, "Studio package lock or template runner is unavailable"
    return {
        "schema_version": 1,
        "execution_kind": "managed_package_runtime",
        "runtime": {
            "package": "pptx-automizer",
            "version": VERSION,
            "lock_file": "package-lock.json",
            "lock_sha256": _file_sha256(lock),
        },
        "source_checkout_role": "reference and reviewed-source provenance; the npm runtime is the executed engine",
        "bridge": {
            "version": BRIDGE_VERSION,
            "python_adapter": "scripts/adapters/template_adapter.py",
            "python_adapter_sha256": _file_sha256(Path(__file__)),
            "node_runner": "node/template_adapter.mjs",
            "node_runner_sha256": _file_sha256(runner),
        },
        "claim_boundary": "A successful result proves the locked npm runtime assembled this source-bound template map; it does not approve visual expression or customer delivery.",
    }, None


def _inspect_archive(source: Path) -> tuple[int, bool]:
    total_uncompressed = 0
    slide_count = 0
    has_timing = False
    with zipfile.ZipFile(source) as archive:
        for info in archive.infolist():
            normalized = info.filename.replace("\\", "/")
            path = PurePosixPath(normalized)
            unix_mode = (info.external_attr >> 16) & 0o170000
            if path.is_absolute() or ".." in path.parts or ":" in path.parts[0] or unix_mode == 0o120000:
                raise ValueError(f"unsafe ZIP entry: {info.filename}")
            total_uncompressed += info.file_size
            if total_uncompressed > 2 * 1024 * 1024 * 1024:
                raise ValueError("template expands beyond the 2 GiB safety limit")
            if normalized.startswith("ppt/slides/slide") and normalized.endswith(".xml"):
                slide_count += 1
                root = ElementTree.fromstring(archive.read(info))
                has_timing = has_timing or any(node.tag.endswith("}timing") for node in root.iter())
    if slide_count == 0:
        raise ValueError("template contains no slides")
    return slide_count, has_timing


def _template_candidate(job_root: Path, request: dict[str, object]) -> tuple[dict[str, object], Path] | tuple[None, dict[str, object]]:
    """Return one verified template source, never an arbitrary first source."""

    candidates = [item for item in source_records(job_root, request) if item[2] in {"template", "edit_target"}]
    if not candidates:
        return None, blocked(
            ENGINE,
            VERSION,
            "input_role",
            "template route requires exactly one PowerPoint source marked template or edit_target",
            "set_input_role",
        )
    if len(candidates) != 1:
        return None, blocked(
            ENGINE,
            VERSION,
            "template_source_ambiguous",
            "multiple template/edit_target sources were supplied; declare one source id instead of choosing the first file",
            "set_input_role",
            candidates=[record.get("id") for record, _, _ in candidates],
        )
    record, source, _ = candidates[0]
    if record.get("kind") != "powerpoint" or source.suffix.lower() != ".pptx":
        return None, blocked(
            ENGINE,
            VERSION,
            "template_source_type",
            "template/edit_target must be a PowerPoint .pptx source",
            "set_input_role",
            source_id=record.get("id"),
        )
    expected_hash = str(record.get("sha256") or "")
    actual_hash = sha256(source)
    if not expected_hash or expected_hash != actual_hash:
        return None, blocked(
            ENGINE,
            VERSION,
            "template_source_hash",
            "template source bytes do not match the normalized source record",
            "reingest_source",
            source_id=record.get("id"),
        )
    return record, source


def _same_visible_message(title: object, point: object) -> bool:
    """Return whether an automatic body point only repeats the title.

    The deck plan intentionally retains the original point for provenance and
    speaker notes.  A generated template map has no reason to put that exact
    statement into both ``{{title}}`` and ``{{body}}``: it produces the
    artificial headline-plus-echo composition the shared HTML renderer avoids.
    """

    def normalize(value: object) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n。！？!?；;，,：:")

    normalized_title, normalized_point = normalize(title), normalize(point)
    return bool(normalized_title and normalized_point and normalized_title == normalized_point)


def _generated_body_points(slide: dict[str, object]) -> list[str]:
    """Keep source points, except the exact title echo in automatic maps."""

    points = [str(point) for point in list(slide.get("points") or [])]
    if points and _same_visible_message(slide.get("title"), points[0]):
        return points[1:]
    return points


def _generated_mapping(
    record: dict[str, object],
    source_hash: str,
    slide_count: int,
    plan: dict[str, object],
    plan_sha256: str,
) -> dict[str, object] | None:
    slides = list(plan.get("slides") or [])
    # Reusing the last layout for an arbitrary number of pages used to hide a
    # decisive design choice behind ``min(index, slide_count)``.  A generated
    # map is allowed only for the one-to-one case; deliberate reuse belongs in
    # an explicit, source-bound mapping reviewed with the template.
    if len(slides) > slide_count:
        return None
    return {
        "schema_version": 2,
        "template_source_id": record["id"],
        "template_source_sha256": source_hash,
        "template_source_slide_count": slide_count,
        "deck_plan_sha256": plan_sha256,
        "generated": True,
        "generated_content_policy": GENERATED_CONTENT_POLICY,
        "allow_animations": False,
        "slides": [
            {
                "deck_slide_id": slide["id"],
                "source_slide": index,
                "replacements": {
                    "title": slide["title"],
                    # Page purpose is planning metadata, not presenter copy.
                    # Only an explicit audience-facing subtitle may fill it.
                    "subtitle": slide.get("subtitle", ""),
                    "body": "\n".join(_generated_body_points(slide)),
                },
            }
            for index, slide in enumerate(slides, start=1)
        ],
    }


def main() -> int:
    args = parse_contract_args()
    request, job_root = load_request(args.request)
    selected = _template_candidate(job_root, request)
    if selected[0] is None:
        write_result(args.result, selected[1])
        return 0
    record, template = selected
    try:
        slide_count, has_timing = _inspect_archive(template)
    except (ValueError, zipfile.BadZipFile) as error:
        write_result(args.result, blocked(ENGINE, VERSION, "unsafe_template_archive", str(error), "reject_source"))
        return 0
    mapping_path = job_root / "planning" / "template-map.json"
    plan_path = job_root / request["deck_plan"]
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan_sha256 = sha256(plan_path)
    if mapping_path.is_file():
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        # Preserve an explicit map exactly as reviewed.  A prior automatic map
        # with the same source and plan may be regenerated to remove the old
        # title/body echo; a stale map remains blocked rather than being silently
        # rebound to changed source or plan bytes.
        if mapping.get("generated") is True and mapping.get("generated_content_policy") != GENERATED_CONTENT_POLICY:
            prior_mapping_error = _validate_mapping(
                mapping,
                record=record,
                source_hash=sha256(template),
                slide_count=slide_count,
                plan=plan,
                plan_sha256=plan_sha256,
            )
            if prior_mapping_error is None:
                refreshed_mapping = _generated_mapping(record, sha256(template), slide_count, plan, plan_sha256)
                if refreshed_mapping is not None:
                    mapping = refreshed_mapping
                    mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        mapping = _generated_mapping(record, sha256(template), slide_count, plan, plan_sha256)
        if mapping is None:
            write_result(
                args.result,
                blocked(
                    ENGINE,
                    VERSION,
                    "template_map_required",
                    "deck has more pages than the template and no source-bound page map declares intentional layout reuse",
                    "review_template_map",
                    template_source_id=record.get("id"),
                    template_slide_count=slide_count,
                    deck_slide_count=len(plan.get("slides") or []),
                ),
            )
            return 0
        mapping_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    mapping_error = _validate_mapping(
        mapping,
        record=record,
        source_hash=sha256(template),
        slide_count=slide_count,
        plan=plan,
        plan_sha256=plan_sha256,
    )
    if mapping_error:
        write_result(
            args.result,
            blocked(
                ENGINE,
                VERSION,
                "template_map_binding",
                mapping_error,
                "review_template_map",
                template_source_id=record.get("id"),
            ),
        )
        return 0
    strict_application = request.get("template_application_required") is True
    application_proof: list[dict] = []
    if strict_application:
        application_proof, application_error = _template_application_inputs(template, mapping, plan)
        if application_error:
            write_result(args.result, blocked(
                ENGINE, VERSION, "template_application_noop", application_error,
                "prepare_editable_template", mapping="planning/template-map.json",
            ))
            return 0
    if has_timing and not mapping.get("allow_animations"):
        write_result(args.result, blocked(ENGINE, VERSION, "unsupported_animation", "template contains animation timing; review and set allow_animations only after a PowerPoint acceptance check", "manual_upstream_adapter", mapping="planning/template-map.json"))
        return 0
    output_root = job_root / request["output_root"]
    if output_root.is_dir():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True)
    output = output_root / "deck.pptx"
    node_runtime = probe_node_runtime()
    node = Path(str(node_runtime["path"])) if node_runtime.get("status") == "passed" else None
    runner = Path(__file__).resolve().parents[2] / "node" / "template_adapter.mjs"
    if not node or not runner.is_file():
        reason = str(node_runtime.get("reason") or node_runtime.get("probe") or "no compatible executable was found")
        write_result(args.result, blocked(ENGINE, VERSION, "runtime", "A compatible host-native Node.js 22.12+ runtime or the Studio template runner is unavailable: " + reason, "manual_upstream_adapter"))
        return 0
    provenance, runtime_error = _runtime_provenance()
    if runtime_error:
        write_result(args.result, blocked(ENGINE, VERSION, "runtime_identity", runtime_error, "repair_managed_runtime"))
        return 0
    completed = subprocess.run([str(node), str(runner), str(template), str(mapping_path), str(output)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, check=False)
    if completed.returncode != 0 or not output.is_file():
        write_result(args.result, blocked(ENGINE, VERSION, "engine", (completed.stderr or completed.stdout or "pptx-automizer failed")[:1000], "manual_upstream_adapter"))
        return 0
    if strict_application:
        application_error = _verify_template_application(template, output, mapping, application_proof)
        if application_error:
            output.unlink(missing_ok=True)
            write_result(args.result, blocked(
                ENGINE, VERSION, "template_application_unverified", application_error,
                "review_template_output", mapping="planning/template-map.json",
            ))
            return 0
    write_result(
        args.result,
        {
            "schema_version": 1,
            "status": "succeeded",
            "receipt": {"engine": ENGINE, "version": VERSION, "command": ["node", "template_adapter.mjs"], "provenance": provenance},
            "editability": "native_editable",
            "artifacts": [artifact(job_root, "pptx", output), artifact(job_root, "build_source", mapping_path)],
            "providers": [],
            "warnings": ["Template content replacement uses {{title}}, {{subtitle}}, and {{body}} tags when present."],
            "metadata": {
                "template": template.name,
                "template_source_id": record.get("id"),
                "template_source_sha256": sha256(template),
                "slides": len(mapping.get("slides", [])),
                "template_application": {
                    "status": "verified" if strict_application else "not_required",
                    "verified_slide_count": len(application_proof),
                    "source_sha256": sha256(template),
                    "page_map_sha256": sha256(mapping_path),
                    "pages": application_proof,
                },
            },
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
