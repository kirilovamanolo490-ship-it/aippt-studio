"""Verified whole-project episodes as core diagnosis inputs, never automatic bugs.

Project demand is evidence of the intended result. The synthesis is an assistant
interpretation of that project's attempts, not a user report of a software fault.
Classification, reproduction, isolated tests and independent review stay in the
existing core evolution path.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import project_learning as pl
from .learning import read_json

VERSION = "project-repair-v1"
_VERIFIED: dict = {}


def _local(root: Path, relative: str, *, strict: bool = True) -> Path:
    path = (root / relative).resolve(strict=strict)
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("Project repair receipt escaped its store")
    return path


def _snapshot(path: Path, expected: str) -> None:
    info = path.stat()
    identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    key = (str(path), expected)
    if _VERIFIED.get(key) == identity:
        return
    if pl._file_hash(path) != expected:
        raise ValueError("Project repair raw snapshot differs from its archive SHA256")
    after = path.stat()
    if identity != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("Project repair snapshot changed while being verified")
    if len(_VERIFIED) >= 256:
        _VERIFIED.clear()
    _VERIFIED[key] = identity


def _bound_sources(work: Path, root: Path, receipts: list[dict]) -> None:
    """Authenticate every reviewed source against this Work's actual archive."""
    store = work / "workspace/conversations"
    manifest = read_json(store / "manifest.json", {})
    bound = {}
    for thread_id, thread in manifest.get("threads", {}).items():
        for snapshot in thread.get("snapshots", []):
            raw = _local(store, snapshot["raw_path"], strict=False)
            index = _local(store, snapshot["derived_path"] + "/record-index.json", strict=False)
            bound[(str(raw), snapshot["sha256"])] = (thread_id, thread.get("conversation_role", "user_conversation"), index)
    indexes = {}
    for receipt in receipts:
        for identifier, source in receipt["sources"].items():
            if source.get("source_id") != identifier:
                raise ValueError("Project source identifier differs from its receipt key")
            if source["kind"] == "artifact":
                path = Path(source["path"]).resolve(strict=True)
                if not path.is_relative_to((root / "evidence").resolve()) or path.name != source["sha256"] + ".json":
                    raise ValueError("Project artifact is not an immutable review evidence file")
                original = Path(source["original_path"]).resolve()
                if not original.is_relative_to(work.resolve()):
                    raise ValueError("Project artifact belongs to another Work")
                continue  # _validate re-reads the artifact SHA and exact quote.
            if source["kind"] != "record":
                raise ValueError("Unsupported project repair evidence kind")
            raw = Path(source["raw_path"]).resolve(strict=True)
            key = (str(raw), source["snapshot_sha256"])
            if key not in bound:
                raise ValueError("Project record snapshot is absent from its Work archive")
            thread, role, index = bound[key]
            if source["thread_id"] != thread or source["conversation_role"] != role:
                raise ValueError("Project record conversation role or thread binding changed")
            if key not in indexes:
                _snapshot(raw, source["snapshot_sha256"])
                value = read_json(index)
                rows, offset = value["records"], 0
                for number, row in enumerate(rows, 1):
                    if row["line"] != number or row["byte_offset"] != offset or row["byte_length"] <= 0:
                        raise ValueError("Project archive index lacks contiguous record coverage")
                    offset += row["byte_length"]
                if offset != raw.stat().st_size or value["raw_bytes"] != offset:
                    raise ValueError("Project archive index byte coverage changed")
                with raw.open("rb") as stream:
                    header = json.loads(stream.readline(262144))
                if header.get("type") != "session_meta" or header.get("payload", {}).get("id") != thread:
                    raise ValueError("Project archive session identity differs from its binding")
                indexes[key] = rows
            rows = indexes[key]
            if not 1 <= source["line"] <= len(rows) or any(
                source[field] != rows[source["line"] - 1][field]
                for field in ("line", "byte_offset", "byte_length")
            ):
                raise ValueError("Project evidence locator differs from its archive index")


