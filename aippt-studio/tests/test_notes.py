from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from aippt_studio.models import SlideSpec
from aippt_studio.notes import audit_pptx_notes, sanitize_pptx_notes, sanitize_speaker_notes_text


def _write_notes_deck(path: Path, paragraphs: list[str]) -> None:
    body = "".join(f"<a:p><a:r><a:t>{escape(text)}</a:t></a:r></a:p>" for text in paragraphs)
    notes = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        '<p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes Placeholder 2"/>'
        '<p:cNvSpPr/><p:nvPr><p:ph type="body" idx="1"/></p:nvPr></p:nvSpPr>'
        f'<p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>{body}</p:txBody>'
        '</p:sp></p:spTree></p:cSld></p:notes>'
    ).encode("utf-8")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/notesSlides/notesSlide1.xml", notes)


def test_sanitize_speaker_notes_text_removes_sources_and_local_paths() -> None:
    notes = "讲清楚本页的经营结论。\n[Sources]\n- F:\\aippt\\jobs\\demo\\source.docx\n- https://example.com"

    assert sanitize_speaker_notes_text(notes) == "讲清楚本页的经营结论。"


def test_slide_spec_sanitizes_internal_path_lines() -> None:
    slide = SlideSpec(
        id="slide-01",
        title="结论",
        purpose="说明结论",
        layout="content",
        speaker_notes="先解释结论。\n素材位于 C:\\Users\\demo\\image.png",
    )

    assert slide.speaker_notes == "先解释结论。"


def test_sanitize_pptx_notes_preserves_talk_track_and_removes_provenance(tmp_path: Path) -> None:
    deck = tmp_path / "deck.pptx"
    _write_notes_deck(
        deck,
        [
            "先讲结论，再说明下一步。",
            "[Sources]",
            "- F:\\aippt\\work\\4\\source.docx",
            "- https://example.com/source",
        ],
    )

    assert audit_pptx_notes(deck)
    result = sanitize_pptx_notes(deck)

    assert result == {"status": "sanitized", "changed_parts": 1, "removed_paragraphs": 3}
    assert audit_pptx_notes(deck) == []
    with zipfile.ZipFile(deck) as archive:
        notes = archive.read("ppt/notesSlides/notesSlide1.xml").decode("utf-8")
    assert "先讲结论，再说明下一步。" in notes
    assert "Sources" not in notes
    assert "F:\\aippt" not in notes
    assert "example.com" not in notes
