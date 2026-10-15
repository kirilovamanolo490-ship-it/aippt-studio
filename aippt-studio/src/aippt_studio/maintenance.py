"""Incremental local archive plus explicitly enabled semantic learning."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from filelock import FileLock, Timeout

from .learning import compile_lessons, evidence_record, evidence_valid, load_events, now, read_json, retrospective_draft, write_json
from .python_runtime import current_process_identity, validate_identity
from .runtime import system_launchctl


# These are outcomes of actual stage work, not scheduler/queue observations.
# In particular, "unchanged" cannot prove that a previously failed model run
# was retried: it may mean a retry delay or an empty currently eligible queue.
_MODEL_SUCCESS_STATUSES = {
    "project_review": {"progress", "review_complete"},
    "semantic_learning": {"learned", "reviewed"},
    "core_evolution": {"diagnosed", "verified_applied"},
}
_MODEL_FAILURE_STATUSES = {
    "error", "failed", "needs_attention", "partial", "needs_recovery",
    "validation_failed", "review_rejected",
}
_DEFAULT_ACTIVE_SNAPSHOT_INTERVAL_SECONDS = 24 * 60 * 60


def build_authoring_context(repo: Path, work: Path) -> dict:
    """Build a compact, durable authoring entrypoint for a minimal Work."""
    request_path = work / "input/request.md"
    request_sha256 = hashlib.sha256(request_path.read_bytes()).hexdigest()
    shared_paths = (
        ".agents/skills/aippt-studio/SKILL.md",
        ".agents/skills/gaojie-layouts/SKILL.md",
        "aippt-studio/docs/AUTHORING_ENTRYPOINTS.md",
        "aippt-studio/docs/playbook/ppt-production-knowledge-base.md",
    )
    return {
        "schema_version": 1,
        "authoring_workflow_version": 1,
        "request_sha256": request_sha256,
        "path_base": "repository root; resolve workspace/.studio-root when operating from inside a Work",
        "shared_entrypoints": [Path(repo / entry).relative_to(repo).as_posix() for entry in shared_paths],
        "workflow": [
            "Identify the specific presentation form, audience need, occasion, and decision or understanding required.",
            "Set the central message, evidence boundary, storyline, and neighboring-page logic before composing pages.",
            "Distinguish factual source material, visual references, and an editable template whose structure must be carried forward.",
            "For new composition, consider suitable whole-deck organization first, then use Gaojie page layouts only where a page relationship fits; record what to borrow or reject.",
            "Map claims, evidence, text, images, and editable objects to each page; inspect rendered pages in context and revise expression defects.",
            "Keep local edits scoped to the requested objects and preserve accepted content; do not restart full-deck planning for a bounded change.",
        ],
        "operating_principles": [
            "This guidance is independent of the current chat.",
            "Judge each current task independently; do not inherit facts or aesthetic approval from another Work.",
            "These shared entrypoints guide decisions; they do not replace task-specific judgment or prove that a reference was used.",
        ],
    }


def initialize_authoring_context(repo: Path, work: Path) -> dict:
    """Persist authoring entrypoints once, without replacing Work choices."""
    record = read_json(work / "work.json", {})
    if isinstance(record, dict) and "authoring_workflow_version" not in record:
        record["authoring_workflow_version"] = 1
        write_json(work / "work.json", record)
    destination = work / "workspace/authoring-context.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file():
        write_json(destination, build_authoring_context(repo, work))
    return read_json(destination, {})


def _active_snapshot_interval_seconds(value: int | None) -> int:
    """Resolve the maintenance-only cadence; explicit sync calls remain immediate."""
    raw: object = value
    if raw is None:
        raw = os.environ.get("AIPPT_MAINTENANCE_ACTIVE_SNAPSHOT_INTERVAL_SECONDS",
                             str(_DEFAULT_ACTIVE_SNAPSHOT_INTERVAL_SECONDS))
    try:
        interval = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("AIPPT_MAINTENANCE_ACTIVE_SNAPSHOT_INTERVAL_SECONDS must be a positive integer") from exc
    if interval <= 0:
        raise ValueError("AIPPT_MAINTENANCE_ACTIVE_SNAPSHOT_INTERVAL_SECONDS must be a positive integer")
    return interval


def _model_has_errors(value: dict) -> bool:
    """A successful segment can still report failures in other project work."""
    return bool(value.get("status") in _MODEL_FAILURE_STATUSES or any(
        value.get(key) for key in ("error", "errors", "projection_errors", "report_error")))


def _preserved_model_errors(previous: dict, stages: dict[str, dict], current_errors: list[dict]) -> list[dict]:
    """Only a meaningful new observation can replace that stage's old error.

    Clean completed work clears the previous error; a newly observed failure
    replaces it with current details. Queued/running/budget/disabled/unchanged
    outcomes and future unknown statuses keep the last unresolved evidence.
    """
    retained = []
    for error in previous.get("errors", []):
        stage = error.get("stage")
        if stage not in _MODEL_SUCCESS_STATUSES:
            continue
        value = stages[stage]
        meaningful = value.get("status") in _MODEL_SUCCESS_STATUSES[stage] | _MODEL_FAILURE_STATUSES
        if not meaningful and error not in current_errors and error not in retained:
            retained.append(error)
    return retained


def pending_binding_path(work: Path) -> Path:
    return work / "workspace/pending-conversation-binding.json"


def retry_pending_binding(work: Path) -> dict:
    """Retry only an explicitly recorded thread ID, never a title or cwd guess."""
    from .conversations import bind_conversation
    path = pending_binding_path(work)
    if not path.is_file():
        return {"status": "none"}
    intent = {}
    try:
        intent = read_json(path)
        if not isinstance(intent, dict) or not isinstance(intent.get("thread_id"), str):
            raise ValueError("Invalid pending conversation binding")
        binding = bind_conversation(work, thread_id=intent["thread_id"],
                                    binding_reason=intent.get("binding_reason", "Retry explicit Work initialization binding"))
        # A metadata-only Work intentionally declines binding. Keep the intent
        # for audit/reconciliation, but do not turn the policy into a retry error.
        if binding.get("status") == "metadata_only":
            return {"status": "metadata_only", "thread_id": intent["thread_id"],
                    "message": "Work retention policy suppresses conversation binding"}
        if binding.get("status") == "disabled":
            return {"status": "disabled", "thread_id": intent["thread_id"],
                    "message": "Work conversation retention is disabled"}
        if binding.get("status") == "retention_policy_invalid":
            return {"status": "retention_policy_invalid", "thread_id": intent["thread_id"],
                    "error": binding.get("error", "Work retention policy is invalid"),
                    "message": "Pending conversation binding retained because its retention policy is invalid"}
        path.unlink()
        return {"status": "bound", "thread_id": intent["thread_id"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {"status": "pending", "thread_id": intent.get("thread_id") if isinstance(intent, dict) else None,
                "error": str(exc)}


def initialize_work(repo: Path, work: Path, *, thread_id: str | None = None) -> dict:
    from .conversations import archive_conversation, refresh_conversation_tree
    from .platform_runtime import select_artifact_runtime

    errors, archive = [], {"status": "unbound"}
    record = read_json(work / "work.json", {})
    # New Works retain source materials, production code/config, deliverables,
    # and compact QA receipts. Full conversation capture remains available to
    # older Works or an explicitly configured future policy.
    minimal = isinstance(record, dict) and record.get("storage_profile") == "minimal"
    if minimal and record.get("conversation_retention") != "disabled":
        record["conversation_retention"] = "disabled"
        write_json(work / "work.json", record)
    if minimal or record.get("conversation_retention") == "disabled":
        archive = {"status": "disabled", "message": "Minimal Work does not bind or snapshot its conversation"}
    if minimal:
        # Write durable guidance before runtime discovery: a missing optional
        # toolchain must not leave a new Work without its shared authoring path.
        initialize_authoring_context(repo, work)
    runtime_dir = work / ("workspace/runtime-selection" if minimal else "workspace/qa/runtime-selection")
    runtime = select_artifact_runtime(runtime_dir)
    write_json(work / "workspace/runtime-environment.json", runtime)
    if thread_id and not minimal and record.get("conversation_retention") != "disabled":
        write_json(pending_binding_path(work), {"schema_version": 1, "thread_id": thread_id,
                   "binding_reason": "Explicit current CODEX_THREAD_ID at Work initialization"})
        try:
            archive = archive_conversation(work, thread_id=thread_id,
                                           binding_reason="Explicit current CODEX_THREAD_ID at Work initialization")
            if archive.get("status") not in {"partial", "error", "retention_policy_invalid"}:
                pending_binding_path(work).unlink(missing_ok=True)
                archive = refresh_conversation_tree(work)
            if archive.get("status") in {"partial", "error", "retention_policy_invalid"}:
                errors.append({"stage": "conversation", "result": archive})
        except (OSError, ValueError, KeyError) as exc:
            errors.append({"stage": "conversation", "error": str(exc)})
    if not minimal:
        from .context_intelligence import recall_context
        try:
            query = record.get("name", "") + "\n" + (work / "input/request.md").read_text(encoding="utf-8-sig")
            lessons = compile_lessons(repo)
            context = recall_context(query, repo, current_work_id=work.name,
                                     validated_lessons=lessons["lessons"])
            context["runtime_capabilities"] = runtime
            write_json(work / "workspace/assistant-context.json", context)
            if context.get("catalog_status") == "partial" or context.get("tool_catalog_error"):
                errors.append({"stage": "context", "catalog_status": context.get("catalog_status"),
                               "tool_catalog_error": context.get("tool_catalog_error")})
        except (OSError, ValueError, KeyError) as exc:
            errors.append({"stage": "context", "error": str(exc)})
    result = {"schema_version": 1, "generated_at": now(), "work_id": work.name,
              "status": "partial" if errors else "ready", "archive": archive, "runtime": runtime, "errors": errors}
    if not minimal:
        write_json(work / "workspace/startup-diagnostics.json", result)
    return result


def closure_evidence(work: Path) -> dict:
    """Capture immutable raw snapshots separately from the live archive manifest."""
    from .conversations import refresh_conversation_tree, _retention_policy
    from .feedback_audit import audit_work_feedback

    binding = retry_pending_binding(work)
    store = work / "workspace/conversations"
    retention_mode, retention_error = _retention_policy(work)
    metadata_only = retention_mode in {"metadata_only", "disabled"}
    retention_reason = "disabled_by_work_policy" if retention_mode == "disabled" else "metadata_only_retention_policy"
    policy_invalid = retention_error is not None
    if policy_invalid:
        refresh = {"status": "retention_policy_invalid", "results": [], "error": retention_error}
    elif metadata_only:
        # Closure must respect the retention decision even if the source trace
        # is still available to the host and the manifest keeps old locators.
        refresh = {"status": "metadata_only", "results": [],
                   "message": "No local raw conversation is retained under metadata_only policy"}
    else:
        refresh = refresh_conversation_tree(work)
    if not policy_invalid:
        retrospective_draft(work)
    feedback = ({"status": "retention_policy_invalid", "warnings": [{"kind": "retention_policy_invalid",
                 "detail": retention_error}]} if policy_invalid else audit_work_feedback(work))
    conversation_coverage = None
    if policy_invalid:
        conversation_coverage = {"status": "retention_policy_invalid", "local_raw_available": False,
                                 "reason": retention_error}
    elif metadata_only:
        bindings = read_json(store / "bindings.json", {})
        bound_threads = bindings.get("bindings", []) if isinstance(bindings, dict) else []
        conversation_coverage = {
            "status": "retention_limited",
            "local_raw_available": False,
            "reason": retention_reason,
            "bound_threads": len(bound_threads) if isinstance(bound_threads, list) else 0,
        }
    records = []
    if not (metadata_only or policy_invalid):
        for path in sorted((work / "workspace/conversations/snapshots").rglob("*.jsonl")):
            records.append(evidence_record(work, path.relative_to(work).as_posix()))
    for relative in ("workspace/learning-events.jsonl", "workspace/feedback-audit/coverage.json"):
        if (work / relative).is_file():
            records.append(evidence_record(work, relative))
    events = load_events(work)
    superseded = {event.get("supersedes") for event in events}
    event_errors = []
    conversation_root = (work / "workspace/conversations").resolve()

    def references_conversation_store(item: dict) -> bool:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            return False
        resolved = (work / item["path"]).resolve()
        # The second check covers hosts with case-insensitive Work volumes.
        actual = str(resolved).casefold()
        root_text = str(conversation_root).casefold()
        return resolved == conversation_root or resolved.is_relative_to(conversation_root) or (
            actual == root_text or actual.startswith(root_text + os.sep))

    for event in events:
        if event["id"] in superseded or event.get("verification") != "passed":
            continue
        if (metadata_only or policy_invalid) and any(
                references_conversation_store(item) for item in event.get("evidence", [])):
            event_errors.append({"event_id": event["id"],
                                 "error": "Conversation snapshot evidence is intentionally unavailable under the retention policy"})
            continue
        if not evidence_valid(work, event.get("evidence", [])):
            event_errors.append({"event_id": event["id"], "error": "Previously passed evidence changed or is missing; update the observation before review"})
        else:
            records.extend(event["evidence"])
    records = list({record["path"]: record for record in records}.values())
    result = {"schema_version": 1, "generated_at": now(), "work_id": work.name,
            "status": "partial" if metadata_only or policy_invalid or refresh.get("status") in {"partial", "error", "retention_policy_invalid"} or binding["status"] in {"pending", "retention_policy_invalid"} or event_errors else "ready",
              "binding_retry": binding,
              "feedback_coverage": feedback,
              "conversation_coverage": conversation_coverage,
              "event_verification_errors": event_errors,
              "archive_status": refresh.get("status"), "archive_results": refresh.get("results", []),
              "records": records, "retrospective_draft": "workspace/retrospective-draft.md",
              "limitations": "Only locally available trace bytes are captured. Sidebar archiving is not customer acceptance."}
    write_json(work / "workspace/qa/closure-evidence.json", result)
    if work.resolve().parent.name == "work" and work.name.isdigit():
        result["project_review"] = {"status": "deferred", "reason": "Request after the final manifest and review metadata are written"}
    else:
        result["project_review"] = {"status": "skipped", "reason": "Custom WorkRoot is outside the canonical workspace project-review queue"}
    write_json(work / "workspace/qa/closure-evidence.json", result)
    return result


def run_maintenance(repo: Path, *, include_model_reviews: bool = True,
                    active_snapshot_interval_seconds: int | None = None) -> dict:
    from .context_intelligence import build_case_index
    from .conversations import refresh_conversation_tree

    repo = repo.resolve()
    active_snapshot_interval = _active_snapshot_interval_seconds(active_snapshot_interval_seconds)
    runtime = repo / "任务知识库/runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(runtime / "maintenance.lock"), timeout=0):
            works, errors, changes = [], [], []
            for work in sorted((repo / "work").glob("[0-9]*"), key=lambda p: int(p.name) if p.name.isdigit() else 0):
                if not work.name.isdigit() or not (work / "work.json").is_file():
                    continue
                try:
                    record = read_json(work / "work.json", {})
                    if isinstance(record, dict) and record.get("storage_profile") == "minimal":
                        works.append({"work_id": work.name, "work_status": record.get("status"),
                                      "archive_status": "disabled", "project_review_request": {"status": "skipped", "reason": "minimal_work_profile"}})
                        continue
                    binding = retry_pending_binding(work)
                    if binding["status"] in {"pending", "retention_policy_invalid"}:
                        errors.append({"work_id": work.name, "stage": "binding", "result": binding})
                    archive = refresh_conversation_tree(
                        work, active_snapshot_interval_seconds=active_snapshot_interval)
                    if archive["status"] == "updated":
                        changes.append({"work_id": work.name, "kind": "conversation_updated"})
                    if archive["status"] in {"error", "partial", "retention_policy_invalid"}:
                        errors.append({"work_id": work.name, "stage": "archive", "result": archive})
                    elif archive["status"] == "unbound" and binding["status"] == "pending":
                        archive = {"status": "pending_binding"}
                    record = read_json(work / "work.json", {})
                    if record.get("status") == "completed":
                        from .project_learning import sync_project_review_request
                        request_sync = sync_project_review_request(repo, work)
                    else:
                        request_sync = {"status": "not_eligible"}
                    if record.get("status") in {"review", "completed"} or archive["status"] == "updated":
                        retrospective_draft(work)
                    works.append({"work_id": work.name, "work_status": record.get("status"),
                                  "archive_status": archive["status"], "project_review_request": request_sync})
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    errors.append({"work_id": work.name, "error": str(exc)})
            lessons = {"lessons": [], "pending": [], "errors": []}
            try:
                lessons = compile_lessons(repo)
            except Exception as exc:
                errors.append({"stage": "lessons", "error": str(exc)})
            try:
                catalog = build_case_index(repo)
                errors.extend({"stage": "catalog", "detail": error} for error in catalog.get("errors", []))
                if catalog.get("status") == "partial" and not catalog.get("errors"):
                    errors.append({"stage": "catalog", "error": "Catalog reports partial results"})
            except Exception as exc:
                errors.append({"stage": "catalog", "error": str(exc)})
            (runtime / "local-refresh-request.json").unlink(missing_ok=True)
    except Timeout:
        if not include_model_reviews:
            write_json(runtime / "local-refresh-request.json", {"requested_at": now(), "reason": "Local archive/index writer is busy; next worker pass must refresh"})
        return {"status": "already_running", "local_refresh": "pending"}
    # Release the archive/index lock before potentially long model calls.
    # Each model pipeline has its own lock and resumable source-bound state.
    # Project retrospection has its own budget and archive cursor. It
    # must not wait behind a backlog of isolated preference messages.
    from .project_learning import run_project_learning
    try:
        project_review = run_project_learning(repo) if include_model_reviews else {"status": "queued", "reason": "background_model_review"}
        if _model_has_errors(project_review):
            errors.append({"stage": "project_review", "result": project_review})
    except Exception as exc:
        project_review = {"status": "needs_attention", "error": str(exc)}
        errors.append({"stage": "project_review", "error": str(exc)})
    # The raw archive/index remains useful if authenticated semantic
    # review is disabled, rate-limited, offline or needs context.
    from .evolution import run_evolution
    try:
        evolution = run_evolution(repo) if include_model_reviews else {"status": "queued", "reason": "background_model_review"}
        if _model_has_errors(evolution):
            errors.append({"stage": "semantic_learning", "result": evolution})
    except Exception as exc:
        evolution = {"status": "needs_attention", "error": str(exc)}
        errors.append({"stage": "semantic_learning", "error": str(exc)})
    # Repair dispatch is independent of the semantic daily budget and
    # of archival success for unrelated old projects.
    from .core_evolution import run_core_evolution
    try:
        core_evolution = run_core_evolution(repo) if include_model_reviews else {"status": "queued", "reason": "background_model_review"}
        if _model_has_errors(core_evolution):
            errors.append({"stage": "core_evolution", "result": core_evolution})
    except Exception as exc:
        core_evolution = {"status": "needs_attention", "error": str(exc)}
        errors.append({"stage": "core_evolution", "error": str(exc)})
    result = {"schema_version": 1, "ran_at": now(), "status": "partial" if errors or lessons["errors"] else "ok",
              "worker_instance": os.environ.get("AIPPT_MAINTENANCE_INSTANCE"),
              "worker_service": os.environ.get("XPC_SERVICE_NAME"),
              "semantic_learning": evolution,
              "project_review": project_review,
              "core_evolution": core_evolution,
              "works": works, "changes": changes, "errors": errors + lessons["errors"],
              "verified_lessons": len(lessons["lessons"]), "pending_lessons": len(lessons["pending"]),
              "catalog_path": "任务知识库/catalog/case-index.json",
              "unbound_work_count": sum(w["archive_status"] == "unbound" for w in works)}
    # Notification file changes only when a problem appears/clears; routine runs stay quiet.
    attention_missing = False
    try:
        previous = read_json(runtime / "attention.json", None)
        if previous is None:
            attention_missing = True
            previous = read_json(runtime / "last-run.json", {})
    except (ValueError, OSError):
        attention_missing = True
        previous = {}
    outstanding = _preserved_model_errors(previous, {
        "project_review": project_review, "semantic_learning": evolution, "core_evolution": core_evolution,
    }, result["errors"])
    problem = {"status": "partial" if result["errors"] or outstanding else "ok",
               "errors": [*result["errors"], *outstanding]}
    # A full worker result must not advertise health=ok merely because another
    # invocation owns a stage lock or its retry/budget window has not opened.
    # Local-only status describes the local refresh; attention still describes
    # all unresolved work, including stages that this command did not execute.
    result["attention_status"] = problem["status"]
    result["preserved_model_errors"] = outstanding
    if include_model_reviews:
        result["status"] = problem["status"]
        result["errors"] = problem["errors"]
    old_problem = {"status": previous.get("status"), "errors": previous.get("errors", [])}
    if ((problem != old_problem and (problem["errors"] or previous.get("errors")))
            or (attention_missing and problem["errors"])):
        write_json(runtime / "attention.json", {"at": now(), **problem})
    write_json(runtime / "last-run.json", result)
    if result["worker_instance"] or result["worker_service"] == "app.aippt.knowledge-maintenance":
        write_json(runtime / "last-worker-run.json", result)
    return result


def wake_maintenance_worker() -> dict:
    """Wake the existing macOS worker without installing or waiting on models."""
    if sys.platform != "darwin":
        return {"status": "pending", "reason": "Run the configured maintenance worker on this host"}
    launchctl = system_launchctl()
    if launchctl is None:
        return {"status": "pending", "reason": "Trusted macOS launchctl bridge is unavailable"}
    service = f"gui/{os.getuid()}/app.aippt.knowledge-maintenance"
    try:
        loaded = subprocess.run([str(launchctl), "print", service], capture_output=True, timeout=10)
        if loaded.returncode:
            return {"status": "pending", "reason": "Existing macOS maintenance worker is not loaded"}
        result = subprocess.run([str(launchctl), "kickstart", service], capture_output=True, timeout=10)
        return {"status": "dispatched" if result.returncode == 0 else "pending", "service": service,
                "reason": "Background worker handles model review; dispatch is not completed analysis"}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "pending", "reason": str(exc)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("initialize")
    init.add_argument("--repo-root", type=Path, required=True)
    init.add_argument("--work-dir", type=Path, required=True)
    init.add_argument("--thread-id")
    close = sub.add_parser("closure")
    close.add_argument("--work-dir", type=Path, required=True)
    for name in ("request-review", "sync-review"):
        request = sub.add_parser(name)
        request.add_argument("--repo-root", type=Path, required=True)
        request.add_argument("--work-dir", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--repo-root", type=Path, required=True)
    run.add_argument("--quiet", action="store_true")
    run.add_argument("--local-only", action="store_true", help="Refresh archive/index and wake the existing background model reviewer")
    args = parser.parse_args()
    # Launchd calls this module directly after installation. Recheck the
    # *running* interpreter here so a later venv replacement cannot make the
    # scheduled archive/model path silently operate through Rosetta.
    runtime_reason = validate_identity(current_process_identity())
    if runtime_reason is not None:
        parser.error("Studio maintenance requires a host-native Python: " + runtime_reason)
    if args.command == "initialize":
        result = initialize_work(args.repo_root.resolve(), args.work_dir.resolve(), thread_id=args.thread_id)
    elif args.command == "closure":
        result = closure_evidence(args.work_dir.resolve())
    elif args.command in {"request-review", "sync-review"}:
        from .project_learning import request_project_review, sync_project_review_request
        action = request_project_review if args.command == "request-review" else sync_project_review_request
        result = action(args.repo_root.resolve(), args.work_dir.resolve())
    else:
        result = run_maintenance(args.repo_root, include_model_reviews=not args.local_only)
        if args.local_only:
            result["background_dispatch"] = wake_maintenance_worker()
            write_json(args.repo_root / "任务知识库/runtime/last-dispatch.json", {"at": now(), **result["background_dispatch"]})
    if not getattr(args, "quiet", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    # The launchd job runs this command with ``--quiet``.  A ``partial``
    # result is a completed, durable maintenance pass with pending evidence or
    # a retryable model-stage problem recorded in ``attention.json``; turning
    # that into a process crash makes the scheduler look broken forever and
    # gives launchd no useful distinction from an unhandled exception.  Keep a
    # direct interactive invocation non-zero so a human still sees attention,
    # while the background worker exits cleanly and retries on its next tick.
    should_fail = result.get("status") == "error"
    if result.get("status") == "partial" and not getattr(args, "quiet", False):
        should_fail = True
    if getattr(args, "local_only", False) and result.get("status") == "already_running":
        should_fail = True
    if should_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
