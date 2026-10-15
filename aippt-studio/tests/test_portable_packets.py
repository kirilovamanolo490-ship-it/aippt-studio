from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest
from typer.testing import CliRunner

from aippt_studio import cli, portable_packets
from aippt_studio.portable_packets import (
    PortablePacketError,
    export_guidance_snapshot,
    import_packet,
    validate_handoff_bundle_v1,
    validate_packet_v1,
    write_guidance_snapshot,
)


def _work(root: Path, *, status: str = "in_progress") -> Path:
    work = root / "work" / "1"
    (work / "workspace").mkdir(parents=True)
    (work / "work.json").write_text(
        json.dumps({"work_id": "1", "status": status, "user_approved": False}),
        encoding="utf-8",
    )
    return work


def _set_work_status(work: Path, status: str) -> None:
    record = json.loads((work / "work.json").read_text(encoding="utf-8"))
    record["status"] = status
    (work / "work.json").write_text(json.dumps(record), encoding="utf-8")


def _source_revision() -> dict:
    return {
        "document_id": "ppt-host-document-77",
        "revision": "revision-4",
        "state": "observed",
        "evidence": ["host-visible-slide-state"],
    }


def _transfer_scope() -> dict:
    return {
        "mode": "sanitized_structured_summary_only",
        "deck_content_embedded": False,
        "speaker_notes_embedded": False,
        "asset_content_embedded": False,
        "raw_conversation_embedded": False,
    }


def _packet() -> dict:
    return {
        "schema": "aippt-learning-packet-v1",
        "packet_id": "office-packet-001",
        "created_at": "2026-09-22T00:00:00+00:00",
        "source_surface": "chatgpt-powerpoint",
        "claim_provenance": "source_report_unverified",
        "source_revision": _source_revision(),
        "transfer_scope": _transfer_scope(),
        "user_authorization": {
            "state": "recorded",
            "scope": "structured_summary_only",
            "evidence": ["current-task confirmation"],
        },
        "idempotency_key": "office-packet-001-revision-4",
        "transmission_state": "prepared_not_transmitted",
        "transmission_evidence": [],
        "presentation_intent_ref": "intent-001",
        "expression_review_ref": "review-001",
        "capability_report_ref": "capabilities-001",
        "guidance_snapshot_ref": {
            "snapshot_id": "guidance-001",
            "guidance_revision": "v1-0123456789abcdef",
            "state": "supplied_by_user",
        },
        "guidance_dispositions": [
            {
                "rule_id": "base:image_surface_geometry",
                "disposition": "applied",
                "evidence": ["visible crop matched the rounded frame"],
                "note": "The actual image edge was reviewed after the crop.",
            }
        ],
        "feedback_events": [
            {
                "event_id": "feedback-001",
                "kind": "feedback",
                "user_observation": "The photo edge conflicts with the rounded visual frame.",
                "context": {
                    "operation": "exact_local_revision",
                    "presentation_form": "business_report",
                    "scope": "slide 3 image",
                },
                "problem": "The visible photo retained exposed rectangular corners.",
                "correction": "Applied a visible crop matching the rounded frame.",
                "verification": {
                    "state": "visually_reviewed",
                    "evidence": ["active-slide view"],
                },
                "reusability": {
                    "state": "conditional_candidate",
                    "applies_when": "A photo sits in a curved visible frame.",
                    "exceptions": ["A rectangular source edge may be intentional evidence."],
                },
            }
        ],
        "tool_execution": {"state": "not_reported", "evidence": []},
        "artifact_review": {"state": "performed", "evidence": ["active-slide view"]},
        "user_acceptance": {
            "state": "not_recorded",
            "verification": "not_verified",
            "evidence": [],
        },
        "unresolved_items": ["Native export was not available in the current host."],
    }


def _presentation_intent() -> dict:
    return {
        "schema": "aippt-presentation-intent-v1",
        "intent_id": "intent-001",
        "created_at": "2026-09-22T00:00:00+00:00",
        "source_revision": _source_revision(),
        "operation": "exact_local_revision",
        "presentation_form": "business_report",
        "audience": "management meeting",
        "occasion": "quarterly review",
        "source_boundary": {
            "authoritative_material": ["active deck", "current source attachment"],
            "may_add_external_material": False,
            "external_material_authorization": "not_authorized",
        },
        "scope": {
            "target_slides": ["3"],
            "preserve": ["accepted slides", "existing navigation"],
            "unknowns": [],
            "preservation_basis": "current_request",
        },
        "page_messages": [
            {
                "page": "3",
                "message": "The comparison explains the operating change.",
                "evidence_role": "comparison chart and source-backed annotation",
                "neighbor_logic": "It follows the problem and prepares the implementation page.",
            }
        ],
        "capability_limits": [],
    }


def _capability_report() -> dict:
    return {
        "schema": "aippt-bridge-capabilities-v1",
        "report_id": "capabilities-001",
        "created_at": "2026-09-22T00:00:00+00:00",
        "source_surface": "chatgpt-powerpoint",
        "source_revision": _source_revision(),
        "operations": {
            "read_active_slide": {"state": "confirmed", "evidence": ["active slide content visible"]},
            "edit_text": {"state": "confirmed", "evidence": ["post-edit text visible"]},
            "edit_geometry": {"state": "unknown", "evidence": []},
            "render_page": {"state": "confirmed", "evidence": ["host page preview"]},
            "save_reopen_pptx": {"state": "not_attempted", "evidence": []},
            "export_or_inspect_pdf": {"state": "unavailable", "evidence": ["export control not exposed"]},
            "animation_playback": {"state": "not_attempted", "evidence": []},
            "transmit_handoff": {"state": "not_attempted", "evidence": []},
        },
        "bridge_mode": "manual_packet",
        "connected_apps": [],
        "limitations": ["No receiver integration is visible in this host."],
    }


def _expression_review() -> dict:
    return {
        "schema": "aippt-expression-review-v1",
        "review_id": "review-001",
        "created_at": "2026-09-22T00:00:00+00:00",
        "document_revision": {
            "document_id": "ppt-host-document-77",
            "pre_review_revision": "revision-3",
            "reviewed_revision": "revision-4",
            "binding_state": "bound_to_observed_revision",
            "evidence": ["host preview after edit"],
        },
        "review_scope": ["slide 3"],
        "visual_inspection": {"state": "performed", "evidence": ["active-slide preview"]},
        "observations": [
            {
                "scope": "slide 3 image and supporting text",
                "visible_observation": "The original rectangular image edge was exposed beyond the rounded visual frame.",
                "message_effect": "The mismatch distracted from the evidence comparison.",
                "decision": "revised",
                "revision_or_reason": "The visible image crop now follows the intended rounded edge.",
                "evidence": ["post-edit active-slide preview"],
            }
        ],
        "adjacent_page_check": {
            "state": "performed",
            "result": "The revised comparison follows the problem page and prepares the next action page.",
            "evidence": ["three-slide overview"],
        },
        "preservation_check": {
            "state": "not_comparable",
            "evidence": ["host exposes no before-and-after non-target comparison"],
            "non_target_slides": {
                "state": "not_comparable",
                "result": "Preservation intent was followed, but non-target pages could not be independently compared.",
                "evidence": ["host limitation"],
            },
        },
        "invalidated_by_changes": ["none"],
        "review_state": "reviewed_current_revision",
        "unverified_limits": ["Native save and reopen were not tested."],
    }


def _bundle() -> dict:
    packet = _packet()
    return {
        "schema": "aippt-handoff-bundle-v1",
        "bundle_id": "office-bundle-001",
        "created_at": "2026-09-22T00:00:00+00:00",
        "source_surface": "chatgpt-powerpoint",
        "claim_provenance": "source_report_unverified",
        "source_revision": copy.deepcopy(packet["source_revision"]),
        "transfer_scope": copy.deepcopy(packet["transfer_scope"]),
        "packet": packet,
        "presentation_intent": _presentation_intent(),
        "expression_review": _expression_review(),
        "capability_report": _capability_report(),
    }


def _write_json(path: Path, payload: dict) -> bytes:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_packet_import_freezes_a_self_contained_external_bundle_without_promoting_it(tmp_path: Path):
    work = _work(tmp_path)
    work_record_before = (work / "work.json").read_bytes()
    events = work / "workspace" / "learning-events.jsonl"
    events.write_bytes(b'{"existing":"local-only"}\n')
    events_before = events.read_bytes()
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())

    result = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert result["status"] == "received_external_unverified"
    assert result["storage_mode"] == "descriptor_bound"
    assert result["bundle_written"] is True
    assert result["triage_state"] == "needs_source_corroboration"
    assert result["truth_boundaries"] == {
        "evidence_class": "external_handoff",
        "raw_conversation_provenance": "absent",
        "automatic_learning": "blocked",
        "acceptance_claim": "untrusted",
        "tool_execution_claim": "unverified",
        "capability_claim": "untrusted",
        "visual_review_claim": "untrusted",
        "authorization_claim": "untrusted",
        "attachment_transfer": "unsupported_in_source_bundle_v1",
    }
    frozen = work / result["bundle_path"]
    receipt = json.loads((work / result["receipt_path"]).read_text(encoding="utf-8"))
    assert frozen.read_bytes() == raw
    assert receipt["receipt_state"] == "received_external_unverified"
    assert receipt["bundle_id"] == "office-bundle-001"
    assert receipt["bundle_sha256"] == result["bundle_sha256"]
    assert receipt["triage"] == [{"feedback_index": 0, "state": "needs_source_corroboration"}]
    assert (work / "work.json").read_bytes() == work_record_before
    assert events.read_bytes() == events_before
    assert not (work / "workspace" / "feedback-audit").exists()
    assert not (work / "workspace" / "conversations").exists()

    repeated = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")
    assert repeated["status"] == "already_received"
    assert repeated["storage_mode"] == "descriptor_bound"
    assert repeated["bundle_written"] is False


