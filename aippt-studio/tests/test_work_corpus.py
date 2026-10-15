from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from aippt_studio import context_intelligence as ci
from aippt_studio import project_learning as project_learning
from aippt_studio import work_corpus as corpus


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _work(root: Path, identifier: str, *, name: str, status: str = "completed",
          user_approved: bool = True, request: str = "") -> Path:
    work = root / "work" / identifier
    (work / "input" / "source").mkdir(parents=True)
    (work / "input" / "reference").mkdir()
    (work / "workspace").mkdir()
    (work / "input" / "request.md").write_text(request, encoding="utf-8")
    _write_json(work / "work.json", {
        "work_id": identifier,
        "name": name,
        "status": status,
        "user_approved": user_approved,
    })
    return work


def _set_hash_bound_retrospective(root: Path, work: Path) -> None:
    """Bind retrospective bytes only; this is deliberately not user evidence."""

    entry = root / "任务知识库" / "entries" / "2026" / f"work-{work.name}.md"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("# 经用户确认的复盘\n\n只保留过程经验。\n", encoding="utf-8")
    record = json.loads((work / "work.json").read_text(encoding="utf-8"))
    record.update({
        "task_knowledge_entry": entry.relative_to(root).as_posix(),
        "task_knowledge_entry_sha256": _sha(entry),
    })
    _write_json(work / "work.json", record)


def _archived_user_message(work: Path, text: str, *, thread: str = "acceptance-thread") -> Path:
    """Create one immutable, Work-bound original user record for a regression."""

    values = [
        {"type": "session_meta", "payload": {"id": thread}},
        {"type": "response_item", "timestamp": "2026-09-22T00:00:00Z", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}],
        }},
    ]
    lines = [(json.dumps(value, ensure_ascii=False) + "\n").encode() for value in values]
    data = b"".join(lines)
    digest = hashlib.sha256(data).hexdigest()
    store = work / "workspace" / "conversations"
    raw = store / "snapshots" / thread / f"{digest}.jsonl"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(data)
    derived = store / "derived" / thread / digest
    offset = 0
    rows = []
    for number, (value, line) in enumerate(zip(values, lines), 1):
        payload = value.get("payload", {})
        rows.append({"line": number, "byte_offset": offset, "byte_length": len(line), "type": value["type"],
                     "payload_type": payload.get("type"), "role": payload.get("role")})
        offset += len(line)
    _write_json(derived / "record-index.json", {"raw_bytes": len(data), "records": rows})
    _write_json(store / "manifest.json", {"threads": {
        thread: {
            "source_state": "archived_snapshot", "conversation_role": "user_conversation", "shared_conversation": False,
            "latest_sha256": digest,
            "snapshots": [{"sha256": digest, "bytes": len(data), "raw_path": raw.relative_to(store).as_posix(),
                           "derived_path": derived.relative_to(store).as_posix(), "previous_sha256": None,
                           "append_only_from_previous": False}],
        },
    }})
    return raw


