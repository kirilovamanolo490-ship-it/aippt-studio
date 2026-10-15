from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from aippt_studio.assets.acquisition import (
    AssetAcquisitionError,
    acquire_asset,
    add_asset_need,
    asset_status,
)
from aippt_studio.ingest import ingest_sources
from aippt_studio.models import DeckPlan, JobBrief, JobState
from aippt_studio.storage import job_dir, load_job, read_json, read_model, write_json
from aippt_studio.workflow import WorkflowError, approve_gate, create_job, export_job, run_job


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source.md"
    source.write_text(
        "\n".join(
            [
                "收入增长 28%，核心客户续约率达到 93%。",
                "企业客户贡献新增收入的 72%，业务结构进一步聚焦。",
                "交付周期从 21 天缩短到 14 天，效率改善得到验证。",
                "关键模块仍存在资源风险，需要补齐岗位。",
                "下一阶段应完成标杆客户复盘和模板标准化。",
                "管理层需要建立统一指标看板。",
                "汇报需要形成责任人明确的行动清单。",
            ]
        ),
        encoding="utf-8",
    )
    return source


def _planned_job(tmp_path: Path, job_id: str = "asset-job") -> str:
    job = create_job("素材工作流", job_id=job_id)
    ingest_sources(job.id, [str(_source(tmp_path))])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == JobState.PROTOTYPE_REVIEW.value
    return job.id


def test_asset_need_is_recorded_inside_job_workspace(tmp_path: Path) -> None:
    job_id = _planned_job(tmp_path)
    need = add_asset_need(
        job_id,
        slide_id="slide-03",
        kind="icon",
        query="企业增长线性图标",
        purpose="强化增长结论",
    )

    status = asset_status(job_id)

    assert need.status == "pending"
    assert status["pending_required"] == 1
    assert (job_dir(job_id) / "planning" / "asset-plan.json").is_file()
    assert (job_dir(job_id) / "assets" / "asset-ledger.json").is_file()

    duplicate = add_asset_need(
        job_id,
        slide_id="slide-03",
        kind="icon",
        query="企业增长线性图标",
        purpose="强化增长结论",
    )
    assert duplicate.id == need.id
    assert len(asset_status(job_id)["needs"]) == 1


