from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, ParamSpec, TypeVar

from pydantic import BaseModel

from .config import jobs_root
from .models import JobRecord, utc_now


T = TypeVar("T", bound=BaseModel)
P = ParamSpec("P")
R = TypeVar("R")
_local_locks = threading.local()


def safe_job_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    if not normalized:
        raise ValueError("job id must contain letters or numbers")
    return normalized[:80]


def job_dir(job_id: str) -> Path:
    root = jobs_root().resolve()
    path = (root / safe_job_id(job_id)).resolve()
    if path.parent != root:
        raise ValueError("job path escapes jobs root")
    return path


def write_json(path: Path, value: BaseModel | dict[str, Any] | list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


@contextmanager
def job_lock(job_id: str, timeout: float = 360.0) -> Iterator[None]:
    identifier = safe_job_id(job_id)
    held = getattr(_local_locks, "held", set())
    if identifier in held:
        yield
        return

    lock_root = jobs_root().resolve() / ".locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{identifier}.lock"
    stream = lock_path.open("a+b")
    stream.seek(0, 2)
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
    deadline = time.monotonic() + timeout
    acquired = False
    try:
        while not acquired:
            stream.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (OSError, BlockingIOError):
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for job lock: {identifier}")
                time.sleep(0.05)
        held = set(held)
        held.add(identifier)
        _local_locks.held = held
        yield
    finally:
        if acquired:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()
        held = set(getattr(_local_locks, "held", set()))
        held.discard(identifier)
        _local_locks.held = held


def locked_job(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def wrapper(job_id: str, *args: P.args, **kwargs: P.kwargs) -> R:
        with job_lock(job_id):
            return function(job_id, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_model(path: Path, model: type[T]) -> T:
    return model.model_validate(read_json(path))


def load_job(job_id: str) -> JobRecord:
    path = job_dir(job_id) / "job.json"
    if not path.is_file():
        raise FileNotFoundError(f"job not found: {job_id}")
    return read_model(path, JobRecord)


def save_job(job: JobRecord) -> None:
    job.updated_at = utc_now()
    write_json(job_dir(job.id) / "job.json", job)


def initialize_job_directories(root: Path) -> None:
    for relative in (
        "sources",
        "normalized",
        "planning",
        "extraction",
        "edits",
        "build/slides",
        "build/previews",
        "assets/acquired",
        "reviews",
        "revisions",
        "deliverables/slides",
        "logs",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