def test_packet_import_rejects_idempotency_reuse_with_different_bundle_bytes(tmp_path: Path):
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())
    first = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    conflicting = _bundle()
    conflicting["packet"]["feedback_events"][0]["problem"] = "A different observation with the same handoff identity."
    _write_json(source, conflicting)

    with pytest.raises(PortablePacketError, match="already received with different bundle bytes"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    receipt_root = work / "workspace" / "bridge-packets" / "v1"
    bundle_directories = [item for item in receipt_root.iterdir() if item.is_dir()]
    assert [item.name for item in bundle_directories] == [first["bundle_sha256"]]


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda packet: packet.update({"unrecognized": "no"}), "unknown fields"),
        (lambda packet: packet.update({"attachment_transfer": [{"attachment_id": "asset-1"}]}), "unknown fields"),
        (lambda packet: packet["source_revision"].update({"document_id": "/Users/private/deck.pptx"}), "non-portable local reference"),
        (lambda packet: packet["transfer_scope"].update({"deck_content_embedded": True}), "expected const"),
        (lambda packet: packet.pop("guidance_snapshot_ref"), "requires"),
        (lambda packet: packet.pop("guidance_dispositions"), "requires"),
        (
            lambda packet: packet.update(
                {
                    "recipient_receipt": {
                        "state": "received",
                        "receiver": "receiver-1",
                        "received_at": "2026-09-22T00:00:00+00:00",
                        "evidence": ["forged"],
                        "validation_errors": [],
                    }
                }
            ),
            "unknown fields",
        ),
    ],
)
def test_packet_validation_rejects_unsafe_or_invalid_source_records(mutation, match: str):
    payload = _packet()
    mutation(payload)
    with pytest.raises(PortablePacketError, match=match):
        validate_packet_v1(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "/home/alice/private.pptx",
        "path=/Users/alice/private.pptx",
        "~/Documents/private.pptx",
        r"\\server\share\private.pptx",
        r"\\server/share/private.pptx",
        r"//server\share\private.pptx",
        r"\\server/share\private.pptx",
        "/var/folders/abc/private.pptx",
        r"C:\\private\\deck.pptx",
        r"\Users\alice\private.pptx",
        r"\Volumes\shared\private.pptx",
        r"\root\private.pptx",
        r"\work\37\workspace",
        r"\node_modules\private",
        r"C:Users\alice\private.pptx",
        r"C:work\37\workspace",
        r"%USERPROFILE%\private\deck.pptx",
        r"%APPDATA%\private\deck.pptx",
        r"%LOCALAPPDATA%\private\deck.pptx",
        r"%HOMEDRIVE%%HOMEPATH%\private\deck.pptx",
        "$HOME/private/deck.pptx",
        "${HOME}/private/deck.pptx",
        "$TMPDIR/private/deck.pptx",
        r"$env:USERPROFILE\private\deck.pptx",
        r"https://example.invalid\Users\alice\private.pptx",
    ],
)
def test_packet_validation_rejects_broad_local_path_forms(reference: str):
    payload = _packet()
    payload["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="non-portable local reference"):
        validate_packet_v1(json.dumps(payload).encode("utf-8"))


def test_packet_validation_allows_web_urls_and_non_path_slashes():
    payload = _packet()
    payload["feedback_events"][0]["problem"] = "https://example.com/reference"
    assert validate_packet_v1(json.dumps(payload).encode("utf-8"))["packet_id"] == "office-packet-001"

    # A remote URL may legally contain a doubled slash before a path segment
    # named like a macOS home. The local-path detector must not restart at the
    # second slash and turn a portable URL into a false local-path finding.
    payload = _packet()
    payload["feedback_events"][0]["problem"] = "https://example.com//Users/alice/reference"
    assert validate_packet_v1(json.dumps(payload).encode("utf-8"))["packet_id"] == "office-packet-001"

    payload = _packet()
    payload["feedback_events"][0]["problem"] = "https://example.com///server/share/reference"
    assert validate_packet_v1(json.dumps(payload).encode("utf-8"))["packet_id"] == "office-packet-001"

    payload = _packet()
    payload["feedback_events"][0]["problem"] = "https://example.com/%2F%2Fserver/share/reference"
    assert validate_packet_v1(json.dumps(payload).encode("utf-8"))["packet_id"] == "office-packet-001"

    payload = _packet()
    payload["feedback_events"][0]["problem"] = "https://example.com/assets;v=1/Users/logo.png"
    assert validate_packet_v1(json.dumps(payload).encode("utf-8"))["packet_id"] == "office-packet-001"

    payload = _packet()
    payload["feedback_events"][0]["problem"] = "https://example.com/;work/37"
    assert validate_packet_v1(json.dumps(payload).encode("utf-8"))["packet_id"] == "office-packet-001"

    for remote_url in (
        "https://localhost/Users/alice/reference",
        "https://127.0.0.1/Users/alice/reference",
        "https://[::1]/Users/alice/reference",
        "https://example.com:8443/Users/alice/reference",
        "https://例子.测试/Users/alice/reference",
    ):
        payload = _packet()
        payload["feedback_events"][0]["problem"] = remote_url
        assert validate_packet_v1(json.dumps(payload).encode("utf-8"))["packet_id"] == "office-packet-001"

    payload = _packet()
    payload["feedback_events"][0]["problem"] = "Compare sales / marketing responsibilities without moving source facts."
    assert validate_packet_v1(json.dumps(payload).encode("utf-8"))["packet_id"] == "office-packet-001"


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.com/?source=/Users/alice/private.pptx",
        "https://example.com/?source=//server/share/private.pptx",
        "https://example.com/#source=/Users/alice/private.pptx",
    ],
)
def test_packet_validation_rejects_local_reference_in_remote_url_query_or_fragment(reference: str):
    payload = _packet()
    payload["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="non-portable local reference"):
        validate_packet_v1(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "[remote](https://example.com/assets)/Users/alice/private.pptx",
        "(https://example.com/assets)/Users/alice/private.pptx",
        "[remote](https://example.com/assets)/work/37",
    ],
)
def test_packet_validation_rejects_local_reference_after_remote_url_delimiter(reference: str):
    payload = _packet()
    payload["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="non-portable local reference"):
        validate_packet_v1(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "https:///Users/alice/private.pptx",
        "http:///root/private-client/deck.pptx",
        "wss:///Users/alice/private.pptx",
        "https:////server/share/private.pptx",
        "https:%2F%2F%2FUsers%2Falice%2Fprivate.pptx",
    ],
)
def test_packet_validation_rejects_malformed_web_prefix_hiding_local_reference(reference: str):
    payload = _packet()
    payload["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="non-portable local reference"):
        validate_packet_v1(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "https://@/Users/alice/private.pptx",
        "https://user@/Users/alice/private.pptx",
        "https://user:password@/Users/alice/private.pptx",
        "https://:password@/Users/alice/private.pptx",
        "https://./Users/alice/private.pptx",
        "https://../Users/alice/private.pptx",
        "https://%40/Users/alice/private.pptx",
        "https://example.com:not-a-port/Users/alice/private.pptx",
        "https://(host)/Users/alice/private.pptx",
        "https://(/Users/alice/private.pptx)",
        "https://{host}/Users/alice/private.pptx",
        "https://,/Users/alice/private.pptx",
    ],
)
def test_packet_validation_rejects_invalid_web_authority_hiding_local_reference(reference: str):
    payload = _packet()
    payload["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="non-portable local reference"):
        validate_packet_v1(json.dumps(payload).encode("utf-8"))


@pytest.mark.parametrize(
"mutate",
    [
        lambda packet: packet["source_revision"].update({"evidence": []}),
        lambda packet: packet["tool_execution"].update({"state": "performed", "evidence": []}),
        lambda packet: packet["tool_execution"].update({"state": "failed", "evidence": []}),
        lambda packet: packet["artifact_review"].update({"state": "performed", "evidence": []}),
        lambda packet: packet["artifact_review"].update({"state": "not_comparable", "evidence": []}),
        lambda packet: packet["feedback_events"][0]["verification"].update({"state": "visually_reviewed", "evidence": []}),
        lambda packet: packet["feedback_events"][0]["verification"].update({"state": "technically_checked", "evidence": []}),
        lambda packet: packet["feedback_events"][0]["reusability"].update({"state": "conditional_candidate"})
        or packet["feedback_events"][0]["verification"].update({"state": "not_checked", "evidence": []}),
        lambda packet: packet["user_acceptance"].update(
            {"state": "accepted_by_user", "verification": "explicit_approval_observed", "evidence": []}
        ),
        lambda packet: packet["user_acceptance"].update(
            {"state": "rejected_by_user", "verification": "explicit_approval_observed", "evidence": ["claim"]}
        ),
        lambda packet: packet.update({"transmission_state": "transmitted", "transmission_evidence": []}),
        lambda packet: packet.update({"transmission_state": "received"}),
    ],
)
def test_packet_schema_rejects_unsubstantiated_or_receiver_only_claims(mutate):
    packet = _packet()
    mutate(packet)
    with pytest.raises(PortablePacketError, match="canonical validation failed"):
        validate_packet_v1(json.dumps(packet).encode("utf-8"))


@pytest.mark.parametrize(
    "field",
    [
        "recipient_receipt",
        "attachment_transfer",
        "receiver_validation",
    ],
)
def test_packet_schema_rejects_receiver_or_attachment_fields(field: str):
    packet = _packet()
    packet[field] = {"synthetic": "untrusted"}
    with pytest.raises(PortablePacketError, match="unknown fields"):
        validate_packet_v1(json.dumps(packet).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "https://example.com/deck?access_token=secret-value-that-must-not-travel",
        "Please call client@example.com about this slide.",
        "Authorization: Bearer abcdefghijklmnopqrstuv",
    ],
)
def test_packet_validation_rejects_credentials_and_personal_contacts(reference: str):
    packet = _packet()
    packet["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="credential or personal contact"):
        validate_packet_v1(json.dumps(packet).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "https://docs.example.com/contact/alice@example.com",
        "https://docs.example.com/contact/alice%40example.com",
        "https://docs.example.com/contact/%2B8613812345678",
        "https://docs.example.com/contact/Cookie:SID=secret",
        "https://docs.example.com/contact/Set-Cookie:SID=secret;Path=/",
        "https://docs.example.com/contact/Bearer%20secretvalue",
        "https://docs.example.com/contact/data%3Atext/plain;base64,c2VjcmV0",
        "https://docs.example.com/?contact=alice@example.com",
        "https://docs.example.com/#contact=13800138000",
        "alice@example.中国",
        "alice@例子.中国",
        "alice@exämple.com",
    ],
)
def test_packet_validation_rejects_private_remote_paths_and_internationalized_contacts(reference: str):
    """Public route masking never makes a concrete contact or carrier portable."""

    packet = _packet()
    packet["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="credential or personal contact"):
        validate_packet_v1(json.dumps(packet).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "∕Users∕alice∕private-client.pptx",
        "⁄Users⁄alice⁄private-client.pptx",
        "⧸Users⧸alice⧸private-client.pptx",
        "∖Users∖alice∖private-client.pptx",
        "⧵Users⧵alice⧵private-client.pptx",
        "work∕37∕workspace",
    ],
)
def test_packet_validation_rejects_visual_path_separator_confusables(reference: str):
    """Visible local or Studio locations cannot hide behind slash lookalikes."""

    packet = _packet()
    packet["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="non-portable local reference"):
        validate_packet_v1(json.dumps(packet).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "vscode:/Users/alice/private-client.pptx",
        "vscode-insiders:/work/37/workspace",
        "https:/Users/alice/private-client.pptx",
        "http:/Users/alice/private-client.pptx",
        "wss:/work/37/workspace",
        "https::/Users/alice/private-client.pptx",
        "Macintosh HD:Users:alice:private-client:deck.pptx",
        "Macintosh HD:work:37:workspace",
    ],
)
def test_packet_validation_rejects_local_application_uris_and_hfs_paths(reference: str):
    """Only a verified remote authority can make its URL path portable."""

    packet = _packet()
    packet["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="non-portable local reference"):
        validate_packet_v1(json.dumps(packet).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "!binary L1VzZXJzL21hYy9wcml2YXRlL2RlY2sucHB0eA==",
        "!vault L1VzZXJzL21hYy9wcml2YXRlL2RlY2sucHB0eA==",
        "config: !vault L1VzZXJzL21hYy9wcml2YXRlL2RlY2sucHB0eA==",
    ],
)
def test_packet_validation_rejects_encoded_unknown_yaml_tag_payloads(reference: str):
    """A custom YAML tag cannot make opaque decoded bytes portable."""

    packet = _packet()
    packet["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="credential or personal contact"):
        validate_packet_v1(json.dumps(packet).encode("utf-8"))


@pytest.mark.parametrize(
    ("reference", "match"),
    [
        (r"\client-secret\deck.pptx", "non-portable local reference"),
        (r"\Documents and Settings\alice\deck.pptx", "non-portable local reference"),
        (r"${Env:APPDATA}\private\deck.pptx", "non-portable local reference"),
        (r"$env:ProgramFiles(x86)\private\deck.pptx", "non-portable local reference"),
        (r"%CD%\client-secret.pptx", "non-portable local reference"),
        (r"%PUBLIC%\secret.pptx", "non-portable local reference"),
        (r"%ALLUSERSPROFILE%\secret.pptx", "non-portable local reference"),
        (r"%HOMESHARE%\secret.pptx", "non-portable local reference"),
        (r"%__APPDIR__%\secret.pptx", "non-portable local reference"),
        (r"$env:CD\secret.pptx", "non-portable local reference"),
        (r"${env:CD}\secret.pptx", "non-portable local reference"),
        (r"$env:HOME\secret.pptx", "non-portable local reference"),
        (r"$env:PUBLIC\secret.pptx", "non-portable local reference"),
        ("$OLDPWD/private/deck.pptx", "non-portable local reference"),
        ("${OLDPWD}/private/deck.pptx", "non-portable local reference"),
        ("$XDG_RUNTIME_DIR/private/deck.pptx", "non-portable local reference"),
        (r"\secret.pptx", "non-portable local reference"),
        (r"\private-client.pptx", "non-portable local reference"),
        (r"\Users", "non-portable local reference"),
        (r"\Windows", "non-portable local reference"),
        (r"\Documents", "non-portable local reference"),
        (r"\private", "non-portable local reference"),
        ("~+/private/deck.pptx", "non-portable local reference"),
        ("~-/private/deck.pptx", "non-portable local reference"),
        ("/c/client-a/private-deck.pptx", "non-portable local reference"),
        ("/d/client-b/secret.pptx", "non-portable local reference"),
        ("/D/work/37/secret.pptx", "non-portable local reference"),
        ("/e/Users/alice/private.pptx", "non-portable local reference"),
        ("/c/Program Files (x86)/App/app.exe", "non-portable local reference"),
        ("/cygdrive/c/client-a/private-deck.pptx", "non-portable local reference"),
        ("C:private.pptx", "non-portable local reference"),
        ("C:client-secret", "non-portable local reference"),
        ("file:C:private.pptx", "non-portable local reference"),
        ("%2Fc%2Fclient-a%2Fprivate-deck.pptx", "non-portable local reference"),
        ("&#x2F;cygdrive&#x2F;c&#x2F;client-a&#x2F;private-deck.pptx", "non-portable local reference"),
        ("https://alice:pw@localhost/private-preview", "credential or personal contact"),
        ("https://alice:a@127.0.0.1/x", "credential or personal contact"),
        ("https://alice:pw@[::1]/x", "credential or personal contact"),
        ("https://alice@localhost/x", "credential or personal contact"),
        ("Bearer x", "credential or personal contact"),
        ("Bearer%20x", "credential or personal contact"),
        ("Bearer&#32;x", "credential or personal contact"),
        ("Bearer 'x'", "credential or personal contact"),
        ('Bearer "x"', "credential or personal contact"),
        ("Set-Cookie: session=x", "credential or personal contact"),
        ("Cookie: token=x", "credential or personal contact"),
        ("session=x", "credential or personal contact"),
        ("session_id=x", "credential or personal contact"),
        ("session='x'", "credential or personal contact"),
        ('session="x"', "credential or personal contact"),
        ('token: "x"', "credential or personal contact"),
        ('password: "x"', "credential or personal contact"),
        ('access_token: "x"', "credential or personal contact"),
        ('api_key: "x"', "credential or personal contact"),
        ('authorization: "x"', "credential or personal contact"),
        ("token: &value x", "credential or personal contact"),
        ("session: &value x", "credential or personal contact"),
        ("password: !!str x", "credential or personal contact"),
        ("'token': &value x", "credential or personal contact"),
        ("token: |\n  x", "credential or personal contact"),
        ("session: >\n  x", "credential or personal contact"),
        ("Bearer &value x", "credential or personal contact"),
        ("value: &value x\ntoken: *value", "credential or personal contact"),
        ("value: &value >\n  x\ntoken: *value", "credential or personal contact"),
        ("value: &value |\n  x\nAuthorization: *value", "credential or personal contact"),
        ("credential: x", "credential or personal contact"),
        ("credentials: x", "credential or personal contact"),
        ("private_key: x", "credential or personal contact"),
        ("privateKey: x", "credential or personal contact"),
        ("secret_key: x", "credential or personal contact"),
        ("authToken: x", "credential or personal contact"),
        ("bearer: x", "credential or personal contact"),
        ("https://docs.example.com/?session=x", "credential or personal contact"),
        ("https://docs.example.com/#token=x", "credential or personal contact"),
        ("https://example.com/token=x", "credential or personal contact"),
        ("https://example.com/password=secret", "credential or personal contact"),
        ("https://example.com/api_key=x", "credential or personal contact"),
        ("https://example.com/session/x", "credential or personal contact"),
        ("https%3A%2F%2Falice%3Apw%40localhost%2Fx", "credential or personal contact"),
        ('{"session":"x"}', "credential or personal contact"),
    ],
)
def test_handoff_bundle_rejects_cross_platform_local_and_short_credential_forms(reference: str, match: str):
    """The runtime bundle gate and package gate share these portable boundaries."""

    bundle = _bundle()
    bundle["packet"]["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match=match):
        validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "/a/b is a documented relative API route.",
        "/v1/users is a documented endpoint.",
        "https://a:80/path",
        "https://docs.example.com/c/Users/alice/reference",
        "https://docs.example.com/cygdrive/c/Users/alice/reference",
        "https://docs.example.com/session=overview",
        "https://docs.example.com/token=release-notes-2026-with-long-title",
        "https://docs.example.com/api_key=public-api-docs-v2",
        "https://docs.example.com/token/overview",
        "https://docs.example.com/guides/bearer-token",
        "https://docs.example.com/user@guide",
        "https://docs.example.com/reference/contact-routing",
        "https://docs.example.com/2026/1380013800-guide",
        "https:∕∕docs.example.com∕c∕Users∕alice",
        "!vault ordinary-prose",
        "https://docs.example.com?topic=foo@bar",
        "https://docs.example.com#topic=foo@bar",
        "Cookie handling is outside this workflow.",
        "Record whether the host session is available.",
        "Authorization is required before transmission.",
        "A bearer token is never requested.",
        "Bearer authentication may be unavailable.",
        "Bearer scheme review is complete.",
        "Bearer credentials are never requested.",
        "token=<token>",
        "Bearer <token>",
        "Figure A:2024 results.",
        "Slide B:3 summarizes the evidence.",
        "The ratio is A:B.",
        "ISBN A:123456 is a placeholder label.",
    ],
)
def test_handoff_bundle_allows_public_url_paths_and_plain_security_prose(reference: str):
    bundle = _bundle()
    bundle["packet"]["feedback_events"][0]["problem"] = reference
    assert validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))["bundle_id"] == "office-bundle-001"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("token", "x"),
        ("session", "x"),
        ("password", 1),
        ("api_key", "x"),
        ("authorization", True),
    ],
)
def test_runtime_scanner_rejects_decoded_sensitive_mapping_values(key: str, value: object):
    """A decoded object must not split a credential key from its scalar value.

    The released packet schema deliberately closes ``feedback_events.context``
    before the generic scanner runs. Exercise the scanner itself here so this
    future-proof guard is not hidden behind an unrelated canonical-field
    rejection.
    """

    with pytest.raises(PortablePacketError, match="credential or personal contact"):
        portable_packets._scan_portable_value({key: value}, field="synthetic evidence")


