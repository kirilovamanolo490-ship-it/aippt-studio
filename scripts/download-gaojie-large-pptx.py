#!/usr/bin/env python3
"""Resume three observed large Gaojie PPTX resources with validated HTTP ranges."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.parse

import requests

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "aippt-studio/.maintenance/gaojie-downloads"
INVENTORY = ROOT / "模板库/图表与视觉素材/版式与排版参考/高阶文化-待下载清单/资源清单.jsonl"
DETAILS = STATE / "详情链接.jsonl"
RECEIPTS = STATE / "下载回执.jsonl"
DEST_DIR = STATE / "files"
CHUNK_ROOT = STATE / "large-files"
SITE = "www.gaojiewenhua.cn"
CDN = "wstx.web.vleader.net.cn"
CHUNK_SIZE = 8 * 1024 * 1024
WORKERS = max(1, min(8, int(os.environ.get("GAOJIE_LARGE_WORKERS", "4"))))
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36"
PROXY = os.environ.get("GAOJIE_EXISTING_SYSTEM_PROXY", "")
EXPECTED = {
    "7816": {"bytes": 325696036, "etag": '"80385654acedb1:0"'},
    "7817": {"bytes": 213245371, "etag": '"0bb4f274acedb1:0"'},
    "7818": {"bytes": 201420712, "etag": '"06796d16093db1:0"'},
}

thread_local = threading.local()
stop_event = threading.Event()
metadata_locks = {item_id: threading.Lock() for item_id in EXPECTED}


class RangeDownloadError(RuntimeError):
    pass


def read_latest(path: Path) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            row = json.loads(line)
            latest[str(row["id"])] = row
    return latest


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def site_detail_url_ok(url: str) -> bool:
    try:
        u = urllib.parse.urlsplit(url)
        return u.scheme == "http" and u.hostname == SITE and u.port in (None, 80) and u.path == "/products_show.aspx"
    except ValueError:
        return False


def resource_url_ok(url: str) -> bool:
    try:
        u = urllib.parse.urlsplit(url)
        if u.username or u.password or u.query or u.fragment:
            return False
        if u.hostname == SITE:
            return u.scheme == "http" and u.port in (None, 80) and u.path.startswith("/upload/") and u.path.lower().endswith(".pptx")
        if u.hostname == CDN:
            return u.scheme == "https" and u.port in (None, 443) and u.path.lower().endswith(".pptx")
        return False
    except ValueError:
        return False


def session_for_thread() -> requests.Session:
    session = getattr(thread_local, "session", None)
    if session is None:
        session = requests.Session()
        session.trust_env = True
        session.auth = lambda request: request  # Do not consult .netrc or attach account credentials.
        if PROXY:
            session.proxies.update({"http": PROXY, "https": PROXY})
        session.headers.update({"User-Agent": UA})
        thread_local.session = session
    return session


def chunk_digest(path: Path) -> str:
    return sha256(path)


def persist_chunk(job: dict, body: bytes) -> None:
    item_id = job["id"]
    directory: Path = job["directory"]
    path = job["chunk_path"]
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    chunk_record = {
        "index": job["index"],
        "start": job["start"],
        "end": job["end"],
        "bytes": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }
    with metadata_locks[item_id]:
        meta_path = directory / "metadata.json"
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        completed = metadata.setdefault("completed_chunks", {})
        completed[str(job["index"])] = chunk_record
        atomic_json(meta_path, metadata)
        os.replace(temp, path)
        done_bytes = sum(int(record.get("bytes", 0)) for record in completed.values())
        print(json.dumps({"id": item_id, "chunk": job["index"], "completed_chunks": len(completed), "total_chunks": job["total_chunks"], "completed_bytes": done_bytes, "total_bytes": job["total"]}, ensure_ascii=False), flush=True)


def fetch_chunk(job: dict) -> dict:
    if stop_event.is_set():
        return {"id": job["id"], "index": job["index"], "status": "cancelled_after_other_error"}
    url = job["url"]
    if not resource_url_ok(url):
        stop_event.set()
        raise RangeDownloadError(f"resource_scope_rejected:{url}")
    expected_length = job["end"] - job["start"] + 1
    headers = {
        "Referer": job["detail_url"],
        "Accept-Encoding": "identity",
        "Range": f"bytes={job['start']}-{job['end']}",
        "If-Range": job["etag"],
    }
    last_error = None
    for attempt in range(3):
        if stop_event.is_set():
            return {"id": job["id"], "index": job["index"], "status": "cancelled_after_other_error"}
        response = None
        session = session_for_thread()
        session.cookies.clear()
        try:
            response = session.get(url, headers=headers, timeout=(15, 90), allow_redirects=False, stream=True)
            if response.status_code in (403, 429):
                raise RangeDownloadError(f"http_{response.status_code}:stop_without_retry")
            if 500 <= response.status_code < 600:
                last_error = f"http_{response.status_code}"
                if attempt < 2:
                    time.sleep(1 + attempt)
                    continue
                raise RangeDownloadError(f"transient_retries_exhausted:{last_error}")
            if response.status_code != 206:
                raise RangeDownloadError(f"range_status_mismatch:{response.status_code}")
            if not resource_url_ok(response.url) or urllib.parse.urlsplit(response.url).hostname != urllib.parse.urlsplit(url).hostname:
                raise RangeDownloadError("response_origin_mismatch")
            if response.headers.get("ETag", "") != job["etag"]:
                raise RangeDownloadError("etag_mismatch")
            expected_range = f"bytes {job['start']}-{job['end']}/{job['total']}"
            if response.headers.get("Content-Range", "") != expected_range:
                raise RangeDownloadError("content_range_mismatch")
            try:
                content_length = int(response.headers.get("Content-Length", ""))
            except ValueError:
                raise RangeDownloadError("missing_or_invalid_content_length")
            if content_length != expected_length:
                raise RangeDownloadError("content_length_mismatch")
            if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
                raise RangeDownloadError("unexpected_content_encoding")
            if response.headers.get("Content-Type", "").lower().startswith("text/html"):
                raise RangeDownloadError("html_content_type")
            body = bytearray()
            for block in response.iter_content(chunk_size=1024 * 1024):
                if stop_event.is_set():
                    raise RangeDownloadError("cancelled_after_other_range_error")
                if not block:
                    continue
                if not body and block.lstrip().lower().startswith((b"<!doctype html", b"<html")):
                    raise RangeDownloadError("html_response_instead_of_range_data")
                body.extend(block)
                if len(body) > expected_length:
                    raise RangeDownloadError("range_body_too_long")
            if len(body) != expected_length:
                raise RangeDownloadError(f"range_body_length_mismatch:{len(body)}:{expected_length}")
            persist_chunk(job, bytes(body))
            return {"id": job["id"], "index": job["index"], "status": "downloaded", "bytes": len(body)}
        except RangeDownloadError:
            stop_event.set()
            raise
        except requests.RequestException as exc:
            if stop_event.is_set():
                raise RangeDownloadError("cancelled_after_other_range_error") from exc
            last_error = f"{type(exc).__name__}:{exc}"
            if attempt == 2:
                stop_event.set()
                raise RangeDownloadError(f"network_retries_exhausted:{last_error}") from exc
            time.sleep(1 + attempt)
        finally:
            if response is not None:
                response.close()
            session.cookies.clear()
    stop_event.set()
    raise RangeDownloadError(f"network_retries_exhausted:{last_error}")


def prepare_item(item_id: str, detail: dict, inventory_item: dict) -> tuple[dict, list[dict]]:
    specification = EXPECTED[item_id]
    url = str(detail.get("url", ""))
    detail_url = str(detail.get("detail_url", ""))
    if detail.get("status") != "resolved" or not resource_url_ok(url):
        raise RangeDownloadError(f"id_{item_id}_not_resolved_to_allowed_pptx_url")
    if not site_detail_url_ok(detail_url):
        raise RangeDownloadError(f"id_{item_id}_invalid_detail_referer")
    if str(detail.get("category_id", "")) != str(inventory_item.get("category_id", "")) or str(inventory_item.get("category_id", "")) == "57":
        raise RangeDownloadError(f"id_{item_id}_category_mismatch_or_excluded")
    directory = CHUNK_ROOT / item_id
    directory.mkdir(parents=True, exist_ok=True)
    metadata_path = directory / "metadata.json"
    binding = {"schema_version": 1, "id": item_id, "url": url, "etag": specification["etag"], "size": specification["bytes"], "chunk_size": CHUNK_SIZE}
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        for key, value in binding.items():
            if metadata.get(key) != value:
                raise RangeDownloadError(f"id_{item_id}_chunk_cache_binding_mismatch:{key}")
    else:
        existing_chunks = list(directory.glob("*.bin"))
        if existing_chunks:
            raise RangeDownloadError(f"id_{item_id}_unbound_chunk_cache_requires_review")
        metadata = {**binding, "completed_chunks": {}}
        atomic_json(metadata_path, metadata)
    total = specification["bytes"]
    completed = metadata.setdefault("completed_chunks", {})
    if not isinstance(completed, dict):
        raise RangeDownloadError(f"id_{item_id}_invalid_completed_chunks_metadata")
    count = (total + CHUNK_SIZE - 1) // CHUNK_SIZE
    allowed_indexes = {str(index) for index in range(count)}
    if set(completed) - allowed_indexes:
        raise RangeDownloadError(f"id_{item_id}_unexpected_metadata_chunks:{sorted(set(completed) - allowed_indexes)}")
    jobs = []
    chunk_files = {p.name for p in directory.glob("*.bin")}
    expected_names = {f"{index:06d}.bin" for index in range(count)}
    if chunk_files - expected_names:
        raise RangeDownloadError(f"id_{item_id}_unexpected_chunk_files:{sorted(chunk_files - expected_names)}")
    for index in range(count):
        start = index * CHUNK_SIZE
        end = min(total - 1, start + CHUNK_SIZE - 1)
        chunk_path = directory / f"{index:06d}.bin"
        length = end - start + 1
        record = completed.get(str(index))
        if chunk_path.exists():
            if not isinstance(record, dict):
                raise RangeDownloadError(f"id_{item_id}_unindexed_chunk:{index}")
            if (record.get("index"), record.get("start"), record.get("end")) != (index, start, end):
                raise RangeDownloadError(f"id_{item_id}_cached_chunk_range_mismatch:{index}")
            if chunk_path.stat().st_size != length or int(record.get("bytes", -1)) != length:
                raise RangeDownloadError(f"id_{item_id}_cached_chunk_size_mismatch:{index}")
            if chunk_digest(chunk_path) != record.get("sha256"):
                raise RangeDownloadError(f"id_{item_id}_cached_chunk_sha256_mismatch:{index}")
            continue
        if record is not None:
            completed.pop(str(index), None)
            atomic_json(metadata_path, metadata)
        jobs.append({"id": item_id, "url": url, "detail_url": detail_url, "etag": specification["etag"], "total": total, "total_chunks": count, "index": index, "start": start, "end": end, "directory": directory, "chunk_path": chunk_path})
    return {**binding, "detail_url": detail_url, "directory": directory, "chunk_count": count}, jobs


def latest_downloaded(receipts: dict[str, dict], item_id: str, destination: Path, expected_size: int) -> bool:
    row = receipts.get(item_id, {})
    if row.get("status") != "downloaded" or not destination.is_file():
        return False
    if str(Path(str(row.get("local_path", ""))).resolve()) != str(destination.resolve()):
        return False
    if destination.stat().st_size != expected_size or int(row.get("bytes", 0) or 0) != expected_size:
        raise RangeDownloadError(f"id_{item_id}_existing_download_receipt_size_mismatch")
    if sha256(destination) != str(row.get("sha256", "")):
        raise RangeDownloadError(f"id_{item_id}_existing_download_receipt_sha256_mismatch")
    return True


def merge_and_record(item_id: str, item: dict, binding: dict, details: dict, receipts: dict[str, dict]) -> dict:
    total = binding["size"]
    directory: Path = binding["directory"]
    metadata_path = directory / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    for key in ("id", "url", "etag", "size", "chunk_size"):
        if metadata.get(key) != binding.get(key):
            raise RangeDownloadError(f"id_{item_id}_cache_binding_changed_before_merge:{key}")
    destination = DEST_DIR / f"{item_id}.pptx"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(f"{item_id}.range-build.tmp")
    digest = hashlib.sha256()
    written = 0
    with temp.open("wb") as output:
        for index in range(binding["chunk_count"]):
            chunk_path = directory / f"{index:06d}.bin"
            record = metadata.get("completed_chunks", {}).get(str(index), {})
            expected_size = min(CHUNK_SIZE, total - index * CHUNK_SIZE)
            if not chunk_path.is_file() or chunk_path.stat().st_size != expected_size or chunk_digest(chunk_path) != record.get("sha256"):
                raise RangeDownloadError(f"id_{item_id}_chunk_failed_premerge_validation:{index}")
            with chunk_path.open("rb") as chunk:
                for block in iter(lambda: chunk.read(1024 * 1024), b""):
                    output.write(block)
                    digest.update(block)
                    written += len(block)
        output.flush()
        os.fsync(output.fileno())
    if written != total or temp.stat().st_size != total:
        raise RangeDownloadError(f"id_{item_id}_merged_size_mismatch:{written}:{total}")
    final_hash = digest.hexdigest()
    if sha256(temp) != final_hash:
        raise RangeDownloadError(f"id_{item_id}_merged_sha256_mismatch")
    if destination.exists():
        if destination.stat().st_size != total or sha256(destination) != final_hash:
            raise RangeDownloadError(f"id_{item_id}_destination_exists_with_different_content")
        temp.unlink(missing_ok=True)
    else:
        os.replace(temp, destination)
    receipt = {
        "id": item_id,
        "category_id": str(item["category_id"]),
        "url": binding["url"],
        "detail_url": details[item_id]["detail_url"],
        "status": "downloaded",
        "download_url": binding["url"],
        "local_path": str(destination),
        "bytes": written,
        "content_length": total,
        "sha256": final_hash,
        "verification": "range_download_validated",
        "range_download": {"etag": binding["etag"], "chunk_size": CHUNK_SIZE, "chunk_count": binding["chunk_count"], "verified_chunks": binding["chunk_count"], "chunk_hashes": metadata.get("completed_chunks", {})},
    }
    with RECEIPTS.open("a", encoding="utf-8") as log:
        log.write(json.dumps(receipt, ensure_ascii=False) + "\n")
        log.flush()
        os.fsync(log.fileno())
    receipts[item_id] = receipt
    return receipt


def main() -> int:
    inventory = {str(row["id"]): row for row in (json.loads(line) for line in INVENTORY.read_text(encoding="utf-8-sig").splitlines() if line.strip()) if str(row.get("category_id")) != "57"}
    details = read_latest(DETAILS)
    receipts = read_latest(RECEIPTS)
    bindings: dict[str, dict] = {}
    jobs: list[dict] = []
    for item_id, specification in EXPECTED.items():
        if item_id not in inventory or item_id not in details:
            raise RangeDownloadError(f"id_{item_id}_missing_from_inventory_or_detail_receipt")
        destination = DEST_DIR / f"{item_id}.pptx"
        if latest_downloaded(receipts, item_id, destination, specification["bytes"]):
            print(json.dumps({"id": item_id, "status": "already_downloaded"}, ensure_ascii=False), flush=True)
            continue
        binding, item_jobs = prepare_item(item_id, details[item_id], inventory[item_id])
        bindings[item_id] = binding
        jobs.extend(item_jobs)
    errors = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(fetch_chunk, job) for job in jobs]
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as exc:
                stop_event.set()
                errors.append(f"{type(exc).__name__}:{exc}")
                print(json.dumps({"status": "stopping_after_range_error", "reason": errors[-1], "errors_seen": len(errors)}, ensure_ascii=False), flush=True)
    if errors:
        raise RangeDownloadError("; ".join(errors[:4]))
    detail_by_id = details
    for item_id, binding in bindings.items():
        receipt = merge_and_record(item_id, inventory[item_id], binding, detail_by_id, receipts)
        print(json.dumps({"id": item_id, "status": "downloaded", "bytes": receipt["bytes"], "sha256": receipt["sha256"]}, ensure_ascii=False), flush=True)
    print(json.dumps({"status": "complete", "ids": sorted(EXPECTED), "chunks_requested": len(jobs), "workers": WORKERS}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RangeDownloadError as exc:
        stop_event.set()
        print(json.dumps({"status": "stopped", "reason": str(exc)}, ensure_ascii=False), flush=True)
        raise SystemExit(2)
