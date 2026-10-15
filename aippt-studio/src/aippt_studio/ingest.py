from __future__ import annotations

import hashlib
import html
import ipaddress
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import urllib.request
from urllib.parse import urlparse
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .models import EvidenceLedger, EvidenceSource, JobState, LockedFact, SourceRecord
from .storage import job_dir, load_job, locked_job, read_model, save_job, write_json


TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".html", ".htm"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".svg"}


def _decode_text_file(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    candidates = ["utf-8-sig"] if data.startswith(b"\xef\xbb\xbf") else ["utf-8"]
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        candidates.insert(0, "utf-16")
    candidates.append("gb18030")
    for encoding in candidates:
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError(f"unsupported text encoding: {path.name}")


def _size_limit(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _validate_remote_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"unsupported or unsafe URL: {url}")
    if os.environ.get("AIPPT_ALLOW_PRIVATE_URLS") == "1":
        return
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))}
    except OSError as error:
        raise ValueError(f"cannot resolve URL host: {parsed.hostname}") from error
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast:
            raise ValueError(f"private or reserved URL targets require AIPPT_ALLOW_PRIVATE_URLS=1: {parsed.hostname}")


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        _validate_remote_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix == ".pptx":
        return "powerpoint"
    if suffix == ".docx":
        return "word"
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".html", ".htm"}:
        return "html"
    if suffix in TEXT_SUFFIXES:
        return "text"
    return mimetypes.guess_type(path.name)[0] or "binary"


def _unique_destination(directory: Path, name: str, reserved: set[Path] | None = None) -> Path:
    clean = re.sub(r"[^\w.()\-\u4e00-\u9fff]+", "_", name, flags=re.UNICODE)
    target = directory / clean
    counter = 2
    while target.exists() or (reserved is not None and target in reserved):
        target = directory / f"{Path(clean).stem}_{counter}{Path(clean).suffix}"
        counter += 1
    return target


def _download_url(url: str, directory: Path) -> Path:
    _validate_remote_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "AI-PPT-Studio/0.1"})
    opener = urllib.request.build_opener(_SafeRedirectHandler())
    max_bytes = _size_limit("AIPPT_MAX_SOURCE_BYTES", 50 * 1024 * 1024)
    with opener.open(request, timeout=45) as response:
        _validate_remote_url(response.geturl())
        declared_size = response.headers.get("Content-Length")
        if declared_size and int(declared_size) > max_bytes:
            raise ValueError(f"URL source exceeds {max_bytes} bytes: {url}")
        content_type = response.headers.get_content_type()
        suffix = mimetypes.guess_extension(content_type) or ".html"
        name = Path(urlparse(url).path).name or f"web-source{suffix}"
        if not Path(name).suffix:
            name += suffix
        target = _unique_destination(directory, name)
        total = 0
        try:
            with target.open("wb") as stream:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f"URL source exceeds {max_bytes} bytes: {url}")
                    stream.write(chunk)
        except Exception:
            target.unlink(missing_ok=True)
            raise
    return target


def _xml_text_from_zip(path: Path, prefixes: tuple[str, ...]) -> str:
    chunks: list[str] = []
    with zipfile.ZipFile(path) as archive:
        infos = sorted(
            (info for info in archive.infolist() if info.filename.startswith(prefixes) and info.filename.endswith(".xml")),
            key=lambda info: info.filename,
        )
        expanded_limit = _size_limit("AIPPT_MAX_EXPANDED_BYTES", 200 * 1024 * 1024)
        if sum(info.file_size for info in infos) > expanded_limit:
            raise ValueError(f"expanded Office XML exceeds {expanded_limit} bytes: {path.name}")
        for info in infos:
            try:
                root = ElementTree.fromstring(archive.read(info))
            except ElementTree.ParseError:
                continue
            values = [node.text.strip() for node in root.iter() if node.tag.rsplit("}", 1)[-1] in {"t", "instrText"} and node.text and node.text.strip()]
            if values:
                chunks.append(" ".join(values))
    return "\n\n".join(chunks)


