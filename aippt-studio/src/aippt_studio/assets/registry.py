from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ..models import EvidenceLedger
from .licenses import license_is_automatic


class AssetRecord(BaseModel):
    id: str
    kind: str
    path: str | None = None
    source_url: str
    license: str | None = None
    license_evidence: str | None = None
    automatic: bool = False
    caveat: str | None = None


class AssetRegistry:
    def __init__(self, records: list[AssetRecord]) -> None:
        self.records = {record.id: record for record in records}

    def get(self, asset_id: str, *, require_automatic: bool = True) -> AssetRecord:
        try:
            record = self.records[asset_id]
        except KeyError as error:
            raise KeyError(f"unknown asset provider: {asset_id}") from error
        if require_automatic and not record.automatic:
            raise ValueError(f"asset provider is not licensed and available for automatic use: {asset_id}")
        return record

    def report(self) -> list[dict[str, object]]:
        return [record.model_dump(mode="json") for record in self.records.values()]


def load_asset_registry(lock_path: Path) -> AssetRegistry:
    payload = json.loads(lock_path.read_text(encoding="utf-8"))
    records: list[AssetRecord] = []
    kind_by_integration = {"asset_catalog": "icons", "font_asset": "font"}
    for item in payload.get("upstreams", []):
        if item.get("lifecycle", "active") == "retired":
            continue
        integration = item.get("integration")
        if integration not in kind_by_integration:
            continue
        license_data = item.get("license", {})
        acquisition = item.get("acquisition", {})
        spdx = license_data.get("spdx")
        local_path = acquisition.get("local_path")
        if local_path and not Path(local_path).is_absolute():
            local_path = str((lock_path.parent / local_path).resolve())
        available = bool(local_path and Path(local_path).exists() and acquisition.get("status") != "pending_snapshot")
        records.append(
            AssetRecord(
                id=item["id"],
                kind=kind_by_integration[integration],
                path=local_path,
                source_url=item.get("source_url", ""),
                license=spdx,
                license_evidence=license_data.get("evidence"),
                automatic=available and license_is_automatic(spdx),
                caveat=license_data.get("caveat"),
            )
        )
    return AssetRegistry(records)


def record_asset_use(ledger: EvidenceLedger, asset: AssetRecord, purpose: str) -> None:
    ledger.external_assets.append(
        {
            "asset_id": asset.id,
            "kind": asset.kind,
            "source_url": asset.source_url,
            "license": asset.license,
            "license_evidence": asset.license_evidence,
            "purpose": purpose,
        }
    )
