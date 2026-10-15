from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aippt_studio.adapter_contracts import (
    AdapterArtifact,
    AdapterBuildRequest,
    AdapterBuildResult,
    AdapterFailure,
    AdapterStatus,
    CapabilityMaturity,
    EditabilityClass,
    EngineReceipt,
    ensure_job_relative,
    redact_secrets,
)
from aippt_studio.models import ExternalEngineManifest, ProductionRoute


def test_adapter_contract_accepts_job_relative_artifacts() -> None:
    request = AdapterBuildRequest(
        job_id="adapter-contract",
        route=ProductionRoute.TEMPLATE,
        brief="planning/brief.json",
        deck_plan="planning/deck-plan.json",
        evidence_ledger="evidence-ledger.json",
        input_roles={"source-1": "template"},
        cloud_allowed=False,
    )
    result = AdapterBuildResult(
        status=AdapterStatus.SUCCEEDED,
        receipt=EngineReceipt(engine="pptx-automizer", version="0.8.2"),
        editability=EditabilityClass.NATIVE_EDITABLE,
        artifacts=[AdapterArtifact(kind="pptx", path="build/deck.pptx", sha256="a" * 64)],
    )

    assert request.route == ProductionRoute.TEMPLATE
    assert result.artifacts[0].path == "build/deck.pptx"


@pytest.mark.parametrize("path", ["C:/secret/file.pptx", "../outside.pptx", "/tmp/out.pptx", "file:///tmp/out.pptx"])
def test_adapter_contract_rejects_non_job_relative_paths(path: str) -> None:
    with pytest.raises(ValueError):
        ensure_job_relative(path)


def test_adapter_contract_rejects_secrets_before_persistence() -> None:
    with pytest.raises(ValidationError):
        AdapterBuildResult(
            status=AdapterStatus.BLOCKED,
            failure=AdapterFailure(category="provider", message="OPENAI_API_KEY=sk-test-secret"),
        )


def test_adapter_contract_redacts_nested_secret_values() -> None:
    payload = redact_secrets({"error": "OPENAI_API_KEY=sk-test-secret-value", "command": ["Bearer abcdefghijk"]})
    assert "sk-test" not in str(payload)
    assert "abcdefghijk" not in str(payload)


def test_external_manifest_rejects_provider_credentials() -> None:
    with pytest.raises(ValidationError):
        ExternalEngineManifest(
            job_id="secret-manifest",
            route=ProductionRoute.VISUAL,
            engine="visual",
            version="1",
            editable_class="image_based",
            deck_plan_sha256="a" * 64,
            pptx_sha256="b" * 64,
            preview_hashes={},
            providers=[{"provider": "example", "secret": "sk-test-secret-value"}],
        )


def test_capability_maturity_is_ordered_by_automatic_readiness() -> None:
    assert CapabilityMaturity.AUTOMATIC_READY.is_automatic is True
    assert CapabilityMaturity.INSTALLED_ONLY.is_automatic is False
