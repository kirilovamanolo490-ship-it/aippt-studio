"""One transaction for a small edit to an existing editable presentation.

The expensive first build is reused.  A fast revision applies exact OOXML
object edits, audits only the changed pages, and optionally renders only those
pages for human visual judgment.  It never exports a PDF or rebuilds the deck.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .pptx_edit import patch_pptx
from .revision_qa import audit_revision
from .storage import write_json


_PREVIEW_BACKENDS = {"none", "officekit", "officecli"}
_MAX_FAST_PAGES = 4


class CandidateIdentityError(ValueError):
    """The scoped QA and preview no longer refer to the same candidate."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_current_candidate(candidate: Path, expected_sha256: str) -> None:
    if not candidate.is_file() or _sha256(candidate) != expected_sha256:
        raise CandidateIdentityError("candidate changed after scoped QA; discard previews and inspect the current file again")


def revise_pptx(
    request: dict,
    output: Path,
    *,
    preview_backend: str = "officekit",
    policy: dict | None = None,
    preview_width: int = 1280,
) -> dict:
    """Make one source-bound candidate with scoped QA and changed-page previews.

    ``request`` uses the same explicit edit contract as ``pptx-patch``.  A
    candidate remains reviewable if an optional approximate preview fails;
    the failure is recorded and native visual verification remains outstanding.
    """
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    if request.get("dry_run") is True:
        raise ValueError("fast revision applies a candidate; use pptx-patch for dry-run planning")
    if preview_backend not in _PREVIEW_BACKENDS:
        raise ValueError(f"preview_backend must be one of {sorted(_PREVIEW_BACKENDS)}")
    if type(preview_width) is not int or not 320 <= preview_width <= 4096:
        raise ValueError("preview_width must be an integer from 320 through 4096")
    edits = request.get("edits")
    if not isinstance(edits, list) or not edits or any(not isinstance(edit, dict) for edit in edits):
        raise ValueError("request must contain explicit edits")
    pages = sorted({edit.get("slide") for edit in edits if type(edit.get("slide")) is int})
    if len(pages) > _MAX_FAST_PAGES:
        raise ValueError("fast revision is limited to four changed pages; use the full review route for a larger change")

    output = Path(output).expanduser().absolute()
    active_work = os.environ.get("AIPPT_WORK_DIR")
    if active_work:
        workspace = Path(active_work).expanduser().resolve(strict=True) / "workspace"
        if not workspace.is_dir() or not output.resolve(strict=False).is_relative_to(workspace.resolve(strict=True)):
            raise ValueError("fast revision output must be a new directory inside the active Work workspace")
    receipt = patch_pptx(request, output)
    candidate = output / "candidate.pptx"
    qa = audit_revision(receipt["source"], candidate, output / "receipt.json", policy=policy)
    write_json(output / "revision-qa.json", qa)
    _require_current_candidate(candidate, receipt["candidate_sha256"])
    selected_pages = qa["scope"]["selected_slides"]
    previews: list[dict] = []
    if preview_backend != "none":
        # A local renderer is only a fast visual aid.  Keep each result and its
        # source-bound receipt on the same candidate as the structural audit.
        from .local_preview import LocalPreviewError, render_slide_preview
        from .local_preview_officecli import OfficeCliPreviewError, render_officecli_slide_preview

        for slide in selected_pages:
            _require_current_candidate(candidate, receipt["candidate_sha256"])
            path = output / f"slide-{slide}.png"
            try:
                if preview_backend == "officekit":
                    rendered = render_slide_preview(candidate, slide, path, width=preview_width,
                                                    load_system_fonts=True)
                else:
                    rendered = render_officecli_slide_preview(candidate, slide, path, width=preview_width)
                if rendered.get("source_sha256") != receipt["candidate_sha256"]:
                    raise CandidateIdentityError("preview source differs from the candidate audited by scoped QA")
                _require_current_candidate(candidate, receipt["candidate_sha256"])
                previews.append({"slide": slide, "status": "rendered_advisory",
                                 "png": rendered["output_png"], "sha256": rendered["output_sha256"],
                                 "renderer": rendered["renderer"],
                                 "candidate_sha256": rendered["source_sha256"]})
            except CandidateIdentityError:
                # A changed source invalidates the QA/preview binding. Keep
                # the files for inspection, but never emit a passing aggregate.
                raise
            except (LocalPreviewError, OfficeCliPreviewError, OSError, ValueError) as error:
                previews.append({"slide": slide, "status": "unavailable", "reason": str(error)})

    _require_current_candidate(candidate, receipt["candidate_sha256"])
    result = {
        "schema_version": 1,
        "kind": "fast_revision",
        "status": ("needs_structural_review" if qa["status"] != "passed_scoped" else
                   "needs_visual_review" if preview_backend == "none" or any(
                       item["status"] != "rendered_advisory" for item in previews) else
                   "candidate_scoped_checked"),
        "source": receipt["source"],
        "source_sha256": receipt["source_sha256"],
        "candidate": receipt["candidate"],
        "candidate_sha256": receipt["candidate_sha256"],
        "changed_slides": selected_pages,
        "changed_parts": [item["part"] for item in receipt["changed_parts"]],
        "revision_qa": str(output / "revision-qa.json"),
        "revision_qa_status": qa["status"],
        "preview_backend": preview_backend,
        "previews": previews,
        "native_visual_verified": False,
        "final_deck_verified": False,
        "next": "Inspect affected whole-page visuals and neighbors when meaning or reading order changed; use native PowerPoint for final acceptance.",
    }
    write_json(output / "fast-revision.json", result)
    return result
