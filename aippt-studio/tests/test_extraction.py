from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import zipfile

from PIL import Image

from aippt_studio import extraction
from aippt_studio import edits
from aippt_studio.workflow import create_job


def _fake_pptx(path: Path) -> None:
    image = BytesIO()
    Image.new("RGB", (8, 8), "#14b8a6").save(image, format="PNG")
    image_fill = BytesIO()
    Image.new("RGB", (12, 8), "#1c53ce").save(image_fill, format="JPEG")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "ppt/presentation.xml",
            '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<p:sldIdLst><p:sldId id="256" r:id="rSlide1"/></p:sldIdLst></p:presentation>',
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rSlide1" Target="slides/slide1.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"/>'
            '</Relationships>',
        )
        archive.writestr("ppt/slides/slide1.xml", "<p:sld/>" )
        archive.writestr(
            "ppt/slides/_rels/slide1.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="../media/image1.png" Type="image"/>'
            "</Relationships>",
        )
        archive.writestr("ppt/media/image1.png", image.getvalue())
        archive.writestr("ppt/media/image2.jpeg", image_fill.getvalue())


def test_inspect_source_file_writes_manifest_and_extracts_assets(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.pptx"
    _fake_pptx(source)

    def fake_run(_executable: Path, arguments: list[str], *, timeout: int = 180) -> str:
        if arguments[0] == "--version":
            return "1.0.test"
        if arguments[0] == "query":
            return '{"success":true,"data":{"matches":0,"results":[]}}'
        if arguments[0] == "get" and arguments[2] == "/":
            return '{"success":true,"data":{"matches":1,"results":[{"path":"/","type":"presentation","childCount":1,"format":{"slideWidth":"960pt","slideHeight":"540pt","slideSize":"widescreen","defaultFont":"Calibri"},"children":[{"path":"/slide[1]","type":"slide","childCount":2,"format":{"name":"Slide 1","layout":"DEFAULT"}}]}]}}'
        if arguments[0] == "get":
            return '{"success":true,"data":{"matches":1,"results":[{"path":"/slide[1]","type":"slide","childCount":3,"format":{"name":"Slide 1","layout":"DEFAULT"},"children":[{"path":"/slide[1]/shape[@id=2]","type":"shape","text":"标题","preview":"标题","format":{"id":2,"name":"Title","x":"1in","y":"1in"}},{"path":"/slide[1]/picture[@id=3]","type":"picture","preview":"Picture 1","format":{"id":3,"name":"Picture 1","relId":"rId1","contentType":"image/png","fileSize":79}},{"path":"/slide[1]/shape[@id=4]","type":"shape","preview":"Image fill","format":{"id":4,"name":"Image fill","image":true,"image.src":"image2.jpeg"}}]}]}}'
        return ""

    monkeypatch.setattr(extraction, "_officecli", lambda: tmp_path / "officecli.exe")
    monkeypatch.setattr(extraction, "_run_officecli", fake_run)
    output = tmp_path / "extraction" / "src-1"
    manifest = extraction.inspect_source_file(
        source,
        source_id="src-1",
        original=str(source),
        stored_path="sources/source.pptx",
        output_root=output,
        capture_screenshots=False,
    )

    assert manifest.slide_count == 1
    assert manifest.slide_width == "960pt"
    assert manifest.slides[0].elements[0].text == "标题"
    assert manifest.slides[0].elements[1].asset_id == "asset-001"
    assert manifest.assets[0].kind == "image"
    assert manifest.assets[0].extraction_status == "extracted"
    assert (output / "assets" / "asset-001.png").is_file()
    assert manifest.slides[0].elements[2].asset_id == "asset-002"
    assert manifest.assets[1].package_path == "ppt/media/image2.jpeg"
    assert (output / "assets" / "asset-002.jpeg").is_file()
    assert (output / "source-deck-manifest.json").is_file()


def test_apply_job_edits_writes_auditable_batch(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.pptx"
    _fake_pptx(source)
    operations_path = tmp_path / "edits.json"
    operations_path.write_text(
        json.dumps(
            {
                "id": "edit-test",
                "operations": [
                    {
                        "id": "title-text",
                        "action": "set",
                        "target": "/slide[1]/shape[@id=2]",
                        "properties": {"text": "Updated title"},
                    },
                    {
                        "id": "title-motion",
                        "action": "add",
                        "target": "/slide[1]/shape[@id=2]",
                        "element": "animation",
                        "properties": {"effect": "fade", "class": "entrance", "duration": 500},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    job = create_job("edit test", job_id="edit-test")

    def fake_run(_executable: Path, arguments: list[str], *, timeout: int = 180) -> str:
        if arguments[0] == "get":
            return '{"success":true,"data":{"matches":1,"results":[{"path":"/slide[1]","children":[{"type":"shape","path":"/slide[1]/shape[@id=2]","format":{"id":2}}]}]}}'
        if arguments[0] == "validate":
            return '{"success":true,"data":"Validation passed"}'
        if arguments[0] == "close":
            return "closed"
        return '{"success":true,"data":"ok"}'

    monkeypatch.setattr(edits, "_officecli", lambda: tmp_path / "officecli.exe")
    monkeypatch.setattr(edits, "_run_officecli", fake_run)
    result = edits.apply_job_edits(
        job.id,
        str(operations_path),
        input_path=str(source),
        output_path="build/edited.pptx",
    )

    assert (tmp_path / "jobs" / job.id / "build" / "edited.pptx").is_file()
    artifact = tmp_path / "jobs" / job.id / "edits" / "edit-test.json"
    assert artifact.is_file()
    assert result["batch"]["validation"]["status"] == "passed"
    assert len(result["batch"]["results"]) == 2
