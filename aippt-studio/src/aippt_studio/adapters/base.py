from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path

from ..adapter_contracts import (
    AdapterBuildRequest,
    AdapterBuildResult,
    AdapterFailure,
    AdapterStatus,
    CapabilityMaturity,
    EngineReceipt,
    redact_secrets,
)
from ..models import ProductionRoute
from ..storage import write_json


class Adapter(ABC):
    route: ProductionRoute
    name: str
    version: str

    @abstractmethod
    def maturity(self) -> CapabilityMaturity:
        raise NotImplementedError

    @abstractmethod
    def build(self, request: AdapterBuildRequest, job_root: Path) -> AdapterBuildResult:
        raise NotImplementedError


class ContractProcessAdapter(Adapter):
    command_environment: str
    acceptance_file: Path
    upstream_path: Path
    reviewed_commit: str | None
    default_command: Path | None
    timeout_seconds: int = 900

    def __init__(
        self,
        *,
        route: ProductionRoute,
        name: str,
        version: str,
        command_environment: str,
        acceptance_file: Path,
        upstream_path: Path,
        reviewed_commit: str | None,
        default_command: Path | None = None,
    ) -> None:
        self.route = route
        self.name = name
        self.version = version
        self.command_environment = command_environment
        self.acceptance_file = acceptance_file
        self.upstream_path = upstream_path
        self.reviewed_commit = reviewed_commit
        self.default_command = default_command

    def command(self) -> Path | None:
        configured = os.environ.get(self.command_environment)
        command = Path(configured).expanduser().resolve() if configured else self.default_command
        return command if command.is_file() else None

    @property
    def _executes_checked_out_source(self) -> bool:
        """Only these adapters execute code from their Git checkout directly.

        Template reuse invokes a separately locked npm runtime instead.  The
        distinction belongs in code instead of a prose-only catalog because a
        changed checkout must stop direct execution but must not falsely be
        reported as the runtime used by the template adapter.
        """

        return self.name in {"codex-ppt-skill", "image-to-editable-ppt-skill"}

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _checkout_integrity_error(self) -> str | None:
        """Return a stable error when a direct-source adapter changed its tree."""

        if not self._executes_checked_out_source:
            return None
        if not (self.upstream_path / ".git").exists():
            return "direct-source adapter checkout has no Git identity"
        from ..upstreams import git_snapshot

        snapshot = git_snapshot(self.upstream_path)
        if snapshot.get("head") != self.reviewed_commit:
            return "direct-source adapter checkout no longer matches the reviewed commit"
        if not snapshot.get("clean"):
            return "direct-source adapter checkout has uncommitted changes"
        return None

    def _receipt_provenance(self, command: Path) -> dict[str, object]:
        """Small, portable claim about what the adapter actually executed."""

        bridge: dict[str, object] = {
            "command": command.name,
            "command_sha256": self._sha256(command),
            "acceptance_sha256": self._sha256(self.acceptance_file),
        }
        if self._executes_checked_out_source:
            return {
                "schema_version": 1,
                "execution_kind": "direct_checked_out_source",
                "source_checkout": {
                    "provider": self.name,
                    "reviewed_commit": self.reviewed_commit,
                    "verified_before_and_after_execution": True,
                },
                "bridge": bridge,
                "claim_boundary": "Proves this source-bound adapter process and its declared artifacts, not visual acceptance or delivery adoption.",
            }
        return {
            "schema_version": 1,
            "execution_kind": "managed_runtime",
            "source_checkout": {
                "provider": self.name,
                "role": "reference and reviewed-source provenance; this checkout is not the runtime process",
                "reviewed_commit": self.reviewed_commit,
            },
            "bridge": bridge,
            "claim_boundary": "Proves this adapter process and its declared artifacts; the runtime identity is recorded by the adapter bridge.",
        }

    def _failure(
        self,
        *,
        category: str,
        message: str,
        command: Path,
        started: float | None = None,
        retryable: bool = True,
        fallback: str | None = None,
    ) -> AdapterBuildResult:
        """Create a failure receipt and never hide a direct-source mutation."""

        integrity_error = self._checkout_integrity_error()
        if integrity_error:
            category = "source_repository_modified"
            message = integrity_error
            retryable = False
            fallback = "restore_locked_checkout"
        receipt = EngineReceipt(
            engine=self.name,
            version=self.version,
            command=[command.name, "--request", "<job-relative>", "--result", "<job-relative>"],
            duration_ms=round((time.monotonic() - started) * 1000) if started is not None else None,
            reviewed_commit=self.reviewed_commit,
            provenance=self._receipt_provenance(command),
        )
        return AdapterBuildResult(
            status=AdapterStatus.FAILED,
            receipt=receipt,
            failure=AdapterFailure(category=category, message=message[:1000], retryable=retryable, fallback=fallback),
        )

    def maturity(self) -> CapabilityMaturity:
        if not self.upstream_path.is_dir():
            return CapabilityMaturity.UNAVAILABLE
        configured = os.environ.get(self.command_environment)
        if not os.environ.get(self.command_environment):
            entry = {"codex-ppt-skill": "skills/codex-ppt/scripts/assemble_ppt.py", "image-to-editable-ppt-skill": "skills/image-to-editable-ppt/cli/editppt/cli.py"}.get(self.name)
            if entry and not (self.upstream_path / entry).is_file():
                return CapabilityMaturity.INSTALLED_ONLY
        command = self.command()
        if command is None:
            return CapabilityMaturity.INSTALLED_ONLY
        # An arbitrary environment override has not gone through the recorded
        # acceptance fixture. It remains a manual option, never an automatic
        # substitution for the Studio-owned bridge.
        if configured and self.default_command is not None and command.resolve() != self.default_command.resolve():
            return CapabilityMaturity.EXECUTABLE
        if not self.reviewed_commit:
            return CapabilityMaturity.EXECUTABLE
        if self._checkout_integrity_error() is not None:
            return CapabilityMaturity.EXECUTABLE
        if not self.acceptance_file.is_file():
            return CapabilityMaturity.VERSION_REVIEWED
        try:
            payload = json.loads(self.acceptance_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return CapabilityMaturity.VERSION_REVIEWED
        if payload.get("status") != "passed":
            return CapabilityMaturity.VERSION_REVIEWED
        if payload.get("engine") != self.name or payload.get("reviewed_commit") != self.reviewed_commit:
            return CapabilityMaturity.ACCEPTANCE_TESTED
        bridge = payload.get("bridge")
        if not isinstance(bridge, dict) or bridge.get("command") != command.name or bridge.get("command_sha256") != self._sha256(command):
            return CapabilityMaturity.ACCEPTANCE_TESTED
        return CapabilityMaturity.AUTOMATIC_READY

    def build(self, request: AdapterBuildRequest, job_root: Path) -> AdapterBuildResult:
        maturity = self.maturity()
        if not maturity.is_automatic:
            return AdapterBuildResult(
                status=AdapterStatus.BLOCKED,
                receipt=EngineReceipt(
                    engine=self.name,
                    version=self.version,
                    reviewed_commit=self.reviewed_commit,
                ),
                failure=AdapterFailure(
                    category="adapter_not_promoted",
                    message=f"{self.name} capability maturity is {maturity.value}",
                    fallback="manual_upstream_adapter",
                ),
                metadata={"maturity": maturity.value},
            )
        command = self.command()
        assert command is not None
        build_dir = job_root / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        request_path = build_dir / "adapter-request.json"
        result_path = build_dir / "adapter-result.json"
        write_json(request_path, request)
        result_path.unlink(missing_ok=True)
        argv = [str(command), "--request", str(request_path), "--result", str(result_path)]
        if command.suffix.lower() == ".py":
            argv.insert(0, sys.executable)
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                cwd=job_root,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return self._failure(category="process", message=str(error), command=command, started=started)
        if completed.returncode != 0 or not result_path.is_file():
            message = str(redact_secrets((completed.stderr or completed.stdout or "adapter returned no result").strip()))
            return self._failure(category="process", message=message, command=command, started=started)
        try:
            raw_result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return self._failure(category="contract", message=f"adapter result is invalid JSON: {error}", command=command, started=started, retryable=False)
        redacted = redact_secrets(raw_result)
        write_json(result_path, redacted)
        try:
            result = AdapterBuildResult.model_validate(redacted)
        except ValueError as error:
            return self._failure(category="contract", message=f"adapter result violates the contract: {error}", command=command, started=started, retryable=False)
        integrity_error = self._checkout_integrity_error()
        if integrity_error:
            return self._failure(category="source_repository_modified", message=integrity_error, command=command, started=started, retryable=False, fallback="restore_locked_checkout")
        if result.receipt is not None:
            if result.receipt.engine != self.name or result.receipt.version != self.version:
                return self._failure(
                    category="receipt_identity",
                    message=f"adapter receipt identified {result.receipt.engine} {result.receipt.version}, expected {self.name} {self.version}",
                    command=command,
                    started=started,
                    retryable=False,
                )
            result.receipt.duration_ms = round((time.monotonic() - started) * 1000)
            result.receipt.reviewed_commit = self.reviewed_commit
            # Preserve adapter-specific runtime details (for example package
            # lock identity) while adding the common source/bridge claim.
            result.receipt.provenance = {
                **result.receipt.provenance,
                "adapter_process": self._receipt_provenance(command),
            }
        return result