def _synthesis_chain(root: Path, receipt: dict, work_id: str, references: list[dict],
                     segments: list[dict], *, active: set | None = None) -> list[dict]:
    """Check links to every actual input receipt, including partial syntheses."""
    active = set() if active is None else active
    coverage = receipt["coverage"]
    before, included, total = (coverage[key] for key in ("segments_before", "segments_included", "segments_total"))
    if (any(type(value) is not int for value in (before, included, total)) or min(before, included) < 0
            or total != len(references) or before + included > total):
        raise ValueError("Project synthesis input coverage range is inconsistent")
    actual = receipt["synthesis_contract"]["input_episodes"]
    links = pl.synthesis_input_receipts(receipt)
    prior_rows = [r for r in links if r["origin"] == "previous_synthesis"]
    for ref in links:
        if ref["origin"] == "segment":
            original = references[ref["segment_number"] - 1]
            if ref["source_receipt_path"] != original["path"] or ref["source_receipt_sha256"] != original["sha256"]:
                raise ValueError("Project synthesis direct receipt link differs from its actual reviewed segment")
    chain, expected = [], []
    if before:
        prior_refs = {(r["source_receipt_path"], r["source_receipt_sha256"]) for r in prior_rows}
        if len(prior_refs) != 1:
            raise ValueError("Previous synthesis receipt cannot be located; earlier project coverage needs review")
        key = next(iter(prior_refs))
        if key in active:
            raise ValueError("Project synthesis receipt chain contains a cycle")
        source = pl._receipt(_local(root, key[0]), key[1])
        if source.get("work_id") != work_id or source.get("phase") != "synthesis":
            raise ValueError("Project synthesis input belongs to a different Work or phase")
        prior_coverage = source["coverage"]
        if prior_coverage["segments_before"] + prior_coverage["segments_included"] != before:
            raise ValueError("Previous synthesis does not end at this continuation's segment boundary")
        expected.extend(pl._episode_inputs(source["review"], key[1], origin="previous_synthesis", source_receipt_path=key[0]))
        chain.append(source)
        chain.extend(_synthesis_chain(root, source, work_id, references, segments, active=active | {key}))
    elif prior_rows:
        raise ValueError("First synthesis cannot claim an earlier synthesis input")
    for index in range(before, before + included):
        ref, source = references[index], segments[index]
        expected.extend(pl._episode_inputs(source["review"], ref["sha256"], origin="segment",
            source_receipt_path=ref["path"], segment_number=index + 1))
        chain.append(source)
    if actual != expected:
        raise ValueError("Project synthesis input episodes do not cover its actual segment receipt chain")
    return chain


def _completed(repo: Path, work_id: str, item: dict) -> tuple[dict, Path, list[dict]]:
    root = pl.directory(repo)
    work = pl._work(repo, work_id)
    if item.get("status") != "review_complete" or not item.get("scan_complete"):
        raise ValueError("Project synthesis is not complete")
    if not item.get("segments") or item.get("synthesis_segments") != len(item["segments"]):
        raise ValueError("Project synthesis does not cover all segment receipts")
    sources = pl._sources(work)
    if pl._signature(work, sources) != item["signature"]:
        raise ValueError("Project gained new archive or closure evidence; synthesis must be refreshed")
    for source in sources:
        _snapshot(Path(source["raw_path"]), source["snapshot_sha256"])
    reference = item["synthesis"]
    path = _local(root, reference["path"])
    receipt = pl._receipt(path, reference["sha256"])
    coverage = receipt["coverage"]
    if (receipt.get("phase") != "synthesis" or receipt.get("work_id") != work_id
            or receipt.get("project_signature") != item["signature"]
            or not coverage.get("final_synthesis") or coverage.get("segments_total") != len(item["segments"])
            or coverage.get("segments_before", 0) + coverage.get("segments_included", 0) != len(item["segments"])):
        raise ValueError("Project synthesis identity or final coverage is inconsistent")
    segments = [pl._receipt(_local(root, ref["path"]), ref["sha256"]) for ref in item["segments"]]
    for segment in segments:
        if segment.get("phase") != "segment" or segment.get("work_id") != work_id:
            raise ValueError("Project segment receipt belongs to a different project or phase")
    chain = _synthesis_chain(root, receipt, work_id, item["segments"], segments)
    _bound_sources(work, root, [*segments, *chain, receipt])
    for value in [*segments, *[value for value in chain if value["phase"] == "segment"]]:
        # Receipt validation must honor its immutable evidence contract.  New
        # fragment-ID receipts need their host-derived anchors, while legacy
        # literal-quote receipts remain independently replayable.
        pl._validate_receipt(value, phase="segment")
    for value in chain:
        if value["phase"] == "synthesis":
            pl.validated_current_synthesis(value, require_final=False)
    source_versions = {key: pl._hash(source) for value in segments for key, source in value["sources"].items()}
    if any(source_versions.get(key) != pl._hash(source) for key, source in receipt["sources"].items()):
        raise ValueError("Project synthesis source differs from its reviewed segment evidence")
    pl.validated_current_synthesis(receipt)
    ids = [episode["episode_id"] for episode in receipt["review"]["episodes"]]
    if any(not identifier.strip() for identifier in ids) or len(ids) != len(set(ids)):
        raise ValueError("Project synthesis episode identifiers must be unique and nonempty")
    return receipt, path, segments


