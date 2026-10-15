from __future__ import annotations


_BLOCKED = {"", "NOASSERTION", "PROPRIETARY", "UNKNOWN", "UNLICENSED", "NONE"}


def license_is_automatic(spdx: str | None) -> bool:
    normalized = (spdx or "").strip().upper()
    return normalized not in _BLOCKED and "UNKNOWN" not in normalized and "NOASSERTION" not in normalized
