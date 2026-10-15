from __future__ import annotations

import re
import json
import shutil
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from urllib.parse import parse_qsl
from xml.etree import ElementTree

from PIL import Image
from pydantic import BaseModel, Field, field_validator

from ..delivery_artifacts import supersede_delivery_artifacts
from ..ingest import _download_url, classify, sha256
from ..models import DeckPlan, JobBrief, JobState, utc_now
from ..storage import job_dir, load_job, locked_job, read_model, save_job, write_json
from .licenses import license_is_automatic


AssetKind = Literal["icon", "image", "illustration", "logo", "map", "texture"]
AssetNeedStatus = Literal["pending", "acquired", "skipped", "blocked"]


class AssetAcquisitionError(RuntimeError):
    pass


class AssetNeed(BaseModel):
    id: str
    slide_id: str
    kind: AssetKind
    query: str
    purpose: str
    required: bool = True
    status: AssetNeedStatus = "pending"
    acquired_asset_id: str | None = None
    resolution_note: str = ""
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("id", "slide_id")
    @classmethod
    def safe_identifier(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("asset and slide ids must contain only letters, numbers, underscores, or hyphens")
        return value


class AssetPlan(BaseModel):
    schema_version: int = 1
    job_id: str
    needs: list[AssetNeed] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)


class AcquiredAsset(BaseModel):
    id: str
    need_id: str
    slide_id: str
    kind: AssetKind
    query: str
    purpose: str
    source_url: str
    download_url: str
    license: str
    attribution: str = ""
    selection_note: str = ""
    stored_path: str
    sha256: str
    size_bytes: int
    media_kind: str
    acquired_at: str = Field(default_factory=utc_now)

    @field_validator("sha256")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("asset sha256 must contain 64 hexadecimal characters")
        return value.lower()

    @field_validator("stored_path")
    @classmethod
    def safe_stored_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized.split("/", 1)[0] or ".." in normalized.split("/"):
            raise ValueError("asset stored_path must remain job-relative")
        return normalized


class AssetLedger(BaseModel):
    schema_version: int = 1
    job_id: str
    assets: list[AcquiredAsset] = Field(default_factory=list)
    updated_at: str = Field(default_factory=utc_now)


def _paths(job_id: str) -> tuple[Path, Path, Path]:
    root = job_dir(job_id)
    return root, root / "planning" / "asset-plan.json", root / "assets" / "asset-ledger.json"


def _read_or_create(job_id: str) -> tuple[Path, AssetPlan, AssetLedger]:
    root, plan_path, ledger_path = _paths(job_id)
    plan = read_model(plan_path, AssetPlan) if plan_path.is_file() else AssetPlan(job_id=job_id)
    ledger = read_model(ledger_path, AssetLedger) if ledger_path.is_file() else AssetLedger(job_id=job_id)
    if not plan_path.is_file():
        write_json(plan_path, plan)
    if not ledger_path.is_file():
        write_json(ledger_path, ledger)
    return root, plan, ledger


