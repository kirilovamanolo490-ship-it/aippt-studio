from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from aippt_studio.conversations import (
    ConversationError, _lock, archive_conversation, bind_conversation,
    discover_conversations, refresh_work_conversations, refresh_conversation_tree,
)


THREAD = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


class ConversationArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "项目 with spaces"
        self.home = Path(self.temp.name) / "codex"
        self.home.mkdir()
        self.work = self.make_work("1")

    def make_work(self, number: str) -> Path:
        work = self.root / "work" / number
        (work / "workspace").mkdir(parents=True)
        (work / "work.json").write_text(json.dumps({"work_id": number, "status": "in_progress"}))
        return work

    def rollout(self, thread_id: str = THREAD, *, cwd: Path | None = None,
                folder: str = "sessions/2026/09/15", filename: str | None = None,
                records: list[dict] | None = None) -> Path:
        path = self.home / folder / (filename or f"rollout-{thread_id}.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        header = {"type": "session_meta", "payload": {"id": thread_id, "cwd": str(cwd or self.root)}}
        rows = [header] + (records or [])
        # Preserve non-default whitespace, Unicode, and CRLF in raw snapshots.
        path.write_bytes(b"\xef\xbb\xbf" + b"\r\n".join(json.dumps(row, ensure_ascii=False).encode() for row in rows) + b"\r\n")
        return path

    def db(self, path: Path, *, thread_id: str = THREAD, archived: int = 0,
           cwd: Path | None = None) -> None:
        with sqlite3.connect(self.home / "state_5.sqlite") as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, rollout_path TEXT, cwd TEXT, title TEXT, archived INTEGER)")
            connection.execute("INSERT OR REPLACE INTO threads VALUES (?, ?, ?, ?, ?)",
                               (thread_id, str(path), str(cwd or self.root), "Work 1 exact-looking title", archived))

    def archive(self, **kwargs) -> dict:
        return archive_conversation(self.work, thread_id=THREAD, codex_home=self.home,
                                    binding_reason="explicit user task association", **kwargs)

    def manifest(self, work: Path | None = None) -> dict:
        return json.loads(((work or self.work) / "workspace/conversations/manifest.json").read_text())

    def snapshot(self) -> tuple[dict, Path]:
        snapshot = self.manifest()["threads"][THREAD]["snapshots"][-1]
        return snapshot, self.work / "workspace/conversations" / snapshot["raw_path"]

    @staticmethod
    def message(role: str, text: str) -> dict:
        return {"type": "response_item", "payload": {"type": "message", "role": role,
                    "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}]}}

    def test_exact_bytes_hash_and_private_files(self) -> None:
        source = self.rollout(records=[self.message("user", "字节要原样保存"), self.message("assistant", "已记录")])
        original_work = (self.work / "work.json").read_bytes()
        result = self.archive()
        snapshot, raw = self.snapshot()
        self.assertEqual(result["status"], "updated")
        self.assertEqual(raw.read_bytes(), source.read_bytes())
        self.assertEqual(snapshot["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual((self.work / "work.json").read_bytes(), original_work)
        self.assertFalse(self.manifest()["complete_conversation_claim"])
        self.assertEqual(raw.stat().st_mode & 0o777, 0o600)
        self.assertEqual(snapshot["summary"]["message_count"], 2)

    def test_deduplicate_then_append_snapshot(self) -> None:
        source = self.rollout(records=[self.message("user", "first")])
        self.archive()
        first_manifest = (self.work / "workspace/conversations/manifest.json").read_bytes()
        self.assertEqual(refresh_work_conversations(self.work, codex_home=self.home)["status"], "unchanged")
        self.assertEqual(first_manifest, (self.work / "workspace/conversations/manifest.json").read_bytes())
        original = source.read_bytes()
        with source.open("ab") as stream:
            stream.write(json.dumps(self.message("assistant", "second")).encode() + b"\n")
        self.assertEqual(refresh_work_conversations(self.work, codex_home=self.home)["status"], "updated")
        history = self.manifest()["threads"][THREAD]
        self.assertEqual(len(history["snapshots"]), 2)
        latest, raw = self.snapshot()
        self.assertTrue(latest["append_only_from_previous"])
        self.assertEqual(latest["delta_bytes"], len(source.read_bytes()) - len(original))
        self.assertEqual(raw.read_bytes(), source.read_bytes())

    def test_append_snapshot_reuses_verified_prefix_without_changing_history(self) -> None:
        source = self.rollout(records=[self.message("user", "first")])
        self.archive()
        first, first_raw = self.snapshot()
        first_bytes = first_raw.read_bytes()
        first_stat = first_raw.stat()
        with source.open("ab") as stream:
            stream.write(json.dumps(self.message("assistant", "second")).encode() + b"\n")

        def simulated_clone(src: Path, dest: Path) -> bool:
            # Simulate clonefile's byte result on non-macOS test hosts.
            dest.write_bytes(src.read_bytes())
            return True

        with patch("aippt_studio.conversations._clonefile", side_effect=simulated_clone):
            self.assertEqual(refresh_work_conversations(self.work, codex_home=self.home)["status"], "updated")

        latest, latest_raw = self.snapshot()
        self.assertEqual(latest_raw.read_bytes(), source.read_bytes())
        self.assertEqual(latest["copy_method"], "apfs_prefix_clonefile")
        self.assertTrue(latest["append_only_from_previous"])
        self.assertEqual(first_raw.read_bytes(), first_bytes)
        self.assertEqual(first_raw.stat().st_ino, first_stat.st_ino)
        self.assertEqual(first["sha256"], hashlib.sha256(first_bytes).hexdigest())

    def test_nonprefix_history_never_attempts_clone(self) -> None:
        source = self.rollout(records=[self.message("user", "first")])
        self.archive()
        with self.snapshot()[1].open("r+b") as stream:
            stream.seek(0)
            stream.write(b"different")
        with source.open("ab") as stream:
            stream.write(json.dumps(self.message("assistant", "second")).encode() + b"\n")

        with patch("aippt_studio.conversations._clonefile", return_value=False) as clone:
            self.assertEqual(refresh_work_conversations(self.work, codex_home=self.home)["status"], "updated")
        self.assertEqual(clone.call_count, 1)
        self.assertNotEqual(clone.call_args.args[0].resolve(), self.snapshot()[1].resolve())
        latest, latest_raw = self.snapshot()
        self.assertEqual(latest_raw.read_bytes(), source.read_bytes())
        self.assertEqual(latest["copy_method"], "bounded_stream_copy")

    def test_prefix_clone_failure_falls_back_to_full_copy(self) -> None:
        source = self.rollout(records=[self.message("user", "first")])
        self.archive()
        with source.open("ab") as stream:
            stream.write(json.dumps(self.message("assistant", "second")).encode() + b"\n")
        with patch("aippt_studio.conversations._clonefile", return_value=False):
            self.assertEqual(refresh_work_conversations(self.work, codex_home=self.home)["status"], "updated")
        latest, latest_raw = self.snapshot()
        self.assertEqual(latest_raw.read_bytes(), source.read_bytes())
        self.assertEqual(latest["copy_method"], "bounded_stream_copy")

    def test_maintenance_defers_active_growth_without_claiming_it_was_archived(self) -> None:
        source = self.rollout(records=[self.message("user", "first")])
        self.archive()
        archived, raw = self.snapshot()
        archived_bytes = raw.read_bytes()
        with source.open("ab") as stream:
            stream.write(json.dumps(self.message("assistant", "new active content")).encode() + b"\n")

        result = refresh_work_conversations(self.work, codex_home=self.home,
                                            active_snapshot_interval_seconds=24 * 60 * 60)

        assert result["status"] == "deferred"
        assert result["results"][0]["status"] == "deferred_active_snapshot"
        assert result["results"][0]["last_archived_sha256"] == archived["sha256"]
        assert "sha256" not in result["results"][0]
        assert len(self.manifest()["threads"][THREAD]["snapshots"]) == 1
        assert raw.read_bytes() == archived_bytes
        deferred = self.manifest()["threads"][THREAD]["active_snapshot_deferral"]
        assert deferred["status"] == "pending"
        assert deferred["observed_source_bytes"] == source.stat().st_size
        assert deferred["last_archived_bytes"] == len(archived_bytes)
        assert deferred["evidence_scope"] == "metadata_only_current_source_bytes_not_archived"
        assert "sha256" not in deferred

    def test_explicit_refresh_and_archived_transition_capture_without_waiting(self) -> None:
        source = self.rollout(records=[self.message("user", "first")])
        self.archive()
        with source.open("ab") as stream:
            stream.write(json.dumps(self.message("assistant", "explicit sync")).encode() + b"\n")
        explicit = refresh_work_conversations(self.work, codex_home=self.home)
        assert explicit["status"] == "updated"
        assert len(self.manifest()["threads"][THREAD]["snapshots"]) == 2

        self.db(source, archived=1)
        with source.open("ab") as stream:
            stream.write(json.dumps(self.message("assistant", "archived transition")).encode() + b"\n")
        result = refresh_work_conversations(self.work, codex_home=self.home,
                                            active_snapshot_interval_seconds=24 * 60 * 60)
        assert result["status"] == "updated"
        assert result["results"][0]["source_state"] == "archived_snapshot"
        assert len(self.manifest()["threads"][THREAD]["snapshots"]) == 3

    def test_maintenance_interval_elapsed_captures_deferred_active_growth(self) -> None:
        source = self.rollout(records=[self.message("user", "first")])
        self.archive()
        with source.open("ab") as stream:
            stream.write(json.dumps(self.message("assistant", "interval elapsed")).encode() + b"\n")
        deferred = refresh_work_conversations(self.work, codex_home=self.home,
                                              active_snapshot_interval_seconds=24 * 60 * 60)
        assert deferred["status"] == "deferred"
        manifest = self.manifest()
        manifest["threads"][THREAD]["snapshots"][-1]["captured_at"] = "2000-01-01T00:00:00+00:00"
        manifest["threads"][THREAD]["snapshots"][-1]["last_full_capture_at"] = "2000-01-01T00:00:00+00:00"
        (self.work / "workspace/conversations/manifest.json").write_text(json.dumps(manifest))

        result = refresh_work_conversations(self.work, codex_home=self.home,
                                            active_snapshot_interval_seconds=60)

        assert result["status"] == "updated"
        assert len(self.manifest()["threads"][THREAD]["snapshots"]) == 2
        assert self.manifest()["threads"][THREAD]["active_snapshot_deferral"]["status"] == "captured_after_deferral"

    def test_missing_previous_raw_snapshot_never_delays_recovery(self) -> None:
        source = self.rollout(records=[self.message("user", "first")])
        self.archive()
        _, old_raw = self.snapshot()
        old_raw.unlink()
        with source.open("ab") as stream:
            stream.write(json.dumps(self.message("assistant", "recover missing snapshot")).encode() + b"\n")

        result = refresh_work_conversations(self.work, codex_home=self.home,
                                            active_snapshot_interval_seconds=24 * 60 * 60)

        assert result["status"] == "updated"
        assert result["results"][0]["status"] == "updated"
        assert len(self.manifest()["threads"][THREAD]["snapshots"]) == 2
        assert "active_snapshot_deferral" not in self.manifest()["threads"][THREAD]

    def test_verified_same_bytes_refresh_resets_active_snapshot_interval(self) -> None:
        source = self.rollout(records=[self.message("user", "unchanged payload")])
        self.archive()
        manifest = self.manifest()
        manifest["threads"][THREAD]["snapshots"][-1]["last_full_capture_at"] = "2000-01-01T00:00:00+00:00"
        (self.work / "workspace/conversations/manifest.json").write_text(json.dumps(manifest))
        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

        verified = refresh_work_conversations(self.work, codex_home=self.home,
                                              active_snapshot_interval_seconds=60)
        assert verified["status"] == "updated"
        history = self.manifest()["threads"][THREAD]
        assert len(history["snapshots"]) == 1
        assert history["snapshots"][-1]["last_full_capture_at"] != "2000-01-01T00:00:00+00:00"

        stat = source.stat()
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
        next_tick = refresh_work_conversations(self.work, codex_home=self.home,
                                               active_snapshot_interval_seconds=24 * 60 * 60)
        assert next_tick["status"] == "deferred"

    def test_rewritten_rollout_is_not_an_append(self) -> None:
        source = self.rollout(records=[self.message("user", "original")])
        self.archive()
        self.rollout(records=[self.message("user", "different retained local trace")])
        refresh_work_conversations(self.work, codex_home=self.home)
        snapshot, raw = self.snapshot()
        self.assertFalse(snapshot["append_only_from_previous"])
        self.assertIsNone(snapshot["delta_bytes"])
        self.assertEqual(raw.read_bytes(), source.read_bytes())

    def test_unchanged_fast_path_does_not_hash_large_rollout(self) -> None:
        self.rollout()
        self.archive()
        with patch("aippt_studio.conversations._sha_file", side_effect=AssertionError("unexpected full read")):
            result = refresh_work_conversations(self.work, codex_home=self.home)
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(result["results"][0]["verification"], "metadata_unchanged")
        result = refresh_work_conversations(self.work, codex_home=self.home, verify=True)
        self.assertEqual(result["results"][0]["verification"], "snapshot_sha256_verified")

    def test_clone_fallback_preserves_bytes(self) -> None:
        source = self.rollout(records=[self.message("user", "fallback")])
        with patch("aippt_studio.conversations._clonefile", return_value=False):
            self.archive()
        snapshot, raw = self.snapshot()
        self.assertEqual(raw.read_bytes(), source.read_bytes())
        self.assertEqual(snapshot["copy_method"], "bounded_stream_copy")

    def test_source_append_after_clone_preserves_snapshot_and_refreshes_next_time(self) -> None:
        source = self.rollout()
        before = source.read_bytes()

        def clone_then_append(src: Path, dest: Path) -> bool:
            dest.write_bytes(src.read_bytes())
            with src.open("ab") as stream:
                stream.write(json.dumps(self.message("assistant", "later append")).encode() + b"\n")
            return True

        with patch("aippt_studio.conversations._clonefile", side_effect=clone_then_append):
            self.archive()
        snapshot, raw = self.snapshot()
        self.assertEqual(raw.read_bytes(), before)
        self.assertTrue(snapshot["source_changed_during_capture"])
        self.assertIsNone(snapshot["source_fingerprint"])
        result = refresh_work_conversations(self.work, codex_home=self.home)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(len(self.manifest()["threads"][THREAD]["snapshots"]), 2)

    def test_sidebar_archived_state_does_not_require_file_move(self) -> None:
        source = self.rollout()
        self.db(source)
        self.archive()
        self.db(source, archived=1)
        result = refresh_work_conversations(self.work, codex_home=self.home)
        history = self.manifest()["threads"][THREAD]
        self.assertEqual(result["results"][0]["source_state"], "archived_snapshot")
        self.assertEqual(len(history["snapshots"]), 1)
        self.assertEqual(len(history["observations"]), 2)
        self.assertEqual(history["observations"][-1]["state_evidence"], "codex_threads_table")

    def test_archived_directory_fallback_and_db_precedence(self) -> None:
        source = self.rollout(folder="archived_sessions")
        result = self.archive()
        self.assertEqual(result["results"][0]["source_state"], "archived_snapshot")
        self.db(source, archived=0)
        result = refresh_work_conversations(self.work, codex_home=self.home)
        self.assertEqual(result["results"][0]["source_state"], "active_snapshot")

    def test_exact_metadata_id_required_even_with_matching_filename(self) -> None:
        source = self.rollout(OTHER, filename=f"rollout-{THREAD}.jsonl")
        with self.assertRaises(ConversationError):
            self.archive(rollout_path=source)
        self.assertFalse((self.work / "workspace/conversations/bindings.json").exists())
        with self.assertRaises(ConversationError):
            self.archive()

    def test_exact_path_can_bind_without_thread_id(self) -> None:
        source = self.rollout(filename="explicit-unusual-name.jsonl")
        result = archive_conversation(self.work, rollout_path=source, codex_home=self.home,
                                      binding_reason="explicit source path supplied")
        self.assertEqual(result["status"], "updated")
        binding = json.loads((self.work / "workspace/conversations/bindings.json").read_text())["bindings"][0]
        self.assertEqual(binding["binding_method"], "explicit_rollout_path")

    def test_unbound_refresh_copies_nothing_and_no_fuzzy_title_mapping(self) -> None:
        source = self.rollout()
        self.db(source)
        result = refresh_work_conversations(self.work, codex_home=self.home)
        self.assertEqual(result["status"], "unbound")
        self.assertFalse((self.work / "workspace/conversations").exists())
        with self.assertRaises(ConversationError):
            bind_conversation(self.work, codex_home=self.home, binding_reason="title seems similar")

    def test_discovery_cwd_only_and_missing_rollout_honesty(self) -> None:
        source = self.rollout()
        self.db(source)
        unrelated = self.rollout(OTHER, cwd=self.root / "unrelated")
        self.db(unrelated, thread_id=OTHER, cwd=self.root / "unrelated")
        report = discover_conversations(self.root, self.home)
        self.assertEqual([x["thread_id"] for x in report["candidates"]], [THREAD])
        self.assertFalse(report["auto_binding"])
        self.assertEqual(report["candidates"][0]["bound_work_ids"], [])
        source.unlink()
        report = discover_conversations(self.root, self.home)
        self.assertEqual(report["candidates"][0]["source_state"], "source_unavailable")
        self.assertEqual(report["candidates"][0]["rollout_paths"], [])

    def test_moved_rollout_resolves_by_exact_thread(self) -> None:
        source = self.rollout()
        self.db(source)
        self.archive()
        moved = self.home / "archived_sessions" / source.name
        moved.parent.mkdir()
        source.rename(moved)
        self.db(moved, archived=1)
        result = refresh_work_conversations(self.work, codex_home=self.home)
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["results"][0]["source_state"], "archived_snapshot")
        self.assertEqual(len(self.manifest()["threads"][THREAD]["snapshots"]), 1)

    def test_missing_source_preserves_evidence_and_reports_error(self) -> None:
        source = self.rollout()
        self.archive()
        _, raw = self.snapshot()
        expected = raw.read_bytes()
        source.unlink()
        result = refresh_work_conversations(self.work, codex_home=self.home)
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["results"][0]["source_state"], "source_unavailable")
        self.assertEqual(raw.read_bytes(), expected)
        self.assertIn("last_error", self.manifest()["threads"][THREAD])

    def test_invalid_and_compacted_records_retained_and_counted(self) -> None:
        source = self.rollout(records=[{"type": "compacted", "payload": {"message": "older trace was compacted"}}])
        with source.open("ab") as stream:
            stream.write(b'{"type":"partial')
        self.archive()
        snapshot, raw = self.snapshot()
        self.assertEqual(raw.read_bytes(), source.read_bytes())
        self.assertEqual(snapshot["summary"]["compaction_record_count"], 1)
        self.assertEqual(snapshot["summary"]["parse_error_count"], 1)
        self.assertFalse(snapshot["summary"]["ends_with_newline"])

    def test_tool_failure_index_and_message_event_deduplication(self) -> None:
        self.rollout(records=[
            self.message("user", "again"),
            {"type": "event_msg", "payload": {"type": "user_message", "message": "again"}},
            self.message("user", "again"),  # actual repeated user message is retained
            {"type": "response_item", "payload": {"type": "function_call", "name": "exec_command", "call_id": "c1", "arguments": "exit 2"}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c1", "output": '{"exit_code":2,"output":"Command failed"}'}},
            {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "c2", "output": '{"exit_code":0,"output":"ok"}'}},
            {"type": "response_item", "payload": {"type": "custom_tool_call_output", "call_id": "c3", "output": [{"type": "text", "text": '{"isError":true,"content":"failure"}'}]}},
            self.message("assistant", "retry recorded"),
        ])
        self.archive()
        snapshot, _ = self.snapshot()
        derived = self.work / "workspace/conversations" / snapshot["derived_path"]
        failures = json.loads((derived / "tool-failures.json").read_text())["failures"]
        self.assertEqual(snapshot["summary"]["message_count"], 3)
        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[0]["tool"], "exec_command")
        self.assertIn("exit_code:2", failures[0]["signals"])
        self.assertTrue(all(item["byte_offset"] > 0 for item in failures))

    def test_shared_conversation_marked_on_both_work_refreshes(self) -> None:
        self.rollout()
        self.archive()
        other_work = self.make_work("2")
        archive_conversation(other_work, thread_id=THREAD, codex_home=self.home, binding_reason="user explicitly says both Work packages share this thread")
        refresh_work_conversations(self.work, codex_home=self.home)
        for work in (self.work, other_work):
            history = self.manifest(work)["threads"][THREAD]
            self.assertTrue(history["shared_conversation"])
            self.assertEqual(history["associated_work_ids"], ["1", "2"])

    def test_subagent_parent_is_verified_and_kept_distinct(self) -> None:
        source = self.rollout(OTHER)
        raw = source.read_bytes().decode("utf-8-sig")
        header = json.loads(raw.splitlines()[0])
        header["payload"]["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": THREAD}}}
        source.write_text(json.dumps(header) + "\n")
        with self.assertRaises(ConversationError):
            bind_conversation(self.work, thread_id=OTHER, codex_home=self.home,
                              binding_reason="derived trace", parent_thread_id=OTHER)
        archive_conversation(self.work, thread_id=OTHER, codex_home=self.home,
                             binding_reason="exact parent metadata of bound primary", parent_thread_id=THREAD)
        history = self.manifest()["threads"][OTHER]
        self.assertEqual(history["conversation_role"], "subagent_trace")
        self.assertEqual(history["parent_thread_id"], THREAD)

    def child(self, thread_id: str, parent: str) -> Path:
        source = self.rollout(thread_id)
        header = json.loads(source.read_bytes().decode("utf-8-sig").splitlines()[0])
        header["payload"]["source"] = {"subagent": {"thread_spawn": {"parent_thread_id": parent}}}
        source.write_text(json.dumps(header) + "\n")
        return source

    def test_tree_refresh_follows_exact_parent_recursively_and_finds_future_children(self) -> None:
        self.rollout()
        self.archive()
        self.child(OTHER, THREAD)
        grandchild = "33333333-3333-4333-8333-333333333333"
        self.child(grandchild, OTHER)
        unrelated = "44444444-4444-4444-8444-444444444444"
        self.child(unrelated, "unbound-parent")
        result = refresh_conversation_tree(self.work, codex_home=self.home)
        self.assertEqual({b["thread_id"] for b in result["tree_discovery"]["new_bindings"]}, {OTHER, grandchild})
        self.assertEqual(set(self.manifest()["threads"]), {THREAD, OTHER, grandchild})
        again = refresh_conversation_tree(self.work, codex_home=self.home)
        self.assertEqual(again["status"], "unchanged")
        self.assertEqual(again["tree_discovery"]["header_reads"], 0)
        self.assertEqual(again["tree_discovery"]["header_cache_hits"], 4)
        future = "55555555-5555-4555-8555-555555555555"
        self.child(future, grandchild)
        result = refresh_conversation_tree(self.work, codex_home=self.home)
        self.assertEqual([x["thread_id"] for x in result["tree_discovery"]["new_bindings"]], [future])
        self.assertNotIn(unrelated, self.manifest()["threads"])

    def test_tree_discovery_requires_explicit_root(self) -> None:
        self.rollout()
        self.child(OTHER, THREAD)
        result = refresh_conversation_tree(self.work, codex_home=self.home)
        self.assertEqual(result["status"], "unbound")
        self.assertFalse((self.work / "workspace/conversations").exists())

    def test_tampered_raw_snapshot_is_never_overwritten(self) -> None:
        self.rollout()
        self.archive()
        _, raw = self.snapshot()
        raw.write_bytes(b"tampered")
        result = refresh_work_conversations(self.work, codex_home=self.home)
        self.assertEqual(result["status"], "error")
        self.assertIn("SHA-256", result["results"][0]["error"])
        self.assertEqual(raw.read_bytes(), b"tampered")

    @unittest.skipIf(sys.platform == "win32", "POSIX same-process advisory lock regression")
    def test_overlapping_refresh_fails_before_mutation(self) -> None:
        self.rollout()
        bind_conversation(self.work, thread_id=THREAD, codex_home=self.home, binding_reason="explicit binding")
        with _lock(self.work / "workspace/conversations"):
            with self.assertRaises(ConversationError):
                refresh_work_conversations(self.work, codex_home=self.home)
        self.assertFalse((self.work / "workspace/conversations/manifest.json").exists())

    def test_cli_operates_with_stdlib_python(self) -> None:
        self.rollout()
        script = Path(__file__).resolve().parents[2] / "scripts/archive-work-conversation.py"
        completed = subprocess.run([sys.executable, str(script), "archive", "--work-dir", str(self.work),
                                    "--codex-home", str(self.home), "--thread-id", THREAD, "--reason", "explicit CLI binding"],
                                   capture_output=True, text=True, timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        self.assertEqual(json.loads(completed.stdout)["status"], "updated")


if __name__ == "__main__":
    unittest.main()
