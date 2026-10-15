from __future__ import annotations

import json
import html
import hashlib
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..ingest import _SafeRedirectHandler, _validate_remote_url
from ..models import JobState
from ..models import JobBrief
from ..storage import job_dir, load_job, read_model
from ..storage import write_json
from .acquisition import AssetAcquisitionError, AssetNeed, AssetPlan, AcquiredAsset, acquire_asset
from .licenses import license_is_automatic


_API = "https://commons.wikimedia.org/w/api.php"
_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_VECTOR_KINDS = {"icon", "logo", "illustration"}
_ALLOWED_MIME = {"image/png", "image/jpeg", "image/webp", "image/gif", "image/svg+xml"}
_LICENSES = {
    "https://creativecommons.org/publicdomain/zero/1.0/": "CC0-1.0",
    "https://creativecommons.org/licenses/by/4.0/": "CC-BY-4.0",
    "https://creativecommons.org/licenses/by/3.0/": "CC-BY-3.0",
    "https://creativecommons.org/licenses/by/2.0/": "CC-BY-2.0",
    "https://creativecommons.org/licenses/by-sa/4.0/": "CC-BY-SA-4.0",
    "https://creativecommons.org/licenses/by-sa/3.0/": "CC-BY-SA-3.0",
    "https://creativecommons.org/licenses/by-sa/2.0/": "CC-BY-SA-2.0",
}


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value", "")
    plain = re.sub(r"<[^>]*>", " ", html.unescape(str(value or "")))
    return " ".join(plain.split())


def _need_for_search(job_id: str, need_id: str) -> AssetNeed:
    job = load_job(job_id)
    if job.state == JobState.DELIVERED:
        raise AssetAcquisitionError("delivered jobs cannot search for new assets")
    root = job_dir(job_id)
    brief_path = root / "planning" / "brief.json"
    if not brief_path.is_file():
        raise AssetAcquisitionError("job brief is required before Commons search")
    brief = read_model(brief_path, JobBrief)
    if not brief.cloud_allowed:
        raise AssetAcquisitionError("cloud and network asset acquisition is disabled for this job")
    path = root / "planning" / "asset-plan.json"
    if not path.is_file():
        raise AssetAcquisitionError("record an asset-need before Commons search")
    plan = read_model(path, AssetPlan)
    need = next((item for item in plan.needs if item.id == need_id), None)
    if need is None:
        raise AssetAcquisitionError(f"unknown asset need: {need_id}")
    if need.status in {"acquired", "skipped"}:
        raise AssetAcquisitionError(f"asset need is already {need.status}")
    return need


def _license_record(info: dict[str, Any]) -> tuple[str, str, str, bool]:
    metadata = info.get("extmetadata") or {}
    license_name = _text(metadata.get("LicenseShortName"))
    license_url = _text(metadata.get("LicenseUrl"))
    # Commons exposes license URIs as per-file metadata. Only map exact, known
    # Creative Commons URIs; other and public-domain assertions need review.
    normalized_url = license_url.replace("http://", "https://", 1)
    if normalized_url and not normalized_url.endswith("/"):
        normalized_url += "/"
    multi_licensed = bool(re.search(r"\b(or|and/or)\b|\|", license_name, flags=re.IGNORECASE))
    spdx = "NOASSERTION" if multi_licensed else _LICENSES.get(normalized_url, "NOASSERTION")
    artist = _text(metadata.get("Artist"))
    credit = _text(metadata.get("Credit"))
    attribution_field = _text(metadata.get("Attribution"))
    attribution_required = _text(metadata.get("AttributionRequired")).lower() == "true"
    creator = attribution_field or artist or credit
    parts = [creator]
    if license_name:
        parts.append(license_name)
    if attribution_required and not artist and not credit:
        parts.append("Creator attribution required; creator metadata unavailable")
    attribution = "; ".join(item for item in parts if item)
    return spdx, license_name, attribution, bool(creator)