def _acceptance_review(packet: dict) -> dict:
    """A deterministic reviewer that labels only the quoted user clause accepted."""

    if packet["phase"] == "synthesis":
        blocks = ([packet["previous_synthesis"]] if packet.get("previous_synthesis") else []) + [
            block["review"] for block in packet["segments"]
        ]
        episodes = copy.deepcopy([episode for block in blocks for episode in block["episodes"]])
        lessons = copy.deepcopy([lesson for block in blocks for lesson in block["conditional_lessons"]])
        # Stored receipts retain host-derived source/quote fields for audit;
        # a new reviewer invocation must select the current packet's IDs only.
        for episode in episodes:
            groups = [episode["evidence"]] + [attempt["evidence"] for attempt in episode["attempts"]]
            for references in groups:
                for index, reference in enumerate(references):
                    references[index] = project_learning._fragment_reference(
                        packet, reference["source_id"], reference["quote"]
                    )
        for lesson in lessons:
            lesson["evidence"] = [project_learning._fragment_reference(packet, reference["source_id"], reference["quote"])
                                   for reference in lesson["evidence"]]
        return {
            "summary": "最终综合回执保留原始用户确认及其范围。",
            "episodes": episodes,
            "conditional_lessons": lessons,
            "limitations": ["该回归只验证原始用户确认的证据链。"],
            "episode_dispositions": [{
                "input_episode_ref": item["input_episode_ref"], "disposition": "retained",
                "reason": "保留带原始用户确认引用的项目结局。", "target_episode_ids": [item["episode_id"]],
            } for item in packet["input_episodes"]],
        }
    source = next(record for record in packet["records"] if record["role"] == "user")
    reference = project_learning._fragment_reference(packet, source["source_id"], source["text"])
    episode = {
        "episode_id": source["source_id"], "problem": "正式汇报的图文边缘与页面关系需要完成核对。",
        "why_repeated": "必须把用户的确认和原始对话证据保持绑定。", "cause_confidence": "observed",
        "attempts": [{"action": "记录原始确认", "result": "用户已明确确认当前结果。", "why_rejected": "无", "evidence": [reference]}],
        "outcome_level": "user_accepted", "outcome_reason": "只按引用的原始用户确认判断。", "evidence": [reference],
    }
    lesson = {
        "title": "图片边缘与正式汇报版面的最终核对", "applies_when": "图文页面完成候选稿后。",
        "exceptions": "当前任务的素材、受众和已接受对象仍需单独确认。",
        "action": "在实际页面比例检查图像轮廓、底板与文字表达，再保留原始确认的范围。",
        "verify_on_page": "查看实际整页，确认没有遮挡、孤立说明或非正式表达。",
        "outcome_level": "user_accepted", "evidence": [reference],
    }
    return {"summary": "当前片段包含可核验的原始用户确认。", "episodes": [episode],
            "conditional_lessons": [lesson], "limitations": ["该回归不代表视觉验收。"]}


def _add_current_explicit_user_acceptance(root: Path, work: Path) -> Path:
    raw = _archived_user_message(work, "这版正式汇报可以了，其他页面不用再改。")
    _write_json(root / "任务知识库" / "evolution" / "config.json", {
        "project_reviews": {"enabled": True, "records_per_chunk": 8},
    })
    for _ in range(8):
        result = project_learning.run_project_learning(
            root, reviewer=lambda _repo, _run, packet, _config: _acceptance_review(packet), work_ids=[work.name]
        )
        assert result["status"] in {"progress", "review_complete"}, result
        if result["status"] == "review_complete":
            return raw
    raise AssertionError("project review did not reach a final synthesis")


