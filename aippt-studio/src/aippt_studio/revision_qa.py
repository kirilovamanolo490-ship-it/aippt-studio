"""Local, source-bound QA for explicit ``pptx-patch`` candidates.

This compares deterministic object-level findings on the edited slides only.
It never claims that an unrendered candidate looks correct or that untouched
slides passed a whole-deck audit.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from .presentation_quality import audit_presentation
from .pptx_edit import _package, _picture_media_part, _select, _slides, _xml, NS

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _read_receipt(value: dict | Path | str) -> tuple[dict, Path | None]:
    receipt_path = None
    if isinstance(value, dict):
        receipt = value
    else:
        receipt_path = Path(value).expanduser().resolve(strict=True)
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if not isinstance(receipt, dict):
        raise ValueError("receipt must contain a JSON object")
    return receipt, receipt_path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _issue_key(issue: dict) -> tuple:
    details = issue.get("details", {})
    return (issue.get("slide"), issue.get("object_id"), issue.get("code"),
            details.get("other_object_id"))


def _open_issues(report: dict) -> dict[tuple, dict]:
    return {_issue_key(issue): issue for issue in report.get("issues", [])
            if issue.get("disposition") == "open" and issue.get("severity") in {"blocker", "review"}}


def _worsening_metric(code: str) -> tuple[str, str] | None:
    """Return (field, direction) for a measured value where lower is worse."""
    if code == "OUT_OF_BOUNDS":
        return "overflow_pt", "higher"
    if code in {"TEXT_OVERLAP", "IMAGE_TEXT_OVERLAP", "TEXT_OCCLUDED"}:
        return "overlap_ratio", "higher"
    if code == "SMALL_FONT":
        return "actual_min_font_pt", "lower"
    return None


def _worsened(before: dict, after: dict) -> dict | None:
    before_details, after_details = before.get("details", {}), after.get("details", {})
    measurement = _worsening_metric(str(after.get("code")))
    if measurement is not None:
        field, direction = measurement
        old, new = before_details.get(field), after_details.get(field)
        if (type(old) in {int, float} and type(new) in {int, float}
                and (new > old if direction == "higher" else new < old)):
            return {"metric": field, "baseline_value": old, "candidate_value": new}
    if before.get("severity") == "review" and after.get("severity") == "blocker":
        return {"metric": "severity", "baseline_value": "review", "candidate_value": "blocker"}
    return None


def audit_revision(
    source: Path | str,
    candidate: Path | str,
    receipt: dict | Path | str,
    *,
    policy: dict | None = None,
) -> dict:
    """Verify a patch receipt and compare source/candidate issues on edited pages.

    ``receipt`` may be the result returned by ``patch_pptx`` or its
    ``receipt.json`` path. The candidate must be the exact file named by the
    receipt. Raises ``ValueError`` on stale, malformed, or inconsistent input.
    """
    source_path = Path(source).expanduser().resolve(strict=True)
    candidate_path = Path(candidate).expanduser().resolve(strict=True)
    receipt_data, receipt_path = _read_receipt(receipt)

    if receipt_data.get("status") != "candidate":
        raise ValueError("revision QA requires an applied pptx-patch candidate receipt")
    source_sha = receipt_data.get("source_sha256")
    candidate_sha = receipt_data.get("candidate_sha256")
    if not isinstance(source_sha, str) or not _SHA256.fullmatch(source_sha):
        raise ValueError("receipt has no valid source_sha256")
    if not isinstance(candidate_sha, str) or not _SHA256.fullmatch(candidate_sha):
        raise ValueError("receipt has no valid candidate_sha256")
    if _sha(source_path.read_bytes()) != source_sha:
        raise ValueError("source SHA-256 does not match the patch receipt")
    recorded_candidate = receipt_data.get("candidate")
    if recorded_candidate:
        expected_candidate = Path(recorded_candidate).expanduser()
        if not expected_candidate.is_absolute() and receipt_path is not None:
            expected_candidate = receipt_path.parent / expected_candidate
        if expected_candidate.resolve() != candidate_path:
            raise ValueError("candidate path does not match the path bound by the patch receipt")
    if _sha(candidate_path.read_bytes()) != candidate_sha:
        raise ValueError("candidate SHA-256 does not match the patch receipt")

    operations = receipt_data.get("operations")
    changed_parts = receipt_data.get("changed_parts")
    if not isinstance(operations, list) or not operations or not isinstance(changed_parts, list) or not changed_parts:
        raise ValueError("receipt must contain non-empty operations and changed_parts")
    if any(not isinstance(operation, dict) or not isinstance(operation.get("request"), dict)
           or operation["request"].get("action") not in {"set", "add_textbox", "replace_text", "replace_picture", "z_order", "align", "distribute"}
           or type(operation.get("slide")) is not int or operation.get("slide", 0) < 1
           or operation["request"].get("slide") != operation.get("slide")
           for operation in operations):
        raise ValueError("receipt operations must contain the explicit request and matching slide")
    slides = sorted({operation.get("slide") for operation in operations
                     if type(operation.get("slide")) is int})
    if not slides:
        raise ValueError("receipt operations must identify positive 1-based target slides")
    if slides[0] < 1:
        raise ValueError("receipt operations must identify positive 1-based target slides")

    before, after = _package(source_path.read_bytes()), _package(candidate_path.read_bytes())
    if set(before) != set(after):
        raise ValueError("candidate PPTX has added or removed package members")
    actual_changed = {name for name in before if before[name] != after[name]}
    recorded = {}
    for item in changed_parts:
        if not isinstance(item, dict) or set(item) != {"part", "before_sha256", "after_sha256"}:
            raise ValueError("receipt changed_parts entries are malformed")
        part = item["part"]
        if not isinstance(part, str) or part in recorded or part not in before:
            raise ValueError("receipt changed_parts contains an invalid or duplicate package member")
        if not _SHA256.fullmatch(str(item["before_sha256"])) or not _SHA256.fullmatch(str(item["after_sha256"])):
            raise ValueError("receipt changed_parts contains an invalid member hash")
        if (_sha(before[part]) != item["before_sha256"] or _sha(after[part]) != item["after_sha256"]
                or before[part] == after[part]):
            raise ValueError(f"package member does not match receipt change evidence: {part}")
        recorded[part] = item
    if actual_changed != set(recorded):
        raise ValueError("actual changed PPTX package members differ from receipt.changed_parts")

    source_slide_parts = _slides(before)
    candidate_slide_parts = _slides(after)
    if source_slide_parts != candidate_slide_parts:
        raise ValueError("slide order or relationships changed; this patch is outside scoped revision QA")
    if any(slide > len(source_slide_parts) for slide in slides):
        raise ValueError("receipt target slide does not exist in the source deck")
    target_parts = {source_slide_parts[number - 1] for number in slides}
    changed_slide_parts = {name for name in actual_changed if name.startswith("ppt/slides/slide") and name.endswith(".xml")}
    if not changed_slide_parts <= target_parts:
        raise ValueError("actual changed slide XML does not match the target pages in receipt.operations")
    expected_media_parts = set()
    for operation in operations:
        request = operation["request"]
        if request["action"] != "replace_picture":
            continue
        slide_part = source_slide_parts[operation["slide"] - 1]
        source_tree = _xml(before[slide_part]).find("p:cSld/p:spTree", NS)
        if source_tree is None:
            raise ValueError("source slide has no explicit shape tree for picture replacement")
        selected = _select(source_tree, request.get("shape_id"))
        expected_media_parts.add(_picture_media_part(before, slide_part, selected))
    changed_media_parts = {name for name in actual_changed if name.startswith("ppt/media/")}
    if changed_media_parts != expected_media_parts:
        raise ValueError("changed PPTX media does not match the exact selected picture media")
    if not changed_slide_parts and not changed_media_parts:
        raise ValueError("scoped revision QA found no target slide or picture media change")
    unexpected = actual_changed - changed_slide_parts - changed_media_parts
    if unexpected:
        raise ValueError(f"scoped revision QA does not accept non-slide package edits: {sorted(unexpected)}")
    for part in changed_slide_parts:
        match = re.fullmatch(r"ppt/slides/slide\d+\.xml", part)
        if not match:
            raise ValueError(f"unsupported changed package member: {part}")
        _xml(after[part])

    source_report = audit_presentation(source_path, policy, selected_slides=slides)
    candidate_report = audit_presentation(candidate_path, policy, selected_slides=slides)
    for label, report in (("source", source_report), ("candidate", candidate_report)):
        if report.get("status") == "errors":
            raise ValueError(f"{label} scoped quality audit failed: {report.get('errors')}")

    baseline, current = _open_issues(source_report), _open_issues(candidate_report)
    new_keys = current.keys() - baseline.keys()
    old_keys = baseline.keys() & current.keys()
    cleared_keys = baseline.keys() - current.keys()
    new_issues = [current[key] for key in sorted(new_keys, key=str)]
    worsened_issues, persistent_issues = [], []
    for key in sorted(old_keys, key=str):
        before_issue, after_issue = baseline[key], current[key]
        worsening = _worsened(before_issue, after_issue)
        if worsening is not None:
            worsened_issues.append({**after_issue, "worsening": worsening,
                                    "baseline_details": before_issue.get("details", {})})
        else:
            persistent_issues.append({**after_issue, "baseline_details": before_issue.get("details", {})})
    cleared_issues = [baseline[key] for key in sorted(cleared_keys, key=str)]

    action_kinds = sorted({operation["request"]["action"] for operation in operations})
    requests = [operation["request"] for operation in operations]
    changes_geometry = any("geometry" in request or request.get("action") in {"align", "distribute", "z_order"}
                           for request in requests)
    changes_font = any("font_size_pt" in request for request in requests)
    replaces_copy = "replace_text" in action_kinds
    replaces_picture = "replace_picture" in action_kinds
    adds_textbox = "add_textbox" in action_kinds
    if changes_font or changes_geometry or replaces_copy or replaces_picture or adds_textbox:
        visual = {"required": True, "scope": "target_slides_only",
                  "recommended": ["render_target_page_preview", "inspect_actual_page_image"],
                  "assistant_review": ("required_for_image_to_claim_and_crop" if replaces_picture
                                       else "required_for_new_page_copy_and_hierarchy" if adds_textbox
                                       else "only_if_copy_meaning_or_hierarchy_changed"),
                  "reason": ("picture replacement requires target-page preview to inspect crop, frame and meaning"
                             if replaces_picture else
                             "new text requires target-page preview to inspect fit, hierarchy and audience-facing wording"
                             if adds_textbox else
                             "text replacement requires target-page preview to check actual wrapping and fit"
                             if replaces_copy else
                             "font size, geometry, or stacking changes can alter rendered fit and visible hierarchy")}
    else:
        visual = {"required": False, "scope": "target_slides_only",
                  "recommended": [], "assistant_review": "only_if_copy_meaning_or_page_claim_changed",
                  "reason": "the patch operations do not declare visual geometry, typography, or stacking changes"}
    if new_issues or worsened_issues:
        visual["required"] = True
        visual["recommended"] = sorted(set(visual["recommended"] + ["assistant_review_changed_quality_findings"]))
        visual["reason"] += "; newly introduced or worsened structural findings need contextual review"

    return {
        "schema_version": 1,
        "kind": "scoped_revision_qa",
        "status": "needs_review" if new_issues or worsened_issues else "passed_scoped",
        "scope": {"kind": "selected_slides", "selected_slides": slides,
                  "full_deck_verified": False, "whole_deck_pass_claimed": False},
        "source_sha256": source_sha,
        "candidate_sha256": candidate_sha,
        "receipt_sha256": _sha(json.dumps(receipt_data, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":"), allow_nan=False).encode("utf-8")),
        "actual_changed_parts": sorted(actual_changed),
        "target_changed_slide_parts": sorted(changed_slide_parts),
        "target_changed_media_parts": sorted(changed_media_parts),
        "actions": action_kinds,
        "quality": {"source_status": source_report["status"], "candidate_status": candidate_report["status"],
                    "source_open_issue_count": len(baseline), "candidate_open_issue_count": len(current),
                    "new_issues": new_issues, "persistent_issues": persistent_issues,
                    "worsened_issues": worsened_issues, "cleared_issues": cleared_issues},
        "visual_review": visual,
        "limitations": ["Only receipt target slides were structurally audited.",
                        "Structural checks do not establish native rendered fit or semantic quality.",
                        "passed_scoped is not a full-deck or aesthetic approval."],
    }
