from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from aippt_studio import cli


def test_json_stdout_is_utf8_when_windows_stream_encoding_is_gbk(tmp_path: Path) -> None:
    name = "中文项目 • 动画 😄"
    env = {
        **os.environ,
        "AIPPT_JOBS_DIR": str(tmp_path / "jobs"),
        "AIPPT_FAKE_RENDER": "1",
        "PYTHONIOENCODING": "cp936",
        "PYTHONUTF8": "0",
    }

    completed = subprocess.run(
        [sys.executable, "-m", "aippt_studio", "new", "--name", name, "--json"],
        cwd=Path(__file__).parents[1],
        env=env,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stderr == b""
    payload = json.loads(completed.stdout.decode("utf-8"))
    assert payload["name"] == name


def test_command_errors_are_written_only_to_stderr(monkeypatch) -> None:
    message = "错误 • 动画 😄"

    def fail_create_job(*args, **kwargs):
        raise ValueError(message)

    monkeypatch.setattr(cli, "create_job", fail_create_job)

    result = CliRunner().invoke(cli.app, ["new", "--name", "测试", "--json"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert message in result.stderr
