"""Read-only inventory of vendored repositories; execution remains in Studio."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .config import PROJECT_ROOT, upstream_root


def git_snapshot(path: Path) -> dict[str, object]:
    if not (path / ".git").exists():
        return {"status": "not_a_checkout", "clean": False}
    def git(*args: str) -> str:
        result = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True, timeout=60, check=True)
        return result.stdout.strip()
    try:
        changes = git("status", "--porcelain", "--untracked-files=all")
        return {"status": "verified", "head": git("rev-parse", "HEAD"),
                "origin": git("remote", "get-url", "origin"),
                "shallow": git("rev-parse", "--is-shallow-repository") == "true",
                "clean": not changes, "changed_files": len(changes.splitlines())}
    except (OSError, subprocess.SubprocessError) as error:
        return {"status": "git_error", "clean": False, "error": str(error)}


def upstream_report(*, verify: bool = False, identifier: str | None = None,
                    project_root: Path = PROJECT_ROOT, source_root: Path | None = None) -> list[dict[str, object]]:
    payload = json.loads((project_root / "upstreams.lock.json").read_text(encoding="utf-8"))
    rows = []
    for item in payload.get("upstreams", []):
        if identifier and identifier != item["id"]:
            continue
        lifecycle = item.get("lifecycle", "active")
        if lifecycle not in {"active", "retired"}:
            raise ValueError(f"unsupported lifecycle for upstream {item['id']}: {lifecycle}")
        retirement = item.get("retirement") if lifecycle == "retired" else None
        directory = item.get("directory")
        acquisition = item.get("acquisition", {})
        candidate_path = ((source_root or upstream_root()) / directory) if directory else None
        if candidate_path is None and acquisition.get("local_path"):
            candidate_path = Path(acquisition["local_path"])
            if not candidate_path.is_absolute():
                candidate_path = project_root / candidate_path
        path = None if lifecycle == "retired" else candidate_path
        row = {"id": item["id"], "source_url": item["source_url"], "path": str(path.resolve()) if path else None,
               "commit_sha": item.get("commit_sha"), "license": item.get("license"),
               "integration": item.get("integration"), "role": item.get("role"),
               "invocation": item.get("invocation", {}), "runtime": item.get("runtime", {}),
               "lifecycle": lifecycle, "retirement": retirement,
               "acquisition_status": acquisition.get("status"),
               "source_present": bool(path and path.is_dir())}
        if lifecycle == "retired":
            row["retired_path"] = str(candidate_path.resolve()) if candidate_path else None
        if verify:
            if lifecycle == "retired":
                snapshot = {"status": "retired", "clean": None, "matches_lock": None,
                            "retired_path": row["retired_path"]}
            else:
                snapshot = git_snapshot(path) if path else {"status": "not_acquired", "clean": False}
                expected_origin = item["source_url"].rstrip("/").removesuffix(".git")
                actual_origin = str(snapshot.get("origin", "")).rstrip("/").removesuffix(".git")
                snapshot["matches_lock"] = bool(snapshot.get("head") == item.get("commit_sha") and actual_origin == expected_origin and snapshot.get("clean"))
            row["verification"] = snapshot
        rows.append(row)
    if identifier and not rows:
        raise ValueError(f"unknown upstream: {identifier}")
    return rows
