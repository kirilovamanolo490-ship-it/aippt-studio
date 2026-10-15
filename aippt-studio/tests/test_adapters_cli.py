from __future__ import annotations

import json
import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aippt_studio import adapters, cli
from aippt_studio.ingest import ingest_sources
from aippt_studio.models import JobState, ProductionRoute
from aippt_studio.storage import job_dir, load_job, read_json
from aippt_studio.workflow import approve_gate, create_job, run_job


def test_source_adapter_resolves_only_canonical_image_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "adapter_common.py"
    spec = importlib.util.spec_from_file_location("studio_adapter_common_fixture", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    upstream_root = tmp_path / "github"
    monkeypatch.setenv("AIPPT_UPSTREAM_DIR", str(upstream_root))
    canonical = upstream_root / "image" / "codex-ppt-skill"
    legacy = upstream_root / "codex-ppt-skill"
    canonical.mkdir(parents=True)
    legacy.mkdir(parents=True)

    assert module.upstream_checkout("codex-ppt-skill") == canonical
    assert module.upstream_checkout("image-to-editable-ppt-skill") == upstream_root / "image" / "image-to-editable-ppt-skill"


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "client-notes.md"
    source.write_text(
        "\n".join(
            [
                "2026 年上半年收入增长 28%，核心客户续约率达到 93%。",
                "企业客户贡献新增收入的 72%，业务结构正在进一步聚焦。",
                "产品交付周期从 21 天缩短到 14 天，效率改善已经得到验证。",
                "两个关键模块仍存在资源风险，需要在第三季度补齐关键岗位。",
                "下一阶段应优先完成标杆客户复盘并统一项目交付模板。",
                "管理层需要建立覆盖增长、续约和交付质量的统一指标看板。",
                "本次汇报必须保留客户数据，并形成责任人明确的行动清单。",
            ]
        ),
        encoding="utf-8",
    )
    return source


def _advance_to_final_review(tmp_path: Path, job_id: str = "candidate-export") -> str:
    job = create_job("季度经营汇报", job_id=job_id)
    ingest_sources(job.id, [str(_source(tmp_path))])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == JobState.PROTOTYPE_REVIEW.value
    approve_gate(job.id, "prototype")
    assert run_job(job.id)["state"] == JobState.FINAL_REVIEW.value
    approve_gate(job.id, "final")
    return job.id


def test_capability_report_describes_native_and_optional_adapters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    upstream = tmp_path / "upstream"
    tools = tmp_path / "tools"
    (project / "node_modules" / "dom-to-pptx").mkdir(parents=True)
    (upstream / "ppt-master").mkdir(parents=True)
    (upstream / "pptx-automizer").mkdir(parents=True)
    tools.mkdir()
    officecli = tools / "officecli.exe"
    officecli.touch()
    monkeypatch.setattr(adapters, "officecli_executable", lambda: officecli)
    monkeypatch.setattr(adapters, "executable_version", lambda path: "1.0.147")
    node = tmp_path / "node.exe"

    monkeypatch.setattr(adapters, "PROJECT_ROOT", project)
    monkeypatch.setattr(adapters, "upstream_root", lambda: upstream)
    monkeypatch.setattr(adapters, "tools_root", lambda: tools)
    monkeypatch.setattr(adapters, "_command", lambda name: node if name == "node" else None)

    report = {item["name"]: item for item in adapters.capability_report()}

    assert report["node"] == {
        "name": "node",
        "available": True,
        "role": "HTML/PPTX renderer",
        "path": str(node),
        "required_for": "native",
    }
    assert report["dom-to-pptx"]["available"] is True
    assert report["dom-to-pptx"]["required_for"] == "native"
    assert report["officecli"]["path"] == str(officecli)
    assert report["pptx-automizer"]["available"] is True
    assert report["image-to-editable-ppt"]["available"] is False
    assert report["codex-ppt"]["available"] is False


def test_doctor_prints_json_and_only_fails_for_required_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = [
        {
            "name": "python",
            "available": True,
            "role": "orchestrator",
            "path": "python",
            "required_for": "all",
        },
        {
            "name": "node",
            "available": True,
            "role": "renderer",
            "path": "node",
            "required_for": "native",
        },
        {
            "name": "codex-ppt",
            "available": False,
            "role": "optional visual version",
            "path": None,
            "required_for": "visual",
        },
    ]
    monkeypatch.setattr(cli, "capability_report", lambda: report)
    runner = CliRunner()

    healthy = runner.invoke(cli.app, ["doctor", "--json"])
    assert healthy.exit_code == 0
    assert json.loads(healthy.stdout) == report

    report[1]["available"] = False
    missing_native = runner.invoke(cli.app, ["doctor", "--json"])
    assert missing_native.exit_code == 1
    assert json.loads(missing_native.stdout) == report


@pytest.mark.parametrize(
    ("route", "upstream_directory"),
    [
        (ProductionRoute.TEMPLATE, "pptx-automizer"),
        (ProductionRoute.REBUILD, "image-to-editable-ppt-skill"),
        (ProductionRoute.VISUAL, "codex-ppt-skill"),
    ],
)
def test_non_native_route_writes_handoff_and_blocks_for_external_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: ProductionRoute,
    upstream_directory: str,
) -> None:
    upstream = tmp_path / "upstream"
    source_category = "image" if route in {ProductionRoute.REBUILD, ProductionRoute.VISUAL} else ""
    (upstream / source_category / upstream_directory).mkdir(parents=True)
    monkeypatch.setenv("AIPPT_UPSTREAM_DIR", str(upstream))
    job = create_job(f"{route.value} route", job_id=f"route-{route.value}", route=route)
    ingest_sources(job.id, [str(_source(tmp_path))])

    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    # A template's first reviewable artifact must come from the real template
    # adapter.  It intentionally skips the generic HTML prototype; the other
    # non-native routes still use that prototype gate before their handoff.
    if route == ProductionRoute.TEMPLATE:
        assert load_job(job.id).state == JobState.FULL_BUILD
    else:
        assert run_job(job.id)["state"] == JobState.PROTOTYPE_REVIEW.value
        approve_gate(job.id, "prototype")
    result = run_job(job.id)

    assert result["state"] == JobState.BLOCKED.value
    blocked = load_job(job.id)
    assert blocked.resume_state == JobState.FULL_BUILD
    assert route.value in (blocked.blocked_reason or "")
    handoff_path = job_dir(job.id) / "build" / f"{route.value}-handoff.json"
    handoff = read_json(handoff_path)
    assert "external-engine-manifest.json" in handoff["validation_error"]
    assert handoff == {
        "job_id": job.id,
        "route": route.value,
        "brief": "planning/brief.json",
        "deck_plan": "planning/deck-plan.json",
        "sources": [f"normalized/sources/{source_id}.json" for source_id in blocked.source_ids],
        "status": "awaiting_external_engine",
        "execution": "manual_upstream_adapter",
        "expected_outputs": {
            "pptx": "build/deck.pptx",
            "previews": "build/previews/<slide-id>.png",
            "manifest": "build/external-engine-manifest.json",
            "build_source": "a relative file path declared by external-engine-manifest.json",
        },
        "manifest_schema": {
            "job_id": job.id,
            "route": route.value,
            "engine": "<engine-name>",
            "version": "<engine-version-or-commit>",
            "editable_class": {
                ProductionRoute.TEMPLATE: "native_editable",
                ProductionRoute.REBUILD: "reconstructed_editable",
                ProductionRoute.VISUAL: "image_based",
            }[route],
            "deck_plan_sha256": "<sha256>",
            "input_hashes": {"<source-id>": "<sha256>"},
            "pptx_sha256": "<sha256>",
            "preview_hashes": {"<slide-id>": "<sha256>"},
            "build_source": "build/<relative-source-file>",
            "build_source_sha256": "<sha256>",
            "providers": [{"provider": "<name>", "model": "<model>", "purpose": "<purpose>"}],
            "warnings": [],
            "adapter_receipt": {"engine": "<engine-name>", "version": "<version>"},
        },
        "resume_command": f"aippt resume {job.id}",
        "validation_error": handoff["validation_error"],
    }


