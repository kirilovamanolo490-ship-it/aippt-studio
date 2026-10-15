"""Read-only current-output identity and structural QA for a Work package.

The report deliberately separates today's local audit from historical QA. A
manifest or report is current only when its artifact hash (and any recorded
slide count) matches the exact PPTX being checked.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import re

from pptx import Presentation

from .presentation_quality import audit_presentation

_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_EVIDENCE_NAMES = {
    "final-output-manifest.json",
    "final-delivery.json",
    "final-delivery-v9.json",
    "presentation-quality.json",
    "final-quality-detail.json",
}
_DELIVERY_PROOF_PREFIXES = ("final-delivery", "native-roundtrip", "package-audit")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")), None
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return None, f"{type(error).__name__}: {error}"


def _pptx_rows(document: dict) -> list[dict]:
    rows = document.get("outputs", document.get("deliverables", []))
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict) and
            (not row.get("path") or str(row.get("path", "")).lower().endswith(".pptx"))]


def _select_pptx_row(rows: list[dict], source_relative: str | None) -> dict | None:
    """Select the record for this exact Work-relative deliverable.

    A single historical row can be used when it names this output, or when it
    omits its path. In a multi-deck record, an exact path match is mandatory.
    """
    if not rows:
        return None
    if source_relative:
        wanted = PurePosixPath(source_relative.replace("\\", "/")).as_posix()
        matches = [row for row in rows if row.get("path") and
                   PurePosixPath(str(row["path"]).replace("\\", "/")).as_posix() == wanted]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
    if len(rows) == 1:
        row = rows[0]
        path = row.get("path")
        if not path or (source_relative and
                        PurePosixPath(str(path).replace("\\", "/")).as_posix() ==
                        PurePosixPath(source_relative.replace("\\", "/")).as_posix()):
            return row
    return None


def _declared_identity(
    document: dict,
    evidence_path: Path,
    source_relative: str | None = None,
) -> tuple[str | None, int | None]:
    """Read only recognized artifact identity fields, never arbitrary hashes."""
    rows = _pptx_rows(document)
    selected = _select_pptx_row(rows, source_relative)
    if rows and selected is None:
        # An explicitly named deliverable for a different PPTX cannot lend
        # its top-level hash/page claims to this output, even in a one-deck
        # receipt. Missing paths in a single legacy row remain compatible.
        return None, None
    proof_family = _delivery_proof_family(evidence_path)
    digest = selected.get("sha256") if selected is not None and selected.get("sha256") else document.get("source_sha256")
    if proof_family and len(rows) > 1:
        # Multi-deck receipts must bind every claim to the selected row. A
        # top-level identity or page count may describe another deck.
        digest = selected.get("sha256") if selected is not None else None
    elif proof_family and digest is None:
        # Older Work receipts bind their audited PPTX as ``sha256`` rather
        # than ``source_sha256``. Accept that field only for these explicit
        # terminal proof names; never guess from arbitrary JSON hashes.
        digest = document.get("sha256")
    scope = document.get("scope") if isinstance(document.get("scope"), dict) else {}
    pages = None
    if evidence_path.name == "presentation-quality.json" or evidence_path.name == "final-quality-detail.json":
        summary = document.get("summary")
        if isinstance(summary, dict):
            pages = summary.get("total_deck_slides")
            if pages is None and scope.get("full_deck_verified"):
                pages = summary.get("slides")
        if pages is None and scope.get("full_deck_verified"):
            pages = document.get("slides")
    elif evidence_path.name.startswith(_DELIVERY_PROOF_PREFIXES):
        pages = selected.get("slides", selected.get("page_count")) if selected is not None else None
        if pages is None and len(rows) <= 1:
            pages = document.get("slides", document.get("slide_count", document.get("page_count")))
        if pages is None and len(rows) <= 1:
            pages = document.get("qa", {}).get("rendered_pages") if isinstance(document.get("qa"), dict) else None
    else:
        if len(rows) > 1:
            digest = selected.get("sha256") if selected is not None else None
            pages = selected.get("slides", selected.get("page_count")) if selected is not None else None
        else:
            if selected is not None:
                digest = selected.get("sha256") or digest
                pages = selected.get("slides", selected.get("page_count"))
            qa = document.get("qa")
            if pages is None and isinstance(qa, dict):
                pages = qa.get("rendered_pages", qa.get("slides"))
            if pages is None:
                pages = document.get("slides", document.get("page_count"))
    digest = digest.lower() if isinstance(digest, str) and _SHA256.fullmatch(digest) else None
    pages = pages if type(pages) is int and pages > 0 else None
    return digest, pages


def _delivery_proof_family(evidence_path: Path) -> str | None:
    return next((prefix for prefix in _DELIVERY_PROOF_PREFIXES
                 if evidence_path.name.startswith(prefix)), None)


def _delivery_proof_passed(document: dict, family: str) -> bool:
    status = document.get("status")
    if not isinstance(status, str):
        return False
    status = status.casefold()
    if family == "native-roundtrip":
        return status == "passed"
    if family == "package-audit":
        return status.startswith("passed")
    return status in {"passed", "ready_for_user_review", "delivered"}


def _evidence(work: Path, source: Path, source_sha: str, slide_count: int) -> list[dict]:
    workspace = work / "workspace"
    if not workspace.is_dir():
        return []
    found = []
    # Only compare claims about the selected final output. Historical authoring
    # candidates legitimately have other hashes and are not stale delivery QA.
    terminal_dirs = (workspace / "qa", workspace / "final-build")
    paths = sorted(p for directory in terminal_dirs if directory.is_dir()
                   for p in directory.glob("*.json")
                   if p.name in _EVIDENCE_NAMES or p.name.startswith(_DELIVERY_PROOF_PREFIXES))
    for path in paths:
        document, error = _read_json(path)
        row = {"path": path.relative_to(work).as_posix()}
        if error:
            row.update(status="unreadable", reason=error)
        elif not isinstance(document, dict):
            row.update(status="unbound", reason="evidence is not a JSON object")
        else:
            source_relative = source.relative_to(work).as_posix()
            digest, pages = _declared_identity(document, path, source_relative)
            row.update(recorded_sha256=digest, recorded_slide_count=pages)
            proof_family = _delivery_proof_family(path)
            if digest is None:
                row.update(status="unbound", reason="no recognized artifact SHA-256")
            elif digest != source_sha:
                row.update(status="stale", reason="artifact SHA-256 differs from current output")
            elif proof_family and pages is None:
                row.update(status="unbound", reason="terminal delivery proof has no slide count")
            elif pages is not None and pages != slide_count:
                row.update(status="stale", reason="recorded slide count differs from current output")
            elif proof_family and not _delivery_proof_passed(document, proof_family):
                row.update(status="failed", reason="terminal delivery proof does not report a passing status")
            else:
                row.update(status="current", reason="artifact SHA-256 matches current output")
        found.append(row)
    return found


def output_preflight(
    work_dir: str | Path,
    pptx_path: str | Path | None = None,
    *,
    policy: dict | None = None,
    policy_source_sha256: str | None = None,
) -> dict:
    """Audit one exact Work output, read-only, and identify stale QA evidence.

    When ``pptx_path`` is omitted, ``work/output`` must contain exactly one
    PPTX. Relative explicit paths are resolved from the Work root.
    """
    work = Path(work_dir).expanduser().resolve()
    if pptx_path is None:
        outputs = sorted((work / "output").glob("*.pptx")) if (work / "output").is_dir() else []
        if len(outputs) != 1:
            return {"schema_version": 1, "status": "errors", "work": str(work),
                    "errors": [{"code": "OUTPUT_SELECTION", "message":
                                f"expected exactly one PPTX in output/, found {len(outputs)}"}],
                    "evidence": []}
        source = outputs[0].resolve()
    else:
        candidate = Path(pptx_path).expanduser()
        source = (candidate if candidate.is_absolute() else work / candidate).resolve()
    if not source.is_file() or source.suffix.lower() != ".pptx":
        return {"schema_version": 1, "status": "errors", "work": str(work),
                "source": str(source), "errors": [{"code": "OUTPUT_MISSING",
                "message": "current output must be an existing .pptx file"}], "evidence": []}
    if not source.is_relative_to((work / "output").resolve()):
        return {"schema_version": 1, "status": "errors", "work": str(work),
                "source": str(source), "errors": [{"code": "OUTPUT_SCOPE",
                "message": "current output must be inside this Work output directory"}], "evidence": []}
    before_sha = _sha(source)
    if policy is not None:
        bound_sha = policy_source_sha256.lower() if isinstance(policy_source_sha256, str) else None
        if bound_sha is None or not _SHA256.fullmatch(bound_sha):
            return {"schema_version": 1, "status": "errors", "work": str(work),
                    "source": str(source), "source_sha256": before_sha,
                    "errors": [{"code": "POLICY_SOURCE_SHA_REQUIRED",
                                "message": "a supplied quality policy must name the exact PPTX SHA-256 it was reviewed for"}],
                    "evidence": []}
        if bound_sha != before_sha:
            return {"schema_version": 1, "status": "needs_review", "work": str(work),
                    "source": str(source), "source_sha256": before_sha,
                    "errors": [{"code": "POLICY_SOURCE_SHA_MISMATCH",
                                "message": "quality policy SHA-256 does not match the current PPTX"}],
                    "evidence": []}
    quality = audit_presentation(source, policy)
    try:
        slide_count = len(Presentation(source).slides)
    except Exception:
        slide_count = None
    after_sha = _sha(source)
    if after_sha != before_sha or quality.get("source_sha256") != before_sha:
        return {"schema_version": 1, "status": "errors", "work": str(work), "source": str(source),
                "source_sha256": after_sha, "slide_count": slide_count, "quality": quality,
                "errors": [{"code": "OUTPUT_CHANGED_DURING_AUDIT",
                            "message": "source bytes changed while the current output was audited"}],
                "evidence": []}
    evidence = _evidence(work, source, before_sha, slide_count or 0)
    # If this Work has terminal delivery proof, at least one receipt in each
    # represented proof family must describe the exact current deck. Historical
    # receipts may remain beside the current one; they do not poison a current
    # receipt. A family with receipts but none matching the selected bytes is
    # unresolved and must not be presented as current verification.
    proof_families: dict[str, list[dict]] = {}
    for row in evidence:
        name = Path(row["path"]).name
        family = _delivery_proof_family(Path(name))
        if family:
            proof_families.setdefault(family, []).append(row)
    proof_issues = [
        {"code": "STALE_DELIVERY_PROOF", "family": family,
         "message": f"{family} receipts exist, but none is bound to the current PPTX SHA-256 and slide count"}
        for family, rows in proof_families.items()
        if not any(row["status"] == "current" for row in rows)
    ]
    status = ("errors" if quality.get("status") == "errors" or slide_count is None else
              "needs_review" if proof_issues else
              "passed" if quality.get("status") == "passed" else "needs_review")
    return {
        "schema_version": 1,
        "status": status,
        "work": str(work),
        "source": str(source),
        "identity": {"sha256": before_sha, "bytes": source.stat().st_size,
                     "slide_count": slide_count},
        "quality": quality,
        "quality_policy_source_sha256": policy_source_sha256.lower() if policy is not None else None,
        "evidence": evidence,
        "delivery_proof_issues": proof_issues,
        "evidence_summary": {
            "current": sum(row["status"] == "current" for row in evidence),
            "stale": sum(row["status"] == "stale" for row in evidence),
            "unbound": sum(row["status"] == "unbound" for row in evidence),
            "unreadable": sum(row["status"] == "unreadable" for row in evidence),
            "failed": sum(row["status"] == "failed" for row in evidence),
        },
        "coverage": "Local structural audit only. A passed result does not establish expression, visual layout, playback, or delivery acceptance.",
    }
