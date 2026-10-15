from __future__ import annotations

from pathlib import Path

from PIL import Image

from aippt_studio import native_scene, workflow
from aippt_studio.adapter_contracts import (
    AdapterArtifact,
    AdapterBuildResult,
    AdapterStatus,
    EditabilityClass,
    EngineReceipt,
)
from aippt_studio.models import DeckPlan, JobRecord, ProductionRoute, SlideSpec
from aippt_studio.renderer import sha256
from aippt_studio.storage import write_json


def _single_page_plan(root: Path) -> DeckPlan:
    plan = DeckPlan(
        title="Preview routing fixture",
        slides=[SlideSpec(id="slide-01", title="Title", purpose="Fixture", layout="content")],
    )
    write_json(root / "planning/deck-plan.json", plan)
    return plan


def test_native_scene_renders_local_preview_and_cleans_temporary_directory(tmp_path, monkeypatch):
    root = tmp_path / "native-job"
    (root / "planning").mkdir(parents=True)
    (root / "build").mkdir()
    plan = _single_page_plan(root)
    plan_path = root / "planning/deck-plan.json"
    write_json(
        root / "planning/native-scene.json",
        {
            "deck_plan_sha256": sha256(plan_path),
            "slides": [
                {"id": slide.id, "elements": [{"type": "text", "text": slide.title}]}
                for slide in plan.slides
            ],
        },
    )

    temporary_dirs: list[Path] = []
    roundtrip_calls: list[tuple] = []

    def fake_invoke(_provider, _operation, _scene, output):
        output.mkdir(parents=True, exist_ok=True)
        (output / "candidate.pptx").write_bytes(b"fixture pptx")
        (output / "receipt.json").write_text("{}", encoding="utf-8")
        return {"result": {"status": "passed", "runtime_version": "fixture"}}

    def fake_export_png(source: Path, output: Path, slide=None):
        assert source.name == "candidate.pptx"
        assert slide is None
        output.mkdir(parents=True, exist_ok=True)
        image = output / "slide-01.png"
        Image.new("RGB", (16, 9), "#123456").save(image)
        temporary_dirs.append(output.parent)
        assert output.parent.name.startswith("native-preview-")
        assert output.parent.exists()
        return {"status": "passed", "slides": [str(image)]}

    monkeypatch.setattr(native_scene, "invoke", fake_invoke)
    monkeypatch.setattr(native_scene, "officecli_validate", lambda _pptx: {"status": "passed"})
    monkeypatch.setattr("aippt_studio.mac_quick_png.export_png", fake_export_png)
    monkeypatch.setattr(
        workflow,
        "powerpoint_roundtrip",
        lambda *args, **kwargs: roundtrip_calls.append((args, kwargs)),
    )

    output_pptx = tmp_path / "deck.pptx"
    output_previews = tmp_path / "previews"
    result = native_scene.render_scene(root, output_pptx, output_previews)

    assert result["status"] == "passed"
    assert output_pptx.read_bytes() == b"fixture pptx"
    assert (output_previews / "slide-01.png").is_file()
    assert Image.open(output_previews / "slide-01.png").size == (16, 9)
    assert len(temporary_dirs) == 1
    assert not temporary_dirs[0].exists()
    assert not roundtrip_calls


def test_adapter_with_only_pptx_gets_local_png_previews_without_roundtrip(tmp_path, monkeypatch):
    monkeypatch.delenv("AIPPT_FAKE_RENDER", raising=False)
    root = tmp_path / "adapter-job"
    (root / "planning").mkdir(parents=True)
    plan = _single_page_plan(root)
    staging = root / "build/adapter-staging"
    staging.mkdir(parents=True)
    pptx = staging / "deck.pptx"
    pptx.write_bytes(b"adapter pptx")
    result = AdapterBuildResult(
        status=AdapterStatus.SUCCEEDED,
        receipt=EngineReceipt(engine="fixture", version="1"),
        editability=EditabilityClass.NATIVE_EDITABLE,
        artifacts=[
            AdapterArtifact(
                kind="pptx",
                path=pptx.relative_to(root).as_posix(),
                sha256=sha256(pptx),
                size_bytes=pptx.stat().st_size,
            )
        ],
    )
    temporary_dirs: list[Path] = []
    roundtrip_calls: list[tuple] = []

    def fake_export_png(source: Path, output: Path, slide=None):
        assert source == pptx.resolve()
        assert slide is None
        output.mkdir(parents=True, exist_ok=True)
        image = output / "page-1.png"
        Image.new("RGB", (20, 12), "#654321").save(image)
        temporary_dirs.append(output.parent)
        assert output.parent.name.startswith("adapter-preview-")
        assert output.parent.exists()
        return {"status": "passed", "slides": [str(image)]}

    monkeypatch.setattr("aippt_studio.mac_quick_png.export_png", fake_export_png)
    monkeypatch.setattr(
        workflow,
        "powerpoint_roundtrip",
        lambda *args, **kwargs: roundtrip_calls.append((args, kwargs)),
    )

    updated = workflow._ensure_adapter_previews(
        JobRecord(id="preview-fixture", name="fixture", route=ProductionRoute.TEMPLATE),
        root,
        result,
    )

    previews = [artifact for artifact in updated.artifacts if artifact.kind == "preview"]
    assert [Path(item.path).name for item in previews] == ["slide-01.png"]
    preview_path = root / previews[0].path
    assert preview_path.is_file()
    assert Image.open(preview_path).size == (20, 12)
    assert previews[0].sha256 == sha256(preview_path)
    assert previews[0].size_bytes == preview_path.stat().st_size
    assert len(temporary_dirs) == 1
    assert not temporary_dirs[0].exists()
    assert not roundtrip_calls