def test_cli_skip_powerpoint_export_remains_candidate_in_final_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = _advance_to_final_review(tmp_path)
    officecli_calls: list[Path] = []

    def validate_candidate(path: Path) -> dict[str, object]:
        officecli_calls.append(path)
        return {"status": "passed-test"}

    monkeypatch.setattr("aippt_studio.workflow.officecli_validate", validate_candidate)

    result = CliRunner().invoke(cli.app, ["export", job_id, "--skip-powerpoint", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["status"] == "candidate"
    assert payload["qa"]["officecli"] == {"status": "passed-test"}
    assert payload["qa"]["powerpoint_roundtrip"] == "skipped"
    assert load_job(job_id).state == JobState.FINAL_REVIEW
    delivery = job_dir(job_id) / "deliverables"
    assert len(officecli_calls) == 1
    assert officecli_calls[0].name == "candidate_editable.pptx"
    assert (delivery / "candidate_editable.pptx").is_file()
    assert not (delivery / "final_editable.pptx").exists()
    assert not (delivery / "final.pdf").exists()
    assert (delivery / "evidence-ledger.json").is_file()
    assert (delivery / "assets").is_dir()
    assert any((delivery / "assets").iterdir())
    assert Path(payload["outputs"]["editable_pptx"]["path"]).name == "candidate_editable.pptx"
    manifest = read_json(delivery / "build-manifest.json")
    assert manifest["status"] == "candidate"
    assert manifest["qa"]["officecli"] == {"status": "passed-test"}
    assert manifest["qa"]["powerpoint_roundtrip"] == "skipped"


def test_cli_route_records_input_role_and_explicit_visual_acceptance(tmp_path: Path) -> None:
    image = tmp_path / "visual.png"
    from PIL import Image

    Image.new("RGB", (64, 36), "white").save(image)
    job = create_job("visual alternate", job_id="cli-route")
    source = ingest_sources(job.id, [str(image)])[0]

    result = CliRunner().invoke(
        cli.app,
        ["route", job.id, "--role", f"{source.id}=visual_reference", "--accept-image-based", "--json"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["route"] == ProductionRoute.VISUAL.value
    assert payload["route_source"] == "user_override"
    assert payload["input_roles"][source.id] == "visual_reference"


def test_cli_asset_need_status_and_skip(tmp_path: Path) -> None:
    job = create_job("asset commands", job_id="cli-assets")
    ingest_sources(job.id, [str(_source(tmp_path))])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == JobState.PROTOTYPE_REVIEW.value
    runner = CliRunner()

    created = runner.invoke(
        cli.app,
        [
            "asset-need",
            job.id,
            "--slide",
            "slide-03",
            "--kind",
            "icon",
            "--query",
            "增长图标",
            "--purpose",
            "强化结论",
            "--json",
        ],
    )
    assert created.exit_code == 0, created.stdout
    need_id = json.loads(created.stdout)["id"]

    status = runner.invoke(cli.app, ["asset-status", job.id, "--json"])
    assert status.exit_code == 0, status.stdout
    assert json.loads(status.stdout)["pending_required"] == 1

    skipped = runner.invoke(
        cli.app,
        ["asset-skip", job.id, "--need", need_id, "--reason", "改用原生图形", "--json"],
    )
    assert skipped.exit_code == 0, skipped.stdout
    assert json.loads(skipped.stdout)["status"] == "skipped"
