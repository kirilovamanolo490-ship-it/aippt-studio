"""Reuse locked ppt-master readers for source facts, never layout decisions.

The five original MIT reader modules execute in a private module namespace.
No global sys.path changes, clone/export code, model, API, or source writes are
involved. Their bytes and commit remain visible in every successful report.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from threading import RLock
import types
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from .config import PROJECT_ROOT
from .upstreams import upstream_report

SCHEMA_VERSION = 1
READER_FILES = ("ooxml.py", "edit_safety.py", "chart_read.py", "diagram_read.py", "analyzer.py")
_CACHE: dict = {}
_LOCK = RLock()


class SourceIdentityChanged(ValueError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _upstream_row(*, verify: bool) -> dict:
    return upstream_report(identifier="ppt-master", verify=verify)[0]


def _stat(path: Path) -> tuple:
    try:
        value = path.stat()
        return str(path), value.st_size, value.st_mtime_ns, value.st_ctime_ns
    except OSError:
        return str(path), None


def reader_cache_identity() -> dict:
    """Cheap per-index fingerprint; source/lock/restoration changes invalidate it."""
    try:
        row = _upstream_row(verify=False)
        root = Path(row["path"]) if row.get("path") else PROJECT_ROOT / ".missing-ppt-master"
        package = root / "skills/ppt-master/scripts/pptx_ooxml"
        paths = [Path(__file__), PROJECT_ROOT / "upstreams.lock.json", root / ".git/HEAD", root / ".git/index",
                 root / ".git/config", root / ".git/packed-refs", root / "LICENSE",
                 *[package / name for name in READER_FILES], *sorted((root / ".git/refs/heads").rglob("*"))]
        identity = (row.get("source_present"), row.get("commit_sha"), tuple(_stat(p) for p in paths))
        return {"schema_version": SCHEMA_VERSION, "source_present": bool(row.get("source_present")),
                "fingerprint_sha256": hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()}
    except (OSError, ValueError, KeyError) as exc:
        return {"schema_version": SCHEMA_VERSION, "source_present": False, "error": type(exc).__name__ + ": " + str(exc)}


def _backend() -> tuple[dict, dict]:
    """Cache code only while lock, Git identity and reader file identity match."""
    with _LOCK:
        row = _upstream_row(verify=False)
        if not row.get("source_present") or not row.get("path"):
            raise FileNotFoundError("Locked ppt-master checkout is unavailable")
        root = Path(row["path"])
        package = root / "skills/ppt-master/scripts/pptx_ooxml"
        files = [package / name for name in READER_FILES]
        identity = [PROJECT_ROOT / "upstreams.lock.json", root / ".git/HEAD", root / ".git/index",
                    root / ".git/config", root / ".git/packed-refs", root / "LICENSE"]
        identity.extend(sorted((root / ".git/refs/heads").rglob("*")))
        signature = (row.get("commit_sha"), tuple(_stat(p) for p in [*identity, *files]))
        if _CACHE.get("signature") == signature:
            return _CACHE["modules"], _CACHE["provenance"]
        verified = _upstream_row(verify=True)
        if not verified.get("verification", {}).get("matches_lock"):
            raise ValueError("ppt-master source identity is dirty or differs from upstreams.lock.json")
        code = {path.stem: path.read_bytes() for path in files}
        hashes = {name + ".py": hashlib.sha256(data).hexdigest() for name, data in code.items()}
        namespace = "_aippt_pptmaster_readers_" + hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()[:20]
        modules = {}
        # Do not import upstream __init__: it also exposes clone/write helpers.
        package_module = types.ModuleType(namespace)
        package_module.__path__ = []
        sys.modules[namespace] = package_module
        try:
            for name, data in code.items():
                module = types.ModuleType(namespace + "." + name)
                module.__package__ = namespace
                module.__file__ = str(package / (name + ".py"))
                sys.modules[module.__name__] = module
                exec(compile(data, module.__file__, "exec"), module.__dict__)
                modules[name] = module
            if signature != (row.get("commit_sha"), tuple(_stat(p) for p in [*identity, *files])):
                raise ValueError("Upstream reader source changed during loading")
        except Exception:
            for name in [namespace, *[namespace + "." + n for n in code]]:
                sys.modules.pop(name, None)
            raise
        provenance = {"provider": "ppt-master", "entrypoint": "pptx_ooxml.analyzer.analyze_pptx",
                      "source_commit": verified["commit_sha"], "source_url": verified["source_url"],
                      "license": verified["license"], "module_directory": str(package),
                      "module_sha256": hashes, "source_modified": False,
                      "loading": "private module namespace; original reader bytes; no sys.path mutation"}
        _CACHE.clear()
        _CACHE.update(signature=signature, modules=modules, provenance=provenance)
        return modules, provenance


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _cache_details(chart: ET.Element, ns: dict) -> dict:
    """Expose literal display caches and formulas without evaluating a workbook."""
    series = []
    for index, node in enumerate(chart.findall(".//c:ser", ns), 1):
        fields = {}
        for name in ("tx", "cat", "val", "xVal", "yVal", "bubbleSize"):
            parent = node.find("c:" + name, ns)
            if parent is None:
                continue
            caches = []
            for cache in parent.iter():
                if _local(cache.tag) not in {"strCache", "numCache", "strLit", "numLit"}:
                    continue
                points = [{"index": point.get("idx"), "value": point.findtext("c:v", default="", namespaces=ns)}
                          for point in cache.findall("c:pt", ns)]
                declared = cache.find("c:ptCount", ns)
                count = declared.get("val") if declared is not None else None
                caches.append({"kind": _local(cache.tag), "declared_count": count, "points": points})
            direct = parent.findtext("c:v", namespaces=ns)
            fields[name] = {"formulas": [value.text or "" for value in parent.findall(".//c:f", ns)],
                            "caches": caches, "direct_value": direct,
                            "status": "cached" if caches else "literal" if direct is not None else "missing_cache"}
        series.append({"series_index": index, "fields": fields})
    missing = [f"series[{s['series_index']}].{name}" for s in series for name, field in s["fields"].items()
               if name != "tx" and field["status"] == "missing_cache"]
    incomplete = []
    for item in series:
        for name, field in item["fields"].items():
            for cache in field["caches"]:
                declared = cache["declared_count"]
                if declared is not None and (not declared.isdigit() or int(declared) != len(cache["points"])):
                    incomplete.append(f"series[{item['series_index']}].{name}")
    return {"series": series, "status": "missing_cache" if missing else "partial_cache" if incomplete else "cached" if series else "no_series",
            "missing_cache_fields": missing, "incomplete_cache_fields": incomplete,
            "formulas_evaluated": False, "workbook_checked": False, "cache_freshness": "not_verified"}


def _literal_values(field: dict) -> list[str]:
    if field.get("direct_value") is not None:
        return [str(field["direct_value"]) ]
    caches = field.get("caches", [])
    return [str(point["value"]) for point in caches[0]["points"]] if caches else []


def _factual_chart(chart: dict, evidence: dict) -> dict:
    value = {key: chart[key] for key in ("chart_id", "chart_kind", "chart_type", "plot_types", "edit_capability") if key in chart}
    value.update(evidence)
    series = []
    categories = []
    for raw in evidence.get("display_cache", {}).get("series", []):
        fields = raw["fields"]
        names = _literal_values(fields.get("tx", {}))
        current_categories = _literal_values(fields.get("cat", {}))
        if not categories:
            categories = current_categories
        series.append({"name": names[0] if names else None, "name_source": "literal_or_display_cache" if names else "unavailable",
                       "values": _literal_values(fields.get("val", {})), "categories": current_categories,
                       "value_representation": "exact_lexical_cache_values"})
    value.update(categories=categories, category_count=len(categories), series=series, series_count=len(series))
    return value


def _chart_evidence(path: Path, modules: dict) -> tuple[dict, list[dict]]:
    reader, safety = modules["ooxml"], modules["edit_safety"]
    result, problems = {}, []
    with ZipFile(path) as archive:
        for slide in reader._parse_slide_refs(archive):
            xml = reader._read_xml(archive, slide.part_name)
            rels = reader._slide_relationships(archive, slide.rels_name)
            for order, frame in enumerate(safety._chart_frames(xml), 1):
                object_id, _ = reader._shape_identity(frame, order)
                chart_id = f"s{slide.index:02d}_ch{object_id}"
                kind, rid = safety._chart_reference(frame)
                rel = rels.get(rid, {})
                payload = {"slide_part": slide.part_name, "shape_id": object_id, "relationship_id": rid}
                try:
                    if kind != "classic" or not rel.get("target"):
                        raise ValueError("ChartEx or missing chart relationship is not covered by classic chart reader")
                    part = reader._normalize_part(rel["target"], slide.part_name)
                    payload.update(chart_part=part, chart_part_sha256=hashlib.sha256(archive.read(part)).hexdigest())
                    payload["display_cache"] = _cache_details(reader._read_xml(archive, part), reader.NS)
                    if payload["display_cache"]["status"] in {"missing_cache", "partial_cache", "no_series"}:
                        problems.append({"slide": slide.index, "object_id": object_id, "code": payload["display_cache"]["status"]})
                except (KeyError, ValueError, RuntimeError, ET.ParseError) as exc:
                    payload["display_cache"] = {"status": "unavailable", "error": str(exc), "formulas_evaluated": False, "workbook_checked": False}
                    problems.append({"slide": slide.index, "object_id": object_id, "code": "chart_cache_unavailable"})
                result[chart_id] = payload
    return result, problems


def analyze_source_semantics(pptx: Path) -> dict:
    path = Path(pptx).resolve()
    result = {"schema_version": SCHEMA_VERSION, "status": "unavailable", "source_sha256": None,
              "source_bytes": None, "provider": None, "slides": [], "issues": [],
              "coverage": {"scope": "native table cells/merges, classic chart display caches, SmartArt text and connections",
                           "visual_reviewed": False, "formulas_evaluated": False, "cache_freshness_verified": False,
                           "images_understood": False, "page_roles_inferred": False, "edit_authorized": False}}
    try:
        result.update(source_sha256=_sha(path), source_bytes=path.stat().st_size)
        with ZipFile(path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 200 * 1024 * 1024:
                raise ValueError("Expanded PPTX exceeds the 200 MB source reader limit")
            if any(item.file_size > 24_000_000 for item in archive.infolist() if item.filename.endswith(".xml")):
                raise ValueError("PPTX XML part exceeds the 24 MB source reader limit")
            presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
            expected_slides = len(presentation.findall("{http://schemas.openxmlformats.org/presentationml/2006/main}sldIdLst/{http://schemas.openxmlformats.org/presentationml/2006/main}sldId"))
        modules, provenance = _backend()
        result["provider"] = dict(provenance)
        original = modules["analyzer"].analyze_pptx(path)
        charts, problems = _chart_evidence(path, modules)
        if len(original["slides"]) != expected_slides:
            problems.append({"code": "unresolved_slide_relationships", "expected_slides": expected_slides, "read_slides": len(original["slides"])})
        for slide in original["slides"]:
            selected_charts = []
            for chart in slide["charts"]:
                value = _factual_chart(chart, charts.get(chart["chart_id"], {}))
                selected_charts.append(value)
            diagrams = [{key: diagram[key] for key in ("diagram_id", "kind", "shape_id", "shape_name", "root_ids", "nodes", "connections", "text_items", "node_count", "text_count", "connection_count", "text_extracted", "has_persisted_drawing", "has_fallback_preview", "status", "warnings") if key in diagram}
                        for diagram in slide["diagrams"]]
            for diagram in diagrams:
                if diagram.get("status") != "ok" or diagram.get("warnings"):
                    problems.append({"slide": slide["slide_index"], "object_id": diagram.get("shape_id"), "code": "smartart_partial", "details": diagram.get("warnings", [])})
            result["slides"].append({"slide": slide["slide_index"], "tables": slide["tables"], "charts": selected_charts, "diagrams": diagrams})
        if _sha(path) != result["source_sha256"]:
            raise SourceIdentityChanged("PPTX source changed during semantic extraction; all extracted facts discarded")
        result.update(status="partial" if problems else "complete", issues=problems)
    except SourceIdentityChanged as exc:
        result.update(status="unavailable", slides=[], issues=[{"code": "source_identity_changed", "details": str(exc)}])
    except Exception as exc:
        result.update(status="partial" if result["slides"] else "unavailable")
        result["issues"].append({"code": "source_semantics_unavailable", "details": f"{type(exc).__name__}: {exc}"})
    return result


def slide_semantic_text(slide: dict) -> str:
    """Searchable literal source content, not generated descriptions or facts."""
    lines = []
    for table in slide.get("tables", []):
        lines.append("表格 " + str(table["table_id"]))
        for row in table.get("rows", []):
            lines.append(" | ".join(str(cell.get("text", "")) for cell in row["cells"] if not cell.get("is_merge_slave")))
    for chart in slide.get("charts", []):
        lines.append("图表显示缓存 " + str(chart.get("chart_type") or chart.get("chart_kind") or "unknown"))
        # Read raw cache values instead of upstream's fallback series names or
        # numeric conversion; this retains precision, cache gaps and formulas.
        for series in chart.get("display_cache", {}).get("series", []):
            for name, field in series["fields"].items():
                if field.get("direct_value") is not None:
                    lines.append(str(field["direct_value"]))
                for cache in field["caches"]:
                    lines.append(" | ".join(str(point["value"]) for point in cache["points"]))
    for diagram in slide.get("diagrams", []):
        lines.append("SmartArt 原生关系图")
        lines.extend(str(value) for value in diagram.get("text_items", []))
    return "\n".join(lines)


def semantic_text(report: dict) -> str:
    lines = ["\n\n## 原生内容语义补充", f"提取状态：{report['status']}；图表值来自显示缓存，未计算公式、未核对工作簿或确认缓存时效。"]
    for slide in report.get("slides", []):
        text = slide_semantic_text(slide)
        if text:
            lines.extend([f"\n### 第 {slide['slide']} 页", text])
    if report.get("issues"):
        lines.append("提取限制：" + "; ".join(str(issue.get("code")) for issue in report["issues"]))
    return "\n".join(lines)
