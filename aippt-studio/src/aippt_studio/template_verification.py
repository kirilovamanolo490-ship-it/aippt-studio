from __future__ import annotations

"""Source-bound page mapping and output checks shared by the adapter and final gate."""

import hashlib
import posixpath
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


def _validate_mapping(
    mapping: dict[str, object],
    *,
    record: dict[str, object],
    source_hash: str,
    slide_count: int,
    plan: dict[str, object],
    plan_sha256: str,
) -> str | None:
    """Validate the map as a source-bound, complete page-level contract."""

    if mapping.get("template_source_id") != record.get("id"):
        return "template-map.json is not bound to the selected template source id"
    if mapping.get("template_source_sha256") != source_hash:
        return "template-map.json is stale or belongs to different template bytes"
    if mapping.get("template_source_slide_count") != slide_count:
        return "template-map.json slide-count binding does not match the selected template"
    if mapping.get("deck_plan_sha256") != plan_sha256:
        return "template-map.json is stale or belongs to a different deck plan"
    plan_slides = list(plan.get("slides") or [])
    expected_ids = [str(slide.get("id") or "") for slide in plan_slides]
    entries = mapping.get("slides")
    if not isinstance(entries, list):
        return "template-map.json must contain a slides list"
    actual_ids = [str(item.get("deck_slide_id") or "") for item in entries if isinstance(item, dict)]
    if actual_ids != expected_ids or len(entries) != len(expected_ids):
        return "template-map.json must map every deck-plan slide exactly once and in deck order"
    for entry in entries:
        if not isinstance(entry, dict):
            return "template-map.json slide entries must be objects"
        source_slide = entry.get("source_slide")
        if isinstance(source_slide, bool) or not isinstance(source_slide, int) or not 1 <= source_slide <= slide_count:
            return "template-map.json source_slide must be an integer within the selected template"
        if not isinstance(entry.get("replacements", {}), dict):
            return "template-map.json replacements must be an object"
        targets = entry.get("shape_targets", {})
        if not isinstance(targets, dict):
            return "template-map.json shape_targets must be an object"
        for field, target in targets.items():
            if (field not in {"title", "body", "subtitle"} or not isinstance(target, dict)
                    or not isinstance(target.get("shape_name"), str) or not target["shape_name"].strip()
                    or not isinstance(target.get("source_text"), str) or not target["source_text"].strip()):
                return "template-map.json shape_targets require a title/body/subtitle shape_name and source_text"
        preserved = entry.get("preserve_text_shapes", [])
        if not isinstance(preserved, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("shape_name"), str)
            or not item["shape_name"].strip() or not isinstance(item.get("source_text"), str)
            or not item["source_text"].strip() or not isinstance(item.get("reason"), str)
            or not item["reason"].strip() for item in preserved
        ):
            return "template-map.json preserve_text_shapes require shape_name, source_text, and reason"
    return None


def _slide_text(archive: zipfile.ZipFile, number: int) -> str:
    root = ElementTree.fromstring(archive.read(f"ppt/slides/slide{number}.xml"))
    return "".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))


def _text_shapes(archive: zipfile.ZipFile, part: str) -> dict[str, list[str]]:
    root = ElementTree.fromstring(archive.read(part))
    shapes: dict[str, list[str]] = {}
    for shape in root.iter():
        if not shape.tag.endswith("}sp"):
            continue
        identities = [node for node in shape.iter() if node.tag.endswith("}cNvPr")]
        if not identities:
            continue
        name = identities[0].attrib.get("name", "")
        value = "".join(node.text or "" for node in shape.iter() if node.tag.endswith("}t"))
        if name and value:
            shapes.setdefault(name, []).append(value)
    return shapes


def _text_shape_geometry(archive: zipfile.ZipFile, part: str) -> dict[str, list[tuple]]:
    root = ElementTree.fromstring(archive.read(part))
    result: dict[str, list[tuple]] = {}
    for shape in root.iter():
        if not shape.tag.endswith("}sp") or not any(node.tag.endswith("}t") for node in shape.iter()):
            continue
        identities = [node for node in shape.iter() if node.tag.endswith("}cNvPr")]
        if not identities:
            continue
        name = identities[0].attrib.get("name", "")
        if not name:
            continue
        positions = tuple(
            (node.tag.rsplit("}", 1)[-1], tuple(sorted(node.attrib.items())))
            for node in shape.iter() if node.tag.endswith("}off") or node.tag.endswith("}ext")
        )
        result.setdefault(name, []).append((identities[0].attrib.get("id"), positions))
    return result


