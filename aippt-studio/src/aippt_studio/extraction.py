from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import posixpath
import shutil
import subprocess
import zipfile
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .config import PROJECT_ROOT
from .runtime import officecli_executable
from .models import (
    SourceAsset,
    SourceAssetReference,
    SourceDeckElement,
    SourceDeckManifest,
    SourceInspectionIndex,
    SourceRecord,
    SourceSlideManifest,
)
from .storage import job_dir, load_job, locked_job, read_model, write_json


class ExtractionError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _officecli() -> Path:
    executable = officecli_executable()
    if executable is None:
        raise ExtractionError("A compatible OfficeCLI is unavailable; set AIPPT_OFFICECLI or install the host binary in .tools")
    return executable


def _run_officecli(executable: Path, arguments: list[str], *, timeout: int = 180) -> str:
    try:
        completed = subprocess.run(
            [str(executable), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except OSError as error:
        raise ExtractionError(f"cannot start OfficeCLI: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise ExtractionError(f"OfficeCLI timed out: {' '.join(arguments)}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "OfficeCLI command failed").strip()
        raise ExtractionError(detail)
    return completed.stdout


def _json_command(executable: Path, arguments: list[str]) -> dict[str, Any]:
    output = _run_officecli(executable, [*arguments, "--json"])
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise ExtractionError(f"OfficeCLI returned invalid JSON for {' '.join(arguments)}") from error
    if payload.get("success") is False:
        error = payload.get("error") or payload.get("message") or "OfficeCLI command failed"
        raise ExtractionError(str(error))
    return payload.get("data", payload)


def _first_result(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise ExtractionError("OfficeCLI returned no document node")
    return results[0]


def _slide_count(root_node: dict[str, Any]) -> int:
    child_count = root_node.get("childCount")
    if isinstance(child_count, int) and child_count >= 0:
        return child_count
    children = root_node.get("children")
    if isinstance(children, list):
        return sum(1 for child in children if isinstance(child, dict) and child.get("type") == "slide")
    raise ExtractionError("OfficeCLI did not report a slide count")


def _element_from_node(node: dict[str, Any]) -> SourceDeckElement:
    fmt = node.get("format") if isinstance(node.get("format"), dict) else {}
    raw_id = fmt.get("id")
    element_id = raw_id if isinstance(raw_id, int) else None
    raw_text = node.get("text")
    raw_preview = node.get("preview")
    return SourceDeckElement(
        path=str(node.get("path", "")),
        type=str(node.get("type", "unknown")),
        id=element_id,
        name=str(fmt["name"]) if fmt.get("name") is not None else None,
        text=str(raw_text) if raw_text is not None else "",
        preview=str(raw_preview) if raw_preview is not None else "",
        format=fmt,
    )


def _related_part(owner: str, relationship: ElementTree.Element) -> str | None:
    target = relationship.get("Target", "")
    if not target or relationship.get("TargetMode", "").lower() == "external":
        return None
    # Only package-local parts can be copied. An unresolved relationship must
    # not become an external fetch or a filename-based guess.
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in target:
        return None
    part = posixpath.normpath(target.lstrip("/") if target.startswith("/") else
                             posixpath.join(posixpath.dirname(owner), target))
    return part if part not in {"", ".", ".."} and not part.startswith("../") else None


def _relationships(archive: zipfile.ZipFile, owner: str) -> dict[str, ElementTree.Element]:
    rels_path = posixpath.join(posixpath.dirname(owner), "_rels", posixpath.basename(owner) + ".rels")
    try:
        root = ElementTree.fromstring(archive.read(rels_path))
    except (KeyError, ElementTree.ParseError):
        return {}
    result: dict[str, ElementTree.Element] = {}
    ambiguous: set[str] = set()
    for relationship in root:
        rel_id = relationship.get("Id")
        if not rel_id:
            continue
        if rel_id in result:
            ambiguous.add(rel_id)
        result[rel_id] = relationship
    return {rel_id: relationship for rel_id, relationship in result.items() if rel_id not in ambiguous}


def _ordered_slide_parts(archive: zipfile.ZipFile) -> dict[int, str]:
    """Resolve logical pages through presentation relationships, never filenames.

    This uses the same ownership/order model as ppt-master's _parse_slide_refs;
    absent relationships retain their logical index instead of shifting pages.
    """
    try:
        presentation = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
    except (KeyError, ElementTree.ParseError):
        return {}
    relationships = _relationships(archive, "ppt/presentation.xml")
    parts: dict[int, str] = {}
    for p_namespace, r_namespace in (
        ("http://schemas.openxmlformats.org/presentationml/2006/main",
         "http://schemas.openxmlformats.org/officeDocument/2006/relationships"),
        ("http://purl.oclc.org/ooxml/presentationml/main",
         "http://purl.oclc.org/ooxml/officeDocument/relationships"),
    ):
        slide_ids = presentation.findall(f"{{{p_namespace}}}sldIdLst/{{{p_namespace}}}sldId")
        for index, slide in enumerate(slide_ids, 1):
            relationship = relationships.get(slide.get(f"{{{r_namespace}}}id", ""))
            if relationship is None or relationship.get("Type") != r_namespace + "/slide":
                continue
            part = _related_part("ppt/presentation.xml", relationship)
            if part and part in archive.namelist():
                parts[index] = part
        if slide_ids:
            break
    return parts


def _media_part_for_rel_id(archive: zipfile.ZipFile, slide_index: int, rel_id: str,
                          *, slide_parts: dict[int, str] | None = None) -> str | None:
    parts = _ordered_slide_parts(archive) if slide_parts is None else slide_parts
    owner = parts.get(slide_index)
    if owner is None:
        return None
    relationship = _relationships(archive, owner).get(rel_id)
    return _related_part(owner, relationship) if relationship is not None else None


def _asset_kind(content_type: str | None, package_path: str | None) -> str:
    value = (content_type or "").lower()
    suffix = PurePosixPath(package_path or "").suffix.lower()
    if value.startswith("video/") or suffix in {".mp4", ".mov", ".wmv", ".avi"}:
        return "video"
    if value.startswith("audio/") or suffix in {".mp3", ".wav", ".m4a", ".aac"}:
        return "audio"
    if value.startswith("image/") or suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"}:
        return "image"
    return "unknown"


def _asset_suffix(content_type: str | None, package_path: str | None) -> str:
    suffix = PurePosixPath(package_path or "").suffix
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension(content_type or "")
    return guessed or ".bin"


def _extract_assets(
    source_path: Path,
    source_id: str,
    slides: list[SourceSlideManifest],
    output_root: Path,
    path_base: Path | None = None,
) -> list[SourceAsset]:
    assets_by_package: dict[str, SourceAsset] = {}
    assets_dir = output_root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    relative_base = path_base or output_root.parent
    try:
        archive = zipfile.ZipFile(source_path)
    except zipfile.BadZipFile as error:
        raise ExtractionError(f"invalid PPTX archive: {source_path.name}") from error
    with archive:
        slide_parts = _ordered_slide_parts(archive)
        for slide in slides:
            for element in slide.elements:
                fmt = element.format
                image_source = str(fmt.get("image.src") or "")
                if element.type not in {"picture", "media", "ole"} and not image_source:
                    continue
                rel_id = fmt.get("relId")
                package_path = _media_part_for_rel_id(archive, slide.index, str(rel_id), slide_parts=slide_parts) if rel_id else None
                if not rel_id and image_source:
                    candidate = f"ppt/media/{PurePosixPath(image_source).name}"
                    package_path = candidate if candidate in archive.namelist() else None
                key = package_path or f"{slide.index}:{element.path}"
                asset = assets_by_package.get(key)
                if asset is None:
                    asset_id = f"asset-{len(assets_by_package) + 1:03d}"
                    content_type = str(fmt["contentType"]) if fmt.get("contentType") else None
                    asset = SourceAsset(
                        id=asset_id,
                        source_id=source_id,
                        kind=_asset_kind(content_type, package_path),
                        package_path=package_path,
                        content_type=content_type,
                        rel_id=str(rel_id) if rel_id else None,
                        file_size=fmt.get("fileSize") if isinstance(fmt.get("fileSize"), int) else None,
                    )
                    if package_path and package_path in archive.namelist():
                        target = assets_dir / f"{asset_id}{_asset_suffix(content_type, package_path)}"
                        target.write_bytes(archive.read(package_path))
                        asset.stored_path = target.relative_to(relative_base).as_posix()
                        asset.sha256 = _sha256(target)
                        asset.extraction_status = "extracted"
                    else:
                        asset.extraction_status = "failed"
                    assets_by_package[key] = asset
                asset.references.append(SourceAssetReference(slide_id=slide.id, element_path=element.path))
                element.asset_id = asset.id
    return list(assets_by_package.values())


def _query_paths(executable: Path, source_path: Path, selector: str) -> list[str]:
    try:
        payload = _json_command(executable, ["query", str(source_path), selector])
    except ExtractionError:
        return []
    return [str(item.get("path")) for item in payload.get("results", []) if isinstance(item, dict) and item.get("path")]


def inspect_source_file(
    source_path: Path,
    *,
    source_id: str,
    original: str,
    stored_path: str,
    source_sha256: str | None = None,
    output_root: Path,
    path_base: Path | None = None,
    capture_screenshots: bool = True,
) -> SourceDeckManifest:
    executable = _officecli()
    output_root.mkdir(parents=True, exist_ok=True)
    relative_base = path_base or output_root.parent
    try:
        _run_officecli(executable, ["close", str(source_path)], timeout=30)
    except ExtractionError:
        pass
    root_node = _first_result(_json_command(executable, ["get", str(source_path), "/", "--depth", "1"]))
    slide_count = _slide_count(root_node)
    root_format = root_node.get("format") if isinstance(root_node.get("format"), dict) else {}
    slides: list[SourceSlideManifest] = []
    warnings: list[str] = []
    for index in range(1, slide_count + 1):
        try:
            node = _first_result(_json_command(executable, ["get", str(source_path), f"/slide[{index}]", "--depth", "1"]))
        except ExtractionError as error:
            warnings.append(f"slide {index} could not be inspected: {error}")
            node = {"path": f"/slide[{index}]", "type": "slide", "format": {}}
        slide_id = f"slide-{index:03d}"
        slide_format = node.get("format") if isinstance(node.get("format"), dict) else {}
        elements = [_element_from_node(child) for child in node.get("children", []) if isinstance(child, dict)]
        screenshot_path: str | None = None
        if capture_screenshots:
            screenshot = output_root / "slides" / f"{slide_id}.png"
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            try:
                _run_officecli(
                    executable,
                    ["view", str(source_path), "screenshot", "--page", str(index), "--out", str(screenshot)],
                    timeout=180,
                )
                screenshot_path = screenshot.relative_to(relative_base).as_posix()
            except ExtractionError as error:
                warnings.append(f"slide {index} screenshot failed: {error}")
        slides.append(
            SourceSlideManifest(
                id=slide_id,
                index=index,
                name=str(slide_format["name"]) if slide_format.get("name") else "",
                layout=str(slide_format["layout"]) if slide_format.get("layout") else None,
                screenshot_path=screenshot_path,
                elements=elements,
                transition={key: value for key, value in slide_format.items() if key.startswith("transition") or key in {"advanceTime", "advanceClick"}} or None,
            )
        )
    animation_paths = _query_paths(executable, source_path, "animation")
    transition_paths = _query_paths(executable, source_path, "transition")
    for slide in slides:
        slide.animation_paths = [path for path in animation_paths if path.startswith(f"/slide[{slide.index}]")]
        slide.transition = slide.transition or ({"paths": [path for path in transition_paths if path.startswith(f"/slide[{slide.index}]")]} if any(path.startswith(f"/slide[{slide.index}]") for path in transition_paths) else None)
    assets = _extract_assets(source_path, source_id, slides, output_root, relative_base)
    manifest = SourceDeckManifest(
        source_id=source_id,
        original=original,
        stored_path=stored_path,
        source_sha256=source_sha256 or _sha256(source_path),
        size_bytes=source_path.stat().st_size,
        slide_count=slide_count,
        slide_width=str(root_format["slideWidth"]) if root_format.get("slideWidth") else None,
        slide_height=str(root_format["slideHeight"]) if root_format.get("slideHeight") else None,
        slide_size=str(root_format["slideSize"]) if root_format.get("slideSize") else None,
        default_font=str(root_format["defaultFont"]) if root_format.get("defaultFont") else None,
        engine="OfficeCLI",
        engine_version=_run_officecli(executable, ["--version"], timeout=15).strip(),
        slides=slides,
        assets=assets,
        warnings=warnings,
    )
    write_json(output_root / "source-deck-manifest.json", manifest)
    return manifest


@locked_job
def inspect_job_sources(job_id: str, source_id: str | None = None) -> dict[str, Any]:
    job = load_job(job_id)
    root = job_dir(job_id)
    records = [
        read_model(root / "normalized" / "sources" / f"{item}.json", SourceRecord)
        for item in job.source_ids
    ]
    if source_id:
        records = [record for record in records if record.id == source_id]
    records = [record for record in records if record.kind == "powerpoint"]
    if not records:
        raise ValueError("no PowerPoint source is available for inspection")
    manifests: list[SourceDeckManifest] = []
    for record in records:
        source_path = (root / record.stored_path).resolve()
        manifest_root = root / "extraction" / record.id
        manifests.append(
            inspect_source_file(
                source_path,
                source_id=record.id,
                original=record.original,
                stored_path=record.stored_path,
                source_sha256=record.sha256,
                output_root=manifest_root,
                path_base=root,
            )
        )
    index = SourceInspectionIndex(
        job_id=job_id,
        manifests=[f"extraction/{manifest.source_id}/source-deck-manifest.json" for manifest in manifests],
    )
    write_json(root / "extraction" / "index.json", index)
    return {
        "job_id": job_id,
        "index": str((root / "extraction" / "index.json").resolve()),
        "manifests": [manifest.model_dump(mode="json") for manifest in manifests],
    }
