"""Safe portable PowerPoint handoff retention and guidance export.

The ChatGPT/PowerPoint skill is intentionally portable. Its handoff packet is
useful evidence for the current local Work, but it is not a substitute for a
bound raw conversation, a local visual review, tool evidence, or user
acceptance. This module therefore has two deliberately narrow responsibilities:

* retain a validated external packet immutably inside its named Work; and
* export de-identified, already-active authoring guidance for a user to attach
  to a later Office session.

Neither operation calls feedback auditing, semantic evolution, project learning,
maintenance, delivery preparation, or completion. A separate, explicit review
step remains necessary before a packet can influence a local project.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import ctypes
import errno
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:  # POSIX descriptor-bound packet storage; Windows uses the guarded fallback below.
    import fcntl
except ImportError:  # pragma: no cover - exercised by host capability checks.
    fcntl = None  # type: ignore[assignment]

try:  # Windows-only lifecycle lease; imported lazily on POSIX test hosts.
    import msvcrt
except ImportError:  # pragma: no cover - unavailable outside Windows.
    msvcrt = None  # type: ignore[assignment]

from filelock import FileLock, Timeout

from .portable_contracts import (
    PortableContractError,
    is_rfc3339_datetime,
    validate_contract_instance,
)
from .portable_safety import (
    PortableTextDecodingError,
    contains_portable_forbidden_control_character,
    contains_portable_unpaired_surrogate,
    contains_portable_local_reference,
    contains_portable_sensitive_value,
    contains_portable_studio_reference,
    has_portable_sensitive_setting_value,
    portable_text_variants,
)


PACKET_SCHEMA = "aippt-learning-packet-v1"
BUNDLE_SCHEMA = "aippt-handoff-bundle-v1"
GUIDANCE_SCHEMA = "aippt-guidance-snapshot-v1"
RECEIPT_SCHEMA = "aippt-external-handoff-receipt-v1"
MAX_PACKET_BYTES = 256 * 1024
MAX_JSON_DEPTH = 16
MAX_CONTAINER_ITEMS = 512
MAX_STRING_BYTES = 24 * 1024

_PACKET_REQUIRED = {
    "schema",
    "packet_id",
    "created_at",
    "source_surface",
    "claim_provenance",
    "source_revision",
    "transfer_scope",
    "user_authorization",
    "idempotency_key",
    "transmission_state",
    "transmission_evidence",
    "presentation_intent_ref",
    "capability_report_ref",
    "feedback_events",
    "tool_execution",
    "artifact_review",
    "user_acceptance",
    "unresolved_items",
}
_PACKET_OPTIONAL = {
    "expression_review_ref",
    "guidance_snapshot_ref",
    "guidance_dispositions",
}
_BUNDLE_REQUIRED = {
    "schema",
    "bundle_id",
    "created_at",
    "source_surface",
    "claim_provenance",
    "source_revision",
    "transfer_scope",
    "packet",
    "presentation_intent",
    "capability_report",
}
_BUNDLE_OPTIONAL = {"expression_review"}
_TRANSMISSION_STATES = {"prepared_not_transmitted", "transmitted"}
_DIGEST_DIRECTORY = re.compile(r"[0-9a-f]{64}")
_PORTABLE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,159}")
_RECEIPT_REQUIRED = frozenset(
    {
        "schema",
        "received_at",
        "work_id",
        "origin",
        "bundle_id",
        "bundle_sha256",
        "bundle_bytes",
        "packet_id",
        "idempotency_key",
        "receipt_state",
        "evidence_class",
        "raw_conversation_provenance",
        "automatic_learning",
        "acceptance_claim",
        "tool_execution_claim",
        "capability_claim",
        "visual_review_claim",
        "authorization_claim",
        "attachment_transfer",
        "triage",
        "limits",
    }
)
_RECEIPT_TRUTH_BOUNDARIES = {
    "receipt_state": "received_external_unverified",
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
_RECEIPT_LIMITS = (
    "The packet is retained as external evidence only. It has not changed work status, user approval, "
    "feedback audit, semantic guidance, project learning, QA, delivery, or Studio core."
)
# The portable snapshot never copies free-form learned text. Its rules are a
# small, source-controlled vocabulary with no customer/project content. Local
# archival learning can choose one of the generic families below, but a raw
# quote, name, source fact, path, address, URL or credential never leaves the
# local evidence chain.
_PORTABLE_BASE_RULES = (
    {
        "rule_id": "base:source_deck_redesign",
        "action": "For whole-deck beautification, inventory source facts, images, charts, tables, notes, media and page roles; map every source page into the new deck rhythm and verify preservation at the level the current host can prove.",
        "applies_when": "A supplied PowerPoint is being redesigned throughout rather than locally patched.",
        "exceptions": ["The user may explicitly authorize content or page-order changes; record that scope before redistributing source material."],
    },
    {
        "rule_id": "base:document_to_deck",
        "action": "Turn Word, notes or other source material into a claim-and-evidence page map before composing; preserve the source's names, quantities and limits without turning every paragraph into a slide.",
        "applies_when": "Creating a new presentation from long-form material or a brief.",
        "exceptions": ["A supplied template or required page structure may constrain the order while the factual coverage still needs checking."],
    },
    {
        "rule_id": "base:style_grammar",
        "action": "Choose the deck's narrative, layout rhythm, image language and audience voice from its occasion and any actually accessible portfolio reference. Keep distinct page roles coherent without imposing one repeated layout.",
        "applies_when": "Selecting a visual direction for a new deck or an overall redesign.",
        "exceptions": ["Do not claim to have inspected a private portfolio that the current task has not supplied or connected."],
    },
    {
        "rule_id": "base:visual_sourcing",
        "action": "Use supplied visuals first; use available web or connected search for real evidence images, and available image generation for conceptual illustrations. Check provenance, relevance, visible crop and host insertion before claiming the visual was used.",
        "applies_when": "A slide needs a visual that is missing from the supplied material.",
        "exceptions": ["Honor source-only boundaries and never present generated imagery as documentary evidence of a real person, event, place, product or measured result."],
    },
    {
        "rule_id": "base:audience_message",
        "action": "Start with what the audience should understand from the page. Keep only claims, evidence, explanation or necessary navigation that support that message.",
        "applies_when": "Planning or revising any audience-facing slide.",
        "exceptions": ["Preserve a required source fact or requested navigation when it has a real audience purpose."],
    },
    {
        "rule_id": "base:formal_copy",
        "action": "Write slide copy as a presenter addressing this audience and occasion. Keep assistant dialogue, editing instructions, QA reminders and process notes out of visible slide copy and speaker notes.",
        "applies_when": "Writing, shortening or revising presentation text.",
        "exceptions": ["Specific project terms and real technical steps belong on a slide when they convey an audience-relevant fact."],
    },
    {
        "rule_id": "base:semantic_hierarchy",
        "action": "Make visual emphasis proportional to meaning and to the whole page. Read headings, supporting content and empty space together before accepting hierarchy.",
        "applies_when": "A page uses contrasting type sizes, weights, columns or callouts.",
        "exceptions": ["A strong contrast is appropriate when the audience needs to see a real quantity, conclusion or decision first."],
    },
    {
        "rule_id": "base:deck_continuity",
        "action": "Check the page before and after in the argument. Repair missing premises, repeated conclusions and disconnected sections in the deck itself.",
        "applies_when": "A revision changes a page message, sequence or section transition.",
        "exceptions": ["A deliberately independent appendix or reference page may have a distinct role."],
    },
    {
        "rule_id": "base:self_review",
        "action": "Inspect the actual page at presentation scale and in adjacent-page context. Judge wording, purpose, hierarchy, spacing, image relevance and obstruction as one composition before reporting it ready.",
        "applies_when": "A slide has new or materially revised content, layout, type or imagery.",
        "exceptions": ["If the host cannot show a page view, record that limitation instead of claiming visual review."],
    },
    {
        "rule_id": "base:precise_revision",
        "action": "Preserve accepted content and non-target objects during a local revision, then inspect the complete affected page after the edit.",
        "applies_when": "Making a narrow correction to an existing presentation.",
        "exceptions": ["Broader changes require an explicit new scope or a new composition route."],
    },
    {
        "rule_id": "base:meaningful_navigation",
        "action": "Avoid decorative corner ordinals, empty numbered circles, arbitrary index tags and ornament-only rules. Keep numerals only when they express a real quantity, unit, comparison, requested navigation, chapter or step.",
        "applies_when": "A layout includes numbers, labels, badges, corner marks or navigation treatments.",
        "exceptions": ["Preserve meaningful audience navigation and complete quantitative evidence."],
    },
    {
        "rule_id": "base:coherent_composition",
        "action": "Create visible relationships among content, hierarchy and space. Vary page roles with the argument instead of repeating a generic card layout.",
        "applies_when": "Arranging text, images, diagrams or evidence on a page.",
        "exceptions": ["A repeated structure is acceptable when it supports a deliberate comparison or sequence."],
    },
    {
        "rule_id": "base:image_claim_fidelity",
        "action": "Map each image, diagram and evidence visual to the page's exact claim. Check relevance, source identity, prior use and crop before placement.",
        "applies_when": "Selecting, reusing or placing a visual.",
        "exceptions": ["A repeated visual can be retained when its role changes and that role is clear to the audience."],
    },
    {
        "rule_id": "base:image_surface_geometry",
        "action": "Treat the visible image, crop, border and backing as one composition. Make the actual image edge match its visible rounded, elliptical or rectangular frame, and inspect for exposed corners, strips, clipping and rotation mismatch.",
        "applies_when": "A photo or screenshot is placed inside a shaped visual treatment.",
        "exceptions": ["Keep a rectangular source edge when it is intentional evidence and the surrounding frame matches it."],
    },
    {
        "rule_id": "base:complete_evidence",
        "action": "Preserve source names, entities, units, percentage signs, comparison bases and evidence roles. A headline may claim a change only when the supplied material establishes the comparison.",
        "applies_when": "Editing evidence, charts, tables, labels or factual claims.",
        "exceptions": ["Do not add unsupported facts merely to fill a layout."],
    },
)
_PORTABLE_LEARNED_FAMILIES = {
    "request_routing": {
        "action": "Classify the requested work before editing: exact local revision, template derivative, new composition, faithful reconstruction or visual alternate. Choose the route from audience, occasion, source boundary and presentation form.",
        "applies_when": "A new PowerPoint request or substantial change arrives.",
        "exceptions": ["Ask for the missing source or style evidence when the requested route cannot be determined safely."],
    },
    "host_capability": {
        "action": "Record the host capability for each claimed operation separately. Do not infer save, reopen, export, playback or transfer success from a visible text edit.",
        "applies_when": "Reporting a completed edit, export, review or handoff.",
        "exceptions": ["When an operation is unavailable or untested, report that limit and provide the supported result."],
    },
    "macos_runtime": {
        "action": "Use the host-native presentation runtime and report a clear capability limit when a required local operation is unavailable. Do not treat a copied incompatible runtime as evidence of success.",
        "applies_when": "A local presentation build, render, export or native validation is required.",
        "exceptions": ["A portable Office session can still provide planning or visible-content review when native operations are unavailable."],
    },
    "whole_page_expression": {
        "action": "Review the whole page for purpose, audience voice, hierarchy, density, overlap, image fit and adjacent-page logic before describing it as ready.",
        "applies_when": "A visual or editorial defect recurs across tasks.",
        "exceptions": ["A measurement or automated check can locate a question but cannot replace visible judgment."],
    },
    "image_geometry": {
        "action": "When a visual frame is rounded or elliptical, crop or mask the actual image to that geometry and verify the rendered edge after editing.",
        "applies_when": "A photo, screenshot or illustration sits in a shaped container.",
        "exceptions": ["Retain rectangular edges when the evidence itself requires them and the frame is intentionally rectangular."],
    },
    "meaningful_navigation": {
        "action": "Remove decorative indexing that does not help the audience navigate, compare or understand the argument.",
        "applies_when": "A repeated numbered badge, corner label or ornamental rule appears.",
        "exceptions": ["Keep actual page navigation, chapter markers, quantities and process steps that serve the audience."],
    },
    "source_boundary": {
        "action": "Keep supplied facts, named entities and evidence roles intact. Treat missing source context as a limit, not permission to invent content.",
        "applies_when": "Reconstructing, adapting or summarizing supplied materials.",
        "exceptions": ["Use external material only when the current task explicitly authorizes it."],
    },
}
_FAMILY_KEYWORDS = {
    "request_routing": ("template", "style", "route", "form", "模板", "风格", "分类", "竞赛", "汇报", "政府", "党政"),
    "host_capability": ("capability", "save", "reopen", "export", "pdf", "animation", "bridge", "能力", "保存", "导出", "播放", "回执"),
    "macos_runtime": ("mac", "macos", "windows", "runtime", "rosetta", "powershell", "运行时", "系统", "兼容"),
    "image_geometry": ("image", "photo", "crop", "mask", "rounded", "ellipse", "图片", "图像", "照片", "裁剪", "圆角", "椭圆", "边缘", "白边"),
    "meaningful_navigation": ("ordinal", "number", "navigation", "编号", "序号", "页码", "角标", "01", "02", "03", "04"),
    "whole_page_expression": ("hierarchy", "layout", "typography", "overlap", "page", "视觉", "排版", "层级", "小字", "遮挡", "页面", "表达"),
    "source_boundary": ("source", "evidence", "reference", "fact", "资料", "来源", "证据", "事实", "引用"),
}



class PortablePacketError(ValueError):
    """A portable packet or its destination does not meet the safety contract."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise PortablePacketError(f"Duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_excessive_json_nesting(text: str) -> None:
    """Bound structural nesting before the recursive JSON decoder runs.

    ``json.loads`` can raise ``RecursionError`` before the later value walker
    sees a deeply nested but small untrusted payload. This small lexical pass
    deliberately recognizes only strings/escapes and bracket depth; the JSON
    decoder remains responsible for full syntax validation.
    """

    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise PortablePacketError(f"Packet exceeds the maximum JSON nesting depth ({MAX_JSON_DEPTH})")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                # Leave malformed bracket ordering to json.loads so callers
                # receive the same controlled invalid-JSON error.
                depth = 0


def _parse_json_bytes(data: bytes) -> dict[str, Any]:
    if not data:
        raise PortablePacketError("Packet is empty")
    if len(data) > MAX_PACKET_BYTES:
        raise PortablePacketError(f"Packet exceeds {MAX_PACKET_BYTES} byte limit")
    try:
        decoded = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise PortablePacketError("Packet must be UTF-8 JSON") from error
    _reject_excessive_json_nesting(decoded)
    try:
        result = json.loads(decoded, object_pairs_hook=_reject_duplicates)
    except (json.JSONDecodeError, PortablePacketError, RecursionError, MemoryError, ValueError) as error:
        raise PortablePacketError(f"Packet is not valid JSON: {error}") from error
    if not isinstance(result, dict):
        raise PortablePacketError("Packet root must be a JSON object")
    return result


