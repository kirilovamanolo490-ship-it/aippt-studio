"""Real pixelmatch tests over tiny synthetic PNGs; no decks or model calls."""
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from aippt_studio import visual_diff
from aippt_studio.runtime import resolve_executable


SIGNATURE = {"renderer": "synthetic-fixture", "version": "1", "platform": "test",
             "fonts_sha256": hashlib.sha256(b"no text or fonts").hexdigest(),
             "settings_sha256": hashlib.sha256(b"RGBA; no scaling").hexdigest()}


def make_request(tmp_path, before=None, after=None, **options):
    before = before if before is not None else Image.new("RGBA", (8, 6), "white")
    after = after if after is not None else before.copy()
    data = {"schema_version": 1}
    for key, image in (("baseline", before), ("candidate", after)):
        path = tmp_path / f"图片 空格 {key}.png"
        image.save(path)
        data[key] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                     "renderer_signature": dict(SIGNATURE)}
    return data | options


def test_actual_vendored_algorithm_is_pinned_unmodified_and_used(tmp_path):
    provenance = visual_diff._provenance()
    assert provenance["version"] == "7.2.0"
    assert provenance["commit"] == "9faed09302aaecec130b4ce0e8505d5ed5221393"
    assert provenance["license"] == "ISC"
    assert next(item["sha256"] for item in provenance["files"] if item["path"] == "index.js") == "52c6026c4916a07548d4ce55090ec4ed918d353e15095406dabc3a8a5135d788"
    result = visual_diff.compare_visuals(make_request(tmp_path), tmp_path / "result")
    assert result["status"] == "unchanged"
    assert result["summary"]["changed_pixels"] == 0
    assert result["summary"]["changed_bounds"] is None
    assert result["algorithm"]["node_executable"] == str(resolve_executable("node"))
    assert Image.open(tmp_path / "result/diff.png").getbbox() is None


def test_single_pixel_corner_regression_and_receipt_hashes(tmp_path):
    after = Image.new("RGBA", (8, 6), "white")
    after.putpixel((7, 5), (0, 0, 0, 255))
    request = make_request(tmp_path, after=after)
    result = visual_diff.compare_visuals(request, tmp_path / "result")
    assert result["status"] == "changed"
    assert result["scope"] == "whole_image"
    assert result["summary"]["changed_pixels"] == 1
    assert result["summary"]["outside_allowed_changed_pixels"] == 1
    assert result["summary"]["changed_bounds"] == {"x": 7, "y": 5, "width": 1, "height": 1}
    assert result["baseline"]["sha256"] == request["baseline"]["sha256"]
    assert hashlib.sha256((tmp_path / "result/diff.png").read_bytes()).hexdigest() == result["artifacts"]["diff_png"]["sha256"]
    assert json.loads((tmp_path / "result/receipt.json").read_text()) == result
    assert sorted(item.name for item in (tmp_path / "result").iterdir()) == ["diff.png", "receipt.json"]


def test_allowed_region_union_and_half_open_bounds(tmp_path):
    after = Image.new("RGBA", (8, 6), "white")
    after.putpixel((1, 1), (0, 0, 0, 255))
    after.putpixel((3, 1), (0, 0, 0, 255))
    request = make_request(tmp_path, after=after, allowed_regions=[{"x": 1, "y": 1, "width": 2, "height": 2}, {"x": 2, "y": 2, "width": 2, "height": 2}])
    result = visual_diff.compare_visuals(request, tmp_path / "result")
    assert result["status"] == "unexpected_changes"
    assert result["summary"]["allowed_pixels"] == 7
    assert result["summary"]["inside_allowed_changed_pixels"] == 1
    assert result["summary"]["outside_allowed_changed_pixels"] == 1
    assert result["summary"]["outside_allowed_bounds"] == {"x": 3, "y": 1, "width": 1, "height": 1}


def test_changes_inside_explicit_region_are_still_reported(tmp_path):
    after = Image.new("RGBA", (8, 6), "white")
    after.putpixel((1, 1), (0, 0, 0, 255))
    result = visual_diff.compare_visuals(make_request(tmp_path, after=after, allowed_regions=[{"x": 1, "y": 1, "width": 1, "height": 1}]), tmp_path / "result")
    assert result["status"] == "changed"
    assert result["summary"]["outside_allowed_changed_pixels"] == 0
    assert result["summary"]["inside_allowed_changed_pixels"] == 1


