"""Bring scoped project experience into planning and actual page review.

Historical guidance stays outside slide copy. A retrieval hit is a question for
the current assistant; only its page-bound disposition records actual adoption.
"""
from __future__ import annotations

import json
from pathlib import Path


def workspace_for_job(root: Path) -> tuple[Path | None, str | None]:
    root = root.resolve()
    for parent in root.parents:
        if parent.name.isdigit() and parent.parent.name == "work" and (parent / "work.json").is_file():
            return parent.parent.parent, parent.name
    from .config import WORKSPACE_ROOT
    if root.is_relative_to(WORKSPACE_ROOT / "jobs"):
        return WORKSPACE_ROOT, None
    # Isolated fixtures/foreign job roots cannot import this user's history.
    return None, None


def project_lesson_context(root: Path, query: str, *, limit: int = 3) -> dict:
    repo, work_id = workspace_for_job(root)
    if repo is None:
        return {"status": "no_workspace", "lessons": [], "errors": []}
    from .project_learning import load_project_lessons
    result = load_project_lessons(repo, query, limit=limit, current_work_id=work_id)
    # Keep the packet stable across unchanged worker runs. Only selected,
    # source-validated guidance and its identity belong in the review binding.
    return {"status": result.get("status", "ready"), "lessons": result.get("lessons", []),
            "errors": result.get("errors", []),
            "instruction": "Judge applicability against this task's current source and audience. Record applied, not_applicable or revise with concrete page observations. Historical client facts and wording are not this deck's content."}


def plan_query(plan, context: dict | None = None) -> str:
    data = plan.model_dump(mode="json") if hasattr(plan, "model_dump") else plan
    # Exclude old retrieved policy text so a lesson cannot match itself.
    return json.dumps({"title": data.get("title"), "slides": data.get("slides", []),
                       "brief": (context or {}).get("planning/brief.json", {})}, ensure_ascii=False)


def validate_lesson_dispositions(packet: dict, request: dict) -> tuple[list, list]:
    from .expression_review import _nonempty
    lessons = {row["lesson_id"]: row for row in packet.get("project_lessons", {}).get("lessons", [])}
    rows = request.get("lesson_dispositions", [])
    if (not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows)
            or len(rows) != len(lessons) or {row.get("lesson_id") for row in rows} != set(lessons)):
        raise ValueError("Address every retrieved project lesson exactly once in this task's actual context")
    pages = {row["slide_id"]: row for row in packet["slides"]}
    review_rows = {row["slide_id"]: row for row in request["slides"]}
    issues = []
    for row in rows:
        if set(row) != {"lesson_id", "decision", "reason", "slide_ids", "verification"}:
            raise ValueError("Lesson disposition requires its ID, decision, reason, slide_ids and verification")
        if row["decision"] not in {"applied", "not_applicable", "revise"}:
            raise ValueError("Lesson decision must be applied, not_applicable or revise")
        _nonempty(row["reason"], "lesson applicability reason")
        _nonempty(row["verification"], "lesson page observation")
        slide_ids = row["slide_ids"]
        if (not isinstance(slide_ids, list) or not all(isinstance(value, str) for value in slide_ids)
                or len(slide_ids) != len(set(slide_ids)) or not set(slide_ids) <= set(pages)):
            raise ValueError("Lesson verification must cite unique actual pages in this review")
        if row["decision"] != "not_applicable" and not slide_ids:
            raise ValueError("Applied or unresolved lessons require actual reviewed page IDs")
        if row["decision"] == "revise":
            if any(review_rows[slide_id]["decision"] == "ready" for slide_id in slide_ids):
                raise ValueError("A page with an unresolved project lesson cannot be ready")
            issues.append({"code": "PROJECT_LESSON_REQUIRES_REVISION", "lesson_id": row["lesson_id"],
                           "slide_ids": slide_ids, "assessment": row["verification"]})
    return rows, issues