def test_runtime_scanner_allows_explicit_sensitive_setting_placeholder():
    portable_packets._scan_portable_value({"token": "<token>"}, field="declarative example")


def test_bundle_validation_requires_complete_matching_records():
    valid = _bundle()
    assert validate_handoff_bundle_v1(json.dumps(valid).encode("utf-8"))["bundle_id"] == "office-bundle-001"

    missing_intent = _bundle()
    missing_intent.pop("presentation_intent")
    with pytest.raises(PortablePacketError, match="missing required fields"):
        validate_handoff_bundle_v1(json.dumps(missing_intent).encode("utf-8"))

    mismatch = _bundle()
    mismatch["packet"]["presentation_intent_ref"] = "other-intent"
    with pytest.raises(PortablePacketError, match="presentation_intent_ref does not match"):
        validate_handoff_bundle_v1(json.dumps(mismatch).encode("utf-8"))

    missing_review = _bundle()
    missing_review.pop("expression_review")
    with pytest.raises(PortablePacketError, match="missing required fields"):
        validate_handoff_bundle_v1(json.dumps(missing_review).encode("utf-8"))

    private_record = _bundle()
    private_record["presentation_intent"]["audience"] = "path=/home/alice/private"
    with pytest.raises(PortablePacketError, match="non-portable local reference"):
        validate_handoff_bundle_v1(json.dumps(private_record).encode("utf-8"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda bundle: bundle["capability_report"]["source_revision"].update({"evidence": []}),
        lambda bundle: bundle["capability_report"]["operations"]["edit_text"].update(
            {"state": "confirmed", "evidence": []}
        ),
        lambda bundle: bundle["capability_report"]["operations"]["export_or_inspect_pdf"].update(
            {"state": "unavailable", "evidence": []}
        ),
        lambda bundle: bundle["expression_review"].update({"review_scope": []}),
        lambda bundle: bundle["expression_review"]["visual_inspection"].update(
            {"state": "performed", "evidence": []}
        ),
        lambda bundle: bundle["expression_review"].update(
            {"invalidated_by_changes": ["none", "slide_geometry_changed"]}
        ),
        lambda bundle: bundle["expression_review"].update(
            {"review_state": "reviewed_current_revision", "invalidated_by_changes": ["slide_geometry_changed"]}
        ),
        lambda bundle: bundle["expression_review"].update(
            {"visual_inspection": {"synthetic": "unknown nested object"}}
        ),
        lambda bundle: bundle.pop("capability_report"),
    ],
)
def test_bundle_schema_rejects_unproven_capabilities_and_incoherent_expression_review(mutate):
    bundle = _bundle()
    mutate(bundle)
    with pytest.raises(PortablePacketError, match="canonical validation failed"):
        validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))


