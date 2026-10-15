"""Preserve actual tool receipts at closure, without turning calls into acceptance.

The collector is read-only unless its CLI is asked to write a summary. Failed
attempts and broken evidence remain visible; they do not prevent an unrelated
successful route from being archived. The closure manifest pins collected bytes.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .learning import read_json, write_json
from .python_runtime import current_process_identity, validate_identity

_LEGACY = {"adapter-request.json", "adapter-result.json", "adapter-last-result.json",
           "native-adapter-result.json", "external-engine-manifest.json"}
_POINTERS = {"native-prototype-call.json", "native-scene-call.json"}


def _require_host_native_python(parser: argparse.ArgumentParser) -> None:
    """Reject a translated standalone closure-summary writer before collection."""

    reason = validate_identity(current_process_identity())
    if reason is not None:
        parser.error("Tool evidence summary requires a host-native Python: " + reason)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _local(base: Path, value: str) -> Path:
    relative = Path(value)
    path = (base / relative).resolve()
    if relative.is_absolute() or not path.is_relative_to(base.resolve()):
        raise ValueError("Receipt reference escapes its local evidence directory")
    return path


def _collect_officecli_edit_batches(work: Path, preserve, output_hashes: dict) -> tuple[list[dict], list[dict]]:
    """Read the existing apply-edits contract, separately from upstream-call.

    A batch records historical input/output hashes. Whether those paths still
    contain the recorded bytes is an independent observation made now.
    """
    from .models import PptxEditBatch

    batches, errors = [], []

    def file_identity(job: Path, locator: str, expected: str | None, *, output: bool) -> dict:
        result = {"recorded_path": locator, "recorded_sha256": expected,
                  "current_status": "unavailable", "current": None}
        try:
            target = (job / Path(locator)).resolve()
            if not target.is_relative_to(work) or (output and not target.is_relative_to(job.resolve())):
                result["current_status"] = "outside_scope"
            elif not target.is_file():
                result["current_status"] = "missing"
            else:
                current = preserve(target)
                result.update(current=current, current_status=("matches_recorded" if expected and
                    current["sha256"] == expected.lower() else "differs_from_recorded" if expected else "historical_hash_absent"))
        except (OSError, ValueError, TypeError) as exc:
            result["error"] = str(exc)
        return result

    # No recursive schema guessing: only the path written by apply_job_edits.
    for path in sorted((work / "workspace/studio-jobs").glob("*/edits/*.json")):
        try:
            source = preserve(path)
            payload = read_json(path)
            job = path.parent.parent
            if not isinstance(payload, dict) or payload.get("schema_version") != 1:
                raise ValueError("Unsupported OfficeCLI edit batch schema")
            batch = PptxEditBatch.model_validate(payload)
            if batch.job_id != job.name or batch.id != path.stem or not batch.operations:
                raise ValueError("Edit batch identity differs from its job/path or operations are empty")
            for digest in (batch.input_sha256, batch.output_sha256):
                if digest is not None and (len(digest) != 64 or any(c not in "0123456789abcdefABCDEF" for c in digest)):
                    raise ValueError("Edit batch has an invalid recorded content hash")
            input_identity = file_identity(job, batch.input_path, batch.input_sha256, output=False)
            output_identity = file_identity(job, batch.output_path, batch.output_sha256, output=True)
            outcome = ("recorded_applied" if batch.applied_at and batch.validation.get("status") == "passed"
                       and batch.output_sha256 else "recorded_failed" if batch.validation.get("status") == "failed"
                       else "not_established")
            copies = []
            if output_identity["current_status"] == "matches_recorded":
                current = output_identity["current"]
                if current["sha256"] in output_hashes:
                    copies.append({"artifact": current["path"], "sha256": current["sha256"],
                                   "outputs": output_hashes[current["sha256"]]})
            batches.append({"provider": "officecli", "operation": "apply-edits", "batch_id": batch.id,
                "job_id": batch.job_id, "batch": source["path"], "batch_sha256": source["sha256"],
                "contract": "aippt_studio.edits.apply_job_edits/PptxEditBatch-v1",
                "runtime_version": "not_recorded", "execution_status": outcome,
                "created_at": batch.created_at, "applied_at": batch.applied_at,
                "operation_count": len(batch.operations), "recorded_result_count": len(batch.results),
                "recorded_validation": batch.validation, "input_identity": input_identity,
                "output_identity": output_identity, "exact_output_copies": copies,
                "final_adoption": "not_established"})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append({"path": path.relative_to(work).as_posix(), "kind": "officecli_edit_batch", "error": str(exc)})
    return batches, errors


def collect_tool_evidence(work: Path) -> dict:
    work = Path(work).resolve()
    records, calls, pointers, errors = {}, [], {}, []

    def preserve(path: Path) -> dict:
        path = path.resolve()
        if not path.is_relative_to(work) or not path.is_file():
            raise ValueError("Tool evidence is absent or outside this Work")
        key = path.relative_to(work).as_posix()
        if key not in records:
            records[key] = {"path": key, "bytes": path.stat().st_size, "sha256": _sha(path)}
        return records[key]

    jobs = work / "workspace/studio-jobs"
    # Upstream-call accepts any new Work-local directory, not only native-scene's
    # conventional build/upstream-calls directory. Inspect receipt schemas before
    # counting, so runtime preflight receipts do not become upstream executions.
    receipt_paths = set((work / "workspace").rglob("receipt.json"))
    if jobs.is_dir():
        for path in sorted(jobs.rglob("*.json")):
            if path.name not in _LEGACY | _POINTERS:
                continue
            try:
                preserve(path)
                if path.name in _POINTERS:
                    value = read_json(path)
                    target = _local(path.parent.parent, value["receipt"])
                    if preserve(target)["sha256"] != value["sha256"]:
                        raise ValueError("Native call pointer receipt hash differs")
                    pointers.setdefault(target, []).append(path.relative_to(work).as_posix())
                    receipt_paths.add(target)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors.append({"path": path.relative_to(work).as_posix(), "error": str(exc)})
    output_hashes = {}
    for path in sorted((work / "output").rglob("*")):
        if path.is_file() and not path.is_symlink():
            output_hashes.setdefault(_sha(path), []).append(path.relative_to(work).as_posix())
    edit_batches, edit_errors = _collect_officecli_edit_batches(work, preserve, output_hashes)
    errors.extend(edit_errors)
    for path in sorted(receipt_paths):
        try:
            if not path.resolve().is_relative_to(work):
                raise ValueError("Receipt is outside this Work")
            receipt = read_json(path)
            if not isinstance(receipt, dict) or not all(receipt.get(key) for key in ("call_id", "provider", "operation", "request_sha256")):
                continue
            source = preserve(path)
            row = {"provider": receipt["provider"], "operation": receipt["operation"],
                "call_id": receipt["call_id"], "receipt": source["path"], "receipt_sha256": source["sha256"],
                "source_commit": receipt.get("source_commit"), "status": receipt.get("status", "unknown"),
                "result_status": (receipt.get("result") or {}).get("status"),
                "execution": receipt.get("execution") if isinstance(receipt.get("execution"), dict) else {
                    "catalog_state": "legacy_unclassified", "execution_kind": "unknown", "executed": False,
                    "proof": "Legacy receipt has no execution classification; inspect its provider and artifacts directly.",
                },
                "validation_source_binding": ((receipt.get("input_source") or {}).get("status", "not_recorded")
                    if receipt["operation"] == "validate" else "not_applicable"),
                "evidence_status": "verified", "issues": [],
                "selected_by_native_pointer": bool(pointers.get(path.resolve())),
                "native_pointers": pointers.get(path.resolve(), []), "exact_output_copies": [],
                "final_adoption": "not_established"}
            request = path.parent / "request.json"
            try:
                preserve(request)
                actual = hashlib.sha256(json.dumps(read_json(request), sort_keys=True).encode()).hexdigest()
                if actual != receipt["request_sha256"]:
                    row["issues"].append({"kind": "request_hash_mismatch"})
            except (OSError, ValueError, TypeError) as exc:
                row["issues"].append({"kind": "request_unavailable", "error": str(exc)})
            artifacts = receipt.get("artifacts", [])
            if not isinstance(artifacts, list):
                row["issues"].append({"kind": "invalid_artifact_manifest"})
                artifacts = []
            if receipt.get("status") == "succeeded" and not artifacts:
                row["issues"].append({"kind": "missing_artifact_manifest"})
            principal = (receipt.get("result") or {}).get("artifact")
            if principal and not any(isinstance(item, dict) and item.get("path") == principal for item in artifacts):
                row["issues"].append({"kind": "principal_artifact_unbound"})
            for artifact in artifacts:
                try:
                    target = _local(path.parent, artifact["path"])
                    value = preserve(target)
                    if value["sha256"] != artifact["sha256"] or ("bytes" in artifact and value["bytes"] != artifact["bytes"]):
                        row["issues"].append({"kind": "artifact_hash_mismatch", "path": artifact["path"]})
                    elif target.name != "request.json" and value["sha256"] in output_hashes:
                        row["exact_output_copies"].append({"artifact": value["path"], "sha256": value["sha256"],
                            "outputs": output_hashes[value["sha256"]]})
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    row["issues"].append({"kind": "artifact_unavailable", "error": str(exc)})
            if row["issues"]:
                row["evidence_status"] = "mismatch"
            calls.append(row)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            errors.append({"path": path.relative_to(work).as_posix(), "error": str(exc)})
    counts = {"recorded_calls": len(calls), "verified_successful_calls": sum(
        c["status"] == "succeeded" and c["evidence_status"] == "verified" for c in calls),
        "failed_calls": sum(c["status"] == "failed" for c in calls),
        "calls_with_evidence_mismatch": sum(c["evidence_status"] != "verified" for c in calls),
        "officecli_edit_batches": len(edit_batches),
        "officecli_recorded_applied_batches": sum(b["execution_status"] == "recorded_applied" for b in edit_batches)}
    return {"schema_version": 1, "work_id": work.name, "counts": counts,
        "providers": dict(Counter(c["provider"] for c in calls)),
        "execution_kinds": dict(Counter(str((c.get("execution") or {}).get("execution_kind", "unknown")) for c in calls)),
        "limits": "Receipt success proves a recorded tool execution, not visual quality, final adoption or user acceptance. Retries and custom preflights remain separate calls. Native pointers select a build attempt; exact output copies prove byte identity only. Direct external scripts without a registered receipt are not counted.",
        "calls": calls, "officecli_edit_batches": edit_batches,
        "errors": errors, "records": [records[key] for key in sorted(records)]}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args(argv)
    # Collection without --summary is read-only. A requested summary can write
    # closure evidence, so reject Rosetta before even collecting its sources.
    # Parsing precedes this guard to preserve --help on a broken runtime.
    if args.summary is not None:
        _require_host_native_python(parser)
    result = collect_tool_evidence(args.work_dir)
    if args.summary and (result["calls"] or result.get("officecli_edit_batches") or result["errors"] or args.summary.exists()):
        summary = args.summary.resolve()
        work = args.work_dir.resolve()
        if not summary.is_relative_to(work / "workspace"):
            raise ValueError("Closure summary must stay in this Work workspace")
        detail = summary.with_name("tool-execution-detail.json")
        write_json(detail, {key: value for key, value in result.items() if key != "records"})
        # Keep the model-facing summary small enough for bounded QA excerpts.
        # Every full receipt remains sealed in the closure manifest and detail.
        ordered = sorted(result["calls"], key=lambda row: (
            not bool(row["issues"]), row["status"] != "failed", not bool(row["exact_output_copies"]),
            not row["selected_by_native_pointer"], row["receipt"]))
        compact = [{key: row[key] for key in (
            "provider", "operation", "receipt", "receipt_sha256", "status", "result_status",
            "evidence_status", "execution", "selected_by_native_pointer", "final_adoption", "validation_source_binding")}
            for row in ordered[:6]]
        write_json(summary, {"schema_version": 1, "work_id": result["work_id"], "counts": result["counts"],
            "providers": result["providers"], "limits": result["limits"],
            "detail": {"path": detail.relative_to(work).as_posix(), "sha256": _sha(detail)},
            "calls": compact, "omitted_calls": max(0, len(ordered) - len(compact)),
            "errors": result["errors"][:3], "omitted_errors": max(0, len(result["errors"]) - 3)})
        for path in (summary, detail):
            result["records"].append({"path": path.relative_to(work).as_posix(),
                "bytes": path.stat().st_size, "sha256": _sha(path)})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
