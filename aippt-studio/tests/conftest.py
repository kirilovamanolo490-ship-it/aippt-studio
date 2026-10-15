from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_jobs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIPPT_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("AIPPT_FAKE_RENDER", "1")

