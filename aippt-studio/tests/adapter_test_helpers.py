from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path


def write_contract_job(root: Path, *, route: str, role: str, source: Path, slides: int = 2, cloud_allowed: bool = True) -> tuple[Path, Path]:
    for directory in ("build", "planning", "normalized/sources", "sources", "workspace"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    stored = root / "sources" / source.name
    stored.write_bytes(source.read_bytes())
    (root / "normalized" / "sources" / "source-1.json").write_text(
        json.dumps(
            {
                "id": "source-1",
                "original": source.name,
                "stored_path": f"sources/{source.name}",
                "kind": "powerpoint" if source.suffix.lower() == ".pptx" else ("pdf" if source.suffix.lower() == ".pdf" else "image"),
                "sha256": hashlib.sha256(stored.read_bytes()).hexdigest(),
                "size_bytes": stored.stat().st_size,
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    plan = {
        "title": "Adapter Fixture",
        "slides": [
            {
                "id": f"slide-{index:02d}",
                "title": f"Fixture title {index}",
                "purpose": "acceptance",
                "layout": "content",
                "points": [f"Point {index}"],
                "speaker_notes": "Presenter-facing note only.",
            }
            for index in range(1, slides + 1)
        ],
    }
    (root / "planning" / "deck-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    (root / "planning" / "brief.json").write_text(json.dumps({"cloud_allowed": cloud_allowed}), encoding="utf-8")
    (root / "evidence-ledger.json").write_text("{}", encoding="utf-8")
    request = root / "build" / "adapter-request.json"
    result = root / "build" / "adapter-result.json"
    request.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "job_id": "fixture",
                "route": route,
                "brief": "planning/brief.json",
                "deck_plan": "planning/deck-plan.json",
                "evidence_ledger": "evidence-ledger.json",
                "output_root": "build/adapter-staging",
                "input_roles": {"source-1": role},
                "cloud_allowed": cloud_allowed,
                "requested_outputs": ["pptx", "previews", "build_source"],
            }
        ),
        encoding="utf-8",
    )
    return request, result


def run_adapter(python: Path, script: Path, request: Path, result: Path) -> dict[str, object]:
    completed = subprocess.run(
        [str(python), str(script), "--request", str(request), "--result", str(result)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(result.read_text(encoding="utf-8"))
