from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from aippt_studio import portable_contracts
from aippt_studio.portable_contracts import PortableContractError, is_rfc3339_datetime, validate_contract_instance
from aippt_studio.portable_packets import PortablePacketError, export_guidance_snapshot, validate_handoff_bundle_v1


STUDIO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = STUDIO_ROOT / "integrations" / "aippt-studio-powerpoint" / "references" / "schemas"
HANDOFF_DOC = STUDIO_ROOT / "integrations" / "aippt-studio-powerpoint" / "references" / "handoff-schemas.md"

_SCHEMA_FILES = {
    "aippt-presentation-intent-v1": "aippt-presentation-intent-v1.schema.json",
    "aippt-bridge-capabilities-v1": "aippt-bridge-capabilities-v1.schema.json",
    "aippt-expression-review-v1": "aippt-expression-review-v1.schema.json",
    "aippt-learning-packet-v1": "aippt-learning-packet-v1.schema.json",
    "aippt-guidance-snapshot-v1": "aippt-guidance-snapshot-v1.schema.json",
    "aippt-handoff-bundle-v1": "aippt-handoff-bundle-v1.schema.json",
}


class ContractValidationError(AssertionError):
    pass


def _schemas() -> dict[str, tuple[Path, dict[str, Any]]]:
    return {
        name: (path := SCHEMA_DIR / filename, json.loads(path.read_text(encoding="utf-8")))
        for name, filename in _SCHEMA_FILES.items()
    }


