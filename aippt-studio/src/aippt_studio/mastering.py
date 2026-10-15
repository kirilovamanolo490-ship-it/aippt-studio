from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, Path
import re
import zipfile
from xml.etree import ElementTree


PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CONTENT_TYPES = "http://schemas.openxmlformats.org/package/2006/content-types"
SLIDE_LAYOUT_REL = f"{REL}/slideLayout"
SLIDE_MASTER_REL = f"{REL}/slideMaster"
THEME_REL = f"{REL}/theme"

ElementTree.register_namespace("p", PML)
ElementTree.register_namespace("r", REL)
ElementTree.register_namespace("", PKG_REL)


@dataclass(frozen=True)
class MasterPromotion:
    slide_index: int
    name: str


def _xml(data: bytes) -> ElementTree.Element:
    return ElementTree.fromstring(data)


def _bytes(root: ElementTree.Element) -> bytes:
    namespace = root.tag[1:].split("}", 1)[0] if root.tag.startswith("{") else ""
    if namespace in {PKG_REL, CONTENT_TYPES}:
        ElementTree.register_namespace("", namespace)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


def _relationships(data: bytes) -> ElementTree.Element:
    return _xml(data)


def _relationship(root: ElementTree.Element, relationship_type: str) -> ElementTree.Element:
    item = next((child for child in root if child.get("Type") == relationship_type), None)
    if item is None:
        raise ValueError(f"relationship not found: {relationship_type}")
    return item


def _part_number(target: str, stem: str) -> int:
    match = re.search(rf"{re.escape(stem)}(\d+)\.xml$", target)
    if not match:
        raise ValueError(f"unexpected OOXML target: {target}")
    return int(match.group(1))


def _clone_master_xml(data: bytes, name: str, layout_rel_ids: list[tuple[int, str]]) -> bytes:
    root = _xml(data)
    common_slide = root.find(f"{{{PML}}}cSld")
    if common_slide is None:
        raise ValueError("slide master has no common slide")
    common_slide.set("name", name)
    layout_list = root.find(f"{{{PML}}}sldLayoutIdLst")
    if layout_list is None:
        raise ValueError("slide master has no layout list")
    layout_list.clear()
    for layout_id, rel_id in layout_rel_ids:
        item = ElementTree.SubElement(layout_list, f"{{{PML}}}sldLayoutId")
        item.set("id", str(layout_id))
        item.set(f"{{{REL}}}id", rel_id)
    return _bytes(root)


def _master_relationships(layout_targets: list[tuple[str, str]], theme_target: str) -> bytes:
    root = ElementTree.Element(f"{{{PKG_REL}}}Relationships")
    for rel_id, target in layout_targets:
        ElementTree.SubElement(
            root,
            f"{{{PKG_REL}}}Relationship",
            {"Id": rel_id, "Type": SLIDE_LAYOUT_REL, "Target": target},
        )
    ElementTree.SubElement(
        root,
        f"{{{PKG_REL}}}Relationship",
        {"Id": f"rId{len(layout_targets) + 1}", "Type": THEME_REL, "Target": theme_target},
    )
    return _bytes(root)


def _master_layout_id_records(entries: dict[str, bytes]) -> tuple[list[tuple[int, str]], int, int]:
    presentation = _xml(entries["ppt/presentation.xml"])
    master_items = presentation.findall(f".//{{{PML}}}sldMasterId")
    records: list[tuple[int, str]] = []
    for index, item in enumerate(master_items, start=1):
        value = item.get("id")
        if value is None:
            raise ValueError(f"missing PowerPoint master ID at ppt/presentation.xml#sldMasterId[{index}]")
        records.append((int(value), f"ppt/presentation.xml#sldMasterId[{index}]"))

    layout_count = 0
    master_paths = sorted(
        (
            name
            for name in entries
            if re.fullmatch(r"ppt/slideMasters/slideMaster\d+\.xml", name)
        ),
        key=lambda name: _part_number(name, "slideMaster"),
    )
    for master_path in master_paths:
        master = _xml(entries[master_path])
        for index, item in enumerate(master.findall(f".//{{{PML}}}sldLayoutId"), start=1):
            value = item.get("id")
            if value is None:
                raise ValueError(f"missing PowerPoint layout ID at {master_path}#sldLayoutId[{index}]")
            records.append((int(value), f"{master_path}#sldLayoutId[{index}]"))
            layout_count += 1
    return records, len(master_items), layout_count


