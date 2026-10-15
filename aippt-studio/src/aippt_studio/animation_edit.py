"""Read native timelines and make bounded, auditable edits without rebuilding chains.

OfficeCLI creates new entrance effects and validates candidates. Duration-only
edits use a byte-preserving OOXML patch because upstream indexed Set rebuilds
the target's chain. Unsupported timing graphs stay inspectable, never guessed.
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import shutil
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as ET

from .storage import write_json

NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
BEHAVIORS = {"anim", "animEffect", "animMotion", "animRot", "animScale", "animClr"}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _xml(data: bytes):
    root = ET.fromstring(data, ET.XMLParser(resolve_entities=False, no_network=True, remove_comments=True))
    if root.getroottree().docinfo.doctype:
        raise ValueError("DTD-bearing XML is unsupported")
    return root


def _package(path: Path) -> dict[str, bytes]:
    with ZipFile(path) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("duplicate package entries are unsupported")
        if sum(i.file_size for i in archive.infolist()) > 100_000_000:
            raise ValueError("animation inspection supports packages up to 100 MB uncompressed")
        return {i.filename: archive.read(i) for i in archive.infolist()}


def _slides(parts: dict[str, bytes]) -> list[str]:
    rels = {r.get("Id"): r for r in _xml(parts["ppt/_rels/presentation.xml.rels"])}
    names = []
    for slide in _xml(parts["ppt/presentation.xml"]).findall("p:sldIdLst/p:sldId", NS):
        rel = rels[slide.get(f"{{{NS['r']}}}id")]
        if rel.get("TargetMode") == "External":
            raise ValueError("external slide relationships are unsupported")
        target = rel.get("Target", "")
        name = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "ppt/" + target)
        if not name.startswith("ppt/slides/") or name not in parts:
            raise ValueError("invalid slide relationship")
        names.append(name)
    return names


def _duration(effect) -> tuple[int | None, list, str | None]:
    if len(effect.xpath(".//p:cTn[@presetClass]", namespaces=NS)):
        return None, [], "nested effects require a dedicated timing adapter"
    if effect.xpath("./p:childTnLst/p:par | ./p:childTnLst/p:seq", namespaces=NS):
        return None, [], "nested timing groups require a dedicated adapter"
    nodes = []
    for behavior in effect.iter():
        if ET.QName(behavior).namespace == NS["p"] and ET.QName(behavior).localname in BEHAVIORS:
            node = behavior.find("p:cBhvr/p:cTn", NS)
            if node is None or not (node.get("dur") or "").isdigit():
                return None, [], "behavior has implicit or indefinite duration"
            if any(c.get("delay", "0") != "0" or c.get("evt") for c in node.findall("p:stCondLst/p:cond", NS)):
                return None, [], "staggered behavior timing requires a dedicated adapter"
            nodes.append(node)
    values = {int(n.get("dur")) for n in nodes}
    if len(values) != 1 or not nodes or next(iter(values)) <= 0:
        return None, [], "unequal, zero, or missing behavior durations are not editable"
    value = next(iter(values))
    if effect.get("dur") is not None:
        if effect.get("dur") != str(value):
            return None, [], "effect duration differs from its behavior durations"
        nodes.append(effect)
    return value, nodes, None


def _start_conditions(effect) -> list[dict]:
    chain = []
    for node in [effect, *effect.iterancestors()]:
        if node.tag != f"{{{NS['p']}}}cTn":
            continue
        if node.get("nodeType") in {"mainSeq", "interactiveSeq", "tmRoot"}:
            break
        chain.append({"node_id": node.get("id"), "node_type": node.get("nodeType"),
                      "conditions": [dict(c.attrib) for c in node.findall("p:stCondLst/p:cond", NS)]})
    return chain


def inspect_animations(path: Path) -> dict:
    parts = _package(path)
    slides = []
    for index, name in enumerate(_slides(parts), 1):
        root = _xml(parts[name])
        objects = []
        tree = root.find("p:cSld/p:spTree", NS)
        top_shapes = tree.findall("p:sp", NS) if tree is not None else []
        if tree is not None:
            for element in tree.iter():
                if ET.QName(element).localname not in {"sp", "pic", "graphicFrame", "grpSp", "cxnSp"}:
                    continue
                ids = element.xpath("./*/p:cNvPr", namespaces=NS)
                if not ids:
                    continue
                nv = ids[0]
                objects.append({"shape_id": nv.get("id"), "name": nv.get("name", ""),
                                "type": ET.QName(element).localname,
                                "text": "".join(element.xpath(".//a:t/text()", namespaces=NS)),
                                "officecli_shape_index": top_shapes.index(element) + 1 if element in top_shapes else None})
        animations = []
        for effect in root.xpath("./p:timing//p:cTn[@presetClass]", namespaces=NS):
            targets = sorted(set(effect.xpath(".//p:spTgt/@spid", namespaces=NS)))
            duration, nodes, reason = _duration(effect)
            if len(targets) != 1:
                reason = "effect must target exactly one object"
            animations.append({"animation_id": effect.get("id"), "shape_ids": targets,
                               "preset_class": effect.get("presetClass"), "preset_id": effect.get("presetID"),
                               "trigger": effect.get("nodeType"), "duration_ms": duration,
                               "duration_seconds": duration / 1000 if duration is not None else None,
                               "duration_editable": reason is None, "unsupported_reason": reason,
                               "duration_node_ids": [n.get("id") for n in nodes],
                               "start_conditions": _start_conditions(effect),
                               "timing_xml": ET.tostring(effect.getparent(), encoding="unicode")})
        slides.append({"slide": index, "part": name, "objects": objects, "animations": animations})
    return {"source_sha256": _sha(path), "slides": slides,
            "verification": "OOXML inspection; playback has not been verified"}


def _milliseconds(value, *, positive: bool = False) -> int:
    if isinstance(value, bool):
        raise ValueError("seconds must be a finite number")
    try:
        number = Decimal(str(value)) * 1000
    except InvalidOperation as error:
        raise ValueError("seconds must be numeric") from error
    if not number.is_finite() or number != number.to_integral_value() or abs(number) > 3_600_000:
        raise ValueError("seconds must resolve to whole milliseconds within one hour")
    if positive and number <= 0:
        raise ValueError("duration must be positive")
    return int(number)


def _select(report: dict, request: dict):
    slide = request.get("slide")
    if type(slide) is not int or not 1 <= slide <= len(report["slides"]):
        raise ValueError("slide must be an existing 1-based slide number")
    page = report["slides"][slide - 1]
    selectors = [k for k in ("shape_id", "shape_name", "shape_text") if k in request]
    if len(selectors) != 1:
        raise ValueError("specify exactly one of shape_id, shape_name, shape_text")
    key = selectors[0]
    field = {"shape_id": "shape_id", "shape_name": "name", "shape_text": "text"}[key]
    found = [o for o in page["objects"] if o[field] == str(request[key])]
    if len(found) != 1:
        raise ValueError("object selector is missing or ambiguous; inspect and use shape_id")
    return page, found[0]


def _patch_duration(source: Path, output: Path, page: dict, animation: dict, new_ms: int) -> dict:
    parts = _package(source)
    raw = parts[page["part"]]
    root = _xml(raw)
    effects = root.xpath("./p:timing//p:cTn[@id=$id]", namespaces=NS, id=animation["animation_id"])
    if len(effects) != 1:
        raise ValueError("timing IDs must be unique")
    old_ms, nodes, reason = _duration(effects[0])
    if reason:
        raise ValueError(reason)
    changed = []
    for node in nodes:
        node_id = node.get("id")
        if not node_id or not node_id.isdigit() or not node.prefix:
            raise ValueError("duration node needs an explicit namespace and numeric ID")
        if len(root.xpath(".//p:cTn[@id=$id]", namespaces=NS, id=node_id)) != 1:
            raise ValueError("duplicate timing node ID")
        tag = re.escape(node.prefix.encode()) + rb":cTn"
        matches = [m for m in re.finditer(rb"<" + tag + rb"\b[^>]*>", raw)
                   if re.search(rb'\bid\s*=\s*([\"\x27])' + node_id.encode() + rb'\1', m.group())]
        if len(matches) != 1:
            raise ValueError("cannot uniquely locate original duration bytes")
        match = matches[0]
        replacement, count = re.subn(rb'(\bdur\s*=\s*[\"\x27])\d+([\"\x27])',
                                    lambda m: m[1] + str(new_ms).encode() + m[2], match.group())
        if count != 1:
            raise ValueError("cannot uniquely patch duration attribute")
        raw = raw[:match.start()] + replacement + raw[match.end():]
        changed.append(node_id)
    # Only the selected attribute bytes change; all other ZIP payloads and
    # slide bytes (including delays, triggers and timing IDs) are preserved.
    with ZipFile(source) as src, ZipFile(output, "w") as dst:
        dst.comment = src.comment
        for item in src.infolist():
            dst.writestr(item, raw if item.filename == page["part"] else parts[item.filename])
    actual = _package(output)
    expected = dict(parts)
    expected[page["part"]] = raw
    if actual != expected:
        raise ValueError("package preservation verification failed")
    return {"before_ms": old_ms, "after_ms": new_ms, "changed_duration_node_ids": changed,
            "all_other_xml_bytes_preserved": True, "writer": "Studio bounded OOXML duration adapter"}


def run_animation_operation(operation: str, request: dict, output: Path) -> dict:
    if operation == "animation-batch":
        return run_animation_batch(request, output)
    from .edits import apply_pptx_operations
    from .models import PptxEditOperation
    from .renderer import officecli_validate

    allowed = {"source"} if operation == "animations" else {
        "source", "source_sha256", "slide", "shape_id", "shape_name", "shape_text"}
    allowed |= ({"animation_id", "delta_seconds", "duration_seconds"} if operation == "animation-duration"
                else {"effect", "duration_seconds", "delay_seconds", "trigger", "direction"} if operation == "animation-add" else set())
    if set(request) - allowed:
        raise ValueError(f"unknown animation fields: {sorted(set(request) - allowed)}")
    source = Path(request["source"]).expanduser().resolve()
    snapshot = output / "input.pptx"
    shutil.copy2(source, snapshot)
    before = inspect_animations(snapshot)
    write_json(output / "before.json", before)
    if operation == "animations":
        return {"artifact": "before.json", "source_sha256": before["source_sha256"], "status": "inspected"}
    if request.get("source_sha256") != before["source_sha256"]:
        raise ValueError("source changed or source_sha256 missing; inspect the current file first")
    page, shape = _select(before, request)
    candidate = output / "candidate.pptx"
    staging = output / "staged.pptx"
    if operation == "animation-duration":
        found = [a for a in page["animations"] if a["animation_id"] == str(request.get("animation_id"))
                 and a["shape_ids"] == [shape["shape_id"]]]
        if len(found) != 1 or not found[0]["duration_editable"]:
            raise ValueError("animation is missing, ambiguous, or has unsupported timing; inspect before editing")
        anim = found[0]
        if ("delta_seconds" in request) == ("duration_seconds" in request):
            raise ValueError("provide exactly one of delta_seconds or duration_seconds")
        new_ms = (anim["duration_ms"] + _milliseconds(request["delta_seconds"]) if "delta_seconds" in request
                  else _milliseconds(request["duration_seconds"], positive=True))
        if not 0 < new_ms <= 3_600_000:
            raise ValueError("resulting duration must be positive and at most one hour")
        result = _patch_duration(snapshot, staging, page, anim, new_ms)
        after = inspect_animations(staging)
        edited = next(a for a in after["slides"][page["slide"] - 1]["animations"] if a["animation_id"] == anim["animation_id"])
        if edited["duration_ms"] != new_ms:
            raise ValueError("duration read-back mismatch")
    else:
        # The initial native-add contract is intentionally small and exercised
        # on the host binary. It does not advertise every upstream preset.
        effect = request.get("effect", "fade")
        trigger = request.get("trigger", "onClick")
        if effect not in {"fade", "wipe"} or trigger not in {"onClick", "afterPrevious", "withPrevious"}:
            raise ValueError("supported effects: fade/wipe; triggers: onClick/afterPrevious/withPrevious")
        if shape["officecli_shape_index"] is None:
            raise ValueError("animation-add currently supports top-level text/shapes only")
        if any(shape["shape_id"] in a["shape_ids"] for a in page["animations"]):
            raise ValueError("animation-add requires an object without existing effects")
        new_ms = _milliseconds(request.get("duration_seconds", 0.5), positive=True)
        delay = _milliseconds(request.get("delay_seconds", 0))
        if delay < 0:
            raise ValueError("delay cannot be negative")
        props = {"effect": effect, "class": "entrance", "duration": new_ms, "delay": delay, "trigger": trigger}
        if "direction" in request:
            if effect != "wipe" or request["direction"] not in {"left", "right", "up", "down"}:
                raise ValueError("direction applies only to wipe: left/right/up/down")
            props["direction"] = request["direction"]
        op = PptxEditOperation(id="add-native-entrance", action="add", element="animation",
                               target=f"/slide[{page['slide']}]/shape[{shape['officecli_shape_index']}]", properties=props)
        results, validation = apply_pptx_operations(snapshot, staging, [op])
        after = inspect_animations(staging)
        effects = [a for a in after["slides"][page["slide"] - 1]["animations"] if a["shape_ids"] == [shape["shape_id"]]]
        if len(effects) != 1 or effects[0]["duration_ms"] != new_ms:
            raise ValueError("added animation duration read-back mismatch")
        expected_trigger = {"onClick": "clickEffect", "afterPrevious": "afterEffect", "withPrevious": "withEffect"}[trigger]
        start_delays = [c.get("delay", "0") for n in effects[0]["start_conditions"] for c in n["conditions"]]
        actual_delay = sum(int(d) for d in start_delays if d.isdigit())
        if effects[0]["trigger"] != expected_trigger or actual_delay != delay:
            raise ValueError("added animation trigger/delay read-back mismatch")
        for old_page, new_page in zip(before["slides"], after["slides"], strict=True):
            if old_page["objects"] != new_page["objects"]:
                raise ValueError("adding animation unexpectedly changed slide objects")
            for existing in old_page["animations"]:
                if existing not in new_page["animations"]:
                    raise ValueError("adding animation changed an existing effect")
        result = {"writer": "OfficeCLI", "operations": results, "validation": validation,
                  "after_ms": new_ms, "animation_id": effects[0]["animation_id"]}
    before_validation = _sha(staging)
    validation = officecli_validate(staging)
    if _sha(staging) != before_validation:
        raise ValueError("validation unexpectedly changed the candidate")
    staging.replace(candidate)
    write_json(output / "after.json", after)
    result.update(artifact="candidate.pptx", status="candidate_requires_review", validation=validation,
                  source_sha256=before["source_sha256"], output_sha256=_sha(candidate),
                  bridge_sha256=_sha(Path(__file__)), playback_verified=False,
                  note="Native PPTX candidate only; existing job deck and approvals are unchanged. Review before adopting.")
    return result


def run_animation_batch(request: dict, output: Path) -> dict:
    """Plan or atomically publish bounded duration edits against one snapshot.

    All IDs and absolute target durations are resolved before any patch. This
    does not insert/delete/reorder effects or silently rebuild a timing graph.
    """
    from .renderer import officecli_validate

    allowed = {"source", "source_sha256", "edits", "dry_run", "expected_plan_sha256"}
    if set(request) - allowed:
        raise ValueError("unknown animation batch fields")
    dry_run = request.get("dry_run", False)
    if type(dry_run) is not bool:
        raise ValueError("dry_run must be boolean")
    edits = request.get("edits")
    if not isinstance(edits, list) or not 1 <= len(edits) <= 100:
        raise ValueError("edits must contain 1-100 duration changes")
    snapshot = output / "input.pptx"
    shutil.copy2(Path(request["source"]).expanduser().resolve(), snapshot)
    before = inspect_animations(snapshot)
    write_json(output / "before.json", before)
    if request.get("source_sha256") != before["source_sha256"]:
        raise ValueError("source changed or source_sha256 missing; inspect the current file first")
    planned, resolved, seen = [], [], set()
    fields = {"slide", "shape_id", "shape_name", "shape_text", "animation_id", "delta_seconds", "duration_seconds"}
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict) or set(edit) - fields:
            raise ValueError(f"edits[{index}] has unsupported fields")
        page, shape = _select(before, edit)
        found = [a for a in page["animations"] if a["animation_id"] == str(edit.get("animation_id"))
                 and a["shape_ids"] == [shape["shape_id"]]]
        if len(found) != 1 or not found[0]["duration_editable"]:
            raise ValueError(f"edits[{index}] animation is missing, ambiguous, or has unsupported timing")
        animation = found[0]
        key = (page["part"], animation["animation_id"])
        if key in seen:
            raise ValueError("duplicate animation target in batch; provide one final duration per effect")
        seen.add(key)
        if ("delta_seconds" in edit) == ("duration_seconds" in edit):
            raise ValueError("provide exactly one of delta_seconds or duration_seconds")
        new_ms = (animation["duration_ms"] + _milliseconds(edit["delta_seconds"]) if "delta_seconds" in edit
                  else _milliseconds(edit["duration_seconds"], positive=True))
        if not 0 < new_ms <= 3_600_000:
            raise ValueError("resulting duration must be positive and at most one hour")
        planned.append({"slide": page["slide"], "shape_id": shape["shape_id"], "shape_name": shape["name"],
                        "animation_id": animation["animation_id"], "before_ms": animation["duration_ms"],
                        "after_ms": new_ms, "delta_ms": new_ms-animation["duration_ms"],
                        "trigger": animation["trigger"], "start_conditions": animation["start_conditions"]})
        resolved.append((page, animation, new_ms))
    plan = {"source_sha256": before["source_sha256"], "changes": planned, "scope": "duration attributes only"}
    plan_sha = hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    if "expected_plan_sha256" in request and request["expected_plan_sha256"] != plan_sha:
        raise ValueError("plan changed; inspect the batch preview again")
    plan["plan_sha256"] = plan_sha
    write_json(output / "plan.json", plan)
    if dry_run:
        return {"status": "planned", "artifact": "plan.json", "plan_sha256": plan_sha,
                "source_sha256": before["source_sha256"], "changes": planned,
                "candidate_generated": False, "note": "Plan only; Office validation and playback have not run."}
    staging = output / "edit-staging"
    staging.mkdir()
    current = snapshot
    for index, (page, animation, new_ms) in enumerate(resolved):
        next_path = staging / f"step-{index+1:03}.pptx"
        _patch_duration(current, next_path, page, animation, new_ms)
        if current != snapshot:
            current.unlink()
        current = next_path
    after = inspect_animations(current)
    for change in planned:
        actual = [a for a in after["slides"][change["slide"]-1]["animations"]
                  if a["animation_id"] == change["animation_id"] and a["shape_ids"] == [change["shape_id"]]]
        if len(actual) != 1 or actual[0]["duration_ms"] != change["after_ms"]:
            raise ValueError("batch duration read-back mismatch")
    candidate_sha = _sha(current)
    validation = officecli_validate(current)
    if _sha(current) != candidate_sha:
        raise ValueError("validation unexpectedly changed the candidate")
    candidate = output / "candidate.pptx"
    current.replace(candidate)
    write_json(output / "after.json", after)
    return {"status": "candidate_requires_review", "artifact": "candidate.pptx", "changes": planned,
            "source_sha256": before["source_sha256"], "output_sha256": candidate_sha,
            "plan_sha256": plan_sha, "validation": validation, "all_other_xml_bytes_preserved": True,
            "writer": "Studio bounded OOXML duration batch adapter", "bridge_sha256": _sha(Path(__file__)),
            "playback_verified": False, "note": "Batch candidate only; job deck and approvals are unchanged."}
