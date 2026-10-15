"""Bind a reviewed image/vector embedded in a catalogued library deck to a slide.

Catalog and structure records are used to establish provenance and locate a
candidate. The requested visible slide and picture shape are still verified
against the current PPTX bytes before any media is copied into a Work.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree

from PIL import Image
from pydantic import BaseModel, Field

from .assets.acquisition import AssetAcquisitionError, _invalidate_rendered_artifacts
from .config import WORKSPACE_ROOT

from .library_read_guard import require_library_available
from .models import DeckPlan, JobState, utc_now
from .storage import job_dir, load_job, locked_job, read_model, save_job, write_json


MAX_MEDIA_BYTES = 20 * 1024 * 1024
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_ASVG = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
_SUPPORTED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg"}
_MEDIA_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".svg": "image/svg+xml",
}


class LibraryAssetError(AssetAcquisitionError):
    """A library media candidate could not be safely and exactly bound."""


class LibraryMedia(BaseModel):
    id: str
    kind: str = "image"
    slide_id: str
    purpose: str
    selection_note: str = ""
    # A task-level disposition is an explicit caller assertion. It never
    # upgrades catalog/source rights, which remain recorded separately.
    task_reuse_authorized: bool = False
    rights_handling_note: str = ""
    stored_path: str
    media_sha256: str
    size_bytes: int
    media_type: str
    source_path: str
    requested_source_path: str | None = None
    source_sha256: str
    source_slide: int
    shape_id: int
    relationship_id: str
    media_part: str
    rights: str = "unverified"
    acquired_at: str = Field(default_factory=utc_now)


class LibraryAssetLedger(BaseModel):
    schema_version: int = 1
    assets: list[LibraryMedia] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    except (OSError, ValueError) as error:
        raise LibraryAssetError(f"library index is unavailable or invalid: {path.name}") from error
    return rows


def _library_source(relative_path: str) -> tuple[Path, dict, str]:
    rel = Path(relative_path)
    if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() != ".pptx":
        raise LibraryAssetError("library media source must be a relative PPTX path")
    library = (WORKSPACE_ROOT / "模板库").resolve()
    require_library_available(library, LibraryAssetError)
    requested = rel.as_posix()
    source = (library / rel).resolve()
    if library not in source.parents:
        raise LibraryAssetError("library media source escapes 模板库")
    normalized = source.relative_to(library).as_posix()
    alias_record = None
    if not source.is_file():
        aliases_path = library / "RENAME-ALIASES.jsonl"
        try:
            aliases = [json.loads(line) for line in aliases_path.read_text(encoding="utf-8-sig").splitlines()
                       if line.strip()]
        except FileNotFoundError:
            aliases = []
        except (OSError, ValueError) as error:
            raise LibraryAssetError("library rename alias index is invalid") from error
        alias_record = next((item for item in aliases if isinstance(item, dict)
                             and item.get("schema") == "aippt.library-path-alias.v1"
                             and item.get("old_path") == requested), None)
        if alias_record is None:
            raise LibraryAssetError("library media source is missing or escapes 模板库")
        current = alias_record.get("current_path")
        if not isinstance(current, str):
            raise LibraryAssetError("library source alias has no current_path")
        current_rel = Path(current)
        if current_rel.is_absolute() or ".." in current_rel.parts or current_rel.suffix.lower() != ".pptx":
            raise LibraryAssetError("library source alias current_path is unsafe")
        source = (library / current_rel).resolve()
        if library not in source.parents or not source.is_file():
            raise LibraryAssetError("library source alias target is missing or escapes 模板库")
        normalized = source.relative_to(library).as_posix()

    catalog = _read_rows(library / "CATALOG.jsonl")
    catalog_row = next((row for row in catalog if row.get("path") == normalized), None)
    if catalog_row is None or str(catalog_row.get("asset_type", "")).casefold() not in {
        "template", "diagram_asset", "diagram asset", "asset", "素材", "图示素材",
    }:
        raise LibraryAssetError("source PPTX is not a catalogued template or diagram asset")
    structures = _read_rows(library / "DESIGN-STRUCTURE.jsonl")
    structure = next((row for row in structures if row.get("source_path") == normalized), None)
    if (structure is None or structure.get("schema") != "aippt.template-design-index.v1"
            or structure.get("status") != "parsed"
            or not isinstance(structure.get("coverage"), dict)
            or structure["coverage"].get("full_slide_coverage") is not True):
        raise LibraryAssetError("source PPTX has no full-coverage structural index")
    expected = structure.get("source_sha256")
    if alias_record is not None and alias_record.get("source_sha256") != expected:
        raise LibraryAssetError("library source alias hash does not match the current design index")
    catalog_hash = catalog_row.get("source_sha256")
    if catalog_hash not in (None, ""):
        if (not isinstance(catalog_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", catalog_hash)
                or catalog_hash.casefold() != str(expected).casefold()):
            raise LibraryAssetError("catalog and design-index source hashes disagree")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise LibraryAssetError("design index has no valid source SHA-256")
    if _sha256_file(source) != expected:
        raise LibraryAssetError("library PPTX bytes no longer match the indexed source hash")
    if alias_record is not None:
        catalog_row = dict(catalog_row)
        catalog_row["_requested_source_path"] = requested
    return source, catalog_row, expected


def _internal_target(owner: str, rel: ElementTree.Element) -> str | None:
    target = rel.get("Target", "")
    if not target or rel.get("TargetMode", "").casefold() == "external":
        return None
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or "\\" in target:
        return None
    part = posixpath.normpath(target.lstrip("/") if target.startswith("/")
                              else posixpath.join(posixpath.dirname(owner), target))
    if part in {"", ".", ".."} or part.startswith("../"):
        return None
    return part


def _relationships(archive: zipfile.ZipFile, owner: str) -> dict[str, ElementTree.Element]:
    rel_path = posixpath.join(posixpath.dirname(owner), "_rels", posixpath.basename(owner) + ".rels")
    try:
        root = ElementTree.fromstring(archive.read(rel_path))
    except (KeyError, ElementTree.ParseError) as error:
        raise LibraryAssetError(f"missing or invalid relationships for {owner}") from error
    rels: dict[str, ElementTree.Element] = {}
    for item in root.findall(f"{{{_REL}}}Relationship"):
        rel_id = item.get("Id")
        if not rel_id or rel_id in rels:
            raise LibraryAssetError(f"ambiguous relationship in {owner}")
        rels[rel_id] = item
    return rels


def _visible_slide_part(archive: zipfile.ZipFile, source_slide: int) -> str:
    if source_slide < 1:
        raise LibraryAssetError("source_slide must be a visible one-based page number")
    try:
        presentation = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
    except (KeyError, ElementTree.ParseError) as error:
        raise LibraryAssetError("PPTX has no valid presentation.xml") from error
    rels = _relationships(archive, "ppt/presentation.xml")
    slide_ids = presentation.find(f"{{{_P}}}sldIdLst")
    if slide_ids is None:
        raise LibraryAssetError("PPTX has no visible slides")
    ordered: list[str] = []
    for slide_id in slide_ids.findall(f"{{{_P}}}sldId"):
        if slide_id.get("show", "1").casefold() in {"0", "false", "off"}:
            continue
        rel_id = slide_id.get(f"{{{_R}}}id")
        rel = rels.get(rel_id or "")
        if rel is None or not rel.get("Type", "").endswith("/slide"):
            raise LibraryAssetError("visible slide relationship is missing or invalid")
        target = _internal_target("ppt/presentation.xml", rel)
        if target is None:
            raise LibraryAssetError("visible slide relationship is external or unsafe")
        ordered.append(target)
    if source_slide > len(ordered):
        raise LibraryAssetError(f"source_slide {source_slide} is outside the visible deck")
    return ordered[source_slide - 1]


def _picture_media(archive: zipfile.ZipFile, slide_part: str, shape_id: int) -> tuple[str, str]:
    try:
        slide = ElementTree.fromstring(archive.read(slide_part))
    except (KeyError, ElementTree.ParseError) as error:
        raise LibraryAssetError("selected visible slide is missing or invalid") from error
    matches: list[ElementTree.Element] = []
    for picture in slide.iter(f"{{{_P}}}pic"):
        nv = picture.find(f"{{{_P}}}nvPicPr/{{{_P}}}cNvPr")
        if nv is not None and nv.get("id") == str(shape_id):
            matches.append(picture)
    if len(matches) != 1:
        raise LibraryAssetError("shape_id does not identify exactly one picture on the requested visible page")
    blip = matches[0].find(f"{{{_P}}}blipFill/{{{_A}}}blip")
    if blip is None:
        raise LibraryAssetError("selected picture has no embedded bitmap relationship")
    if blip.get(f"{{{_R}}}link"):
        raise LibraryAssetError("externally linked pictures are not supported")
    relationships = _relationships(archive, slide_part)

    # Office stores many vector images as an SVG extension attached to a PNG
    # blip fallback. Prefer the vector part when it is explicitly embedded.
    # If the SVG extension exists but is unsafe or malformed, fail closed
    # instead of silently claiming that its raster fallback preserved vector.
    svg_blips = matches[0].findall(f".//{{{_ASVG}}}svgBlip")
    if len(svg_blips) > 1:
        raise LibraryAssetError("selected picture has ambiguous embedded SVG relationships")
    if svg_blips:
        svg_id = svg_blips[0].get(f"{{{_R}}}embed")
        if not svg_id or svg_blips[0].get(f"{{{_R}}}link"):
            raise LibraryAssetError("selected SVG is not an embedded local relationship")
        svg_rel = relationships.get(svg_id)
        if svg_rel is None or not svg_rel.get("Type", "").endswith("/image"):
            raise LibraryAssetError("selected SVG relationship is missing or invalid")
        part = _internal_target(slide_part, svg_rel)
        if part is None:
            raise LibraryAssetError("selected SVG target is external or unsafe")
        if Path(part).suffix.lower() != ".svg":
            raise LibraryAssetError("selected SVG relationship does not target an SVG part")
        rel_id = svg_id
    else:
        rel_id = blip.get(f"{{{_R}}}embed")
        if not rel_id:
            raise LibraryAssetError("selected picture has no embedded media relationship")
        rel = relationships.get(rel_id)
        if rel is None or not rel.get("Type", "").endswith("/image"):
            raise LibraryAssetError("selected picture relationship is missing or is not an image")
        part = _internal_target(slide_part, rel)
        if part is None:
            raise LibraryAssetError("selected picture media target is external or unsafe")
    suffix = Path(part).suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise LibraryAssetError("selected picture format is unsupported; use PNG, JPEG, GIF, or SVG")
    try:
        info = archive.getinfo(part)
    except KeyError as error:
        raise LibraryAssetError("selected image part is missing from the PPTX") from error
    if info.file_size > MAX_MEDIA_BYTES:
        raise LibraryAssetError("selected media exceeds the 20 MiB safety limit")
    payload = archive.read(part)
    if len(payload) > MAX_MEDIA_BYTES:
        raise LibraryAssetError("selected media exceeds the 20 MiB safety limit")
    _validate_media(payload, suffix)
    return rel_id, part


def _validate_media(payload: bytes, suffix: str) -> None:
    if suffix == ".svg":
        try:
            root = ElementTree.fromstring(payload)
        except ElementTree.ParseError as error:
            raise LibraryAssetError("selected SVG is invalid XML") from error
        if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
            raise LibraryAssetError("selected media is not an SVG document")
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1].casefold()
            if tag in {"script", "foreignobject", "iframe", "object", "embed"}:
                raise LibraryAssetError("active SVG elements are not allowed")
            if tag == "style" and re.search(
                    r"(?i)(?:@import|url\s*\(\s*['\"]?\s*(?!#)|https?:|file:|javascript:|data:)",
                    element.text or ""):
                raise LibraryAssetError("SVG stylesheet contains active or external resources")
            for key, value in element.attrib.items():
                name = key.rsplit("}", 1)[-1].casefold()
                if name.startswith("on"):
                    raise LibraryAssetError("SVG event handlers are not allowed")
                if name in {"href", "src"} and value.strip() and not value.strip().startswith("#"):
                    raise LibraryAssetError("SVG external or data references are not allowed")
                if re.search(r"(?i)(?:url\s*\(\s*['\"]?\s*(?!#)|@import|https?:|file:|javascript:)", value):
                    raise LibraryAssetError("SVG external resources or active CSS are not allowed")
        return
    try:
        from io import BytesIO
        with Image.open(BytesIO(payload)) as image:
            formats = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".gif": "GIF"}
            if image.format != formats[suffix]:
                raise LibraryAssetError("image extension and encoded format disagree")
            image.verify()
    except LibraryAssetError:
        raise
    except Exception as error:
        raise LibraryAssetError("selected raster image is invalid") from error


@locked_job
def bind_library_media(
    job_id: str,
    relative_path: str,
    *,
    source_slide: int,
    shape_id: int,
    slide_id: str,
    purpose: str,
    selection_note: str = "",
    task_reuse_authorized: bool = False,
    rights_handling_note: str = "",
) -> dict:
    """Copy one explicitly selected picture from a catalogued library slide.

    ``source_slide`` follows the visible PowerPoint slide order. ``shape_id``
    is the OOXML non-visual picture id shown by the source deck inspection.
    """
    if (isinstance(source_slide, bool) or not isinstance(source_slide, int) or source_slide < 1
            or isinstance(shape_id, bool) or not isinstance(shape_id, int) or shape_id < 1):
        raise LibraryAssetError("source_slide and shape_id must be positive integers")
    job = load_job(job_id)
    if job.state == JobState.DELIVERED:
        raise LibraryAssetError("delivered jobs cannot bind new library media")
    if not purpose.strip():
        raise LibraryAssetError("purpose is required for a bound library asset")
    if not selection_note.strip():
        raise LibraryAssetError("selection_note is required to record the current task's concrete choice")
    if not rights_handling_note.strip():
        raise LibraryAssetError("rights_handling_note is required for this task's rights disposition")
    if task_reuse_authorized is not True:
        raise LibraryAssetError(
            "direct library-media binding requires explicit task_reuse_authorized=True; "
            "reference-only candidates cannot be silently placed in the deck"
        )
    root = job_dir(job_id)
    plan_path = root / "planning" / "deck-plan.json"
    if not plan_path.is_file():
        raise LibraryAssetError("a current deck plan is required before binding library media")
    plan = read_model(plan_path, DeckPlan)
    slide = next((item for item in plan.slides if item.id == slide_id), None)
    if slide is None:
        raise LibraryAssetError(f"unknown target slide_id: {slide_id}")

    source, catalog_row, source_sha = _library_source(relative_path)
    try:
        with zipfile.ZipFile(source) as archive:
            slide_part = _visible_slide_part(archive, source_slide)
            relationship_id, media_part = _picture_media(archive, slide_part, shape_id)
            payload = archive.read(media_part)
    except (OSError, zipfile.BadZipFile) as error:
        raise LibraryAssetError("library source is not a readable PPTX") from error
    if _sha256_file(source) != source_sha:
        raise LibraryAssetError("library PPTX changed while its media was being read")

    ledger_path = root / "assets" / "library" / "ledger.json"
    ledger = read_model(ledger_path, LibraryAssetLedger) if ledger_path.is_file() else LibraryAssetLedger()
    media_sha = _sha256_bytes(payload)
    # Rebinding the same source object to the same page is idempotent.
    existing = next((item for item in ledger.assets
                     if item.source_sha256 == source_sha and item.source_slide == source_slide
                     and item.shape_id == shape_id and item.slide_id == slide_id), None)
    item = None
    if existing is not None:
        stored = (root / existing.stored_path).resolve()
        if stored.is_file() and _sha256_file(stored) == existing.media_sha256 == media_sha:
            existing.purpose = purpose.strip()
            existing.selection_note = selection_note.strip()
            existing.task_reuse_authorized = True
            existing.rights_handling_note = rights_handling_note.strip()
            existing.rights = str(catalog_row.get("media_rights") or "unverified")
            reference = f"library:{existing.id}"
            if reference in slide.asset_refs:
                ledger.updated_at = utc_now()
                write_json(ledger_path, ledger)
                return {"status": "bound", "asset_ref": reference, "asset": existing.model_dump(mode="json")}
            item = existing
    if item is None:
        asset_id = f"lib-{uuid.uuid4().hex[:12]}"
        suffix = Path(media_part).suffix.lower()
        stored_path = f"assets/library/{asset_id}{suffix}"
        target = root / stored_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        item = LibraryMedia(
            id=asset_id, slide_id=slide_id, purpose=purpose.strip(),
            selection_note=selection_note.strip(), task_reuse_authorized=True,
            rights_handling_note=rights_handling_note.strip(), stored_path=stored_path,
            media_sha256=media_sha, size_bytes=len(payload), media_type=_MEDIA_TYPES[suffix],
            source_path=source.relative_to((WORKSPACE_ROOT / "模板库").resolve()).as_posix(),
            requested_source_path=catalog_row.get("_requested_source_path"), source_sha256=source_sha,
            source_slide=source_slide, shape_id=shape_id, relationship_id=relationship_id,
            media_part=media_part, rights=str(catalog_row.get("media_rights") or "unverified"),
        )
        ledger.assets.append(item)
    reference = f"library:{item.id}"
    if reference not in slide.asset_refs:
        slide.asset_refs.append(reference)
    ledger.updated_at = utc_now()
    write_json(ledger_path, ledger)
    write_json(plan_path, plan)

    reason = "library media changed the current deck plan"
    job.approvals.update({"prototype": False, "final": False})
    job.approval_hashes.pop("prototype", None)
    job.approval_hashes.pop("final", None)
    was_blocked = job.state == JobState.BLOCKED
    state_to_invalidate = job.resume_state if was_blocked and job.resume_state is not None else job.state
    invalidated_state = _invalidate_rendered_artifacts(root, state_to_invalidate, reason=reason)
    if was_blocked:
        job.resume_state = invalidated_state
    else:
        job.state = invalidated_state
    save_job(job)
    return {"status": "bound", "asset_ref": reference, "asset": item.model_dump(mode="json")}