def _ticket_bytes(ticket: dict[str, Any]) -> bytes:
    return json.dumps(ticket, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _ticket_path(job_id: str, search_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", search_id):
        raise AssetAcquisitionError("Commons search id must be a SHA-256 digest")
    return job_dir(job_id) / "planning" / "asset-searches" / f"commons-{search_id}.json"


def search_commons_assets(job_id: str, *, need_id: str, limit: int = 8) -> dict[str, Any]:
    """Return bounded, per-file Commons candidates; never downloads or selects one."""
    if not 1 <= limit <= 20:
        raise AssetAcquisitionError("limit must be between 1 and 20")
    need = _need_for_search(job_id, need_id)
    query = " ".join(need.query.split())
    # CirrusSearch documents one filetype per predicate: bitmap or drawing.
    # A pipe is not its documented union syntax. Keep the search tied to the
    # declared need, and independently inspect the returned MIME before use.
    filetype = "drawing" if need.kind in _VECTOR_KINDS else "bitmap"
    params = urllib.parse.urlencode(
        {
            "action": "query",
            "generator": "search",
            "gsrsearch": f"filetype:{filetype} {query}",
            "gsrnamespace": "6",
            "gsrlimit": str(limit),
            "prop": "imageinfo",
            "iiprop": "url|mime|size|extmetadata",
            "iiextmetadatafilter": "Artist|Attribution|AttributionRequired|Credit|LicenseShortName|LicenseUrl|UsageTerms|Copyrighted",
            "iiextmetadatalanguage": "en",
            "format": "json",
            "formatversion": "2",
        }
    )
    url = f"{_API}?{params}"
    _validate_remote_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "AI-PPT-Studio/1.2 (Wikimedia Commons asset discovery)"})
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    try:
        with opener.open(request, timeout=25) as response:
            final_url = response.geturl()
            _validate_remote_url(final_url)
            parsed = urlparse(final_url)
            if parsed.scheme != "https" or parsed.hostname != "commons.wikimedia.org":
                raise AssetAcquisitionError("Commons API redirected outside its official HTTPS host")
            payload = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_RESPONSE_BYTES:
                raise AssetAcquisitionError("Commons API response exceeded the 4 MiB limit")
        data = json.loads(payload.decode("utf-8"))
    except AssetAcquisitionError:
        raise
    except Exception as error:
        raise AssetAcquisitionError(f"Wikimedia Commons search failed: {error}") from error

    pages = (data.get("query") or {}).get("pages") or []
    candidates: list[dict[str, Any]] = []
    for page in pages:
        imageinfo = page.get("imageinfo") or []
        if not imageinfo:
            continue
        info = imageinfo[0]
        mime = str(info.get("mime") or "").lower()
        if mime not in _ALLOWED_MIME:
            continue
        direct_url = str(info.get("url") or "")
        source_url = str(info.get("descriptionurl") or page.get("canonicalurl") or "")
        if (urlparse(direct_url).scheme != "https" or urlparse(direct_url).hostname != "upload.wikimedia.org"
                or urlparse(source_url).scheme != "https" or urlparse(source_url).hostname != "commons.wikimedia.org"):
            continue
        spdx, license_name, attribution, creator_present = _license_record(info)
        candidates.append(
            {
                "title": str(page.get("title") or ""),
                "source_url": source_url,
                "download_url": direct_url,
                "license_spdx": spdx,
                "license_name": license_name,
                "license_url": _text((info.get("extmetadata") or {}).get("LicenseUrl")),
                "license_review_required": True,
                "license_review_note": "Check this file page for per-file terms and any multi-license choice before use; API metadata is a candidate aid.",
                "attribution": attribution,
                "creator_metadata_present": creator_present,
                "width": info.get("width"),
                "height": info.get("height"),
                "mime": mime,
            }
        )
    ticket = {
        "schema_version": 2,
        "job_id": job_id,
        "need_id": need.id,
        "slide_id": need.slide_id,
        "kind": need.kind,
        "purpose": need.purpose,
        "query": query,
        "provider": "Wikimedia Commons",
        "candidates": candidates,
    }
    search_id = hashlib.sha256(_ticket_bytes(ticket)).hexdigest()
    ticket_path = _ticket_path(job_id, search_id)
    if ticket_path.is_file():
        try:
            existing = json.loads(ticket_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise AssetAcquisitionError("saved Commons search ticket is unreadable") from error
        if existing != ticket:
            raise AssetAcquisitionError("saved Commons search ticket does not match current search")
    else:
        write_json(ticket_path, ticket)
    return {
        **ticket,
        "api_url": _API,
        "search_id": search_id,
        "ticket_path": ticket_path.relative_to(job_dir(job_id)).as_posix(),
        "candidate_count": len(candidates),
        "next_step": "Review the file description page, then use asset-pick-commons with this search id and the 1-based candidate number. The search never downloads or inserts a candidate.",
    }


def pick_commons_asset(job_id: str, *, need_id: str, search_id: str,
                       candidate_number: int, license_review_note: str,
                       selection_note: str) -> AcquiredAsset:
    """Fetch one reviewed candidate without retyping or trusting copied URLs.

    A content-addressed local search ticket binds the exact candidate metadata
    that the operator reviewed. This still requires a current file-page review;
    the API metadata alone cannot establish every per-file usage condition.
    """
    if not license_review_note.strip() or not selection_note.strip():
        raise AssetAcquisitionError("license-review-note and selection-note are required")
    need = _need_for_search(job_id, need_id)
    path = _ticket_path(job_id, search_id)
    try:
        ticket = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AssetAcquisitionError("saved Commons search ticket is missing or unreadable") from error
    if not isinstance(ticket, dict) or hashlib.sha256(_ticket_bytes(ticket)).hexdigest() != search_id:
        raise AssetAcquisitionError("saved Commons search ticket hash mismatch")
    if (ticket.get("schema_version") != 2 or ticket.get("provider") != "Wikimedia Commons"
            or ticket.get("job_id") != job_id or ticket.get("need_id") != need_id
            or ticket.get("slide_id") != need.slide_id or ticket.get("kind") != need.kind
            or ticket.get("purpose") != need.purpose
            or ticket.get("query") != " ".join(need.query.split())):
        raise AssetAcquisitionError("Commons search ticket no longer matches this asset need")
    candidates = ticket.get("candidates")
    if (not isinstance(candidates, list) or type(candidate_number) is not int
            or not 1 <= candidate_number <= len(candidates)):
        raise AssetAcquisitionError("candidate number is outside the saved Commons search")
    candidate = candidates[candidate_number - 1]
    if not isinstance(candidate, dict):
        raise AssetAcquisitionError("saved Commons candidate is invalid")
    source_url = candidate.get("source_url")
    download_url = candidate.get("download_url")
    license_id = candidate.get("license_spdx")
    if (not isinstance(source_url, str) or urlparse(source_url).scheme != "https"
            or urlparse(source_url).hostname != "commons.wikimedia.org"
            or not isinstance(download_url, str) or urlparse(download_url).scheme != "https"
            or urlparse(download_url).hostname != "upload.wikimedia.org"
            or not isinstance(license_id, str) or not license_is_automatic(license_id)):
        raise AssetAcquisitionError("candidate URL or license is not eligible for Commons acquisition")
    if license_id.startswith("CC-BY") and not candidate.get("creator_metadata_present"):
        raise AssetAcquisitionError("candidate lacks creator metadata; use asset-fetch after establishing attribution from the file page")
    return acquire_asset(
        job_id,
        need_id=need_id,
        download_url=download_url,
        source_url=source_url,
        license_id=license_id,
        attribution=str(candidate.get("attribution") or ""),
        selection_note=f"{selection_note.strip()} | file-page license review: {license_review_note.strip()}",
    )
