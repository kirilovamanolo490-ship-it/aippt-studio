from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from PIL import Image

from aippt_studio.ingest import ingest_sources
from aippt_studio.models import DeckPlan, ExternalEngineManifest, JobState, ProductionRoute
from aippt_studio.renderer import sha256
from aippt_studio.storage import job_dir, read_model, write_json
from aippt_studio.workflow import approve_gate, create_job, export_job, resume_job, run_job, status_payload


@pytest.mark.parametrize(
    ("route", "upstream_name", "editable_class", "output_name", "manifest_key"),
    [
        (ProductionRoute.TEMPLATE, "pptx-automizer", "native_editable", "candidate_editable.pptx", "editable_pptx"),
        (ProductionRoute.REBUILD, "image/image-to-editable-ppt-skill", "reconstructed_editable", "candidate_editable.pptx", "editable_pptx"),
        (ProductionRoute.VISUAL, "image/codex-ppt-skill", "image_based", "candidate_visual_alternate.pptx", "visual_alternate_pptx"),
    ],
)
def test_manual_external_handoff_is_verified_and_labeled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    route: ProductionRoute,
    upstream_name: str,
    editable_class: str,
    output_name: str,
    manifest_key: str,
) -> None:
    upstream = tmp_path / "upstream"
    (upstream / upstream_name).mkdir(parents=True)
    monkeypatch.setenv("AIPPT_UPSTREAM_DIR", str(upstream))
    source = tmp_path / "notes.md"
    source.write_text("\n".join(f"第 {index} 项经营证据包含明确事实和下一步行动。" for index in range(1, 9)), encoding="utf-8")
    job = create_job("外部路线验收", job_id=f"external-{route.value}", route=route)
    ingest_sources(job.id, [str(source)])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    if route == ProductionRoute.TEMPLATE:
        # Do not approve a generic HTML prototype for a template job.  The
        # source-bound template adapter builds the first reviewable PPTX.
        assert run_job(job.id)["state"] == JobState.BLOCKED.value
    else:
        assert run_job(job.id)["state"] == JobState.PROTOTYPE_REVIEW.value
        approve_gate(job.id, "prototype")
        assert run_job(job.id)["state"] == JobState.BLOCKED.value

    root = job_dir(job.id)
    plan_path = root / "planning" / "deck-plan.json"
    plan = read_model(plan_path, DeckPlan)
    deck = root / "build" / "deck.pptx"
    deck.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    preview_hashes: dict[str, str] = {}
    for slide in plan.slides:
        preview = root / "build" / "previews" / f"{slide.id}.png"
        Image.new("RGB", (640, 360), "#12314a").save(preview)
        preview_hashes[slide.id] = sha256(preview)
    build_source = root / "build" / "external-source.json"
    build_source.write_text('{"rebuild":"source"}', encoding="utf-8")
    write_json(
        root / "build" / "external-engine-manifest.json",
        ExternalEngineManifest(
            job_id=job.id,
            route=route,
            engine=f"test-{route.value}",
            version="1.0.0",
            editable_class=editable_class,
            deck_plan_sha256=sha256(plan_path),
            pptx_sha256=sha256(deck),
            preview_hashes=preview_hashes,
            build_source="build/external-source.json",
            build_source_sha256=sha256(build_source),
        ),
    )

    assert resume_job(job.id)["state"] == JobState.FINAL_REVIEW.value
    approve_gate(job.id, "final")
    manifest = export_job(job.id, skip_powerpoint=True)

    assert manifest.status == "candidate"
    assert manifest.qa["editable_class"] == editable_class
    assert manifest.engines["renderer"] == f"test-{route.value} 1.0.0"
    assert manifest_key in manifest.outputs
    assert (root / "deliverables" / output_name).is_file()
    status = status_payload(read_model(root / "job.json", type(job)))
    assert status["artifacts"]["delivery"].endswith(output_name)
    if route == ProductionRoute.VISUAL:
        assert not (root / "deliverables" / "candidate_editable.pptx").exists()


def test_external_handoff_rejects_source_paths_in_speaker_notes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upstream = tmp_path / "upstream"
    (upstream / "pptx-automizer").mkdir(parents=True)
    monkeypatch.setenv("AIPPT_UPSTREAM_DIR", str(upstream))
    source = tmp_path / "notes.md"
    source.write_text("\n".join(f"第 {index} 项经营证据包含明确事实和下一步行动。" for index in range(1, 9)), encoding="utf-8")
    job = create_job("备注路径门禁", job_id="external-notes-gate", route=ProductionRoute.TEMPLATE)
    ingest_sources(job.id, [str(source)])
    run_job(job.id)
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == JobState.BLOCKED.value

    root = job_dir(job.id)
    plan_path = root / "planning" / "deck-plan.json"
    plan = read_model(plan_path, DeckPlan)
    deck = root / "build" / "deck.pptx"
    notes_xml = b'''<p:notes xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="3" name="Notes"/><p:cNvSpPr/><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>[Sources]</a:t></a:r></a:p><a:p><a:r><a:t>F:\\aippt\\jobs\\demo\\source.docx</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:notes>'''
    with zipfile.ZipFile(deck, "w") as archive:
        archive.writestr("ppt/notesSlides/notesSlide1.xml", notes_xml)
    preview_hashes: dict[str, str] = {}
    for slide in plan.slides:
        preview = root / "build" / "previews" / f"{slide.id}.png"
        Image.new("RGB", (640, 360), "#12314a").save(preview)
        preview_hashes[slide.id] = sha256(preview)
    build_source = root / "build" / "external-source.json"
    build_source.write_text('{"rebuild":"source"}', encoding="utf-8")
    write_json(
        root / "build" / "external-engine-manifest.json",
        ExternalEngineManifest(
            job_id=job.id,
            route=ProductionRoute.TEMPLATE,
            engine="test-template",
            version="1.0.0",
            editable_class="native_editable",
            deck_plan_sha256=sha256(plan_path),
            pptx_sha256=sha256(deck),
            preview_hashes=preview_hashes,
            build_source="build/external-source.json",
            build_source_sha256=sha256(build_source),
        ),
    )

    status = resume_job(job.id)

    assert status["state"] == JobState.BLOCKED.value
    assert "speaker notes" in status["blocked_reason"]
