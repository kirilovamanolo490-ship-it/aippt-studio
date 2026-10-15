#!/usr/bin/env python3
"""Build a sanitized, deterministic ChatGPT for PowerPoint skill archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml
from yaml.constructor import ConstructorError
from yaml.resolver import BaseResolver


STUDIO_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_SOURCE = STUDIO_ROOT / "src"
if str(_RUNTIME_SOURCE) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_SOURCE))

from aippt_studio.portable_contracts import PortableContractError, validate_contract_instance
from aippt_studio.portable_packets import PortablePacketError, validate_handoff_bundle_v1
from aippt_studio.portable_safety import (
    PORTABLE_LOCAL_REFERENCE_PATTERNS,
    PORTABLE_SENSITIVE_VALUE_PATTERNS,
    PORTABLE_STUDIO_REFERENCE_PATTERNS,
    PortableTextDecodingError,
    contains_portable_forbidden_control_character,
    contains_portable_unpaired_surrogate,
    contains_portable_sensitive_value,
    has_portable_sensitive_setting_value,
    portable_path_scan_variants,
    portable_sensitive_scan_variants,
)


DEFAULT_SKILL_DIR = STUDIO_ROOT / "integrations" / "aippt-studio-powerpoint"
DEFAULT_OUTPUT = STUDIO_ROOT / "dist" / "aippt-studio-powerpoint-skill.zip"

EXPECTED_FILES = frozenset(
    {
        "SKILL.md",
        "agents/openai.yaml",
        "references/intake-routing.md",
        "references/expression-review.md",
        "references/evidence-and-learning.md",
        "references/platform-and-bridge.md",
        "references/visual-sourcing.md",
        "references/handoff-schemas.md",
        "references/release-contract.md",
        "references/release-manifest.json",
        "references/schemas/aippt-presentation-intent-v1.schema.json",
        "references/schemas/aippt-expression-review-v1.schema.json",
        "references/schemas/aippt-learning-packet-v1.schema.json",
        "references/schemas/aippt-bridge-capabilities-v1.schema.json",
        "references/schemas/aippt-guidance-snapshot-v1.schema.json",
        "references/schemas/aippt-handoff-bundle-v1.schema.json",
    }
)

SCHEMA_IDENTIFIERS = {
    "references/schemas/aippt-presentation-intent-v1.schema.json": "urn:aippt-studio:schema:aippt-presentation-intent:v1",
    "references/schemas/aippt-expression-review-v1.schema.json": "urn:aippt-studio:schema:aippt-expression-review:v1",
    "references/schemas/aippt-learning-packet-v1.schema.json": "urn:aippt-studio:schema:aippt-learning-packet:v1",
    "references/schemas/aippt-bridge-capabilities-v1.schema.json": "urn:aippt-studio:schema:aippt-bridge-capabilities:v1",
    "references/schemas/aippt-guidance-snapshot-v1.schema.json": "urn:aippt-studio:schema:aippt-guidance-snapshot:v1",
    "references/schemas/aippt-handoff-bundle-v1.schema.json": "urn:aippt-studio:schema:aippt-handoff-bundle:v1",
}
RELEASE_MANIFEST_PATH = "references/release-manifest.json"
HANDOFF_SCHEMAS_PATH = "references/handoff-schemas.md"
AGENT_MANIFEST_PATH = "agents/openai.yaml"
PORTABLE_CONTRACT_NAMES = frozenset(
    {
        "aippt-presentation-intent-v1",
        "aippt-bridge-capabilities-v1",
        "aippt-expression-review-v1",
        "aippt-learning-packet-v1",
        "aippt-guidance-snapshot-v1",
        "aippt-handoff-bundle-v1",
    }
)

PORTABLE_PATH_REFERENCE_PATTERNS = (
    *PORTABLE_LOCAL_REFERENCE_PATTERNS,
    *PORTABLE_STUDIO_REFERENCE_PATTERNS,
)
PORTABLE_SENSITIVE_REFERENCE_PATTERNS = (
    # Shared high-confidence credential/contact detectors keep the upload
    # release gate aligned with the local external-handoff receiver.
    *PORTABLE_SENSITIVE_VALUE_PATTERNS,
)
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\((?P<target>[^)]+)\)")


class PortableSkillError(ValueError):
    """The portable skill violates an upload boundary."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that refuses duplicate keys before scanning values."""