def _priority(repo: Path, work_id: str, signature: str) -> str:
    return pl.project_review_priority(repo, work_id, signature)


def _candidate(work_id: str, item: dict, receipt: dict, path: Path, episode: dict, *, priority_at: str = "") -> dict:
    identifier = pl._hash([VERSION, work_id, episode["episode_id"]])[:24]
    return {"source_kind": "project_synthesis", "candidate_id": identifier,
        "input_sha256": pl._hash([VERSION, item["signature"], item["synthesis"]["sha256"], episode]),
        "project_work_id": work_id, "project_episode_id": episode["episode_id"],
        "title": episode["problem"][:160], "observed_problem": episode["problem"],
        "next_verification": "Compare the complete project's attempts and source evidence with the current shared core call chain; classify before proposing a reproducing code test.",
        "diagnostic_basis": "Assistant project synthesis; original user quotes express intended project behavior, not a reported software defect.",
        "defect_established": False, "source_events": [], "evidence": episode["evidence"],
        "receipt": str(path), "receipt_sha256": item["synthesis"]["sha256"],
        "accepted_at": receipt.get("accepted_at", ""), "priority_at": priority_at,
        "source_timestamp": max((s.get("timestamp") or "" for s in receipt["sources"].values()), default="")}


def project_repair_candidates(repo: Path) -> dict:
    repo = Path(repo).resolve(); root = pl.directory(repo)
    candidates, errors, coverage = [], [], []
    try:
        state = read_json(root / "state.json", {"works": {}})
        if not isinstance(state, dict) or not isinstance(state.get("works"), dict):
            raise ValueError("Project review state is invalid")
    except (OSError, ValueError, TypeError) as exc:
        return {"candidates": [], "errors": [{"source_kind": "project_synthesis", "error": str(exc)}], "coverage": []}
    for work_id, item in state["works"].items():
        try:
            if not isinstance(item, dict):
                raise ValueError("Project review state entry is invalid")
            coverage.append({"work_id": work_id, "status": item.get("status"), "eligible": item.get("status") == "review_complete"})
            if item.get("status") != "review_complete":
                continue
            receipt, path, _ = _completed(repo, work_id, item)
            priority = _priority(repo, work_id, item["signature"])
            candidates.extend(_candidate(work_id, item, receipt, path, episode, priority_at=priority) for episode in receipt["review"]["episodes"])
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, RecursionError) as exc:
            errors.append({"source_kind": "project_synthesis", "work_id": work_id, "error": str(exc)})
    return {"candidates": candidates, "errors": errors, "coverage": coverage}