def _require_text(value: object, field: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortablePacketError(f"{field} must be a non-empty string")
    if contains_portable_unpaired_surrogate(value):
        raise PortablePacketError(f"{field} contains an invalid Unicode surrogate")
    if len(value.encode("utf-8")) > MAX_STRING_BYTES:
        raise PortablePacketError(f"{field} exceeds the portable text limit")
    if identifier and _PORTABLE_IDENTIFIER.fullmatch(value) is None:
        raise PortablePacketError(f"{field} must be a portable identifier")
    return value


def _require_time(value: object, field: str) -> str:
    text = _require_text(value, field)
    if not is_rfc3339_datetime(text):
        raise PortablePacketError(f"{field} must be an RFC 3339 timestamp with timezone")
    return text


def _scan_portable_value(value: Any, *, field: str = "packet", depth: int = 0) -> None:
    """Bound arbitrary evidence fields without treating their text as instructions."""

    if depth > MAX_JSON_DEPTH:
        raise PortablePacketError(f"{field} exceeds the maximum JSON nesting depth")
    if isinstance(value, str):
        if contains_portable_unpaired_surrogate(value):
            raise PortablePacketError(f"{field} contains an invalid Unicode surrogate")
        if len(value.encode("utf-8")) > MAX_STRING_BYTES:
            raise PortablePacketError(f"{field} contains an oversized string")
        try:
            text_variants = portable_text_variants(value)
        except PortableTextDecodingError as error:
            raise PortablePacketError(f"{field} contains excessively nested encodings") from error
        if "\x00" in value:
            raise PortablePacketError(f"{field} contains a NUL character")
        if any(contains_portable_forbidden_control_character(candidate) for candidate in text_variants):
            raise PortablePacketError(f"{field} contains a forbidden control character")
        if contains_portable_local_reference(value) or contains_portable_studio_reference(value):
            raise PortablePacketError(f"{field} contains a non-portable local reference")
        if contains_portable_sensitive_value(value):
            raise PortablePacketError(f"{field} contains a credential or personal contact reference")
        return
    if value is None or isinstance(value, (bool, int, float)):
        return
    if isinstance(value, list):
        if len(value) > MAX_CONTAINER_ITEMS:
            raise PortablePacketError(f"{field} contains too many items")
        for index, item in enumerate(value):
            _scan_portable_value(item, field=f"{field}[{index}]", depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > MAX_CONTAINER_ITEMS:
            raise PortablePacketError(f"{field} contains too many fields")
        for key, item in value.items():
            _require_text(key, f"{field} key")
            # A decoded mapping preserves the association that a free-form
            # text scanner cannot see.  YAML anchors/tags are relevant to the
            # upload gate and JSON objects can carry the same carrier/value
            # shape in packet evidence, so reject either representation here.
            if has_portable_sensitive_setting_value(key, item):
                raise PortablePacketError(f"{field}.{key} contains a credential or personal contact reference")
            _scan_portable_value(item, field=f"{field}.{key}", depth=depth + 1)
        return
    raise PortablePacketError(f"{field} contains an unsupported JSON value")


def _require_object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PortablePacketError(f"{field} must be an object")
    return value


def _require_list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise PortablePacketError(f"{field} must be a list")
    return value


def _strict_object(
    value: object,
    field: str,
    *,
    required: set[str],
    allowed: set[str] | None = None,
) -> dict[str, Any]:
    result = _require_object(value, field)
    permitted = required if allowed is None else allowed
    unknown = set(result) - permitted
    missing = required - set(result)
    if unknown:
        raise PortablePacketError(f"{field} has unknown fields: {sorted(unknown)}")
    if missing:
        raise PortablePacketError(f"{field} is missing required fields: {sorted(missing)}")
    return result


def _enum(value: object, field: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise PortablePacketError(f"{field} must be one of {sorted(allowed)}")
    return value


def _string_list(value: object, field: str, *, maximum: int) -> list[str]:
    result = _require_list(value, field)
    if len(result) > maximum or any(not isinstance(item, str) or not item.strip() for item in result):
        raise PortablePacketError(f"{field} must contain at most {maximum} non-empty strings")
    return result


def _validate_canonical_contract(value: object, schema_name: str) -> None:
    """Validate the released schema before any source-side semantic checks.

    The local receiver still treats a valid source report as external,
    unverified evidence. This step prevents a weaker handwritten parser from
    accepting records the published contract rejects.
    """

    try:
        validate_contract_instance(value, schema_name)
    except PortableContractError as error:
        raise PortablePacketError(f"{schema_name} canonical validation failed: {error}") from error


def validate_packet_v1(raw_bytes: bytes) -> dict[str, Any]:
    """Validate a source-side learning record before bundle retention."""

    return _validate_packet_v1_value(_parse_json_bytes(raw_bytes))


def _validate_packet_v1_value(packet: dict[str, Any]) -> dict[str, Any]:
    """Validate an outbound source report without retaining or trusting it."""

    _validate_canonical_contract(packet, PACKET_SCHEMA)
    if packet.get("claim_provenance") != "source_report_unverified":
        raise PortablePacketError("Packet claim_provenance must be source_report_unverified")
    if packet.get("source_surface") != "chatgpt-powerpoint":
        raise PortablePacketError("source_surface must be chatgpt-powerpoint for v1")
    _require_text(packet["packet_id"], "packet_id", identifier=True)
    _require_time(packet["created_at"], "created_at")
    _bundle_source_revision(packet["source_revision"], "source_revision")
    _bundle_transfer_scope(packet["transfer_scope"], "transfer_scope")
    authorization = _strict_object(
        packet["user_authorization"],
        "user_authorization",
        required={"state", "scope", "evidence"},
    )
    authorization_state = _enum(authorization["state"], "user_authorization.state", {"recorded", "not_recorded", "denied"})
    authorization_scope = _enum(
        authorization["scope"],
        "user_authorization.scope",
        {"structured_summary_only", "not_authorized"},
    )
    _string_list(authorization["evidence"], "user_authorization.evidence", maximum=32)
    _require_text(packet["idempotency_key"], "idempotency_key", identifier=True)
    state = _enum(packet["transmission_state"], "transmission_state", _TRANSMISSION_STATES)
    evidence = _string_list(packet["transmission_evidence"], "transmission_evidence", maximum=32)
    if state == "prepared_not_transmitted" and evidence:
        raise PortablePacketError("prepared_not_transmitted cannot include transmission evidence")
    if state == "transmitted" and (
        authorization_state != "recorded" or authorization_scope != "structured_summary_only" or not authorization["evidence"] or not evidence
    ):
        raise PortablePacketError("transmitted requires recorded structured-summary authorization and transmission evidence")
    _require_text(packet["presentation_intent_ref"], "presentation_intent_ref", identifier=True)
    _require_text(packet["capability_report_ref"], "capability_report_ref", identifier=True)
    has_guidance_snapshot = "guidance_snapshot_ref" in packet
    has_guidance_dispositions = "guidance_dispositions" in packet
    if has_guidance_snapshot != has_guidance_dispositions:
        raise PortablePacketError("guidance_snapshot_ref and guidance_dispositions must be supplied together")
    if "expression_review_ref" in packet:
        _require_text(packet["expression_review_ref"], "expression_review_ref", identifier=True)
    _scan_portable_value(packet)
    return packet

def _bundle_source_revision(value: object, field: str) -> dict[str, Any]:
    result = _strict_object(
        value,
        field,
        required={"document_id", "revision", "state", "evidence"},
    )
    _require_text(result["document_id"], f"{field}.document_id")
    _require_text(result["revision"], f"{field}.revision")
    _enum(result["state"], f"{field}.state", {"observed", "unavailable"})
    _string_list(result["evidence"], f"{field}.evidence", maximum=32)
    return result


def _bundle_transfer_scope(value: object, field: str) -> dict[str, Any]:
    result = _strict_object(
        value,
        field,
        required={
            "mode",
            "deck_content_embedded",
            "speaker_notes_embedded",
            "asset_content_embedded",
            "raw_conversation_embedded",
        },
    )
    _enum(
        result["mode"],
        f"{field}.mode",
        {"sanitized_structured_summary_only"},
    )
    for key in (
        "deck_content_embedded",
        "speaker_notes_embedded",
        "asset_content_embedded",
        "raw_conversation_embedded",
    ):
        if result[key] is not False:
            raise PortablePacketError(f"{field}.{key} must be false")
    return result


def _validate_presentation_intent_record(
    value: object, *, source_revision: dict[str, Any]
) -> dict[str, Any]:
    intent = _strict_object(
        value,
        "presentation_intent",
        required={
            "schema",
            "intent_id",
            "created_at",
            "source_revision",
            "operation",
            "presentation_form",
            "audience",
            "occasion",
            "source_boundary",
            "scope",
            "page_messages",
            "capability_limits",
        },
    )
    if intent["schema"] != "aippt-presentation-intent-v1":
        raise PortablePacketError("presentation_intent schema is unsupported")
    _require_text(intent["intent_id"], "presentation_intent.intent_id", identifier=True)
    _require_time(intent["created_at"], "presentation_intent.created_at")
    if _bundle_source_revision(intent["source_revision"], "presentation_intent.source_revision") != source_revision:
        raise PortablePacketError("presentation_intent source_revision does not match bundle source_revision")
    _enum(
        intent["operation"],
        "presentation_intent.operation",
        {"exact_local_revision", "template_derivative", "new_composition", "faithful_reconstruction", "visual_alternate"},
    )
    _enum(
        intent["presentation_form"],
        "presentation_intent.presentation_form",
        {
            "formal_government",
            "business_report",
            "competition_defense",
            "academic_defense",
            "teaching",
            "technical_explanation",
            "ceremonial_narrative",
            "premium_portfolio",
            "other_or_unspecified",
        },
    )
    _require_text(intent["audience"], "presentation_intent.audience")
    _require_text(intent["occasion"], "presentation_intent.occasion")
    source_boundary = _strict_object(
        intent["source_boundary"],
        "presentation_intent.source_boundary",
        required={"authoritative_material", "may_add_external_material", "external_material_authorization"},
    )
    _string_list(source_boundary["authoritative_material"], "presentation_intent.source_boundary.authoritative_material", maximum=64)
    if not isinstance(source_boundary["may_add_external_material"], bool):
        raise PortablePacketError("presentation_intent.source_boundary.may_add_external_material must be boolean")
    _enum(
        source_boundary["external_material_authorization"],
        "presentation_intent.source_boundary.external_material_authorization",
        {"authorized", "not_authorized", "not_applicable", "unknown"},
    )
    scope = _strict_object(
        intent["scope"],
        "presentation_intent.scope",
        required={"target_slides", "preserve", "unknowns", "preservation_basis"},
    )
    _string_list(scope["target_slides"], "presentation_intent.scope.target_slides", maximum=512)
    _string_list(scope["preserve"], "presentation_intent.scope.preserve", maximum=512)
    _string_list(scope["unknowns"], "presentation_intent.scope.unknowns", maximum=128)
    _enum(
        scope["preservation_basis"],
        "presentation_intent.scope.preservation_basis",
        {"current_request", "active_presentation", "provided_reference", "unknown"},
    )
    page_messages = _require_list(intent["page_messages"], "presentation_intent.page_messages")
    if len(page_messages) > 512:
        raise PortablePacketError("presentation_intent.page_messages exceeds 512 entries")
    for index, item in enumerate(page_messages):
        page = _strict_object(
            item,
            f"presentation_intent.page_messages[{index}]",
            required={"page", "message", "evidence_role", "neighbor_logic"},
        )
        for key in ("page", "message", "evidence_role", "neighbor_logic"):
            _require_text(page[key], f"presentation_intent.page_messages[{index}].{key}")
    _string_list(intent["capability_limits"], "presentation_intent.capability_limits", maximum=128)
    return intent


def _validate_capability_record(
    value: object, *, source_revision: dict[str, Any]
) -> dict[str, Any]:
    record = _strict_object(
        value,
        "capability_report",
        required={
            "schema",
            "report_id",
            "created_at",
            "source_surface",
            "source_revision",
            "operations",
            "bridge_mode",
            "connected_apps",
            "limitations",
        },
    )
    if record["schema"] != "aippt-bridge-capabilities-v1":
        raise PortablePacketError("capability_report schema is unsupported")
    _require_text(record["report_id"], "capability_report.report_id", identifier=True)
    _require_time(record["created_at"], "capability_report.created_at")
    if record["source_surface"] != "chatgpt-powerpoint":
        raise PortablePacketError("capability_report source_surface must be chatgpt-powerpoint")
    if _bundle_source_revision(record["source_revision"], "capability_report.source_revision") != source_revision:
        raise PortablePacketError("capability_report source_revision does not match bundle source_revision")
    operation_names = {
        "read_active_slide",
        "edit_text",
        "edit_geometry",
        "render_page",
        "save_reopen_pptx",
        "export_or_inspect_pdf",
        "animation_playback",
        "transmit_handoff",
    }
    operations = _strict_object(record["operations"], "capability_report.operations", required=operation_names)
    for name, operation in operations.items():
        observation = _strict_object(
            operation,
            f"capability_report.operations.{name}",
            required={"state", "evidence"},
        )
        _enum(
            observation["state"],
            f"capability_report.operations.{name}.state",
            {"confirmed", "unavailable", "not_attempted", "unknown"},
        )
        _string_list(observation["evidence"], f"capability_report.operations.{name}.evidence", maximum=32)
    _enum(record["bridge_mode"], "capability_report.bridge_mode", {"direct_bridge", "manual_packet", "isolated_session"})
    _string_list(record["connected_apps"], "capability_report.connected_apps", maximum=32)
    _string_list(record["limitations"], "capability_report.limitations", maximum=128)
    return record


def _validate_expression_review_record(
    value: object, *, source_revision: dict[str, Any]
) -> dict[str, Any]:
    record = _strict_object(
        value,
        "expression_review",
        required={
            "schema",
            "review_id",
            "created_at",
            "document_revision",
            "review_scope",
            "visual_inspection",
            "observations",
            "adjacent_page_check",
            "preservation_check",
            "invalidated_by_changes",
            "review_state",
            "unverified_limits",
        },
    )
    if record["schema"] != "aippt-expression-review-v1":
        raise PortablePacketError("expression_review schema is unsupported")
    _require_text(record["review_id"], "expression_review.review_id", identifier=True)
    _require_time(record["created_at"], "expression_review.created_at")
    revision = _strict_object(
        record["document_revision"],
        "expression_review.document_revision",
        required={"document_id", "pre_review_revision", "reviewed_revision", "binding_state", "evidence"},
    )
    _require_text(revision["document_id"], "expression_review.document_revision.document_id")
    _require_text(revision["pre_review_revision"], "expression_review.document_revision.pre_review_revision")
    _require_text(revision["reviewed_revision"], "expression_review.document_revision.reviewed_revision")
    _enum(
        revision["binding_state"],
        "expression_review.document_revision.binding_state",
        {"bound_to_observed_revision", "revision_unavailable"},
    )
    _string_list(revision["evidence"], "expression_review.document_revision.evidence", maximum=32)
    if revision["document_id"] != source_revision["document_id"] or revision["reviewed_revision"] != source_revision["revision"]:
        raise PortablePacketError("expression_review document revision does not match bundle source_revision")
    _require_list(record["review_scope"], "expression_review.review_scope")
    _require_object(record["visual_inspection"], "expression_review.visual_inspection")
    _require_list(record["observations"], "expression_review.observations")
    _require_object(record["adjacent_page_check"], "expression_review.adjacent_page_check")
    _require_object(record["preservation_check"], "expression_review.preservation_check")
    _require_list(record["invalidated_by_changes"], "expression_review.invalidated_by_changes")
    _enum(
        record["review_state"],
        "expression_review.review_state",
        {"reviewed_current_revision", "invalidated_requires_rereview", "not_reviewed", "not_comparable"},
    )
    _require_list(record["unverified_limits"], "expression_review.unverified_limits")
    return record


def validate_handoff_bundle_v1(raw_bytes: bytes) -> dict[str, Any]:
    """Validate one self-contained external PowerPoint source report.

    Canonical structural validation runs before these semantic bindings. The
    result remains source_report_unverified after import: only the local
    receiver creates a receipt, and no source assertion can change a Work,
    guidance, acceptance or Studio core state.
    """

    bundle = _parse_json_bytes(raw_bytes)
    _validate_canonical_contract(bundle, BUNDLE_SCHEMA)
    if bundle.get("claim_provenance") != "source_report_unverified":
        raise PortablePacketError("bundle claim_provenance must be source_report_unverified")
    _require_text(bundle["bundle_id"], "bundle_id", identifier=True)
    _require_time(bundle["created_at"], "bundle.created_at")
    if bundle["source_surface"] != "chatgpt-powerpoint":
        raise PortablePacketError("bundle source_surface must be chatgpt-powerpoint")
    source_revision = _bundle_source_revision(bundle["source_revision"], "bundle.source_revision")
    transfer_scope = _bundle_transfer_scope(bundle["transfer_scope"], "bundle.transfer_scope")
    packet = _validate_packet_v1_value(_require_object(bundle["packet"], "bundle.packet"))
    if packet["claim_provenance"] != bundle["claim_provenance"]:
        raise PortablePacketError("packet claim_provenance does not match bundle claim_provenance")
    if packet["source_surface"] != bundle["source_surface"]:
        raise PortablePacketError("packet source_surface does not match bundle source_surface")
    if packet["source_revision"] != source_revision:
        raise PortablePacketError("packet source_revision does not match bundle source_revision")
    if packet["transfer_scope"] != transfer_scope:
        raise PortablePacketError("packet transfer_scope does not match bundle transfer_scope")
    intent = _validate_presentation_intent_record(bundle["presentation_intent"], source_revision=source_revision)
    if packet["presentation_intent_ref"] != intent["intent_id"]:
        raise PortablePacketError("packet presentation_intent_ref does not match bundled presentation_intent")
    capability = _validate_capability_record(bundle["capability_report"], source_revision=source_revision)
    if packet["capability_report_ref"] != capability["report_id"]:
        raise PortablePacketError("packet capability_report_ref does not match bundled capability_report")
    has_review_ref = "expression_review_ref" in packet
    has_review = "expression_review" in bundle
    if has_review_ref != has_review:
        raise PortablePacketError("expression review record must be present exactly when packet references it")
    review: dict[str, Any] | None = None
    if has_review:
        review = _validate_expression_review_record(bundle["expression_review"], source_revision=source_revision)
        if packet["expression_review_ref"] != review["review_id"]:
            raise PortablePacketError("packet expression_review_ref does not match bundled expression_review")
        if review["review_state"] == "reviewed_current_revision" and source_revision["state"] != "observed":
            raise PortablePacketError("reviewed_current_revision requires an observed bundle source_revision")
    requires_expression_review = packet["artifact_review"]["state"] == "performed" or any(
        event["verification"]["state"] == "visually_reviewed" for event in packet["feedback_events"]
    )
    if requires_expression_review and review is None:
        raise PortablePacketError("visible artifact or feedback review requires a bundled expression_review")
    handoff_operation = capability["operations"]["transmit_handoff"]
    if packet["transmission_state"] == "transmitted":
        if capability["bridge_mode"] != "direct_bridge":
            raise PortablePacketError("transmitted requires capability_report.bridge_mode direct_bridge")
        if handoff_operation["state"] != "confirmed" or not handoff_operation["evidence"]:
            raise PortablePacketError("transmitted requires confirmed transmit_handoff capability evidence")
    elif handoff_operation["state"] == "confirmed":
        raise PortablePacketError("prepared_not_transmitted cannot claim a confirmed transmit_handoff")
    _scan_portable_value(bundle)
    return bundle

def _atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        try:
            path.chmod(mode)
        except OSError:
            pass
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, value: dict[str, Any], *, mode: int = 0o600) -> None:
    _atomic_write_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), mode=mode)