def _construct_unique_yaml_mapping(
    loader: yaml.SafeLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[object, object]:
    mapping: dict[object, object] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            if key in mapping:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from error
    return mapping


_UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_yaml_mapping
)


@dataclass(frozen=True)
class PackagedFile:
    path: str
    sha256: str
    bytes: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _relative_files(skill_dir: Path) -> list[Path]:
    if not skill_dir.is_dir():
        raise PortableSkillError(f"Skill directory does not exist: {skill_dir}")

    relative_paths: list[Path] = []
    for candidate in skill_dir.rglob("*"):
        if candidate.is_symlink():
            raise PortableSkillError(f"Symlinks are not allowed in an upload skill: {candidate}")
        if candidate.is_file():
            relative_paths.append(candidate.relative_to(skill_dir))

    return sorted(relative_paths, key=lambda path: path.as_posix())


def _read_portable_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise PortableSkillError(f"Non-text file is not allowed in portable skill: {path}") from exc
    if contains_portable_forbidden_control_character(text) or contains_portable_unpaired_surrogate(text):
        raise PortableSkillError(f"{path.name} contains a forbidden text character")
    return text


def _reject_forbidden_portable_text(text: str, *, source: str) -> None:
    """Apply the shared upload boundary to raw or decoded structured text.

    Raw Markdown/YAML text can hide a value through HTML/percent encodings;
    JSON and YAML scalars can also hide it through their own escape sequences.
    Keep the same detector list after each relevant decode so the package gate
    has one data boundary rather than representation-specific loopholes.
    """

    if contains_portable_forbidden_control_character(text) or contains_portable_unpaired_surrogate(text):
        raise PortableSkillError(f"{source} contains a forbidden text character")
    try:
        path_variants = portable_path_scan_variants(text)
        sensitive_variants = portable_sensitive_scan_variants(text)
    except PortableTextDecodingError as error:
        raise PortableSkillError(f"{source} has excessively nested encodings") from error

    # Pattern priority is deliberate: the deepest decoded spelling can turn
    # an invalid percent expression into a generic rooted path, while the raw
    # form still identifies a more useful Windows/POSIX environment reference.
    # Scan every representation for the more specific pattern before falling
    # through to the next one.
    if any(
        contains_portable_forbidden_control_character(candidate)
        or contains_portable_unpaired_surrogate(candidate)
        for candidate in (*path_variants, *sensitive_variants)
    ):
        raise PortableSkillError(f"{source} contains a forbidden text character")
    for pattern, label in PORTABLE_PATH_REFERENCE_PATTERNS:
        for candidate in path_variants:
            if pattern.search(candidate):
                raise PortableSkillError(f"{source} contains forbidden {label}")
    for candidate in sensitive_variants:
        for pattern, label in PORTABLE_SENSITIVE_REFERENCE_PATTERNS:
            if pattern.search(candidate):
                raise PortableSkillError(f"{source} contains forbidden {label}")
    # Some credential carriers are intentionally recognized as a relationship
    # across YAML-looking lines (an anchored scalar referenced by ``token:
    # *alias``). The shared semantic helper keeps that free-text case aligned
    # with runtime packet validation as well as decoded YAML mapping checks.
    if contains_portable_sensitive_value(text):
        raise PortableSkillError(f"{source} contains forbidden credential value")