def _verified_studio_case(root: Path, identifier: str = "11") -> Path:
    """Make one modern current-Work QA chain without client material in its index."""

    work = _work(
        root,
        identifier,
        name="CLIENT_NAME_SHOULD_NOT_LEAK 竞赛答辩案例",
        request="REQUEST_SECRET：这是竞赛答辩，保留确认模板后重新组织证据与可行性。",
    )
    _set_hash_bound_retrospective(root, work)
    request = work / "input" / "request.md"
    request_sha = _sha(request)
    job = work / "workspace" / "studio-jobs" / "verified-case"
    snapshot = job / "planning" / "intake-requests" / f"{request_sha}.md"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(request.read_bytes())
    binding = {
        "work_id": identifier,
        "source_relative_path": "input/request.md",
        "source_sha256": request_sha,
        "snapshot_path": f"planning/intake-requests/{request_sha}.md",
        "snapshot_sha256": request_sha,
        "bound_at": "2026-09-22T00:00:00+00:00",
    }
    intent = {
        "schema_version": 1,
        "change_mode": "template_relayout",
        "visual_system": "preserve_existing",
        "presentation_form": "competition_defense",
        "preservation": {"target_slide_numbers": [], "preserve_non_target_slides": False,
                           "allow_auto_cover": True, "allow_auto_outline": False, "allow_auto_conclusion": False},
        "source": "automatic", "change_mode_source": "automatic", "presentation_form_source": "automatic",
        "visual_system_source": "automatic", "confidence": "explicit",
        "reason": "Only the structured category is relevant to this regression.", "evidence": ["work_request"],
        "request_sha256": request_sha, "decided_at": "2026-09-22T00:00:01+00:00",
    }
    source = job / "input" / "source" / "source.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("SOURCE_SECRET：不得进入分类索引的客户事实。", encoding="utf-8")
    source_sha = _sha(source)
    _write_json(job / "job.json", {
        "id": "verified-case", "route": "template", "work_request": binding,
        "source_ids": ["source-1"], "input_roles": {"source-1": "content"},
    })
    _write_json(job / "normalized" / "sources" / "source-1.json", {
        "id": "source-1", "stored_path": "input/source/source.txt", "sha256": source_sha,
    })
    _write_json(job / "evidence-ledger.json", {"sources": [{"source_id": "source-1", "sha256": source_sha}]})
    _write_json(job / "planning" / "intake-decision.json", {
        "schema_version": 1, "brief_path": "planning/brief.json", "work_request": binding,
        "presentation_intent": intent,
    })
    _write_json(job / "planning" / "brief.json", {"presentation_intent": intent})
    _write_json(job / "planning" / "deck-plan.json", {
        "title": "PRIVATE_DECK_TITLE", "slides": [],
        "planning_strategy": {"presentation_intent": intent},
    })
    deck = job / "build" / "deck.pptx"
    deck.parent.mkdir(parents=True, exist_ok=True)
    deck.write_bytes(b"current deck bytes")
    provenance = {
        "schema_version": 1, "work_id": identifier, "job_id": "verified-case", "route": "template",
        "request_sha256": request_sha,
        "brief_sha256": _sha(job / "planning" / "brief.json"),
        "intake_decision_sha256": _sha(job / "planning" / "intake-decision.json"),
        "deck_plan_sha256": _sha(job / "planning" / "deck-plan.json"),
        "source_hashes": {"source-1": source_sha},
        "output_path": "build/deck.pptx", "output_sha256": _sha(deck),
    }
    _write_json(job / "qa" / "presentation-quality-final.json", {
        "status": "passed", "checks": ["bounds"], "project_learning_provenance": provenance,
    })
    return work


def _work_tree_digest(work: Path) -> dict[str, str]:
    return {path.relative_to(work).as_posix(): _sha(path) for path in sorted(work.rglob("*")) if path.is_file()}


def test_request_driven_legacy_category_does_not_retain_request_or_client_text(tmp_path: Path) -> None:
    work = _work(
        tmp_path,
        "8",
        name="CLIENT_SECRET_NAME 普通材料",
        request="REQUEST_SECRET：为政府领导做正式防汛工作汇报，事实与责任关系必须清楚。",
    )
    (work / "input" / "source" / "facts.docx").write_bytes(b"docx")
    before = _work_tree_digest(work)

    index = corpus.build_work_corpus_index(tmp_path)
    row = index["works"][0]
    rendered = json.dumps(index, ensure_ascii=False)

    assert row["classification"]["case_type"] == "presentation_work"
    assert row["classification"]["presentation_form"] == {
        "value": "government_briefing", "confidence": "bounded_work_request_inference"
    }
    assert row["classification"]["change_mode"]["value"] is None
    assert row["input_profile"]["source"]["types"] == {"document": 1}
    assert row["reuse"]["level"] == "completion_metadata_history"
    assert row["reuse"]["eligible_for_future_context"] is False
    assert "CLIENT_SECRET_NAME" not in rendered
    assert "REQUEST_SECRET" not in rendered
    assert before == _work_tree_digest(work)


