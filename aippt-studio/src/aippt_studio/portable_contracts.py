"""Strict, dependency-free validation for the shipped portable contract schemas.

The portable handoff is deliberately validated against the exact schemas that
are released with the PowerPoint skill.  Installing a general JSON-Schema
runtime solely for this small, fixed contract would add another host-dependent
runtime dependency.  This module therefore implements the complete subset used
by the six checked-in Draft 2020-12 schemas and fails closed if a future schema
introduces an unsupported validation keyword.

It is not a general-purpose JSON Schema implementation.  Its narrow scope is a
feature: every supported keyword is exercised by the portable contract tests,
and a missing local schema is an error rather than an invitation to accept
unvalidated external data.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


class PortableContractError(ValueError):
    """A value does not satisfy a canonical portable contract schema."""


_SCHEMA_FILENAMES = {
    "aippt-presentation-intent-v1": "aippt-presentation-intent-v1.schema.json",
    "aippt-bridge-capabilities-v1": "aippt-bridge-capabilities-v1.schema.json",
    "aippt-expression-review-v1": "aippt-expression-review-v1.schema.json",
    "aippt-learning-packet-v1": "aippt-learning-packet-v1.schema.json",
    "aippt-guidance-snapshot-v1": "aippt-guidance-snapshot-v1.schema.json",
    "aippt-handoff-bundle-v1": "aippt-handoff-bundle-v1.schema.json",
}
_ANNOTATION_KEYWORDS = {"$schema", "$id", "title", "description", "default"}
_VALIDATION_KEYWORDS = {
    "$ref",
    "$defs",
    "type",
    "additionalProperties",
    "required",
    "properties",
    "dependentRequired",
    "allOf",
    "if",
    "then",
    "else",
    "not",
    "const",
    "enum",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minItems",
    "maxItems",
    "uniqueItems",
    "items",
}
_SUPPORTED_TYPES = {"object", "array", "string", "boolean", "null", "integer", "number"}
_RFC3339_DATE_TIME_PATTERN = re.compile(
    r"^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])[Tt]"
    r"(?:[01]\d|2[0-3]):[0-5]\d:(?:[0-5]\d|60)(?:\.\d+)?"
    r"(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)
# RFC 3339 permits ``:60`` only at a positive UTC leap second. Python's
# datetime deliberately cannot represent that second, so validate it by
# parsing its immediately preceding ``:59`` instant and comparing the UTC day
# to the published IERS leap-second dates through the current table horizon.
_UTC_POSITIVE_LEAP_SECOND_DATES = frozenset(
    {
        date(1972, 6, 30), date(1972, 12, 31), date(1973, 12, 31), date(1974, 12, 31),
        date(1975, 12, 31), date(1976, 12, 31), date(1977, 12, 31), date(1978, 12, 31),
        date(1979, 12, 31), date(1981, 6, 30), date(1982, 6, 30), date(1983, 6, 30),
        date(1985, 6, 30), date(1987, 12, 31), date(1989, 12, 31), date(1990, 12, 31),
        date(1992, 6, 30), date(1993, 6, 30), date(1994, 6, 30), date(1995, 12, 31),
        date(1997, 6, 30), date(1998, 12, 31), date(2005, 12, 31), date(2008, 12, 31),
        date(2012, 6, 30), date(2015, 6, 30), date(2016, 12, 31),
    }
)


def _is_proleptic_gregorian_date(year: int, month: int, day: int) -> bool:
    """Validate RFC 3339's four-digit proleptic Gregorian date component.

    RFC 3339 permits the year ``0000``. Python's ``datetime`` intentionally
    starts at year 1, so using it for ordinary calendar validation would make
    the released contract reject a syntactically and semantically valid wire
    value. Keep the small calendar check explicit and reserve ``datetime`` for
    converting a real, known positive leap second to UTC below.
    """

    if not 1 <= month <= 12 or day < 1:
        return False
    days_by_month = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    maximum = days_by_month[month - 1]
    if month == 2 and year % 4 == 0 and (year % 100 != 0 or year % 400 == 0):
        maximum = 29
    return day <= maximum


def schema_directory() -> Path:
    """Return trusted bundled schemas, falling back to the source checkout.

    Hatch force-includes the single checked-in schema tree into a wheel under
    ``aippt_studio/portable_schemas``. During source-tree development that
    destination is intentionally absent, so the original integration source is
    used instead. Both paths fail closed when unavailable.
    """

    package_copy = Path(__file__).resolve().parent / "portable_schemas"
    source_copy = Path(__file__).resolve().parents[2] / "integrations" / "aippt-studio-powerpoint" / "references" / "schemas"
    for directory in (package_copy, source_copy):
        if directory.is_dir() and all((directory / filename).is_file() for filename in _SCHEMA_FILENAMES.values()):
            return directory.resolve()
    raise PortableContractError("Canonical portable schemas are unavailable in this Studio installation")


def _pointer(document: Any, fragment: str, *, location: str) -> Any:
    if not fragment:
        return document
    if not fragment.startswith("/"):
        raise PortableContractError(f"{location}: unsupported schema reference fragment {fragment!r}")
    current = document
    for segment in fragment[1:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            raise PortableContractError(f"{location}: missing schema reference fragment {fragment!r}")
    return current


def _assert_supported_schema(schema: Any, *, location: str = "$") -> None:
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise PortableContractError(f"{location}: schema node must be an object or boolean")
    unknown = set(schema) - _ANNOTATION_KEYWORDS - _VALIDATION_KEYWORDS
    if unknown:
        raise PortableContractError(f"{location}: unsupported portable schema keywords {sorted(unknown)}")
    if "type" in schema:
        declared = schema["type"]
        values = {declared} if isinstance(declared, str) else set(declared) if isinstance(declared, list) else set()
        if not values or not values <= _SUPPORTED_TYPES:
            raise PortableContractError(f"{location}: unsupported portable schema type")
    if "$defs" in schema:
        if not isinstance(schema["$defs"], dict):
            raise PortableContractError(f"{location}.$defs must be an object")
        for key, value in schema["$defs"].items():
            _assert_supported_schema(value, location=f"{location}.$defs.{key}")
    if "properties" in schema:
        if not isinstance(schema["properties"], dict):
            raise PortableContractError(f"{location}.properties must be an object")
        for key, value in schema["properties"].items():
            _assert_supported_schema(value, location=f"{location}.properties.{key}")
    if "items" in schema:
        _assert_supported_schema(schema["items"], location=f"{location}.items")
    for key in ("if", "then", "else", "not"):
        if key in schema:
            _assert_supported_schema(schema[key], location=f"{location}.{key}")
    if "allOf" in schema:
        if not isinstance(schema["allOf"], list):
            raise PortableContractError(f"{location}.allOf must be an array")
        for index, value in enumerate(schema["allOf"]):
            _assert_supported_schema(value, location=f"{location}.allOf[{index}]")


@lru_cache(maxsize=16)
def _load_schema(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PortableContractError(f"Cannot read canonical portable schema {path.name}") from error
    _assert_supported_schema(loaded, location=path.name)
    return loaded


def _resolve_reference(reference: str, *, document: dict[str, Any], document_path: Path, location: str) -> tuple[dict[str, Any], Path, Any]:
    filename, separator, fragment = reference.partition("#")
    if filename:
        root = schema_directory()
        target_path = (document_path.parent / filename).resolve()
        if not target_path.is_relative_to(root) or not target_path.is_file():
            raise PortableContractError(f"{location}: non-local or missing contract reference {reference!r}")
        target_document = _load_schema(target_path)
    else:
        target_path = document_path
        target_document = document
    return target_document, target_path, _pointer(target_document, fragment if separator else "", location=location)


def _is_valid(instance: Any, schema: Any, *, document: dict[str, Any], document_path: Path) -> bool:
    try:
        _validate(instance, schema, document=document, document_path=document_path, location="$")
    except PortableContractError:
        return False
    return True


def _type_matches(instance: Any, declared: str) -> bool:
    if declared == "object":
        return isinstance(instance, dict)
    if declared == "array":
        return isinstance(instance, list)
    if declared == "string":
        return isinstance(instance, str)
    if declared == "boolean":
        return isinstance(instance, bool)
    if declared == "null":
        return instance is None
    if declared == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if declared == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    return False


def _json_equal(left: Any, right: Any) -> bool:
    """Compare JSON values without Python's ``False == 0`` coercion.

    Canonical contracts are JSON contracts: booleans and numbers are different
    JSON types even though Python treats them as equal. Numbers deliberately
    retain JSON's mathematical equality (``1`` and ``1.0`` match), while
    arrays and objects compare their JSON structure recursively.
    """

    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    left_is_number = isinstance(left, (int, float)) and not isinstance(left, bool)
    right_is_number = isinstance(right, (int, float)) and not isinstance(right, bool)
    if left_is_number or right_is_number:
        return left_is_number and right_is_number and left == right
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str) and left == right
    if isinstance(left, list) or isinstance(right, list):
        return (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right)
            and all(_json_equal(item, other) for item, other in zip(left, right, strict=True))
        )
    if isinstance(left, dict) or isinstance(right, dict):
        if not isinstance(left, dict) or not isinstance(right, dict) or set(left) != set(right):
            return False
        return all(
            isinstance(key, str) and _json_equal(value, right[key])
            for key, value in left.items()
        )
    # Non-JSON values are not allowed to match a canonical JSON literal.
    return False


def is_rfc3339_datetime(value: str) -> bool:
    """Return whether ``value`` is the strict RFC 3339 date-time subset used here.

    ``datetime.fromisoformat`` accepts convenient Python extensions including
    a space/X separator, basic-format dates and second-resolution offsets.
    Portable records need one interoperable wire representation, so a strict
    lexical RFC 3339 gate runs before semantic calendar/timezone validation.
    """

    if _RFC3339_DATE_TIME_PATTERN.fullmatch(value) is None:
        return False
    year = int(value[:4])
    month = int(value[5:7])
    day = int(value[8:10])
    if not _is_proleptic_gregorian_date(year, month, day):
        return False
    normalized = value[:10] + "T" + value[11:]
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    leap_second = normalized[17:19] == "60"
    if leap_second:
        normalized = normalized[:17] + "59" + normalized[19:]
    if not leap_second:
        return True
    # There is no announced positive leap second in year 0000, and Python's
    # datetime cannot convert that proleptic year in any event.
    if year == 0:
        return False
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        return False
    utc_before_leap = parsed.astimezone(timezone.utc)
    return (
        utc_before_leap.date() in _UTC_POSITIVE_LEAP_SECOND_DATES
        and utc_before_leap.hour == 23
        and utc_before_leap.minute == 59
        and utc_before_leap.second == 59
    )


def _validate(instance: Any, schema: Any, *, document: dict[str, Any], document_path: Path, location: str) -> None:
    if isinstance(schema, bool):
        if not schema:
            raise PortableContractError(f"{location}: rejected by canonical schema")
        return
    if not isinstance(schema, dict):
        raise PortableContractError(f"{location}: schema node must be an object")
    if "$ref" in schema:
        reference = schema["$ref"]
        if not isinstance(reference, str):
            raise PortableContractError(f"{location}: $ref must be text")
        target_document, target_path, target = _resolve_reference(
            reference, document=document, document_path=document_path, location=location
        )
        _validate(instance, target, document=target_document, document_path=target_path, location=location)
        # The shipped contracts do not use validation siblings beside $ref. If a
        # future version does, support them rather than quietly ignoring them.
        schema = {key: value for key, value in schema.items() if key != "$ref"}
    if "const" in schema and not _json_equal(instance, schema["const"]):
        raise PortableContractError(f"{location}: expected const {schema['const']!r}")
    if "enum" in schema and not any(_json_equal(instance, candidate) for candidate in schema["enum"]):
        raise PortableContractError(f"{location}: value is outside enum")
    if "type" in schema:
        declared = schema["type"]
        names = [declared] if isinstance(declared, str) else declared
        if not isinstance(names, list) or not any(isinstance(name, str) and _type_matches(instance, name) for name in names):
            raise PortableContractError(f"{location}: type does not match canonical schema")
    if "allOf" in schema:
        for index, item in enumerate(schema["allOf"]):
            _validate(instance, item, document=document, document_path=document_path, location=f"{location}.allOf[{index}]")
    if "if" in schema:
        branch = "then" if _is_valid(instance, schema["if"], document=document, document_path=document_path) else "else"
        if branch in schema:
            _validate(instance, schema[branch], document=document, document_path=document_path, location=f"{location}.{branch}")
    if "not" in schema and _is_valid(instance, schema["not"], document=document, document_path=document_path):
        raise PortableContractError(f"{location}: unexpectedly satisfies forbidden schema")
    if isinstance(instance, dict):
        required = schema.get("required", [])
        if not isinstance(required, list):
            raise PortableContractError(f"{location}: required must be an array")
        missing = [key for key in required if key not in instance]
        if missing:
            raise PortableContractError(f"{location}: missing required fields {missing}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise PortableContractError(f"{location}: properties must be an object")
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(properties)
            if extra:
                raise PortableContractError(f"{location}: unknown fields {sorted(extra)}")
        dependencies = schema.get("dependentRequired", {})
        if not isinstance(dependencies, dict):
            raise PortableContractError(f"{location}: dependentRequired must be an object")
        for source, required_items in dependencies.items():
            if source in instance:
                if not isinstance(required_items, list):
                    raise PortableContractError(f"{location}.{source}: dependency list must be an array")
                absent = [item for item in required_items if item not in instance]
                if absent:
                    raise PortableContractError(f"{location}: {source} requires {absent}")
        for key, value in instance.items():
            if key in properties:
                _validate(value, properties[key], document=document, document_path=document_path, location=f"{location}.{key}")
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            raise PortableContractError(f"{location}: below minItems")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise PortableContractError(f"{location}: above maxItems")
        if schema.get("uniqueItems"):
            encoded = [json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for value in instance]
            if len(encoded) != len(set(encoded)):
                raise PortableContractError(f"{location}: duplicate array value")
        if "items" in schema:
            for index, value in enumerate(instance):
                _validate(value, schema["items"], document=document, document_path=document_path, location=f"{location}[{index}]")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            raise PortableContractError(f"{location}: below minLength")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise PortableContractError(f"{location}: above maxLength")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise PortableContractError(f"{location}: does not match pattern")
        if schema.get("format") == "date-time":
            if not is_rfc3339_datetime(instance):
                raise PortableContractError(f"{location}: invalid RFC 3339 date-time")


def validate_contract_instance(instance: Any, schema_name: str) -> None:
    """Validate an external value against one shipped portable schema.

    The validation is structural only. Callers still enforce cross-record
    identity, source/revision binding, persistence and trust boundaries.
    """

    filename = _SCHEMA_FILENAMES.get(schema_name)
    if filename is None:
        raise PortableContractError(f"Unsupported portable schema name {schema_name!r}")
    path = schema_directory() / filename
    document = _load_schema(path)
    _validate(instance, document, document=document, document_path=path, location="$")
