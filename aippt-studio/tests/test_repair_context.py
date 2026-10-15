import hashlib
import json

from aippt_studio.repair_context import build_repair_context


def fixture(tmp_path, *, large=False):
    user = "图片边缘有白色像素，保留人物本身。"
    records = [
        {"type": "session_meta", "payload": {"id": "thread"}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "analysis", "content": [{"type": "output_text", "text": "private reasoning excluded"}]}},
        {"type": "response_item", "payload": {"type": "function_call", "name": "exec", "call_id": "call-1", "arguments": 'python cutout.py; api_key="sk-veryprivate123456"'}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call-1", "output": "Traceback: alpha composite failed; exit code 1"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": user}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "commentary", "content": [{"type": "output_text", "text": "我将核对人物边缘与原始图像。"}, {"type": "image", "data": "PRIVATE_IMAGE"}]}},
        {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "media", "output": "data:image/png;base64," + ("A" * (10000 if large else 200))}},
    ]
    data = [(json.dumps(r, ensure_ascii=False) + "\n").encode() for r in records]
    raw_bytes = b"".join(data); digest = hashlib.sha256(raw_bytes).hexdigest()
    store = tmp_path / "work/1/workspace/conversations"
    raw = store / f"snapshots/thread/{digest}.jsonl"; raw.parent.mkdir(parents=True); raw.write_bytes(raw_bytes)
    derived = store / f"derived/thread/{digest}"; derived.mkdir(parents=True)
    rows, offset = [], 0
    for line, (record, value) in enumerate(zip(records, data), 1):
        payload = record["payload"]
        rows.append({"line": line, "byte_offset": offset, "byte_length": len(value), "type": record["type"],
                     "payload_type": payload.get("type"), "role": payload.get("role")})
        offset += len(value)
    (derived / "record-index.json").write_text(json.dumps({"raw_bytes": offset, "records": rows}))
    (store / "manifest.json").write_text(json.dumps({"threads": {"thread": {"snapshots": [{"sha256": digest,
        "raw_path": raw.relative_to(store).as_posix(), "derived_path": derived.relative_to(store).as_posix()}]}}}))
    source = {"raw_path": str(raw), "snapshot_sha256": digest, "record_sha256": hashlib.sha256(data[4]).hexdigest(),
              **{k: rows[4][k] for k in ("line", "byte_offset", "byte_length")}}
    event = {"event_id": "user-1", "text": user, "text_sha256": hashlib.sha256(user.encode()).hexdigest(), "work_ids": ["1"], "sources": [source]}
    return {"candidate_id": "edge", "source_events": [event], "evidence": [{"event_id": "user-1", "quote": user}], "receipt": "accepted.json"}, raw, derived


def test_user_and_adjacent_execution_stay_role_separated_and_hashed(tmp_path):
    candidate, raw, _ = fixture(tmp_path)
    result = build_repair_context(candidate, repo=tmp_path)
    assert not result["verification_errors"]
    assert result["source_events"][0]["text"] == candidate["source_events"][0]["text"]
    failure = next(r for r in result["context_records"] if r["role"] == "tool_failure_candidate")
    assert failure["call_id"] == "call-1"
    with raw.open("rb") as stream:
        stream.seek(failure["source"]["byte_offset"])
        assert hashlib.sha256(stream.read(failure["source"]["byte_length"])).hexdigest() == failure["source"]["record_sha256"]
    assert "Adjacency does not prove causality" in result["semantic_limits"][0]


def test_secrets_images_and_reasoning_are_not_emitted(tmp_path):
    candidate, _, _ = fixture(tmp_path)
    result = build_repair_context(candidate, repo=tmp_path)
    text = json.dumps(result)
    assert "sk-veryprivate" not in text and "PRIVATE_IMAGE" not in text
    assert "private reasoning excluded" not in text and "A" * 160 not in text
    assert any(r["redactions"] for r in result["context_records"])
    assert result["status"] == "partial"