def test_bundle_requires_a_real_direct_bridge_claim_before_source_reports_transmission():
    bundle = _bundle()
    packet = bundle["packet"]
    packet["transmission_state"] = "transmitted"
    packet["transmission_evidence"] = ["authorized source-side handoff was sent"]

    with pytest.raises(PortablePacketError, match="direct_bridge"):
        validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))

    capability = bundle["capability_report"]
    capability["bridge_mode"] = "direct_bridge"
    capability["connected_apps"] = ["approved-local-receiver"]
    with pytest.raises(PortablePacketError, match="transmit_handoff"):
        validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))

    capability["operations"]["transmit_handoff"] = {
        "state": "confirmed",
        "evidence": ["authorized handoff endpoint reported a source-side send"],
    }
    assert validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))["packet"]["transmission_state"] == "transmitted"

    prepared = _bundle()
    prepared["capability_report"]["operations"]["transmit_handoff"] = {
        "state": "confirmed",
        "evidence": ["incorrect source claim"],
    }
    with pytest.raises(PortablePacketError, match="prepared_not_transmitted"):
        validate_handoff_bundle_v1(json.dumps(prepared).encode("utf-8"))


def test_packet_validation_rejects_duplicate_json_keys():
    raw = b'{"schema":"aippt-learning-packet-v1","schema":"aippt-learning-packet-v1"}'
    with pytest.raises(PortablePacketError, match="Duplicate JSON key"):
        validate_packet_v1(raw)


def test_packet_validation_rejects_excessive_nesting_before_json_decoder_recurses():
    from aippt_studio.portable_packets import MAX_JSON_DEPTH

    raw = (b'{"untrusted":' + b"[" * MAX_JSON_DEPTH + b"0" + b"]" * MAX_JSON_DEPTH + b"}")
    with pytest.raises(PortablePacketError, match="maximum JSON nesting depth"):
        validate_packet_v1(raw)


def test_packet_validation_maps_pathological_json_numbers_to_a_controlled_error():
    raw = b'{"untrusted":' + b"9" * 5000 + b"}"
    with pytest.raises(PortablePacketError, match="not valid JSON"):
        validate_packet_v1(raw)


def test_packet_import_rejects_sealed_work_and_never_changes_its_status(tmp_path: Path):
    work = _work(tmp_path, status="review")
    before = (work / "work.json").read_bytes()
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())

    with pytest.raises(PortablePacketError, match="intake or in_progress"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert (work / "work.json").read_bytes() == before
    assert not (work / "workspace" / "bridge-packets").exists()


def test_packet_import_preserves_local_evolution_state_and_existing_guidance_artifacts(tmp_path: Path):
    work = _work(tmp_path)
    evolution_state = tmp_path / "任务知识库" / "evolution" / "state.json"
    evolution_state.parent.mkdir(parents=True)
    evolution_state.write_bytes(b'{"local":"evidence stays local"}\n')
    active_guidance = tmp_path / "任务知识库" / "active-guidance.json"
    active_guidance.parent.mkdir(parents=True, exist_ok=True)
    active_guidance.write_bytes(b'{"local":"guidance stays local"}\n')
    before_state = evolution_state.read_bytes()
    before_guidance = active_guidance.read_bytes()
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())

    result = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")
    receipt = json.loads((work / result["receipt_path"]).read_text(encoding="utf-8"))

    assert evolution_state.read_bytes() == before_state
    assert active_guidance.read_bytes() == before_guidance
    assert receipt["receipt_state"] == "received_external_unverified"
    assert receipt["capability_claim"] == "untrusted"
    assert receipt["visual_review_claim"] == "untrusted"
    assert receipt["authorization_claim"] == "untrusted"
    assert receipt["acceptance_claim"] == "untrusted"
    assert receipt["attachment_transfer"] == "unsupported_in_source_bundle_v1"