def test_transparency_is_compared_on_checkerboard_not_flat_white(tmp_path):
    result = visual_diff.compare_visuals(make_request(tmp_path, before=Image.new("RGBA", (8, 6), (255, 255, 255, 0))), tmp_path / "transparent")
    assert result["status"] == "unchanged"
    result = visual_diff.compare_visuals(make_request(tmp_path, before=Image.new("RGBA", (8, 6), (255, 255, 255, 0)), after=Image.new("RGBA", (8, 6), "white")), tmp_path / "opaque")
    assert result["status"] == "changed"
    assert result["summary"]["changed_pixels"] == 48


def test_invisible_rgb_does_not_claim_visible_change(tmp_path):
    result = visual_diff.compare_visuals(make_request(tmp_path, before=Image.new("RGBA", (8, 6), (255, 0, 0, 0)), after=Image.new("RGBA", (8, 6), (0, 255, 0, 0))), tmp_path / "result")
    assert result["status"] == "unchanged"
    assert result["baseline"]["rgba_sha256"] != result["candidate"]["rgba_sha256"]


def test_antialias_exclusion_is_real_and_recorded(tmp_path):
    before = Image.new("RGBA", (8, 6), "white")
    for x in range(4):
        for y in range(6):
            before.putpixel((x, y), (128, 128, 128, 255) if x == 3 else (0, 0, 0, 255))
    after = before.copy()
    for y in range(6):
        after.putpixel((3, y), (180, 180, 180, 255))
    request = make_request(tmp_path, before, after, threshold=0)
    excluded = visual_diff.compare_visuals(request, tmp_path / "excluded")
    included = visual_diff.compare_visuals(request | {"include_antialiasing": True}, tmp_path / "included")
    assert excluded["summary"]["changed_pixels"] == 0
    assert included["summary"]["changed_pixels"] == 6
    assert included["parameters"]["include_antialiasing"] is True


@pytest.mark.parametrize("mutation,reason", [
    (lambda request: request["candidate"]["renderer_signature"].update(version="2"), "renderer_signature_mismatch"),
    (lambda request: request["candidate"]["renderer_signature"].update(fonts_sha256="a" * 64), "renderer_signature_mismatch"),
    (lambda request: request["candidate"].pop("renderer_signature"), "missing_or_incomplete_renderer_signature"),
    (lambda request: request["candidate"].update(renderer_signature={"renderer": "same"}), "missing_or_incomplete_renderer_signature"),
])
def test_renderer_evidence_mismatch_not_comparable_and_no_algorithm_run(tmp_path, monkeypatch, mutation, reason):
    request = make_request(tmp_path)
    mutation(request)
    monkeypatch.setattr(visual_diff, "probe_node_runtime", lambda: pytest.fail("incomparable images must not execute pixelmatch"))
    result = visual_diff.compare_visuals(request, tmp_path / "result")
    assert result["status"] == "not_comparable"
    assert reason in result["reasons"]
    assert result["summary"] is None
    assert not (tmp_path / "result/diff.png").exists()


def test_different_dimensions_are_not_rescaled(tmp_path):
    result = visual_diff.compare_visuals(make_request(tmp_path, after=Image.new("RGB", (9, 6), "white")), tmp_path / "result")
    assert result["status"] == "not_comparable"
    assert "image_dimensions_mismatch" in result["reasons"]


def test_color_profile_difference_is_not_ignored(tmp_path):
    request = make_request(tmp_path)
    path = Path(request["candidate"]["path"])
    metadata = PngInfo()
    metadata.add(b"gAMA", (45455).to_bytes(4, "big"))
    Image.new("RGBA", (8, 6), "white").save(path, pnginfo=metadata)
    request["candidate"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = visual_diff.compare_visuals(request, tmp_path / "result")
    assert result["status"] == "not_comparable"
    assert "png_color_metadata_mismatch" in result["reasons"]


@pytest.mark.parametrize("region", [{"x": -1, "y": 0, "width": 1, "height": 1}, {"x": 7, "y": 0, "width": 2, "height": 1}, {"x": 1.5, "y": 0, "width": 1, "height": 1}, {"x": True, "y": 0, "width": 1, "height": 1}, {"x": 1, "y": 0, "width": 0, "height": 1}, {"x": 1, "y": 0, "w": 1, "h": 1}])
def test_invalid_regions_fail_without_publishing(tmp_path, region):
    with pytest.raises(visual_diff.VisualDiffError, match="region"):
        visual_diff.compare_visuals(make_request(tmp_path, allowed_regions=[region]), tmp_path / "result")
    assert not (tmp_path / "result").exists()


@pytest.mark.parametrize("options", [{"threshold": float("nan")}, {"threshold": True}, {"threshold": -0.01}, {"threshold": 1.01}, {"include_antialiasing": "false"}, {"allow_regions": []}, {"schema_version": True}])
def test_invalid_request_options_do_not_silently_fall_back(tmp_path, options):
    with pytest.raises(visual_diff.VisualDiffError):
        visual_diff.compare_visuals(make_request(tmp_path, **options), tmp_path / "result")
    assert not (tmp_path / "result").exists()


def test_stale_source_hash_fails_before_output(tmp_path):
    request = make_request(tmp_path)
    Path(request["baseline"]["path"]).write_bytes(b"new revision")
    with pytest.raises(visual_diff.VisualDiffError, match="SHA-256 mismatch"):
        visual_diff.compare_visuals(request, tmp_path / "result")
    assert not (tmp_path / "result").exists()


def test_failed_subprocess_is_atomic_and_preserves_sources(tmp_path, monkeypatch):
    request = make_request(tmp_path)
    original = Path(request["baseline"]["path"]).read_bytes()
    monkeypatch.setattr(visual_diff.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 1, "", "fixture execution failure"))
    with pytest.raises(visual_diff.VisualDiffError, match="fixture execution failure"):
        visual_diff.compare_visuals(request, tmp_path / "result")
    assert Path(request["baseline"]["path"]).read_bytes() == original
    assert not (tmp_path / "result").exists()
    assert not list(tmp_path.glob(".result-*"))


