from __future__ import annotations

import json
from pathlib import Path

import pytest

from aippt_studio.assets import load_asset_registry, record_asset_use
from aippt_studio.models import EvidenceLedger


def test_asset_registry_blocks_ambiguous_license_and_records_provenance(tmp_path: Path) -> None:
    licensed = tmp_path / "icons"
    licensed.mkdir()
    ambiguous = tmp_path / "unknown"
    ambiguous.mkdir()
    lock = tmp_path / "upstreams.lock.json"
    lock.write_text(
        json.dumps(
            {
                "upstreams": [
                    {"id": "icons", "source_url": "https://example/icons", "integration": "asset_catalog", "license": {"spdx": "MIT", "evidence": "LICENSE"}, "acquisition": {"status": "present_git", "local_path": str(licensed)}},
                    {"id": "unknown", "source_url": "https://example/unknown", "integration": "font_asset", "license": {"spdx": "NOASSERTION"}, "acquisition": {"status": "present_git", "local_path": str(ambiguous)}},
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = load_asset_registry(lock)
    asset = registry.get("icons")
    with pytest.raises(ValueError):
        registry.get("unknown")
    ledger = EvidenceLedger()
    record_asset_use(ledger, asset, "slide icon")
    assert ledger.external_assets[0]["license"] == "MIT"