def test_packet_import_rejects_oversized_file_before_creating_bridge_storage(tmp_path: Path):
    from aippt_studio.portable_packets import MAX_PACKET_BYTES

    work = _work(tmp_path)
    source = tmp_path / "oversized-handoff.json"
    with source.open("wb") as stream:
        stream.truncate(MAX_PACKET_BYTES + 1)

    with pytest.raises(PortablePacketError, match="exceeds"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert not (work / "workspace" / "bridge-packets").exists()


def test_packet_import_quarantines_an_interrupted_old_partial_packet_and_retries(tmp_path: Path):
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    interrupted = work / "workspace" / "bridge-packets" / "v1" / digest
    interrupted.mkdir(parents=True)
    (interrupted / "bundle.json").write_bytes(raw)

    result = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")
    root = work / "workspace" / "bridge-packets" / "v1"

    assert result["status"] == "received_external_unverified"
    assert (root / digest / "bundle.json").read_bytes() == raw
    assert (root / digest / "receipt.json").is_file()
    assert any(path.name.startswith(f".quarantine-{digest}-") for path in root.iterdir())


def test_packet_import_uses_a_staged_transaction_when_receipt_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    original_write = portable_packets._secure_write_new_at

    def fail_receipt(parent_fd: int, name: str, data: bytes, *, label: str, mode: int = 0o600) -> None:
        if name == "receipt.json":
            raise PortablePacketError("synthetic receipt write failure")
        original_write(parent_fd, name, data, label=label, mode=mode)

    monkeypatch.setattr(portable_packets, "_secure_write_new_at", fail_receipt)
    with pytest.raises(PortablePacketError, match="synthetic receipt write failure"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    root = work / "workspace" / "bridge-packets" / "v1"
    assert not (root / digest).exists()
    monkeypatch.setattr(portable_packets, "_secure_write_new_at", original_write)
    assert import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")["status"] == "received_external_unverified"


def test_packet_import_rejects_a_leaf_symlink_even_without_o_nofollow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    work = _work(tmp_path)
    target = tmp_path / "actual-handoff.json"
    _write_json(target, _bundle())
    source = tmp_path / "linked-handoff.json"
    source.symlink_to(target)
    monkeypatch.delattr(portable_packets.os, "O_NOFOLLOW", raising=False)

    with pytest.raises(PortablePacketError, match="non-symlink"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert not (work / "workspace" / "bridge-packets").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("automatic_learning", "performed"),
        ("acceptance_claim", "accepted"),
        ("capability_claim", "verified"),
        ("raw_conversation_provenance", "present"),
    ],
)
def test_packet_reimport_rejects_tampered_local_receipt_truth_boundaries(
    tmp_path: Path, field: str, value: str
):
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())
    first = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")
    receipt_path = work / first["receipt_path"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt[field] = value
    _write_json(receipt_path, receipt)

    with pytest.raises(PortablePacketError, match=f"invalid {field}"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")


def test_packet_reimport_rejects_partial_or_linked_local_receipts(tmp_path: Path):
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())
    first = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")
    receipt_path = work / first["receipt_path"]

    receipt_path.write_text('{"schema":"aippt-external-handoff-receipt-v1"}\n', encoding="utf-8")
    with pytest.raises(PortablePacketError, match="invalid field set"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    receipt_path.unlink()
    alternate = tmp_path / "outside-receipt.json"
    alternate.write_text('{"schema":"aippt-external-handoff-receipt-v1"}\n', encoding="utf-8")
    receipt_path.symlink_to(alternate)
    with pytest.raises(PortablePacketError, match="symlink or reparse"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")


def test_packet_reimport_rejects_a_linked_or_replaced_frozen_bundle(tmp_path: Path):
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())
    first = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")
    frozen_path = work / first["bundle_path"]
    frozen_path.unlink()
    external = tmp_path / "outside-bundle.json"
    _write_json(external, _bundle())
    frozen_path.symlink_to(external)

    with pytest.raises(PortablePacketError, match="symlink or reparse"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")


def _walk_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def test_guidance_snapshot_is_schema_shaped_and_strips_private_provenance(tmp_path: Path):
    snapshot = export_guidance_snapshot(tmp_path, include_learned=False)

    assert snapshot["schema"] == "aippt-guidance-snapshot-v1"
    assert snapshot["source_workbench"] == "aippt-studio"
    assert snapshot["transfer_scope"] == "sanitized_active_guidance_only"
    assert snapshot["authorization"] == {"state": "not_recorded", "evidence": []}
    assert snapshot["status"] == "active_read_only"
    assert snapshot["rules"]
    assert {
        "base:source_deck_redesign",
        "base:document_to_deck",
        "base:style_grammar",
        "base:visual_sourcing",
    }.issubset({rule["rule_id"] for rule in snapshot["rules"]})
    assert all(
        set(rule) == {"rule_id", "status", "action", "applies_when", "exceptions"}
        for rule in snapshot["rules"]
    )
    assert not {"user_quote", "source_receipt", "source"} & set(_walk_keys(snapshot))
    serialized = json.dumps(snapshot, ensure_ascii=False)
    assert "/Users/" not in serialized
    assert "file://" not in serialized
    assert "generated_at" not in snapshot
    assert "created_at" in snapshot
    assert "Works 23" not in serialized
    assert "Work 37" not in serialized

    destination = tmp_path / "guidance.json"
    result = write_guidance_snapshot(tmp_path, destination, include_learned=False)
    assert result["status"] == "exported"
    written = json.loads(destination.read_text(encoding="utf-8"))
    assert set(written) == set(snapshot)
    assert written["created_at"]
    assert written["guidance_revision"] == snapshot["guidance_revision"]
    assert written["rules"] == snapshot["rules"]
    with pytest.raises(PortablePacketError, match="already exists"):
        write_guidance_snapshot(tmp_path, destination, include_learned=False)
    assert write_guidance_snapshot(
        tmp_path, destination, include_learned=False, overwrite=True
    )["status"] == "exported"


def test_guidance_export_projects_only_static_generic_families_and_never_raw_client_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import evolution

    monkeypatch.setattr(
        evolution,
        "load_guidance",
        lambda _workspace: {
            "status": "ready",
            "errors": [],
            "rules": [
                {
                    "id": "client-acme-private-image",
                    "action": "Acme confidential Q4 plan: crop image at /Users/alice/private.pptx; email client@example.com.",
                    "applies_when": "An image is placed in an ellipse.",
                    "exceptions": ["Do not disclose the client material."],
                    "source_receipt": "local-only",
                },
            ]
        },
    )
    snapshot = export_guidance_snapshot(tmp_path, include_learned=True)
    serialized = json.dumps(snapshot, ensure_ascii=False)

    assert snapshot["status"] == "active_read_only"
    assert snapshot["learned_projection"] == "included_verified"
    assert any(rule["rule_id"] == "learned:image_geometry" for rule in snapshot["rules"])
    for marker in ("Acme", "Q4", "/Users/", "client@example.com", "private.pptx", "source_receipt"):
        assert marker not in serialized


def test_guidance_export_withholds_all_learned_families_when_local_integrity_is_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import evolution

    monkeypatch.setattr(
        evolution,
        "load_guidance",
        lambda _workspace: {
            "status": "partial",
            "errors": ["integrity receipt missing"],
            "rules": [{"id": "image", "action": "crop image", "applies_when": "image", "exceptions": []}],
        },
    )
    snapshot = export_guidance_snapshot(tmp_path, include_learned=True)

    assert snapshot["learned_projection"] == "withheld_integrity_not_ready"
    assert all(not rule["rule_id"].startswith("learned:") for rule in snapshot["rules"])
    assert "withheld" in snapshot["limitations"][-1]


def test_cli_exposes_safe_bundle_and_guidance_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path / "aippt-studio")
    monkeypatch.setattr(cli, "_require_host_native_python", lambda: None)
    runner = CliRunner()

    imported = runner.invoke(
        cli.app,
        ["packet-import", "--work-dir", str(work), "--packet", str(source), "--json"],
    )
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.stdout)["status"] == "received_external_unverified"

    guidance = tmp_path / "guidance.json"
    exported = runner.invoke(cli.app, ["guidance-export", "--output", str(guidance), "--json"])
    assert exported.exit_code == 0, exported.output
    assert json.loads(exported.stdout)["transmission"] == "not_transmitted"


@pytest.mark.parametrize(
    ("value", "match"),
    [
        ("/root/private-client/deck.pptx", "non-portable local reference"),
        ("/mnt/confidential/report.pptx", "non-portable local reference"),
        ("/workspace/private/deck.pptx", "non-portable local reference"),
        ("file:/root/private-client/deck.pptx", "non-portable local reference"),
        ("~alice/private-client/deck.pptx", "non-portable local reference"),
        ("source:~alice/private-client/deck.pptx", "non-portable local reference"),
        (r"source:\\server\share\private-client.pptx", "non-portable local reference"),
        ("//server/share/private-client.pptx", "non-portable local reference"),
        ("smb://server/share/private-client.pptx", "non-portable local reference"),
        (r"/Users\alice\private-client.pptx", "non-portable local reference"),
        ("//Users/alice/private-client.pptx", "non-portable local reference"),
        (r"\Device\HarddiskVolume4\Users\alice\private-client.pptx", "non-portable local reference"),
        (r"\??\C:\Users\alice\private-client.pptx", "non-portable local reference"),
        ("file:%2F%2F%2FUsers%2Falice%2Fprivate-client.pptx", "non-portable local reference"),
        ("C:%5CUsers%5Calice%5Cprivate-client.pptx", "non-portable local reference"),
        ("%2FUsers%2Falice%2Fprivate-client.pptx", "non-portable local reference"),
        ("work%2F37", "non-portable local reference"),
        ("[work/37]", "non-portable local reference"),
        ("`work/37`", "non-portable local reference"),
        ("work/37.", "non-portable local reference"),
        ("foo/work/37", "non-portable local reference"),
        ("jobs%2Farchive", "non-portable local reference"),
        ("outputs%2Fdeck.pptx", "non-portable local reference"),
        ("node_modules%2Fdependency", "non-portable local reference"),
        (".venv%2Fbin", "non-portable local reference"),
        (".tools%2Fcache", "non-portable local reference"),
        ("../private-client/deck.pptx", "non-portable local reference"),
        ("./private-client/deck.pptx", "non-portable local reference"),
        ("`../private-client/deck.pptx`", "non-portable local reference"),
        ("[./private-client/deck.pptx]", "non-portable local reference"),
        ("{../private-client/deck.pptx}", "non-portable local reference"),
        ("path=./private-client/deck.pptx", "non-portable local reference"),
        ("source:../private-client/deck.pptx", "non-portable local reference"),
        ("/Us<span>ers</span>/alice/private-client.pptx", "non-portable local reference"),
        ("/Us**ers**/alice/private-client.pptx", "non-portable local reference"),
        ("/Us\u200bers/alice/private-client.pptx", "non-portable local reference"),
        ("/Us\u180eers/alice/private-client.pptx", "non-portable local reference"),
        ("fi\u200ble:///Users/alice/private-client.pptx", "non-portable local reference"),
        ("／Ｕｓｅｒｓ／alice／private-client．pptx", "non-portable local reference"),
        ("%EF%BC%8FUsers%EF%BC%8Falice%EF%BC%8Fprivate-client.pptx", "non-portable local reference"),
        ("work<span>/</span>37/workspace", "non-portable local reference"),
        ("wo**rk**/37/workspace", "non-portable local reference"),
        ("work\u200b/37/workspace", "non-portable local reference"),
        ("ｗｏｒｋ／３７／ｗｏｒｋｓｐａｃｅ", "non-portable local reference"),
        ("Set-Cookie: session=abcdefghijklmnopqrstuvwxyz0123456789", "credential or personal contact"),
        ("Cookie: JSESSIONID=secretvalue", "credential or personal contact"),
        ("Set-Cookie: csrf=secretvalue; Path=/", "credential or personal contact"),
        ("Cookie<span>:</span>sid=secretvalue", "credential or personal contact"),
        ("Cookie\u00a0:\u00a0JSESSIONID=secretvalue", "credential or personal contact"),
        ("Bearer\u00a0secretvalue", "credential or personal contact"),
        ("session\u00a0=\u00a0secretvalue", "credential or personal contact"),
        ("Cookie\u200e:\u200eJSESSIONID=secretvalue", "credential or personal contact"),
        ("Bearer\u200esecretvalue", "credential or personal contact"),
        ("https://token:abcdefghijklmnop@localhost/api", "credential or personal contact"),
        ("gho_abcdefghijklmnopqrstuvwxyz0123456789", "credential or personal contact"),
        ("xoxb-abcdefghijklmnopqrstuvwxyz0123456789", "credential or personal contact"),
        ("AIzaabcdefghijklmnopqrstuvwxyz0123456789", "credential or personal contact"),
        ("-----BEGIN PRIVATE KEY-----", "credential or personal contact"),
        ("13800138000", "credential or personal contact"),
        ("tel:+442079460958", "credential or personal contact"),
        ("+44 20 7946 0958", "credential or personal contact"),
        ("+86 138 0013 8000", "credential or personal contact"),
        ("+86\u00a0138\u00a01234\u00a05678", "credential or personal contact"),
        ("+86\u2011138\u20111234\u20115678", "credential or personal contact"),
        ("＋８６ １３８ １２３４ ５６７８", "credential or personal contact"),
        ("138-0013-8000", "credential or personal contact"),
        ("(212) 555-1234", "credential or personal contact"),
        ("client%40private%2Eexample", "credential or personal contact"),
        ("alice<span>@</span>example.com", "credential or personal contact"),
        ("alice**@**example.com", "credential or personal contact"),
        ("alice\u200b@example.com", "credential or personal contact"),
        ("alice\u2066@example.com", "credential or personal contact"),
        ("&#x2F;Users&#x2F;alice&#x2F;private-client.pptx", "non-portable local reference"),
        (r"\u002fUsers\u002falice\u002fprivate-client.pptx", "non-portable local reference"),
        (r"\u002e\u002e\u002fprivate-client\u002fdeck.pptx", "non-portable local reference"),
        (r"work\u002f37", "non-portable local reference"),
        (r"\x2fUsers\x2falice\x2fprivate-client.pptx", "non-portable local reference"),
    ],
)
def test_packet_validation_rejects_extended_private_location_credentials_and_contacts(value: str, match: str):
    payload = _packet()
    payload["feedback_events"][0]["problem"] = value

    with pytest.raises(PortablePacketError, match=match):
        validate_packet_v1(json.dumps(payload).encode("utf-8"))


def test_packet_import_rejects_noncanonical_work_directory_even_with_matching_metadata(tmp_path: Path):
    work = tmp_path / "work" / "not-a-work-number"
    (work / "workspace").mkdir(parents=True)
    (work / "work.json").write_text(
        json.dumps({"work_id": "not-a-work-number", "status": "in_progress"}), encoding="utf-8"
    )
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())

    with pytest.raises(PortablePacketError, match="canonical work/<N>"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")


def _force_guarded_windows_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    from aippt_studio import portable_packets

    monkeypatch.setattr(portable_packets, "_secure_storage_supported", lambda: False)
    monkeypatch.setattr(portable_packets, "_guarded_windows_fallback_enabled", lambda: True)


def test_packet_import_guarded_windows_fallback_retains_and_reimports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _force_guarded_windows_fallback(monkeypatch)
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())

    first = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")
    repeated = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert first["status"] == "received_external_unverified"
    assert repeated["status"] == "already_received"
    assert first["storage_mode"] == "guarded_exclusive_work"
    assert repeated["storage_mode"] == "guarded_exclusive_work"
    assert (work / first["bundle_path"]).is_file()
    assert (work / first["receipt_path"]).is_file()


