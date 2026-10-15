"""Classify the local Work corpus without turning client history into a template.

The workspace can contain dozens of real projects, legacy experiments, and
Studio-core maintenance tasks.  A directory listing is not enough to decide
that a prior project is a reusable example: it may be unfinished, copied from
an old machine, have changed after QA, or contain client-specific facts that
must never migrate into a new deck.

This module produces a small local-only index made from safe metadata:

* canonical Work identity and lifecycle state;
* hash-validated modern presentation-intent records when they exist;
* observed legacy route/input-file families, clearly labelled as observation;
* the strict QA provenance inventory already used by project learning; and
* hash-bound retrospective bytes plus independently verified original user
  acceptance, kept as separate authorities.

It deliberately does *not* index request text, client names, slide copy,
speaker notes, source filenames, asset paths, deck imagery, or old job names.
Those data stay inside their source Work.  A recalled case supplies an
editorial category and an evidence boundary, never material to copy.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Iterable

from .knowledge import context_concepts
from .models import ChangeMode, InputRole, JobBrief, PresentationForm, ProductionRoute, VisualSystemMode, utc_now
from .python_runtime import current_process_identity, validate_identity
from .presentation_intent import (
    requests_bounded_revision,
    requests_template_adaptation,
    select_presentation_intent,
)


SCHEMA_VERSION = 2
INDEX_RELATIVE_PATH = Path("任务知识库/catalog/work-corpus-index.json")
MAX_JSON_BYTES = 1_000_000
MAX_REQUEST_CLASSIFICATION_BYTES = 256 * 1024
MAX_TRACKED_FILES_PER_WORK = 2_048
MAX_STUDIO_JOBS_PER_WORK = 64
MAX_INPUT_FILES_PER_WORK = 1_024

_FORM_VALUES = {item.value for item in PresentationForm}
_MODE_VALUES = {item.value for item in ChangeMode}
_VISUAL_VALUES = {item.value for item in VisualSystemMode}
_ROUTE_VALUES = {item.value for item in ProductionRoute}
_ROLE_VALUES = {item.value for item in InputRole}
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")

_PRESENTATION_WORDS = ("ppt", "pptx", "演示", "课件", "汇报", "答辩", "竞赛", "路演", "美化", "版式")
_CORE_WORDS = ("aippt", "ai ppt", "studio", "工作台")
_PLATFORM_WORDS = ("mac", "macos", "windows", "跨平台", "兼容", "运行时", "runtime", "powerpoint")
_UPSTREAM_WORDS = ("github", "上游", "upstream", "adapter", "适配器")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_file(path: Path, *, maximum: int | None = None) -> str:
    before = path.stat()
    if maximum is not None and before.st_size > maximum:
        raise ValueError("file_exceeds_read_limit")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns
    ):
        raise ValueError("file_changed_during_read")
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".work-corpus-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _safe_child(root: Path, relative: str | Path) -> Path:
    path = (root / relative).resolve()
    if path == root.resolve() or not path.is_relative_to(root.resolve()):
        raise ValueError("path_escapes_workspace")
    return path


def _read_json(path: Path, *, maximum: int = MAX_JSON_BYTES) -> dict:
    data = path.read_bytes()
    if len(data) > maximum:
        raise ValueError("json_exceeds_read_limit")
    value = json.loads(data.decode("utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("json_must_be_object")
    return value


def _canonical_work_record(work: Path) -> tuple[dict, str]:
    """Read a Work only when physical and declared identities agree."""

    work = work.resolve()
    if work.parent.name != "work" or not work.name.isdigit():
        raise ValueError("work_path_is_not_canonical")
    path = work / "work.json"
    if not path.is_file() or path.is_symlink():
        raise ValueError("work_metadata_missing")
    record = _read_json(path)
    if str(record.get("work_id", "")) != work.name:
        raise ValueError("work_metadata_identity_mismatch")
    return record, _sha_bytes(path.read_bytes())


def _file_group(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in {".ppt", ".pptx", ".potx", ".ppsx"}:
        return "presentation"
    if suffix in {".doc", ".docx", ".rtf", ".odt"}:
        return "document"
    if suffix in {".pdf"}:
        return "pdf"
    if suffix in {".xlsx", ".xls", ".csv", ".tsv"}:
        return "spreadsheet"
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".tif", ".tiff", ".heic"}:
        return "image"
    if suffix in {".mp4", ".mov", ".avi", ".mp3", ".wav", ".m4a"}:
        return "media"
    if suffix in {".md", ".txt", ".json", ".yaml", ".yml", ".xml"}:
        return "text_or_structured"
    return "other"


def _safe_files(root: Path, *, limit: int) -> tuple[list[Path], list[str]]:
    """List ordinary local files and report bounded omissions without paths."""

    if not root.is_dir() or root.is_symlink():
        return [], []
    paths: list[Path] = []
    omissions: list[str] = []
    try:
        candidates = sorted(root.rglob("*"))
    except OSError:
        return [], ["tree_unreadable"]
    for path in candidates:
        if path.is_symlink():
            omissions.append("symlink_excluded")
            continue
        if not path.is_file():
            continue
        if len(paths) >= limit:
            omissions.append("file_limit")
            break
        paths.append(path)
    return paths, sorted(set(omissions))


def _input_profile(work: Path) -> tuple[dict, list[Path], list[str]]:
    profile: dict[str, dict] = {}
    all_files: list[Path] = []
    omissions: list[str] = []
    for directory in ("source", "reference"):
        files, missing = _safe_files(work / "input" / directory, limit=MAX_INPUT_FILES_PER_WORK)
        all_files.extend(files)
        counts = Counter(_file_group(path) for path in files)
        profile[directory] = {"file_count": len(files), "types": dict(sorted(counts.items())),
                              "complete": "file_limit" not in missing}
        omissions.extend(f"{directory}:{item}" for item in missing)
    request = work / "input" / "request.md"
    request_info = {"present": request.is_file() and not request.is_symlink()}
    if request_info["present"]:
        try:
            request_info["sha256"] = _sha_file(request, maximum=MAX_JSON_BYTES)
        except (OSError, ValueError):
            request_info["hash_status"] = "unavailable"
    return {"request": request_info, **profile}, all_files, sorted(set(omissions))


def _tracking_files(work: Path, input_files: Iterable[Path]) -> tuple[list[dict], list[str]]:
    """Return only stat metadata for cache invalidation; no names leave this function."""

    candidates: list[Path] = [work / "work.json", *input_files]
    request = work / "input" / "request.md"
    if request.is_file() and not request.is_symlink():
        candidates.append(request)
    workspace = work / "workspace"
    for root, limit in ((workspace / "qa", MAX_TRACKED_FILES_PER_WORK),
                        (workspace / "studio-jobs", MAX_TRACKED_FILES_PER_WORK)):
        files, _ = _safe_files(root, limit=limit)
        for path in files:
            relative = path.relative_to(root).as_posix()
            if root.name == "studio-jobs":
                # Studio QA, current plan and job records define safe corpus
                # state.  Media/previews and arbitrary user scripts do not.
                if not (
                    relative.endswith("/job.json")
                    or relative.endswith("/planning/brief.json")
                    or relative.endswith("/planning/intake-decision.json")
                    or relative.endswith("/planning/deck-plan.json")
                    or "/qa/" in f"/{relative}"
                    or "/reviews/" in f"/{relative}"
                ):
                    continue
            candidates.append(path)
    # An approved retrospective's current hash changes its reuse authority.
    try:
        record, _ = _canonical_work_record(work)
        locator = record.get("task_knowledge_entry")
        if isinstance(locator, str) and locator.strip():
            entry = _safe_child(work.parent.parent, locator)
            if entry.is_file() and not entry.is_symlink():
                candidates.append(entry)
    except (OSError, ValueError, TypeError):
        pass
    rows: list[dict] = []
    omissions: list[str] = []
    seen: set[Path] = set()
    for path in sorted({path.resolve() for path in candidates if path.is_file() and not path.is_symlink()}):
        if path in seen:
            continue
        seen.add(path)
        if len(rows) >= MAX_TRACKED_FILES_PER_WORK:
            omissions.append("tracked_file_limit")
            break
        try:
            stat = path.stat()
            # The locator itself is intentionally not retained.  Its ordinal
            # state is enough to invalidate a cached classification.
            rows.append({"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns})
        except OSError:
            omissions.append("tracked_file_unreadable")
    return rows, sorted(set(omissions))


def _job_observation(work: Path) -> dict:
    jobs = work / "workspace" / "studio-jobs"
    if not jobs.is_dir() or jobs.is_symlink():
        return {"job_count": 0, "route": {"value": None, "confidence": "unavailable"},
                "input_roles": {}, "omissions": []}
    roots = [path for path in sorted(jobs.iterdir()) if path.is_dir() and not path.is_symlink() and not path.name.startswith(".")]
    omissions = ["studio_job_limit"] if len(roots) > MAX_STUDIO_JOBS_PER_WORK else []
    routes: set[str] = set(); roles: Counter[str] = Counter(); malformed = 0
    for root in roots[:MAX_STUDIO_JOBS_PER_WORK]:
        record = root / "job.json"
        if not record.is_file() or record.is_symlink():
            continue
        try:
            value = _read_json(record)
            if value.get("id") != root.name:
                raise ValueError("job_identity_mismatch")
            route = value.get("route")
            if route in _ROUTE_VALUES:
                routes.add(route)
            input_roles = value.get("input_roles", {})
            if isinstance(input_roles, dict):
                roles.update(str(item) for item in input_roles.values() if str(item) in _ROLE_VALUES)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            malformed += 1
    if malformed:
        omissions.append("unreadable_or_invalid_job_record")
    if len(routes) == 1:
        route = {"value": next(iter(routes)), "confidence": "legacy_observed_job_record"}
    elif len(routes) > 1:
        route = {"value": None, "confidence": "ambiguous_multiple_job_routes"}
    else:
        route = {"value": None, "confidence": "unavailable"}
    return {"job_count": min(len(roots), MAX_STUDIO_JOBS_PER_WORK), "route": route,
            "input_roles": dict(sorted(roles.items())), "omissions": sorted(set(omissions))}


def _historical_procedural_signals(work: Path) -> dict:
    """Count repeated one-off operation families without retaining script names.

    Older Work folders legitimately contain helper scripts.  A filename can
    reveal that PDF export, packaging, or QA was repeatedly reimplemented, but
    it cannot prove the script ran, that it is portable, or that its logic
    should be copied into a new task.  This signal is therefore a maintenance
    prioritisation hint only; core capabilities and their own receipts remain
    the sole execution authority.
    """

    root = work / "workspace"
    counts: Counter[str] = Counter()
    scanned = 0; complete = True
    if not root.is_dir() or root.is_symlink():
        return {"counts": {}, "scanned_script_count": 0, "complete": True,
                "boundary": "No workspace script inventory is available."}
    for base, directories, files in os.walk(root, followlinks=False):
        directories[:] = sorted(
            name for name in directories
            if name not in {"node_modules", ".venv", ".venv-macos", ".git", "__pycache__"}
            and not (Path(base) / name).is_symlink()
        )
        for filename in sorted(files):
            path = Path(base) / filename
            if path.is_symlink() or path.suffix.casefold() not in {".ps1", ".applescript", ".py", ".sh", ".js", ".mjs", ".cjs"}:
                continue
            scanned += 1
            if scanned > MAX_TRACKED_FILES_PER_WORK:
                complete = False
                break
            stem = path.stem.casefold()
            if "pdf" in stem and any(word in stem for word in ("export", "convert", "render", "powerpoint")):
                counts["pdf_export"] += 1
            if any(word in stem for word in ("publish", "finalize", "package", "deliver")):
                counts["delivery_packaging"] += 1
            if any(word in stem for word in ("qa", "quality", "roundtrip", "validate", "verify")):
                counts["presentation_qa"] += 1
        if not complete:
            break
    return {
        "counts": dict(sorted(counts.items())),
        "scanned_script_count": min(scanned, MAX_TRACKED_FILES_PER_WORK),
        "complete": complete,
        "boundary": "Static historical script-family signal only. It is not a runtime instruction, an execution receipt, portability evidence, or authorization to copy a Work script.",
    }


def _validated_intents(work: Path) -> tuple[list[dict], list[str]]:
    """Read only the same hash-validated intent chain trusted by archive review."""

    try:
        from .project_learning import _studio_intake_artifacts

        artifacts, omissions = _studio_intake_artifacts(work)
    except (OSError, ValueError, TypeError, ImportError):
        return [], ["validated_intent_inventory_unavailable"]
    values: list[dict] = []
    for artifact in artifacts:
        if artifact.get("role") != "studio_intake_decision":
            continue
        try:
            projected = json.loads(str(artifact.get("text", "")))
            intent = projected.get("presentation_intent", {})
            if not isinstance(intent, dict):
                raise ValueError
            form, mode, visual = (intent.get("presentation_form"), intent.get("change_mode"), intent.get("visual_system"))
            if form not in _FORM_VALUES or mode not in _MODE_VALUES or visual not in _VISUAL_VALUES:
                raise ValueError
            values.append({"presentation_form": form, "change_mode": mode, "visual_system": visual})
        except (TypeError, ValueError, json.JSONDecodeError):
            omissions.append({"reason": "unreadable_projected_intent"})
    reasons = sorted({str(item.get("reason", "unverified_studio_intake")) for item in omissions if isinstance(item, dict)})
    return values, reasons


def _direct_studio_repair(text: str) -> bool:
    """Separate a Studio repair from a deck merely mentioning the tool.

    Most legacy requests say that Studio should *make* a deck.  That alone is
    not a Studio-core case.  A request such as ``使用 AI PPT Studio 优化第 3
    页模板`` still belongs to the presentation corpus, even though it contains
    both the tool name and a change verb.  Require the Studio itself to be the
    apparent target of maintenance, and reject the common tool-operates-on-a-
    deck construction before assigning a case to the maintenance corpus.
    """

    folded = text.casefold()
    identity = r"(?:aippt|ai\s*ppt\s*studio|ai\s*ppt|studio\s*工作台|工作台)"
    action = r"(?:整改|改造|优化|修复|重构|升级|迁移|适配|兼容|完善)"
    core_target = r"(?:整体|核心|架构|代码(?:库)?|工作流|运行时|归档|知识库|技能(?:目录)?|平台|核心逻辑|维护)"
    deck_target = r"(?:pptx?|演示(?:稿|文稿)?|课件|汇报|页面|幻灯片|模板|版式|文案|封面)"
    deck_operation = r"(?:制作|生成|创建|做|输出|修改|美化|优化|调整|重排|排版|重构|编辑)"

    # This narrow pattern describes the tool being used on a client deck.  It
    # must win over generic words such as "优化", otherwise ordinary template
    # revisions pollute the Studio-core history.
    if re.search(rf"{identity}.{{0,24}}{deck_operation}.{{0,48}}{deck_target}", folded, re.S):
        return False
    if re.search(rf"(?:使用|用|通过|利用|调用|让).{{0,16}}{identity}", folded, re.S) and re.search(deck_target, folded):
        return False

    if re.search(rf"{identity}.{{0,32}}{core_target}", folded, re.S):
        return True
    # "优化 AI PPT Studio" makes Studio the grammatical target.  It remains
    # a core signal when an old title omits a more specific architecture noun.
    if re.search(rf"{action}.{{0,20}}{identity}", folded, re.S):
        return True
    return bool(re.search(rf"{identity}.{{0,32}}{action}.{{0,32}}{core_target}", folded, re.S))


def _case_tags(title: str, request_text: str = "") -> list[str]:
    title_folded = title.casefold()
    request_folded = request_text.casefold()
    explicit_core = _direct_studio_repair(title) or _direct_studio_repair(request_text)
    # A Work title is an explicit task label.  Treat an AI PPT Studio title as
    # core only when it also carries a maintenance marker; a normal request
    # that merely says "use Studio" stays a presentation case.
    title_core = _direct_studio_repair(title)
    is_core = explicit_core or title_core
    combined = "\n".join((title_folded, request_folded))
    tags = []
    if is_core:
        tags.append("studio_core")
    # A direct platform-maintenance title is also useful even if it predates
    # structured Studio naming.  Mentioning macOS inside a slide request is
    # insufficient to convert a client deck into a platform repair.
    direct_platform = any(word in title_folded for word in _PLATFORM_WORDS) and any(
        word in title_folded for word in ("兼容", "迁移", "运行时", "修复", "检查", "适配")
    )
    if (is_core or direct_platform) and any(word in combined for word in _PLATFORM_WORDS):
        tags.append("platform_compatibility")
    direct_upstream = any(word in title_folded for word in ("github", "上游", "upstream")) and any(
        word in title_folded for word in ("接入", "集成", "刷新", "适配", "调用")
    )
    if (is_core or direct_upstream) and any(word in combined for word in _UPSTREAM_WORDS):
        tags.append("upstream_integration")
    return tags


def _core_case_type(title: str, request_text: str = "") -> str:
    tags = _case_tags(title, request_text)
    # A Studio-core repair can include Mac compatibility or upstream routing,
    # but it must not enter the client-deck corpus as a presentation case.
    if "studio_core" in tags:
        return "studio_core"
    if "platform_compatibility" in tags:
        return "platform_compatibility"
    if "upstream_integration" in tags:
        return "upstream_integration"
    folded = "\n".join((title, request_text)).casefold()
    if any(word in folded for word in ("word", "docx", "演讲稿", "文稿")) and not any(word in folded for word in _PRESENTATION_WORDS):
        return "document_or_content"
    return "presentation_work"


def _request_for_classification(work: Path) -> tuple[str | None, str]:
    """Read a bounded instruction channel transiently, never index its text.

    Legacy Work names are often too shallow to distinguish a classroom deck
    from a government briefing or a template revision.  ``input/request.md``
    is the Work's dedicated instruction channel, so it can improve category
    recognition.  The bytes are consumed only inside this function's caller:
    callers retain a category, a confidence label, and the already-recorded
    anonymous request hash, never the text itself.
    """

    path = work / "input" / "request.md"
    try:
        if not path.is_file() or path.is_symlink():
            return None, "legacy_work_name"
        data = path.read_bytes()
        if len(data) > MAX_REQUEST_CLASSIFICATION_BYTES:
            return None, "request_exceeds_classification_limit"
        return data.decode("utf-8-sig", errors="replace"), "bounded_work_request"
    except OSError:
        return None, "request_unavailable"


def _legacy_intent(name: str, request_text: str | None, source: str) -> dict:
    """Derive a cautious legacy label from bounded instructions before name.

    This is categorisation, not a retroactive intake decision.  It never adds
    preservation scope or an executable route to a historical Work.
    """

    decision_text = request_text if request_text and request_text.strip() else name

    try:
        derived = select_presentation_intent(JobBrief(goal=""), {}, request_text=decision_text)
        form = derived.presentation_form.value
    except (TypeError, ValueError):
        form = PresentationForm.GENERAL.value
    folded = decision_text.casefold()
    if requests_bounded_revision(decision_text) or re.search(r"第\s*\d{1,3}\s*页", decision_text):
        legacy_shape = "bounded_revision_signal"
    elif any(word in folded for word in ("修订", "修改", "美化", "优化", "调整", "字体", "改版")):
        legacy_shape = "refinement_signal"
    elif requests_template_adaptation(decision_text) or "模板" in decision_text or "母版" in decision_text:
        legacy_shape = "template_signal"
    else:
        legacy_shape = "unknown"
    explicit_new_composition = any(word in folded for word in ("新建模板", "全新模板", "全新风格", "完全重新设计", "从零设计", "新视觉体系"))
    # ``select_presentation_intent`` defaults to new composition when a
    # modern source-role contract is absent.  That default is correct for a
    # *new* Work, but it would be false history here; retain a mode only when
    # the old instruction explicitly made the operation clear.
    if requests_bounded_revision(decision_text):
        inferred_mode = derived.change_mode.value if 'derived' in locals() else ChangeMode.UNCLASSIFIED.value
    elif explicit_new_composition:
        inferred_mode = ChangeMode.NEW_COMPOSITION.value
    else:
        inferred_mode = None
    return {
        "presentation_form": {"value": form, "confidence": f"{source}_inference"},
        # A legacy title must not backfill a modern change-mode contract: the
        # title can say "美化" even when the correct execution was a new deck.
        "change_mode": {"value": inferred_mode, "confidence": f"{source}_inference" if inferred_mode else "legacy_not_backfilled", "legacy_shape": legacy_shape},
        "visual_system": {"value": VisualSystemMode.CREATE_NEW.value if explicit_new_composition else None,
                          "confidence": f"{source}_inference" if explicit_new_composition else "legacy_not_backfilled"},
        "status": "legacy_request_inferred" if source == "bounded_work_request" else "legacy_inferred",
    }


def _classify(work: Path, record: dict) -> dict:
    title = str(record.get("name", ""))
    request_text, request_source = _request_for_classification(work)
    # The local title remains a fallback for old core-maintenance Works whose
    # request was never captured.  It is not retained in the index.
    case_type = _core_case_type(title, request_text or "")
    tags = _case_tags(title, request_text or "")
    intents, omissions = _validated_intents(work)
    unique = {(row["presentation_form"], row["change_mode"], row["visual_system"]) for row in intents}
    if len(unique) == 1:
        form, mode, visual = next(iter(unique))
        result = {
            "case_type": case_type,
            "presentation_form": {"value": form, "confidence": "hash_validated_studio_intent"},
            "change_mode": {"value": mode, "confidence": "hash_validated_studio_intent"},
            "visual_system": {"value": visual, "confidence": "hash_validated_studio_intent"},
            "status": "structured",
        }
    elif len(unique) > 1:
        result = {
            "case_type": case_type,
            "presentation_form": {"value": None, "confidence": "ambiguous_multiple_valid_intents"},
            "change_mode": {"value": None, "confidence": "ambiguous_multiple_valid_intents"},
            "visual_system": {"value": None, "confidence": "ambiguous_multiple_valid_intents"},
            "status": "ambiguous_structured_intent",
        }
    else:
        result = {"case_type": case_type, **_legacy_intent(title, request_text, request_source)}
    if case_type != "presentation_work":
        # A core-maintenance Work may mention a client portfolio or a test deck
        # while fixing Studio.  It is not a precedent for a future deck form.
        result.update({
            "presentation_form": {"value": None, "confidence": "not_applicable_to_non_presentation_work"},
            "change_mode": {"value": None, "confidence": "not_applicable_to_non_presentation_work"},
            "visual_system": {"value": None, "confidence": "not_applicable_to_non_presentation_work"},
        })
    result["capability_tags"] = tags
    # Do not retain job IDs or raw omission details: counts distinguish a
    # trustworthy modern intent from old/partial data without exposing history.
    result["validated_intent"] = {"count": len(intents), "omission_count": len(omissions),
                                  "legacy_request_source": request_source if not intents else None}
    return result


def _approved_retrospective(work: Path, record: dict) -> dict:
    """Authenticate retrospective bytes without mistaking them for acceptance."""

    expected = record.get("task_knowledge_entry_sha256")
    locator = record.get("task_knowledge_entry")
    if isinstance(expected, str) and _SHA256.fullmatch(expected) and isinstance(locator, str) and locator.strip():
        try:
            entry = _safe_child(work.parent.parent, locator)
            if entry.is_file() and not entry.is_symlink() and _sha_file(entry, maximum=MAX_JSON_BYTES) == expected.lower():
                return {"state": "current_hash_validated"}
        except (OSError, ValueError, TypeError):
            pass
        return {"state": "declared_hash_unavailable_or_changed"}
    return {"state": "no_current_retrospective_hash"}


def _approval(work: Path, record: dict, quality: dict) -> dict:
    """Keep lifecycle metadata, retrospective bytes and user evidence separate.

    ``work.json.user_approved`` is a closure control, not a quoted original
    user message.  Likewise, a current hash only proves which retrospective
    bytes were approved for storage.  The corpus may use either only after the
    project-learning reader has independently verified an original user
    acceptance clause in the current final synthesis.
    """

    completion = {
        "completed": record.get("status") == "completed",
        "user_approved": record.get("user_approved") is True,
    }
    retrospective = _approved_retrospective(work, record)
    try:
        from .project_learning import current_explicit_user_acceptance

        original = current_explicit_user_acceptance(work.parent.parent, work)
    except (OSError, ValueError, TypeError, ImportError):
        original = {
            "state": "explicit_user_acceptance_evidence_unavailable",
            "accepted_episode_count": 0,
            "review_phase": None,
        }
    explicit = original.get("state") == "explicit_original_user_acceptance"
    if explicit and retrospective["state"] == "current_hash_validated":
        state = "explicit_original_user_acceptance_with_current_retrospective"
    elif explicit:
        state = "explicit_original_user_acceptance"
    elif completion["completed"] or completion["user_approved"] or retrospective["state"] == "current_hash_validated":
        state = "completion_metadata_without_explicit_original_user_acceptance"
    else:
        state = "no_explicit_original_user_acceptance"
    return {
        "state": state,
        "completion_metadata": completion,
        "retrospective": retrospective,
        "original_user_acceptance": original,
        "current_qa_present": bool(quality.get("current_work_bound_records")),
    }


def _quality(work: Path) -> dict:
    """Reuse the project-learning QA gate rather than interpreting QA filenames."""

    try:
        from .project_learning import _qa_artifact_inventory

        _artifacts, inventory = _qa_artifact_inventory(work)
        descriptors = inventory.get("artifacts", [])
        current = sum(bool(item.get("verification_passed")) for item in descriptors if isinstance(item, dict))
        historical = sum(not bool(item.get("verification_passed")) for item in descriptors if isinstance(item, dict))
        invalid = sum(item.get("status") in {"invalid_json", "unreadable"} for item in descriptors if isinstance(item, dict))
        if current:
            state = "current_work_bound_qa"
        elif descriptors:
            state = "historical_or_unbound_qa_only"
        else:
            state = "no_known_qa"
        return {"state": state, "current_work_bound_records": current,
                "historical_or_unbound_records": historical, "invalid_records": invalid,
                "omission_count": len(inventory.get("omissions", []))}
    except (OSError, ValueError, TypeError, ImportError):
        return {"state": "qa_inventory_unavailable", "current_work_bound_records": 0,
                "historical_or_unbound_records": 0, "invalid_records": 0, "omission_count": 1}


def _reuse(approval: dict, quality: dict) -> dict:
    explicit = approval.get("original_user_acceptance", {}).get("state") == "explicit_original_user_acceptance"
    retrospective_current = approval.get("retrospective", {}).get("state") == "current_hash_validated"
    if explicit and quality["current_work_bound_records"]:
        level = "verified_case"
        eligible = True
    elif explicit and retrospective_current:
        level = "accepted_lesson"
        eligible = True
    elif approval.get("completion_metadata", {}).get("completed") or approval.get("completion_metadata", {}).get("user_approved"):
        level = "completion_metadata_history"
        eligible = False
    else:
        level = "not_reusable"
        eligible = False
    return {
        "level": level,
        "eligible_for_future_context": eligible,
        "boundary": (
            "Only reusable process/category guidance may be recalled. Current materials, audience, facts, assets, "
            "accepted objects and visual decisions remain authoritative for the new Work."
        ),
    }


def _build_work_row(root: Path, work: Path) -> dict:
    record, metadata_sha = _canonical_work_record(work)
    input_profile, input_files, input_omissions = _input_profile(work)
    tracking_rows, tracking_omissions = _tracking_files(work, input_files)
    fingerprint = _sha_bytes(json.dumps({"state": tracking_rows,
                                         "omissions": sorted(set([*input_omissions, *tracking_omissions]))},
                                        sort_keys=True, separators=(",", ":")).encode())
    classification = _classify(work, record)
    execution = _job_observation(work)
    procedural = _historical_procedural_signals(work)
    quality = _quality(work)
    approval = _approval(work, record, quality)
    reuse = _reuse(approval, quality)
    return {
        "work_id": work.name,
        "work_metadata_sha256": metadata_sha,
        "source_fingerprint": fingerprint,
        "classification": classification,
        "input_profile": input_profile,
        "execution": execution,
        "historical_procedural_signals": procedural,
        "quality_evidence": quality,
        "acceptance": approval,
        "reuse": reuse,
        "limitations": [
            "This row contains categories and evidence states only; it excludes Work names, client identifiers, request text, source content, assets, paths and slide copy.",
            "Legacy Work names can supply a cautious category label but never backfill a modern route, preservation contract or design decision.",
            "A current Work-bound QA receipt proves only the named output chain; it is separate from user acceptance and visual suitability for a new request.",
        ],
    }


def _statistics(rows: list[dict]) -> dict:
    def values(path: tuple[str, ...]) -> Counter[str]:
        counter: Counter[str] = Counter()
        for row in rows:
            value = row
            for key in path:
                value = value.get(key, {}) if isinstance(value, dict) else {}
            if isinstance(value, str) and value:
                counter[value] += 1
        return counter
    procedural: Counter[str] = Counter()
    for row in rows:
        counts = row.get("historical_procedural_signals", {}).get("counts", {})
        if isinstance(counts, dict):
            procedural.update({str(key): int(value) for key, value in counts.items()
                               if isinstance(value, int) and value > 0})
    return {
        "work_count": len(rows),
        "case_types": dict(sorted(values(("classification", "case_type")).items())),
        "capability_tags": dict(sorted(Counter(
            tag for row in rows for tag in row.get("classification", {}).get("capability_tags", []) if isinstance(tag, str)
        ).items())),
        "presentation_forms": dict(sorted(values(("classification", "presentation_form", "value")).items())),
        "presentation_form_confidence": dict(sorted(values(("classification", "presentation_form", "confidence")).items())),
        "change_modes": dict(sorted(values(("classification", "change_mode", "value")).items())),
        "routes": dict(sorted(values(("execution", "route", "value")).items())),
        "historical_procedural_signals": dict(sorted(procedural.items())),
        "quality_evidence": dict(sorted(values(("quality_evidence", "state")).items())),
        "approval_states": dict(sorted(values(("acceptance", "state")).items())),
        "reuse_levels": dict(sorted(values(("reuse", "level")).items())),
        "context_eligible_case_count": sum(bool(row.get("reuse", {}).get("eligible_for_future_context")) for row in rows),
    }


def _index_path(root: Path, index_path: Path | None) -> Path:
    return (index_path or root / INDEX_RELATIVE_PATH).expanduser().resolve()


def build_work_corpus_index(project_root: Path, *, index_path: Path | None = None, force: bool = False) -> dict:
    """Build a local, source-safe Work corpus index.

    ``force`` is retained for operational clarity.  The current implementation
    rescans compact metadata on every pass because Work QA and archive evidence
    can be invalidated by a changed byte without changing a lifecycle state.
    The scan never mutates a Work.
    """

    del force  # See the deliberate correctness-first note above.
    root = project_root.expanduser().resolve()
    destination = _index_path(root, index_path)
    rows: list[dict] = []
    errors: list[dict] = []
    work_root = root / "work"
    candidates = sorted((path for path in work_root.glob("[0-9]*") if path.is_dir() and path.name.isdigit()),
                        key=lambda path: int(path.name)) if work_root.is_dir() else []
    for work in candidates:
        if not (work / "work.json").is_file():
            continue
        try:
            rows.append(_build_work_row(root, work))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            errors.append({"work_id": work.name, "stage": "classification", "reason": str(error)[:160]})
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "workspace_root": str(root),
        "status": "partial" if errors else "ready",
        "index_path": INDEX_RELATIVE_PATH.as_posix() if destination == (root / INDEX_RELATIVE_PATH).resolve() else str(destination),
        "statistics": _statistics(rows),
        "works": rows,
        "errors": errors,
        "limitations": [
            "The index is local-only and never writes inside a source Work.",
            "It is a category and evidence map, not a visual review, customer acceptance substitute, source archive, asset catalog or design template.",
            "Only verified_case and accepted_lesson entries with current final-project-review evidence are eligible for automatic future context. Completion metadata remains navigation-only until a quoted original user acceptance validates.",
        ],
    }
    _write_json(destination, payload)
    return payload


def _load_index(root: Path, index_path: Path | None, *, refresh: bool) -> tuple[dict, Path]:
    destination = _index_path(root, index_path)
    if refresh or not destination.is_file():
        return build_work_corpus_index(root, index_path=destination), destination
    value = _read_json(destination, maximum=32_000_000)
    if value.get("workspace_root") != str(root):
        raise ValueError("work_corpus_index_belongs_to_another_workspace")
    # A cached corpus created before a stricter evidence rule must never keep
    # feeding old eligibility decisions into a new Work.  Rebuild locally;
    # source Works remain read-only throughout the migration.
    if value.get("schema_version") != SCHEMA_VERSION:
        return build_work_corpus_index(root, index_path=destination), destination
    return value, destination


def _query_profile(query: str) -> dict:
    try:
        form = select_presentation_intent(JobBrief(goal=""), {}, request_text=query).presentation_form.value
    except (TypeError, ValueError):
        form = PresentationForm.GENERAL.value
    folded = query.casefold()
    tags = _case_tags(query, query)
    case_type = _core_case_type(query, query) if tags else None
    if any(word in folded for word in ("扁平", "扫描", "重建", "可编辑重构")):
        mode = ChangeMode.FAITHFUL_RECONSTRUCTION.value
    elif any(word in folded for word in ("视觉替代", "图片型", "image_based")):
        mode = ChangeMode.VISUAL_ALTERNATE.value
    elif requests_bounded_revision(query):
        mode = ChangeMode.LOCAL_PATCH.value
    elif requests_template_adaptation(query):
        mode = ChangeMode.TEMPLATE_RELAYOUT.value
    elif any(word in folded for word in ("新建模板", "从零", "全新设计", "新视觉体系")):
        mode = ChangeMode.NEW_COMPOSITION.value
    else:
        mode = None
    route_hints = []
    if any(word in folded for word in ("模板", "母版", "原稿", "保留页")):
        route_hints.append(ProductionRoute.TEMPLATE.value)
    if any(word in folded for word in ("扁平", "扫描", "重建")):
        route_hints.append(ProductionRoute.REBUILD.value)
    if any(word in folded for word in ("视觉替代", "图片型", "image_based")):
        route_hints.append(ProductionRoute.VISUAL.value)
    return {"presentation_form": form if form != PresentationForm.GENERAL.value else None,
            "change_mode": mode, "case_type": case_type, "capability_tags": tags, "route_hints": sorted(set(route_hints)),
            "concepts": sorted(context_concepts(query)),
            "boundary": "This is a retrieval hint only. The current Work intake remains the only source of an approved presentation-intent decision."}


def _score_case(query: dict, row: dict) -> tuple[int, list[str]]:
    classification = row.get("classification", {})
    execution = row.get("execution", {})
    score = 0; reasons: list[str] = []
    if query.get("case_type") and classification.get("case_type") == query["case_type"]:
        score += 48; reasons.append("case_type")
    tag_overlap = set(query.get("capability_tags", [])) & set(classification.get("capability_tags", []))
    if tag_overlap:
        score += 14 * len(tag_overlap); reasons.extend(sorted(tag_overlap))
    requested_form = query.get("presentation_form")
    case_form = classification.get("presentation_form", {}).get("value")
    if requested_form and case_form == requested_form:
        score += 36; reasons.append("presentation_form")
    requested_mode = query.get("change_mode")
    case_mode = classification.get("change_mode", {}).get("value")
    if requested_mode and case_mode == requested_mode:
        score += 24; reasons.append("change_mode")
    if execution.get("route", {}).get("value") in set(query.get("route_hints", [])):
        score += 16; reasons.append("observed_route")
    # Concepts are a deliberately small, generic vocabulary; no client nouns
    # or original request wording are retained in the corpus.
    case_concepts = set()
    if classification.get("case_type") == "platform_compatibility":
        case_concepts.add("mac_platform")
    if classification.get("case_type") == "upstream_integration":
        case_concepts.add("upstream_integration")
    if case_mode == ChangeMode.LOCAL_PATCH.value:
        case_concepts.add("revision_scope")
    if case_mode == ChangeMode.TEMPLATE_RELAYOUT.value:
        case_concepts.add("template_preservation")
    overlap = set(query.get("concepts", [])) & case_concepts
    if overlap:
        score += 8 * len(overlap); reasons.extend(sorted(overlap))
    return score, reasons


def _public_case(row: dict, score: int, reasons: list[str]) -> dict:
    classification = row["classification"]
    return {
        "work_id": row["work_id"],
        "match": {"score": score, "signals": reasons},
        "classification": {
            "case_type": classification["case_type"],
            "capability_tags": classification.get("capability_tags", []),
            "presentation_form": classification["presentation_form"],
            "change_mode": classification["change_mode"],
            "visual_system": classification["visual_system"],
            "status": classification["status"],
        },
        "input_profile": row["input_profile"],
        "execution": {"route": row["execution"]["route"], "input_roles": row["execution"]["input_roles"]},
        "quality_evidence": row["quality_evidence"],
        "acceptance": row["acceptance"],
        "reuse": row["reuse"],
        "advisory_only": True,
    }


def recall_work_corpus(query: str, project_root: Path, *, index_path: Path | None = None,
                       current_work_id: str | None = None, limit: int = 3, refresh: bool = True) -> dict:
    """Return a few safe, evidence-qualified category matches for a new task."""

    root = project_root.expanduser().resolve()
    index, destination = _load_index(root, index_path, refresh=refresh)
    profile = _query_profile(query)
    maximum = max(1, min(int(limit), 5))
    candidates = []
    excluded = Counter()
    for row in index.get("works", []):
        if not isinstance(row, dict) or str(row.get("work_id")) == str(current_work_id):
            continue
        reuse = row.get("reuse", {})
        if not reuse.get("eligible_for_future_context"):
            excluded[str(reuse.get("level", "unknown"))] += 1
            continue
        score, reasons = _score_case(profile, row)
        if score <= 0:
            continue
        candidates.append((score, row["work_id"], _public_case(row, score, reasons)))
    candidates.sort(key=lambda item: (-item[0], int(item[1]) if str(item[1]).isdigit() else 10**9))
    selected = [item[2] for item in candidates[:maximum]]
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "matched" if selected else ("partial" if index.get("status") == "partial" else "no_match"),
        "generated_at": utc_now(),
        "index_path": str(destination),
        "index_status": index.get("status"),
        "index_sha256": _sha_file(destination, maximum=32_000_000),
        "query_profile": profile,
        "reusable_cases": selected,
        "excluded_case_counts": dict(sorted(excluded.items())),
        "advisory_only": True,
        "precedence": ["current_user_request", "current_evidence", "current_work_intake", "recalled_case_category"],
        "reuse_boundary": "Recalled cases supply only category/process prompts. They never import a prior client's facts, assets, wording, output, accepted objects or visual system into the current Work.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "recall"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--index-path", type=Path)
    parser.add_argument("--query", default="")
    parser.add_argument("--current-work-id")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    # ``build`` writes a shared archive-derived index and this module is
    # documented as a standalone command. Do not let a translated Python
    # bypass the physical-host guard used by Studio's other public entrypoints.
    # Parsing first preserves ``--help`` as a recovery path.
    runtime_reason = validate_identity(current_process_identity())
    if runtime_reason is not None:
        parser.error("Studio Work corpus requires a host-native Python: " + runtime_reason)
    if args.command == "build":
        result = build_work_corpus_index(args.project_root, index_path=args.index_path, force=args.force)
        result = {key: value for key, value in result.items() if key != "works"}
    else:
        result = recall_work_corpus(args.query, args.project_root, index_path=args.index_path,
                                    current_work_id=args.current_work_id, limit=args.limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
