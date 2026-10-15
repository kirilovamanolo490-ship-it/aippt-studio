from __future__ import annotations

from pathlib import Path

from PIL import Image

from adapter_test_helpers import run_adapter, write_contract_job


def test_visual_adapter_assembles_prepared_images_and_labels_output(tmp_path: Path) -> None:
    source = tmp_path / "reference.png"
    Image.new("RGB", (320, 180), "#123456").save(source)
    root = tmp_path / "job"
    request, result = write_contract_job(root, route="visual", role="visual_reference", source=source, slides=2)
    images = root / "workspace" / "visual-images"
    images.mkdir(parents=True)
    Image.new("RGB", (1280, 720), "#123456").save(images / "slide-01.png")
    Image.new("RGB", (1280, 720), "#654321").save(images / "slide-02.png")

    payload = run_adapter(Path(__import__("sys").executable), Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "visual_adapter.py", request, result)

    assert payload["status"] == "succeeded", payload
    assert payload["editability"] == "image_based"
    assert (root / "build" / "adapter-staging" / "deck.pptx").is_file()


def test_visual_adapter_blocks_remote_generation_when_cloud_is_disabled(tmp_path: Path) -> None:
    source = tmp_path / "reference.png"
    Image.new("RGB", (320, 180), "white").save(source)
    root = tmp_path / "job"
    request, result = write_contract_job(root, route="visual", role="visual_reference", source=source, cloud_allowed=False)
    payload = run_adapter(Path(__import__("sys").executable), Path(__file__).resolve().parents[1] / "scripts" / "adapters" / "visual_adapter.py", request, result)
    assert payload["status"] == "blocked"
    assert payload["failure"]["category"] == "cloud_disabled"
