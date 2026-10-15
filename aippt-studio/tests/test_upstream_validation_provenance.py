from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from aippt_studio import renderer, upstream_tools


def isolate(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(upstream_tools, "workspace_output", lambda path: path)
    monkeypatch.setattr(upstream_tools, "_row", lambda *args, **kwargs: {
        "path": str(tmp_path), "commit_sha": "fixture-commit",
        "source_url": "https://example.invalid/officecli", "license": {"spdx": "Apache-2.0"},
    })
    source = tmp_path / "source.pptx"
    source.write_bytes(b"fixture bytes; validator is replaced, no presentation is generated")
    return source


def test_validation_receipt_binds_unchanged_source_bytes(monkeypatch, tmp_path):
    source = isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(renderer, "officecli_validate", lambda path: {"status": "passed", "version": "fixture"})
    result = upstream_tools.invoke("officecli", "validate", {"source": str(source)}, tmp_path / "call")
    identity = {"path": str(source), "bytes": source.stat().st_size,
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    assert result["status"] == "succeeded"
    assert result["input_source"] == {"before": identity, "after": identity, "status": "verified"}
    saved = json.loads((tmp_path / "call/receipt.json").read_text())
    assert saved["input_source"] == result["input_source"]
    # Later customer edits must not rewrite the input identity in this receipt.
    source.write_bytes(b"later revision")
    assert saved["input_source"]["before"]["sha256"] != hashlib.sha256(source.read_bytes()).hexdigest()


@pytest.mark.parametrize("change", ["rewrite", "delete"])
def test_validation_source_mutation_cannot_leave_passed_receipt(monkeypatch, tmp_path, change):
    source = isolate(monkeypatch, tmp_path)

    def validate(path):
        path.write_bytes(b"new bytes") if change == "rewrite" else path.unlink()
        return {"status": "passed", "version": "fixture"}

    monkeypatch.setattr(renderer, "officecli_validate", validate)
    with pytest.raises(ValueError, match="source changed or disappeared"):
        upstream_tools.invoke("officecli", "validate", {"source": str(source)}, tmp_path / "call")
    saved = json.loads((tmp_path / "call/receipt.json").read_text())
    assert saved["status"] == "failed"
    assert saved["input_source"]["status"] == "changed"
    assert saved["input_source"]["before"] != saved["input_source"]["after"]
    assert saved.get("result", {}).get("status") != "passed"


def test_validator_error_retains_input_identity_and_failure(monkeypatch, tmp_path):
    source = isolate(monkeypatch, tmp_path)

    def validate(path):
        raise ValueError("OfficeCLI rejected input")

    monkeypatch.setattr(renderer, "officecli_validate", validate)
    with pytest.raises(ValueError, match="OfficeCLI rejected input"):
        upstream_tools.invoke("officecli", "validate", {"source": str(source)}, tmp_path / "call")
    saved = json.loads((tmp_path / "call/receipt.json").read_text())
    assert saved["status"] == "failed"
    assert saved["input_source"]["status"] == "verified"
    assert "OfficeCLI rejected input" in saved["error"]