def test_existing_output_is_never_replaced(tmp_path):
    request = make_request(tmp_path)
    output = tmp_path / "result"
    output.mkdir()
    (output / "keep.txt").write_text("accepted evidence")
    with pytest.raises(visual_diff.VisualDiffError, match="existing evidence"):
        visual_diff.compare_visuals(request, output)
    assert (output / "keep.txt").read_text() == "accepted evidence"


def test_node_raw_buffer_length_is_validated(tmp_path):
    baseline = tmp_path / "baseline.rgba"
    candidate = tmp_path / "candidate.rgba"
    baseline.write_bytes(bytes(3)); candidate.write_bytes(bytes(3))
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps({"baseline_rgba": str(baseline), "candidate_rgba": str(candidate), "width": 1, "height": 1, "mask_rgba": str(tmp_path / "mask.rgba")}))
    result = subprocess.run([str(resolve_executable("node")), str(visual_diff.NODE_SCRIPT), str(request_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert "RGBA buffer sizes" in result.stderr
    assert not (tmp_path / "mask.rgba").exists()


def test_threshold_applies_to_perceived_change_and_is_bound_to_receipt(tmp_path):
    request = make_request(tmp_path, after=Image.new("RGBA", (8, 6), (250, 250, 250, 255)))
    tolerant = visual_diff.compare_visuals(request, tmp_path / "tolerant")
    exact = visual_diff.compare_visuals(request | {"threshold": 0}, tmp_path / "exact")
    assert tolerant["status"] == "unchanged"
    assert exact["summary"]["changed_pixels"] == 48
    assert tolerant["request_sha256"] != exact["request_sha256"]
    assert tolerant["parameters"]["threshold"] == 0.1


def test_corner_mask_regression_is_localized_without_resizing(tmp_path):
    before = Image.new("RGBA", (9, 7), (80, 150, 200, 255))
    for coordinate in ((0, 0), (8, 0), (0, 6), (8, 6)):
        before.putpixel(coordinate, (0, 0, 0, 0))
    after = before.copy()
    after.putpixel((8, 6), (80, 150, 200, 255))
    request = make_request(tmp_path, before, after, threshold=0, include_antialiasing=True,
                           allowed_regions=[{"x": 2, "y": 2, "width": 5, "height": 3}])
    result = visual_diff.compare_visuals(request, tmp_path / "result")
    assert result["status"] == "unexpected_changes"
    assert result["summary"]["changed_pixels"] == 1
    assert result["summary"]["outside_allowed_bounds"] == {"x": 8, "y": 6, "width": 1, "height": 1}


def test_comparison_does_not_overwrite_output_created_while_it_runs(tmp_path, monkeypatch):
    request = make_request(tmp_path)
    real_run = visual_diff.subprocess.run
    output = tmp_path / "result"

    def competing_write(*args, **kwargs):
        result = real_run(*args, **kwargs)
        # Runtime selection also probes Node with subprocess.run.  Create the
        # competing directory only when the actual visual-diff worker runs.
        if len(args) and len(args[0]) > 1 and args[0][1] == str(visual_diff.NODE_SCRIPT):
            output.mkdir()
            (output / "keep.txt").write_text("independently published evidence")
        return result

    monkeypatch.setattr(visual_diff.subprocess, "run", competing_write)
    with pytest.raises(visual_diff.VisualDiffError, match="output appeared"):
        visual_diff.compare_visuals(request, output)
    assert (output / "keep.txt").read_text() == "independently published evidence"
    assert not (output / "receipt.json").exists()
    assert not (tmp_path / ".result.publish.lock").exists()
