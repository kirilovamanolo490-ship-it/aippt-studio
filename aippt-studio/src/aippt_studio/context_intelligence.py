"""Local, incremental evidence retrieval; no model, network, or slide execution.

The catalog describes XML/text structure. A retrieved portfolio deck has not
been visually reviewed, approved, or adopted. Work metadata and retrospectives
are separate evidence sources; conflicting completion states never confer trust.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from .knowledge import RETROSPECTIVE_READER_VERSION, context_concepts, match_context_text, matched_case_history, retrospective_sections
from .models import utc_now
from .python_runtime import current_process_identity, validate_identity

SCHEMA_VERSION = 4
_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
       "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _require_host_native_python(parser: argparse.ArgumentParser) -> None:
    """Reject a translated standalone writer before it can update indexes."""

    reason = validate_identity(current_process_identity())
    if reason is not None:
        parser.error("Context intelligence requires a host-native Python: " + reason)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                     prefix=".context-", suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _safe_local(root: Path, locator: str) -> Path:
    path = (root / locator).resolve()
    if path == root or root not in path.parents:
        raise ValueError("source path is outside the workspace")
    return path


def _retrospective_links(root: Path) -> dict[str, dict]:
    index = root / "任务知识库" / "INDEX.md"
    if not index.is_file():
        return {}
    rows: dict[str, dict] = {}
    pattern = r"^\|\s*(\d+)\s*\|[^|]*\|\s*([^|]+)\|\s*([^|]+)\|[^\n]*?\(([^)]+)\)"
    for match in re.finditer(pattern, index.read_text(encoding="utf-8-sig"), re.MULTILINE):
        try:
            path = (index.parent / match[4]).resolve()
            if index.parent.resolve() not in path.parents or not path.is_file() or path.suffix != ".md":
                continue
            relative = _relative(root, path)
        except ValueError:
            continue
        metadata = {"work_id": match[1], "index_title": match[2].strip(),
                    "index_status": match[3].strip().casefold()}
        if relative in rows:
            metadata["index_status"] = "conflicting_index"
        rows[relative] = metadata
    # Duplicate Work entries are not independent completed lessons.
    counts = Counter(item["work_id"] for item in rows.values())
    for item in rows.values():
        if counts[item["work_id"]] > 1:
            item["index_status"] = "conflicting_index"
    return rows


def _read_xml(archive: ZipFile, name: str) -> ET.Element:
    info = archive.getinfo(name)
    if info.file_size > 24_000_000:
        raise ValueError(f"oversized PPTX XML part: {name}")
    return ET.fromstring(archive.read(name))


def _slide_names(archive: ZipFile) -> list[str]:
    presentation = _read_xml(archive, "ppt/presentation.xml")
    relationships = _read_xml(archive, "ppt/_rels/presentation.xml.rels")
    targets = {}
    for rel in relationships.findall(f"{{{_REL}}}Relationship"):
        if rel.get("TargetMode") == "External":
            continue
        target = rel.get("Target", "")
        name = str(PurePosixPath(target.lstrip("/"))) if target.startswith("/") else str(PurePosixPath("ppt") / target)
        if ".." not in PurePosixPath(name).parts:
            targets[rel.get("Id")] = name
    return [targets[item.get(f"{{{_NS['r']}}}id")] for item in presentation.findall("p:sldIdLst/p:sldId", _NS)
            if item.get(f"{{{_NS['r']}}}id") in targets]


def extract_pptx_structure(path: Path) -> dict:
    """Read page order, text, object counts and declared geometry, without rendering."""
    with ZipFile(path) as archive:
        presentation = _read_xml(archive, "ppt/presentation.xml")
        size = presentation.find("p:sldSz", _NS)
        width, height = (int(size.get("cx", 0)), int(size.get("cy", 0))) if size is not None else (0, 0)
        slides = []
        for number, name in enumerate(_slide_names(archive), 1):
            slide = _read_xml(archive, name)
            texts, titles, objects = [], [], []
            counts: Counter = Counter()
            for shape in slide.findall(".//p:spTree//*", _NS):
                tag = shape.tag.rsplit("}", 1)[-1]
                if tag not in {"sp", "pic", "graphicFrame", "cxnSp", "grpSp"}:
                    continue
                shape_text = " ".join(item.text or "" for item in shape.findall(".//a:t", _NS)).strip()
                kind = {"sp": "shape", "pic": "picture", "cxnSp": "connector", "grpSp": "group", "graphicFrame": "graphic_frame"}[tag]
                if tag == "graphicFrame":
                    data = shape.find("a:graphic/a:graphicData", _NS)
                    uri = data.get("uri", "") if data is not None else ""
                    kind = "chart" if uri.endswith("/chart") else ("table" if uri.endswith("/table") else "graphic_frame")
                if shape_text and tag != "grpSp":
                    texts.append(shape_text)
                    if kind == "shape":
                        kind = "text_shape"
                    placeholder = shape.find("p:nvSpPr/p:nvPr/p:ph", _NS)
                    if placeholder is not None and placeholder.get("type") in {"title", "ctrTitle"}:
                        titles.append(shape_text)
                counts[kind] += 1
                xfrm = shape.find("p:spPr/a:xfrm", _NS)
                if xfrm is None:
                    xfrm = shape.find("p:xfrm", _NS)
                bounds = None
                if xfrm is not None:
                    off, ext = xfrm.find("a:off", _NS), xfrm.find("a:ext", _NS)
                    if off is not None and ext is not None:
                        bounds = {"x": int(off.get("x", 0)), "y": int(off.get("y", 0)),
                                  "width": int(ext.get("cx", 0)), "height": int(ext.get("cy", 0))}
                objects.append({"kind": kind, "text_preview": shape_text[:240], "bounds_emu": bounds})
            slides.append({"slide": number, "part": name, "title": (titles or texts or [""])[0][:300],
                           "title_source": "title_placeholder" if titles else "first_text_fallback",
                           "text": "\n".join(texts)[:12000], "object_counts": dict(counts), "objects": objects[:200]})
    from .source_semantics import analyze_source_semantics, slide_semantic_text
    semantics = analyze_source_semantics(path)
    semantic_slides = {item["slide"]: item for item in semantics.get("slides", [])}
    for slide in slides:
        native = semantic_slides.get(slide["slide"])
        if native is not None:
            slide["native_content"] = native
            # Existing matching reads slides.text; facts must reach that field
            # rather than sitting in an otherwise unsearchable sidecar.
            supplement = slide_semantic_text(native)
            if supplement:
                slide["text"] += "\n" + supplement
    return {"title": next((slide["title"] for slide in slides if slide["title"]), path.stem),
            "page_count": len(slides), "slide_size_emu": {"width": width, "height": height},
            "aspect_ratio": round(width / height, 4) if height else None,
            "slides": slides, "inspection": "ooxml_structure_only", "visually_reviewed": False,
            "source_semantics": {key: value for key, value in semantics.items() if key != "slides"}}


def _parse_document(path: Path, kind: str) -> dict:
    if kind in {"portfolio", "template"}:
        return extract_pptx_structure(path)
    if kind == "work":
        metadata = json.loads(path.read_text(encoding="utf-8-sig"))
        return {"title": str(metadata.get("name", path.parent.name)), "work_id": str(metadata.get("work_id", path.parent.name)),
                "status": metadata.get("status", "unknown"), "user_approved": metadata.get("user_approved") is True,
                "knowledge_status": metadata.get("knowledge_status", "unknown"),
                "closure_schema_version": int(metadata.get("closure_schema_version", 1)),
                "task_knowledge_entry": metadata.get("task_knowledge_entry"),
                "task_knowledge_entry_sha256": metadata.get("task_knowledge_entry_sha256")}
    text = path.read_text(encoding="utf-8-sig")
    heading = re.search(r"(?m)^#\s+(.+)$", text)
    status = re.search(r"(?m)^-\s*状态：\s*(\S+)", text)
    metadata = " ".join(match[2].strip() for match in re.finditer(
        r"(?m)^-\s*(任务类型|生产路线|受众|检索关键词)：\s*(.+)$", text))
    return {"title": heading[1].strip() if heading else path.stem,
            "status": status[1].casefold() if status else "unknown", "metadata": metadata,
            **retrospective_sections(text)}


def build_case_index(project_root: Path, *, index_path: Path | None = None, force: bool = False) -> dict:
    """Refresh only changed local documents; a missing source removes its cached row."""
    root = project_root.expanduser().resolve()
    destination = index_path or root / "任务知识库/catalog/case-index.json"
    old = {}
    try:
        previous = json.loads(destination.read_text(encoding="utf-8"))
        if previous.get("schema_version") == SCHEMA_VERSION and previous.get("workspace_root") == str(root):
            old = {item["source_path"]: item for item in previous.get("documents", [])}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    links = _retrospective_links(root)
    sources = [(path, "work") for path in sorted((root / "work").glob("*/work.json")) if path.parent.name.isdigit()]
    sources += [(root / name, "retrospective") for name in sorted(links)]
    # The root catalog lists presentation files stored directly under the
    # library's category folders. Completed portfolio examples are excluded
    # from that fast catalog and remain independently indexed below.
    library = root / "模板库"
    metadata_catalog = library / "CATALOG.jsonl"
    cataloged_paths: set[str] = set()
    if metadata_catalog.is_file():
        try:
            for line in metadata_catalog.read_text(encoding="utf-8-sig").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                raw = row.get("path") if isinstance(row, dict) else None
                if not isinstance(raw, str) or not raw.strip():
                    continue
                relative = Path(raw)
                if relative.is_absolute() or ".." in relative.parts:
                    continue
                parts = relative.parts[1:] if relative.parts[:1] == ("模板库",) else relative.parts
                if not parts or parts[0] == "作品集" or relative.suffix.casefold() not in {".ppt", ".pptx", ".pptm"}:
                    continue
                candidate = (library / Path(*parts)).resolve()
                try:
                    candidate.relative_to(library.resolve())
                except ValueError:
                    continue
                cataloged_paths.add(Path(*parts).as_posix())
        except OSError:
            cataloged_paths.clear()
    # Cataloged presentation files use fast filename/category search on demand.
    # Unlisted files and the independent portfolio subtree retain the historical
    # OOXML scan so an incomplete catalog does not hide otherwise useful assets.
    for scan_root in (library, root / "作品集"):
        for path in sorted(scan_root.rglob("*.pptx")):
            if not path.is_file() or path.name.startswith("~$"):
                continue
            try:
                library_relative = path.resolve().relative_to(scan_root.resolve()).as_posix()
            except ValueError:
                library_relative = ""
            if scan_root == library and library_relative in cataloged_paths:
                continue
            try:
                relative = path.resolve().relative_to(scan_root.resolve())
            except ValueError:
                continue
            if scan_root.name == "作品集" or "作品集" in relative.parts:
                kind = "portfolio"
            elif relative.parts[0] == "字体包":
                continue
            else:
                kind = "template"
            sources.append((path, kind))
    documents, errors = [], []
    stats = {"reused": 0, "reparsed": 0, "removed": 0}
    from .source_semantics import reader_cache_identity
    semantic_identity = reader_cache_identity()
    for path, kind in sources:
        try:
            relative = _relative(root, path)
            stat = path.stat()
            fingerprint = {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns}
            cached = old.get(relative, {})
            semantic_cache_current = kind not in {"portfolio", "template"} or cached.get("semantic_reader_identity") == semantic_identity
            retrospective_cache_current = kind != "retrospective" or cached.get("retrospective_reader_version") == RETROSPECTIVE_READER_VERSION
            reader_cache_current = semantic_cache_current and retrospective_cache_current
            if not force and reader_cache_current and cached.get("fingerprint") == fingerprint and cached.get("kind") == kind:
                document = dict(cached)
                stats["reused"] += 1
            else:
                digest = _sha(path)
                if not force and reader_cache_current and cached.get("source_sha256") == digest and cached.get("kind") == kind:
                    document = {**cached, "fingerprint": fingerprint}
                    stats["reused"] += 1
                else:
                    document = {"kind": kind, "source_path": relative, "source_sha256": digest,
                                "fingerprint": fingerprint, **_parse_document(path, kind)}
                    stats["reparsed"] += 1
            if kind in {"portfolio", "template"}:
                document["semantic_reader_identity"] = semantic_identity
                # Cover text may be a single decorative character or a
                # placeholder. The filename is a separate, exact source label
                # and often carries the real topic of a finished example.
                document["source_name"] = path.stem
            if kind == "retrospective":
                document["retrospective_reader_version"] = RETROSPECTIVE_READER_VERSION
                document.update(links[relative])
            documents.append(document)
        except (OSError, ValueError, KeyError, ET.ParseError) as error:
            errors.append({"source_path": str(path), "error": f"{type(error).__name__}: {error}"})
        except Exception as error:
            # A corrupt legacy deck must not prevent the other cases being indexed.
            errors.append({"source_path": str(path), "error": f"{type(error).__name__}: {error}"})
    stats["removed"] = len(set(old) - {item["source_path"] for item in documents})
    # The case index carries authored retrospectives and portfolio structures.
    # The Work corpus is a separate, source-safe category/evidence map.  Refresh
    # it from the same local maintenance/new-Work pass so it cannot become a
    # forgotten static report, while keeping either index useful if the other
    # has one malformed legacy artifact.
    try:
        from .work_corpus import build_work_corpus_index

        corpus = build_work_corpus_index(root)
        corpus_summary = {
            "status": corpus.get("status"),
            "index_path": corpus.get("index_path"),
            "statistics": corpus.get("statistics", {}),
            "error_count": len(corpus.get("errors", [])),
        }
        if corpus.get("status") == "partial":
            errors.extend({"source_path": "work-corpus-index", "error": item.get("reason", "classification failed")}
                          for item in corpus.get("errors", []) if isinstance(item, dict))
    except Exception as error:
        corpus_summary = {"status": "partial", "error_count": 1}
        errors.append({"source_path": "work-corpus-index", "error": f"{type(error).__name__}: {error}"})
    payload = {"schema_version": SCHEMA_VERSION, "generated_at": utc_now(), "workspace_root": str(root),
               "status": "ready" if not errors else "partial", "statistics": stats,
               "document_count": len(documents), "documents": documents, "errors": errors,
               "work_corpus": corpus_summary,
               "template_catalog": (_relative(root, metadata_catalog) if metadata_catalog.is_file() else None),
               "limitations": ["PPTX inspection reads text and OOXML geometry; no visual quality or semantic image judgment.",
                               "Unchanged files reuse a stat/ctime fingerprint and recorded hash; force=True rehashes and reparses.",
                               "A cataloged large template collection is searched through template_library, not this case index.",
                               "Historical content is advisory and never overrides the current request."]}
    _write_json(destination, payload)
    return payload


def recommend_upstream_tools(query: str, *, catalog: list[dict] | None = None) -> list[dict]:
    """Recommend actual registered operations; never infer execution from a checkout."""
    if catalog is None:
        from .upstream_tools import tool_catalog
        catalog = tool_catalog()
    needs = context_concepts(query)
    mapping = {"native_chart": {"chart", "author"}, "flow_diagram": {"diagram", "author"},
               "native_motion": {"animations", "native-motion", "animation-duration", "timeline"},
               "font_typography": {"font"}, "icon_asset": {"icon"}, "editable_authoring": {"author"},
               "media_inspection": {"inspect"}, "template_preservation": {"validate"}}
    wanted = set().union(*(mapping.get(need, set()) for need in needs)) if needs else set()
    results = []
    for row in catalog:
        provider = row["provider"]
        operations = row.get("operations", {})
        selected = sorted(wanted & set(operations))
        provider_requested = provider.casefold() in query.casefold()
        if not selected and (provider_requested or "upstream_integration" in needs):
            selected = [operation for operation in ("search", "read") if operation in operations]
        for operation in selected:
            resource = operation in {"read", "search"}
            results.append({"provider": provider, "operation": operation, "description": operations[operation],
                            "role": "resource_read" if resource else ("inspection" if operation in {"inspect", "validate", "animations"} else "generation_or_edit"),
                            "availability": "source_missing" if not row.get("source_present") else
                            ("source_available" if resource else ("requires_setup" if row.get("runtime_requirement") else "contract_available_runtime_unchecked")),
                            "source_present": bool(row.get("source_present")), "source_commit": row.get("source_commit"),
                            "runtime_requirement": row.get("runtime_requirement"), "license": row.get("license"),
                            "execution_profile": (row.get("execution_profile") if not resource else {
                                "catalog_state": "resource_access_only", "execution_kind": "source_resource_read",
                                "executed": False,
                                "proof": "A successful receipt proves only the bounded source read/search operation.",
                            }),
                            "executed": False, "adopted": False,
                            "invocation": f"upstream-call {provider} {operation} --request REQUEST.json --output NEW_WORK_DIRECTORY",
                            "next_check": "Inspect upstream-tools and doctor; an individual successful receipt is required to claim execution."})
    # Broad GitHub discovery must not push the brief's concrete chart/diagram
    # operations out of a bounded recommendation list.
    results.sort(key=lambda item: (item["role"] == "resource_read", not item["source_present"],
                                   item["provider"].casefold(), item["operation"]))
    return results[:18]


def _validated_lesson_matches(query: str, root: Path, lessons: list[dict], current_work_id: str | None) -> list[dict]:
    matches = []
    for lesson in lessons:
        if lesson.get("status") not in {"verified", "validated"} or str(lesson.get("work_id")) == str(current_work_id):
            continue
        locator = lesson.get("source", lesson.get("source_path"))
        digest = lesson.get("source_sha256")
        if not locator or not digest:
            continue
        try:
            source = _safe_local(root, locator)
            if not source.is_file() or _sha(source) != digest:
                continue
        except (OSError, ValueError):
            continue
        match = match_context_text(query, str(lesson.get("trigger", "")), str(lesson.get("lesson", "")))
        if match:
            matches.append({**lesson, **match, "advisory_only": True})
    return sorted(matches, key=lambda item: -item["score"])


def _retrospective_content_trust(root: Path, item: dict, work: dict) -> tuple[str, str]:
    """Authenticate retrospective bytes without treating them as user acceptance."""
    expected = work.get("task_knowledge_entry_sha256")
    if not expected:
        if work.get("closure_schema_version", 1) >= 2:
            return "pending_or_unverified", "missing_approval_hash"
        return "legacy_status_only", "legacy_entry_without_content_hash"
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
        return "pending_or_unverified", "invalid_approval_hash"
    try:
        entry = _safe_local(root, item["source_path"])
        approved_locator = work.get("task_knowledge_entry")
        if approved_locator and _safe_local(root, approved_locator) != entry:
            return "pending_or_unverified", "approval_entry_path_mismatch"
        # Read the current small Markdown file even for cached retrieval. The
        # catalog hash records what was indexed; the closure hash records approval.
        actual = _sha(entry)
        if actual != expected.lower() or actual != item.get("source_sha256"):
            return "pending_or_unverified", "approval_hash_mismatch"
    except (OSError, ValueError, TypeError, KeyError):
        return "pending_or_unverified", "approval_entry_unavailable"
    return "confirmed_completed", "approved_content_hash_matches"


def _current_original_user_acceptance(root: Path, work_id: object) -> dict:
    """Return the final-review proof required before legacy lesson text can recur.

    A Work's completed state, ``user_approved`` flag, and hash-bound
    retrospective each describe a distinct storage/lifecycle fact.  None is an
    original user statement.  Ask the project-review reader for the narrow,
    current proof instead and project only its source-safe scalar metadata into
    this general context response.
    """

    unavailable = {
        "state": "explicit_user_acceptance_evidence_unavailable",
        "accepted_episode_count": 0,
        "review_phase": None,
    }
    if not isinstance(work_id, str) or not work_id.isdigit():
        return unavailable
    try:
        from .project_learning import current_explicit_user_acceptance

        proof = current_explicit_user_acceptance(root, root / "work" / work_id)
    except (OSError, ValueError, TypeError, AttributeError, ImportError, KeyError):
        return unavailable
    if not isinstance(proof, dict):
        return unavailable
    state = proof.get("state")
    accepted = proof.get("accepted_episode_count")
    phase = proof.get("review_phase")
    result = {
        "state": state if isinstance(state, str) and re.fullmatch(r"[a-z_]{1,96}", state) else unavailable["state"],
        "accepted_episode_count": accepted if isinstance(accepted, int) and not isinstance(accepted, bool) and accepted >= 0 else 0,
        "review_phase": phase if phase in {None, "synthesis"} else None,
    }
    receipt = proof.get("review_receipt_sha256")
    if isinstance(receipt, str) and re.fullmatch(r"[0-9a-fA-F]{64}", receipt):
        result["review_receipt_sha256"] = receipt.lower()
    return result


def recall_context(query: str, project_root: Path, *, index_path: Path | None = None,
                   limit: int = 5, current_work_id: str | None = None, refresh: bool = True,
                   validated_lessons: list[dict] | None = None) -> dict:
    root = project_root.expanduser().resolve()
    destination = index_path or root / "任务知识库/catalog/case-index.json"
    if refresh or not destination.is_file():
        catalog = build_case_index(root, index_path=destination)
    else:
        catalog = json.loads(destination.read_text(encoding="utf-8"))
        if catalog.get("schema_version") != SCHEMA_VERSION or catalog.get("workspace_root") != str(root):
            raise ValueError("case catalog belongs to another workspace or schema")
    max_count = max(1, min(int(limit), 10))
    groups: dict[str, list] = {"completed_lessons": [], "unfinished_cases": [],
                               "portfolio_references": [], "template_references": []}
    works = {item["work_id"]: item for item in catalog["documents"] if item["kind"] == "work"}
    has_retrospective = {item["work_id"] for item in catalog["documents"] if item["kind"] == "retrospective"}
    for item in catalog["documents"]:
        work_id = item.get("work_id")
        if current_work_id is not None and work_id == str(current_work_id):
            continue
        if item["kind"] == "work" and work_id in has_retrospective:
            continue
        title = " ".join(str(item.get(key, "")) for key in ("title", "source_name", "metadata", "index_title"))
        body = "\n".join(str(item.get(key, "")) for key in ("summary", "reusable_experience", "failure_lessons", "case_history"))
        slide_matches = []
        if item["kind"] in {"portfolio", "template"}:
            for slide in item.get("slides", []):
                match = match_context_text(query, slide["title"], slide["text"])
                if match:
                    slide_matches.append({"slide": slide["slide"], "title": slide["title"],
                                          "object_counts": slide["object_counts"], **match})
            body = "\n".join(slide["text"] for slide in item.get("slides", []))
        scope_title = f"{item['title']} {item.get('source_name', '')} {item.get('index_title', '')}"
        match = match_context_text(query, title, body, scope_title=scope_title)
        if slide_matches:
            strongest_slide = max(slide_matches, key=lambda value: value["score"])
            if not match or strongest_slide["score"] > match["score"]:
                match = strongest_slide
        if not match:
            continue
        result = {key: item[key] for key in ("work_id", "title", "source_name", "source_path", "source_sha256", "status") if key in item}
        result.update({key: match[key] for key in ("score", "matched_terms", "matched_concepts")})
        result["advisory_only"] = True
        if item["kind"] in {"portfolio", "template"}:
            result.update({"page_count": item["page_count"], "slide_size_emu": item["slide_size_emu"],
                           "aspect_ratio": item["aspect_ratio"], "matching_slides": sorted(slide_matches, key=lambda value: -value["score"])[:5],
                           "inspection": "ooxml_structure_only", "visually_reviewed": False,
                           "reuse_boundary": "Inspect actual slides and license before adopting layout or media; client facts/images are not current-task evidence."})
            groups["portfolio_references" if item["kind"] == "portfolio" else "template_references"].append(result)
        else:
            work = works.get(work_id, {})
            completed_status = (item["kind"] == "retrospective" and item.get("index_status") == "completed"
                         and item.get("status") == "completed" and work.get("status") == "completed"
                         and work.get("user_approved") is True)
            trust, content_approval = (_retrospective_content_trust(root, item, work) if completed_status
                                       else ("pending_or_unverified", "work_completion_unconfirmed"))
            original_acceptance = _current_original_user_acceptance(root, work_id)
            explicit_acceptance = original_acceptance["state"] == "explicit_original_user_acceptance"
            # A final review authenticates an original user acceptance; the
            # entry hash authenticates the legacy text about to be returned.
            # Both are necessary.  ``completed`` / ``user_approved`` alone are
            # navigation metadata and must never restore reusable prose.
            completed = completed_status and trust == "confirmed_completed" and explicit_acceptance
            if completed:
                reuse_status = "accepted_lesson"
            elif completed_status and not explicit_acceptance:
                reuse_status = "completion_metadata_history"
            else:
                reuse_status = "not_reusable"
            result.update({"work_status": work.get("status", item.get("status", "unknown")), "trust": trust,
                           "retrospective_content_approval": content_approval,
                           "work_metadata_source": work.get("source_path"), "work_metadata_sha256": work.get("source_sha256"),
                           "original_user_acceptance": original_acceptance, "reuse_status": reuse_status})
            if completed:
                result.update({"summary": item.get("summary", ""), "reusable_experience": item.get("reusable_experience", ""),
                               "failure_lessons": item.get("failure_lessons", "")})
                history = matched_case_history(query, item.get("case_history", ""),
                                               source_path=item["source_path"], source_sha256=item["source_sha256"])
                if history:
                    result["case_history_context"] = history
                result["reuse_boundary"] = "Current final project review verified an original user acceptance; the hash-bound retrospective remains advisory and scoped to a comparable future case."
            elif completed_status and not explicit_acceptance:
                result["reuse_boundary"] = (
                    "Case navigation only; completed/user_approved metadata and a hash-bound retrospective do not establish "
                    "original user acceptance. Automatic lessons require a current final project review with verified original acceptance."
                )
            elif completed_status:
                result["reuse_boundary"] = (
                    "Case navigation only; current final review proves original user acceptance, but this retrospective is no longer "
                    "current and hash-bound, so its historical text cannot be reused."
                )
            else:
                # Preserve the pre-existing navigation view for Work cases that
                # are actually unfinished or mutually inconsistent.  The
                # completed-legacy branch above is deliberately narrower: it
                # never projects retrospective prose merely because closure
                # metadata exists.
                result["summary"] = item.get("summary", "")
                history = matched_case_history(query, item.get("case_history", ""),
                                               source_path=item["source_path"], source_sha256=item["source_sha256"])
                if history:
                    result["case_history_context"] = history
                result["reuse_boundary"] = "Case navigation only; incomplete/unconfirmed lessons require fresh verification."
            groups["completed_lessons" if completed else "unfinished_cases"].append(result)
    for key in groups:
        groups[key] = sorted(groups[key], key=lambda item: (-item["score"], item["source_path"]))[:max_count]
    tool_error = None
    try:
        recommendations = recommend_upstream_tools(query)
    except (OSError, ValueError, KeyError) as error:
        recommendations, tool_error = [], str(error)
    if re.search(r"微调|局部修订|局部修改|前后对比|未改区域|未修改页|不要动|pixelmatch|visual-diff", query, re.I):
        from .visual_diff import _provenance
        try:
            comparator = _provenance()
            recommendations.insert(0, {
                "provider": "pixelmatch", "operation": "compare", "role": "inspection",
                "description": "Compare rendered page changes and explicitly preserved regions after a local edit.",
                "availability": "vendored_contract_available_runtime_unchecked", "source_present": True,
                "source_commit": comparator["commit"], "license": comparator["license"],
                "executed": False, "adopted": False,
                "runtime_requirement": "Two hash-bound PNGs with matching measured renderer/font/settings signatures, plus host Node.",
                "invocation": "visual-diff --request REQUEST.json --output NEW_QA_DIRECTORY --json",
                "next_check": "Inspect the real difference image and its receipt; this is not an aesthetic score."})
        except (OSError, ValueError, KeyError) as error:
            tool_error = "; ".join(filter(None, [tool_error, "visual-diff: " + str(error)]))
    verified = _validated_lesson_matches(query, root, validated_lessons or [], current_work_id)[:max_count]
    try:
        from .work_corpus import recall_work_corpus

        # ``build_case_index`` above has already refreshed the shared corpus
        # during normal context creation.  A direct cached case-index read may
        # not have done so, therefore let the corpus refresh its own compact
        # evidence map in that path.  This never writes into a source Work.
        work_corpus = recall_work_corpus(query, root, current_work_id=current_work_id,
                                         limit=max_count, refresh=False)
    except (OSError, ValueError, KeyError, TypeError) as error:
        work_corpus = {
            "schema_version": 1, "status": "partial", "reusable_cases": [], "advisory_only": True,
            "error": f"{type(error).__name__}: {error}",
            "reuse_boundary": "Work corpus retrieval is unavailable; do not infer a historical case from directory names.",
        }
    from .authoring_policy import authoring_defaults
    from .evolution import directory as evolution_directory, load_guidance
    learned = load_guidance(root)
    from .project_learning import load_project_lessons
    project_learning = load_project_lessons(root, query, limit=max_count, current_work_id=current_work_id)
    # Expose the documented discovery entry in every new Work context. Historical
    # Work scripts remain task-specific candidates, never eligible lessons or
    # executable tool recommendations merely because the guide is present.
    work_code_guide = root / "aippt-studio" / "docs" / "WORK_CODE_REUSE.md"
    work_code_reuse = {
        "status": "discovery_only" if work_code_guide.is_file() else "unavailable",
        "guide_path": str(work_code_guide) if work_code_guide.is_file() else None,
        "guide_sha256": _sha(work_code_guide) if work_code_guide.is_file() else None,
        "reuse_boundary": "Use the guide to find historical Work code only when relevant; inspect current source, inputs, runtime, and QA before reuse. This is not a verified lesson or a registered Studio capability.",
    }
    return {"schema_version": SCHEMA_VERSION, "generated_at": utc_now(),
            "status": "matched" if any(groups.values()) or verified or project_learning["lessons"] or work_corpus.get("reusable_cases") else "no_match",
            "query_sha256": hashlib.sha256(query.strip().encode()).hexdigest(), "query_concepts": sorted(context_concepts(query)),
            "catalog_path": str(destination), "catalog_sha256": _sha(destination), "catalog_status": catalog["status"],
            "template_catalog": catalog.get("template_catalog"),
            "advisory_only": True, "precedence": ["current_user_request", "current_evidence", "user_authoring_defaults", "verified_rules", "historical_examples"],
            "user_authoring_defaults": authoring_defaults(root),
            "semantic_learning": {"status": learned["status"], "active_rules": len(learned["rules"]), "errors": learned["errors"],
                                  "repair_candidates_path": str(evolution_directory(root) / "repair-candidates.json"),
                                  "limits": learned["limits"]},
            **groups, "validated_lessons": verified, "project_learning": project_learning, "work_corpus": work_corpus,
            "work_code_reuse": work_code_reuse,
            "tool_recommendations": recommendations,
            "tool_catalog_error": tool_error, "limitations": catalog["limitations"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("build", "recall"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--index-path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--query", default="")
    parser.add_argument("--current-work-id")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    # Both standalone commands can refresh the shared case/work-corpus index:
    # recall_context refreshes the index by default even without --output.
    # Parse first so --help remains available under a translated interpreter.
    _require_host_native_python(parser)
    if args.command == "build":
        result = build_case_index(args.project_root, index_path=args.index_path or args.output, force=args.force)
    else:
        result = recall_context(args.query, args.project_root, index_path=args.index_path,
                                current_work_id=args.current_work_id, limit=args.limit)
        if args.output:
            _write_json(args.output, result)
    print(json.dumps(result if args.command == "recall" else
                     {key: value for key, value in result.items() if key != "documents"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
