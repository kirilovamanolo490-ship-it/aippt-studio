import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]


def allocate(root):
    result = subprocess.run([str(ROOT / "scripts/new-work.sh"), "-Name", "identity test", "-WorkRoot", str(root)],
                            env={**os.environ, "AIPPT_PYTHON": str(root / "not-installed")},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_cancelled_or_removed_work_number_is_not_reused(tmp_path):
    root = tmp_path / "work"
    (root / "_cancelled/29-cancelled-by-user").mkdir(parents=True)
    first = allocate(root)
    assert first["work_id"] == "30"
    record = json.loads((root / "30/work.json").read_text(encoding="utf-8-sig"))
    assert record["work_uuid"]
    (root / "30").rename(tmp_path / "separately-archived-work")
    second = allocate(root)
    assert second["work_id"] == "31"
    assert json.loads((root / ".work-sequence.json").read_text(encoding="utf-8-sig"))["high_water_mark"] == 31
