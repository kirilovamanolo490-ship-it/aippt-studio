"""Require an explicit library reuse outcome for Work-bound new compositions.

The deep index is discovery. A new deck with relevant candidates cannot move
to a prototype solely because a retrieval file exists: it needs a bound
library asset or a current, page-specific reason for not using one.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .models import DeckPlan, JobRecord, ProductionRoute
from .storage import job_dir, load_job, locked_job, read_json, read_model, write_json


_CANDIDATES = Path("planning/library-reuse-candidates.json")
_DECISION = Path("planning/library-reuse-decision.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def plan_message_signature(slides: list[Any]) -> str:
    """Hash the page messages that drive retrieval, excluding later asset refs."""
    values = [{"id": slide.id, "title": slide.title, "points": list(slide.points),
               "purpose": slide.purpose, "page_role": slide.page_role, "layout": slide.layout}
              for slide in slides]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _active(job: JobRecord, plan: DeckPlan) -> bool:
    profile = plan.planning_strategy.get("visual_style_profile")
    return (job.work_request is not None and job.route == ProductionRoute.NATIVE
            and isinstance(profile, dict) and profile.get("application") == "baseline_new_composition")


def _candidate_paths(packet: dict[str, Any]) -> dict[str, set[int]]:
    paths: dict[str, set[int]] = {}
    for row in packet.get("candidates", []):
        if not isinstance(row, dict) or not isinstance(row.get("path"), str):
            continue
        count = row.get("page_count")
        if isinstance(count, int) and count > 0:
            paths[row["path"]] = set(range(1, count + 1))
    for row in [*packet.get("asset_candidates", []), *packet.get("source_page_candidates", [])]:
        if not isinstance(row, dict):
            continue
        path, page = row.get("source_pptx"), row.get("source_slide")
        if isinstance(path, str) and isinstance(page, int) and page > 0:
            paths.setdefault(path, set()).add(page)
    return paths


def library_reuse_block_reason(job: JobRecord, root: Path, plan: DeckPlan) -> str | None:
    """Return a concrete blocker when a new Work silently skipped its library."""
    if not _active(job, plan):
        return None
    from .config import WORKSPACE_ROOT
    if not (WORKSPACE_ROOT / "模板库/CATALOG.jsonl").is_file():
        return None
    packet_path = root / _CANDIDATES
    if not packet_path.is_file():
        return "新建稿缺少当前模板库逐页检索结果；请重新生成 deck plan 或运行 library-reuse。"
    try:
        packet = read_json(packet_path)
    except (OSError, ValueError):
        return "模板库检索结果损坏；请重新运行 library-reuse。"
    if not isinstance(packet, dict):
        return "模板库检索结果格式无效；请重新运行 library-reuse。"
    if packet.get("plan_message_signature") != plan_message_signature(plan.slides):
        return "模板库检索结果对应的页内容已变化；请重新运行 library-reuse 再选择素材。"
    if not packet.get("candidates") and not packet.get("asset_candidates") and not packet.get("source_page_candidates"):
        return None

    from .html_deck import _asset_info
    refs = [ref for slide in plan.slides for ref in slide.asset_refs if ref.startswith("library:")]
    if refs and all(_asset_info(root, ref) is not None for ref in refs):
        return None
    if refs:
        return "已绑定的模板库素材账本或文件失效；请修复素材后重新构建。"

    decision_path = root / _DECISION
    if decision_path.is_file():
        try:
            decision = read_json(decision_path)
        except (OSError, ValueError):
            decision = None
        if (isinstance(decision, dict) and decision.get("decision") == "no_suitable_candidate"
                and decision.get("plan_sha256") == _sha256(root / "planning/deck-plan.json")
                and decision.get("candidate_sha256") == _sha256(packet_path)
                and isinstance(decision.get("reviewed_pages"), list) and decision["reviewed_pages"]
                and isinstance(decision.get("reason"), str) and len(decision["reason"].strip()) >= 10):
            return None
    return ("模板库已找到候选，但当前新稿尚未实际套用模板或绑定库内图片/矢量素材。"
            "请检查来源整页并用 library-template/library-media 应用；若确无合适候选，"
            "用 library-reuse-decision 记录具体页码、观察和原因。")


@locked_job
def record_no_suitable_library_candidate(job_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Record a source-page-specific no-fit exception for the current plan."""
    job = load_job(job_id)
    root = job_dir(job_id)
    plan_path = root / "planning/deck-plan.json"
    packet_path = root / _CANDIDATES
    if not plan_path.is_file() or not packet_path.is_file():
        raise ValueError("current deck plan and library candidates are required")
    plan = read_model(plan_path, DeckPlan)
    if not _active(job, plan):
        raise ValueError("library reuse decision applies only to Work-bound new compositions")
    packet = read_json(packet_path)
    if not isinstance(packet, dict):
        raise ValueError("library candidates are invalid")
    if request.get("decision") != "no_suitable_candidate":
        raise ValueError("decision must be no_suitable_candidate")
    reason = request.get("reason")
    if not isinstance(reason, str) or len(reason.strip()) < 10:
        raise ValueError("a specific no-fit reason of at least 10 characters is required")
    observations = request.get("reviewed_pages")
    if not isinstance(observations, list) or not 1 <= len(observations) <= 10:
        raise ValueError("reviewed_pages must contain 1 to 10 actual source-page observations")
    allowed = _candidate_paths(packet)
    reviewed = []
    for item in observations:
        if not isinstance(item, dict):
            raise ValueError("each reviewed page must be an object")
        path, slide, finding = item.get("source_path"), item.get("source_slide"), item.get("finding")
        if not isinstance(path, str) or not isinstance(slide, int) or slide not in allowed.get(path, set()):
            raise ValueError("reviewed page must name a current candidate path and source slide")
        if not isinstance(finding, str) or len(finding.strip()) < 8:
            raise ValueError("each reviewed page needs a concrete visual or content observation")
        reviewed.append({"source_path": path, "source_slide": slide, "finding": finding.strip()})
    decision = {"schema": "aippt.library-reuse-decision.v1", "decision": "no_suitable_candidate",
                "reason": reason.strip(), "reviewed_pages": reviewed,
                "plan_sha256": _sha256(plan_path), "candidate_sha256": _sha256(packet_path),
                "status": "recorded_for_current_plan"}
    write_json(root / _DECISION, decision)
    return decision