def _scan_decoded_structured_value(value: object, *, source: str) -> None:
    """Reject forbidden values and keys after JSON or YAML decoding."""

    # Package JSON is intentionally small and static.  This cap prevents a
    # malicious candidate file from turning validation into unbounded work,
    # while remaining far above the checked-in schemas' actual node count.
    maximum_nodes = 100_000
    scanned_nodes = 0
    stack: list[tuple[str, object]] = [(source, value)]
    visited_containers: set[int] = set()
    while stack:
        field, current = stack.pop()
        scanned_nodes += 1
        if scanned_nodes > maximum_nodes:
            raise PortableSkillError(f"{source} contains too many decoded structured values")
        if isinstance(current, str):
            _reject_forbidden_portable_text(current, source=field)
        elif current is None or isinstance(current, (bool, int, float)):
            continue
        elif isinstance(current, list):
            container_id = id(current)
            if container_id in visited_containers:
                continue
            visited_containers.add(container_id)
            for index, item in enumerate(current):
                stack.append((f"{field}[{index}]", item))
        elif isinstance(current, dict):
            container_id = id(current)
            if container_id in visited_containers:
                continue
            visited_containers.add(container_id)
            for key, item in current.items():
                if not isinstance(key, str):
                    raise PortableSkillError(f"{field} has a non-text JSON key")
                _reject_forbidden_portable_text(key, source=f"{field} key")
                # ``safe_load`` resolves YAML aliases, anchors, tags and block
                # scalars before this point.  Inspect the key/value relation
                # after decoding so ``token: &anchor x`` cannot evade the raw
                # text scan by separating its sensitive key from the value.
                if has_portable_sensitive_setting_value(key, item):
                    raise PortableSkillError(f"{field}.{key} contains forbidden credential value")
                stack.append((f"{field}.{key}", item))
        else:
            raise PortableSkillError(f"{field} has an unsupported decoded structured value")


def _scan_decoded_json_value(value: object, *, source: str) -> None:
    """JSON-named wrapper retained for contract-example call sites."""

    _scan_decoded_structured_value(value, source=source)


def _read_json_document(path: Path) -> object:
    try:
        return json.loads(
            _read_portable_text(path), object_pairs_hook=_reject_duplicate_json_pairs
        )
    except (json.JSONDecodeError, PortableSkillError) as exc:
        raise PortableSkillError(f"{path.name} is not valid JSON") from exc