def test_packet_import_guarded_windows_fallback_rejects_linked_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _force_guarded_windows_fallback(monkeypatch)
    work = _work(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    bridge = work / "workspace" / "bridge-packets"
    bridge.symlink_to(outside, target_is_directory=True)
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())

    with pytest.raises(PortablePacketError, match="link or reparse"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert not (outside / "v1").exists()


def test_guarded_windows_fallback_requires_operator_verified_local_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Stable stat identities are necessary, not proof that a volume is local."""

    from aippt_studio import portable_packets

    _force_guarded_windows_fallback(monkeypatch)
    monkeypatch.setattr(portable_packets, "_has_stable_file_identity", lambda _metadata: False)
    work = _work(tmp_path)

    with pytest.raises(
        PortablePacketError,
        match="stable file identities; the operator must verify a suitable local filesystem",
    ):
        portable_packets._guarded_directory(work, label="Work", create=False)


def test_packet_import_guarded_windows_fallback_rejects_sealed_work_without_creating_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _force_guarded_windows_fallback(monkeypatch)
    work = _work(tmp_path, status="review")
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())

    with pytest.raises(PortablePacketError, match="intake or in_progress"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert not (work / "workspace" / "bridge-packets").exists()


def test_guarded_import_never_creates_storage_in_work_replaced_after_lifecycle_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A hard-linked lifecycle record must not redirect guarded create=True work."""

    from aippt_studio import portable_packets

    _force_guarded_windows_fallback(monkeypatch)
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())
    original_assert = portable_packets._GuardedWindowsPacketStore.assert_work_current_and_importable
    calls = 0

    def swap_after_second_lifecycle_assert(self):
        nonlocal calls
        original_assert(self)
        calls += 1
        if calls == 2:
            previous = work.with_name("1-before-guarded-work-swap")
            work.rename(previous)
            work.mkdir()
            (work / "workspace").mkdir()
            # Keep the held lifecycle inode unchanged so this test covers the
            # path-tree binding rather than only a work.json replacement.
            (work / "work.json").hardlink_to(previous / "work.json")

    monkeypatch.setattr(
        portable_packets._GuardedWindowsPacketStore,
        "assert_work_current_and_importable",
        swap_after_second_lifecycle_assert,
    )
    with pytest.raises(PortablePacketError, match="Work package changed"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert calls >= 2
    assert not (work / "workspace" / "bridge-packets").exists()
    assert not (work / "workspace" / "bridge-packets" / "v1").exists()


def test_packet_import_guarded_windows_fallback_does_not_publish_on_receipt_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    _force_guarded_windows_fallback(monkeypatch)
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    original_write = portable_packets._GuardedWindowsPacketStore._write_new

    def fail_receipt(self, directory, identity, name, data, *, label):
        if name == "receipt.json":
            raise PortablePacketError("synthetic guarded receipt write failure")
        original_write(self, directory, identity, name, data, label=label)

    monkeypatch.setattr(portable_packets._GuardedWindowsPacketStore, "_write_new", fail_receipt)
    with pytest.raises(PortablePacketError, match="synthetic guarded receipt write failure"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    root = work / "workspace" / "bridge-packets" / "v1"
    assert not (root / digest).exists()
    monkeypatch.setattr(portable_packets._GuardedWindowsPacketStore, "_write_new", original_write)
    assert import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")["status"] == "received_external_unverified"


def test_descriptor_import_rechecks_work_status_after_staging_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    original_verify = portable_packets._SecurePacketStore.verify_staged_packet_directory

    def close_after_staged_verification(self, *args, **kwargs):
        original_verify(self, *args, **kwargs)
        _set_work_status(work, "review")

    monkeypatch.setattr(
        portable_packets._SecurePacketStore,
        "verify_staged_packet_directory",
        close_after_staged_verification,
    )
    with pytest.raises(PortablePacketError, match="intake or in_progress"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert not (work / "workspace" / "bridge-packets" / "v1" / digest).exists()


def test_guarded_import_rechecks_work_status_after_staging_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    _force_guarded_windows_fallback(monkeypatch)
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    original_verify = portable_packets._GuardedWindowsPacketStore.verify_staged_packet

    def close_after_staged_verification(self, *args, **kwargs):
        original_verify(self, *args, **kwargs)
        _set_work_status(work, "review")

    monkeypatch.setattr(
        portable_packets._GuardedWindowsPacketStore,
        "verify_staged_packet",
        close_after_staged_verification,
    )
    with pytest.raises(PortablePacketError, match="intake or in_progress"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert not (work / "workspace" / "bridge-packets" / "v1" / digest).exists()


def test_descriptor_import_rechecks_work_status_before_existing_packet_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    assert import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")["status"] == "received_external_unverified"
    original_open = portable_packets._SecurePacketStore.open_packet_directory

    def close_after_existing_open(store, value: str):
        result = original_open(store, value)
        if result is not None:
            _set_work_status(work, "review")
        return result

    monkeypatch.setattr(portable_packets._SecurePacketStore, "open_packet_directory", close_after_existing_open)
    with pytest.raises(PortablePacketError, match="intake or in_progress"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    directory = work / "workspace" / "bridge-packets" / "v1" / digest
    assert (directory / "bundle.json").read_bytes() == raw
    assert (directory / "receipt.json").is_file()


def test_guarded_import_rechecks_work_status_before_existing_packet_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    _force_guarded_windows_fallback(monkeypatch)
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    assert import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")["status"] == "received_external_unverified"
    original_existing = portable_packets._GuardedWindowsPacketStore.existing_packet

    def close_after_existing_read(store, *args, **kwargs):
        result = original_existing(store, *args, **kwargs)
        if result is not None:
            _set_work_status(work, "review")
        return result

    monkeypatch.setattr(portable_packets._GuardedWindowsPacketStore, "existing_packet", close_after_existing_read)
    with pytest.raises(PortablePacketError, match="intake or in_progress"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    directory = work / "workspace" / "bridge-packets" / "v1" / digest
    assert (directory / "bundle.json").read_bytes() == raw
    assert (directory / "receipt.json").is_file()


def test_descriptor_import_rejects_post_publish_work_close_but_preserves_committed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    original_publish = portable_packets._SecurePacketStore.publish_staged_packet_directory

    def close_after_publish(store, *args, **kwargs):
        original_publish(store, *args, **kwargs)
        _set_work_status(work, "review")

    monkeypatch.setattr(portable_packets._SecurePacketStore, "publish_staged_packet_directory", close_after_publish)
    with pytest.raises(PortablePacketError, match="intake or in_progress"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    directory = work / "workspace" / "bridge-packets" / "v1" / digest
    assert (directory / "bundle.json").read_bytes() == raw
    assert (directory / "receipt.json").is_file()


def test_guarded_import_rejects_post_publish_work_close_but_preserves_committed_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    _force_guarded_windows_fallback(monkeypatch)
    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    original_publish = portable_packets._GuardedWindowsPacketStore.publish_staged_packet

    def close_after_publish(store, *args, **kwargs):
        original_publish(store, *args, **kwargs)
        _set_work_status(work, "review")

    monkeypatch.setattr(portable_packets._GuardedWindowsPacketStore, "publish_staged_packet", close_after_publish)
    with pytest.raises(PortablePacketError, match="intake or in_progress"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    directory = work / "workspace" / "bridge-packets" / "v1" / digest
    assert (directory / "bundle.json").read_bytes() == raw
    assert (directory / "receipt.json").is_file()


def test_descriptor_import_rejects_detached_work_parent_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    original_stage = portable_packets._SecurePacketStore.stage_packet_directory

    def detach_after_stage(store, value: str):
        result = original_stage(store, value)
        original_parent = tmp_path / "work"
        escaped = tmp_path / "escaped-work-parent"
        original_parent.rename(escaped)
        original_parent.symlink_to(escaped, target_is_directory=True)
        return result

    monkeypatch.setattr(portable_packets._SecurePacketStore, "stage_packet_directory", detach_after_stage)
    with pytest.raises(PortablePacketError, match="link|detached|changed"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert not (tmp_path / "escaped-work-parent" / "1" / "workspace" / "bridge-packets" / "v1" / digest).exists()


def test_descriptor_import_rejects_work_parent_replaced_after_locator_before_storage_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())
    original_read = portable_packets._read_bounded_regular_source

    def detach_after_locator(value):
        original_parent = tmp_path / "work"
        escaped = tmp_path / "escaped-after-locator"
        original_parent.rename(escaped)
        original_parent.symlink_to(escaped, target_is_directory=True)
        return original_read(value)

    monkeypatch.setattr(portable_packets, "_read_bounded_regular_source", detach_after_locator)
    with pytest.raises(PortablePacketError, match="Work root.*symlink|Work root.*link"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    assert not (tmp_path / "escaped-after-locator" / "1" / "workspace" / "bridge-packets").exists()


@pytest.mark.parametrize("guarded", [False, True], ids=["descriptor-bound", "guarded-windows"])
@pytest.mark.parametrize(
    ("replacement", "expected_label"),
    [
        ("workspace_root", "Workspace root"),
        ("work_package", "Work package"),
    ],
)
def test_packet_import_rejects_real_canonical_tree_swap_after_source_preflight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guarded: bool,
    replacement: str,
    expected_label: str,
):
    """Do not let source validation retarget an import to a same-shaped Work."""

    from aippt_studio import portable_packets

    if guarded:
        _force_guarded_windows_fallback(monkeypatch)
    work = _work(tmp_path)
    # Keep the source outside a root replacement so the test exercises the
    # Work identity handoff rather than failing because its packet disappeared.
    source = tmp_path.parent / f"{tmp_path.name}-external-handoff.json"
    _write_json(source, _bundle())
    original_read = portable_packets._read_bounded_regular_source
    escaped: Path | None = None

    def replace_after_preflight(value):
        nonlocal escaped
        if replacement == "workspace_root":
            escaped = tmp_path.parent / f"{tmp_path.name}-original-workspace"
            tmp_path.rename(escaped)
            tmp_path.mkdir()
            _work(tmp_path)
        else:
            escaped = tmp_path / "original-work-1"
            work.rename(escaped)
            _work(tmp_path)
        return original_read(value)

    monkeypatch.setattr(portable_packets, "_read_bounded_regular_source", replace_after_preflight)
    try:
        with pytest.raises(PortablePacketError, match=rf"{expected_label} changed after canonical Work intake preflight"):
            import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")
    finally:
        source.unlink(missing_ok=True)

    assert escaped is not None
    escaped_workspace = escaped / "work" / "1" / "workspace" if replacement == "workspace_root" else escaped / "workspace"
    assert not (escaped_workspace / "bridge-packets").exists()
    assert not (tmp_path / "work" / "1" / "workspace" / "bridge-packets").exists()


def test_descriptor_import_preserves_committed_packet_if_directory_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    original_fsync = portable_packets.os.fsync
    directory_syncs = 0

    def fail_final_directory_sync(descriptor: int) -> None:
        nonlocal directory_syncs
        metadata = portable_packets.os.fstat(descriptor)
        if portable_packets.stat.S_ISDIR(metadata.st_mode):
            directory_syncs += 1
            if directory_syncs == 3:
                raise OSError("synthetic final directory fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(portable_packets.os, "fsync", fail_final_directory_sync)
    with pytest.raises(PortablePacketError, match="could not be published"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    directory = work / "workspace" / "bridge-packets" / "v1" / digest
    assert (directory / "bundle.json").read_bytes() == raw
    assert (directory / "receipt.json").is_file()


def test_descriptor_import_quarantines_a_stage_replacement_before_returning_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    original_rename = portable_packets._rename_noreplace_at

    def replace_staged_name(parent_fd: int, stage_name: str, final_name: str) -> None:
        root = work / "workspace" / "bridge-packets" / "v1"
        stage = root / stage_name
        stage.rename(root / f".parked-{stage_name}")
        outside = tmp_path / "outside-stage-target"
        outside.mkdir()
        stage.symlink_to(outside, target_is_directory=True)
        original_rename(parent_fd, stage_name, final_name)

    monkeypatch.setattr(portable_packets, "_rename_noreplace_at", replace_staged_name)
    with pytest.raises(PortablePacketError, match="symlink or reparse|does not match the staged packet"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    root = work / "workspace" / "bridge-packets" / "v1"
    assert not (root / digest).exists()
    assert any(path.name.startswith(".parked-.stage-") for path in root.iterdir())
    assert not any((tmp_path / "outside-stage-target").iterdir())


def test_descriptor_import_never_overwrites_a_destination_that_appears_during_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from aippt_studio import portable_packets

    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    raw = _write_json(source, _bundle())
    digest = hashlib.sha256(raw).hexdigest()
    original_rename = portable_packets._rename_noreplace_at

    def create_destination_then_commit(parent_fd: int, stage_name: str, final_name: str) -> None:
        destination = work / "workspace" / "bridge-packets" / "v1" / final_name
        destination.mkdir()
        (destination / "sentinel.txt").write_text("preexisting", encoding="utf-8")
        original_rename(parent_fd, stage_name, final_name)

    monkeypatch.setattr(portable_packets, "_rename_noreplace_at", create_destination_then_commit)
    with pytest.raises(PortablePacketError, match="could not be published"):
        import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")

    destination = work / "workspace" / "bridge-packets" / "v1" / digest
    assert (destination / "sentinel.txt").read_text(encoding="utf-8") == "preexisting"


@pytest.mark.parametrize(
    "reference",
    [
        "session id: x",
        "https://example.com/?api+key=x",
        "aws_secret_access_key=x",
        "aws_session_token=x",
        "https://example.com/gho_abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.com/sk-abcdefghijklmnopqrstuvwxyz0123456789",
        "data:text/plain;base64,c2VjcmV0",
        "https://example.com/token=abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.com/secret=abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.com/api_key=abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.com/password=abcdefghijklmnopqrstuvwxyz0123456789",
        "https://example.com/authorization=Bearer-abcdefghijklmnopqrstuvwxyz0123456789",
        "https://docs.example.com/token=abcdefgh-ijklmnop-qrstuvwx",
        "https://docs.example.com/session/abcdefgh-ijklmnop-qrstuvwx",
        "https://example.com/access_token/abcdefghijklmnop-qrstuvwxyz012345",
        "https://example.com/secret/abc.defghijklmnopqrstuvwxyz0123456789",
        "https://example.com/session/abcdefghijklmno%2Dpqrstuvwxyz0123456789",
        "https://example.com/Basic/YWxpY2U6c2VjcmV0",
        "https://example.com/basic/YWxpY2U6c2VjcmV0",
        "https://example.com/Basic%2FYWxpY2U6c2VjcmV0",
        "https://docs.example.com/resource;token=secretvalue",
        "https://docs.example.com/resource;api_key=secretvalue",
        "https://docs.example.com/resource;token/secretvalue",
        "https://docs.example.com/resource%3Btoken%3Dsecretvalue",
        "https://docs.example.com/resource;basic/dXNlcjpwYXNz",
        "https://docs.example.com/;Basic/YWxpY2U6c2VjcmV0",
        "https://docs.example.com/?q=;Basic/YWxpY2U6c2VjcmV0",
        "https://docs.example.com/#q=;Basic/YWxpY2U6c2VjcmV0",
        "https://docs.example.com/?q=Basic/YWxpY2U6c2VjcmV0",
        "https://docs.example.com/#q=Basic/YWxpY2U6c2VjcmV0",
        "https://docs.example.com/?q=basic%2FYWxpY2U6c2VjcmV0",
        "Basic dXNlcjpwYXNz",
        "value: &value\n  x\ntoken: *value",
        "{value: &value x, token: *value}",
        "value: &value x\n? token\n: *value",
        "value: &value x\n!!str token: *value",
        "token: !<tag:yaml.org,2002:str> |\n  x",
        "? token\n: |\n  x",
        "value: &value x\ntoken: *value\ntoken: <token>",
        "{value: &value x, token: *value, token: <token>}",
        "---\nsafe: docs\n---\nvalue: &value x\ntoken: *value",
        "value: &value !!binary eA==\ntoken: *value",
        "value: &value !!timestamp 2026-01-01\ntoken: *value",
        "value: &value !secret x\ntoken: *value",
        "!!binary dG9rZW4=: x",
        "? !!binary dG9rZW4=\n: x",
        "!<tag:yaml.org,2002:binary> dG9rZW4=: x",
        "key: &k !!binary dG9rZW4=\n*k: x",
        "> token: |\n>   secretvalue",
        "> value: &v secretvalue\n> token: *v",
        "&gt; token: |\n&gt;   secretvalue",
        "<p>token: |\n  secretvalue</p>",
        "authorization:\n  state: Basic YWxpY2U6c2VjcmV0\n  evidence: []",
        "authorization:\n  state: not_recorded\n  evidence: [Basic YWxpY2U6c2VjcmV0]",
        "config: |\n  value: &v secretvalue\n  token: *v",
        "config: >\n  value: &v secretvalue\n  token: *v",
        "config: |\n  ? !!str token\n  : secretvalue",
        '{"config":"value: &v secretvalue\\ntoken: *v"}',
        'config: "value: &v secretvalue\\ntoken: *v"',
        'outer: { config: "value: &v secretvalue\\ntoken: *v" }',
        "config: |\n  <span>value: &v secretvalue<br>token: *v</span>",
        "config: |\n  > value: &v secretvalue\n  > token: *v",
        "`{value: &v secretvalue, token: *v}`",
        "to**ken**: secretvalue",
        "to_ken_: secretvalue",
        "to`ken`: secretvalue",
        '<div data-config="{value: &v secretvalue, token: *v}">safe</div>',
        "? [token]\n: secretvalue",
        "# value: &v secretvalue\n# token: *v",
        "<!-- ? !!str token\n: secretvalue -->",
        "<!DOCTYPE x [ ? !!str token\n: secretvalue ]>",
        "# title\n" + ("ordinary prose line\n" * 129) + "\nvalue: &v secretvalue\ntoken: *v",
        "# title\nordinary prose without colon\n? &v secretvalue\n: x\n? token\n: *v",
        "````markdown\n# title\nordinary prose without colon\nvalue: &v secretvalue\ntoken: *v\n````",
        "~~~~~yaml\n# title\nordinary prose without colon\nvalue: &v secretvalue\ntoken: *v\n~~~~~",
        '<?config data="{value: &v secretvalue, token: *v}"?>',
        "!foo {value: &v secretvalue, token: *v}",
        "!foo\n- value: &v secretvalue\n- token: *v",
        "%TAG !e! tag:example.com,2026:\n---\n!e!foo {value: &v secretvalue, token: *v}",
        "# title\n!foo {value: &v secretvalue, token: *v}",
        "````yaml\n# title\n!foo {value: &v secretvalue, token: *v}\n````",
        "<span>notice</span>\n!foo {value: &v secretvalue, token: *v}",
        "> notice\n> !foo {value: &v secretvalue, token: *v}",
        "&root !foo {value: &v secretvalue, token: *v}",
        "- !foo {value: &v secretvalue, token: *v}",
        "- !foo\n  value: &v secretvalue\n  token: *v",
        "- &root !foo {value: &v secretvalue, token: *v}",
        "!!set {value: &v secretvalue, token: *v}",
        "!!omap [{value: &v secretvalue}, {token: *v}]",
        "!!pairs [{value: &v secretvalue}, {token: *v}]",
        "- !!set {value: &v secretvalue, token: *v}",
    ],
)
def test_handoff_bundle_rejects_semantic_yaml_and_remote_credential_bypasses(reference: str):
    """Runtime and upload package must share every portable privacy boundary."""

    bundle = _bundle()
    bundle["packet"]["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="credential or personal contact"):
        validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "https://docs.example.com/token=overview",
        "https://docs.example.com/session=overview",
        "https://docs.example.com/token=release-notes-2026-with-long-title",
        "https://docs.example.com/api_key=public-api-docs-v2",
        "https://docs.example.com/token/overview",
        "https://docs.example.com/resource;token=overview",
        "https://docs.example.com/resource;token/overview",
        "https://docs.example.com/Us\u200bers/reference",
        "token:\n  type: string",
        "token: <token>",
        "token: {type: string, default: <token>}",
        "api_key: {examples: [<token>, redacted]}",
        "authorization: {properties: {state: {enum: [user_provided_current_task, authorized_direct_bridge]}}}",
        "authorization:\n  state: not_recorded\n  evidence: []",
        "Basic authentication may be unavailable.",
        "Basic education",
        "config: |\n  token:\n    type: string",
        "config: |\n  token: <token>",
        "`token: <token>`",
        "# token: <token>",
    ],
)
def test_handoff_bundle_allows_public_route_and_declarative_sensitive_word_controls(reference: str):
    bundle = _bundle()
    bundle["packet"]["feedback_events"][0]["problem"] = reference
    assert validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))["bundle_id"] == "office-bundle-001"


@pytest.mark.parametrize("reference", ["\x00", "\x1b[31m", "\x7f", chr(0xD800)])
def test_handoff_bundle_rejects_nonportable_control_characters(reference: str):
    bundle = _bundle()
    bundle["packet"]["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="NUL character|forbidden control character|invalid Unicode surrogate"):
        validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "!!binary L1VzZXJzL21hYy9wcml2YXRlL2RlY2sucHB0eA==",
        "!!binary dG9rZW46IHNlY3JldHZhbHVl",
        "!!binary YWxpY2VAZXhhbXBsZS5jb20=",
        "!!binary dmFsdWU6ICZ2IHNlY3JldHZhbHVlCnRva2VuOiAqdg==",
    ],
)
def test_handoff_bundle_rejects_privacy_values_decoded_from_yaml_binary(reference: str):
    bundle = _bundle()
    bundle["packet"]["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="non-portable local reference|credential or personal contact"):
        validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))


@pytest.mark.parametrize("reference", ["!!binary AA==", "!!binary AQ==", "!!binary fw=="])
def test_handoff_bundle_rejects_forbidden_controls_decoded_from_yaml_binary(reference: str):
    bundle = _bundle()
    bundle["packet"]["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="credential or personal contact"):
        validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))


@pytest.mark.parametrize(
    "reference",
    [
        "token:\n  default: credentialvalue123",
        "api_key: {examples: [credentialvalue123]}",
        "password: {const: credentialvalue123}",
        "token: {x-private-value: credentialvalue123}",
        "token: {properties: {api_key: {default: credentialvalue123}}}",
    ],
)
def test_handoff_bundle_rejects_concrete_values_hidden_in_sensitive_schema_descriptors(reference: str):
    bundle = _bundle()
    bundle["packet"]["feedback_events"][0]["problem"] = reference
    with pytest.raises(PortablePacketError, match="credential or personal contact"):
        validate_handoff_bundle_v1(json.dumps(bundle).encode("utf-8"))


_LIFECYCLE_LOCK_PROBE = """
import fcntl
import os
import sys

descriptor = os.open(sys.argv[1], os.O_RDWR)
try:
    try:
        fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0, os.SEEK_SET)
    except OSError:
        print('BLOCKED')
    else:
        print('ACQUIRED')
finally:
    os.close(descriptor)
"""


@pytest.mark.skipif(os.name == "nt", reason="Exercises the POSIX record-lease implementation")
@pytest.mark.parametrize("guarded", [False, True], ids=["descriptor-bound", "guarded-posix-test-seam"])
def test_packet_store_keeps_the_work_json_lifecycle_lease_through_status_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guarded: bool
):
    """A status read must not close a same-inode FD and drop the process lock."""

    from aippt_studio import portable_packets

    if guarded:
        _force_guarded_windows_fallback(monkeypatch)
        store_type = portable_packets._GuardedWindowsPacketStore
    else:
        store_type = portable_packets._SecurePacketStore
    work = _work(tmp_path)
    snapshot = portable_packets._capture_import_work_identity_snapshot(tmp_path, work)

    def probe() -> str:
        return subprocess.check_output(
            [sys.executable, "-c", _LIFECYCLE_LOCK_PROBE, str(work / "work.json")], text=True
        ).strip()

    assert probe() == "ACQUIRED"
    with store_type(tmp_path, "1", expected_tree=snapshot) as store:
        assert probe() == "BLOCKED"
        store.assert_work_current_and_importable()
        assert probe() == "BLOCKED"
    assert probe() == "ACQUIRED"


@pytest.mark.skipif(os.name == "nt", reason="Exercises POSIX process-scoped fcntl record-lock semantics")
def test_same_process_imports_serialize_before_opening_a_second_work_lifecycle_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A second Studio import cannot open work.json before the first exits.

    POSIX closes every process-held record lock for an inode when *any* same-
    process descriptor for that inode closes. Pausing the first transaction
    after staging and proving the second has not entered its packet store
    establishes the process-local guard around the whole lifecycle lease.
    """

    from aippt_studio import portable_packets

    work = _work(tmp_path)
    source_a = tmp_path / "handoff-a.json"
    source_b = tmp_path / "handoff-b.json"
    raw_a = _write_json(source_a, _bundle())
    second_bundle = copy.deepcopy(_bundle())
    second_bundle["bundle_id"] = "office-bundle-002"
    second_bundle["packet"]["packet_id"] = "office-packet-002"
    second_bundle["packet"]["idempotency_key"] = "office-packet-002-revision-4"
    _write_json(source_b, second_bundle)

    first_staged = threading.Event()
    release_first = threading.Event()
    second_entered_store = threading.Event()
    outcomes: dict[str, object] = {}
    original_verify = portable_packets._SecurePacketStore.verify_staged_packet_directory
    original_enter = portable_packets._SecurePacketStore.__enter__

    def pause_first_after_verify(self, directory_fd, **kwargs):
        original_verify(self, directory_fd, **kwargs)
        if kwargs["raw"] == raw_a:
            first_staged.set()
            assert release_first.wait(timeout=10)

    def track_store_entry(self):
        if threading.current_thread().name == "packet-import-b":
            second_entered_store.set()
        return original_enter(self)

    monkeypatch.setattr(portable_packets._SecurePacketStore, "verify_staged_packet_directory", pause_first_after_verify)
    monkeypatch.setattr(portable_packets._SecurePacketStore, "__enter__", track_store_entry)

    def run(label: str, source: Path) -> None:
        try:
            outcomes[label] = import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")
        except BaseException as error:  # Thread assertions must reach the parent test.
            outcomes[label] = error

    first = threading.Thread(target=run, args=("first", source_a), name="packet-import-a")
    second = threading.Thread(target=run, args=("second", source_b), name="packet-import-b")
    first.start()
    assert first_staged.wait(timeout=10)
    second.start()
    try:
        assert not second_entered_store.wait(timeout=0.4)
    finally:
        release_first.set()
        first.join(timeout=15)
        second.join(timeout=15)

    assert not first.is_alive()
    assert not second.is_alive()
    assert isinstance(outcomes.get("first"), dict)
    assert isinstance(outcomes.get("second"), dict)
    assert outcomes["first"]["status"] == "received_external_unverified"  # type: ignore[index]
    assert outcomes["second"]["status"] == "received_external_unverified"  # type: ignore[index]


@pytest.mark.skipif(os.name == "nt", reason="Exercises the POSIX record-lease implementation")
def test_packet_import_rejects_a_concurrently_held_work_lifecycle_lease(tmp_path: Path):
    """A separate lifecycle owner prevents any bridge storage mutation."""

    work = _work(tmp_path)
    source = tmp_path / "handoff.json"
    _write_json(source, _bundle())
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, os, sys, time; fd=os.open(sys.argv[1], os.O_RDWR); fcntl.lockf(fd, fcntl.LOCK_EX); print('HELD', flush=True); time.sleep(15)",
            str(work / "work.json"),
        ],
        text=True,
        stdout=subprocess.PIPE,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "HELD"
        with pytest.raises(PortablePacketError, match="Work lifecycle is busy"):
            import_packet(tmp_path, work, source, origin="chatgpt-powerpoint")
        assert not (work / "workspace" / "bridge-packets").exists()
    finally:
        holder.terminate()
        holder.wait(timeout=10)