def _related_part(archive: zipfile.ZipFile, part: str, kind: str) -> str:
    directory, name = posixpath.split(part)
    relationships = ElementTree.fromstring(archive.read(posixpath.join(directory, "_rels", name + ".rels")))
    targets = [node.attrib["Target"] for node in relationships
               if node.attrib.get("Type", "").endswith("/" + kind)]
    if len(targets) != 1:
        raise ValueError(f"{part} has no unique {kind} relationship")
    target = targets[0]
    return target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join(directory, target))


def _slide_visual_chain(archive: zipfile.ZipFile, slide_part: str) -> tuple[str, str, str]:
    """Hash the layout, normalized master, and theme used by a visible slide."""

    layout = _related_part(archive, slide_part, "slideLayout")
    master = _related_part(archive, layout, "slideMaster")
    theme = _related_part(archive, master, "theme")
    master_xml = ElementTree.fromstring(archive.read(master))
    for node in master_xml.iter():
        if node.tag.endswith("}sldLayoutId"):
            node.attrib.pop("id", None)  # imported layout sequence IDs change
        for key in list(node.attrib):
            if key.startswith("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"):
                node.attrib[key] = "REL"
    return (
        hashlib.sha256(archive.read(layout)).hexdigest(),
        hashlib.sha256(ElementTree.tostring(master_xml)).hexdigest(),
        hashlib.sha256(archive.read(theme)).hexdigest(),
    )


def _presentation_slide_names(archive: zipfile.ZipFile) -> list[str]:
    """Resolve visible slide order; ZIPs may retain unreferenced source slides."""

    presentation = ElementTree.fromstring(archive.read("ppt/presentation.xml"))
    relationships = ElementTree.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
    targets = {
        node.attrib["Id"]: node.attrib["Target"]
        for node in relationships
        if node.attrib.get("Type", "").endswith("/slide")
    }
    relationship_id = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    result: list[str] = []
    for node in presentation.iter():
        if not node.tag.endswith("}sldId"):
            continue
        target = targets[node.attrib[relationship_id]]
        part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(posixpath.join("ppt", target))
        if part not in archive.namelist():
            raise ValueError(f"presentation references missing slide {part}")
        result.append(part)
    return result