@dataclass(frozen=True)
class _ImportWorkIdentitySnapshot:
    """Immutable identities for the existing canonical Work intake tree."""

    repo_root: os.stat_result
    work_root: os.stat_result
    work: os.stat_result
    workspace: os.stat_result


_PROCESS_LOCAL_WORK_IMPORT_GUARD = threading.Lock()
_PROCESS_LOCAL_WORK_IMPORT_LOCKS: dict[tuple[int, int], threading.Lock] = {}
_PROCESS_LOCAL_WORK_IMPORT_HELD = threading.local()


@contextmanager
def _same_process_work_import_lease(snapshot: _ImportWorkIdentitySnapshot):
    """Serialize Studio packet imports for one Work inside this Python process.

    POSIX ``fcntl`` record locks belong to the process, not an individual file
    descriptor. If two same-process imports independently opened ``work.json``,
    closing either descriptor could release the other transaction's lease for
    an external lifecycle writer. This keyed, non-reentrant mutex ensures a
    second Studio import cannot open that record until the first transaction
    has completed publishing, cleanup, and its final lifecycle recheck.

    It coordinates this Studio process only. A foreign same-process library
    that opens and closes the same inode outside this lease remains outside the
    advisory-lock contract; the cross-process lifecycle scripts are protected
    by the actual ``work.json`` lease below.
    """

    key = (int(snapshot.work.st_dev), int(snapshot.work.st_ino))
    held = getattr(_PROCESS_LOCAL_WORK_IMPORT_HELD, "keys", None)
    if held is None:
        held = set()
        _PROCESS_LOCAL_WORK_IMPORT_HELD.keys = held
    if key in held:
        raise PortablePacketError("Recursive packet-import for one Work is not supported while its lifecycle lease is active")
    with _PROCESS_LOCAL_WORK_IMPORT_GUARD:
        lock = _PROCESS_LOCAL_WORK_IMPORT_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROCESS_LOCAL_WORK_IMPORT_LOCKS[key] = lock
    lock.acquire()
    held.add(key)
    try:
        yield
    finally:
        held.remove(key)
        lock.release()


def _snapshot_real_directory(path: Path, *, label: str) -> os.stat_result:
    """Capture one existing directory without accepting an indirection."""

    try:
        metadata = os.lstat(path)
    except OSError as error:
        raise PortablePacketError(f"{label} cannot be inspected before packet source validation") from error
    if _is_link_or_reparse_point(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise PortablePacketError(f"{label} must be a real directory, not a link or reparse point")
    if not _has_stable_file_identity(metadata):
        raise PortablePacketError(f"{label} requires a stable filesystem identity")
    return metadata


def _capture_import_work_identity_snapshot(root: Path, work: Path) -> _ImportWorkIdentitySnapshot:
    """Bind the canonical existing Work tree before reading untrusted bytes.

    Reading and validating a source handoff can take long enough for a
    lifecycle or filesystem operation to replace a real directory with another
    real directory of the same name. The storage backends receive this snapshot
    and reject a different tree before they create bridge storage or retain
    external bytes. A second immediate pass makes the multi-component capture
    itself fail closed if the tree changes while it is being observed.
    """

    entries = (
        (root, "Workspace root"),
        (root / "work", "Work root"),
        (work, "Work package"),
        (work / "workspace", "Work workspace"),
    )
    captured = tuple(_snapshot_real_directory(path, label=label) for path, label in entries)
    for (path, label), expected in zip(entries, captured, strict=True):
        current = _snapshot_real_directory(path, label=label)
        if not _same_file_identity(expected, current):
            raise PortablePacketError(f"{label} changed while binding the canonical Work intake tree")
    return _ImportWorkIdentitySnapshot(
        repo_root=captured[0],
        work_root=captured[1],
        work=captured[2],
        workspace=captured[3],
    )


def _import_work_locator(repo: Path | str, work_dir: Path | str) -> tuple[Path, Path]:
    """Return only a stable lexical Work location before storage is opened.

    The returned ``work`` path is not trusted for later filesystem access.
    Descriptor-bound storage opens ``repo/work/<id>`` one component at a time;
    the guarded fallback rechecks every component identity while holding its
    local lock.  Separating the display path from the storage authority avoids
    a check-then-use escape if the ``work`` parent is replaced after CLI input
    validation.
    """

    root = Path(repo).expanduser().resolve()
    requested = Path(work_dir).expanduser().resolve()
    expected_parent = root / "work"
    if requested.parent != expected_parent or not requested.name.isdigit():
        raise PortablePacketError("packet-import requires the canonical work/<N> directory under the supplied workspace root")
    return root, expected_parent / requested.name


def _validate_import_work_record(raw: bytes, *, work_id: str) -> dict[str, Any]:
    """Validate a Work status only after its storage root is anchored safely."""

    try:
        record = _parse_json_bytes(raw)
    except PortablePacketError as error:
        raise PortablePacketError("Work has unreadable work.json") from error
    if not work_id.isdigit() or record.get("work_id") != work_id:
        raise PortablePacketError("work.json work_id must match the physical work directory")
    if record.get("status") not in {"intake", "in_progress"}:
        raise PortablePacketError(
            "packet-import accepts only intake or in_progress Work packages; reopen review/completed Work first"
        )
    return record


def _receipt_triage(packet: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"feedback_index": index, "state": "needs_source_corroboration"}
        for index, _ in enumerate(packet["feedback_events"])
    ]


def _validate_receipt_shape(value: object) -> dict[str, Any]:
    """Validate a locally created receipt before it can affect a re-import.

    The frozen bundle remains untrusted external evidence. A receipt is local
    authority only for the narrow fact that its exact bytes were retained, so a
    partially written or locally tampered receipt must never be accepted as a
    source for output trust boundaries.
    """

    receipt = _require_object(value, "Existing bridge receipt")
    if set(receipt) != _RECEIPT_REQUIRED:
        missing = sorted(_RECEIPT_REQUIRED - set(receipt))
        unexpected = sorted(set(receipt) - _RECEIPT_REQUIRED)
        details = []
        if missing:
            details.append(f"missing {missing}")
        if unexpected:
            details.append(f"unexpected {unexpected}")
        raise PortablePacketError("Existing bridge receipt has an invalid field set: " + "; ".join(details))
    if receipt["schema"] != RECEIPT_SCHEMA:
        raise PortablePacketError("Existing bridge receipt has an unsupported schema")
    _require_time(receipt["received_at"], "Existing bridge receipt.received_at")
    _require_text(receipt["work_id"], "Existing bridge receipt.work_id", identifier=True)
    if receipt["origin"] != "chatgpt-powerpoint":
        raise PortablePacketError("Existing bridge receipt has an unsupported origin")
    _require_text(receipt["bundle_id"], "Existing bridge receipt.bundle_id", identifier=True)
    if not isinstance(receipt["bundle_sha256"], str) or _DIGEST_DIRECTORY.fullmatch(receipt["bundle_sha256"]) is None:
        raise PortablePacketError("Existing bridge receipt has an invalid bundle digest")
    if not isinstance(receipt["bundle_bytes"], int) or isinstance(receipt["bundle_bytes"], bool) or receipt["bundle_bytes"] <= 0:
        raise PortablePacketError("Existing bridge receipt has an invalid bundle byte count")
    _require_text(receipt["packet_id"], "Existing bridge receipt.packet_id", identifier=True)
    _require_text(receipt["idempotency_key"], "Existing bridge receipt.idempotency_key", identifier=True)
    for field, expected in _RECEIPT_TRUTH_BOUNDARIES.items():
        if receipt[field] != expected:
            raise PortablePacketError(f"Existing bridge receipt has an invalid {field}")
    triage = _require_list(receipt["triage"], "Existing bridge receipt.triage")
    if len(triage) > MAX_CONTAINER_ITEMS:
        raise PortablePacketError("Existing bridge receipt has too many triage entries")
    for index, item in enumerate(triage):
        entry = _strict_object(
            item,
            f"Existing bridge receipt.triage[{index}]",
            required={"feedback_index", "state"},
        )
        if not isinstance(entry["feedback_index"], int) or isinstance(entry["feedback_index"], bool) or entry["feedback_index"] < 0:
            raise PortablePacketError("Existing bridge receipt has an invalid triage feedback index")
        if entry["state"] != "needs_source_corroboration":
            raise PortablePacketError("Existing bridge receipt has an invalid triage state")
    if receipt["limits"] != _RECEIPT_LIMITS:
        raise PortablePacketError("Existing bridge receipt has invalid trust-boundary text")
    return receipt


def _read_receipt(path: Path) -> dict[str, Any]:
    try:
        value = _parse_json_bytes(_read_bounded_regular_file(path, label="Existing bridge receipt"))
    except (OSError, json.JSONDecodeError, PortablePacketError) as error:
        raise PortablePacketError(f"Existing bridge receipt is unreadable: {path.name}") from error
    return _validate_receipt_shape(value)


def _validate_existing_receipt(
    receipt: dict[str, Any],
    *,
    work: Path,
    bundle: dict[str, Any],
    packet: dict[str, Any],
    digest: str,
    byte_count: int,
    origin: str,
) -> None:
    """Bind a strict local receipt to this exact frozen source bundle."""

    expected = {
        "work_id": work.name,
        "origin": origin,
        "bundle_id": bundle["bundle_id"],
        "bundle_sha256": digest,
        "bundle_bytes": byte_count,
        "packet_id": packet["packet_id"],
        "idempotency_key": packet["idempotency_key"],
        "triage": _receipt_triage(packet),
    }
    for field, expected_value in expected.items():
        if receipt[field] != expected_value:
            raise PortablePacketError(f"Existing bridge receipt does not match the frozen handoff bundle ({field})")


def _reject_idempotency_conflict(root: Path, packet: dict[str, Any], digest: str) -> None:
    """A packet ID/key may recur only for exactly the same frozen bundle bytes."""

    for directory in sorted(root.iterdir(), key=lambda item: item.name):
        if not directory.is_dir() or _DIGEST_DIRECTORY.fullmatch(directory.name) is None:
            continue
        if directory.is_symlink():
            raise PortablePacketError("External handoff storage contains a symlink")
        receipt_path = directory / "receipt.json"
        if not _existing_regular_file(receipt_path, label="Existing bridge receipt"):
            continue
        receipt = _read_receipt(receipt_path)
        existing_digest = receipt.get("bundle_sha256")
        if not isinstance(existing_digest, str) or _DIGEST_DIRECTORY.fullmatch(existing_digest) is None:
            raise PortablePacketError("Existing bridge receipt has an invalid bundle digest")
        same_packet_id = receipt.get("packet_id") == packet["packet_id"]
        same_key = receipt.get("idempotency_key") == packet["idempotency_key"]
        if (same_packet_id or same_key) and existing_digest != digest:
            conflict = "packet_id" if same_packet_id else "idempotency_key"
            raise PortablePacketError(f"External packet {conflict} was already received with different bundle bytes")


