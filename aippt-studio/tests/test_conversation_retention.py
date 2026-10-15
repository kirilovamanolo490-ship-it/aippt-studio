from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
import sys
import types

from aippt_studio.conversations import (
    archive_conversation, bind_conversation, refresh_conversation_tree,
    refresh_work_conversations,
)
# maintenance imports filelock as an optional runtime dependency. These tests
# exercise its direct retry helper, so a tiny import stub avoids requiring the
# full Studio runtime merely to load that helper.
_using_filelock_stub = False
try:
    import filelock  # noqa: F401
except ImportError:
    _using_filelock_stub = True
    class _UnusedLock:
        def __init__(self, *args, **kwargs): pass
    _filelock = types.ModuleType("filelock")
    _filelock.FileLock = _UnusedLock
    _filelock.Timeout = TimeoutError
    sys.modules["filelock"] = _filelock

from aippt_studio.maintenance import retry_pending_binding
if _using_filelock_stub:
    # Do not leave the test-only import stub visible to unrelated tests.
    sys.modules.pop("filelock", None)
from aippt_studio.project_learning import _sources
from aippt_studio.project_learning import run_project_learning


class MetadataOnlyRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name) / "work" / "61"
        (self.work / "workspace").mkdir(parents=True)
        (self.work / "work.json").write_text(json.dumps({"work_id": "61"}))
        store = self.work / "workspace/conversations"
        store.mkdir()
        (store / "retention-policy.json").write_text(
            json.dumps({"schema_version": 1, "mode": "metadata_only"}))

    def assert_no_archive_payload(self):
        store = self.work / "workspace/conversations"
        self.assertFalse((store / "snapshots").exists())
        self.assertFalse((store / "derived").exists())

    def test_binding_is_skipped_before_source_resolution(self):
        result = bind_conversation(self.work, thread_id="11111111-1111-4111-8111-111111111111",
                                   codex_home=Path(self.temp.name) / "missing",
                                   binding_reason="explicit task association")
        self.assertEqual(result["status"], "metadata_only")
        self.assert_no_archive_payload()

        self.assertFalse((self.work / "workspace/conversations/bindings.json").exists())

    def test_refresh_and_tree_return_explicit_mode_without_creating_store(self):
        for call in (refresh_work_conversations, refresh_conversation_tree):
            result = call(self.work, codex_home=Path(self.temp.name) / "missing")
            self.assertEqual(result["status"], "metadata_only")
            self.assert_no_archive_payload()
        store = self.work / "workspace/conversations"
        self.assertFalse((store / "bindings.json").exists())
        self.assertFalse((store / "manifest.json").exists())
        self.assertFalse((store / "tree-discovery-cache.json").exists())

    def test_archive_does_not_bind_or_capture(self):
        result = archive_conversation(self.work, thread_id="11111111-1111-4111-8111-111111111111",
                                      codex_home=Path(self.temp.name) / "missing",
                                      binding_reason="explicit task association")
        self.assertEqual(result["status"], "metadata_only")
        self.assertEqual(result.get("binding_status"), "metadata_only")
        self.assert_no_archive_payload()

    def test_pending_binding_is_retained_and_reported(self):
        pending = self.work / "workspace/pending-conversation-binding.json"
        pending.write_text(json.dumps({"thread_id": "11111111-1111-4111-8111-111111111111"}))
        result = retry_pending_binding(self.work)
        self.assertEqual(result["status"], "metadata_only")
        self.assertTrue(pending.exists())
        self.assert_no_archive_payload()

    def test_invalid_policy_keeps_pending_binding_and_reports_explicit_error(self):
        pending = self.work / "workspace/pending-conversation-binding.json"
        pending.write_text(json.dumps({"thread_id": "11111111-1111-4111-8111-111111111111"}))
        (self.work / "workspace/conversations/retention-policy.json").write_text('{broken')

        result = retry_pending_binding(self.work)

        self.assertEqual(result["status"], "retention_policy_invalid")
        self.assertTrue(pending.exists())
        self.assert_no_archive_payload()

    def test_maintenance_reports_invalid_policy_without_retrying_archive(self):
        pending = self.work / "workspace/pending-conversation-binding.json"
        pending.write_text(json.dumps({"thread_id": "11111111-1111-4111-8111-111111111111"}))
        (self.work / "workspace/conversations/retention-policy.json").write_text('{broken')

        from aippt_studio.maintenance import run_maintenance
        result = run_maintenance(self.work.parent.parent, include_model_reviews=False)

        binding_errors = [row for row in result["errors"] if row.get("stage") == "binding"]
        self.assertEqual(binding_errors[0]["result"]["status"], "retention_policy_invalid")
        self.assertTrue(pending.exists())
        self.assert_no_archive_payload()

    def test_maintenance_reports_invalid_policy_without_pending_intent(self):
        (self.work / "workspace/conversations/retention-policy.json").write_text('{broken')

        from aippt_studio.maintenance import run_maintenance
        result = run_maintenance(self.work.parent.parent, include_model_reviews=False)

        self.assertTrue(any(row.get("stage") == "archive" and
                            row.get("result", {}).get("status") == "retention_policy_invalid"
                            for row in result["errors"]))
        self.assert_no_archive_payload()

    def test_closure_keeps_invalid_policy_in_partial_state_without_backfill(self):
        from aippt_studio.maintenance import closure_evidence
        (self.work / "workspace/conversations/retention-policy.json").write_text(json.dumps({"mode": "unknown"}))

        result = closure_evidence(self.work)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["archive_status"], "retention_policy_invalid")
        self.assertEqual(result["binding_retry"]["status"], "none")
        self.assert_no_archive_payload()

    def test_conversation_sync_cli_exits_nonzero_for_invalid_policy(self):
        from typer.testing import CliRunner
        from aippt_studio.cli import app
        (self.work / "workspace/conversations/retention-policy.json").write_text('{broken')

        result = CliRunner().invoke(app, ["conversation-sync", "--work-dir", str(self.work), "--json"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('"status": "retention_policy_invalid"', result.stdout)
        self.assert_no_archive_payload()

    def test_project_learning_ignores_archive_sources_in_metadata_only_mode(self):
        result = _sources(self.work)
        self.assertEqual(result, [])
        self.assert_no_archive_payload()

    def test_invalid_policy_fails_closed_for_all_archive_entry_points(self):
        policy = self.work / "workspace/conversations/retention-policy.json"
        for invalid in ("{broken", json.dumps({"mode": "future_mode"}), json.dumps([])):
            policy.write_text(invalid)
            calls = (
                lambda: bind_conversation(self.work, thread_id="11111111-1111-4111-8111-111111111111",
                    codex_home=Path(self.temp.name) / "missing", binding_reason="explicit task association"),
                lambda: refresh_work_conversations(self.work, codex_home=Path(self.temp.name) / "missing"),
                lambda: refresh_conversation_tree(self.work, codex_home=Path(self.temp.name) / "missing"),
                lambda: archive_conversation(self.work, thread_id="11111111-1111-4111-8111-111111111111",
                    codex_home=Path(self.temp.name) / "missing", binding_reason="explicit task association"),
            )
            for call in calls:
                result = call()
                self.assertEqual(result["status"], "retention_policy_invalid")
            self.assertFalse((self.work / "workspace/conversations/bindings.json").exists())
            self.assert_no_archive_payload()

    def test_project_learning_rejects_invalid_retention_policy(self):
        policy = self.work / "workspace/conversations/retention-policy.json"
        for invalid in ("{broken", json.dumps({"mode": "future_mode"}), json.dumps([])):
            policy.write_text(invalid)
            with self.assertRaisesRegex(ValueError, "retention policy"):
                _sources(self.work)

    def test_project_review_reports_retention_limited_without_claiming_completion(self):
        repo = Path(self.temp.name) / "repo"
        work = repo / "work" / "1"
        store = work / "workspace/conversations"
        store.mkdir(parents=True)
        (work / "work.json").write_text(json.dumps({"work_id": "1", "status": "completed"}))
        (store / "retention-policy.json").write_text(json.dumps({"mode": "metadata_only"}))
        config = repo / "任务知识库/evolution/config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"project_reviews": {"enabled": True}}))

        result = run_project_learning(repo, only_work_ids=["1"])

        self.assertEqual(result["status"], "retention_limited")
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["retention_limited"][0]["status"], "retention_limited")
        self.assertFalse(result.get("review_complete", False))
        self.assertFalse((repo / "任务知识库/project_reviews/reports/work-1.md").exists())


if __name__ == "__main__":
    unittest.main()


class MinimalWorkRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name) / "work" / "1"
        (self.work / "workspace").mkdir(parents=True)
        (self.work / "work.json").write_text(json.dumps({"work_id": "1", "storage_profile": "minimal",
                                                          "conversation_retention": "disabled"}))

    def test_conversation_operations_skip_without_creating_archive_tree(self):
        for call in (bind_conversation, archive_conversation):
            result = call(self.work, thread_id="11111111-1111-4111-8111-111111111111",
                          codex_home=Path(self.temp.name) / "missing", binding_reason="explicit")
            self.assertEqual(result["status"], "disabled")
        for call in (refresh_work_conversations, refresh_conversation_tree):
            result = call(self.work, codex_home=Path(self.temp.name) / "missing")
            self.assertEqual(result["status"], "disabled")
        self.assertFalse((self.work / "workspace/conversations").exists())
        self.assertEqual(_sources(self.work), [])
