from __future__ import annotations

from pathlib import Path

from ..models import ProductionRoute
from .base import ContractProcessAdapter


class RebuildAdapter(ContractProcessAdapter):
    def __init__(self, *, project_root: Path, upstream_root: Path, reviewed_commit: str | None) -> None:
        super().__init__(
            route=ProductionRoute.REBUILD,
            name="image-to-editable-ppt-skill",
            version=reviewed_commit[:12] if reviewed_commit else "unversioned",
            command_environment="AIPPT_REBUILD_ADAPTER",
            acceptance_file=project_root / "adapter-acceptance" / "rebuild.json",
            upstream_path=upstream_root / "image" / "image-to-editable-ppt-skill",
            reviewed_commit=reviewed_commit,
            default_command=project_root / "scripts" / "adapters" / "rebuild_adapter.py",
        )
