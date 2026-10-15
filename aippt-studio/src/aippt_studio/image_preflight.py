"""Local, advisory image checks for presentation assets.

This module never changes an image and does not judge subject matter, visual
quality, licensing, or whether an image supports a slide's claim.
"""
from __future__ import annotations

import math
import hashlib
import os
import warnings
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 24_000_000
ANALYSIS_MAX_EDGE = 512
MIN_REFERENCE_PPI = 150.0
PRINT_REFERENCE_PPI = 300.0
SUPPORTED_FORMATS = {"PNG", "JPEG", "WEBP", "GIF", "TIFF", "BMP"}
_FORMAT_BY_SUFFIX = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP",
                    ".tif": "TIFF", ".tiff": "TIFF", ".bmp": "BMP"}
_ALPHA_FORMATS = {"PNG", "WEBP", "TIFF"}
_TARGET_UNITS = {"px", "in", "mm", "cm"}


class ImagePreflightError(ValueError):
    """The input could not be safely inspected."""


def _target(size: tuple[float, float] | None, unit: str) -> dict[str, Any] | None:
    if size is None:
        return None
    if unit not in _TARGET_UNITS:
        raise ValueError(f"target_unit must be one of {', '.join(sorted(_TARGET_UNITS))}")
    if (not isinstance(size, (tuple, list)) or len(size) != 2
            or any(isinstance(value, bool) or not isinstance(value, (int, float))
                   or not math.isfinite(value) or value <= 0 for value in size)):
        raise ValueError("target_size must contain two finite positive numbers")
    if unit != "px" and any(value > 5000 for value in size):
        raise ValueError("physical target dimensions above 5000 units are unsupported")
    return {"width": float(size[0]), "height": float(size[1]), "unit": unit}


def _near_white(r: int, g: int, b: int) -> bool:
    return min(r, g, b) >= 245 and max(r, g, b) - min(r, g, b) <= 12