def test_completion_metadata_and_hash_bound_retrospective_do_not_enter_future_context(tmp_path: Path) -> None:
    """The P1 regression: metadata can never stand in for a user quote."""

    _verified_studio_case(tmp_path)
    index = corpus.build_work_corpus_index(tmp_path)
    row = index["works"][0]

    assert row["quality_evidence"]["current_work_bound_records"] == 1
    assert row["acceptance"]["retrospective"] == {"state": "current_hash_validated"}
    assert row["acceptance"]["completion_metadata"] == {"completed": True, "user_approved": True}
    assert row["acceptance"]["original_user_acceptance"]["state"] == "project_review_not_started"
    assert row["acceptance"]["state"] == "completion_metadata_without_explicit_original_user_acceptance"
    assert row["reuse"]["level"] == "completion_metadata_history"
    assert row["reuse"]["eligible_for_future_context"] is False
    assert corpus.recall_work_corpus("竞赛答辩，保留模板后重新组织", tmp_path)["reusable_cases"] == []
    assert ci.recall_context("竞赛答辩，保留模板后重新组织", tmp_path)["work_corpus"]["reusable_cases"] == []


def test_cached_pre_acceptance_schema_rebuilds_before_context_recall(tmp_path: Path) -> None:
    """A pre-fix index cannot keep its formerly eligible rows in circulation."""

    work = _verified_studio_case(tmp_path)
    destination = tmp_path / corpus.INDEX_RELATIVE_PATH
    _write_json(destination, {
        "schema_version": 1,
        "workspace_root": str(tmp_path.resolve()),
        "status": "ready",
        "works": [{"work_id": work.name, "reuse": {"level": "accepted_lesson", "eligible_for_future_context": True}}],
    })

    recalled = corpus.recall_work_corpus("竞赛答辩，保留模板后重新组织", tmp_path, refresh=False)
    rebuilt = json.loads(destination.read_text(encoding="utf-8"))

    assert recalled["reusable_cases"] == []
    assert rebuilt["schema_version"] == corpus.SCHEMA_VERSION == 2
    assert rebuilt["works"][0]["reuse"]["eligible_for_future_context"] is False


def test_verified_case_exposes_only_category_and_current_evidence_for_context(tmp_path: Path) -> None:
    work = _verified_studio_case(tmp_path)
    _add_current_explicit_user_acceptance(tmp_path, work)

    index = corpus.build_work_corpus_index(tmp_path)
    row = index["works"][0]
    rendered = json.dumps(index, ensure_ascii=False)

    assert row["classification"]["status"] == "structured"
    assert row["classification"]["presentation_form"] == {
        "value": "competition_defense", "confidence": "hash_validated_studio_intent"
    }
    assert row["classification"]["change_mode"]["value"] == "template_relayout"
    assert row["execution"]["route"] == {"value": "template", "confidence": "legacy_observed_job_record"}
    assert row["quality_evidence"]["state"] == "current_work_bound_qa"
    assert row["quality_evidence"]["current_work_bound_records"] == 1
    assert row["acceptance"]["state"] == "explicit_original_user_acceptance_with_current_retrospective"
    assert row["acceptance"]["original_user_acceptance"]["state"] == "explicit_original_user_acceptance"
    assert row["reuse"]["level"] == "verified_case"
    assert row["reuse"]["eligible_for_future_context"] is True
    for private in ("CLIENT_NAME_SHOULD_NOT_LEAK", "REQUEST_SECRET", "SOURCE_SECRET", "PRIVATE_DECK_TITLE", "verified-case"):
        assert private not in rendered

    recalled = corpus.recall_work_corpus("竞赛答辩，保留模板后重新组织", tmp_path)
    assert recalled["status"] == "matched"
    assert recalled["reusable_cases"][0]["work_id"] == work.name
    assert recalled["reusable_cases"][0]["reuse"]["level"] == "verified_case"
    assert "CLIENT_NAME_SHOULD_NOT_LEAK" not in json.dumps(recalled, ensure_ascii=False)

    context = ci.recall_context("竞赛答辩，保留模板后重新组织", tmp_path)
    assert context["work_corpus"]["reusable_cases"][0]["work_id"] == work.name
    assert context["work_corpus"]["reusable_cases"][0]["classification"]["presentation_form"]["value"] == "competition_defense"


