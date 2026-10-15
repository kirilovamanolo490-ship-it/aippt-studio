import pytest
from PIL import Image

from aippt_studio import image_preflight
from aippt_studio.image_preflight import ImagePreflightError, preflight_image, prepare_image


def test_exif_orientation_drives_effective_dimensions_and_density(tmp_path):
    path = tmp_path / "rotated.jpg"
    image = Image.new("RGB", (120, 60), (20, 80, 160))
    exif = image.getexif()
    exif[274] = 6
    image.save(path, exif=exif)

    result = preflight_image(path, (1, 2), target_unit="in")

    assert result["decode_status"] == "decoded"
    assert result["encoded_size_px"] == {"width": 120, "height": 60}
    assert result["orientation_corrected_size_px"] == {"width": 60, "height": 120}
    assert result["exif_orientation"] == 6
    assert result["effective_ppi"] == {"width": 60, "height": 60}
    assert any(item["code"] == "below_150_ppi_reference" for item in result["diagnostics"])
    assert result["assessment"] == "advisory_only"
    assert result["semantic_match_assessed"] is False


def test_corrupt_image_returns_advisory_rejection(tmp_path):
    path = tmp_path / "broken.png"
    path.write_bytes(b"not a real image")

    result = preflight_image(path)

    assert result["decode_status"] == "rejected"
    assert result["error"]
    assert result["diagnostics"][0]["code"] == "image_not_inspected"
    assert result["assessment"] == "advisory_only"


def test_pixel_limit_is_checked_before_decode(tmp_path, monkeypatch):
    path = tmp_path / "moderate.png"
    Image.new("RGB", (20, 20), "blue").save(path)
    monkeypatch.setattr(image_preflight, "MAX_IMAGE_PIXELS", 100)

    result = preflight_image(path)

    assert result["decode_status"] == "rejected"
    assert "pixel safety limit" in result["error"]


def test_transparent_margin_reports_alpha_and_content_bounds(tmp_path):
    path = tmp_path / "transparent.png"
    image = Image.new("RGBA", (100, 80), (0, 0, 0, 0))
    for x in range(20, 80):
        for y in range(15, 65):
            image.putpixel((x, y), (220, 30, 50, 255))
    image.save(path)

    result = preflight_image(path)

    assert result["decode_status"] == "decoded"
    assert result["alpha_present"] is True
    assert result["pixel_diagnostics"]["alpha_fraction"] > 0.5
    assert result["pixel_diagnostics"]["effective_content_bbox_px"] == [20, 15, 80, 65]
    assert any(item["code"] == "background_border_detected" for item in result["diagnostics"])


def test_near_white_border_and_blank_image_are_reported_as_signals(tmp_path):
    path = tmp_path / "border.png"
    image = Image.new("RGB", (100, 100), (252, 252, 250))
    for x in range(20, 80):
        for y in range(20, 80):
            image.putpixel((x, y), (30, 90, 180))
    image.save(path)
    result = preflight_image(path)
    assert result["pixel_diagnostics"]["effective_content_bbox_px"] == [20, 20, 80, 80]
    assert result["pixel_diagnostics"]["background_border_px"]["top"] >= 19
    assert any(item["code"] == "background_border_detected" for item in result["diagnostics"])

    blank_path = tmp_path / "blank.png"
    Image.new("RGB", (80, 40), "white").save(blank_path)
    blank = preflight_image(blank_path)
    codes = {item["code"] for item in blank["diagnostics"]}
    assert "no_detectable_content" in codes
    assert "nearly_uniform_image" in codes


def test_normal_image_has_no_low_pixel_or_blank_warning(tmp_path):
    path = tmp_path / "gradient.png"
    image = Image.new("RGB", (640, 320))
    for y in range(320):
        for x in range(640):
            image.putpixel((x, y), (x % 256, y % 256, (x + y) % 256))
    image.save(path)

    result = preflight_image(path, (4, 2), target_unit="in")

    assert result["decode_status"] == "decoded"
    assert result["effective_ppi"] == {"width": 160, "height": 160}
    assert not {"below_150_ppi_reference", "no_detectable_content", "nearly_uniform_image"} & {
        item["code"] for item in result["diagnostics"]
    }
    assert any(item["code"] == "below_300_ppi_reference" for item in result["diagnostics"])


