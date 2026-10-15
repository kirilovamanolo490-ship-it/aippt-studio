from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from typer.testing import CliRunner

from aippt_studio import cli, mac_quick_mp4, mac_quick_pdf


def test_explicit_pdf_conversion_uses_finished_pptx_without_job_flow(tmp_path, monkeypatch):
    source = tmp_path / "finished.pptx"
    source.write_bytes(b"pptx")
    seen = []
    monkeypatch.setattr(cli, "_require_host_native_python", lambda: None)
    monkeypatch.setattr(cli, "export_job", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("job export ran")))

    def convert(actual_source, actual_output):
        seen.append((actual_source, actual_output))
        return {"status": "passed", "path": str(actual_output)}

    monkeypatch.setattr(mac_quick_pdf, "export_pdf", convert)
    result = CliRunner().invoke(cli.app, ["export-pdf", str(source), "--json"])

    assert result.exit_code == 0, result.output
    assert seen == [(source, source.with_suffix(".pdf"))]
    assert json.loads(result.stdout)["path"] == str(source.with_suffix(".pdf"))


def test_explicit_mp4_conversion_honors_output_without_job_flow(tmp_path, monkeypatch):
    source = tmp_path / "finished.pptx"
    source.write_bytes(b"pptx")
    output = tmp_path / "delivery" / "film.mp4"
    seen = []
    monkeypatch.setattr(cli, "_require_host_native_python", lambda: None)
    monkeypatch.setattr(cli, "export_job", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("job export ran")))

    def convert(actual_source, actual_output):
        seen.append((actual_source, actual_output))
        return {"status": "exported", "output": str(actual_output)}

    monkeypatch.setattr(mac_quick_mp4, "export_mp4", convert)
    result = CliRunner().invoke(cli.app, ["export-mp4", str(source), "--output", str(output), "--json"])

    assert result.exit_code == 0, result.output
    assert seen == [(source, output)]
    assert json.loads(result.stdout)["output"] == str(output)


def test_explicit_png_export_dispatches_defaults_without_job_flow(tmp_path, monkeypatch):
    source = tmp_path / "finished.pptx"
    source.write_bytes(b"pptx")
    seen = []
    module = types.ModuleType("aippt_studio.mac_quick_png")

    def convert(actual_source, actual_output=None, actual_slide=None):
        seen.append((actual_source, actual_output, actual_slide))
        return {"status": "exported", "files": ["slide-1.png"]}

    module.export_png = convert
    monkeypatch.setitem(sys.modules, "aippt_studio.mac_quick_png", module)
    monkeypatch.setattr(cli, "_require_host_native_python", lambda: None)
    monkeypatch.setattr(cli, "export_job", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("job export ran")))

    result = CliRunner().invoke(cli.app, ["export-png", str(source), "--json"])

    assert result.exit_code == 0, result.output
    assert seen == [(source, None, None)]
    assert json.loads(result.stdout) == {"status": "exported", "files": ["slide-1.png"]}


def test_explicit_png_export_honors_slide_and_output_options(tmp_path, monkeypatch):
    source = tmp_path / "finished.pptx"
    source.write_bytes(b"pptx")
    output = tmp_path / "chosen.png"
    seen = []
    module = types.ModuleType("aippt_studio.mac_quick_png")

    def convert(actual_source, actual_output=None, actual_slide=None):
        seen.append((actual_source, actual_output, actual_slide))
        return {"status": "exported", "output": str(actual_output), "slide": actual_slide}

    module.export_png = convert
    monkeypatch.setitem(sys.modules, "aippt_studio.mac_quick_png", module)
    monkeypatch.setattr(cli, "_require_host_native_python", lambda: None)

    result = CliRunner().invoke(cli.app, ["export-png", str(source), "--slide", "3",
                                         "--output", str(output), "--json"])

    assert result.exit_code == 0, result.output
    assert seen == [(source, output, 3)]
    assert json.loads(result.stdout) == {"status": "exported", "output": str(output), "slide": 3}
