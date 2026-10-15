"""Source-bound human confirmation records for catalog template selection.

This module validates evidence supplied by the trusted CLI caller. A successful
write means only that a confirmation record was supplied; it cannot prove who
looked at previews or whether quoted human text is authentic.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

from PIL import Image

from .storage import read_json, write_json

_RECEIPT = Path("planning/template-selection-confirmation.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _slide_count(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "ppt/presentation.xml" not in names:
            raise ValueError("catalog template is not a valid presentation")
        from .template_verification import _presentation_slide_names
        return len(_presentation_slide_names(archive))


def _work_request_hash(text: str) -> str:
    return _text_hash(text)


def _candidate_binding(
    *, root: Path, relative_path: str, source_sha256: str, request_text: str,
) -> tuple[Path, str]:
    """Resolve the current candidate packet for voluntary or explicit selection."""
    from .presentation_intent import requests_bounded_revision, requests_template_adaptation

    explicit = requests_template_adaptation(request_text) and not requests_bounded_revision(request_text)
    if explicit:
        path = root / "planning/template-selection-candidates.json"
        brief_path = root / "planning/brief.json"
        if not path.is_file() or not brief_path.is_file():
            raise ValueError("explicit template requests need current brief-stage template candidates")
        packet = read_json(path)
        if not isinstance(packet, dict) or packet.get("status") != "candidates_require_page_review":
            raise ValueError("brief-stage template candidates are missing or stale")
        if packet.get("brief_sha256") != _sha256(brief_path):
            raise ValueError("brief-stage template candidates do not match the current brief")
        if packet.get("work_request_sha256") != _work_request_hash(request_text):
            raise ValueError("brief-stage template candidates do not match the current Work request")
        if not any(isinstance(item, dict) and item.get("path") == relative_path
                   for item in packet.get("candidates", [])):
            raise ValueError("selected template is not among the current brief-stage candidates")
        return path, "brief_template_selection"

    path = root / "planning/library-reuse-candidates.json"
    plan_path = root / "planning/deck-plan.json"
    if not path.is_file() or not plan_path.is_file():
        raise ValueError("current page-level template candidates and deck plan are required")
    packet = read_json(path)
    from .library_reuse_gate import plan_message_signature
    from .models import DeckPlan
    from .storage import read_model
    plan = read_model(plan_path, DeckPlan)
    if (not isinstance(packet, dict)
            or packet.get("plan_message_signature") != plan_message_signature(plan.slides)):
        raise ValueError("template confirmation requires current page-level candidates for the current plan")
    if not any(isinstance(candidate, dict) and candidate.get("path") == relative_path
               and candidate.get("source_sha256") == source_sha256
               for candidate in packet.get("candidates", [])):
        raise ValueError("selected template is not among the current source-bound page candidates")
    return path, "page_level_library_reuse"


def _validate_rendered_source_preview(
    *, work: Path, item: dict[str, Any], source_sha256: str, source_slide: int,
) -> dict[str, Any]:
    """Verify the established preview-slide receipt and its Work-local PNG."""
    receipt_path_raw = item.get("render_receipt_path")
    if not isinstance(receipt_path_raw, str) or not receipt_path_raw.strip():
        raise ValueError("each source-page preview requires a preview-slide .preview.json receipt")
    receipt_path = Path(receipt_path_raw).resolve()
    work_root = work.resolve()
    if work_root not in receipt_path.parents or not receipt_path.is_file():
        raise ValueError("preview render receipt must be an existing file inside this Work")
    receipt_sha256 = _sha256(receipt_path)
    if item.get("render_receipt_sha256") != receipt_sha256:
        raise ValueError("preview render receipt SHA-256 does not match its current bytes")
    data = read_json(receipt_path)
    if not isinstance(data, dict) or (
        data.get("status") != "rendered_advisory"
        or data.get("source_sha256") != source_sha256
        or data.get("slide_number") != source_slide
        or data.get("approximate_renderer") is not True
        or not isinstance(data.get("limitations"), list) or not data["limitations"]
    ):
        raise ValueError("preview render receipt does not bind an advisory render of this source slide")
    output_raw = data.get("output_png")
    if not isinstance(output_raw, str) or not output_raw.strip():
        raise ValueError("preview render receipt has no output_png")
    output_path = Path(output_raw).resolve()
    if work_root not in output_path.parents or not output_path.is_file():
        raise ValueError("rendered source-page PNG must be an existing file inside this Work")
    output_sha256 = _sha256(output_path)
    if data.get("output_sha256") != output_sha256:
        raise ValueError("preview render receipt output SHA-256 does not match its PNG")
    image_path = Path(str(item.get("work_local_path") or "")).resolve()
    if image_path != output_path or item.get("sha256") != output_sha256:
        raise ValueError("source preview image must be the exact PNG bound by its render receipt")
    return {"render_receipt_path": str(receipt_path), "render_receipt_sha256": receipt_sha256,
            "approximate_renderer": True, "limitations": data["limitations"]}


def prepare_template_selection(
    *, library_root: Path, catalog_path: Path, relative_path: str,
) -> dict[str, Any]:
    """Return a review packet contract; this is metadata, not visual review."""
    from .template_library import cataloged_template_by_path

    row = cataloged_template_by_path(catalog_path, relative_path)
    if row is None:
        raise ValueError("selected library PPTX is not a current hash-verified template catalog entry")
    source = (library_root / row["path"]).resolve()
    if not source.is_file() or _sha256(source) != row["source_sha256"]:
        raise ValueError("catalog template bytes are missing or changed")
    count = _slide_count(source)
    return {
        "schema": "aippt.template-selection-review.v1",
        "status": "previews_and_human_choice_required",
        "selected_catalog_path": row["path"],
        "source_sha256": row["source_sha256"],
        "source_slide_count": count,
        "minimum_distinct_source_previews": 1 if count == 1 else 2,
        "preview_contract": {
            "source_slide": "1-based visible source page number",
            "work_local_path": "absolute image path inside this Work workspace",
            "sha256": "SHA-256 of the preview image",
            "render_receipt_path": "Work-local preview-slide .preview.json path",
            "render_receipt_sha256": "SHA-256 of that receipt",
            "observation": "specific visual observation from inspecting this actual page",
        },
        "confirmation_contract": {
            "user_confirmation_text": "non-empty current human selection quote; original request may be used when it names this exact template",
            "adaptation_scope": {
                "preserve_composition_theme": "what composition and competition theme language will carry forward",
                "text_image_replacement": "which old text/images will be replaced with current project material",
                "allowed_layout_adjustments": "allowed extent of geometry/layout changes",
            },
        },
        "disclaimer": "This packet does not show or attest to visual inspection. Present actual page images and record the human choice before confirmation.",
    }


def record_template_confirmation(
    *, job_id: str, root: Path, work: Path, request_text: str, catalog_path: Path,
    library_root: Path, payload: dict[str, Any],
) -> dict[str, Any]:
    """Validate and save the caller's page evidence and selected adaptation scope."""
    packet = prepare_template_selection(
        library_root=library_root, catalog_path=catalog_path,
        relative_path=str(payload.get("selected_catalog_path") or ""),
    )
    if payload.get("source_sha256") != packet["source_sha256"]:
        raise ValueError("selected template source SHA-256 does not match the current catalog")
    previews = payload.get("source_previews")
    minimum = packet["minimum_distinct_source_previews"]
    if not isinstance(previews, list) or len(previews) < minimum:
        raise ValueError(f"at least {minimum} actual source-page preview(s) are required")
    seen: set[int] = set()
    bound_previews = []
    for item in previews:
        if not isinstance(item, dict):
            raise ValueError("each source preview must be an object")
        page = item.get("source_slide")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page > packet["source_slide_count"]:
            raise ValueError("source preview page is outside the selected template")
        if page in seen:
            raise ValueError("source previews must use distinct source pages")
        seen.add(page)
        raw_path = item.get("work_local_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ValueError("each preview needs its Work-local image path")
        preview = Path(raw_path).resolve()
        workspace = (work / "workspace").resolve()
        if workspace not in preview.parents or not preview.is_file():
            raise ValueError("source preview must be an existing image inside this Work workspace")
        digest = _sha256(preview)
        if item.get("sha256") != digest:
            raise ValueError("source preview SHA-256 does not match its current bytes")
        try:
            with Image.open(preview) as image:
                image.verify()
        except Exception as error:
            raise ValueError("source preview is not a readable image") from error
        observation = item.get("observation")
        if not isinstance(observation, str) or len(observation.strip()) < 8:
            raise ValueError("each preview requires a concrete visual observation")
        render_receipt = _validate_rendered_source_preview(
            work=work, item=item, source_sha256=packet["source_sha256"], source_slide=page,
        )
        bound_previews.append({"source_slide": page, "work_local_path": str(preview),
                               "sha256": digest, "observation": observation.strip(), **render_receipt})
    confirmation = payload.get("user_confirmation_text")
    if not isinstance(confirmation, str) or not confirmation.strip():
        raise ValueError("non-empty human confirmation text is required")
    scope = payload.get("adaptation_scope")
    required_scope = ("preserve_composition_theme", "text_image_replacement", "allowed_layout_adjustments")
    if not isinstance(scope, dict) or any(
        not isinstance(scope.get(key), str) or not scope[key].strip() for key in required_scope
    ):
        raise ValueError("adaptation_scope must define preserved composition/theme, text/image replacement, and allowed layout adjustments")
    scope = {key: scope[key].strip() for key in required_scope}
    brief_path = root / "planning/brief.json"
    if not brief_path.is_file():
        raise ValueError("current brief is required before confirming a library template")
    candidates_path, candidate_kind = _candidate_binding(
        root=root, relative_path=packet["selected_catalog_path"],
        source_sha256=packet["source_sha256"], request_text=request_text,
    )
    plan_path = root / "planning/deck-plan.json"
    receipt = {
        "schema": "aippt.template-selection-confirmation.v1",
        "status": "recorded_for_current_request",
        "job_id": job_id,
        "selected_catalog_path": packet["selected_catalog_path"],
        "source_sha256": packet["source_sha256"],
        "source_slide_count": packet["source_slide_count"],
        "work_request_sha256": _work_request_hash(request_text),
        "brief_sha256": _sha256(brief_path),
        "deck_plan_sha256": _sha256(plan_path) if plan_path.is_file() else None,
        "source_previews": bound_previews,
        "user_confirmation_text": confirmation.strip(),
        "user_confirmation_sha256": _text_hash(confirmation.strip()),
        "adaptation_scope": scope,
        "adaptation_scope_sha256": _text_hash(json.dumps(scope, ensure_ascii=False, sort_keys=True)),
        "recorded_by_interface": "trusted_cli_confirmation_record",
        "candidate_packet_path": candidates_path.relative_to(root).as_posix(),
        "candidate_packet_kind": candidate_kind,
        "authenticity_note": "Record storage does not independently authenticate the quoted human or prove visual inspection.",
    }
    receipt["candidate_packet_sha256"] = _sha256(candidates_path)
    write_json(root / _RECEIPT, receipt)
    return {"status": "confirmation_recorded", "receipt": str(root / _RECEIPT),
            "selected_catalog_path": packet["selected_catalog_path"],
            "source_sha256": packet["source_sha256"], "preview_count": len(bound_previews),
            "note": receipt["authenticity_note"]}


def require_current_template_confirmation(
    *, job_id: str, root: Path, request_text: str, catalog_path: Path,
    library_root: Path, relative_path: str, source_sha256: str, adopted: bool = False,
) -> dict[str, Any]:
    receipt_path = root / _RECEIPT
    if not receipt_path.is_file():
        raise ValueError("template selection confirmation is required before binding; present candidates and record the human choice")
    receipt = read_json(receipt_path)
    if not isinstance(receipt, dict) or receipt.get("schema") != "aippt.template-selection-confirmation.v1":
        raise ValueError("template selection confirmation receipt is invalid")
    if (receipt.get("job_id") != job_id
            or receipt.get("selected_catalog_path") != relative_path
            or receipt.get("source_sha256") != source_sha256
            or receipt.get("work_request_sha256") != _work_request_hash(request_text)):
        raise ValueError("template selection confirmation is stale or belongs to a different template/request")
    scope = receipt.get("adaptation_scope")
    if (not isinstance(scope, dict)
            or receipt.get("adaptation_scope_sha256") != _text_hash(
                json.dumps(scope, ensure_ascii=False, sort_keys=True))
            or receipt.get("user_confirmation_sha256") != _text_hash(
                str(receipt.get("user_confirmation_text") or ""))):
        raise ValueError("template selection confirmation contents were changed")
    # Recheck current catalog bytes and the previews before any Work mutation.
    packet = prepare_template_selection(library_root=library_root, catalog_path=catalog_path,
                                       relative_path=relative_path)
    if packet["source_sha256"] != source_sha256:
        raise ValueError("confirmed template source changed in the catalog")
    previews = receipt.get("source_previews")
    minimum = packet["minimum_distinct_source_previews"]
    if not isinstance(previews, list) or len(previews) < minimum:
        raise ValueError("template selection confirmation has too few source previews")
    candidate_packet_relative = receipt.get("candidate_packet_path")
    allowed_candidate_packets = {
        "planning/library-reuse-candidates.json",
        "planning/template-selection-candidates.json",
    }
    if candidate_packet_relative not in allowed_candidate_packets:
        raise ValueError("template confirmation has an invalid candidate packet reference")
    candidates_path = root / candidate_packet_relative
    expected_candidates_hash = receipt.get("candidate_packet_sha256")
    if not adopted and (not candidates_path.is_file() or expected_candidates_hash is None
                        or _sha256(candidates_path) != expected_candidates_hash):
        raise ValueError("template selection confirmation is stale because the candidate packet changed or is missing")
    for artifact_key, artifact_path in (
        ("brief_sha256", root / "planning/brief.json"),
        ("deck_plan_sha256", root / "planning/deck-plan.json"),
    ):
        expected = receipt.get(artifact_key)
        if not adopted and (artifact_key == "brief_sha256" or expected is not None):
            if not artifact_path.is_file() or expected is None or _sha256(artifact_path) != expected:
                raise ValueError(f"template selection confirmation is stale because {artifact_path.name} changed or is missing")
    seen: set[int] = set()
    for preview in previews:
        page = preview.get("source_slide") if isinstance(preview, dict) else None
        if (isinstance(page, bool) or not isinstance(page, int) or page < 1
                or page > packet["source_slide_count"] or page in seen):
            raise ValueError("template selection confirmation contains an invalid/duplicate source page")
        seen.add(page)
        if not isinstance(preview.get("observation"), str) or len(preview["observation"].strip()) < 8:
            raise ValueError("template selection source-page observation is missing")
        path = Path(str(preview.get("work_local_path") or "")).resolve()
        if not path.is_file() or _sha256(path) != preview.get("sha256"):
            raise ValueError("confirmed source preview is missing or changed")
        render_receipt = _validate_rendered_source_preview(
            work=root.parent.parent, item=preview, source_sha256=source_sha256, source_slide=page,
        )
        if (render_receipt["render_receipt_sha256"] != preview.get("render_receipt_sha256")
                or render_receipt["render_receipt_path"] != preview.get("render_receipt_path")):
            raise ValueError("confirmed source preview render receipt is missing or changed")
    return receipt