def build_project_repair_context(candidate: dict, *, repo: Path, max_context_chars: int = 16000) -> dict:
    """Re-read the authority; candidate indexes and saved model packets are not it."""
    from .repair_context import _safe_text
    result = {"schema_version": 1, "source_kind": "project_synthesis", "candidate_id": candidate.get("candidate_id"),
        "status": "invalid", "verification_errors": [], "source_events": [], "context_records": [], "omissions": [],
        "source_verification": "complete synthesis and segment receipt hashes, bound archive snapshots, exact record/artifact bytes and every episode/attempt quote",
        "semantic_limits": ["Project requirements are not user reports of a core fault.",
            "Episode causes remain synthesis inferences; assistant claims do not prove fixes or visual acceptance.",
            "Use core_defect/project_revision/needs_context/already_covered diagnosis; this context authorizes no code or project mutation."],
        "limits": {"max_context_chars": max_context_chars, "emitted_chars": 0}}
    try:
        if not 1 <= max_context_chars <= 120000:
            raise ValueError("Project repair context exceeds the bounded evidence contract")
        work_id = candidate["project_work_id"]
        item = read_json(pl.directory(repo) / "state.json")["works"][work_id]
        receipt, path, segments = _completed(repo, work_id, item)
        episode = next(e for e in receipt["review"]["episodes"] if e["episode_id"] == candidate["project_episode_id"])
        current = _candidate(work_id, item, receipt, path, episode)
        if any(candidate.get(key) != current[key] for key in ("candidate_id", "input_sha256", "receipt", "receipt_sha256")):
            raise ValueError("Project repair candidate is stale or differs from its authoritative synthesis")
        remaining = max_context_chars
        def safe(value):
            nonlocal remaining
            if isinstance(value, str):
                clean, redactions = _safe_text(value); length = min(remaining, len(clean)); remaining -= length
                if redactions or length < len(clean):
                    result["omissions"].append({"reason": "redacted" if redactions else "context_character_limit"})
                return clean[:length]
            if isinstance(value, list): return [safe(v) for v in value]
            if isinstance(value, dict): return {k: safe(v) for k, v in value.items()}
            return value
        # The selected episode and ALL attempts precede optional project context.
        result["episode"] = safe(episode)
        refs = [*episode["evidence"], *[ref for attempt in episode["attempts"] for ref in attempt["evidence"]]]
        for ref in {pl._hash(ref): ref for ref in refs}.values():
            source = receipt["sources"][ref["source_id"]]
            result["context_records"].append({"source_id": ref["source_id"], "role": source["role"], "kind": source["kind"],
                "quote": safe(ref["quote"]), "quote_sha256": pl._hash(ref["quote"]),
                "source": {key: source[key] for key in ("raw_path", "snapshot_sha256", "record_sha256", "thread_id", "conversation_role",
                    "line", "byte_offset", "byte_length", "shared_conversation", "parent_thread_id", "path", "sha256", "original_path", "verification_scope") if key in source},
                "causality": "not_established"})
        result["project_summary"] = safe(receipt["review"]["summary"])
        result["project_conditional_lessons"] = safe(receipt["review"]["conditional_lessons"])
        result["other_episodes"] = safe([{key: e[key] for key in ("episode_id", "problem", "why_repeated", "cause_confidence", "outcome_level", "outcome_reason")}
                                       for e in receipt["review"]["episodes"] if e["episode_id"] != episode["episode_id"]])
        result["project_limits"] = safe(receipt["review"]["limitations"])
        result.update(status="partial" if result["omissions"] else "ready", receipt=str(path), receipt_sha256=item["synthesis"]["sha256"],
                      segment_receipts_verified=len(segments), coverage=receipt["coverage"], project_signature=item["signature"])
        result["limits"]["emitted_chars"] = max_context_chars - remaining
        result["context_fingerprint"] = pl._hash([VERSION, current["input_sha256"], max_context_chars])
    except (OSError, ValueError, KeyError, TypeError, IndexError, StopIteration, AttributeError, RecursionError) as exc:
        result["verification_errors"].append({"error": str(exc)})
    return result