def _validate_office_archive(path: Path) -> None:
    expanded_limit = _size_limit("AIPPT_MAX_EXPANDED_BYTES", 200 * 1024 * 1024)
    try:
        with zipfile.ZipFile(path) as archive:
            expanded = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as error:
        raise ValueError(f"invalid Office archive: {path.name}") from error
    if expanded > expanded_limit:
        raise ValueError(f"expanded Office package exceeds {expanded_limit} bytes: {path.name}")


def _markitdown(path: Path) -> str | None:
    # Use the package in the selected Studio Python. PATH and sibling launchers
    # may belong to a migrated Windows environment or another Python install.
    command = [sys.executable, "-m", "markitdown", str(path)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"MarkItDown timed out after 180s for {path.name}") from error
    except OSError as error:
        raise ValueError(f"MarkItDown could not start for {path.name}: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown conversion error").strip()
        raise ValueError(f"MarkItDown failed for {path.name}: {detail}")
    text = completed.stdout.strip()
    if len(text.encode("utf-8")) > _size_limit("AIPPT_MAX_EXPANDED_BYTES", 200 * 1024 * 1024):
        raise ValueError(f"MarkItDown output is too large for {path.name}")
    return text or None


def extract_text(path: Path, kind: str) -> str:
    if kind in {"text", "html"}:
        raw, _ = _decode_text_file(path)
        if kind == "html":
            raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.IGNORECASE | re.DOTALL)
            raw = re.sub(r"<[^>]+>", "\n", raw)
            raw = html.unescape(raw)
        return raw.strip()
    if kind == "image":
        return ""
    if kind not in {"powerpoint", "word", "pdf"}:
        return ""
    if kind in {"powerpoint", "word"}:
        _validate_office_archive(path)
    converted = _markitdown(path)
    if converted:
        return converted
    if kind == "powerpoint":
        return _xml_text_from_zip(path, ("ppt/slides/", "ppt/notesSlides/"))
    if kind == "word":
        return _xml_text_from_zip(path, ("word/",))
    if kind == "pdf":
        raise ValueError(f"PDF text extraction is unavailable for {path.name}; install MarkItDown PDF support or use the rebuild route")
    return ""


def _image_metadata(path: Path) -> dict[str, int | str]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            return {"width": image.width, "height": image.height, "format": image.format or path.suffix.lstrip(".")}
    except Exception:
        return {}


def _fact_candidates(text: str, source_id: str) -> list[LockedFact]:
    facts: list[LockedFact] = []
    for index, line in enumerate(text.splitlines(), start=1):
        candidate = re.sub(r"\s+", " ", line).strip(" -#\t")
        if 8 <= len(candidate) <= 180 and re.search(r"\d|[%％¥￥$]|同比|环比|增长|下降|结论", candidate):
            facts.append(LockedFact(text=candidate, source_id=source_id, locator=f"line:{index}"))
        if len(facts) >= 12:
            break
    return facts