def _audit_master_layout_id_entries(entries: dict[str, bytes]) -> dict[str, object]:
    records, master_count, layout_count = _master_layout_id_records(entries)
    first_locations: dict[int, str] = {}
    duplicate_messages: list[str] = []
    for value, location in records:
        first_location = first_locations.setdefault(value, location)
        if first_location != location:
            duplicate_messages.append(f"{value} at {first_location} and {location}")
    if duplicate_messages:
        details = "; ".join(duplicate_messages)
        raise ValueError(f"PowerPoint master/layout IDs must be globally unique: {details}")
    return {
        "master_count": master_count,
        "layout_count": layout_count,
        "ids": [value for value, _ in records],
    }


def audit_master_layout_ids(pptx_path: Path) -> dict[str, object]:
    """Verify PowerPoint's shared global ID namespace for masters and layouts."""

    with zipfile.ZipFile(pptx_path) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    return _audit_master_layout_id_entries(entries)


def promote_layouts_to_masters(
    input_path: Path,
    output_path: Path,
    promotions: list[MasterPromotion],
) -> None:
    if len(promotions) < 2:
        raise ValueError("at least two layouts are required for multi-master promotion")

    with zipfile.ZipFile(input_path) as source:
        infos = source.infolist()
        entries = {info.filename: source.read(info.filename) for info in infos}

    master_path = "ppt/slideMasters/slideMaster1.xml"
    master_rels_path = "ppt/slideMasters/_rels/slideMaster1.xml.rels"
    if master_path not in entries or master_rels_path not in entries:
        raise ValueError("source deck does not contain slideMaster1")

    id_records, _, _ = _master_layout_id_records(entries)
    used_global_ids = {value for value, _ in id_records}
    next_global_id = max(used_global_ids | {2147483647}) + 1

    def allocate_global_id() -> int:
        nonlocal next_global_id
        while next_global_id in used_global_ids:
            next_global_id += 1
        value = next_global_id
        used_global_ids.add(value)
        next_global_id += 1
        return value

    master_rels = _relationships(entries[master_rels_path])
    theme_target = _relationship(master_rels, THEME_REL).get("Target")
    if not theme_target:
        raise ValueError("source master has no theme target")

    assigned_layouts: list[tuple[MasterPromotion, int]] = []
    for promotion in promotions:
        slide_rels_path = f"ppt/slides/_rels/slide{promotion.slide_index}.xml.rels"
        slide_rels = _relationships(entries[slide_rels_path])
        layout_target = _relationship(slide_rels, SLIDE_LAYOUT_REL).get("Target")
        if not layout_target:
            raise ValueError(f"slide {promotion.slide_index} has no layout target")
        assigned_layouts.append((promotion, _part_number(layout_target, "slideLayout")))

    used_layout_numbers = {number for _, number in assigned_layouts}
    next_layout = max(
        _part_number(name, "slideLayout")
        for name in entries
        if re.fullmatch(r"ppt/slideLayouts/slideLayout\d+\.xml", name)
    ) + 1

    first_promotion, first_layout = assigned_layouts[0]
    blank_layouts = [
        (_part_number(item.get("Target", ""), "slideLayout"), item.get("Id", ""))
        for item in master_rels
        if item.get("Type") == SLIDE_LAYOUT_REL
        and _part_number(item.get("Target", ""), "slideLayout") not in used_layout_numbers
    ]
    first_rel = next(
        item.get("Id", "")
        for item in master_rels
        if item.get("Type") == SLIDE_LAYOUT_REL
        and _part_number(item.get("Target", ""), "slideLayout") == first_layout
    )
    first_layouts = [(allocate_global_id(), first_rel)] + [
        (allocate_global_id(), rel_id) for _, rel_id in blank_layouts
    ]
    entries[master_path] = _clone_master_xml(entries[master_path], first_promotion.name, first_layouts)
    entries[master_rels_path] = _master_relationships(
        [(first_rel, f"../slideLayouts/slideLayout{first_layout}.xml")]
        + [(rel_id, f"../slideLayouts/slideLayout{number}.xml") for number, rel_id in blank_layouts],
        theme_target,
    )

    presentation = _xml(entries["ppt/presentation.xml"])
    master_list = presentation.find(f"{{{PML}}}sldMasterIdLst")
    if master_list is None:
        raise ValueError("presentation has no master list")
    presentation_rels = _relationships(entries["ppt/_rels/presentation.xml.rels"])
    existing_rel_numbers = [int(match.group(1)) for item in presentation_rels if (match := re.fullmatch(r"rId(\d+)", item.get("Id", "")))]
    next_presentation_rel = max(existing_rel_numbers) + 1

    content_types = _xml(entries["[Content_Types].xml"])
    for child in list(content_types):
        part_name = child.get("PartName", "")
        if re.fullmatch(r"/ppt/slideMasters/slideMaster[2-9]\d*\.xml", part_name):
            content_types.remove(child)
    removed_parts: set[str] = set()
    for offset, (promotion, source_layout) in enumerate(assigned_layouts[1:], start=2):
        master_number = offset
        layout_number = next_layout + offset - 2
        source_layout_path = f"ppt/slideLayouts/slideLayout{source_layout}.xml"
        source_layout_rels = f"ppt/slideLayouts/_rels/slideLayout{source_layout}.xml.rels"
        new_layout_path = f"ppt/slideLayouts/slideLayout{layout_number}.xml"
        new_layout_rels = f"ppt/slideLayouts/_rels/slideLayout{layout_number}.xml.rels"
        entries[new_layout_path] = entries[source_layout_path]

        layout_rels = _relationships(entries[source_layout_rels])
        _relationship(layout_rels, SLIDE_MASTER_REL).set("Target", f"../slideMasters/slideMaster{master_number}.xml")
        entries[new_layout_rels] = _bytes(layout_rels)

        master_rel_id = f"rId{next_presentation_rel + offset - 2}"
        master_id = allocate_global_id()
        master_item = ElementTree.SubElement(master_list, f"{{{PML}}}sldMasterId")
        master_item.set("id", str(master_id))
        master_item.set(f"{{{REL}}}id", master_rel_id)
        ElementTree.SubElement(
            presentation_rels,
            f"{{{PKG_REL}}}Relationship",
            {"Id": master_rel_id, "Type": SLIDE_MASTER_REL, "Target": f"slideMasters/slideMaster{master_number}.xml"},
        )

        entries[f"ppt/slideMasters/slideMaster{master_number}.xml"] = _clone_master_xml(
            entries[master_path], promotion.name, [(allocate_global_id(), "rId1")]
        )
        entries[f"ppt/slideMasters/_rels/slideMaster{master_number}.xml.rels"] = _master_relationships(
            [("rId1", f"../slideLayouts/slideLayout{layout_number}.xml")], theme_target
        )

        slide_rels_path = f"ppt/slides/_rels/slide{promotion.slide_index}.xml.rels"
        slide_rels = _relationships(entries[slide_rels_path])
        _relationship(slide_rels, SLIDE_LAYOUT_REL).set("Target", f"../slideLayouts/slideLayout{layout_number}.xml")
        entries[slide_rels_path] = _bytes(slide_rels)

        ElementTree.SubElement(
            content_types,
            f"{{{CONTENT_TYPES}}}Override",
            {
                "PartName": f"/ppt/slideMasters/slideMaster{master_number}.xml",
                "ContentType": "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml",
            },
        )
        ElementTree.SubElement(
            content_types,
            f"{{{CONTENT_TYPES}}}Override",
            {
                "PartName": f"/ppt/slideLayouts/slideLayout{layout_number}.xml",
                "ContentType": "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml",
            },
        )
        removed_parts.update({source_layout_path, source_layout_rels})

    for child in list(content_types):
        part_name = child.get("PartName", "").lstrip("/")
        if part_name in removed_parts:
            content_types.remove(child)
    for part in removed_parts:
        entries.pop(part, None)

    entries["ppt/presentation.xml"] = _bytes(presentation)
    entries["ppt/_rels/presentation.xml.rels"] = _bytes(presentation_rels)
    entries["[Content_Types].xml"] = _bytes(content_types)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_name(f".{output_path.name}.mastering.tmp")
    try:
        with zipfile.ZipFile(temporary_path, "w", compression=zipfile.ZIP_DEFLATED) as target:
            written: set[str] = set()
            for info in infos:
                if info.filename in entries:
                    target.writestr(info, entries[info.filename])
                    written.add(info.filename)
            for name, data in entries.items():
                if name not in written:
                    target.writestr(name, data)
        audit_master_layout_ids(temporary_path)
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