def _resolve_pointer(document: Any, fragment: str, *, location: str) -> Any:
    if not fragment:
        return document
    if not fragment.startswith("/"):
        raise ContractValidationError(f"{location}: unsupported reference fragment {fragment!r}")
    current = document
    for segment in fragment[1:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            raise ContractValidationError(f"{location}: missing reference fragment {fragment!r}")
    return current


def _resolve_reference(reference: str, *, document: dict[str, Any], document_path: Path, location: str) -> tuple[dict[str, Any], Path, Any]:
    filename, separator, fragment = reference.partition("#")
    if filename:
        target_path = (document_path.parent / filename).resolve()
        if not target_path.is_relative_to(SCHEMA_DIR.resolve()) or not target_path.is_file():
            raise ContractValidationError(f"{location}: non-local or missing reference {reference!r}")
        target_document = json.loads(target_path.read_text(encoding="utf-8"))
    else:
        target_path = document_path
        target_document = document
    return target_document, target_path, _resolve_pointer(
        target_document, fragment if separator else "", location=location
    )


def _is_valid(instance: Any, schema: dict[str, Any], *, document: dict[str, Any], document_path: Path) -> bool:
    try:
        _validate(instance, schema, document=document, document_path=document_path, location="$")
    except ContractValidationError:
        return False
    return True


def _validate(instance: Any, schema: Any, *, document: dict[str, Any], document_path: Path, location: str) -> None:
    if not isinstance(schema, dict):
        raise ContractValidationError(f"{location}: schema node must be an object")
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str):
            raise ContractValidationError(f"{location}: $ref must be text")
        target_document, target_path, target = _resolve_reference(
            reference, document=document, document_path=document_path, location=location
        )
        _validate(instance, target, document=target_document, document_path=target_path, location=location)
        return
    if "const" in schema and instance != schema["const"]:
        raise ContractValidationError(f"{location}: expected const {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise ContractValidationError(f"{location}: value {instance!r} is outside enum")
    declared_type = schema.get("type")
    if declared_type == "object" and not isinstance(instance, dict):
        raise ContractValidationError(f"{location}: expected object")
    if declared_type == "array" and not isinstance(instance, list):
        raise ContractValidationError(f"{location}: expected array")
    if declared_type == "string" and not isinstance(instance, str):
        raise ContractValidationError(f"{location}: expected string")
    if declared_type == "boolean" and not isinstance(instance, bool):
        raise ContractValidationError(f"{location}: expected boolean")
    if "allOf" in schema:
        for index, item in enumerate(schema["allOf"]):
            _validate(instance, item, document=document, document_path=document_path, location=f"{location}.allOf[{index}]")
    if "if" in schema:
        branch = "then" if _is_valid(instance, schema["if"], document=document, document_path=document_path) else "else"
        if branch in schema:
            _validate(instance, schema[branch], document=document, document_path=document_path, location=f"{location}.{branch}")
    if "not" in schema and _is_valid(instance, schema["not"], document=document, document_path=document_path):
        raise ContractValidationError(f"{location}: unexpectedly satisfies forbidden schema")
    if isinstance(instance, dict):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise ContractValidationError(f"{location}: required must be a list")
        missing = [key for key in required if key not in instance]
        if missing:
            raise ContractValidationError(f"{location}: missing {missing}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ContractValidationError(f"{location}: properties must be an object")
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(properties)
            if extra:
                raise ContractValidationError(f"{location}: unknown fields {sorted(extra)}")
        for source, dependencies in schema.get("dependentRequired", {}).items():
            if source in instance:
                absent = [item for item in dependencies if item not in instance]
                if absent:
                    raise ContractValidationError(f"{location}: {source} requires {absent}")
        for key, value in instance.items():
            if key in properties:
                _validate(value, properties[key], document=document, document_path=document_path, location=f"{location}.{key}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise ContractValidationError(f"{location}: below minItems")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise ContractValidationError(f"{location}: above maxItems")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(value, ensure_ascii=False, sort_keys=True) for value in instance]
            if len(encoded) != len(set(encoded)):
                raise ContractValidationError(f"{location}: duplicate array value")
        if "items" in schema:
            for index, value in enumerate(instance):
                _validate(value, schema["items"], document=document, document_path=document_path, location=f"{location}[{index}]")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise ContractValidationError(f"{location}: below minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise ContractValidationError(f"{location}: above maxLength")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise ContractValidationError(f"{location}: does not match pattern")
        if schema.get("format") == "date-time" and not is_rfc3339_datetime(instance):
            raise ContractValidationError(f"{location}: invalid RFC 3339 date-time")


def _fenced_examples() -> dict[str, dict[str, Any]]:
    blocks = re.findall(r"~~~json\n(.*?)\n~~~", HANDOFF_DOC.read_text(encoding="utf-8"), re.DOTALL)
    examples = [json.loads(block) for block in blocks]
    by_schema = {item["schema"]: item for item in examples}
    expected = set(_SCHEMA_FILES)
    assert len(by_schema) == len(examples)
    assert set(by_schema) == expected
    return by_schema


def _bundle_from_examples(examples: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return copy.deepcopy(examples["aippt-handoff-bundle-v1"])


def _validate_named(instance: dict[str, Any], name: str) -> None:
    path, schema = _schemas()[name]
    _validate(instance, schema, document=schema, document_path=path, location="$")
    try:
        validate_contract_instance(instance, name)
    except PortableContractError as error:
        raise ContractValidationError(str(error)) from error


def test_every_fenced_contract_example_validates_against_its_canonical_schema():
    examples = _fenced_examples()
    for name, example in examples.items():
        _validate_named(example, name)
    bundle = _bundle_from_examples(examples)
    _validate_named(bundle, "aippt-handoff-bundle-v1")
    assert validate_handoff_bundle_v1(json.dumps(bundle, ensure_ascii=False).encode("utf-8"))["bundle_id"] == bundle["bundle_id"]


def test_schema_rejects_contradictory_packet_lifecycle_and_attachment_states():
    examples = _fenced_examples()
    packet = examples["aippt-learning-packet-v1"]

    prepared_with_receipt = copy.deepcopy(packet)
    prepared_with_receipt["recipient_receipt"] = {"synthetic": "receiver claims are local-only"}
    with pytest.raises(ContractValidationError, match="unknown fields"):
        _validate_named(prepared_with_receipt, "aippt-learning-packet-v1")

    receiver_state = copy.deepcopy(packet)
    receiver_state["transmission_state"] = "received"
    with pytest.raises(ContractValidationError, match="outside enum"):
        _validate_named(receiver_state, "aippt-learning-packet-v1")

    unauthorized_attachment = copy.deepcopy(packet)
    unauthorized_attachment["attachment_transfer"] = []
    with pytest.raises(ContractValidationError, match="unknown fields"):
        _validate_named(unauthorized_attachment, "aippt-learning-packet-v1")

    prepared_claims_transferred = copy.deepcopy(packet)
    prepared_claims_transferred["transfer_scope"]["mode"] = "sanitized_summary_with_explicit_attachment_transfer"
    with pytest.raises(ContractValidationError, match="outside enum"):
        _validate_named(prepared_claims_transferred, "aippt-learning-packet-v1")

    unproven_transmission = copy.deepcopy(packet)
    unproven_transmission["transmission_state"] = "transmitted"
    unproven_transmission["transmission_evidence"] = []
    with pytest.raises(ContractValidationError, match="below minItems"):
        _validate_named(unproven_transmission, "aippt-learning-packet-v1")


def test_schema_rejects_guidance_status_and_rule_count_mismatch(tmp_path: Path):
    examples = _fenced_examples()
    active = copy.deepcopy(examples["aippt-guidance-snapshot-v1"])
    active["rules"] = []
    with pytest.raises(ContractValidationError, match="below minItems"):
        _validate_named(active, "aippt-guidance-snapshot-v1")

    inactive = copy.deepcopy(examples["aippt-guidance-snapshot-v1"])
    inactive["status"] = "no_active_guidance_available"
    with pytest.raises(ContractValidationError, match="above maxItems"):
        _validate_named(inactive, "aippt-guidance-snapshot-v1")

    exported = export_guidance_snapshot(tmp_path, include_learned=False)
    _validate_named(exported, "aippt-guidance-snapshot-v1")

    unproven_direct_authorization = copy.deepcopy(exported)
    unproven_direct_authorization["authorization"] = {"state": "authorized_direct_bridge", "evidence": []}
    with pytest.raises(ContractValidationError, match="below minItems"):
        _validate_named(unproven_direct_authorization, "aippt-guidance-snapshot-v1")


def test_schema_rejects_bundle_with_dangling_optional_record_reference():
    bundle = _bundle_from_examples(_fenced_examples())
    bundle.pop("expression_review")
    with pytest.raises(ContractValidationError, match="missing"):
        _validate_named(bundle, "aippt-handoff-bundle-v1")


def test_canonical_validator_keeps_json_booleans_distinct_from_numbers():
    packet = copy.deepcopy(_fenced_examples()["aippt-learning-packet-v1"])
    packet["transfer_scope"]["deck_content_embedded"] = 0

    with pytest.raises(PortableContractError, match="expected const"):
        validate_contract_instance(packet, "aippt-learning-packet-v1")

    # The released schemas currently use string enums, so exercise the shared
    # JSON semantic comparator directly with the boolean/number case that
    # Python's normal ``in`` comparison would otherwise accept.
    with pytest.raises(PortableContractError, match="outside enum"):
        portable_contracts._validate(
            0,
            {"enum": [False]},
            document={},
            document_path=Path("synthetic.schema.json"),
            location="$",
        )


@pytest.mark.parametrize(
    "invalid",
    [
        "2026-09-22 00:00:00+00:00",
        "2026-09-22X00:00:00+00:00",
        "20260922T000000+0000",
        "2026-09-22T00:00:00+00",
        "2026-09-22T00:00:00+00:00:00",
        "2026-09-22T00:00:00,1+00:00",
        "2026-09-22T00:00:00+14:60",
        "2026-09-22T00:00:00-14:60",
        "2026-09-22T00:00:00+14:99",
    ],
)
def test_canonical_contract_and_packet_receiver_reject_non_rfc3339_timestamps(invalid: str):
    bundle = _bundle_from_examples(_fenced_examples())
    for record in (
        bundle,
        bundle["packet"],
        bundle["presentation_intent"],
        bundle["expression_review"],
        bundle["capability_report"],
    ):
        record["created_at"] = invalid

    with pytest.raises(PortableContractError, match="RFC 3339"):
        validate_contract_instance(bundle, "aippt-handoff-bundle-v1")
    with pytest.raises(PortablePacketError, match="RFC 3339"):
        validate_handoff_bundle_v1(json.dumps(bundle, ensure_ascii=False).encode("utf-8"))


@pytest.mark.parametrize(
    "valid",
    ["2026-09-22T00:00:00Z", "2026-09-22T00:00:00+00:00", "2026-09-22t00:00:00z", "2026-09-22T00:00:00+23:59", "2026-09-22T00:00:00-00:00"],
)
def test_rfc3339_contract_gate_accepts_canonical_timestamps(valid: str):
    assert is_rfc3339_datetime(valid)


@pytest.mark.parametrize("valid", ["0000-01-01T00:00:00Z", "0000-02-29T23:59:59+23:59"])
def test_rfc3339_contract_gate_accepts_year_zero_and_proleptic_leap_day(valid: str):
    bundle = _bundle_from_examples(_fenced_examples())
    for record in (
        bundle,
        bundle["packet"],
        bundle["presentation_intent"],
        bundle["expression_review"],
        bundle["capability_report"],
    ):
        record["created_at"] = valid
    assert is_rfc3339_datetime(valid)
    validate_contract_instance(bundle, "aippt-handoff-bundle-v1")
    assert validate_handoff_bundle_v1(json.dumps(bundle, ensure_ascii=False).encode("utf-8"))["bundle_id"] == bundle["bundle_id"]


@pytest.mark.parametrize("valid", ["2016-12-31T23:59:60Z", "2017-01-01T00:59:60+01:00"])
def test_rfc3339_contract_gate_accepts_announced_positive_leap_seconds(valid: str):
    bundle = _bundle_from_examples(_fenced_examples())
    for record in (
        bundle,
        bundle["packet"],
        bundle["presentation_intent"],
        bundle["expression_review"],
        bundle["capability_report"],
    ):
        record["created_at"] = valid
    assert is_rfc3339_datetime(valid)
    validate_contract_instance(bundle, "aippt-handoff-bundle-v1")
    assert validate_handoff_bundle_v1(json.dumps(bundle, ensure_ascii=False).encode("utf-8"))["bundle_id"] == bundle["bundle_id"]


@pytest.mark.parametrize(
    "invalid",
    ["0000-02-30T00:00:00Z", "0000-02-29T23:59:60Z", "1900-02-29T00:00:00Z", "2016-12-30T23:59:60Z", "2016-12-31T23:59:61Z"],
)
def test_rfc3339_contract_gate_rejects_invalid_positive_leap_seconds(invalid: str):
    assert not is_rfc3339_datetime(invalid)