def test_large_media_record_is_skipped_and_total_excerpt_budget_is_bounded(tmp_path):
    candidate, _, _ = fixture(tmp_path, large=True)
    result = build_repair_context(candidate, repo=tmp_path, max_record_bytes=1000, max_context_chars=60)
    assert result["limits"]["emitted_chars"] <= 60
    assert any(r["reason"] == "record_size_limit" for r in result["omissions"])
    assert result["limits"]["raw_bytes_read"] < 2000


def test_original_user_tamper_and_missing_event_do_not_become_verified_context(tmp_path):
    candidate, _, _ = fixture(tmp_path)
    candidate["source_events"][0]["text"] = "forged preference"
    result = build_repair_context(candidate, repo=tmp_path)
    assert result["status"] == "invalid" and result["verification_errors"]
    assert not result["context_records"]
    assert build_repair_context({}, repo=tmp_path)["status"] == "invalid"


def test_index_offsets_and_raw_path_boundary_are_checked(tmp_path):
    candidate, _, derived = fixture(tmp_path)
    path = derived / "record-index.json"
    index = json.loads(path.read_text()); index["records"][2]["byte_offset"] += 1; path.write_text(json.dumps(index))
    assert build_repair_context(candidate, repo=tmp_path)["status"] == "invalid"
    candidate["source_events"][0]["sources"][0]["raw_path"] = str(tmp_path / "outside.jsonl")
    assert build_repair_context(candidate, repo=tmp_path)["status"] == "invalid"


def append_snapshot(raw, derived, text):
    store = raw.parents[2]
    record = {"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "commentary", "content": text}}
    extra = (json.dumps(record, ensure_ascii=False) + "\n").encode()
    data = raw.read_bytes() + extra; digest = hashlib.sha256(data).hexdigest()
    target = raw.with_name(digest + ".jsonl"); target.write_bytes(data)
    index = json.loads((derived / "record-index.json").read_text())
    index["records"].append({"line": len(index["records"]) + 1, "byte_offset": index["raw_bytes"], "byte_length": len(extra),
                             "type": "response_item", "payload_type": "message", "role": "assistant"})
    index["raw_bytes"] = len(data)
    new_derived = derived.with_name(digest); new_derived.mkdir()
    (new_derived / "record-index.json").write_text(json.dumps(index))
    path = store / "manifest.json"; manifest = json.loads(path.read_text()); thread = manifest["threads"]["thread"]
    thread["snapshots"].append({"sha256": digest, "raw_path": target.relative_to(store).as_posix(),
                               "derived_path": new_derived.relative_to(store).as_posix()})
    thread["latest_sha256"] = digest; path.write_text(json.dumps(manifest))
    return target


def test_new_bound_snapshot_keeps_old_authority_and_exposes_later_context(tmp_path):
    candidate, raw, derived = fixture(tmp_path)
    initial = build_repair_context(candidate, repo=tmp_path, neighbor_records=1)
    latest = append_snapshot(raw, derived, "Additional diagnostic context: source mask is rectangular.")
    candidate["priority_at"] = "2026-09-15T10:00:00Z"
    result = build_repair_context(candidate, repo=tmp_path, neighbor_records=1)
    assert result["source_events"][0]["source"]["raw_path"] == str(raw)
    tail = next(r for r in result["context_records"] if "Additional diagnostic" in r["text"])
    assert tail["relation"] == "later_snapshot_tail" and tail["causality"] == "not_established"
    assert tail["source"]["raw_path"] == str(latest)
    assert result["context_fingerprint"] != initial["context_fingerprint"]
    assert result["archive_priority"] == candidate["priority_at"]
    assert result["context_snapshots"][0]["newer_snapshot_used"]


def test_modified_user_bytes_in_new_snapshot_cannot_replace_old_authority(tmp_path):
    candidate, raw, derived = fixture(tmp_path)
    latest = append_snapshot(raw, derived, "New context")
    data = latest.read_bytes(); latest.write_bytes(data.replace("白色".encode(), "黑色".encode()))
    result = build_repair_context(candidate, repo=tmp_path)
    assert result["source_events"][0]["text"] == candidate["source_events"][0]["text"]
    assert result["status"] == "partial" and result["omissions"]
    assert not result["context_snapshots"][0]["newer_snapshot_used"]
    assert any(item["reason"] == "newer_context_unavailable" for item in result["omissions"])