def test_hash_bound_retrospective_with_original_user_acceptance_is_an_accepted_lesson(tmp_path: Path) -> None:
    work = _work(
        tmp_path,
        "14",
        name="正式汇报图文调整",
        request="请完成正式业务汇报，页面图文关系和表达需要适合现场汇报。",
    )
    _set_hash_bound_retrospective(tmp_path, work)
    _add_current_explicit_user_acceptance(tmp_path, work)

    row = corpus.build_work_corpus_index(tmp_path)["works"][0]

    assert row["quality_evidence"]["current_work_bound_records"] == 0
    assert row["acceptance"]["original_user_acceptance"]["state"] == "explicit_original_user_acceptance"
    assert row["reuse"]["level"] == "accepted_lesson"
    assert row["reuse"]["eligible_for_future_context"] is True


def test_changed_output_revokes_verified_case_until_its_project_review_is_current(tmp_path: Path) -> None:
    work = _verified_studio_case(tmp_path)
    _add_current_explicit_user_acceptance(tmp_path, work)
    first = corpus.build_work_corpus_index(tmp_path)
    assert first["works"][0]["reuse"]["level"] == "verified_case"

    deck = work / "workspace" / "studio-jobs" / "verified-case" / "build" / "deck.pptx"
    deck.write_bytes(b"changed after QA")
    refreshed = corpus.build_work_corpus_index(tmp_path)
    row = refreshed["works"][0]

    assert row["quality_evidence"]["current_work_bound_records"] == 0
    assert row["quality_evidence"]["state"] == "historical_or_unbound_qa_only"
    assert row["acceptance"]["original_user_acceptance"]["state"] == "project_review_stale"
    assert row["reuse"]["level"] == "completion_metadata_history"
    assert row["reuse"]["eligible_for_future_context"] is False


def test_changed_original_acceptance_snapshot_revokes_case_reuse(tmp_path: Path) -> None:
    work = _verified_studio_case(tmp_path)
    raw = _add_current_explicit_user_acceptance(tmp_path, work)
    assert corpus.build_work_corpus_index(tmp_path)["works"][0]["reuse"]["level"] == "verified_case"

    raw.write_bytes(raw.read_bytes().replace("可以了".encode(), "不可以".encode()))
    refreshed = corpus.build_work_corpus_index(tmp_path)["works"][0]

    assert refreshed["acceptance"]["original_user_acceptance"]["state"] == "explicit_user_acceptance_evidence_unavailable"
    assert refreshed["reuse"]["level"] == "completion_metadata_history"
    assert refreshed["reuse"]["eligible_for_future_context"] is False


def test_tool_name_in_a_presentation_request_does_not_misclassify_it_as_studio_core(tmp_path: Path) -> None:
    work = _work(
        tmp_path,
        "12",
        name="客户课堂演示",
        status="review",
        user_approved=False,
        request="请使用 AI PPT Studio 制作一份适合小学课堂的教学演示，不要把工具操作写到页面上。",
    )
    row = corpus.build_work_corpus_index(tmp_path)["works"][0]

    assert row["classification"]["case_type"] == "presentation_work"
    assert row["classification"]["capability_tags"] == []
    assert row["classification"]["presentation_form"]["value"] == "teaching"


def test_studio_tool_used_for_template_edit_is_not_a_core_repair(tmp_path: Path) -> None:
    work = _work(
        tmp_path,
        "13",
        name="季度管理汇报微调",
        status="review",
        user_approved=False,
        request="请使用 AI PPT Studio 只改现有业务汇报模板的第 3 页，其他页不动。",
    )
    row = corpus.build_work_corpus_index(tmp_path)["works"][0]

    assert row["classification"]["case_type"] == "presentation_work"
    assert row["classification"]["capability_tags"] == []
    # The old request makes the limited edit clear, but it has no bound
    # editable original, so a retrospective category must not invent a modern
    # local-patch contract.
    assert row["classification"]["change_mode"]["value"] == "unclassified"
