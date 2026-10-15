from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ..adapter_contracts import AdapterBuildRequest, AdapterBuildResult, CapabilityMaturity
from ..models import ProductionRoute
from .base import ContractProcessAdapter


class TemplateAdapter(ContractProcessAdapter):
    def build(self, request: AdapterBuildRequest, job_root: Path) -> AdapterBuildResult:
        from ..hybrid_job import BINDING, build_bound_hybrid
        if (job_root / BINDING).exists() or (job_root / BINDING).is_symlink():
            return build_bound_hybrid(request, job_root)
        return super().build(request, job_root)

    def __init__(self, *, project_root: Path, upstream_root: Path, reviewed_commit: str | None) -> None:
        self.project_root = project_root
        super().__init__(
            route=ProductionRoute.TEMPLATE,
            name="pptx-automizer",
            version="0.8.2",
            command_environment="AIPPT_TEMPLATE_ADAPTER",
            acceptance_file=project_root / "adapter-acceptance" / "template.json",
            upstream_path=upstream_root / "pptx-automizer",
            reviewed_commit=reviewed_commit,
            default_command=project_root / "scripts" / "adapters" / "template_adapter.py",
        )

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def maturity(self) -> CapabilityMaturity:
        """Do not advertise template automation when its locked npm runtime drifted."""

        maturity = super().maturity()
        if not maturity.is_automatic:
            return maturity
        package = self.project_root / "node_modules" / "pptx-automizer" / "package.json"
        lock = self.project_root / "package-lock.json"
        runner = self.project_root / "node" / "template_adapter.mjs"
        try:
            package_meta = json.loads(package.read_text(encoding="utf-8"))
            acceptance = json.loads(self.acceptance_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return CapabilityMaturity.ACCEPTANCE_TESTED
        runtime = acceptance.get("runtime")
        bridge = acceptance.get("bridge")
        if (
            package_meta.get("version") != self.version
            or not lock.is_file()
            or not runner.is_file()
            or not isinstance(runtime, dict)
            or runtime.get("package") != "pptx-automizer"
            or runtime.get("version") != self.version
            or runtime.get("lock_file") != "package-lock.json"
            or runtime.get("lock_sha256") != self._sha256(lock)
            or not isinstance(bridge, dict)
            or bridge.get("node_runner") != "node/template_adapter.mjs"
            or bridge.get("node_runner_sha256") != self._sha256(runner)
        ):
            return CapabilityMaturity.ACCEPTANCE_TESTED
        return maturity
