from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from .models import ProductionRoute


_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{8,}"),
)


def contains_secret(value: object) -> bool:
    if isinstance(value, BaseModel):
        return contains_secret(value.__dict__)
    if isinstance(value, dict):
        return any(contains_secret(key) or contains_secret(item) for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return any(contains_secret(item) for item in value)
    if isinstance(value, str):
        return any(pattern.search(value) for pattern in _SECRET_PATTERNS)
    return False


def redact_secrets(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): redact_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, tuple):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("<redacted>", redacted)
        return redacted
    return value


def ensure_job_relative(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.lower().startswith("file:")
        or path.is_absolute()
        or ":" in path.parts[0]
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("adapter artifact paths must be safe job-relative paths")
    return path.as_posix()


class CapabilityMaturity(StrEnum):
    UNAVAILABLE = "unavailable"
    INSTALLED_ONLY = "installed_only"
    EXECUTABLE = "executable"
    VERSION_REVIEWED = "version_reviewed"
    ACCEPTANCE_TESTED = "acceptance_tested"
    AUTOMATIC_READY = "automatic_ready"

    @property
    def is_automatic(self) -> bool:
        return self == CapabilityMaturity.AUTOMATIC_READY


class AdapterStatus(StrEnum):
    SUCCEEDED = "succeeded"
    BLOCKED = "blocked"
    FAILED = "failed"


class EditabilityClass(StrEnum):
    NATIVE_EDITABLE = "native_editable"
    RECONSTRUCTED_EDITABLE = "reconstructed_editable"
    IMAGE_BASED = "image_based"


class AdapterFailure(BaseModel):
    category: str
    message: str
    retryable: bool = False
    fallback: str | None = None


class EngineReceipt(BaseModel):
    engine: str
    version: str
    command: list[str] = Field(default_factory=list)
    duration_ms: int | None = None
    reviewed_commit: str | None = None
    # The engine name and version establish only a label.  ``provenance``
    # records whether the engine was a checked-out source tree, a separately
    # locked runtime, or an explicitly configured external service.  It is
    # deliberately part of the receipt copied into external-engine manifests,
    # so a selected GitHub directory cannot later be mistaken for an executed
    # dependency.
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_secrets(self) -> "EngineReceipt":
        if contains_secret(self.__dict__):
            raise ValueError("engine receipt contains credential material")
        return self


class AdapterArtifact(BaseModel):
    kind: str
    path: str
    sha256: str
    size_bytes: int | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return ensure_job_relative(value)

    @field_validator("sha256")
    @classmethod
    def validate_hash(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("artifact sha256 must contain 64 hexadecimal characters")
        return value.lower()


class AdapterBuildRequest(BaseModel):
    schema_version: int = 1
    job_id: str
    route: ProductionRoute
    brief: str
    deck_plan: str
    evidence_ledger: str
    asset_plan: str | None = None
    asset_ledger: str | None = None
    output_root: str = "build/adapter-staging"
    input_roles: dict[str, str] = Field(default_factory=dict)
    cloud_allowed: bool = True
    template_application_required: bool = False
    requested_outputs: list[str] = Field(default_factory=lambda: ["pptx", "previews", "build_source"])

    @field_validator("brief", "deck_plan", "evidence_ledger", "output_root", "asset_plan", "asset_ledger")
    @classmethod
    def validate_paths(cls, value: str | None) -> str | None:
        return ensure_job_relative(value) if value is not None else None

    @model_validator(mode="after")
    def reject_secrets(self) -> "AdapterBuildRequest":
        if contains_secret(self.__dict__):
            raise ValueError("adapter request contains credential material")
        return self


class AdapterBuildResult(BaseModel):
    schema_version: int = 1
    status: AdapterStatus
    receipt: EngineReceipt | None = None
    editability: EditabilityClass | None = None
    artifacts: list[AdapterArtifact] = Field(default_factory=list)
    providers: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    failure: AdapterFailure | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_result(self) -> "AdapterBuildResult":
        if self.status == AdapterStatus.SUCCEEDED and (self.receipt is None or self.editability is None):
            raise ValueError("successful adapter results require a receipt and editability class")
        if self.status != AdapterStatus.SUCCEEDED and self.failure is None:
            raise ValueError("blocked or failed adapter results require a failure")
        if contains_secret(self.__dict__):
            raise ValueError("adapter result contains credential material")
        return self
