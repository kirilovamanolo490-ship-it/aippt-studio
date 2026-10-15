from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent


def jobs_root() -> Path:
    configured = os.environ.get("AIPPT_JOBS_DIR")
    return Path(configured).expanduser().resolve() if configured else WORKSPACE_ROOT / "jobs"


def upstream_root() -> Path:
    configured = os.environ.get("AIPPT_UPSTREAM_DIR")
    return Path(configured).expanduser().resolve() if configured else WORKSPACE_ROOT / "github"


def tools_root() -> Path:
    return PROJECT_ROOT / ".tools"

