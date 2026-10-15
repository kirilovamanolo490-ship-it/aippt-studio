"""Bind source-preserving template adaptation to the normal Work build path.

The binding is a reproducible build source, not an approval. The adapter emits
the same artifact contract as other engines; normal visual/final gates remain.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import uuid
from zipfile import BadZipFile, ZipFile

from .adapter_contracts import (AdapterArtifact, AdapterBuildRequest, AdapterBuildResult,
    AdapterFailure, AdapterStatus, EditabilityClass, EngineReceipt)
from .config import WORKSPACE_ROOT
from .models import DeckPlan, InputRole, JobState, ProductionRoute, SourceRecord
from .motion_clone import _read_xml, _safe_entries, _slide_objects, _slide_order
from .renderer import sha256
from .storage import job_dir, load_job, locked_job, read_json, read_model, save_job, write_json
from .style_hybrid_workflow import _content_audit, _source, build_hybrid_candidate
from .template_content_replace import _image_refs

BINDING = "planning/hybrid-template.json"
RECEIPT = "build/hybrid-template-receipt.json"
ENGINE = "studio-source-preserving-hybrid"


def _canonical(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def _inside(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("hybrid artifact must stay inside the job without symlink escape")
    return path


def _selected(root: Path, roles: dict) -> SourceRecord:
    selected = [key for key, value in roles.items() if value == InputRole.TEMPLATE]
    if len(selected) != 1:
        raise ValueError("hybrid binding requires one already selected template source")
    source = read_model(root / "normalized/sources" / f"{selected[0]}.json", SourceRecord)
    stored = _inside(root, source.stored_path)
    if not stored.is_file() or sha256(stored) != source.sha256:
        raise ValueError("selected template bytes changed; ingest/select the current source first")
    return source


def _sources_current(packet: dict, library: Path) -> None:
    _source(library, packet["primary_source_path"], packet["primary_source_sha256"], require_template=True)
    for item in packet.get("page_overrides", []):
        _source(library, item["donor_source_path"], item["donor_source_sha256"],
                require_template=item.get("layout_only_reference") is not True)


def _materialize_inputs(root: Path, packet: dict) -> dict:
    """Keep build inputs in the job once; don't depend on a temporary preview."""
    packet = copy.deepcopy(packet)
    inputs: list[dict] = []
    def store(raw: str) -> Path:
        source = Path(raw).expanduser().resolve(strict=True)
        if not source.is_file():
            raise ValueError("hybrid dependency must be a regular file")
        digest = sha256(source)
        target = _inside(root, f"assets/hybrid-inputs/{digest}{source.suffix.lower()}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source, target)
        if sha256(target) != digest:
            raise ValueError("stored hybrid dependency changed")
        inputs.append({"path": target.relative_to(root).as_posix(), "sha256": digest})
        return target
    reviews = list(packet.get("reviewed_primary_pages", []))
    reviews += [item["reviewed_donor_page"] for item in packet.get("page_overrides", [])]
    for review in reviews:
        preview = read_json(Path(review["preview_receipt"]).expanduser())
        png = store(preview["output_png"])
        if sha256(png) != preview.get("output_sha256"):
            raise ValueError("source-page preview changed before binding")
        preview["output_png"] = str(png)
        saved = _inside(root, f"assets/hybrid-inputs/{_canonical(preview)}.preview.json")
        write_json(saved, preview)
        review["preview_receipt"] = str(saved)
        inputs.append({"path": saved.relative_to(root).as_posix(), "sha256": sha256(saved)})
    for item in packet.get("image_replacements", []):
        raw = item.get("image_path", item.get("path"))
        if not raw:
            raise ValueError("image replacement requires image_path")
        item["image_path"] = str(store(raw))
        item.pop("path", None)
    packet["bound_dependencies"] = sorted({item["path"]: item for item in inputs}.values(), key=lambda i: i["path"])
    return packet


@locked_job
def bind_hybrid_template(job_id: str, request: dict) -> dict:
    job, root = load_job(job_id), job_dir(job_id)
    if job.route != ProductionRoute.TEMPLATE or job.state not in {
            JobState.BRIEF_REVIEW, JobState.FULL_BUILD, JobState.FINAL_REVIEW, JobState.BLOCKED}:
        raise ValueError("bind a hybrid template to a selected template job with a current deck plan, before delivery")
    if job.state == JobState.BLOCKED and job.resume_state != JobState.FULL_BUILD:
        raise ValueError("resolve the current non-build blocker before binding a hybrid template")
    plan = read_model(root / "planning/deck-plan.json", DeckPlan)
    source = _selected(root, job.input_roles)
    if request.get("primary_source_sha256") != source.sha256:
        raise ValueError("hybrid primary must match this Work's selected template")
    library = WORKSPACE_ROOT / "模板库"
    _sources_current(request, library)
    primary = _source(library, request["primary_source_path"], source.sha256, require_template=True)
    if primary["catalog"]["slide_count"] != len(plan.slides):
        raise ValueError("whole-template hybrid page count must match the current deck plan")
    packet = _materialize_inputs(root, request)
    for override in packet.get("page_overrides", []):
        override["omit_donor_notes"] = True
        override["donor_notes_disposition"] = "同一 job 构建将在换版后逐页写入当前 deck plan 的演讲备注，不继承供体旧项目讲稿。"
    binding = {"schema": "aippt.work-hybrid.v1", "job_id": job.id,
               "template_source_id": source.id, "template_source_sha256": source.sha256,
               "deck_plan_sha256": sha256(root / "planning/deck-plan.json"), "request": packet}
    write_json(root / BINDING, binding)
    # Do not erase the current usable deck: the stale binding/plan checks keep
    # it out of promotion, and successful normal promotion replaces it atomically.
    job.approvals.update({"prototype": False, "final": False})
    job.approval_hashes.pop("prototype", None)
    job.approval_hashes.pop("final", None)
    if job.state != JobState.BRIEF_REVIEW:
        job.state = JobState.FULL_BUILD
        job.resume_state = None
        job.blocked_reason = None
        job.failure = None
    save_job(job)
    return {"job_id": job.id, "state": job.state.value, "build_source": BINDING,
            "sha256": sha256(root / BINDING), "engine": ENGINE,
            "next": "run the existing job; inspect its actual pages in final review",
            "visual_approved": False}


def _binding_current(root: Path, job_id: str, roles: dict, plan: DeckPlan) -> dict:
    binding = read_json(_inside(root, BINDING))
    source = _selected(root, roles)
    if (binding.get("schema") != "aippt.work-hybrid.v1"
            or binding.get("job_id") != job_id or binding.get("template_source_id") != source.id
            or binding.get("template_source_sha256") != source.sha256
            or binding.get("deck_plan_sha256") != sha256(root / "planning/deck-plan.json")):
        raise ValueError("hybrid binding is stale for this job, template or deck plan; bind again")
    packet = binding["request"]
    if packet.get("primary_source_sha256") != source.sha256:
        raise ValueError("bound hybrid primary does not match the selected template")
    _sources_current(packet, WORKSPACE_ROOT / "模板库")
    for item in packet.get("bound_dependencies", []):
        path = _inside(root, item["path"])
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise ValueError("hybrid input image or source preview changed after binding")
    return binding


def _copy_check(deck: Path, plan: DeckPlan, packet: dict) -> dict:
    """Every source text/picture needs a replacement or a specific disposition."""
    blockers: list[dict] = []
    audit = _content_audit(deck)
    if audit["flagged_slide_count"]:
        blockers.append({"reason": "common placeholder text remains", "audit": audit})
    mapped = {(item["slide"], str(item["shape_id"]), kind)
              for key, kind in (("text_replacements", "text"), ("image_replacements", "image"))
              for item in packet.get(key, [])}
    retained = {(item.get("slide"), str(item.get("shape_id")), item.get("kind")): item
                for item in packet.get("retained_objects", []) if isinstance(item, dict)}
    normalize = lambda text: re.sub(r"\s+", "", text)
    with ZipFile(deck) as archive:
        entries = _safe_entries(archive)
        _, parts = _slide_order(archive, entries)
        if len(parts) != len(plan.slides):
            raise ValueError("hybrid output page count differs from current plan")
        for number, (part, slide) in enumerate(zip(parts, plan.slides, strict=True), 1):
            page = _read_xml(archive, entries, part)
            image_digests: dict[str, list[str]] = {}
            for ref in _image_refs(archive, entries, part, page, number):
                image_digests.setdefault(ref["shape_id"], []).append(
                    hashlib.sha256(archive.read(ref["media_part"])).hexdigest())
            paragraphs = {
                normalize("".join(node.xpath(".//a:t/text()", namespaces={"a": "http://schemas.openxmlformats.org/drawingml/2006/main"})))
                for node in page.xpath(".//a:p", namespaces={"a": "http://schemas.openxmlformats.org/drawingml/2006/main"})
            }
            for copy_text in [slide.title, *slide.points]:
                if normalize(copy_text) and normalize(copy_text) not in paragraphs:
                    blockers.append({"slide": number, "reason": "current planned copy is absent", "text": copy_text})
            for shape_id, shape in _slide_objects(page).items():
                if shape.tag.rsplit("}", 1)[-1] == "grpSp":
                    continue
                value = "".join(shape.xpath(".//a:t/text()", namespaces={"a": "http://schemas.openxmlformats.org/drawingml/2006/main"})).strip()
                identities = {}
                if value:
                    identities["text"] = hashlib.sha256(value.encode()).hexdigest()
                if shape_id in image_digests:
                    # Include embedded bytes, not just a relationship ID: filled
                    # shapes are images too, and may also contain text.
                    identities["image"] = _canonical(image_digests[shape_id])
                for kind, digest in identities.items():
                    if (number, shape_id, kind) in mapped:
                        continue
                    keep = retained.get((number, shape_id, kind), {})
                    if keep.get("content_sha256") != digest or len(str(keep.get("reason", "")).strip()) < 8:
                        blockers.append({"slide": number, "shape_id": shape_id, "kind": kind,
                                         "content_sha256": digest, "text_excerpt": value[:120],
                                         "reason": "unmapped source object needs replacement or a specific retention reason"})
    return {"status": "passed" if not blockers else "needs_content_mapping", "blockers": blockers,
            "scope": "slide text and pictures; inherited visuals and truth still require actual whole-page review"}


def _parts(deck: Path) -> dict[str, str]:
    with ZipFile(deck) as archive:
        entries = _safe_entries(archive)
        return {name: hashlib.sha256(archive.read(name)).hexdigest() for name in sorted(entries)}


def build_bound_hybrid(request: AdapterBuildRequest, root: Path) -> AdapterBuildResult:
    try:
        plan = read_model(root / request.deck_plan, DeckPlan)
        binding = _binding_current(root, request.job_id, request.input_roles, plan)
        packet = binding["request"]
        stage = _inside(root, f"{request.output_root}/hybrid-{uuid.uuid4().hex[:12]}")
        stage.mkdir(parents=True)
        deck = stage / "deck.pptx"
        result = build_hybrid_candidate(WORKSPACE_ROOT / "模板库", packet, deck)
        from .hybrid_notes import update_hybrid_notes
        notes = update_hybrid_notes(deck, plan)
        copy_check = _copy_check(deck, plan, packet)
        record = {"schema": "aippt.work-hybrid-build.v1", "job_id": request.job_id,
                  "binding_sha256": sha256(root / BINDING),
                  "deck_plan_sha256": sha256(root / request.deck_plan),
                  "staging_candidate_at_build": deck.relative_to(root).as_posix(),
                  "output_sha256": sha256(deck),
                  "build": result, "notes": notes, "content_check": copy_check,
                  "package_parts": _parts(deck), "visual_approved": False}
        write_json(root / RECEIPT, record)
        if copy_check["status"] != "passed":
            raise ValueError(f"hybrid content mapping is incomplete; inspect {RECEIPT} and rebind corrected content")
        proof = {"kind": ENGINE, "status": "verified", "source_sha256": binding["template_source_sha256"],
                 "page_map_sha256": sha256(root / BINDING), "verified_slide_count": len(plan.slides),
                 "receipt_sha256": sha256(root / RECEIPT),
                 "pages": [{**origin, "deck_slide_id": slide.id} for origin, slide in
                           zip(result["page_origin_map"], plan.slides, strict=True)]}
        return AdapterBuildResult(status=AdapterStatus.SUCCEEDED,
            receipt=EngineReceipt(engine=ENGINE, version="1", command=["library-hybrid-bind", "run"],
                provenance={"kind": "studio_source", "module_sha256": sha256(Path(__file__)),
                            "builder_sha256": sha256(Path(__file__).with_name("style_hybrid_workflow.py"))}),
            editability=EditabilityClass.NATIVE_EDITABLE,
            artifacts=[AdapterArtifact(kind="pptx", path=deck.relative_to(root).as_posix(), sha256=sha256(deck)),
                       AdapterArtifact(kind="build_source", path=BINDING, sha256=sha256(root / BINDING))],
            warnings=["Source-preserving hybrid is structurally verified; inspect actual pages and native display as required."],
            metadata={"template_application": proof, "content_check": copy_check})
    except (OSError, ValueError, KeyError, TypeError, BadZipFile) as error:
        return AdapterBuildResult(status=AdapterStatus.BLOCKED,
            failure=AdapterFailure(category="hybrid_template", message=str(error), retryable=True,
                                   fallback="correct the bound request and use library-hybrid-bind again"))


def verify_hybrid_output(job, root: Path, plan: DeckPlan, proof: dict, map_sha: str, deck: Path) -> str | None:
    try:
        binding = _binding_current(root, job.id, job.input_roles, plan)
        if (proof.get("kind") != ENGINE or sha256(root / BINDING) != map_sha
                or proof.get("status") != "verified"
                or proof.get("source_sha256") != binding["template_source_sha256"]
                or proof.get("page_map_sha256") != map_sha
                or proof.get("verified_slide_count") != len(plan.slides)):
            raise ValueError("hybrid output must use the current bound build source")
        record = read_json(_inside(root, RECEIPT))
        if (sha256(root / RECEIPT) != proof.get("receipt_sha256")
                or record.get("job_id") != job.id or record.get("binding_sha256") != map_sha
                or record.get("deck_plan_sha256") != sha256(root / "planning/deck-plan.json")
                or record.get("content_check", {}).get("status") != "passed"):
            raise ValueError("hybrid build receipt is missing, stale or still has unmapped content")
        pages = [{**origin, "deck_slide_id": slide.id} for origin, slide in zip(
            record["build"]["page_origin_map"], plan.slides, strict=True)]
        if proof.get("pages") != pages:
            raise ValueError("hybrid page proof differs from its build receipt or current deck plan")
        if _parts(deck) != record.get("package_parts"):
            raise ValueError("hybrid output package differs from the actual source-preserving build")
        if _copy_check(deck, plan, binding["request"])["status"] != "passed":
            raise ValueError("hybrid current copy or retained source content no longer matches its binding")
    except (OSError, ValueError, KeyError, TypeError, BadZipFile) as error:
        return str(error)
    return None


def clean_promoted_hybrid_staging(root: Path, result: AdapterBuildResult) -> None:
    """Remove only hash-matching engine artifacts after successful promotion."""
    if result.metadata.get("template_application", {}).get("kind") != ENGINE:
        return
    folders: set[Path] = set()
    for artifact in result.artifacts:
        relative = Path(artifact.path)
        if len(relative.parts) < 4 or relative.parts[:2] != ("build", "adapter-staging"):
            continue
        if not re.fullmatch(r"hybrid-[0-9a-f]{12}", relative.parts[2]):
            continue
        try:
            path = _inside(root, artifact.path)
            if path.is_file() and sha256(path) == artifact.sha256:
                path.unlink()
                stop = root / "build/adapter-staging"
                parent = path.parent
                while parent != stop:
                    folders.add(parent)
                    parent = parent.parent
        except (OSError, ValueError) as error:
            # Promotion has committed. A cleanup problem must not turn that
            # successful publication into an apparent failed build.
            logging.getLogger(__name__).warning("Kept hybrid staging artifact %s: %s", artifact.path, error)
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()  # Never delete unknown files which appeared in staging.
        except OSError:
            pass
