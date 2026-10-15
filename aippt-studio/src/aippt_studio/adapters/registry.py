from __future__ import annotations

import json
from pathlib import Path

from ..models import ProductionRoute
from .base import Adapter
from .native import NativeAdapter
from .rebuild import RebuildAdapter
from .template import TemplateAdapter
from .visual import VisualAdapter


def _reviewed_commits(lock_path: Path) -> dict[str, str]:
    if not lock_path.is_file():
        return {}
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        str(item.get("id")): str(item.get("runtime", {}).get("reviewed_commit") or item.get("commit_sha"))
        for item in payload.get("upstreams", [])
        if item.get("id") and item.get("commit_sha")
    }


def reviewed_commit(project_root: Path, upstream_id: str) -> str | None:
    return _reviewed_commits(project_root / "upstreams.lock.json").get(upstream_id)


class AdapterRegistry:
    def __init__(self, adapters: list[Adapter]) -> None:
        self._adapters = {adapter.route: adapter for adapter in adapters}

    def get(self, route: ProductionRoute) -> Adapter:
        try:
            return self._adapters[route]
        except KeyError as error:
            raise KeyError(f"no adapter registered for route: {route.value}") from error

    def all(self) -> list[Adapter]:
        return list(self._adapters.values())


def build_registry(project_root: Path, upstream_root: Path, native: NativeAdapter | None = None) -> AdapterRegistry:
    commits = _reviewed_commits(project_root / "upstreams.lock.json")
    return AdapterRegistry(
        [
            native or NativeAdapter(reviewed_commit=commits.get("dom-to-pptx")),
            TemplateAdapter(
                project_root=project_root,
                upstream_root=upstream_root,
                reviewed_commit=commits.get("pptx-automizer"),
            ),
            RebuildAdapter(
                project_root=project_root,
                upstream_root=upstream_root,
                reviewed_commit=commits.get("image-to-editable-ppt-skill"),
            ),
            VisualAdapter(
                project_root=project_root,
                upstream_root=upstream_root,
                reviewed_commit=commits.get("codex-ppt-skill"),
            ),
        ]
    )