def _receipt(
    *,
    work: Path,
    bundle: dict[str, Any],
    packet: dict[str, Any],
    digest: str,
    byte_count: int,
    origin: str,
) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "received_at": _now(),
        "work_id": work.name,
        "origin": origin,
        "bundle_id": bundle["bundle_id"],
        "bundle_sha256": digest,
        "bundle_bytes": byte_count,
        "packet_id": packet["packet_id"],
        "idempotency_key": packet["idempotency_key"],
        "receipt_state": "received_external_unverified",
        "evidence_class": "external_handoff",
        "raw_conversation_provenance": "absent",
        "automatic_learning": "blocked",
        "acceptance_claim": "untrusted",
        "tool_execution_claim": "unverified",
        "capability_claim": "untrusted",
        "visual_review_claim": "untrusted",
        "authorization_claim": "untrusted",
        "attachment_transfer": "unsupported_in_source_bundle_v1",
        "triage": _receipt_triage(packet),
        "limits": _RECEIPT_LIMITS,
    }


def _is_link_or_reparse_point(metadata: os.stat_result) -> bool:
    """Return true for a symbolic link or Windows reparse point.

    ``lstat`` exposes a reparse-point bit on Windows even when the mode alone
    is not sufficient to identify a link. Treat every reparse point as unsafe
    for external packet/receipt bytes rather than following a junction or
    other indirection outside the bounded Work store.
    """

    if stat.S_ISLNK(metadata.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & reparse)


def _same_file_identity(before: os.stat_result, after: os.stat_result) -> bool:
    """Compare the object inspected by lstat with the opened descriptor."""

    values = (before.st_dev, before.st_ino, after.st_dev, after.st_ino)
    # On a host without O_NOFOLLOW, an unusable file identity would turn a
    # pre-open symlink check into a best-effort check. Refuse that input rather
    # than accept 0 == 0 as proof that the opened descriptor is the same file.
    return all(isinstance(value, int) and value != 0 for value in values) and before.st_dev == after.st_dev and before.st_ino == after.st_ino


def _has_stable_file_identity(metadata: os.stat_result) -> bool:
    """Whether this filesystem exposes an identity usable for guarded storage."""

    return (
        isinstance(metadata.st_dev, int)
        and metadata.st_dev != 0
        and isinstance(metadata.st_ino, int)
        and metadata.st_ino != 0
    )


def _read_bounded_regular_file(path: Path | str, *, label: str, maximum_bytes: int = MAX_PACKET_BYTES) -> bytes:
    """Read a bounded regular file without following a leaf link or reparse point.

    A pre-open ``lstat`` rejects a leaf symlink/reparse point on every host.
    We then use ``O_NOFOLLOW`` where the host exposes it and compare the
    pre-open object identity with ``fstat`` after opening. The identity check
    remains necessary on hosts without ``O_NOFOLLOW`` and closes the gap where
    a symlink is substituted between the preflight and open calls. The bounded
    descriptor read also avoids allocating from a growing file or blocking on
    a FIFO replacement.
    """

    source = Path(path).expanduser()
    try:
        before = os.lstat(source)
    except OSError as error:
        raise PortablePacketError(f"{label} must be a readable regular non-symlink file") from error
    if _is_link_or_reparse_point(before) or not stat.S_ISREG(before.st_mode):
        raise PortablePacketError(f"{label} must be a readable regular non-symlink file")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise PortablePacketError(f"{label} must be a readable regular non-symlink file") from error
    try:
        metadata = os.fstat(descriptor)
        if _is_link_or_reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode) or not _same_file_identity(before, metadata):
            raise PortablePacketError(f"{label} changed or became unsafe while opening")
        if metadata.st_size > maximum_bytes:
            raise PortablePacketError(f"{label} exceeds {maximum_bytes} byte limit")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(maximum_bytes + 1)
        if len(raw) > maximum_bytes:
            raise PortablePacketError(f"{label} exceeds {maximum_bytes} byte limit")
        return raw
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _read_bounded_regular_source(packet_path: Path | str) -> bytes:
    return _read_bounded_regular_file(packet_path, label="--packet")


def _secure_storage_supported() -> bool:
    """Whether this Python exposes the POSIX primitives needed for safe storage."""

    required_dir_fd = (os.open, os.stat, os.mkdir, os.rename, os.link, os.unlink, os.rmdir)
    return bool(
        fcntl is not None
        and getattr(os, "O_DIRECTORY", None) is not None
        and getattr(os, "O_NOFOLLOW", None) is not None
        and all(operation in os.supports_dir_fd for operation in required_dir_fd)
        and os.stat in os.supports_follow_symlinks
        and os.listdir in getattr(os, "supports_fd", set())
        and hasattr(os, "fchmod")
    )


def _guarded_windows_fallback_enabled() -> bool:
    """Keep the weaker guarded storage route limited to Windows hosts.

    This small seam also lets the regression suite exercise its behavior on a
    macOS/Linux test runner without mutating ``os.name`` (which changes
    pathlib's concrete-path implementation at runtime).
    """

    return os.name == "nt"


def _secure_child_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name:
        raise PortablePacketError("Internal packet storage name is unsafe")


def _secure_stat_at(directory_fd: int, name: str, *, label: str) -> os.stat_result | None:
    """Inspect a direct child without following a link/reparse point."""

    _secure_child_name(name)
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PortablePacketError(f"{label} cannot be inspected") from error
    if _is_link_or_reparse_point(metadata):
        raise PortablePacketError(f"{label} must not be a symlink or reparse point")
    return metadata


def _secure_open_directory_path(path: Path, *, label: str) -> int:
    """Open a verified directory and bind subsequent work to its descriptor."""

    try:
        before = os.lstat(path)
    except OSError as error:
        raise PortablePacketError(f"{label} cannot be opened securely") from error
    if _is_link_or_reparse_point(before) or not stat.S_ISDIR(before.st_mode):
        raise PortablePacketError(f"{label} must be a real directory, not a link")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        after = os.fstat(descriptor)
        if _is_link_or_reparse_point(after) or not stat.S_ISDIR(after.st_mode) or not _same_file_identity(before, after):
            raise PortablePacketError(f"{label} changed or became unsafe while opening")
        return descriptor
    except OSError as error:
        raise PortablePacketError(f"{label} cannot be opened securely") from error
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _secure_open_child_directory(
    parent_fd: int,
    name: str,
    *,
    label: str,
    create: bool,
) -> int:
    """Open or create a directory directly below an already-open parent FD."""

    before = _secure_stat_at(parent_fd, name, label=label)
    created = False
    if before is None:
        if not create:
            raise PortablePacketError(f"{label} is missing")
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
            created = True
        except FileExistsError:
            pass
        except OSError as error:
            raise PortablePacketError(f"{label} cannot be created securely") from error
        before = _secure_stat_at(parent_fd, name, label=label)
    if before is None or not stat.S_ISDIR(before.st_mode):
        raise PortablePacketError(f"{label} must be a real directory")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        after = os.fstat(descriptor)
        if _is_link_or_reparse_point(after) or not stat.S_ISDIR(after.st_mode) or not _same_file_identity(before, after):
            raise PortablePacketError(f"{label} changed or became unsafe while opening")
        if created:
            try:
                os.fchmod(descriptor, 0o700)
            except OSError:
                pass
        return descriptor
    except OSError as error:
        raise PortablePacketError(f"{label} cannot be opened securely") from error
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise


def _secure_existing_regular_at(parent_fd: int, name: str, *, label: str) -> os.stat_result | None:
    metadata = _secure_stat_at(parent_fd, name, label=label)
    if metadata is not None and not stat.S_ISREG(metadata.st_mode):
        raise PortablePacketError(f"{label} must be a regular file")
    return metadata


def _lock_work_lifecycle_fd(descriptor: int) -> None:
    """Take the shared lifecycle lease on the existing ``work.json`` inode.

    Lifecycle scripts open that same file read/write with only read sharing.
    POSIX uses a one-byte advisory record lock; the guarded Windows route also
    opens a same-file Win32 handle that denies competing writers.  The lease is
    intentionally on ``work.json`` itself rather than a sidecar so a close
    transition and packet import cannot both believe they own the same Work.
    """

    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            if msvcrt is None:  # pragma: no cover - defensive host guard.
                raise PortablePacketError("Windows lifecycle locking support is unavailable")
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            if fcntl is None:  # pragma: no cover - descriptor path preflight blocks this.
                raise PortablePacketError("POSIX lifecycle locking support is unavailable")
            fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB, 1, 0, os.SEEK_SET)
    except PortablePacketError:
        raise
    except OSError as error:
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EBUSY, errno.EPERM}:
            raise PortablePacketError(
                "Work lifecycle is busy; retry packet-import after its current transition"
            ) from error
        raise PortablePacketError("Work lifecycle lock cannot be acquired") from error


def _unlock_work_lifecycle_fd(descriptor: int) -> None:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == "nt":
            if msvcrt is not None:
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        elif fcntl is not None:
            fcntl.lockf(descriptor, fcntl.LOCK_UN, 1, 0, os.SEEK_SET)
    except OSError:
        # Closing the descriptor also releases this best-effort lease.
        pass


def _secure_open_work_lifecycle_record_at(parent_fd: int) -> int:
    """Open and lock the bound POSIX Work record without following a leaf link."""

    before = _secure_existing_regular_at(parent_fd, "work.json", label="Work work.json")
    if before is None:
        raise PortablePacketError("Work work.json is missing")
    flags = os.O_RDWR | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open("work.json", flags, dir_fd=parent_fd)
        after = os.fstat(descriptor)
        if _is_link_or_reparse_point(after) or not stat.S_ISREG(after.st_mode) or not _same_file_identity(before, after):
            raise PortablePacketError("Work work.json changed or became unsafe while opening lifecycle lease")
        _lock_work_lifecycle_fd(descriptor)
        return descriptor
    except OSError as error:
        if descriptor is not None:
            _unlock_work_lifecycle_fd(descriptor)
            os.close(descriptor)
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EBUSY, errno.EPERM}:
            raise PortablePacketError(
                "Work lifecycle is busy; retry packet-import after its current transition"
            ) from error
        raise PortablePacketError("Work lifecycle record cannot be opened securely") from error
    except Exception:
        if descriptor is not None:
            _unlock_work_lifecycle_fd(descriptor)
            os.close(descriptor)
        raise


def _read_bounded_locked_lifecycle_record(descriptor: int, *, label: str) -> bytes:
    """Read a lifecycle record through its held lease descriptor only.

    POSIX ``fcntl`` record locks are process-scoped: opening and then closing
    another descriptor for the same inode can release every lock held by that
    process.  Do not delegate this to the ordinary safe-file helper, which
    correctly opens/closes a fresh descriptor for unrelated files.  The
    lifecycle read must stay on the one FD that owns the cross-process lease.
    """

    try:
        metadata = os.fstat(descriptor)
        if _is_link_or_reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode):
            raise PortablePacketError(f"{label} became unsafe while lifecycle lease was held")
        if metadata.st_size > MAX_PACKET_BYTES:
            raise PortablePacketError(f"{label} exceeds {MAX_PACKET_BYTES} byte limit")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = MAX_PACKET_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_PACKET_BYTES:
            raise PortablePacketError(f"{label} exceeds {MAX_PACKET_BYTES} byte limit")
        return raw
    except PortablePacketError:
        raise
    except OSError as error:
        raise PortablePacketError(f"{label} cannot be read through lifecycle lease") from error


def _open_windows_shared_lifecycle_record(path: Path) -> int:
    """Open a Windows Work record read/write while sharing only reads.

    ``os.open`` does not let this code state its Windows sharing contract
    explicitly. ``CreateFileW`` does: a held importer handle lets readers
    inspect ``work.json`` but prevents a lifecycle script from opening it for
    write until the receipt transaction has ended.
    """

    if os.name != "nt":
        return os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
    if msvcrt is None:  # pragma: no cover - defensive native-host guard.
        raise PortablePacketError("Windows lifecycle locking support is unavailable")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    generic_read_write = 0xC0000000
    file_share_read = 0x00000001
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_flag_open_reparse_point = 0x00200000
    invalid_handle = ctypes.c_void_p(-1).value
    handle = create_file(
        str(path),
        generic_read_write,
        file_share_read,
        None,
        open_existing,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == invalid_handle:
        error = ctypes.get_last_error()
        if error in {5, 32, 33}:  # access denied, sharing violation, lock violation
            raise PortablePacketError(
                "Work lifecycle is busy; retry packet-import after its current transition"
            ) from OSError(error, "CreateFileW work lifecycle lease failed")
        raise PortablePacketError("Work lifecycle record cannot be opened") from OSError(
            error, "CreateFileW work lifecycle lease failed"
        )
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDWR | getattr(os, "O_BINARY", 0))
    except OSError:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        raise


def _guarded_open_work_lifecycle_record(path: Path) -> tuple[int, os.stat_result]:
    """Open a guarded Work's exact record and retain its lifecycle lease."""

    before = _guarded_regular(path, label="Work work.json")
    if before is None:
        raise PortablePacketError("Work work.json is missing")
    descriptor: int | None = None
    try:
        descriptor = _open_windows_shared_lifecycle_record(path)
        after = os.fstat(descriptor)
        if _is_link_or_reparse_point(after) or not stat.S_ISREG(after.st_mode) or not _same_file_identity(before, after):
            raise PortablePacketError("Work work.json changed or became unsafe while opening lifecycle lease")
        _lock_work_lifecycle_fd(descriptor)
        return descriptor, after
    except OSError as error:
        if descriptor is not None:
            _unlock_work_lifecycle_fd(descriptor)
            os.close(descriptor)
        if error.errno in {errno.EACCES, errno.EAGAIN, errno.EBUSY, errno.EPERM}:
            raise PortablePacketError(
                "Work lifecycle is busy; retry packet-import after its current transition"
            ) from error
        raise PortablePacketError("Work lifecycle record cannot be opened") from error
    except Exception:
        if descriptor is not None:
            _unlock_work_lifecycle_fd(descriptor)
            os.close(descriptor)
        raise


