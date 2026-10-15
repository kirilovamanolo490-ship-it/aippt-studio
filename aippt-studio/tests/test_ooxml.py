from __future__ import annotations

import zipfile
from pathlib import Path

from aippt_studio.renderer import normalize_ooxml


def test_normalize_removes_empty_generated_notes_tree(tmp_path: Path) -> None:
    deck = tmp_path / "broken.pptx"
    broken = b'<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"><p:sldIdLst></p:sldIdLst><p:notesMasterIdLst><p:notesMasterId/></p:notesMasterIdLst><p:sldSz/><p:notesSz/></p:presentation>'
    relationships = b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster" Target="notesMasters/notesMaster1.xml"/></Relationships>'
    slide_relationships = b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>'
    content_types = b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/ppt/notesMasters/notesMaster1.xml" ContentType="notes-master"/><Override PartName="/ppt/notesSlides/notesSlide1.xml" ContentType="notes-slide"/></Types>'
    with zipfile.ZipFile(deck, "w") as archive:
        archive.writestr("ppt/presentation.xml", broken)
        archive.writestr("ppt/_rels/presentation.xml.rels", relationships)
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", slide_relationships)
        archive.writestr("ppt/notesMasters/notesMaster1.xml", b"<notesMaster/>")
        archive.writestr("ppt/notesSlides/notesSlide1.xml", b"<notesSlide/>")
        archive.writestr("[Content_Types].xml", content_types)
    normalize_ooxml(deck)
    with zipfile.ZipFile(deck) as archive:
        assert b"notesMasterIdLst" not in archive.read("ppt/presentation.xml")
        assert b"notesMaster" not in archive.read("ppt/_rels/presentation.xml.rels")
        assert b"notesSlide" not in archive.read("ppt/slides/_rels/slide1.xml.rels")
        assert b"notesMaster" not in archive.read("[Content_Types].xml")
        assert not any(name.startswith(("ppt/notesMasters/", "ppt/notesSlides/")) for name in archive.namelist())
