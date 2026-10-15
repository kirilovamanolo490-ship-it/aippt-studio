from __future__ import annotations

import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

_SOURCE_HEADERS = {
    "[source]",
    "[sources]",
    "source",
    "sources",
    "[来源]",
    "[资料来源]",
    "来源",
    "资料来源",
}
_LOCAL_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]"),
    re.compile(r"(?:^|\s)\\\\[^\\\s]+\\"),
    re.compile(r"\bfile:(?://|\\\\)", re.IGNORECASE),
    re.compile(
        r"(?<![A-Za-z0-9])/(?:Users|home|tmp|var|mnt|Volumes|private|workspace|app|opt|srv|root)(?:/|\b)",
        re.IGNORECASE,
    ),
)


def _normalized_header(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).rstrip(":：").casefold()


def _is_source_header(text: str) -> bool:
    return _normalized_header(text) in _SOURCE_HEADERS


def _contains_local_path(text: str) -> bool:
    return any(pattern.search(text) for pattern in _LOCAL_PATH_PATTERNS)


def sanitize_speaker_notes_text(text: str) -> str:
    """Keep presenter-facing notes while removing provenance blocks and local paths."""

    kept: list[str] = []
    source_block = False
    for line in text.splitlines():
        if _is_source_header(line):
            source_block = True
            continue
        if source_block or _contains_local_path(line):
            continue
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def _paragraph_text(paragraph: ElementTree.Element) -> str:
    return "".join(node.text or "" for node in paragraph.iter(f"{{{DML_NS}}}t"))


def _body_text_shapes(root: ElementTree.Element) -> list[ElementTree.Element]:
    bodies: list[ElementTree.Element] = []
    for shape in root.findall(f".//{{{PML_NS}}}sp"):
        placeholder = shape.find(f"./{{{PML_NS}}}nvSpPr/{{{PML_NS}}}nvPr/{{{PML_NS}}}ph")
        if placeholder is not None and placeholder.get("type") in {"sldImg", "sldNum", "hdr", "ftr", "dt"}:
            continue
        text_body = shape.find(f"{{{PML_NS}}}txBody")
        if text_body is not None:
            bodies.append(text_body)
    return bodies


def audit_pptx_notes(pptx_path: Path) -> list[dict[str, str]]:
    """Report source blocks or local filesystem paths embedded in slide notes."""

    issues: list[dict[str, str]] = []
    try:
        with zipfile.ZipFile(pptx_path, "r") as archive:
            for name in archive.namelist():
                if not re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name):
                    continue
                root = ElementTree.fromstring(archive.read(name))
                for text_body in _body_text_shapes(root):
                    source_block = False
                    for paragraph in text_body.findall(f"{{{DML_NS}}}p"):
                        text = _paragraph_text(paragraph).strip()
                        if _is_source_header(text):
                            source_block = True
                            issues.append({"part": name, "reason": "source_block", "text": text})
                        elif not source_block and _contains_local_path(text):
                            issues.append({"part": name, "reason": "local_path", "text": text})
    except (zipfile.BadZipFile, ElementTree.ParseError):
        return []
    return issues


def sanitize_pptx_notes(pptx_path: Path) -> dict[str, object]:
    """Remove internal provenance blocks and local paths from embedded slide notes."""

    try:
        with zipfile.ZipFile(pptx_path, "r") as source:
            entries = [(info, source.read(info.filename)) for info in source.infolist()]
    except zipfile.BadZipFile:
        return {"status": "not_applicable", "changed_parts": 0, "removed_paragraphs": 0}

    changed_parts = 0
    removed_paragraphs = 0
    rewritten: list[tuple[zipfile.ZipInfo, bytes]] = []
    for info, data in entries:
        if not re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", info.filename):
            rewritten.append((info, data))
            continue
        try:
            root = ElementTree.fromstring(data)
        except ElementTree.ParseError:
            rewritten.append((info, data))
            continue
        part_changed = False
        for text_body in _body_text_shapes(root):
            source_block = False
            for paragraph in list(text_body.findall(f"{{{DML_NS}}}p")):
                text = _paragraph_text(paragraph).strip()
                if _is_source_header(text):
                    source_block = True
                if source_block or _contains_local_path(text):
                    text_body.remove(paragraph)
                    removed_paragraphs += 1
                    part_changed = True
            if not text_body.findall(f"{{{DML_NS}}}p"):
                ElementTree.SubElement(text_body, f"{{{DML_NS}}}p")
        if part_changed:
            changed_parts += 1
            ElementTree.register_namespace("p", PML_NS)
            ElementTree.register_namespace("a", DML_NS)
            ElementTree.register_namespace(
                "r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
            )
            data = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
        rewritten.append((info, data))

    if changed_parts:
        temporary = pptx_path.with_suffix(".notes-sanitized.pptx")
        with zipfile.ZipFile(temporary, "w") as target:
            for info, data in rewritten:
                target.writestr(info, data)
        temporary.replace(pptx_path)
    return {
        "status": "sanitized" if changed_parts else "clean",
        "changed_parts": changed_parts,
        "removed_paragraphs": removed_paragraphs,
    }
