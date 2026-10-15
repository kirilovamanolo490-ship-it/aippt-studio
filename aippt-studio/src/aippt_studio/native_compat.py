"""Narrow OOXML fixes at the PptxGenJS 4.0.1 output boundary."""
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .renderer import normalize_ooxml


_PRESENTATION_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_CNVPR_TAG = re.compile(rb"<(?:(?:[A-Za-z_][A-Za-z0-9_.-]*):)?cNvPr\b[^>]*>")
_ID_ATTRIBUTE = re.compile(rb"(?<![A-Za-z0-9_:.-])id\s*=\s*(?P<quote>['\"])(?P<id>[0-9]+)(?P=quote)")
_POINT_LABEL_FLAGS = {"showLegendKey", "showVal", "showCatName", "showSerName", "showPercent", "showBubbleSize"}


def _renumber_duplicate_shape_ids(xml: bytes) -> tuple[bytes, int]:
    """Repair duplicate slide-local cNvPr IDs without reserializing slide XML.

    PptxGenJS 4.0.1 can emit the same ID for a normal shape and a native table.
    Timed slides are left untouched because IDs can be referenced by animation
    timing trees; this repair is for newly-authored, unanimated native scenes.
    """
    root = ET.fromstring(xml)
    if root.find(f".//{{{_PRESENTATION_NS}}}timing") is not None:
        return xml, 0
    tree = root.find(f".//{{{_PRESENTATION_NS}}}spTree")
    if tree is None:
        return xml, 0
    nodes = tree.findall(f".//{{{_PRESENTATION_NS}}}cNvPr")
    ids = [node.get("id") for node in nodes]
    if len(ids) == len(set(ids)):
        return xml, 0
    try:
        next_id = max(int(shape_id) for shape_id in ids if shape_id is not None) + 1
    except ValueError:
        return xml, 0

    tags = list(_CNVPR_TAG.finditer(xml))
    if len(tags) != len(nodes):
        return xml, 0
    seen: set[str] = set()
    edits: list[tuple[int, int, bytes]] = []
    changed = 0
    for node, tag in zip(nodes, tags):
        shape_id = node.get("id")
        id_match = _ID_ATTRIBUTE.search(tag.group(0))
        if shape_id is None or id_match is None or id_match.group("id").decode("ascii") != shape_id:
            return xml, 0
        if shape_id in seen:
            replacement = str(next_id).encode("ascii")
            start = tag.start() + id_match.start("id")
            end = tag.start() + id_match.end("id")
            edits.append((start, end, replacement))
            seen.add(replacement.decode("ascii"))
            next_id += 1
            changed += 1
        else:
            seen.add(shape_id)
    for start, end, replacement in reversed(edits):
        xml = xml[:start] + replacement + xml[end:]
    return xml, changed


def _disable_redundant_pie_parent_labels(root: ET.Element, ns: dict[str, str]) -> int:
    """Honor explicit point-level off flags when pie chart defaults enable them."""
    charts = root.findall(".//c:pieChart", ns) + root.findall(".//c:doughnutChart", ns)
    changed = 0
    for chart in charts:
        for labels in chart.findall("c:ser/c:dLbls", ns):
            points = labels.findall("c:dLbl", ns)
            if not points:
                continue
            for parent_flag in labels:
                name = parent_flag.tag.rsplit("}", 1)[-1]
                if name not in _POINT_LABEL_FLAGS or parent_flag.get("val") != "1":
                    continue
                if all((point_flag := point.find(f"c:{name}", ns)) is not None
                       and point_flag.get("val") == "0" for point in points):
                    parent_flag.set("val", "0")
                    changed += 1
    return changed


def normalize_native_candidate(path: Path) -> dict:
    normalize_ooxml(path)
    namespace = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    ns = {"c": namespace}
    removed = 0
    font_attributes = 0
    duplicate_shape_ids = 0
    disabled_parent_point_label_flags = 0
    with ZipFile(path) as archive:
        entries = [(info, archive.read(info.filename)) for info in archive.infolist()]
    output = []
    for info, data in entries:
        if info.filename.startswith("ppt/slides/slide") and info.filename.endswith(".xml"):
            data, fixed_ids = _renumber_duplicate_shape_ids(data)
            duplicate_shape_ids += fixed_ids
            root = ET.fromstring(data)
            drawing = "http://schemas.openxmlformats.org/drawingml/2006/main"
            slide_font_attributes = 0
            for table in root.findall(f".//{{{drawing}}}tbl"):
                for font in table.iter():
                    if font.tag in {f"{{{drawing}}}latin", f"{{{drawing}}}ea", f"{{{drawing}}}cs"}:
                        for attribute in ("charset", "pitchFamily"):
                            if attribute in font.attrib:
                                del font.attrib[attribute]
                                font_attributes += 1
                                slide_font_attributes += 1
            if slide_font_attributes:
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        if info.filename.startswith("ppt/charts/chart") and info.filename.endswith(".xml"):
            root = ET.fromstring(data)
            changed = False
            fixed_label_flags = _disable_redundant_pie_parent_labels(root, ns)
            if fixed_label_flags:
                disabled_parent_point_label_flags += fixed_label_flags
                changed = True
            for plot in root.findall(".//c:plotArea", ns):
                declared = {axis.find("c:axId", ns).get("val") for axis in plot
                            if axis.tag in {f"{{{namespace}}}catAx", f"{{{namespace}}}valAx", f"{{{namespace}}}dateAx", f"{{{namespace}}}serAx"}
                            and axis.find("c:axId", ns) is not None}
                for chart in plot:
                    if chart.tag not in {f"{{{namespace}}}{name}" for name in ("barChart", "lineChart", "areaChart", "scatterChart", "radarChart")}:
                        continue
                    refs = chart.findall("c:axId", ns)
                    # 4.0.1 emits a third, undeclared primary series axis even
                    # for a 2D chart. Keep all legitimate axis definitions.
                    for ref in refs:
                        if len(refs) == 3 and ref.get("val") == "2094734556" and ref.get("val") not in declared:
                            chart.remove(ref)
                            removed += 1
                            changed = True
            if changed:
                ET.register_namespace("c", namespace)
                data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        output.append((info, data))
    if removed or font_attributes or duplicate_shape_ids or disabled_parent_point_label_flags:
        temporary = path.with_suffix(".compat.pptx")
        with ZipFile(temporary, "w") as archive:
            for info, data in output:
                archive.writestr(info, data)
        temporary.replace(path)
    return {"normalization": "empty notes, dangling 2D series axis, Windows font charset hints in tables, duplicate shape IDs in unanimated native scenes, explicit pie point-label visibility",
            "removed_dangling_axis_refs": removed, "removed_table_font_hints": font_attributes,
            "renumbered_duplicate_shape_ids": duplicate_shape_ids,
            "disabled_parent_point_label_flags": disabled_parent_point_label_flags}