def _template_application_inputs(template: Path, mapping: dict, plan: dict) -> tuple[list[dict], str | None]:
    """Prove that each mapped page can receive current audience-facing copy."""

    by_id = {slide["id"]: slide for slide in plan.get("slides", [])}
    proof: list[dict] = []
    def normalized(value: str) -> str:
        return re.sub(r"\s+", "", value)

    with zipfile.ZipFile(template) as archive:
        for output_number, entry in enumerate(mapping["slides"], start=1):
            source_number = entry["source_slide"]
            source_text = _slide_text(archive, source_number)
            tags = set(re.findall(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}", source_text))
            shapes = _text_shapes(archive, f"ppt/slides/slide{source_number}.xml")
            planned = by_id[entry["deck_slide_id"]]
            replacements = entry.get("replacements", {})
            shape_targets = entry.get("shape_targets", {})
            preserve = {item["shape_name"]: item for item in entry.get("preserve_text_shapes", [])}
            if len(preserve) != len(entry.get("preserve_text_shapes", [])):
                return [], f"mapped page {output_number} has duplicate preserved text shape names"
            required = {"title"}
            if planned.get("points"):
                required.add("body")
            if str(planned.get("subtitle") or "").strip():
                required.add("subtitle")
            missing = []
            methods = {}
            for tag in sorted(required):
                copy = str(replacements.get(tag) or "").strip()
                if not copy:
                    missing.append(tag)
                    continue
                if tag in tags:
                    methods[tag] = "placeholder"
                    continue
                target = shape_targets.get(tag)
                if isinstance(target, dict):
                    shape_name = target["shape_name"]
                    matches = shapes.get(shape_name, [])
                    if (len(matches) == 1
                            and normalized(matches[0]) == normalized(target["source_text"])
                            and normalized(matches[0]) != normalized(copy)):
                        methods[tag] = "source_shape"
                        continue
                missing.append(tag)
            if missing:
                return [], (
                    f"mapped page {output_number} uses source page {source_number}, but cannot replace "
                    f"the current {'/'.join(missing)} copy; prepare matching placeholders or a "
                    "source-bound shape_targets map with each shape's original name and text"
                )
            target_names = [target["shape_name"] for target in shape_targets.values()]
            if len(target_names) != len(set(target_names)):
                return [], f"mapped page {output_number} maps multiple fields to one source text shape"
            for field, target in shape_targets.items():
                matches = shapes.get(target["shape_name"], [])
                if (field not in replacements or len(matches) != 1
                        or normalized(matches[0]) != normalized(target["source_text"])):
                    return [], f"mapped page {output_number} has an unverified {field} source text shape"
            for name, item in preserve.items():
                matches = shapes.get(name, [])
                if (len(matches) != 1 or normalized(matches[0]) != normalized(item["source_text"])
                        or name in target_names):
                    return [], f"mapped page {output_number} has an unverified preserved text shape {name}"
            unmanaged = []
            for name, values in shapes.items():
                if len(values) != 1:
                    unmanaged.append(name)
                    continue
                source_copy = values[0]
                remaining_copy = re.sub(r"\{\{[A-Za-z][A-Za-z0-9_]*\}\}", "", source_copy).strip()
                present_tags = set(re.findall(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}", source_copy))
                if present_tags and not present_tags.issubset(replacements):
                    unmanaged.append(name)
                elif name in target_names or name in preserve:
                    continue
                elif not present_tags or remaining_copy:
                    unmanaged.append(name)
            if unmanaged:
                return [], (
                    f"mapped page {output_number} retains unreviewed source text in shapes "
                    f"{', '.join(unmanaged[:5])}; replace it or declare preserve_text_shapes with a reason"
                )
            field_shapes = {}
            for field in required:
                if methods[field] == "source_shape":
                    field_shapes[field] = [shape_targets[field]["shape_name"]]
                else:
                    field_shapes[field] = sorted(
                        name for name, values in shapes.items()
                        if any("{{" + field + "}}" in value for value in values)
                    )
            proof.append({
                "deck_slide_id": entry["deck_slide_id"],
                "source_slide": source_number,
                "replacement_tags": sorted(required),
                "replacement_methods": methods,
                "field_shapes": field_shapes,
                "preserved_text_shapes": sorted(preserve),
            })
    return proof, None


def _verify_template_application(template: Path, output: Path, mapping: dict, proof: list[dict]) -> str | None:
    """Check actual output copy and inherited theme bytes after the adapter runs."""

    def normalized(value: str) -> str:
        return re.sub(r"\s+", "", value)

    with zipfile.ZipFile(template) as source, zipfile.ZipFile(output) as built:
        slide_names = _presentation_slide_names(built)
        if len(slide_names) != len(proof):
            return f"output has {len(slide_names)} visible slides, expected {len(proof)} mapped slides"
        for number, row in enumerate(proof, start=1):
            source_part = f"ppt/slides/slide{row['source_slide']}.xml"
            output_part = slide_names[number - 1]
            if _slide_visual_chain(source, source_part) != _slide_visual_chain(built, output_part):
                return f"output page {number} does not inherit its mapped source layout/master/theme"
            if _text_shape_geometry(source, source_part) != _text_shape_geometry(built, output_part):
                return f"output page {number} does not retain its mapped source text shape geometry"
            output_shapes = _text_shapes(built, output_part)
            source_shapes = _text_shapes(source, source_part)
            replacements = mapping["slides"][number - 1]["replacements"]
            for tag in row["replacement_tags"]:
                expected = normalized(str(replacements[tag]))
                for shape_name in row["field_shapes"][tag]:
                    values = output_shapes.get(shape_name, [])
                    if len(values) != 1 or expected not in normalized(values[0]) or "{{" in values[0]:
                        return f"output page {number} does not contain its mapped {tag} replacement in {shape_name}"
            for shape_name in row["preserved_text_shapes"]:
                if output_shapes.get(shape_name) != source_shapes.get(shape_name):
                    return f"output page {number} altered reviewed preserved text in {shape_name}"
    return None


