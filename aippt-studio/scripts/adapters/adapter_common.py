from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any


def upstream_version(directory: str) -> str:
    root = upstream_checkout(directory)
    try:
        result = subprocess.run(["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True, timeout=10, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unversioned"


def upstream_checkout(directory: str) -> Path:
    """Resolve image-related source checkouts from their canonical category."""
    root = Path(os.environ.get("AIPPT_UPSTREAM_DIR", str(Path(__file__).resolve().parents[3] / "github")))
    return root / "image" / directory


def parse_contract_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    return parser.parse_args()


def load_request(path: str) -> tuple[dict[str, Any], Path]:
    request_path = Path(path).resolve()
    return json.loads(request_path.read_text(encoding="utf-8")), request_path.parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact(job_root: Path, kind: str, path: Path) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": path.resolve().relative_to(job_root.resolve()).as_posix(),
        "sha256": sha256(path),
        "size_bytes": path.stat().st_size,
    }


def write_result(path: str, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(_redact(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "<redacted>", value)
        value = re.sub(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+", "<redacted>", value)
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~-]{8,}", "<redacted>", value)
        return value
    return value


def blocked(engine: str, version: str, category: str, message: str, fallback: str, **metadata: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "blocked",
        "receipt": {"engine": engine, "version": version},
        "failure": {"category": category, "message": message, "retryable": True, "fallback": fallback},
        "metadata": metadata,
    }


def source_records(job_root: Path, request: dict[str, Any]) -> list[tuple[dict[str, Any], Path, str]]:
    records: list[tuple[dict[str, Any], Path, str]] = []
    for source_id, role in request.get("input_roles", {}).items():
        record_path = job_root / "normalized" / "sources" / f"{source_id}.json"
        if not record_path.is_file():
            continue
        record = json.loads(record_path.read_text(encoding="utf-8"))
        source = (job_root / record["stored_path"]).resolve()
        if job_root.resolve() in source.parents and source.is_file():
            records.append((record, source, role))
    return records