def test_acquired_asset_is_downloaded_hashed_and_bound_to_slide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _planned_job(tmp_path, "asset-acquire")
    root = job_dir(job_id)
    candidate = root / "deliverables" / "candidate_editable.pptx"
    candidate.write_bytes(b"candidate before asset acquisition")
    need = add_asset_need(
        job_id,
        slide_id="slide-03",
        kind="icon",
        query="增长图标",
        purpose="结论视觉",
    )

    def fake_download(_url: str, directory: Path) -> Path:
        target = directory / "download.png"
        Image.new("RGBA", (128, 128), (20, 90, 160, 255)).save(target)
        return target

    monkeypatch.setattr("aippt_studio.assets.acquisition._download_url", fake_download)
    acquired = acquire_asset(
        job_id,
        need_id=need.id,
        download_url="https://cdn.example.test/icon.png",
        source_url="https://example.test/icon-license",
        license_id="MIT",
        attribution="Example Icons",
    )

    assert (root / acquired.stored_path).is_file()
    assert len(acquired.sha256) == 64
    assert asset_status(job_id)["pending_required"] == 0
    plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    slide = next(item for item in plan.slides if item.id == "slide-03")
    assert f"acquired:{acquired.id}" in slide.asset_refs
    ledger = read_json(root / "assets" / "asset-ledger.json")
    assert ledger["assets"][-1]["source_url"] == "https://example.test/icon-license"
    assert ledger["assets"][-1]["sha256"] == acquired.sha256
    assert not (root / "build" / "prototype.pptx").exists()
    assert not candidate.exists()
    archived = list((root / "history" / "superseded-deliverables").rglob("candidate_editable.pptx"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == b"candidate before asset acquisition"
    receipt = next((root / "reviews" / "delivery-invalidations").glob("*-deliverables.json"))
    assert read_json(receipt)["reason"] == "acquired asset changed the current deck plan"


def test_asset_download_requires_known_license_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _planned_job(tmp_path, "asset-license")
    need = add_asset_need(job_id, slide_id="slide-03", kind="image", query="办公场景", purpose="背景")
    called = False

    def should_not_download(_url: str, _directory: Path) -> Path:
        nonlocal called
        called = True
        raise AssertionError("download should not start")

    monkeypatch.setattr("aippt_studio.assets.acquisition._download_url", should_not_download)
    with pytest.raises(AssetAcquisitionError, match="license"):
        acquire_asset(
            job_id,
            need_id=need.id,
            download_url="https://example.test/image.png",
            source_url="https://example.test/page",
            license_id="NOASSERTION",
        )
    assert called is False


def test_cloud_disabled_blocks_asset_download_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _planned_job(tmp_path, "asset-cloud")
    root = job_dir(job_id)
    brief = read_model(root / "planning" / "brief.json", JobBrief)
    brief.cloud_allowed = False
    write_json(root / "planning" / "brief.json", brief)
    need = add_asset_need(job_id, slide_id="slide-03", kind="image", query="办公场景", purpose="背景")
    monkeypatch.setattr(
        "aippt_studio.assets.acquisition._download_url",
        lambda *_args: (_ for _ in ()).throw(AssertionError("network should not start")),
    )
    with pytest.raises(AssetAcquisitionError, match="cloud"):
        acquire_asset(
            job_id,
            need_id=need.id,
            download_url="https://example.test/image.png",
            source_url="https://example.test/page",
            license_id="MIT",
        )


def test_unsafe_svg_is_rejected_and_failure_remains_in_asset_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _planned_job(tmp_path, "asset-svg")
    need = add_asset_need(job_id, slide_id="slide-03", kind="icon", query="风险图标", purpose="风险提示")

    def unsafe_svg(_url: str, directory: Path) -> Path:
        target = directory / "unsafe.svg"
        target.write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', encoding="utf-8")
        return target

    monkeypatch.setattr("aippt_studio.assets.acquisition._download_url", unsafe_svg)
    with pytest.raises(AssetAcquisitionError, match="SVG"):
        acquire_asset(
            job_id,
            need_id=need.id,
            download_url="https://cdn.example.test/unsafe.svg",
            source_url="https://example.test/icon-page",
            license_id="MIT",
        )
    status = asset_status(job_id)
    failed_need = next(item for item in status["needs"] if item["id"] == need.id)
    assert failed_need["status"] == "blocked"
    assert status["pending_required"] == 1
    assert not any((job_dir(job_id) / "assets" / "acquired").iterdir())


def test_required_asset_need_blocks_prototype_approval(tmp_path: Path) -> None:
    job_id = _planned_job(tmp_path, "asset-gate")
    add_asset_need(job_id, slide_id="slide-03", kind="icon", query="风险图标", purpose="风险提示")

    with pytest.raises(WorkflowError, match="required assets"):
        approve_gate(job_id, "prototype")


def test_acquired_asset_is_portable_in_delivery_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _planned_job(tmp_path, "asset-delivery")
    need = add_asset_need(job_id, slide_id="slide-03", kind="image", query="增长场景", purpose="支撑结论")

    def fake_download(_url: str, directory: Path) -> Path:
        target = directory / "photo.png"
        Image.new("RGB", (640, 360), "#135a8c").save(target)
        return target

    monkeypatch.setattr("aippt_studio.assets.acquisition._download_url", fake_download)
    acquired = acquire_asset(
        job_id,
        need_id=need.id,
        download_url="https://cdn.example.test/photo.png",
        source_url="https://example.test/photo-license",
        license_id="CC-BY-4.0",
        attribution="Example Photographer",
    )
    assert run_job(job_id)["state"] == JobState.PROTOTYPE_REVIEW.value
    approve_gate(job_id, "prototype")
    assert run_job(job_id)["state"] == JobState.FINAL_REVIEW.value
    approve_gate(job_id, "final")

    export_job(job_id, skip_powerpoint=True)

    delivery = job_dir(job_id) / "deliverables"
    html = (delivery / "deck.html").read_text(encoding="utf-8")
    assert "file:///" not in html
    assert f"assets/acquired/{Path(acquired.stored_path).name}" in html
    assert (delivery / "assets" / "acquired" / Path(acquired.stored_path).name).is_file()
    assert (delivery / "asset-ledger.json").is_file()