def _analyse_pixels(image: Image.Image) -> dict[str, Any]:
    width, height = image.size
    scale = min(1.0, ANALYSIS_MAX_EDGE / max(width, height))
    sample_size = (max(1, round(width * scale)), max(1, round(height * scale)))
    sample = image.convert("RGBA")
    if sample.size != sample_size:
        sample.thumbnail(sample_size, Image.Resampling.LANCZOS)
    packed = memoryview(sample.tobytes()).cast("B")
    pixels = [tuple(packed[index:index + 4]) for index in range(0, len(packed), 4)]
    sw, sh = sample.size
    total = max(1, len(pixels))
    transparent_count = sum(1 for _, _, _, alpha in pixels if alpha < 8)
    partial_alpha_count = sum(1 for _, _, _, alpha in pixels if 8 <= alpha < 255)
    white_count = sum(1 for r, g, b, a in pixels if a >= 8 and _near_white(r, g, b))
    visible = [(x, y) for y in range(sh) for x in range(sw)
               if (p := pixels[y * sw + x])[3] >= 8 and not _near_white(*p[:3])]
    if visible:
        left = min(point[0] for point in visible)
        top = min(point[1] for point in visible)
        right = max(point[0] for point in visible) + 1
        bottom = max(point[1] for point in visible) + 1
        bbox = [round(left / sw * width), round(top / sh * height),
                round(right / sw * width), round(bottom / sh * height)]
        area_fraction = ((right - left) * (bottom - top)) / total
    else:
        bbox = None
        area_fraction = 0.0

    def run_from_edge(points: list[tuple[int, int, int, int]]) -> int:
        run = 0
        for row in points:
            count = len(row)
            background = sum(1 for r, g, b, a in row if a < 8 or (a >= 8 and _near_white(r, g, b)))
            if count and background / count >= 0.97:
                run += 1
            else:
                break
        return run

    rows = [[pixels[y * sw + x] for x in range(sw)] for y in range(sh)]
    cols = [[pixels[y * sw + x] for y in range(sh)] for x in range(sw)]
    border_sample = {"top": run_from_edge(rows), "bottom": run_from_edge(list(reversed(rows))),
                     "left": run_from_edge(cols), "right": run_from_edge(list(reversed(cols)))}
    border_px = {edge: round(value / (sh if edge in {"top", "bottom"} else sw)
                             * (height if edge in {"top", "bottom"} else width))
                 for edge, value in border_sample.items()}

    # Approximate flatness from a small analysis sample; this is only a signal.
    quantized: dict[tuple[int, int, int, int], int] = {}
    for pixel in pixels:
        key = tuple((channel // 16) * 16 for channel in pixel)
        quantized[key] = quantized.get(key, 0) + 1
    dominant_fraction = max(quantized.values()) / total
    return {"alpha_fraction": transparent_count / total,
            "partial_alpha_fraction": partial_alpha_count / total,
            "near_white_fraction": white_count / total,
            "effective_content_bbox_px": bbox,
            "effective_content_area_fraction": round(area_fraction, 6),
            "background_border_px": border_px,
            "dominant_16_level_color_fraction": round(dominant_fraction, 6)}


def preflight_image(
    path: str | Path,
    target_size: tuple[float, float] | None = None,
    *,
    target_unit: str = "px",
) -> dict[str, Any]:
    """Inspect one local raster image and return advisory diagnostics.

    ``target_size`` is ``(width, height)`` with ``target_unit`` of ``px``,
    ``in``, ``mm``, or ``cm``. Physical dimensions produce effective PPI
    signals; pixel dimensions produce source-to-output pixel coverage signals.
    The image is opened read-only and never written back.
    """
    target = _target(target_size, target_unit)
    source = Path(path).expanduser()
    result: dict[str, Any] = {
        "schema_version": 1,
        "assessment": "advisory_only",
        "semantic_match_assessed": False,
        "visual_quality_judged": False,
        "source": str(source),
        "target": target,
        "diagnostics": [],
    }
    try:
        if not source.is_file():
            raise ImagePreflightError("source is not a regular file")
        file_size = source.stat().st_size
        result["file_bytes"] = file_size
        if file_size <= 0:
            raise ImagePreflightError("image file is empty")
        if file_size > MAX_FILE_BYTES:
            raise ImagePreflightError(f"file exceeds the {MAX_FILE_BYTES}-byte safety limit")

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as original:
                image_format = (original.format or "").upper()
                result["format"] = image_format or None
                if image_format not in SUPPORTED_FORMATS:
                    raise ImagePreflightError(f"unsupported raster format: {image_format or 'unknown'}")
                raw_size = original.size
                if raw_size[0] <= 0 or raw_size[1] <= 0 or raw_size[0] * raw_size[1] > MAX_IMAGE_PIXELS:
                    raise ImagePreflightError(f"decoded dimensions exceed the {MAX_IMAGE_PIXELS}-pixel safety limit")
                result["encoded_size_px"] = {"width": raw_size[0], "height": raw_size[1]}
                result["frame_count"] = int(getattr(original, "n_frames", 1))
                original.load()
                oriented = ImageOps.exif_transpose(original)
                oriented.load()
                width, height = oriented.size
                result["orientation_corrected_size_px"] = {"width": width, "height": height}
                result["exif_orientation"] = int(original.getexif().get(274, 1))
                rgba = oriented.convert("RGBA")
                result["alpha_present"] = ("A" in oriented.getbands()
                                            or "transparency" in original.info)
                result["pixel_diagnostics"] = _analyse_pixels(rgba)

        p = result["pixel_diagnostics"]
        if p["effective_content_bbox_px"] is None:
            result["diagnostics"].append({"code": "no_detectable_content", "severity": "advisory",
                                          "detail": "No pixel outside the near-white/transparent background threshold was detected."})
        if p["dominant_16_level_color_fraction"] >= 0.99:
            result["diagnostics"].append({"code": "nearly_uniform_image", "severity": "advisory",
                                          "detail": "At least 99% of sampled pixels fall in one coarse color bin; inspect for blank or low-detail artwork."})
        bw, bh = width, height
        borders = p["background_border_px"]
        if any(value >= max(8, round((bh if edge in {"top", "bottom"} else bw) * 0.02))
               for edge, value in borders.items()):
            result["diagnostics"].append({"code": "background_border_detected", "severity": "advisory",
                                          "detail": "Near-white or transparent edge bands were detected; review crop and placement."})

        if target is not None:
            if target["unit"] == "px":
                coverage = {"width": width / target["width"], "height": height / target["height"]}
                result["pixel_coverage_ratio"] = coverage
                if min(coverage.values()) < 1:
                    result["diagnostics"].append({"code": "source_pixels_below_target", "severity": "advisory",
                                                  "detail": "Source pixels are below the requested output pixel dimensions on at least one axis."})
            else:
                inches = {"in": 1.0, "mm": 1 / 25.4, "cm": 1 / 2.54}[target["unit"]]
                ppi = {"width": width / (target["width"] * inches),
                       "height": height / (target["height"] * inches)}
                result["effective_ppi"] = ppi
                result["density_reference"] = {"minimum_diagnostic_reference_ppi": MIN_REFERENCE_PPI,
                                                "higher_detail_reference_ppi": PRINT_REFERENCE_PPI}
                if min(ppi.values()) < MIN_REFERENCE_PPI:
                    result["diagnostics"].append({"code": "below_150_ppi_reference", "severity": "advisory",
                                                  "detail": "Effective density is below the 150 PPI diagnostic reference; suitability depends on viewing size and use."})
                elif min(ppi.values()) < PRINT_REFERENCE_PPI:
                    result["diagnostics"].append({"code": "below_300_ppi_reference", "severity": "info",
                                                  "detail": "Effective density is below the 300 PPI higher-detail reference; this is not a pass/fail rule."})
        result["decode_status"] = "decoded"
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning,
            ImagePreflightError, SyntaxError, ValueError) as error:
        result["decode_status"] = "rejected"
        result["error"] = str(error) or "image could not be decoded safely"
        result["diagnostics"].append({"code": "image_not_inspected", "severity": "advisory",
                                      "detail": result["error"]})
    return result


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare_image(
    source_path: str | Path,
    output_path: str | Path,
    *,
    max_edge: int,
) -> dict[str, Any]:
    """Write an EXIF-oriented, optionally downscaled copy to a new file.

    The source is never modified. Resizing is proportional and never enlarges;
    no cropping, background removal, sharpening, or semantic edit is performed.
    The output path must not exist and its parent directory must already exist.
    The format is selected from the output suffix to make encoding explicit.
    """
    if isinstance(max_edge, bool) or not isinstance(max_edge, int) or max_edge <= 0:
        raise ValueError("max_edge must be a positive integer number of pixels")
    source = Path(source_path).expanduser()
    output = Path(output_path).expanduser()
    if not source.is_file():
        raise ImagePreflightError("source is not a regular file")
    if output.exists() or output.is_symlink():
        raise ImagePreflightError("output must be a new, non-existing file")
    if not output.parent.is_dir():
        raise ImagePreflightError("output parent directory must already exist")
    output_format = _FORMAT_BY_SUFFIX.get(output.suffix.lower())
    if output_format is None:
        raise ImagePreflightError("output suffix must be .png, .jpg, .jpeg, .webp, .tif, .tiff, or .bmp")

    input_bytes = source.stat().st_size
    if input_bytes <= 0 or input_bytes > MAX_FILE_BYTES:
        raise ImagePreflightError(f"source file must be between 1 and {MAX_FILE_BYTES} bytes")
    input_sha256 = _file_sha256(source)

    created_output = False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source) as original:
                input_format = (original.format or "").upper()
                if input_format not in SUPPORTED_FORMATS:
                    raise ImagePreflightError(f"unsupported raster format: {input_format or 'unknown'}")
                if int(getattr(original, "n_frames", 1)) != 1:
                    raise ImagePreflightError("multi-frame images are not prepared to avoid dropping animation frames")
                encoded_size = original.size
                if (encoded_size[0] <= 0 or encoded_size[1] <= 0
                        or encoded_size[0] * encoded_size[1] > MAX_IMAGE_PIXELS):
                    raise ImagePreflightError(f"decoded dimensions exceed the {MAX_IMAGE_PIXELS}-pixel safety limit")
                original.load()
                exif_orientation = int(original.getexif().get(274, 1))
                oriented = ImageOps.exif_transpose(original)
                oriented.load()
                oriented_size = oriented.size
                alpha_present = ("A" in oriented.getbands() or "transparency" in original.info)
                if alpha_present and output_format not in _ALPHA_FORMATS:
                    raise ImagePreflightError(f"{output_format} output cannot safely preserve transparency")

                width, height = oriented_size
                scale = min(1.0, max_edge / max(width, height))
                if alpha_present:
                    prepared = oriented.convert("RGBA")
                elif oriented.mode not in {"RGB", "L", "1", "I", "F", "CMYK"}:
                    prepared = oriented.convert("RGB")
                else:
                    prepared = oriented.copy()
                if scale < 1.0:
                    resized = (max(1, round(width * scale)), max(1, round(height * scale)))
                    prepared = prepared.resize(resized, Image.Resampling.LANCZOS)
                else:
                    resized = oriented_size

                if output_format == "JPEG" and prepared.mode not in {"RGB", "L", "CMYK"}:
                    prepared = prepared.convert("RGB")
                # Exclusive creation ensures this operation never overwrites a
                # file that appeared after the initial existence check.
                with output.open("xb") as target:
                    created_output = True
                    prepared.save(target, format=output_format)
                    target.flush()
                    os.fsync(target.fileno())
        if _file_sha256(source) != input_sha256:
            raise ImagePreflightError("source changed while it was being prepared")
        return {
            "schema_version": 1,
            "status": "prepared",
            "assessment": "mechanical_transform_receipt",
            "source_sha256": input_sha256,
            "output_sha256": _file_sha256(output),
            "source_bytes": input_bytes,
            "output_bytes": output.stat().st_size,
            "input_format": input_format,
            "output_format": output_format,
            "source_encoded_size_px": {"width": encoded_size[0], "height": encoded_size[1]},
            "exif_orientation": exif_orientation,
            "exif_orientation_normalized": exif_orientation not in {0, 1},
            "oriented_size_px": {"width": oriented_size[0], "height": oriented_size[1]},
            "output_size_px": {"width": resized[0], "height": resized[1]},
            "max_edge_px": max_edge,
            "alpha_preserved": alpha_present,
            "operations": ["exif_orientation_normalization"] + (["proportional_downscale_lanczos"] if scale < 1.0 else []),
            "crop": "none",
            "background_removal": "none",
            "sharpening": "none",
            "source_modified": False,
            "semantic_match_assessed": False,
        }
    except Exception:
        if created_output:
            output.unlink(missing_ok=True)
        raise
