from __future__ import annotations

from pathlib import Path

from ..models import ProductionRoute
from .base import ContractProcessAdapter


class VisualAdapter(ContractProcessAdapter):
    def __init__(self, *, project_root: Path, upstream_root: Path, reviewed_commit: str | None) -> None:
        super().__init__(
            route=ProductionRoute.VISUAL,
            name="codex-ppt-skill",
            version=reviewed_commit[:12] if reviewed_commit else "unversioned",
            command_environment="AIPPT_VISUAL_ADAPTER",
            acceptance_file=project_root / "adapter-acceptance" / "visual.json",
            upstream_path=upstream_root / "image" / "codex-ppt-skill",
            reviewed_commit=reviewed_commit,
            default_command=project_root / "scripts" / "adapters" / "visual_adapter.py",
        )