def test_invalid_target_fails_fast():
    with pytest.raises(ValueError, match="target_size"):
        preflight_image("unused.png", (0, 10), target_unit="px")


def test_pixel_target_reports_axis_coverage_without_claiming_display_quality(tmp_path):
    path = tmp_path / "source.png"
    Image.new("RGB", (800, 400), (40, 90, 180)).save(path)

    result = preflight_image(path, (1200, 600), target_unit="px")

    assert result["pixel_coverage_ratio"] == {"width": 800 / 1200, "height": 400 / 600}
    assert any(item["code"] == "source_pixels_below_target" for item in result["diagnostics"])
    assert result["visual_quality_judged"] is False


def test_prepare_image_normalizes_exif_orientation_and_downscales_proportionally(tmp_path):
    source = tmp_path / "oriented.jpg"
    output = tmp_path / "prepared.png"
    image = Image.new("RGB", (1200, 600), (20, 100, 180))
    exif = image.getexif()
    exif[274] = 6
    image.save(source, exif=exif)

    receipt = prepare_image(source, output, max_edge=300)

    assert receipt["status"] == "prepared"
    assert receipt["source_sha256"]
    assert receipt["output_sha256"]
    assert receipt["source_encoded_size_px"] == {"width": 1200, "height": 600}
    assert receipt["oriented_size_px"] == {"width": 600, "height": 1200}
    assert receipt["output_size_px"] == {"width": 150, "height": 300}
    assert "exif_orientation_normalization" in receipt["operations"]
    assert "proportional_downscale_lanczos" in receipt["operations"]
    assert receipt["source_modified"] is False
    with Image.open(source) as original:
        assert original.size == (1200, 600)
        assert original.getexif().get(274) == 6
    with Image.open(output) as prepared:
        assert prepared.size == (150, 300)
        assert prepared.getexif().get(274, 1) == 1


def test_prepare_image_preserves_transparency_and_never_upscales(tmp_path):
    source = tmp_path / "alpha.png"
    output = tmp_path / "copy.webp"
    image = Image.new("RGBA", (40, 20), (20, 40, 60, 0))
    image.putpixel((10, 10), (200, 30, 80, 128))
    image.save(source)

    receipt = prepare_image(source, output, max_edge=100)

    assert receipt["output_size_px"] == {"width": 40, "height": 20}
    assert receipt["alpha_preserved"] is True
    assert "proportional_downscale_lanczos" not in receipt["operations"]
    with Image.open(output) as prepared:
        assert prepared.mode == "RGBA"
        assert prepared.getpixel((10, 10))[3] in range(120, 137)
        assert prepared.getpixel((0, 0))[3] == 0


def test_prepare_image_refuses_existing_output_and_alpha_losing_format(tmp_path):
    source = tmp_path / "alpha.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(source)
    existing = tmp_path / "existing.png"
    existing.write_bytes(b"keep me")
    with pytest.raises(ImagePreflightError, match="new"):
        prepare_image(source, existing, max_edge=10)
    assert existing.read_bytes() == b"keep me"
    with pytest.raises(ImagePreflightError, match="transparency"):
        prepare_image(source, tmp_path / "opaque.jpg", max_edge=10)
    assert not (tmp_path / "opaque.jpg").exists()


def test_prepare_image_uses_the_preflight_file_and_pixel_limits(tmp_path, monkeypatch):
    source = tmp_path / "large.png"
    Image.new("RGB", (20, 20), "blue").save(source)
    monkeypatch.setattr(image_preflight, "MAX_IMAGE_PIXELS", 100)
    with pytest.raises(ImagePreflightError, match="pixel safety limit"):
        prepare_image(source, tmp_path / "out.png", max_edge=10)
    assert not (tmp_path / "out.png").exists()
