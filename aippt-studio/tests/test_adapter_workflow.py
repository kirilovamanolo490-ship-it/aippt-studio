from __future__ import annotations

from pathlib import Path

from aippt_studio.adapter_contracts import (
    AdapterArtifact,
    AdapterBuildRequest,
    AdapterBuildResult,
    AdapterStatus,
    CapabilityMaturity,
    EditabilityClass,
    EngineReceipt,
)
from aippt_studio.adapters.registry import AdapterRegistry
from aippt_studio.ingest import ingest_sources
from aippt_studio.models import JobState, ProductionRoute
from aippt_studio.renderer import sha256
from aippt_studio.storage import job_dir, load_job, read_json
from aippt_studio.workflow import approve_gate, create_job, run_job


class FixtureTemplateAdapter:
    route = ProductionRoute.TEMPLATE
    name = "fixture-template"
    version = "1"

    def maturity(self) -> CapabilityMaturity:
        return CapabilityMaturity.AUTOMATIC_READY

    def build(self, request: AdapterBuildRequest, root: Path) -> AdapterBuildResult:
        staging = root / request.output_root
        staging.mkdir(parents=True, exist_ok=True)
        deck = staging / "deck.pptx"
        deck.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        source = staging / "template-map.json"
        source.write_text('{"fixture": true}', encoding="utf-8")
        return AdapterBuildResult(
            status=AdapterStatus.SUCCEEDED,
            receipt=EngineReceipt(engine=self.name, version=self.version),
            editability=EditabilityClass.NATIVE_EDITABLE,
            artifacts=[
                AdapterArtifact(kind="pptx", path=deck.relative_to(root).as_posix(), sha256=sha256(deck)),
                AdapterArtifact(kind="build_source", path=source.relative_to(root).as_posix(), sha256=sha256(source)),
            ],
        )


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source.md"
    source.write_text(
        "\n".join(
            [
                "收入增长 28%，续约率达到 93%，经营质量继续改善。",
                "企业客户贡献新增收入的 72%，业务结构更加聚焦。",
                "交付周期从 21 天缩短到 14 天，效率改善得到验证。",
                "关键岗位仍存在资源风险，需要在第三季度完成补齐。",
                "下一阶段应完成标杆客户复盘并统一项目交付模板。",
                "管理层需要建立增长、续约和交付质量的统一指标。",
                "汇报必须形成责任人明确的行动清单和检查机制。",
            ]
        ),
        encoding="utf-8",
    )
    return source


def test_automatic_adapter_reaches_final_review_with_common_manifest_and_qa(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("aippt_studio.workflow.route_ready", lambda route: (True, "fixture"))
    monkeypatch.setattr(
        "aippt_studio.workflow.adapter_registry",
        lambda: AdapterRegistry([FixtureTemplateAdapter()]),
    )
    job = create_job("自动模板路线", job_id="automatic-template", route=ProductionRoute.TEMPLATE)
    ingest_sources(job.id, [str(_source(tmp_path))])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    assert load_job(job.id).state == JobState.FULL_BUILD

    result = run_job(job.id)

    assert result["state"] == JobState.FINAL_REVIEW.value
    root = job_dir(job.id)
    manifest = read_json(root / "build" / "external-engine-manifest.json")
    assert manifest["engine"] == "fixture-template"
    assert manifest["adapter_receipt"]["version"] == "1"
    assert manifest["input_hashes"]
    # A flexible default page count must not be filled by a generic closing
    # page.  The adapter still has to preview every page the reviewed plan did
    # produce, whatever its source-grounded cardinality is.
    plan = read_json(root / "planning" / "deck-plan.json")
    assert set(manifest["preview_hashes"]) == {slide["id"] for slide in plan["slides"]}
    assert (root / "build" / "adapter-last-result.json").is_file()
    approve_gate(job.id, "final")
