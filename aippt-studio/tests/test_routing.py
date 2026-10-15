from __future__ import annotations

from aippt_studio.models import InputRole, ProductionRoute, SourceRecord
from aippt_studio.routing import RoutePolicy, classify_input_roles, select_route
from aippt_studio.ingest import ingest_sources
from aippt_studio.storage import load_job
from aippt_studio.workflow import configure_route, create_job
from PIL import Image


def _source(source_id: str, kind: str, name: str) -> SourceRecord:
    return SourceRecord(
        id=source_id,
        original=name,
        stored_path=f"sources/{name}",
        kind=kind,
        sha256="a" * 64,
        size_bytes=1,
    )


def test_mandatory_template_selects_template_route() -> None:
    roles = {"ppt": InputRole.TEMPLATE}
    decision = select_route(roles, RoutePolicy())
    assert decision.route == ProductionRoute.TEMPLATE
    assert decision.explainable is True


def test_powerpoint_content_does_not_imply_template() -> None:
    roles = classify_input_roles([_source("ppt", "powerpoint", "prior-report.pptx")])
    assert roles["ppt"] == InputRole.CONTENT
    assert select_route(roles, RoutePolicy()).route == ProductionRoute.NATIVE


def test_scanned_pdf_selects_rebuild() -> None:
    roles = classify_input_roles([_source("pdf", "pdf", "scanned-contract.pdf")])
    assert roles["pdf"] == InputRole.FLATTENED_INPUT
    assert select_route(roles, RoutePolicy()).route == ProductionRoute.REBUILD


def test_visual_requires_explicit_acceptance() -> None:
    roles = {"ref": InputRole.VISUAL_REFERENCE}
    assert select_route(roles, RoutePolicy()).route == ProductionRoute.NATIVE
    assert select_route(roles, RoutePolicy(image_based_accepted=True)).route == ProductionRoute.VISUAL


def test_user_override_wins_and_is_recorded() -> None:
    decision = select_route(
        {"ppt": InputRole.TEMPLATE},
        RoutePolicy(override=ProductionRoute.REBUILD, override_reason="user requested reconstruction"),
    )
    assert decision.route == ProductionRoute.REBUILD
    assert decision.source == "user_override"
    assert "user requested" in decision.reason


def test_image_based_acceptance_persists_as_user_override(tmp_path) -> None:
    image = tmp_path / "reference.png"
    Image.new("RGB", (32, 18), "white").save(image)
    job = create_job("Visual route", job_id="visual-route-persistence")
    source = ingest_sources(job.id, [str(image)])[0]
    configured = configure_route(
        job.id,
        roles={source.id: InputRole.VISUAL_REFERENCE},
        image_based_accepted=True,
    )
    assert configured.route == ProductionRoute.VISUAL
    assert configured.route_source == "user_override"
    assert load_job(job.id).route == ProductionRoute.VISUAL
