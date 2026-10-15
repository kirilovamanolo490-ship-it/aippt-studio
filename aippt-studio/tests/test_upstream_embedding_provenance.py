from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from aippt_studio import upstream_tools as tools


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(tools, "workspace_output", lambda output: output)
    monkeypatch.setattr(tools, "_row", lambda *args, **kwargs: {
        "path": str(tmp_path), "commit_sha": "fixture-commit", "source_url": "https://example.invalid/source",
        "license": {"spdx": "MIT"}, "source_present": True,
    })


@pytest.mark.parametrize(("provider", "operation"), [("echarts", "chart"), ("mermaid-cli", "diagram")])
def test_support_embedding_bytes_match_result_provenance_and_receipt(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                                   provider: str, operation: str):
    _isolate(monkeypatch, tmp_path)

    def render(kind: str, source: Path, output: Path) -> dict:
        output.write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>结构测试</text></svg>', encoding="utf-8")
        embedded = output.with_suffix(".embed.html")
        encoded = base64.b64encode(output.read_bytes()).decode("ascii")
        embedded.write_text(f'<img alt="{kind}" src="data:image/svg+xml;base64,{encoded}">\n', encoding="utf-8")
        payload = {"status": "succeeded", "html_embedding": str(embedded), "html_embedding_sha256": _sha(embedded)}
        output.with_suffix(".provenance.json").write_text(json.dumps(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(tools, "render_support", render)
    output = tmp_path / "call"
    receipt = tools.invoke(provider, operation, {"source": str(tmp_path / "source.json")}, output)
    embedded = output / "asset.embed.html"
    provenance = json.loads((output / "asset.provenance.json").read_text())
    artifact = next(item for item in receipt["artifacts"] if item["path"] == "asset.embed.html")
    assert embedded.read_text().startswith(f'<img alt="{operation}"')
    assert _sha(embedded) == receipt["result"]["html_embedding_sha256"] == provenance["html_embedding_sha256"] == artifact["sha256"]
    saved = json.loads((output / "receipt.json").read_text())
    assert saved["result"]["html_embedding_sha256"] == artifact["sha256"]


def test_icon_without_renderer_embedding_gets_generic_self_contained_fragment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _isolate(monkeypatch, tmp_path)

    def icon(root: Path, request: dict, output: Path) -> dict:
        (output / "asset.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        return {"artifact": "asset.svg"}

    monkeypatch.setattr(tools, "_icon", icon)
    receipt = tools.invoke("iconify-icon-sets", "icon", {"name": "demo:test"}, tmp_path / "call")
    embedded = tmp_path / "call/asset.embed.html"
    assert 'src="data:image/svg+xml;base64,' in embedded.read_text()
    artifact = next(item for item in receipt["artifacts"] if item["path"] == "asset.embed.html")
    assert artifact["sha256"] == _sha(embedded)
