"""Source-bound rendered-image differences, not an aesthetic quality score.

compare_visuals accepts schema_version=1, baseline/candidate objects containing
path, sha256 and renderer_signature, plus optional allowed_regions (integer
pixel x/y/width/height), threshold (default .1) and include_antialiasing (false).
The renderer signature must identify renderer/version/platform and SHA-256
fonts/settings manifests; it is caller-supplied render evidence, not inferred
from matching PNG dimensions. Incomplete/different signatures cannot establish
comparability. This module never renders, resizes, registers, or edits a source.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, UnidentifiedImageError, __version__ as PILLOW_VERSION

from .config import PROJECT_ROOT
from .platform_runtime import probe_node_runtime

NODE_SCRIPT = PROJECT_ROOT / "node/visual-diff.mjs"
VENDOR = PROJECT_ROOT / "node/vendor/pixelmatch"
MAX_PIXELS = 20_000_000
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SIGNATURE_KEYS = {"renderer", "version", "platform", "fonts_sha256", "settings_sha256"}


class VisualDiffError(ValueError):
    """Invalid/stale inputs or a failed comparator. No candidate is published."""


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _object(value: object, name: str, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict) or not required <= value.keys() or value.keys() - allowed:
        raise VisualDiffError(f"{name} requires {sorted(required)}; allowed fields: {sorted(allowed)}")
    return value


def _signature(value: object) -> bool:
    return (isinstance(value, dict) and _SIGNATURE_KEYS <= value.keys()
            and all(isinstance(value[key], str) and value[key].strip() for key in ("renderer", "version", "platform"))
            and all(isinstance(value[key], str) and _SHA.fullmatch(value[key]) for key in ("fonts_sha256", "settings_sha256")))


def _image(value: object, label: str) -> tuple[dict, Image.Image]:
    data = _object(value, label, {"path", "sha256", "renderer_signature"}, {"path", "sha256"})
    if not isinstance(data["path"], str) or not data["path"]:
        raise VisualDiffError(f"{label}.path must be a PNG file path")
    if not isinstance(data["sha256"], str) or not _SHA.fullmatch(data["sha256"]):
        raise VisualDiffError(f"{label}.sha256 must be a lowercase SHA-256")
    path = Path(data["path"]).expanduser().resolve(strict=True)
    content = path.read_bytes()
    digest = _hash(content)
    if digest != data["sha256"]:
        raise VisualDiffError(f"{label} SHA-256 mismatch: file changed since render evidence was recorded")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                raise VisualDiffError(f"{label} must be a single-frame PNG")
            if image.width * image.height > MAX_PIXELS:
                raise VisualDiffError(f"{label} exceeds {MAX_PIXELS} pixels")
            rgba = image.convert("RGBA")
            # Pillow does not automatically color-manage PNG conversion. Differing
            # declarations must not be silently treated as a rendered regression.
            color = {"icc_profile_sha256": _hash(image.info["icc_profile"]) if image.info.get("icc_profile") else None,
                     "gamma": image.info.get("gamma"), "srgb": image.info.get("srgb")}
    except (UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise VisualDiffError(f"{label} is not a supported PNG: {exc}") from exc
    return {"path": str(path), "sha256": digest, "width": rgba.width, "height": rgba.height,
            "renderer_signature": data.get("renderer_signature"), "color_metadata": color,
            "rgba_sha256": _hash(rgba.tobytes())}, rgba


def _regions(value: object, width: int, height: int) -> list[dict]:
    if not isinstance(value, list) or len(value) > 1000:
        raise VisualDiffError("allowed_regions must be an array of at most 1000 rectangles")
    for region in value:
        _object(region, "allowed region", {"x", "y", "width", "height"}, {"x", "y", "width", "height"})
        if any(type(region[key]) is not int for key in region):
            raise VisualDiffError("allowed region coordinates must be integer pixels")
        if region["x"] < 0 or region["y"] < 0 or region["width"] <= 0 or region["height"] <= 0:
            raise VisualDiffError("allowed region origins must be nonnegative and sizes positive")
        if region["x"] + region["width"] > width or region["y"] + region["height"] > height:
            raise VisualDiffError("allowed region is outside baseline image bounds")
    return value


def _provenance() -> dict:
    value = json.loads((VENDOR / "provenance.json").read_text(encoding="utf-8"))
    for item in value["files"]:
        if item["path"] not in {"index.js", "LICENSE"} or _hash((VENDOR / item["path"]).read_bytes()) != item["sha256"]:
            raise VisualDiffError("vendored pixelmatch does not match its pinned provenance")
    return {"name": value["name"], "version": value["version"], "commit": value["commit"], "license": value["license"],
            "repository": value["repository"], "files": value["files"],
            "provenance_sha256": _hash((VENDOR / "provenance.json").read_bytes()),
            "adapter_sha256": _hash(NODE_SCRIPT.read_bytes()), "python_adapter_sha256": _hash(Path(__file__).read_bytes()),
            "pillow_version": PILLOW_VERSION}


def compare_visuals(request: dict, output: Path) -> dict:
    """Atomically publish receipt.json and optional diff.png into a NEW directory.

    An absent/empty allowed_regions list compares the whole page and reports
    changed. With explicit regions, a change outside their union reports
    unexpected_changes. Even an unchanged result means only no differences
    detected at the recorded threshold/antialias settings.
    """
    _object(request, "request", {"schema_version", "baseline", "candidate", "allowed_regions", "threshold", "include_antialiasing"},
            {"schema_version", "baseline", "candidate"})
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise VisualDiffError("schema_version must be 1")
    threshold = request.get("threshold", 0.1)
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise VisualDiffError("threshold must be a finite number between 0 and 1")
    include_aa = request.get("include_antialiasing", False)
    if type(include_aa) is not bool:
        raise VisualDiffError("include_antialiasing must be boolean")
    # Also reject non-JSON signatures before writing any evidence.
    request_hash = _hash(_json_bytes(request))
    output = Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise VisualDiffError("output must be a new directory; existing evidence is never overwritten")
    baseline, before = _image(request["baseline"], "baseline")
    candidate, after = _image(request["candidate"], "candidate")
    regions = _regions(request.get("allowed_regions", []), before.width, before.height)
    reasons = []
    signatures = (baseline["renderer_signature"], candidate["renderer_signature"])
    if not all(_signature(item) for item in signatures):
        reasons.append("missing_or_incomplete_renderer_signature")
    elif signatures[0] != signatures[1]:
        reasons.append("renderer_signature_mismatch")
    if before.size != after.size:
        reasons.append("image_dimensions_mismatch")
    if baseline["color_metadata"] != candidate["color_metadata"]:
        reasons.append("png_color_metadata_mismatch")
    receipt = {"schema_version": 1, "kind": "rendered_visual_diff", "status": "not_comparable", "reasons": reasons,
               "request_sha256": request_hash, "baseline": baseline, "candidate": candidate,
               "renderer_signature_sha256": _hash(_json_bytes(signatures[0])) if all(_signature(item) for item in signatures) and signatures[0] == signatures[1] else None,
               "allowed_regions": regions, "scope": "allowed_regions" if regions else "whole_image",
               "parameters": {"threshold": threshold, "include_antialiasing": include_aa, "checkerboard": True},
               "algorithm": _provenance(), "summary": None, "artifacts": {},
               "interpretation": "Pixel differences are review evidence, not an aesthetic score or proof of semantic correctness."}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        if not reasons:
            node_runtime = probe_node_runtime()
            if node_runtime.get("status") != "passed":
                reason = str(node_runtime.get("reason") or node_runtime.get("probe") or "no compatible executable was found")
                raise VisualDiffError("A compatible host-native Node.js 22.12+ runtime is unavailable: " + reason)
            node = Path(str(node_runtime["path"]))
            with tempfile.TemporaryDirectory(prefix="rgba-", dir=staging) as raw_dir:
                raw = Path(raw_dir)
                (raw / "baseline.rgba").write_bytes(before.tobytes())
                (raw / "candidate.rgba").write_bytes(after.tobytes())
                native_request = {"baseline_rgba": str(raw / "baseline.rgba"), "candidate_rgba": str(raw / "candidate.rgba"),
                                  "mask_rgba": str(raw / "mask.rgba"), "width": before.width, "height": before.height,
                                  "threshold": threshold, "include_antialiasing": include_aa, "allowed_regions": regions}
                (raw / "request.json").write_bytes(_json_bytes(native_request))
                try:
                    result = subprocess.run([str(node), str(NODE_SCRIPT), str(raw / "request.json")], capture_output=True, text=True, timeout=90, check=False)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise VisualDiffError(f"pixelmatch execution failed: {exc}") from exc
                if result.returncode:
                    raise VisualDiffError(f"pixelmatch execution failed: {result.stderr.strip()}")
                try:
                    summary = json.loads(result.stdout)
                    mask = Image.frombytes("RGBA", before.size, (raw / "mask.rgba").read_bytes())
                except (OSError, ValueError) as exc:
                    raise VisualDiffError(f"invalid pixelmatch output: {exc}") from exc
                mask.save(staging / "diff.png", format="PNG")
                receipt["summary"] = summary
                receipt["status"] = ("unchanged" if summary["changed_pixels"] == 0 else
                                     "unexpected_changes" if regions and summary["outside_allowed_changed_pixels"] else "changed")
                receipt["algorithm"]["node_executable"] = str(node)
                receipt["artifacts"]["diff_png"] = {"path": str(output / "diff.png"), "sha256": _hash((staging / "diff.png").read_bytes())}
        (staging / "receipt.json").write_bytes(_json_bytes(receipt))
        # A dedicated lock avoids two comparisons publishing to one destination.
        lock = output.parent / f".{output.name}.publish.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise VisualDiffError("another comparison is publishing this output") from exc
        try:
            os.close(descriptor)
            if output.exists() or output.is_symlink():
                raise VisualDiffError("output appeared during comparison; existing evidence was preserved")
            staging.rename(output)
        finally:
            lock.unlink(missing_ok=True)
    return receipt
