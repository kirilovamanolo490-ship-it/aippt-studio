"""Keep obsolete delivery packages as evidence without advertising them live.

This module deliberately depends only on storage/model primitives.  Mutations
to a current deck originate in several routes (policy, assets, scenes and
revisions), so importing workflow code here would create a circular dependency
and make those routes silently skip the same invalidation rule.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .models import utc_now
from .storage import write_json


def supersede_delivery_artifacts(
    root: Path,
    reason: str,
    *,
    receipt_directory: str = "reviews/delivery-invalidations",
) -> dict[str, Any] | None:
    """Archive the current delivery package when its source has changed.

    The old package remains byte-for-byte available under ``history`` for a
    later review, while the recreated ``deliverables`` directory is empty.  A
    status request can therefore never present a candidate that was exported
    from an older plan, policy, asset selection, scene, or revision.

    ``receipt_directory`` exists for the legacy intent-invalidations receipt
    stream; ordinary callers use the common delivery invalidation stream.
    """

    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("delivery invalidation requires a reason")
    root = Path(root).resolve()
    delivery = root / "deliverables"
    if not delivery.is_dir():
        return None
    files = sorted(
        (path for path in delivery.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(delivery).as_posix(),
    )
    if not files:
        return None

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "kind": "delivery_artifacts_superseded",
        "superseded_at": utc_now(),
        "reason": normalized_reason,
        "files": [path.relative_to(delivery).as_posix() for path in files],
        "rule": "Superseded delivery packages remain historical evidence and cannot be reported as current outputs.",
    }
    token = hashlib.sha256(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    archive = root / "history" / "superseded-deliverables" / token
    archive.parent.mkdir(parents=True, exist_ok=True)
    if archive.exists():
        archive = archive.with_name(f"{token}-{uuid.uuid4().hex[:8]}")
    receipt["archive_path"] = archive.relative_to(root).as_posix()
    receipt_path = root / receipt_directory / f"{token}-deliverables.json"

    moved = False
    try:
        # Move the whole package so its manifest and portable assets keep the
        # same relative relationships they had when it was exported.
        delivery.replace(archive)
        moved = True
        delivery.mkdir(parents=True, exist_ok=True)
        write_json(receipt_path, receipt)
    except Exception:
        # Do not leave an unreceipted archive or an accidental empty live
        # delivery surface if recording the invalidation itself failed.
        if moved and archive.exists() and delivery.is_dir() and not any(delivery.iterdir()):
            delivery.rmdir()
            archive.replace(delivery)
        raise
    return receipt