def _secure_read_regular_at(
    parent_fd: int,
    name: str,
    *,
    label: str,
    maximum_bytes: int = MAX_PACKET_BYTES,
) -> bytes:
    """Read a regular direct child from an already-bound directory FD."""

    before = _secure_existing_regular_at(parent_fd, name, label=label)
    if before is None:
        raise PortablePacketError(f"{label} is missing")
    if before.st_size > maximum_bytes:
        raise PortablePacketError(f"{label} exceeds {maximum_bytes} byte limit")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        after = os.fstat(descriptor)
        if _is_link_or_reparse_point(after) or not stat.S_ISREG(after.st_mode) or not _same_file_identity(before, after):
            raise PortablePacketError(f"{label} changed or became unsafe while opening")
        if after.st_size > maximum_bytes:
            raise PortablePacketError(f"{label} exceeds {maximum_bytes} byte limit")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(maximum_bytes + 1)
        if len(data) > maximum_bytes:
            raise PortablePacketError(f"{label} exceeds {maximum_bytes} byte limit")
        return data
    except OSError as error:
        raise PortablePacketError(f"{label} cannot be read securely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _secure_write_new_at(parent_fd: int, name: str, data: bytes, *, label: str, mode: int = 0o600) -> None:
    """Atomically create one new file inside a bound directory descriptor."""

    _secure_child_name(name)
    if _secure_existing_regular_at(parent_fd, name, label=label) is not None:
        raise PortablePacketError(f"{label} already exists")
    temporary_name: str | None = None
    descriptor: int | None = None
    try:
        for _ in range(16):
            candidate = f".{name}.{os.getpid()}.{secrets.token_hex(12)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                    mode,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                continue
            except OSError as error:
                raise PortablePacketError(f"{label} cannot be written securely") from error
            temporary_name = candidate
            break
        if descriptor is None or temporary_name is None:
            raise PortablePacketError(f"{label} could not allocate a secure temporary file")
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise PortablePacketError(f"{label} could not be written completely")
            offset += written
        os.fsync(descriptor)
        try:
            os.fchmod(descriptor, mode)
        except OSError:
            pass
        temporary_metadata = os.fstat(descriptor)
        os.close(descriptor)
        descriptor = None
        current_temporary = _secure_existing_regular_at(parent_fd, temporary_name, label=f"{label} temporary file")
        if current_temporary is None or not _same_file_identity(temporary_metadata, current_temporary):
            raise PortablePacketError(f"{label} temporary file changed before publication")
        # Hard-linking a closed, verified temporary inode supplies portable
        # no-overwrite semantics: another writer that creates ``name`` first
        # wins the race and this import fails rather than replacing its bytes.
        # Both entries live under the held parent descriptor, so no ancestor
        # pathname is resolved during publication.
        try:
            os.link(
                temporary_name,
                name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise PortablePacketError(f"{label} already exists") from error
        finalized = _secure_existing_regular_at(parent_fd, name, label=label)
        if finalized is None or not _same_file_identity(temporary_metadata, finalized):
            raise PortablePacketError(f"{label} changed during publication")
        os.unlink(temporary_name, dir_fd=parent_fd)
        temporary_name = None
        try:
            os.fsync(parent_fd)
        except OSError:
            pass
    except OSError as error:
        raise PortablePacketError(f"{label} cannot be finalized securely") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass


def _rename_noreplace_at(parent_fd: int, source: str, destination: str) -> None:
    """Rename a directory entry only when its destination is absent.

    POSIX ``rename`` may replace an empty destination directory, which is not
    acceptable for a content-addressed receipt.  macOS and Linux expose a
    native no-replace variant; hosts without either primitive fail closed
    rather than silently weakening publication semantics.
    """

    _secure_child_name(source)
    _secure_child_name(destination)
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        operation = getattr(library, "renameatx_np", None)
        flags = 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        operation = getattr(library, "renameat2", None)
        flags = 0x00000001  # RENAME_NOREPLACE
    else:
        operation = None
        flags = 0
    if operation is None:
        raise PortablePacketError(
            "packet-import requires a host no-replace directory rename primitive for descriptor-bound publication"
        )
    operation.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    operation.restype = ctypes.c_int
    result = operation(parent_fd, source.encode("utf-8"), parent_fd, destination.encode("utf-8"), flags)
    if result == 0:
        return
    code = ctypes.get_errno()
    if code in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(code, os.strerror(code), destination)
    raise OSError(code, os.strerror(code), destination)


class _SecurePacketStore:
    """Descriptor-bound Work storage for immutable external packet receipt files.

    The repository descriptor is opened before the ``work`` parent and Work
    identifier.  This matters as much as the final Work directory's
    ``O_NOFOLLOW`` flag: a renamed ``work`` parent cannot redirect writes once
    the ancestor descriptor chain is held.
    """

    def __init__(
        self,
        repo_root: Path,
        work_id: str,
        *,
        expected_tree: _ImportWorkIdentitySnapshot,
    ):
        self.repo_root = repo_root
        self.work_id = work_id
        self.work = repo_root / "work" / work_id
        self.expected_tree = expected_tree
        self._descriptors: list[int] = []
        self._bound_chain: list[tuple[int, str, int, str]] = []
        self.root_fd: int | None = None
        self.work_fd: int | None = None
        self._lock_fd: int | None = None
        self._lifecycle_lock_fd: int | None = None
        self.work_record: dict[str, Any] | None = None

    def __enter__(self) -> "_SecurePacketStore":
        if not _secure_storage_supported():
            raise PortablePacketError(
                "packet-import requires host secure directory-descriptor support; this runtime cannot safely retain external packets"
            )
        try:
            repo_fd = _secure_open_directory_path(self.repo_root, label="Workspace root")
            self._descriptors.append(repo_fd)
            self._assert_preflight_identity(
                os.fstat(repo_fd), self.expected_tree.repo_root, label="Workspace root"
            )
            work_parent_fd = _secure_open_child_directory(repo_fd, "work", label="Work root", create=False)
            self._descriptors.append(work_parent_fd)
            self._bound_chain.append((repo_fd, "work", work_parent_fd, "Work root"))
            self._assert_preflight_identity(
                os.fstat(work_parent_fd), self.expected_tree.work_root, label="Work root"
            )
            work_fd = _secure_open_child_directory(work_parent_fd, self.work_id, label="Work package", create=False)
            self._descriptors.append(work_fd)
            self._bound_chain.append((work_parent_fd, self.work_id, work_fd, "Work package"))
            self.work_fd = work_fd
            self._assert_preflight_identity(
                os.fstat(work_fd), self.expected_tree.work, label="Work package"
            )
            # Lock the actual lifecycle record before creating bridge storage.
            # ``prepare-work-close``, ``finish-work`` and ``reopen-work``
            # acquire this exact same record lease, so either operation owns a
            # whole Work transition or the immutable receipt transaction does.
            self._lifecycle_lock_fd = _secure_open_work_lifecycle_record_at(work_fd)
            self.assert_work_current_and_importable()
            workspace_fd = _secure_open_child_directory(work_fd, "workspace", label="Work workspace", create=False)
            self._descriptors.append(workspace_fd)
            self._bound_chain.append((work_fd, "workspace", workspace_fd, "Work workspace"))
            self._assert_preflight_identity(
                os.fstat(workspace_fd), self.expected_tree.workspace, label="Work workspace"
            )
            # Re-read lifecycle immediately before every retained-storage
            # creation. A later check cannot undo a directory made after a
            # concurrent close transition.
            self.assert_work_current_and_importable()
            bridge_fd = _secure_open_child_directory(workspace_fd, "bridge-packets", label="External handoff storage", create=True)
            self._descriptors.append(bridge_fd)
            self._bound_chain.append((workspace_fd, "bridge-packets", bridge_fd, "External handoff storage"))
            self.assert_work_current_and_importable()
            root_fd = _secure_open_child_directory(bridge_fd, "v1", label="External handoff storage", create=True)
            self._descriptors.append(root_fd)
            self._bound_chain.append((bridge_fd, "v1", root_fd, "External handoff storage"))
            self.root_fd = root_fd
            self.assert_work_current_and_importable()
            self._lock_fd = self._open_lock(root_fd)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX)  # type: ignore[union-attr]
            self.assert_work_current_and_importable()
        except PortablePacketError:
            self._close()
            raise
        except OSError as error:
            self._close()
            raise PortablePacketError("packet-import cannot acquire its secure local lock") from error
        except Exception:
            self._close()
            raise
        return self

    @staticmethod
    def _assert_preflight_identity(
        current: os.stat_result, expected: os.stat_result, *, label: str
    ) -> None:
        if not _same_file_identity(expected, current):
            raise PortablePacketError(
                f"{label} changed after canonical Work intake preflight; refusing to retain the external packet"
            )

    def _open_lock(self, parent_fd: int) -> int:
        name = "packet-import.lock"
        before = _secure_existing_regular_at(parent_fd, name, label="Packet-import lock")
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        descriptor: int | None = None
        try:
            descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
            after = os.fstat(descriptor)
            if _is_link_or_reparse_point(after) or not stat.S_ISREG(after.st_mode):
                raise PortablePacketError("Packet-import lock became unsafe while opening")
            if before is not None and not _same_file_identity(before, after):
                raise PortablePacketError("Packet-import lock changed while opening")
            return descriptor
        except OSError as error:
            raise PortablePacketError("Packet-import lock cannot be opened securely") from error
        except Exception:
            if descriptor is not None:
                os.close(descriptor)
            raise

    def _close(self) -> None:
        if self._lifecycle_lock_fd is not None:
            _unlock_work_lifecycle_fd(self._lifecycle_lock_fd)
            try:
                os.close(self._lifecycle_lock_fd)
            except OSError:
                pass
            self._lifecycle_lock_fd = None
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)  # type: ignore[union-attr]
            except OSError:
                pass
            try:
                os.close(self._lock_fd)
            except OSError:
                pass
            self._lock_fd = None
        for descriptor in reversed(self._descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass
        self._descriptors.clear()
        self._bound_chain.clear()
        self.root_fd = None
        self.work_fd = None
        self.work_record = None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._close()

    def assert_binding_current(self) -> None:
        """Reject a detached/replaced ancestor chain before committing output.

        Holding descriptors stops a replaced ``work`` parent from redirecting
        a write.  Rechecking the names through the repository descriptor also
        ensures a Work that was moved out of the repository after opening is
        not silently treated as the current canonical destination.
        """

        for parent_fd, name, child_fd, label in self._bound_chain:
            current = _secure_stat_at(parent_fd, name, label=label)
            expected = os.fstat(child_fd)
            if (
                current is None
                or not stat.S_ISDIR(current.st_mode)
                or not _same_file_identity(expected, current)
            ):
                raise PortablePacketError(f"{label} changed or detached while packet-import was running")

    def assert_work_current_and_importable(self) -> None:
        """Bind the current Work lifecycle state to this receipt transaction.

        A Work can be closed by a cooperative lifecycle command without its
        directory inode changing. Re-reading its record through the held Work
        descriptor after the packet lock and immediately before publication
        prevents a staged external bundle from crossing into review/completed
        state merely because the status changed during the import.
        """

        if self.work_fd is None or self._lifecycle_lock_fd is None:
            raise PortablePacketError("Secure Work storage is not open")
        self.assert_binding_current()
        current_record = _secure_existing_regular_at(self.work_fd, "work.json", label="Work work.json")
        expected_record = os.fstat(self._lifecycle_lock_fd)
        if (
            current_record is None
            or _is_link_or_reparse_point(expected_record)
            or not stat.S_ISREG(expected_record.st_mode)
            or not _same_file_identity(expected_record, current_record)
        ):
            raise PortablePacketError("Work work.json changed while packet-import held its lifecycle lease")
        self.work_record = _validate_import_work_record(
            _read_bounded_locked_lifecycle_record(self._lifecycle_lock_fd, label="Work work.json"),
            work_id=self.work_id,
        )
        current_record = _secure_existing_regular_at(self.work_fd, "work.json", label="Work work.json")
        if current_record is None or not _same_file_identity(expected_record, current_record):
            raise PortablePacketError("Work work.json changed while packet-import read its lifecycle state")

    def open_packet_directory(self, digest: str) -> int | None:
        if self.root_fd is None:
            raise PortablePacketError("Secure packet storage is not open")
        self.assert_binding_current()
        if _secure_stat_at(self.root_fd, digest, label="External handoff packet directory") is None:
            return None
        return _secure_open_child_directory(
            self.root_fd, digest, label="External handoff packet directory", create=False
        )

    def stage_packet_directory(self, digest: str) -> tuple[int, str]:
        if self.root_fd is None:
            raise PortablePacketError("Secure packet storage is not open")
        self.assert_work_current_and_importable()
        for _ in range(16):
            stage_name = f".stage-{digest}-{secrets.token_hex(12)}"
            if _secure_stat_at(self.root_fd, stage_name, label="Packet staging directory") is not None:
                continue
            self.assert_work_current_and_importable()
            descriptor = _secure_open_child_directory(
                self.root_fd, stage_name, label="Packet staging directory", create=True
            )
            return descriptor, stage_name
        raise PortablePacketError("Could not allocate a secure packet staging directory")

    def verify_staged_packet_directory(
        self,
        directory_fd: int,
        *,
        raw: bytes,
        work: Path,
        bundle: dict[str, Any],
        packet: dict[str, Any],
        digest: str,
        origin: str,
    ) -> None:
        """Re-read staged bytes before publication.

        A successful write is not by itself a proof that the staged content
        remained unchanged until the commit boundary.  This check also makes a
        malformed local receipt fail before its directory can receive the
        content-addressed final name.
        """

        self.assert_work_current_and_importable()
        if _secure_read_regular_at(directory_fd, "bundle.json", label="Frozen handoff bundle") != raw:
            raise PortablePacketError("Staged frozen handoff bundle changed before publication")
        receipt = _read_receipt_at(directory_fd, "receipt.json")
        _validate_existing_receipt(
            receipt,
            work=work,
            bundle=bundle,
            packet=packet,
            digest=digest,
            byte_count=len(raw),
            origin=origin,
        )

    def publish_staged_packet_directory(self, stage_name: str, digest: str, stage_fd: int) -> None:
        if self.root_fd is None:
            raise PortablePacketError("Secure packet storage is not open")
        self.assert_work_current_and_importable()
        expected_stage = os.fstat(stage_fd)
        current_stage = _secure_stat_at(self.root_fd, stage_name, label="Packet staging directory")
        if (
            current_stage is None
            or not stat.S_ISDIR(current_stage.st_mode)
            or not _same_file_identity(expected_stage, current_stage)
        ):
            raise PortablePacketError("Packet staging directory changed before publication")
        if _secure_stat_at(self.root_fd, digest, label="External handoff packet directory") is not None:
            raise PortablePacketError("Content-addressed frozen handoff packet appeared during import")
        try:
            self.assert_work_current_and_importable()
            _rename_noreplace_at(self.root_fd, stage_name, digest)
            try:
                published = _secure_stat_at(self.root_fd, digest, label="External handoff packet directory")
            except PortablePacketError:
                _quarantine_unexpected_packet_entry_at(
                    self.root_fd,
                    digest,
                    assert_work_current=self.assert_work_current_and_importable,
                )
                raise
            if (
                published is None
                or not stat.S_ISDIR(published.st_mode)
                or not _same_file_identity(expected_stage, published)
            ):
                _quarantine_unexpected_packet_entry_at(
                    self.root_fd,
                    digest,
                    assert_work_current=self.assert_work_current_and_importable,
                )
                raise PortablePacketError("Published packet directory does not match the staged packet")
            os.fsync(self.root_fd)
        except OSError as error:
            raise PortablePacketError("Completed packet staging could not be published securely") from error

    def verify_published_packet_directory(
        self,
        digest: str,
        stage_fd: int,
        *,
        raw: bytes,
        work: Path,
        bundle: dict[str, Any],
        packet: dict[str, Any],
        origin: str,
    ) -> None:
        """Re-open the final name and bind it to the committed stage inode.

        The success path deliberately does not trust a prior staging pathname.
        It follows the bound root descriptor to the digest name again, checks
        inode identity, and validates both retained files before reporting a
        completed local receipt.
        """

        if self.root_fd is None:
            raise PortablePacketError("Secure packet storage is not open")
        self.assert_work_current_and_importable()
        expected = os.fstat(stage_fd)
        final_fd = _secure_open_child_directory(
            self.root_fd,
            digest,
            label="Published frozen handoff packet",
            create=False,
        )
        try:
            actual = os.fstat(final_fd)
            if not _same_file_identity(expected, actual):
                raise PortablePacketError("Published packet directory changed before verification")
            stored_raw, _stored_bundle, _stored_packet, _stored_receipt = _validate_stored_packet_at(
                final_fd,
                directory_digest=digest,
                work=work,
                origin=origin,
            )
            if stored_raw != raw:
                raise PortablePacketError("Published frozen handoff bundle differs from staged bytes")
            # ``_validate_stored_packet_at`` validates binding against the
            # retained bundle. Reapply the caller's already-parsed identities
            # so a forged but self-consistent local record cannot satisfy this
            # transaction's receipt.
            receipt = _read_receipt_at(final_fd, "receipt.json")
            _validate_existing_receipt(
                receipt,
                work=work,
                bundle=bundle,
                packet=packet,
                digest=digest,
                byte_count=len(raw),
                origin=origin,
            )
            # Publication is irreversible evidence retention. If a
            # cooperative close moved the Work out of intake/in_progress while
            # the digest was being checked, preserve the receipt but do not
            # report success for a sealed Work.
            self.assert_work_current_and_importable()
        finally:
            os.close(final_fd)

    def discard_staged_packet_directory(self, stage_name: str, directory_fd: int) -> None:
        """Best-effort cleanup; an interrupted stage remains harmless and auditable."""

        if self.root_fd is None or not stage_name:
            return
        try:
            # Do not tidy an interrupted stage inside a Work that became
            # sealed; it remains a harmless, auditable incomplete entry.
            self.assert_work_current_and_importable()
            expected = os.fstat(directory_fd)
            current = _secure_stat_at(self.root_fd, stage_name, label="Packet staging directory")
        except (OSError, PortablePacketError):
            return
        # A successful rename removes the staging name while keeping this FD
        # open.  Never use that FD to delete a now-published digest directory.
        if (
            current is None
            or not stat.S_ISDIR(current.st_mode)
            or not _same_file_identity(expected, current)
        ):
            return
        for name in ("receipt.json", "bundle.json"):
            try:
                self.assert_work_current_and_importable()
                os.unlink(name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except (OSError, PortablePacketError):
                return
        try:
            self.assert_work_current_and_importable()
            os.rmdir(stage_name, dir_fd=self.root_fd)
        except (OSError, PortablePacketError):
            pass


def _read_receipt_bytes(raw: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = _parse_json_bytes(raw)
    except PortablePacketError as error:
        raise PortablePacketError(f"Existing bridge receipt is unreadable: {name}") from error
    return _validate_receipt_shape(value)


def _read_receipt_at(parent_fd: int, name: str) -> dict[str, Any]:
    return _read_receipt_bytes(
        _secure_read_regular_at(parent_fd, name, label="Existing bridge receipt"),
        name=name,
    )


def _quarantine_incomplete_packet_at(
    root_fd: int,
    digest: str,
    *,
    assert_work_current: Callable[[], None],
) -> None:
    """Keep an interrupted packet directory while allowing future retry.

    A transaction either publishes a complete digest directory or leaves an
    ignored staging directory. This repair covers incomplete directories from
    an interrupted older build without deleting evidence or permanently
    poisoning later imports.
    """

    # Quarantine renames user-visible Work evidence. Re-read the lifecycle
    # record at the mutation boundary rather than assuming the status captured
    # when this import began remains true after idempotency scanning.
    assert_work_current()
    for _ in range(16):
        quarantine = f".quarantine-{digest}-{secrets.token_hex(12)}"
        if _secure_stat_at(root_fd, quarantine, label="Packet quarantine directory") is not None:
            continue
        try:
            # POSIX ``rename`` can replace an empty destination directory.
            # Reuse the native exclusive-rename primitive used for packet
            # publication so a concurrent audit entry is never overwritten.
            assert_work_current()
            _rename_noreplace_at(root_fd, digest, quarantine)
            os.fsync(root_fd)
            return
        except FileExistsError:
            continue
        except OSError as error:
            raise PortablePacketError("Incomplete frozen handoff packet could not be quarantined safely") from error
    raise PortablePacketError("Could not allocate a quarantine location for an incomplete handoff packet")


def _quarantine_unexpected_packet_entry_at(
    root_fd: int,
    name: str,
    *,
    assert_work_current: Callable[[], None],
) -> None:
    """Remove a mismatched final name without deleting the suspect object.

    This is used only after a staged-name race is detected.  A real directory
    is moved into the same audit quarantine namespace as interrupted packets;
    a link or non-directory entry is unlinked by its direct root-FD name so it
    cannot remain at a content-addressed digest path.
    """

    assert_work_current()
    _secure_child_name(name)
    try:
        metadata = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as error:
        raise PortablePacketError("Unexpected published packet entry cannot be inspected") from error
    if _is_link_or_reparse_point(metadata) or not stat.S_ISDIR(metadata.st_mode):
        try:
            assert_work_current()
            os.unlink(name, dir_fd=root_fd)
        except OSError as error:
            raise PortablePacketError("Unexpected published packet entry cannot be removed safely") from error
        return
    _quarantine_incomplete_packet_at(root_fd, name, assert_work_current=assert_work_current)


def _validate_stored_packet_at(
    directory_fd: int,
    *,
    directory_digest: str,
    work: Path,
    origin: str,
) -> tuple[bytes, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Bind a complete retained directory before it influences idempotency.

    A receipt alone is not enough: a corrupt or locally replaced historical
    directory must never create a false packet-ID conflict or masquerade as an
    already-received bundle.
    """

    raw = _secure_read_regular_at(directory_fd, "bundle.json", label="Existing frozen handoff bundle")
    if _sha256(raw) != directory_digest:
        raise PortablePacketError("Existing frozen handoff bundle does not match its content-addressed directory")
    bundle = validate_handoff_bundle_v1(raw)
    if bundle["source_surface"] != origin:
        raise PortablePacketError("Existing frozen handoff bundle has an unsupported source surface")
    packet = bundle["packet"]
    receipt = _read_receipt_at(directory_fd, "receipt.json")
    _validate_existing_receipt(
        receipt,
        work=work,
        bundle=bundle,
        packet=packet,
        digest=directory_digest,
        byte_count=len(raw),
        origin=origin,
    )
    return raw, bundle, packet, receipt


def _reject_idempotency_conflict_at(
    root_fd: int,
    packet: dict[str, Any],
    digest: str,
    *,
    work: Path,
    origin: str,
    assert_work_current: Callable[[], None],
) -> None:
    """Perform idempotency checks entirely through a bound storage directory FD."""

    try:
        entries = sorted(os.listdir(root_fd))
    except OSError as error:
        raise PortablePacketError("External handoff storage cannot be listed securely") from error
    for name in entries:
        if _DIGEST_DIRECTORY.fullmatch(name) is None:
            continue
        metadata = _secure_stat_at(root_fd, name, label="External handoff packet directory")
        if metadata is None or not stat.S_ISDIR(metadata.st_mode):
            raise PortablePacketError("External handoff storage contains an unsafe packet entry")
        directory_fd = _secure_open_child_directory(
            root_fd,
            name,
            label="External handoff packet directory",
            create=False,
        )
        try:
            has_bundle = _secure_existing_regular_at(
                directory_fd, "bundle.json", label="Existing frozen handoff bundle"
            ) is not None
            has_receipt = _secure_existing_regular_at(
                directory_fd, "receipt.json", label="Existing bridge receipt"
            ) is not None
            if has_bundle != has_receipt:
                _quarantine_incomplete_packet_at(
                    root_fd, name, assert_work_current=assert_work_current
                )
                continue
            if not has_bundle:
                _quarantine_incomplete_packet_at(
                    root_fd, name, assert_work_current=assert_work_current
                )
                continue
            _raw, _bundle, stored_packet, receipt = _validate_stored_packet_at(
                directory_fd,
                directory_digest=name,
                work=work,
                origin=origin,
            )
            same_packet_id = stored_packet["packet_id"] == packet["packet_id"]
            same_key = stored_packet["idempotency_key"] == packet["idempotency_key"]
            if (same_packet_id or same_key) and receipt["bundle_sha256"] != digest:
                conflict = "packet_id" if same_packet_id else "idempotency_key"
                raise PortablePacketError(f"External packet {conflict} was already received with different bundle bytes")
        finally:
            os.close(directory_fd)


def _import_packet_descriptor_bound(
    root: Path,
    work: Path,
    expected_tree: _ImportWorkIdentitySnapshot,
    raw: bytes,
    bundle: dict[str, Any],
    *,
    origin: str,
) -> dict[str, Any]:
    """Retain validated bytes through the descriptor-bound POSIX storage path."""

    packet = bundle["packet"]
    digest = _sha256(raw)
    with _SecurePacketStore(root, work.name, expected_tree=expected_tree) as store:
        if store.root_fd is None:  # Defensive: __enter__ always binds this FD.
            raise PortablePacketError("Secure packet storage did not initialize")
        store.assert_binding_current()
        _reject_idempotency_conflict_at(
            store.root_fd,
            packet,
            digest,
            work=work,
            origin=origin,
            assert_work_current=store.assert_work_current_and_importable,
        )
        packet_fd = store.open_packet_directory(digest)
        if packet_fd is not None:
            try:
                has_frozen_bundle = _secure_existing_regular_at(
                    packet_fd, "bundle.json", label="Existing frozen handoff bundle"
                ) is not None
                has_frozen_receipt = _secure_existing_regular_at(
                    packet_fd, "receipt.json", label="Existing bridge receipt"
                ) is not None
                if has_frozen_bundle != has_frozen_receipt or not has_frozen_bundle:
                    # This may occur only if a non-cooperating process created
                    # a malformed entry after the initial root scan. Preserve it
                    # under quarantine and continue through a fresh transaction.
                    os.close(packet_fd)
                    packet_fd = None
                    _quarantine_incomplete_packet_at(
                        store.root_fd,
                        digest,
                        assert_work_current=store.assert_work_current_and_importable,
                    )
                else:
                    if _secure_read_regular_at(
                        packet_fd, "bundle.json", label="Existing frozen handoff bundle"
                    ) != raw:
                        raise PortablePacketError("Content-addressed frozen handoff bundle differs from incoming bytes")
                    receipt = _read_receipt_at(packet_fd, "receipt.json")
                    _validate_existing_receipt(
                        receipt,
                        work=work,
                        bundle=bundle,
                        packet=packet,
                        digest=digest,
                        byte_count=len(raw),
                        origin=origin,
                    )
                    store.assert_work_current_and_importable()
                    relative_root = f"workspace/bridge-packets/v1/{digest}"
                    return {
                        "status": "already_received",
                        "storage_mode": "descriptor_bound",
                        "work_id": work.name,
                        "bundle_id": bundle["bundle_id"],
                        "bundle_sha256": digest,
                        "bundle_bytes": len(raw),
                        "bundle_path": f"{relative_root}/bundle.json",
                        "receipt_path": f"{relative_root}/receipt.json",
                        "bundle_written": False,
                        "packet_id": packet["packet_id"],
                        "feedback_items": len(packet["feedback_events"]),
                        "triage_state": "needs_source_corroboration",
                        "truth_boundaries": {
                            key: receipt[key]
                            for key in (
                                "evidence_class",
                                "raw_conversation_provenance",
                                "automatic_learning",
                                "acceptance_claim",
                                "tool_execution_claim",
                                "capability_claim",
                                "visual_review_claim",
                                "authorization_claim",
                                "attachment_transfer",
                            )
                        },
                    }
            finally:
                if packet_fd is not None:
                    os.close(packet_fd)

        stage_fd, stage_name = store.stage_packet_directory(digest)
        try:
            store.assert_work_current_and_importable()
            _secure_write_new_at(stage_fd, "bundle.json", raw, label="Frozen handoff bundle")
            receipt = _receipt(
                work=work,
                bundle=bundle,
                packet=packet,
                digest=digest,
                byte_count=len(raw),
                origin=origin,
            )
            receipt_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            store.assert_work_current_and_importable()
            _secure_write_new_at(stage_fd, "receipt.json", receipt_bytes, label="Bridge receipt")
            store.verify_staged_packet_directory(
                stage_fd,
                raw=raw,
                work=work,
                bundle=bundle,
                packet=packet,
                digest=digest,
                origin=origin,
            )
            store.publish_staged_packet_directory(stage_name, digest, stage_fd)
            store.verify_published_packet_directory(
                digest,
                stage_fd,
                raw=raw,
                work=work,
                bundle=bundle,
                packet=packet,
                origin=origin,
            )
            stage_name = ""
        except Exception:
            store.discard_staged_packet_directory(stage_name, stage_fd)
            raise
        finally:
            os.close(stage_fd)
        store.assert_work_current_and_importable()
        relative_root = f"workspace/bridge-packets/v1/{digest}"
        return {
            "status": "received_external_unverified",
            "storage_mode": "descriptor_bound",
            "work_id": work.name,
            "bundle_id": bundle["bundle_id"],
            "bundle_sha256": digest,
            "bundle_bytes": len(raw),
            "bundle_path": f"{relative_root}/bundle.json",
            "receipt_path": f"{relative_root}/receipt.json",
            "bundle_written": True,
            "packet_id": packet["packet_id"],
            "feedback_items": len(packet["feedback_events"]),
            "triage_state": "needs_source_corroboration",
            "truth_boundaries": {
                key: receipt[key]
                for key in (
                    "evidence_class",
                    "raw_conversation_provenance",
                    "automatic_learning",
                    "acceptance_claim",
                    "tool_execution_claim",
                    "capability_claim",
                    "visual_review_claim",
                    "authorization_claim",
                    "attachment_transfer",
                )
            },
        }


def _guarded_directory(path: Path, *, label: str, create: bool = False) -> os.stat_result:
    """Inspect one real directory for the Windows guarded storage fallback."""

    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        if not create:
            raise PortablePacketError(f"{label} is missing")
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise PortablePacketError(f"{label} cannot be created") from error
        try:
            metadata = os.lstat(path)
        except OSError as error:
            raise PortablePacketError(f"{label} cannot be inspected after creation") from error
    except OSError as error:
        raise PortablePacketError(f"{label} cannot be inspected") from error
    if _is_link_or_reparse_point(metadata) or not stat.S_ISDIR(metadata.st_mode):
        raise PortablePacketError(f"{label} must be a real directory, not a link or reparse point")
    if not _has_stable_file_identity(metadata):
        raise PortablePacketError(
            f"{label} requires stable file identities; the operator must verify a suitable local filesystem"
        )
    return metadata


def _guarded_regular(path: Path, *, label: str) -> os.stat_result | None:
    """Inspect one direct regular-file leaf without following an indirection."""

    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise PortablePacketError(f"{label} cannot be inspected") from error
    if _is_link_or_reparse_point(metadata) or not stat.S_ISREG(metadata.st_mode):
        raise PortablePacketError(f"{label} must be a regular non-symlink file")
    if not _has_stable_file_identity(metadata):
        raise PortablePacketError(
            f"{label} requires stable file identities; the operator must verify a suitable local filesystem"
        )
    return metadata


class _GuardedWindowsPacketStore:
    """Windows guarded-exclusive-Work storage for external packet retention.

    Standard Python path APIs cannot offer the POSIX path's descriptor-bound
    guarantee against a hostile process with independent write access to the
    Work.  This fallback is therefore intentionally Windows-only: it rejects
    links/reparse points, records directory identities, serializes cooperative
    importers with a lock, stages atomically, and rechecks every boundary.  It
    is an operator-constrained fallback for a current-user-owned, exclusive
    Work whose local filesystem and access controls have been independently
    verified. Stable identities are necessary, but standard Python cannot prove
    the volume type, owner, or ACL here, so callers must establish those
    preconditions before using this Windows-only mode.
    """

    def __init__(
        self,
        repo_root: Path,
        work_id: str,
        *,
        expected_tree: _ImportWorkIdentitySnapshot,
    ):
        self.repo_root = repo_root
        self.work_id = work_id
        self.expected_tree = expected_tree
        self.work = repo_root / "work" / work_id
        self.workspace = self.work / "workspace"
        self.bridge = self.workspace / "bridge-packets"
        self.root = self.bridge / "v1"
        self._tree: tuple[tuple[Path, str, bool], ...] = (
            (self.repo_root, "Workspace root", False),
            (self.repo_root / "work", "Work root", False),
            (self.work, "Work package", False),
            (self.workspace, "Work workspace", False),
            (self.bridge, "External handoff storage", True),
            (self.root, "External handoff storage", True),
        )
        self._identities: dict[Path, os.stat_result] = {}
        self._lock: FileLock | None = None
        self._lock_path = self.root / "packet-import.lock"
        self._lock_identity: os.stat_result | None = None
        self._lifecycle_lock_fd: int | None = None
        self._lifecycle_lock_identity: os.stat_result | None = None
        self.work_record: dict[str, Any] | None = None

    def _capture_tree(self, entries: tuple[tuple[Path, str, bool], ...] | None = None) -> None:
        requested = entries if entries is not None else self._tree
        # A guarded path has no descriptor-bound parent authority.  Before it
        # ever asks Path.mkdir() to materialize a bridge directory, bind the
        # still-path-addressed Work tree back to the canonical intake snapshot.
        # Do the same immediately afterwards: a replacement must be detected
        # before the newly created path can be used as guarded storage.
        creates_storage = any(create for _path, _label, create in requested)
        if creates_storage:
            self._assert_tree_stable()
            self._assert_preflight_tree()
        for path, label, create in requested:
            metadata = _guarded_directory(path, label=label, create=create)
            self._identities[path] = metadata
        if creates_storage:
            self._assert_tree_stable()
            self._assert_preflight_tree()

    def _assert_tree_stable(self) -> None:
        for path, label, _create in self._tree:
            expected = self._identities.get(path)
            if expected is None:
                continue
            actual = _guarded_directory(path, label=label, create=False)
            if not _same_file_identity(expected, actual):
                raise PortablePacketError(f"{label} changed while packet-import held its guarded lock")
        if self._lock_identity is not None:
            current_lock = _guarded_regular(self._lock_path, label="Packet-import lock")
            if current_lock is None or not _same_file_identity(self._lock_identity, current_lock):
                raise PortablePacketError("Packet-import lock changed while guarded storage was active")

    def _assert_preflight_tree(self) -> None:
        """Reject a real-directory replacement before guarded storage is created."""

        expected = (
            (self.repo_root, self.expected_tree.repo_root, "Workspace root"),
            (self.repo_root / "work", self.expected_tree.work_root, "Work root"),
            (self.work, self.expected_tree.work, "Work package"),
            (self.workspace, self.expected_tree.workspace, "Work workspace"),
        )
        for path, snapshot, label in expected:
            captured = self._identities.get(path)
            # Re-open the actual pathname as well as comparing the captured
            # identity.  A replacement Work can retain a hard-linked
            # work.json, so lifecycle-record identity alone is insufficient to
            # prove that later Path.mkdir()/Path.rename() calls still target
            # the Work admitted at intake.
            current = _guarded_directory(path, label=label, create=False)
            if (
                captured is None
                or not _same_file_identity(snapshot, captured)
                or not _same_file_identity(snapshot, current)
                or not _same_file_identity(captured, current)
            ):
                raise PortablePacketError(
                    f"{label} changed after canonical Work intake preflight; refusing to retain the external packet"
                )

    @staticmethod
    def _assert_directory_identity(path: Path, expected: os.stat_result, *, label: str) -> None:
        actual = _guarded_directory(path, label=label, create=False)
        if not _same_file_identity(expected, actual):
            raise PortablePacketError(f"{label} changed while packet-import was operating")

    def __enter__(self) -> "_GuardedWindowsPacketStore":
        try:
            # Do not create bridge storage in a sealed Work merely to discover
            # it is not importable. Capture the existing lifecycle subtree
            # first, then materialize guarded storage only after validation.
            self._capture_tree(self._tree[:4])
            self._assert_preflight_tree()
            self._lifecycle_lock_fd, self._lifecycle_lock_identity = _guarded_open_work_lifecycle_record(
                self.work / "work.json"
            )
            self.assert_work_current_and_importable()
            # Check again at the creation boundary; this route is explicitly
            # guarded rather than descriptor-bound, so lifecycle checks must
            # surround every retained-storage mutation.
            self.assert_work_current_and_importable()
            self._capture_tree(self._tree[4:])
            self.assert_work_current_and_importable()
            before_lock = _guarded_regular(self._lock_path, label="Packet-import lock")
            self._lock = FileLock(str(self._lock_path), timeout=10)
            try:
                self._lock.acquire()
            except Timeout as error:
                raise PortablePacketError("packet-import cannot acquire its guarded local lock") from error
            after_lock = _guarded_regular(self._lock_path, label="Packet-import lock")
            if after_lock is None:
                raise PortablePacketError("Packet-import lock was not created")
            if before_lock is not None and not _same_file_identity(before_lock, after_lock):
                raise PortablePacketError("Packet-import lock changed while opening")
            self._lock_identity = after_lock
            self._assert_tree_stable()
            self.assert_work_current_and_importable()
            return self
        except Exception:
            self._close()
            raise

    def _close(self) -> None:
        if self._lock is not None:
            try:
                self._lock.release()
            except OSError:
                pass
            self._lock = None
        self._lock_identity = None
        if self._lifecycle_lock_fd is not None:
            _unlock_work_lifecycle_fd(self._lifecycle_lock_fd)
            try:
                os.close(self._lifecycle_lock_fd)
            except OSError:
                pass
            self._lifecycle_lock_fd = None
        self._lifecycle_lock_identity = None
        self.work_record = None

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._close()

    def assert_work_current_and_importable(self) -> None:
        """Re-read the current Work lifecycle state under the guarded lock."""

        # Treat the lifecycle record as evidence about the already-admitted
        # Work tree, never as a substitute for that tree's identity.  This
        # closes the create=True window in which a replacement Work could
        # hard-link the old work.json after a status check but before bridge
        # storage is materialized.
        self._assert_tree_stable()
        self._assert_preflight_tree()
        work_identity = self._identities.get(self.work)
        lifecycle_identity = self._lifecycle_lock_identity
        if work_identity is None or lifecycle_identity is None or self._lifecycle_lock_fd is None:
            raise PortablePacketError("Guarded Work storage is not initialized")
        current_record = _guarded_regular(self.work / "work.json", label="Work work.json")
        descriptor_record = os.fstat(self._lifecycle_lock_fd)
        if (
            current_record is None
            or _is_link_or_reparse_point(descriptor_record)
            or not stat.S_ISREG(descriptor_record.st_mode)
            or not _same_file_identity(lifecycle_identity, current_record)
            or not _same_file_identity(lifecycle_identity, descriptor_record)
        ):
            raise PortablePacketError("Work work.json changed while packet-import held its lifecycle lease")
        self.work_record = _validate_import_work_record(
            _read_bounded_locked_lifecycle_record(self._lifecycle_lock_fd, label="Work work.json"),
            work_id=self.work_id,
        )
        current_record = _guarded_regular(self.work / "work.json", label="Work work.json")
        if current_record is None or not _same_file_identity(lifecycle_identity, current_record):
            raise PortablePacketError("Work work.json changed while packet-import read its lifecycle state")

    def _read_regular(
        self,
        directory: Path,
        directory_identity: os.stat_result,
        name: str,
        *,
        label: str,
    ) -> bytes:
        _secure_child_name(name)
        self._assert_tree_stable()
        self._assert_directory_identity(directory, directory_identity, label=label.rsplit(" ", 1)[0])
        target = directory / name
        before = _guarded_regular(target, label=label)
        if before is None:
            raise PortablePacketError(f"{label} is missing")
        raw = _read_bounded_regular_file(target, label=label)
        after = _guarded_regular(target, label=label)
        if after is None or not _same_file_identity(before, after):
            raise PortablePacketError(f"{label} changed while being read")
        self._assert_directory_identity(directory, directory_identity, label=label.rsplit(" ", 1)[0])
        self._assert_tree_stable()
        return raw

    def _write_new(
        self,
        directory: Path,
        directory_identity: os.stat_result,
        name: str,
        data: bytes,
        *,
        label: str,
    ) -> None:
        self.assert_work_current_and_importable()
        _secure_child_name(name)
        self._assert_tree_stable()
        self._assert_directory_identity(directory, directory_identity, label="Packet staging directory")
        target = directory / name
        if _guarded_regular(target, label=label) is not None:
            raise PortablePacketError(f"{label} already exists")
        temporary: Path | None = None
        descriptor: int | None = None
        try:
            for _ in range(16):
                candidate = directory / f".{name}.{os.getpid()}.{secrets.token_hex(12)}.tmp"
                try:
                    descriptor = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                except FileExistsError:
                    continue
                except OSError as error:
                    raise PortablePacketError(f"{label} cannot be written in guarded storage") from error
                temporary = candidate
                break
            if temporary is None or descriptor is None:
                raise PortablePacketError(f"{label} could not allocate a guarded temporary file")
            offset = 0
            while offset < len(data):
                written = os.write(descriptor, data[offset:])
                if written <= 0:
                    raise PortablePacketError(f"{label} could not be written completely")
                offset += written
            os.fsync(descriptor)
            expected_temporary = os.fstat(descriptor)
            os.close(descriptor)
            descriptor = None
            actual_temporary = _guarded_regular(temporary, label=f"{label} temporary file")
            if actual_temporary is None or not _same_file_identity(expected_temporary, actual_temporary):
                raise PortablePacketError(f"{label} temporary file changed before publication")
            self._assert_directory_identity(directory, directory_identity, label="Packet staging directory")
            # ``link`` supplies the guarded no-overwrite final-name primitive
            # where the operator has independently verified a suitable local
            # filesystem. Its availability alone does not establish volume type.
            try:
                self.assert_work_current_and_importable()
                os.link(temporary, target)
            except FileExistsError as error:
                raise PortablePacketError(f"{label} already exists") from error
            except OSError as error:
                raise PortablePacketError(
                    f"{label} requires guarded no-overwrite links on an operator-verified local filesystem"
                ) from error
            finalized = _guarded_regular(target, label=label)
            if finalized is None or not _same_file_identity(expected_temporary, finalized):
                raise PortablePacketError(f"{label} changed during guarded publication")
            temporary.unlink()
            temporary = None
            self._assert_directory_identity(directory, directory_identity, label="Packet staging directory")
            self._assert_tree_stable()
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def _packet_directory(self, digest: str) -> os.stat_result | None:
        self._assert_tree_stable()
        path = self.root / digest
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise PortablePacketError("External handoff packet directory cannot be inspected") from error
        if _is_link_or_reparse_point(metadata) or not stat.S_ISDIR(metadata.st_mode):
            raise PortablePacketError("External handoff packet directory must be a real directory, not a link")
        if not _has_stable_file_identity(metadata):
            raise PortablePacketError("External handoff packet directory requires stable file identities")
        return metadata

    def _validate_stored_packet(
        self,
        directory: Path,
        directory_identity: os.stat_result,
        *,
        digest: str,
        work: Path,
        origin: str,
    ) -> tuple[bytes, dict[str, Any], dict[str, Any], dict[str, Any]]:
        raw = self._read_regular(directory, directory_identity, "bundle.json", label="Existing frozen handoff bundle")
        if _sha256(raw) != digest:
            raise PortablePacketError("Existing frozen handoff bundle does not match its content-addressed directory")
        bundle = validate_handoff_bundle_v1(raw)
        if bundle["source_surface"] != origin:
            raise PortablePacketError("Existing frozen handoff bundle has an unsupported source surface")
        packet = bundle["packet"]
        receipt = _read_receipt_bytes(
            self._read_regular(directory, directory_identity, "receipt.json", label="Existing bridge receipt"),
            name="receipt.json",
        )
        _validate_existing_receipt(
            receipt,
            work=work,
            bundle=bundle,
            packet=packet,
            digest=digest,
            byte_count=len(raw),
            origin=origin,
        )
        return raw, bundle, packet, receipt

    def _quarantine_incomplete(self, digest: str, directory_identity: os.stat_result) -> None:
        source = self.root / digest
        # This rename mutates retained Work evidence. The guarded route has no
        # descriptor authority, so bind the latest lifecycle record before it
        # touches the incomplete directory as well as while it checks the tree.
        self.assert_work_current_and_importable()
        self._assert_tree_stable()
        self._assert_directory_identity(source, directory_identity, label="External handoff packet directory")
        for _ in range(16):
            target = self.root / f".quarantine-{digest}-{secrets.token_hex(12)}"
            try:
                os.lstat(target)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise PortablePacketError("Packet quarantine directory cannot be inspected") from error
            else:
                continue
            try:
                self.assert_work_current_and_importable()
                os.rename(source, target)
            except FileExistsError:
                continue
            except OSError as error:
                raise PortablePacketError("Incomplete frozen handoff packet could not be quarantined") from error
            actual = _guarded_directory(target, label="Packet quarantine directory", create=False)
            if not _same_file_identity(directory_identity, actual):
                raise PortablePacketError("Quarantined packet directory changed during rename")
            self._assert_tree_stable()
            return
        raise PortablePacketError("Could not allocate a quarantine location for an incomplete handoff packet")

    def reject_idempotency_conflict(self, packet: dict[str, Any], digest: str, *, work: Path, origin: str) -> None:
        self._assert_tree_stable()
        try:
            entries = sorted(self.root.iterdir(), key=lambda item: item.name)
        except OSError as error:
            raise PortablePacketError("External handoff storage cannot be listed in guarded mode") from error
        for entry in entries:
            if _DIGEST_DIRECTORY.fullmatch(entry.name) is None:
                continue
            directory_identity = self._packet_directory(entry.name)
            if directory_identity is None:
                continue
            has_bundle = _guarded_regular(entry / "bundle.json", label="Existing frozen handoff bundle") is not None
            has_receipt = _guarded_regular(entry / "receipt.json", label="Existing bridge receipt") is not None
            if has_bundle != has_receipt or not has_bundle:
                self._quarantine_incomplete(entry.name, directory_identity)
                continue
            _raw, _bundle, stored_packet, receipt = self._validate_stored_packet(
                entry,
                directory_identity,
                digest=entry.name,
                work=work,
                origin=origin,
            )
            same_packet_id = stored_packet["packet_id"] == packet["packet_id"]
            same_key = stored_packet["idempotency_key"] == packet["idempotency_key"]
            if (same_packet_id or same_key) and receipt["bundle_sha256"] != digest:
                conflict = "packet_id" if same_packet_id else "idempotency_key"
                raise PortablePacketError(f"External packet {conflict} was already received with different bundle bytes")
        self._assert_tree_stable()

    def existing_packet(
        self,
        digest: str,
        *,
        raw: bytes,
        work: Path,
        bundle: dict[str, Any],
        packet: dict[str, Any],
        origin: str,
    ) -> dict[str, Any] | None:
        identity = self._packet_directory(digest)
        if identity is None:
            return None
        directory = self.root / digest
        has_bundle = _guarded_regular(directory / "bundle.json", label="Existing frozen handoff bundle") is not None
        has_receipt = _guarded_regular(directory / "receipt.json", label="Existing bridge receipt") is not None
        if has_bundle != has_receipt or not has_bundle:
            self._quarantine_incomplete(digest, identity)
            return None
        stored_raw, _stored_bundle, _stored_packet, receipt = self._validate_stored_packet(
            directory,
            identity,
            digest=digest,
            work=work,
            origin=origin,
        )
        if stored_raw != raw:
            raise PortablePacketError("Content-addressed frozen handoff bundle differs from incoming bytes")
        _validate_existing_receipt(
            receipt,
            work=work,
            bundle=bundle,
            packet=packet,
            digest=digest,
            byte_count=len(raw),
            origin=origin,
        )
        return receipt

    def stage_packet_directory(self, digest: str) -> tuple[Path, os.stat_result]:
        self.assert_work_current_and_importable()
        for _ in range(16):
            stage = self.root / f".stage-{digest}-{secrets.token_hex(12)}"
            try:
                self.assert_work_current_and_importable()
                stage.mkdir(mode=0o700)
            except FileExistsError:
                continue
            except OSError as error:
                raise PortablePacketError("Could not allocate a guarded packet staging directory") from error
            identity = _guarded_directory(stage, label="Packet staging directory", create=False)
            self._assert_tree_stable()
            return stage, identity
        raise PortablePacketError("Could not allocate a guarded packet staging directory")

    def verify_staged_packet(
        self,
        stage: Path,
        identity: os.stat_result,
        *,
        raw: bytes,
        work: Path,
        bundle: dict[str, Any],
        packet: dict[str, Any],
        digest: str,
        origin: str,
    ) -> None:
        self.assert_work_current_and_importable()
        staged_raw = self._read_regular(stage, identity, "bundle.json", label="Frozen handoff bundle")
        if staged_raw != raw:
            raise PortablePacketError("Staged frozen handoff bundle changed before publication")
        receipt = _read_receipt_bytes(
            self._read_regular(stage, identity, "receipt.json", label="Bridge receipt"),
            name="receipt.json",
        )
        _validate_existing_receipt(
            receipt,
            work=work,
            bundle=bundle,
            packet=packet,
            digest=digest,
            byte_count=len(raw),
            origin=origin,
        )

    def publish_staged_packet(
        self,
        stage: Path,
        identity: os.stat_result,
        digest: str,
        *,
        raw: bytes,
        work: Path,
        bundle: dict[str, Any],
        packet: dict[str, Any],
        origin: str,
    ) -> None:
        self.assert_work_current_and_importable()
        self._assert_directory_identity(stage, identity, label="Packet staging directory")
        destination = self.root / digest
        if self._packet_directory(digest) is not None:
            raise PortablePacketError("Content-addressed frozen handoff packet appeared during import")
        try:
            self.assert_work_current_and_importable()
            os.rename(stage, destination)
        except FileExistsError as error:
            raise PortablePacketError("Content-addressed frozen handoff packet appeared during import") from error
        except OSError as error:
            raise PortablePacketError("Completed packet staging could not be published in guarded mode") from error
        final_identity = _guarded_directory(destination, label="Published frozen handoff packet", create=False)
        if not _same_file_identity(identity, final_identity):
            raise PortablePacketError("Published packet directory does not match the staged packet")
        stored_raw, _stored_bundle, _stored_packet, receipt = self._validate_stored_packet(
            destination,
            final_identity,
            digest=digest,
            work=work,
            origin=origin,
        )
        if stored_raw != raw:
            raise PortablePacketError("Published frozen handoff bundle differs from staged bytes")
        _validate_existing_receipt(
            receipt,
            work=work,
            bundle=bundle,
            packet=packet,
            digest=digest,
            byte_count=len(raw),
            origin=origin,
        )
        self._assert_tree_stable()
        # A committed digest remains retained if the Work was sealed while it
        # was being verified, but the current import must fail rather than
        # report success against that sealed lifecycle state.
        self.assert_work_current_and_importable()

    def discard_staged_packet(self, stage: Path, identity: os.stat_result) -> None:
        try:
            self.assert_work_current_and_importable()
            self._assert_tree_stable()
            self._assert_directory_identity(stage, identity, label="Packet staging directory")
        except PortablePacketError:
            return
        for name in ("receipt.json", "bundle.json"):
            leaf = stage / name
            try:
                self.assert_work_current_and_importable()
                metadata = _guarded_regular(leaf, label="Packet staging file")
                if metadata is not None:
                    leaf.unlink()
            except (OSError, PortablePacketError):
                return
        try:
            self.assert_work_current_and_importable()
            stage.rmdir()
        except (OSError, PortablePacketError):
            pass


def _import_packet_guarded_windows(
    root: Path,
    work: Path,
    expected_tree: _ImportWorkIdentitySnapshot,
    raw: bytes,
    bundle: dict[str, Any],
    *,
    origin: str,
) -> dict[str, Any]:
    """Retain validated bytes in Windows guarded-exclusive-Work mode."""

    packet = bundle["packet"]
    digest = _sha256(raw)
    with _GuardedWindowsPacketStore(root, work.name, expected_tree=expected_tree) as store:
        store.reject_idempotency_conflict(packet, digest, work=work, origin=origin)
        existing = store.existing_packet(
            digest,
            raw=raw,
            work=work,
            bundle=bundle,
            packet=packet,
            origin=origin,
        )
        if existing is not None:
            store.assert_work_current_and_importable()
            relative_root = f"workspace/bridge-packets/v1/{digest}"
            return {
                "status": "already_received",
                "storage_mode": "guarded_exclusive_work",
                "work_id": work.name,
                "bundle_id": bundle["bundle_id"],
                "bundle_sha256": digest,
                "bundle_bytes": len(raw),
                "bundle_path": f"{relative_root}/bundle.json",
                "receipt_path": f"{relative_root}/receipt.json",
                "bundle_written": False,
                "packet_id": packet["packet_id"],
                "feedback_items": len(packet["feedback_events"]),
                "triage_state": "needs_source_corroboration",
                "truth_boundaries": {
                    key: existing[key]
                    for key in (
                        "evidence_class",
                        "raw_conversation_provenance",
                        "automatic_learning",
                        "acceptance_claim",
                        "tool_execution_claim",
                        "capability_claim",
                        "visual_review_claim",
                        "authorization_claim",
                        "attachment_transfer",
                    )
                },
            }

        stage, identity = store.stage_packet_directory(digest)
        try:
            store._write_new(stage, identity, "bundle.json", raw, label="Frozen handoff bundle")
            receipt = _receipt(
                work=work,
                bundle=bundle,
                packet=packet,
                digest=digest,
                byte_count=len(raw),
                origin=origin,
            )
            receipt_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            store._write_new(stage, identity, "receipt.json", receipt_bytes, label="Bridge receipt")
            store.verify_staged_packet(
                stage,
                identity,
                raw=raw,
                work=work,
                bundle=bundle,
                packet=packet,
                digest=digest,
                origin=origin,
            )
            store.publish_staged_packet(
                stage,
                identity,
                digest,
                raw=raw,
                work=work,
                bundle=bundle,
                packet=packet,
                origin=origin,
            )
        except Exception:
            store.discard_staged_packet(stage, identity)
            raise
        store.assert_work_current_and_importable()
        relative_root = f"workspace/bridge-packets/v1/{digest}"
        return {
            "status": "received_external_unverified",
            "storage_mode": "guarded_exclusive_work",
            "work_id": work.name,
            "bundle_id": bundle["bundle_id"],
            "bundle_sha256": digest,
            "bundle_bytes": len(raw),
            "bundle_path": f"{relative_root}/bundle.json",
            "receipt_path": f"{relative_root}/receipt.json",
            "bundle_written": True,
            "packet_id": packet["packet_id"],
            "feedback_items": len(packet["feedback_events"]),
            "triage_state": "needs_source_corroboration",
            "truth_boundaries": {
                key: receipt[key]
                for key in (
                    "evidence_class",
                    "raw_conversation_provenance",
                    "automatic_learning",
                    "acceptance_claim",
                    "tool_execution_claim",
                    "capability_claim",
                    "visual_review_claim",
                    "authorization_claim",
                    "attachment_transfer",
                )
            },
        }


def import_packet(
    repo: Path | str,
    work_dir: Path | str,
    packet_path: Path | str,
    *,
    origin: str,
) -> dict[str, Any]:
    """Freeze a portable PowerPoint handoff bundle without promoting assertions.

    Source bytes are fully validated before a host-specific storage path is
    selected.  macOS/Linux use descriptor-bound storage; Windows uses the
    separately documented guarded exclusive-Work path below.
    """

    if origin != "chatgpt-powerpoint":
        raise PortablePacketError("packet-import v1 accepts origin chatgpt-powerpoint only")
    root, work = _import_work_locator(repo, work_dir)
    expected_tree = _capture_import_work_identity_snapshot(root, work)
    raw = _read_bounded_regular_source(packet_path)
    bundle = validate_handoff_bundle_v1(raw)
    if bundle["source_surface"] != origin:
        raise PortablePacketError("Handoff bundle source_surface does not match --origin")
    if _secure_storage_supported():
        with _same_process_work_import_lease(expected_tree):
            return _import_packet_descriptor_bound(root, work, expected_tree, raw, bundle, origin=origin)
    if _guarded_windows_fallback_enabled():
        with _same_process_work_import_lease(expected_tree):
            return _import_packet_guarded_windows(root, work, expected_tree, raw, bundle, origin=origin)
    raise PortablePacketError(
        "packet-import requires descriptor-bound storage on this host; the guarded fallback is Windows-only"
    )


def _portable_family_for_rule(rule: dict[str, Any]) -> str | None:
    """Choose a static generic projection without exporting source wording."""

    explicit = rule.get("portable_family")
    if isinstance(explicit, str) and explicit in _PORTABLE_LEARNED_FAMILIES:
        return explicit
    # This heuristic is evaluated only inside the local process. It never
    # serializes the source rule identifier, action, exceptions, quotes or any
    # associated evidence; unmatched rules are conservatively withheld.
    material = " ".join(str(rule.get(key, "")) for key in ("id", "action", "applies_when", "exceptions", "scope_reason")).casefold()
    for family, keywords in _FAMILY_KEYWORDS.items():
        if any(keyword.casefold() in material for keyword in keywords):
            return family
    return None


def _safe_static_rules() -> list[dict[str, Any]]:
    return [dict(rule, status="active") for rule in _PORTABLE_BASE_RULES]


def export_guidance_snapshot(repo: Path | str, *, include_learned: bool = True) -> dict[str, Any]:
    """Create a de-identified, read-only Office guidance snapshot in memory.

    Free-form learned text is intentionally never exported. A fully intact
    local guidance set may contribute only a finite, static generic family;
    partial or unverifiable local guidance falls back to the base vocabulary.
    Imported bundles and their dispositions cannot enter this path.
    """

    from .evolution import load_guidance

    workspace = Path(repo).expanduser().resolve()
    rules = _safe_static_rules()
    learned_projection = "base_only_requested"
    withheld = 0
    if include_learned:
        guidance = load_guidance(workspace)
        if guidance.get("status") != "ready" or guidance.get("errors"):
            learned_projection = "withheld_integrity_not_ready"
            withheld = len(guidance.get("rules", []))
        else:
            projected: dict[str, dict[str, Any]] = {}
            for raw in guidance.get("rules", []):
                if not isinstance(raw, dict):
                    withheld += 1
                    continue
                family = _portable_family_for_rule(raw)
                template = _PORTABLE_LEARNED_FAMILIES.get(family or "")
                if template is None:
                    withheld += 1
                    continue
                projected[family] = {
                    "rule_id": f"learned:{family}",
                    **template,
                    "status": "active",
                }
            rules.extend(projected[key] for key in sorted(projected))
            learned_projection = "included_verified" if projected else "withheld_no_generic_projection"
    ordered = sorted({rule["rule_id"]: rule for rule in rules}.values(), key=lambda rule: rule["rule_id"])
    canonical = json.dumps(ordered, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    guidance_revision = "v1-" + _sha256(canonical)[:16]
    snapshot = {
        "schema": GUIDANCE_SCHEMA,
        "snapshot_id": "guidance-" + _sha256(canonical)[:16],
        "created_at": _now(),
        "source_workbench": "aippt-studio",
        "guidance_revision": guidance_revision,
        "transfer_scope": "sanitized_active_guidance_only",
        "authorization": {"state": "not_recorded", "evidence": []},
        "learned_projection": learned_projection,
        "status": "active_read_only" if ordered else "no_active_guidance_available",
        "rules": ordered,
        "limitations": [
            "This is read-only, de-identified guidance; the user must explicitly provide or authorize it for the current Office task.",
            "Each Office task must record whether a considered rule was applied, not_applicable, needs_revision, or not_reviewed.",
            "The snapshot does not prove a native edit, visual review, delivery, user acceptance, or Studio core repair.",
            (
                "Some learned guidance was withheld because the local guidance integrity set was incomplete or it could not be projected to a static generic family."
                if withheld or learned_projection.startswith("withheld_")
                else "Only static generic guidance is included; raw feedback, source locations, receipts and client material are excluded."
            ),
        ],
    }
    _scan_portable_value(snapshot)
    return snapshot

def write_guidance_snapshot(
    repo: Path | str,
    output: Path | str,
    *,
    include_learned: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write an explicitly requested portable snapshot, never auto-transmit it."""

    destination = Path(output).expanduser().resolve()
    if destination.exists() and not destination.is_file():
        raise PortablePacketError("--output must be a file path")
    if destination.exists() and not overwrite:
        raise PortablePacketError("Guidance output already exists; use --overwrite after reviewing the new snapshot")
    snapshot = export_guidance_snapshot(repo, include_learned=include_learned)
    payload = (json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(destination, payload, mode=0o600)
    return {
        "status": "exported",
        "schema": GUIDANCE_SCHEMA,
        "snapshot_id": snapshot["snapshot_id"],
        "guidance_revision": snapshot["guidance_revision"],
        "rule_count": len(snapshot["rules"]),
        "bytes": len(payload),
        "sha256": _sha256(payload),
        "output": str(destination),
        "transmission": "not_transmitted",
    }