def _validate_record_url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise AssetAcquisitionError(f"{label} must be a public HTTP or HTTPS URL")
    sensitive_keys = {"token", "key", "api_key", "apikey", "auth", "signature", "sig", "x-amz-credential", "x-amz-signature"}
    if any(key.lower() in sensitive_keys for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        raise AssetAcquisitionError(f"{label} must not contain credentials or signed access parameters")
    return value


def _validate_downloaded_asset(path: Path) -> str:
    media_kind = classify(path)
    if media_kind != "image":
        raise AssetAcquisitionError(f"downloaded asset is not a supported image: {path.name}")
    if path.suffix.lower() == ".svg":
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?i)<script|<foreignObject|\bon\w+\s*=|(?:href|src)\s*=\s*['\"]https?://", text):
            raise AssetAcquisitionError("SVG contains active or externally loaded content")
        try:
            root = ElementTree.fromstring(text)
        except ElementTree.ParseError as error:
            raise AssetAcquisitionError("downloaded SVG is invalid") from error
        if root.tag.rsplit("}", 1)[-1].lower() != "svg":
            raise AssetAcquisitionError("downloaded file is not an SVG document")
    else:
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as error:
            raise AssetAcquisitionError(f"downloaded image is invalid: {path.name}") from error
    return media_kind


def _invalidate_rendered_artifacts(
    root: Path,
    job_state: JobState,
    *,
    reason: str = "rendered deck inputs changed",
) -> JobState:
    """Discard derived renders and any delivery that used their old inputs."""
    supersede_delivery_artifacts(root, reason)
    if job_state == JobState.PROTOTYPE_REVIEW:
        for path in (
            root / "build" / "prototype.pptx",
            root / "build" / "prototype.html",
            root / "build" / "prototype-montage.png",
        ):
            path.unlink(missing_ok=True)
        preview_dir = root / "build" / "prototype-previews"
        if preview_dir.is_dir():
            shutil.rmtree(preview_dir)
        return job_state
    if job_state in {JobState.FULL_BUILD, JobState.FINAL_REVIEW}:
        for path in (
            root / "build" / "deck.pptx",
            root / "build" / "deck.html",
            root / "build" / "montage.png",
            root / "build" / "external-engine-manifest.json",
            root / "build" / "adapter-last-result.json",
            root / "build" / "native-adapter-result.json",
        ):
            path.unlink(missing_ok=True)
        preview_dir = root / "build" / "previews"
        if preview_dir.is_dir():
            shutil.rmtree(preview_dir)
        preview_dir.mkdir(parents=True, exist_ok=True)
        return JobState.FULL_BUILD
    return job_state


def pending_required_assets(root: Path) -> list[AssetNeed]:
    path = root / "planning" / "asset-plan.json"
    if not path.is_file():
        return []
    plan = read_model(path, AssetPlan)
    return [need for need in plan.needs if need.required and need.status in {"pending", "blocked"}]


@locked_job
def add_asset_need(
    job_id: str,
    *,
    slide_id: str,
    kind: AssetKind,
    query: str,
    purpose: str,
    required: bool = True,
) -> AssetNeed:
    root, asset_plan, _ = _read_or_create(job_id)
    deck_plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    if slide_id not in {slide.id for slide in deck_plan.slides}:
        raise AssetAcquisitionError(f"unknown slide id: {slide_id}")
    if not query.strip() or not purpose.strip():
        raise AssetAcquisitionError("asset query and purpose are required")
    existing = next(
        (
            need
            for need in asset_plan.needs
            if need.slide_id == slide_id
            and need.kind == kind
            and need.query == query.strip()
            and need.purpose == purpose.strip()
            and need.status in {"pending", "blocked"}
        ),
        None,
    )
    if existing is not None:
        return existing
    need = AssetNeed(
        id=f"need-{uuid.uuid4().hex[:10]}",
        slide_id=slide_id,
        kind=kind,
        query=query.strip(),
        purpose=purpose.strip(),
        required=required,
    )
    asset_plan.needs.append(need)
    asset_plan.updated_at = utc_now()
    write_json(root / "planning" / "asset-plan.json", asset_plan)
    return need


@locked_job
def acquire_asset(
    job_id: str,
    *,
    need_id: str,
    download_url: str,
    source_url: str,
    license_id: str,
    attribution: str = "",
    selection_note: str = "",
) -> AcquiredAsset:
    job = load_job(job_id)
    if job.state == JobState.DELIVERED:
        raise AssetAcquisitionError("delivered jobs cannot acquire new assets")
    root, asset_plan, asset_ledger = _read_or_create(job_id)
    brief = read_model(root / "planning" / "brief.json", JobBrief)
    if not brief.cloud_allowed:
        raise AssetAcquisitionError("cloud and network asset acquisition is disabled for this job")
    if not license_is_automatic(license_id):
        raise AssetAcquisitionError("a known, reviewable license is required before asset download")
    _validate_record_url(download_url, "download_url")
    _validate_record_url(source_url, "source_url")
    if urlparse(download_url).scheme != "https":
        raise AssetAcquisitionError("download_url must use HTTPS")
    need = next((item for item in asset_plan.needs if item.id == need_id), None)
    if need is None:
        raise AssetAcquisitionError(f"unknown asset need: {need_id}")
    if need.status == "skipped":
        raise AssetAcquisitionError("skipped asset needs cannot be downloaded; create a new need if the design changed")
    if need.status == "acquired" and need.acquired_asset_id:
        existing = next((item for item in asset_ledger.assets if item.id == need.acquired_asset_id), None)
        if existing is not None and (root / existing.stored_path).is_file():
            return existing

    acquired_dir = root / "assets" / "acquired"
    acquired_dir.mkdir(parents=True, exist_ok=True)
    downloaded: Path | None = None
    try:
        downloaded = _download_url(download_url, acquired_dir)
        media_kind = _validate_downloaded_asset(downloaded)
        asset_id = f"asset-{uuid.uuid4().hex[:10]}"
        target = acquired_dir / f"{asset_id}{downloaded.suffix.lower()}"
        downloaded.replace(target)
    except Exception as error:
        if downloaded is not None:
            downloaded.unlink(missing_ok=True)
        need.status = "blocked"
        need.resolution_note = str(error)[:500]
        need.updated_at = utc_now()
        asset_plan.updated_at = utc_now()
        write_json(root / "planning" / "asset-plan.json", asset_plan)
        raise

    acquired = AcquiredAsset(
        id=asset_id,
        need_id=need.id,
        slide_id=need.slide_id,
        kind=need.kind,
        query=need.query,
        purpose=need.purpose,
        source_url=source_url,
        download_url=download_url,
        license=license_id.strip(),
        attribution=attribution.strip(),
        selection_note=selection_note.strip(),
        stored_path=target.relative_to(root).as_posix(),
        sha256=sha256(target),
        size_bytes=target.stat().st_size,
        media_kind=media_kind,
    )
    asset_ledger.assets.append(acquired)
    asset_ledger.updated_at = utc_now()
    write_json(root / "assets" / "asset-ledger.json", asset_ledger)

    need.status = "acquired"
    need.acquired_asset_id = acquired.id
    need.resolution_note = selection_note.strip()
    need.updated_at = utc_now()
    asset_plan.updated_at = utc_now()
    write_json(root / "planning" / "asset-plan.json", asset_plan)

    deck_plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    slide = next(item for item in deck_plan.slides if item.id == need.slide_id)
    reference = f"acquired:{acquired.id}"
    if reference not in slide.asset_refs:
        slide.asset_refs.append(reference)
        write_json(root / "planning" / "deck-plan.json", deck_plan)

    job.approvals.update({"prototype": False, "final": False})
    job.approval_hashes.pop("prototype", None)
    job.approval_hashes.pop("final", None)
    job.state = _invalidate_rendered_artifacts(
        root,
        job.state,
        reason="acquired asset changed the current deck plan",
    )
    save_job(job)
    return acquired


@locked_job
def skip_asset_need(job_id: str, *, need_id: str, reason: str) -> AssetNeed:
    root, asset_plan, _ = _read_or_create(job_id)
    need = next((item for item in asset_plan.needs if item.id == need_id), None)
    if need is None:
        raise AssetAcquisitionError(f"unknown asset need: {need_id}")
    if need.status == "acquired":
        raise AssetAcquisitionError("acquired asset needs cannot be skipped")
    if not reason.strip():
        raise AssetAcquisitionError("a reason is required when skipping an asset need")
    need.status = "skipped"
    need.resolution_note = reason.strip()
    need.updated_at = utc_now()
    asset_plan.updated_at = utc_now()
    write_json(root / "planning" / "asset-plan.json", asset_plan)
    return need


def asset_status(job_id: str) -> dict[str, object]:
    load_job(job_id)
    root, plan_path, ledger_path = _paths(job_id)
    asset_plan = read_model(plan_path, AssetPlan) if plan_path.is_file() else AssetPlan(job_id=job_id)
    asset_ledger = read_model(ledger_path, AssetLedger) if ledger_path.is_file() else AssetLedger(job_id=job_id)
    pending = [need for need in asset_plan.needs if need.status in {"pending", "blocked"}]
    return {
        "job_id": job_id,
        "asset_plan": str(plan_path) if plan_path.is_file() else None,
        "asset_ledger": str(ledger_path) if ledger_path.is_file() else None,
        "needs": [need.model_dump(mode="json") for need in asset_plan.needs],
        "assets": [asset.model_dump(mode="json") for asset in asset_ledger.assets],
        "pending": len(pending),
        "pending_required": len([need for need in pending if need.required]),
    }


@locked_job
def import_upstream_asset(job_id: str, *, need_id: str, receipt_path: Path) -> AcquiredAsset:
    """Bind a verified local tool artifact without network or hand-edited state."""
    from ..support import workspace_output
    job = load_job(job_id)
    if job.state == JobState.DELIVERED:
        raise AssetAcquisitionError("delivered jobs cannot import assets")
    receipt_path = workspace_output(receipt_path)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if receipt.get("status") != "succeeded" or receipt.get("operation") not in {"icon", "chart", "diagram"}:
        raise AssetAcquisitionError("a successful icon/chart/diagram upstream-call receipt is required")
    result = receipt["result"]
    source = (receipt_path.parent / result["artifact"]).resolve()
    if receipt_path.parent.resolve() not in source.parents:
        raise AssetAcquisitionError("artifact escapes its call directory")
    artifact = next((a for a in receipt.get("artifacts", []) if a["path"] == result["artifact"]), None)
    if artifact is None or not source.is_file() or artifact["sha256"] != sha256(source):
        raise AssetAcquisitionError("tool artifact hash does not match receipt")
    media_kind = _validate_downloaded_asset(source)
    license_info = result.get("asset_license", receipt.get("license", {}))
    license_id = license_info.get("spdx", "")
    if not license_is_automatic(license_id):
        raise AssetAcquisitionError("asset license is unresolved")
    root, asset_plan, ledger = _read_or_create(job_id)
    need = next((n for n in asset_plan.needs if n.id == need_id), None)
    if need is None or need.status not in {"pending", "blocked"}:
        raise AssetAcquisitionError("asset need must exist and be pending or blocked")
    deck = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    slide = next((s for s in deck.slides if s.id == need.slide_id), None)
    if slide is None:
        raise AssetAcquisitionError("asset need refers to an absent slide")
    asset_id = f"asset-{uuid.uuid4().hex[:10]}"
    target = root / "assets" / "acquired" / f"{asset_id}{source.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    proof = target.with_suffix(".upstream-receipt.json")
    shutil.copy2(receipt_path, proof)
    asset = AcquiredAsset(id=asset_id, need_id=need.id, slide_id=need.slide_id, kind=need.kind,
                          query=need.query, purpose=need.purpose, source_url=receipt["source_url"],
                          download_url=receipt["source_url"], license=license_id,
                          attribution=json.dumps(result.get("attribution", ""), ensure_ascii=False),
                          selection_note=f"Local upstream call {receipt['call_id']}; proof {proof.relative_to(root).as_posix()}",
                          stored_path=target.relative_to(root).as_posix(), sha256=sha256(target),
                          size_bytes=target.stat().st_size, media_kind=media_kind)
    ledger.assets.append(asset)
    ledger.updated_at = utc_now()
    need.status, need.acquired_asset_id = "acquired", asset.id
    need.updated_at = asset_plan.updated_at = utc_now()
    need.resolution_note = "Verified local upstream artifact bound to slide"
    slide.asset_refs.append(f"acquired:{asset.id}")
    write_json(root / "assets" / "asset-ledger.json", ledger)
    write_json(root / "planning" / "asset-plan.json", asset_plan)
    write_json(root / "planning" / "deck-plan.json", deck)
    job.approvals.update({"prototype": False, "final": False})
    job.approval_hashes.pop("prototype", None)
    job.approval_hashes.pop("final", None)
    job.state = _invalidate_rendered_artifacts(
        root,
        job.state,
        reason="verified imported asset changed the current deck plan",
    )
    save_job(job)
    return asset
