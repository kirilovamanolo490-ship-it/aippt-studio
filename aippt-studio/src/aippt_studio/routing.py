from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import InputRole, ProductionRoute, RouteDecision, SourceRecord


@dataclass(frozen=True)
class RoutePolicy:
    override: ProductionRoute | None = None
    override_reason: str = ""
    image_based_accepted: bool = False


def classify_input_roles(records: list[SourceRecord]) -> dict[str, InputRole]:
    roles: dict[str, InputRole] = {}
    for record in records:
        name = Path(record.original).name.lower()
        explicit = str(record.metadata.get("input_role", "")).strip().lower()
        if explicit:
            try:
                roles[record.id] = InputRole(explicit)
                continue
            except ValueError:
                pass
        if record.kind == "image":
            roles[record.id] = InputRole.ASSET
        elif record.kind == "pdf" and (
            record.metadata.get("extraction") == "no_text"
            or any(marker in name for marker in ("scan", "scanned", "扫描", "截图"))
        ):
            roles[record.id] = InputRole.FLATTENED_INPUT
        elif record.kind == "powerpoint":
            roles[record.id] = InputRole.CONTENT
        else:
            roles[record.id] = InputRole.CONTENT
    return roles


def select_route(roles: dict[str, InputRole], policy: RoutePolicy) -> RouteDecision:
    if policy.override is not None:
        reason = policy.override_reason.strip() or "user selected this production route"
        return RouteDecision(route=policy.override, source="user_override", reason=reason, input_roles=roles)
    role_values = set(roles.values())
    if InputRole.EDIT_TARGET in role_values or InputRole.TEMPLATE in role_values:
        return RouteDecision(
            route=ProductionRoute.TEMPLATE,
            source="automatic",
            reason="an input is explicitly designated as the edit target or mandatory template",
            input_roles=roles,
        )
    if InputRole.FLATTENED_INPUT in role_values:
        return RouteDecision(
            route=ProductionRoute.REBUILD,
            source="automatic",
            reason="the source is flattened and requires editable reconstruction",
            input_roles=roles,
        )
    if policy.image_based_accepted and InputRole.VISUAL_REFERENCE in role_values:
        return RouteDecision(
            route=ProductionRoute.VISUAL,
            source="automatic",
            reason="an image-based alternate was explicitly accepted",
            input_roles=roles,
        )
    return RouteDecision(
        route=ProductionRoute.NATIVE,
        source="automatic",
        reason="the inputs can be authored as a native editable presentation",
        input_roles=roles,
    )