@locked_job
def ingest_sources(
    job_id: str,
    inputs: list[str],
    *,
    inline_texts: list[tuple[str, str]] | None = None,
) -> list[SourceRecord]:
    job = load_job(job_id)
    accepting_intake = job.state == JobState.INTAKE or (
        job.state == JobState.BLOCKED and job.resume_state == JobState.INTAKE
    )
    if not accepting_intake:
        raise ValueError(f"sources can only be ingested during intake; job is {job.state.value}")
    root = job_dir(job_id)
    ledger_path = root / "evidence-ledger.json"
    ledger = read_model(ledger_path, EvidenceLedger) if ledger_path.is_file() else EvidenceLedger()
    original_job = job.model_copy(deep=True)
    original_ledger = ledger.model_copy(deep=True)
    records: list[SourceRecord] = []
    staging = root / f".ingest-{uuid.uuid4().hex[:8]}"
    staged_files: list[tuple[Path, Path]] = []
    committed: list[Path] = []
    reserved_sources: set[Path] = set()
    try:
        (staging / "sources").mkdir(parents=True)
        (staging / "normalized" / "sources").mkdir(parents=True)
        entries: list[tuple[str, str | tuple[str, str]]] = [("source", item) for item in inputs]
        entries.extend(("inline", item) for item in (inline_texts or []))
        if not entries:
            raise ValueError("at least one file, URL, or inline text source is required")
        for entry_kind, payload in entries:
            if entry_kind == "inline":
                name, text_value = payload  # type: ignore[misc]
                encoded = text_value.encode("utf-8")
                max_bytes = _size_limit("AIPPT_MAX_SOURCE_BYTES", 50 * 1024 * 1024)
                if len(encoded) > max_bytes:
                    raise ValueError(f"inline text exceeds {max_bytes} bytes")
                staged_stored = _unique_destination(staging / "sources", name or "chat.md")
                staged_stored.write_bytes(encoded)
                original = "chat:inline-text"
            else:
                item = str(payload)
                if item.startswith(("http://", "https://")):
                    staged_stored = _download_url(item, staging / "sources")
                    original = item
                else:
                    source = Path(item).expanduser().resolve()
                    if not source.is_file():
                        raise FileNotFoundError(f"source not found: {item}")
                    max_bytes = _size_limit("AIPPT_MAX_SOURCE_BYTES", 50 * 1024 * 1024)
                    if source.stat().st_size > max_bytes:
                        raise ValueError(f"source exceeds {max_bytes} bytes: {source.name}")
                    staged_stored = _unique_destination(staging / "sources", source.name)
                    shutil.copy2(source, staged_stored)
                    original = str(source)

            final_stored = _unique_destination(root / "sources", staged_stored.name, reserved_sources)
            reserved_sources.add(final_stored)
            kind = classify(staged_stored)
            source_id = f"src-{uuid.uuid4().hex[:10]}"
            text = extract_text(staged_stored, kind)
            fact_text = text
            semantics_metadata = {}
            if kind == "powerpoint":
                from .source_semantics import analyze_source_semantics, semantic_text
                semantics = analyze_source_semantics(staged_stored)
                staged_semantics = staging / "normalized" / "sources" / f"{source_id}.semantics.json"
                write_json(staged_semantics, semantics)
                final_semantics = root / "normalized" / "sources" / staged_semantics.name
                staged_files.append((staged_semantics, final_semantics))
                semantics_metadata = {"source_semantics_path": final_semantics.relative_to(root).as_posix(),
                                      "source_semantics_sha256": sha256(staged_semantics),
                                      "source_semantics_status": semantics["status"]}
                text += semantic_text(semantics)
            normalized_path: str | None = None
            if text:
                staged_normalized = staging / "normalized" / f"{source_id}.md"
                staged_normalized.write_text(text, encoding="utf-8")
                final_normalized = root / "normalized" / f"{source_id}.md"
                normalized_path = final_normalized.relative_to(root).as_posix()
                staged_files.append((staged_normalized, final_normalized))
            metadata = _image_metadata(staged_stored) if kind == "image" else {"extraction": "text" if text else "no_text"}
            metadata.update(semantics_metadata)
            if kind in {"text", "html"}:
                metadata["encoding"] = _decode_text_file(staged_stored)[1]
            record = SourceRecord(
                id=source_id,
                original=original,
                stored_path=final_stored.relative_to(root).as_posix(),
                normalized_path=normalized_path,
                kind=kind,
                sha256=sha256(staged_stored),
                size_bytes=staged_stored.stat().st_size,
                metadata=metadata,
            )
            staged_record = staging / "normalized" / "sources" / f"{source_id}.json"
            write_json(staged_record, record)
            staged_files.extend(
                (
                    (staged_stored, final_stored),
                    (staged_record, root / "normalized" / "sources" / f"{source_id}.json"),
                )
            )
            records.append(record)
            job.source_ids.append(source_id)
            ledger.sources.append(
                EvidenceSource(
                    source_id=source_id,
                    sha256=record.sha256,
                    kind=kind,
                    original=original,
                    normalized_path=normalized_path,
                )
            )
            # Cache/structure supplements are searchable source evidence, not
            # additional automatically locked business facts.
            ledger.locked_facts.extend(_fact_candidates(fact_text, source_id))

        for staged, destination in staged_files:
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged.replace(destination)
            committed.append(destination)
        save_job(job)
        write_json(ledger_path, ledger)
        return records
    except Exception:
        for destination in reversed(committed):
            destination.unlink(missing_ok=True)
        save_job(original_job)
        write_json(ledger_path, original_ledger)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
