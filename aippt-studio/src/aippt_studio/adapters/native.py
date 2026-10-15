from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..adapter_contracts import AdapterBuildRequest, AdapterBuildResult, CapabilityMaturity
from ..models import ProductionRoute
from .base import Adapter


class NativeAdapter(Adapter):
    route = ProductionRoute.NATIVE
    name = "dom-to-pptx"
    version = "2.0.3"

    def __init__(
        self,
        runner: Callable[[AdapterBuildRequest, Path], AdapterBuildResult] | None = None,
        *,
        reviewed_commit: str | None = None,
    ) -> None:
        self.runner = runner
        self.reviewed_commit = reviewed_commit

    def maturity(self) -> CapabilityMaturity:
        return CapabilityMaturity.AUTOMATIC_READY

    def build(self, request: AdapterBuildRequest, job_root: Path) -> AdapterBuildResult:
        if self.runner is None:
            raise RuntimeError("native adapter requires the Studio native build pipeline")
        result = self.runner(request, job_root)
        if result.receipt is not None and result.receipt.engine == self.name and not result.receipt.reviewed_commit:
            result.receipt.reviewed_commit = self.reviewed_commit
        return result