def _read_yaml_document(path: Path) -> object:
    try:
        return yaml.load(_read_portable_text(path), Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise PortableSkillError(f"{path.name} is not valid YAML") from exc


def _read_yaml_text(text: str, *, source: str) -> object:
    try:
        return yaml.load(text, Loader=_UniqueKeySafeLoader)
    except yaml.YAMLError as exc:
        raise PortableSkillError(f"{source} is not valid YAML") from exc


def _validate_agent_manifest(skill_dir: Path) -> None:
    """Require the shipped ChatGPT skill manifest to be complete and closed.

    The ZIP has a fixed file inventory, but merely having an ``openai.yaml``
    file does not make a package invokable or displayable. Keep the portable
    release manifest intentionally small: current inventory has no icon assets,
    so only the interface fields and explicit invocation policy that this skill
    actually ships are accepted.
    """

    manifest = _read_yaml_document(skill_dir / AGENT_MANIFEST_PATH)
    if not isinstance(manifest, dict) or set(manifest) != {"interface", "policy"}:
        raise PortableSkillError("agents/openai.yaml must be a closed agent manifest")
    interface = manifest["interface"]
    policy = manifest["policy"]
    if not isinstance(interface, dict) or set(interface) != {
        "display_name", "short_description", "default_prompt"
    }:
        raise PortableSkillError("agents/openai.yaml interface is incomplete or unsupported")
    if not isinstance(policy, dict) or set(policy) != {"allow_implicit_invocation"}:
        raise PortableSkillError("agents/openai.yaml policy is incomplete or unsupported")
    for key, limit in (
        ("display_name", 128),
        ("short_description", 1024),
        ("default_prompt", 2048),
    ):
        value = interface[key]
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise PortableSkillError(f"agents/openai.yaml interface.{key} is missing or invalid")
    if not isinstance(policy["allow_implicit_invocation"], bool):
        raise PortableSkillError("agents/openai.yaml policy.allow_implicit_invocation must be boolean")


def _validate_frontmatter(skill_dir: Path) -> None:
    skill_md = skill_dir / "SKILL.md"
    content = _read_portable_text(skill_md)
    match = re.match(r"^---\n(?P<header>.*?)\n---\n", content, re.DOTALL)
    if not match:
        raise PortableSkillError("SKILL.md must start with YAML frontmatter")

    header = match.group("header")
    frontmatter = _read_yaml_text(header, source="SKILL.md frontmatter")
    _scan_decoded_structured_value(frontmatter, source="SKILL.md frontmatter decoded YAML")
    if not isinstance(frontmatter, dict):
        raise PortableSkillError("SKILL.md frontmatter must be a YAML object")
    name = frontmatter.get("name")
    description = frontmatter.get("description")
    if not isinstance(name, str) or not isinstance(description, str):
        raise PortableSkillError("SKILL.md frontmatter needs name and description")

    name = name.strip()
    description = description.strip()
    if name != skill_dir.name:
        raise PortableSkillError(
            f"SKILL.md name {name!r} must match skill directory {skill_dir.name!r}"
        )
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
        raise PortableSkillError("Skill name must be hyphen-case and no longer than 64 characters")
    if not description or len(description) > 1024 or "<" in description or ">" in description:
        raise PortableSkillError("Skill description is missing or invalid")
    if "[TODO:" in content:
        raise PortableSkillError("Portable skill contains an unfinished TODO marker")


def _validate_local_markdown_links(skill_dir: Path, relative_paths: Iterable[Path]) -> None:
    for relative_path in relative_paths:
        if relative_path.suffix.lower() != ".md":
            continue
        source_path = skill_dir / relative_path
        for match in MARKDOWN_LINK_PATTERN.finditer(_read_portable_text(source_path)):
            raw_target = match.group("target").strip()
            target_path = raw_target.split("#", 1)[0]
            if not target_path or "://" in target_path or target_path.startswith("mailto:"):
                continue
            resolved = (source_path.parent / target_path).resolve()
            if not resolved.is_relative_to(skill_dir) or not resolved.is_file():
                raise PortableSkillError(
                    f"{relative_path.as_posix()} links to missing local reference {raw_target!r}"
                )


def _read_json_contract(path: Path) -> dict[str, object]:
    value = _read_json_document(path)
    if not isinstance(value, dict):
        raise PortableSkillError(f"{path.name} must contain a JSON object")
    return value


def _reject_duplicate_json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise PortableSkillError(f"Duplicate JSON key in fenced contract example: {key}")
        value[key] = item
    return value


def _resolve_json_pointer(value: object, pointer: str, *, label: str) -> object:
    if not pointer:
        return value
    if not pointer.startswith("/"):
        raise PortableSkillError(f"{label} has an invalid JSON Schema reference fragment")
    current = value
    for segment in pointer[1:].split("/"):
        token = segment.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise PortableSkillError(f"{label} points to a missing JSON Schema fragment")
    return current


def _walk_schema_references(value: object) -> Iterable[str]:
    if isinstance(value, dict):
        reference = value.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference:
                raise PortableSkillError("JSON Schema $ref must be a non-empty string")
            yield reference
        for item in value.values():
            yield from _walk_schema_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_schema_references(item)


def _validate_schema_reference(
    skill_dir: Path, *, relative: str, schema: dict[str, object], reference: str
) -> None:
    target, separator, fragment = reference.partition("#")
    if target:
        if "://" in target or target.startswith("/"):
            raise PortableSkillError(f"{relative} has a non-portable JSON Schema reference")
        resolved = (skill_dir / "references" / "schemas" / target).resolve()
        schema_root = (skill_dir / "references" / "schemas").resolve()
        if not resolved.is_relative_to(schema_root) or not resolved.is_file():
            raise PortableSkillError(f"{relative} points to a missing JSON Schema file {target!r}")
        target_schema = _read_json_contract(resolved)
    else:
        target_schema = schema
    _resolve_json_pointer(target_schema, fragment if separator else "", label=f"{relative} $ref {reference!r}")


def _validate_schema_contracts(skill_dir: Path) -> None:
    for relative, identifier in SCHEMA_IDENTIFIERS.items():
        candidate_path = skill_dir / relative
        trusted_path = DEFAULT_SKILL_DIR / relative
        try:
            candidate_bytes = candidate_path.read_bytes()
            trusted_bytes = trusted_path.read_bytes()
        except OSError as error:
            raise PortableSkillError(f"{relative} cannot be compared to the local canonical schema") from error
        # The receiver always validates with its checked-in/bundled schemas.
        # Do not let a copied upload directory silently publish a relaxed or
        # divergent contract under the same schema ID.
        if candidate_bytes != trusted_bytes:
            raise PortableSkillError(f"{relative} does not match the local canonical schema")
        schema = _read_json_contract(candidate_path)
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise PortableSkillError(f"{relative} must declare JSON Schema draft 2020-12")
        if schema.get("$id") != identifier:
            raise PortableSkillError(f"{relative} has an unexpected schema identifier")
        if schema.get("type") != "object" or schema.get("additionalProperties") is not False:
            raise PortableSkillError(f"{relative} must be a closed JSON object contract")
        required = schema.get("required")
        properties = schema.get("properties")
        if not isinstance(required, list) or not required or not isinstance(properties, dict):
            raise PortableSkillError(f"{relative} must declare required object properties")
        if any(not isinstance(field, str) or field not in properties for field in required):
            raise PortableSkillError(f"{relative} has invalid required properties")
        for reference in _walk_schema_references(schema):
            _validate_schema_reference(skill_dir, relative=relative, schema=schema, reference=reference)


def _validate_release_manifest(skill_dir: Path) -> None:
    manifest = _read_json_contract(skill_dir / RELEASE_MANIFEST_PATH)
    expected = {
        "schema",
        "package_name",
        "release_version",
        "supported_surfaces",
        "entrypoint",
        "contracts",
        "data_boundary",
        "learning_delivery",
        "capability_contract",
    }
    if set(manifest) != expected:
        raise PortableSkillError("release manifest has unsupported or missing fields")
    if manifest["schema"] != "aippt-portable-skill-release-manifest-v1":
        raise PortableSkillError("release manifest has an unsupported schema")
    if manifest["package_name"] != skill_dir.name or manifest["entrypoint"] != "SKILL.md":
        raise PortableSkillError("release manifest does not identify this skill package")
    if not isinstance(manifest["release_version"], str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", manifest["release_version"]):
        raise PortableSkillError("release manifest version must be semantic-version shaped")
    if manifest["supported_surfaces"] != ["chatgpt-powerpoint"]:
        raise PortableSkillError("release manifest must target chatgpt-powerpoint only")
    expected_contracts = {
        "aippt-presentation-intent-v1",
        "aippt-expression-review-v1",
        "aippt-learning-packet-v1",
        "aippt-bridge-capabilities-v1",
        "aippt-guidance-snapshot-v1",
        "aippt-handoff-bundle-v1",
    }
    contracts = manifest["contracts"]
    if (
        not isinstance(contracts, list)
        or any(not isinstance(contract, str) for contract in contracts)
        or len(contracts) != len(expected_contracts)
        or set(contracts) != expected_contracts
    ):
        raise PortableSkillError("release manifest contract list is incomplete")
    boundary = manifest["data_boundary"]
    if not isinstance(boundary, dict) or set(boundary) != {
        "includes_local_paths",
        "includes_runtime_or_credentials",
        "includes_client_material",
        "automatic_attachment_transfer",
        "automatic_core_mutation",
    } or any(value is not False for value in boundary.values()):
        raise PortableSkillError("release manifest must retain the portable data boundary")
    if manifest["capability_contract"] != "aippt-bridge-capabilities-v1":
        raise PortableSkillError("release manifest capability contract is invalid")
    if not isinstance(manifest["learning_delivery"], str) or not manifest["learning_delivery"]:
        raise PortableSkillError("release manifest learning delivery statement is missing")


def _fenced_contract_examples(skill_dir: Path) -> dict[str, dict[str, object]]:
    """Read the executable source-bundle examples shipped to Office users.

    A package whose narrative example cannot pass the exact local receiver
    contract is worse than no example: it invites users to create a handoff the
    workbench must reject.  The package therefore carries one complete,
    self-contained example for every v1 contract, including the bundle itself.
    """

    source = _read_portable_text(skill_dir / HANDOFF_SCHEMAS_PATH)
    blocks = re.findall(r"^~~~json\s*$\n(?P<payload>.*?)\n^~~~\s*$", source, re.MULTILINE | re.DOTALL)
    if not blocks:
        raise PortableSkillError("handoff-schemas.md must contain fenced JSON contract examples")
    examples: dict[str, dict[str, object]] = {}
    for index, raw in enumerate(blocks, start=1):
        try:
            item = json.loads(raw, object_pairs_hook=_reject_duplicate_json_pairs)
        except (json.JSONDecodeError, PortableSkillError) as error:
            raise PortableSkillError(f"handoff-schemas.md JSON example {index} is invalid") from error
        _scan_decoded_json_value(
            item, source=f"handoff-schemas.md JSON example {index} decoded JSON"
        )
        if not isinstance(item, dict) or not isinstance(item.get("schema"), str):
            raise PortableSkillError(f"handoff-schemas.md JSON example {index} must be a schema-tagged object")
        name = item["schema"]
        if name in examples:
            raise PortableSkillError(f"handoff-schemas.md duplicates contract example {name!r}")
        examples[name] = item
    if set(examples) != PORTABLE_CONTRACT_NAMES:
        missing = sorted(PORTABLE_CONTRACT_NAMES - set(examples))
        unexpected = sorted(set(examples) - PORTABLE_CONTRACT_NAMES)
        raise PortableSkillError(
            "handoff-schemas.md must include each portable v1 contract exactly once"
            + (f"; missing: {', '.join(missing)}" if missing else "")
            + (f"; unexpected: {', '.join(unexpected)}" if unexpected else "")
        )
    return examples


def _validate_handoff_contract_examples(skill_dir: Path) -> None:
    """Reject a release whose public examples drift from the local receiver."""

    examples = _fenced_contract_examples(skill_dir)
    for schema_name, instance in examples.items():
        try:
            validate_contract_instance(instance, schema_name)
        except PortableContractError as error:
            raise PortableSkillError(
                f"handoff-schemas.md {schema_name} example violates the canonical contract: {error}"
            ) from error
    try:
        validate_handoff_bundle_v1(
            json.dumps(examples["aippt-handoff-bundle-v1"], ensure_ascii=False).encode("utf-8")
        )
    except PortablePacketError as error:
        raise PortableSkillError(
            f"handoff-schemas.md handoff bundle example fails local semantic validation: {error}"
        ) from error


def _validate_decoded_json_documents(skill_dir: Path, relative_paths: Iterable[Path]) -> None:
    """Scan every shipped JSON document after unescaping its string values."""

    for relative_path in relative_paths:
        if relative_path.suffix.lower() != ".json":
            continue
        _scan_decoded_json_value(
            _read_json_document(skill_dir / relative_path),
            source=f"{relative_path.as_posix()} decoded JSON",
        )


def _validate_decoded_yaml_documents(skill_dir: Path, relative_paths: Iterable[Path]) -> None:
    """Scan every shipped YAML document after escape sequences are decoded."""

    for relative_path in relative_paths:
        if relative_path.suffix.lower() not in {".yaml", ".yml"}:
            continue
        _scan_decoded_structured_value(
            _read_yaml_document(skill_dir / relative_path),
            source=f"{relative_path.as_posix()} decoded YAML",
        )


def validate_skill_source(skill_dir: Path) -> list[PackagedFile]:
    """Validate the portable source and return its stable file inventory."""

    skill_dir = skill_dir.resolve()
    _validate_frontmatter(skill_dir)

    relative_paths = _relative_files(skill_dir)
    observed_paths = {path.as_posix() for path in relative_paths}
    missing = sorted(EXPECTED_FILES - observed_paths)
    unexpected = sorted(observed_paths - EXPECTED_FILES)
    if missing or unexpected:
        problems: list[str] = []
        if missing:
            problems.append(f"missing required files: {', '.join(missing)}")
        if unexpected:
            problems.append(f"unexpected files: {', '.join(unexpected)}")
        raise PortableSkillError("; ".join(problems))
    _validate_local_markdown_links(skill_dir, relative_paths)
    _validate_decoded_json_documents(skill_dir, relative_paths)
    _validate_decoded_yaml_documents(skill_dir, relative_paths)
    _validate_agent_manifest(skill_dir)
    _validate_schema_contracts(skill_dir)
    _validate_release_manifest(skill_dir)
    _validate_handoff_contract_examples(skill_dir)

    inventory: list[PackagedFile] = []
    for relative_path in relative_paths:
        absolute_path = skill_dir / relative_path
        text = _read_portable_text(absolute_path)
        _reject_forbidden_portable_text(text, source=relative_path.as_posix())
        raw = text.encode("utf-8")
        inventory.append(
            PackagedFile(
                path=relative_path.as_posix(),
                sha256=_sha256(raw),
                bytes=len(raw),
            )
        )
    return inventory


def _materialize_verified_snapshot(skill_dir: Path, snapshot_root: Path) -> Path:
    """Copy one byte snapshot, then validate that snapshot before packaging.

    Validation and ZIP writing must use the same bytes.  Reading a source tree
    twice would permit a concurrently replaced document or schema to pass the
    first validation yet enter the archive on the second read.  The temporary
    snapshot is private to this build and is revalidated as a complete skill.
    """

    skill_dir = skill_dir.resolve()
    initial_inventory = validate_skill_source(skill_dir)
    snapshot = snapshot_root / skill_dir.name
    for entry in initial_inventory:
        source = skill_dir / entry.path
        try:
            payload = source.read_bytes()
        except OSError as error:
            raise PortableSkillError(f"Cannot capture verified package file {entry.path}") from error
        if len(payload) != entry.bytes or _sha256(payload) != entry.sha256:
            raise PortableSkillError(f"Package source changed while creating a verified snapshot: {entry.path}")
        destination = snapshot / entry.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
    # Run all structural, schema, documentation and content-boundary checks
    # against the immutable copy that will be written to the ZIP.
    validate_skill_source(snapshot)
    return snapshot


def _write_deterministic_zip(
    skill_dir: Path, output_path: Path, inventory: Iterable[PackagedFile]
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with zipfile.ZipFile(
            temporary_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for entry in inventory:
                payload = (skill_dir / entry.path).read_bytes()
                zip_entry = zipfile.ZipInfo(
                    f"{skill_dir.name}/{entry.path}",
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                zip_entry.compress_type = zipfile.ZIP_DEFLATED
                zip_entry.external_attr = 0o100644 << 16
                archive.writestr(zip_entry, payload)

        with zipfile.ZipFile(temporary_path) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise PortableSkillError(f"Archive integrity failure: {corrupt_member}")
            expected_names = [f"{skill_dir.name}/{entry.path}" for entry in inventory]
            if archive.namelist() != expected_names:
                raise PortableSkillError("Archive member list differs from the verified inventory")
            for entry in inventory:
                payload = archive.read(f"{skill_dir.name}/{entry.path}")
                if len(payload) != entry.bytes or _sha256(payload) != entry.sha256:
                    raise PortableSkillError(f"Archive member differs from verified source: {entry.path}")
        temporary_path.replace(output_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def build_release(skill_dir: Path, output_path: Path) -> dict[str, object]:
    skill_dir = skill_dir.resolve()
    output_path = output_path.resolve()
    with tempfile.TemporaryDirectory(prefix="aippt-portable-skill-") as temporary:
        snapshot = _materialize_verified_snapshot(skill_dir, Path(temporary))
        inventory = validate_skill_source(snapshot)
        _write_deterministic_zip(snapshot, output_path, inventory)

    archive_bytes = output_path.read_bytes()
    manifest = {
        "schema": "aippt-portable-skill-release-v1",
        "skill_name": skill_dir.name,
        "archive": output_path.name,
        "archive_sha256": _sha256(archive_bytes),
        "archive_bytes": len(archive_bytes),
        "files": [
            {"path": entry.path, "sha256": entry.sha256, "bytes": entry.bytes}
            for entry in inventory
        ],
    }
    manifest_path = output_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"manifest": manifest, "manifest_path": manifest_path}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate and package the portable AI PPT Studio PowerPoint skill."
    )
    parser.add_argument(
        "--skill-dir",
        type=Path,
        default=DEFAULT_SKILL_DIR,
        help="Portable skill source directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination ZIP path.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate source only; do not create a ZIP.",
    )
    args = parser.parse_args()

    try:
        if args.check:
            inventory = validate_skill_source(args.skill_dir)
            print(
                json.dumps(
                    {
                        "schema": "aippt-portable-skill-check-v1",
                        "skill_name": args.skill_dir.name,
                        "files": [
                            {"path": entry.path, "sha256": entry.sha256, "bytes": entry.bytes}
                            for entry in inventory
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        release = build_release(args.skill_dir, args.output)
        print(
            json.dumps(
                {
                    "archive": str(args.output.resolve()),
                    "manifest": str(release["manifest_path"]),
                    "archive_sha256": release["manifest"]["archive_sha256"],
                    "archive_bytes": release["manifest"]["archive_bytes"],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    except PortableSkillError as exc:
        print(f"Portable skill validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
