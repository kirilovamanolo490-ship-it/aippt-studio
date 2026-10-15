from __future__ import annotations

import sys

from datetime import datetime, timezone
from enum import StrEnum
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .notes import sanitize_speaker_notes_text


MIN_TARGET_PAGES = 1
MAX_TARGET_PAGES = 200
DEFAULT_TARGET_PAGES = 10


class PageCountError(ValueError):
    """The current request needs corrected page requirements or more planning."""

    def __init__(self, message: str, *, needs_material: bool = False):
        super().__init__(message)
        self.needs_material = needs_material


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobState(StrEnum):
    INTAKE = "intake"
    BRIEF_REVIEW = "brief_review"
    PROTOTYPE_REVIEW = "prototype_review"
    FULL_BUILD = "full_build"
    FINAL_REVIEW = "final_review"
    DELIVERED = "delivered"
    BLOCKED = "blocked"
    FAILED = "failed"


class ProductionRoute(StrEnum):
    NATIVE = "native"
    TEMPLATE = "template"
    REBUILD = "rebuild"
    VISUAL = "visual"


class ChangeMode(StrEnum):
    """What kind of presentation work the request actually asks for.

    This is deliberately separate from :class:`ProductionRoute`.  A government
    briefing can start as a new composition and later receive a local patch;
    its technical renderer may change without losing the editorial contract.
    """

    UNCLASSIFIED = "unclassified"
    LOCAL_PATCH = "local_patch"
    TEMPLATE_RELAYOUT = "template_relayout"
    NEW_COMPOSITION = "new_composition"
    FAITHFUL_RECONSTRUCTION = "faithful_reconstruction"
    VISUAL_ALTERNATE = "visual_alternate"


class VisualSystemMode(StrEnum):
    """How the request relates to an existing visual system or reference."""

    PRESERVE_EXISTING = "preserve_existing"
    DERIVE_FROM_REFERENCE = "derive_from_reference"
    CREATE_NEW = "create_new"


class PresentationForm(StrEnum):
    """Editorial occasion, never a fixed colour palette or universal template."""

    GENERAL = "general"
    GOVERNMENT_BRIEFING = "government_briefing"
    BUSINESS_REPORT = "business_report"
    COMPETITION_DEFENSE = "competition_defense"
    ACADEMIC_DEFENSE = "academic_defense"
    TEACHING = "teaching"
    TECHNICAL_EXPLANATION = "technical_explanation"
    CEREMONIAL_STORY = "ceremonial_story"
    PREMIUM_BRAND_PORTFOLIO = "premium_brand_portfolio"


class PreservationContract(BaseModel):
    """Explicitly records what a scoped revision may and may not disturb."""

    target_slide_numbers: list[int] = Field(default_factory=list)
    preserve_non_target_slides: bool = False
    preserve_master_theme: bool = False
    preserve_brand_objects: bool = False
    preserve_media: bool = False
    insert_position: Literal[
        "before_first_slide", "after_last_slide", "replace_target_slides", "unspecified"
    ] = "unspecified"
    page_delta: int | None = None
    allow_auto_cover: bool = True
    allow_auto_outline: bool = True
    allow_auto_conclusion: bool = True

    @field_validator("target_slide_numbers", mode="before")
    @classmethod
    def validate_target_slide_number_types(cls, value: object) -> object:
        # Pydantic otherwise coerces ``True`` to ``1`` before the range
        # validator runs.  A boolean is never a valid page identifier, and
        # accepting it would make a preservation contract ambiguous.
        if not isinstance(value, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in value):
            raise ValueError("target_slide_numbers must contain integer page numbers")
        return value

    @field_validator("target_slide_numbers")
    @classmethod
    def validate_target_slide_numbers(cls, value: list[int]) -> list[int]:
        if any(item < 1 for item in value):
            raise ValueError("target_slide_numbers must contain positive page numbers")
        if len(value) != len(set(value)):
            raise ValueError("target_slide_numbers must not repeat a page number")
        return sorted(value)


class PresentationIntent(BaseModel):
    """A SHA-bound intake decision that is reviewed with the brief.

    The intent captures the *meaning* of a request before a renderer is
    selected.  Its principles guide planning and page expression, but it does
    not prescribe a colour, font size, or one visual template for every form.
    """

    schema_version: int = 1
    change_mode: ChangeMode = ChangeMode.UNCLASSIFIED
    visual_system: VisualSystemMode = VisualSystemMode.DERIVE_FROM_REFERENCE
    presentation_form: PresentationForm = PresentationForm.GENERAL
    preservation: PreservationContract = Field(default_factory=PreservationContract)
    source: Literal["automatic", "user_override", "legacy"] = "legacy"
    change_mode_source: Literal["automatic", "user_override", "legacy"] = "legacy"
    presentation_form_source: Literal["automatic", "user_override", "legacy"] = "legacy"
    visual_system_source: Literal["automatic", "user_override", "legacy"] = "legacy"
    confidence: Literal["explicit", "inferred", "unresolved", "legacy"] = "legacy"
    reason: str = "No structured intake request is bound to this legacy job."
    evidence: list[str] = Field(default_factory=list)
    request_sha256: str | None = None
    decided_at: str = Field(default_factory=utc_now)

    @field_validator("request_sha256")
    @classmethod
    def validate_request_sha256(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("request_sha256 must contain 64 hexadecimal characters")
        return value.lower() if value is not None else None

    @property
    def explainable(self) -> bool:
        return bool(self.reason.strip()) and bool(self.evidence or self.source == "legacy")


class WorkRequestBinding(BaseModel):
    """Provenance for a Work request kept outside content and evidence sources."""

    work_id: str
    source_relative_path: str = "input/request.md"
    source_sha256: str
    snapshot_path: str
    snapshot_sha256: str
    bound_at: str = Field(default_factory=utc_now)

    @field_validator("source_sha256", "snapshot_sha256")
    @classmethod
    def validate_binding_sha256(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("Work request hashes must contain 64 hexadecimal characters")
        return value.lower()


class InputRole(StrEnum):
    CONTENT = "content"
    TEMPLATE = "template"
    EDIT_TARGET = "edit_target"
    VISUAL_REFERENCE = "visual_reference"
    FLATTENED_INPUT = "flattened_input"
    ASSET = "asset"


class RouteDecision(BaseModel):
    route: ProductionRoute
    source: Literal["automatic", "user_override", "legacy"] = "automatic"
    reason: str
    input_roles: dict[str, InputRole] = Field(default_factory=dict)
    decided_at: str = Field(default_factory=utc_now)

    @property
    def explainable(self) -> bool:
        return bool(self.reason.strip())


class ExternalEngineManifest(BaseModel):
    job_id: str
    route: ProductionRoute
    engine: str
    version: str
    editable_class: Literal["native_editable", "reconstructed_editable", "image_based"]
    deck_plan_sha256: str
    input_hashes: dict[str, str] = Field(default_factory=dict)
    pptx_sha256: str
    preview_hashes: dict[str, str]
    build_source: str | None = None
    build_source_sha256: str | None = None
    providers: list[dict[str, str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    adapter_receipt: dict[str, Any] = Field(default_factory=dict)
    template_application: dict[str, Any] = Field(default_factory=dict)

    @field_validator("deck_plan_sha256", "pptx_sha256", "build_source_sha256")
    @classmethod
    def validate_optional_hash(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("manifest SHA-256 values must contain 64 hexadecimal characters")
        return value.lower() if value is not None else None

    @field_validator("input_hashes", "preview_hashes")
    @classmethod
    def validate_hash_map(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not re.fullmatch(r"[0-9a-fA-F]{64}", item) for item in value.values()):
            raise ValueError("manifest SHA-256 maps must contain 64-character hexadecimal values")
        return {key: item.lower() for key, item in value.items()}

    @model_validator(mode="after")
    def reject_credentials(self) -> "ExternalEngineManifest":
        serialized = str((self.providers, self.warnings, self.adapter_receipt, self.template_application))
        if re.search(r"\bsk-[A-Za-z0-9_-]{8,}", serialized) or re.search(
            r"(?i)\b(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*\S+",
            serialized,
        ):
            raise ValueError("external engine manifest contains credential material")
        return self


class SourceRecord(BaseModel):
    id: str
    original: str
    stored_path: str
    normalized_path: str | None = None
    kind: str
    sha256: str
    size_bytes: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceDeckElement(BaseModel):
    path: str
    type: str
    id: int | None = None
    name: str | None = None
    text: str = ""
    preview: str = ""
    asset_id: str | None = None
    format: dict[str, Any] = Field(default_factory=dict)


class SourceAssetReference(BaseModel):
    slide_id: str
    element_path: str


class SourceAsset(BaseModel):
    id: str
    source_id: str
    kind: Literal["image", "video", "audio", "unknown"] = "unknown"
    package_path: str | None = None
    content_type: str | None = None
    rel_id: str | None = None
    file_size: int | None = None
    stored_path: str | None = None
    sha256: str | None = None
    extraction_status: Literal["extracted", "not_extracted", "failed"] = "not_extracted"
    references: list[SourceAssetReference] = Field(default_factory=list)


class SourceSlideManifest(BaseModel):
    id: str
    index: int
    name: str = ""
    layout: str | None = None
    screenshot_path: str | None = None
    elements: list[SourceDeckElement] = Field(default_factory=list)
    animation_paths: list[str] = Field(default_factory=list)
    transition: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


class SourceDeckManifest(BaseModel):
    schema_version: int = 1
    source_id: str
    original: str
    stored_path: str
    source_sha256: str
    size_bytes: int
    slide_count: int
    slide_width: str | None = None
    slide_height: str | None = None
    slide_size: str | None = None
    default_font: str | None = None
    engine: str
    engine_version: str
    slides: list[SourceSlideManifest] = Field(default_factory=list)
    assets: list[SourceAsset] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class SourceInspectionIndex(BaseModel):
    schema_version: int = 1
    job_id: str
    manifests: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now)


class PptxEditOperation(BaseModel):
    id: str
    action: Literal["set", "add", "remove"]
    target: str
    element: str | None = None
    properties: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("target")
    @classmethod
    def validate_target(cls, value: str) -> str:
        if not value.startswith("/") or any(token in value for token in ("\n", "\r", "..")):
            raise ValueError("PPTX edit target must be an absolute OfficeCLI path")
        return value

    @model_validator(mode="after")
    def validate_action(self) -> "PptxEditOperation":
        if self.action == "add" and not self.element:
            raise ValueError("add operations require an element type")
        if self.action == "remove" and self.properties:
            raise ValueError("remove operations do not accept properties")
        return self


class PptxEditBatch(BaseModel):
    schema_version: int = 1
    id: str
    job_id: str
    input_path: str
    output_path: str
    input_sha256: str
    output_sha256: str | None = None
    operations: list[PptxEditOperation]
    results: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    applied_at: str | None = None


class LockedFact(BaseModel):
    text: str
    source_id: str
    locator: str | None = None


class EvidenceSource(BaseModel):
    source_id: str
    sha256: str
    kind: str
    original: str
    normalized_path: str | None = None
    license: str | None = None


class EvidenceLedger(BaseModel):
    sources: list[EvidenceSource] = Field(default_factory=list)
    locked_facts: list[LockedFact] = Field(default_factory=list)
    external_assets: list[dict[str, Any]] = Field(default_factory=list)


class JobBrief(BaseModel):
    goal: str
    # Absence is not evidence of a leadership/business audience. Existing
    # serialized audience values remain intact; no historical brief is migrated.
    audience: str = ""
    audience_candidates: list[dict[str, str]] = Field(default_factory=list)
    language: str = "zh-CN"
    target_pages: int = DEFAULT_TARGET_PAGES
    # Legacy briefs used 10 as a flexible planning default. Preserve that behavior
    # while carrying an explicit user's page count through planning and review.
    target_pages_explicit: bool = False
    aspect_ratio: Literal["16:9", "A0-portrait"] = "16:9"
    must_include: list[str] = Field(default_factory=list)
    locked_fact_ids: list[int] = Field(default_factory=list)
    customer_constraints: list[str] = Field(default_factory=list)
    rewrite_policy: Literal["preserve_facts_restructure"] = "preserve_facts_restructure"
    cloud_allowed: bool = True
    audience_profile: Literal["general", "leadership"] = "general"
    style_direction: str = ""
    # The reviewed task-intent layer sits beside, rather than inside, the
    # renderer route.  Defaults retain compatibility with historical briefs.
    presentation_intent: PresentationIntent = Field(default_factory=PresentationIntent)

    @field_validator("target_pages", mode="before")
    @classmethod
    def reject_boolean_pages(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("target_pages must be an integer page count")
        return value

    @field_validator("target_pages")
    @classmethod
    def validate_pages(cls, value: int) -> int:
        if not MIN_TARGET_PAGES <= value <= MAX_TARGET_PAGES:
            raise ValueError(f"target_pages must be between {MIN_TARGET_PAGES} and {MAX_TARGET_PAGES}")
        return value


class SlideSpec(BaseModel):
    id: str
    title: str
    purpose: str
    layout: Literal["cover", "summary", "content", "evidence", "conclusion"]
    # A presentation-form-specific page role changes composition without
    # proliferating incompatible layout enums or hard-coding a colour theme.
    page_role: str = ""
    points: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    asset_refs: list[str] = Field(default_factory=list)
    # Shared picture fit/crop/mask/border contract, keyed by exact asset_ref.
    # The image renderer validates geometry before export; no automatic crop.
    asset_geometry: dict[str, dict[str, Any]] = Field(default_factory=dict)
    speaker_notes: str = ""
    motion: Literal["none", "fade", "reveal"] = "reveal"
    revision_notes: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("slide id must contain only letters, numbers, underscores, or hyphens")
        return value

    @field_validator("page_role")
    @classmethod
    def validate_page_role(cls, value: str) -> str:
        if value and not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
            raise ValueError("page_role must use lowercase letters, digits, and hyphens")
        return value

    @field_validator("speaker_notes")
    @classmethod
    def remove_internal_paths_from_speaker_notes(cls, value: str) -> str:
        return sanitize_speaker_notes_text(value)


class DeckPlan(BaseModel):
    title: str
    subtitle: str = ""
    # An empty value means no visual identity has been evidenced yet.  The
    # planner's visual-system contract may provide an explicit direction or a
    # neutral baseline; a serialized plan must never revive a generic Studio
    # deep-blue/teal style on its own.
    visual_direction: str = ""
    font_family: str = "PingFang SC" if sys.platform == "darwin" else "Microsoft YaHei"
    planning_strategy: dict[str, Any] = Field(default_factory=dict)
    prototype_slide_ids: list[str] = Field(default_factory=list)
    slides: list[SlideSpec]

    @model_validator(mode="after")
    def validate_slides(self) -> "DeckPlan":
        if not MIN_TARGET_PAGES <= len(self.slides) <= MAX_TARGET_PAGES:
            raise ValueError(f"DeckPlan must contain between {MIN_TARGET_PAGES} and {MAX_TARGET_PAGES} slides")
        slide_ids = [slide.id for slide in self.slides]
        if len(slide_ids) != len(set(slide_ids)):
            raise ValueError("DeckPlan slide ids must be unique")
        if self.prototype_slide_ids:
            if not 1 <= len(self.prototype_slide_ids) <= 5:
                raise ValueError("prototype_slide_ids must contain between 1 and 5 slide ids")
            if len(self.prototype_slide_ids) != len(set(self.prototype_slide_ids)):
                raise ValueError("prototype_slide_ids must be unique")
            unknown = [slide_id for slide_id in self.prototype_slide_ids if slide_id not in slide_ids]
            if unknown:
                raise ValueError(f"prototype_slide_ids contains unknown slide ids: {unknown}")
        return self


def has_explicit_page_target(brief: JobBrief) -> bool:
    """A nondefault number in an older, manually authored brief is intentional too."""
    return brief.target_pages_explicit or brief.target_pages != DEFAULT_TARGET_PAGES


def validate_plan_page_count(brief: JobBrief, plan: DeckPlan) -> None:
    if has_explicit_page_target(brief) and len(plan.slides) != brief.target_pages:
        raise PageCountError(
            f"明确要求 {brief.target_pages} 页，当前 deck plan 为 {len(plan.slides)} 页；"
            "请补充分页规划或更新已审阅的页数要求，不能静默减页或凑页。"
        )


def select_prototype_slides(plan: DeckPlan) -> list[SlideSpec]:
    if plan.prototype_slide_ids:
        by_id = {slide.id: slide for slide in plan.slides}
        return [by_id[slide_id] for slide_id in plan.prototype_slide_ids]
    if len(plan.slides) <= 3:
        return plan.slides
    return [plan.slides[0], plan.slides[len(plan.slides) // 2], plan.slides[-1]]


class ReviewDecision(BaseModel):
    gate: Literal["brief", "prototype", "final"]
    approved: bool
    artifact_hashes: dict[str, str] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
    note: str = ""


class ReviewComment(BaseModel):
    id: str
    slide_id: str
    body: str
    status: Literal["open", "queued", "applied", "resolved", "superseded"] = "open"
    created_at: str = Field(default_factory=utc_now)
    superseded_at: str | None = None
    superseded_reason: str | None = None


class RevisionBatch(BaseModel):
    id: str
    slide_ids: list[str]
    comment_ids: list[str]
    review_gate: Literal["prototype", "final"] = "final"
    slide_hashes: dict[str, str] = Field(default_factory=dict)
    status: Literal["pending_agent", "applied", "rebuilt", "superseded"] = "pending_agent"
    created_at: str = Field(default_factory=utc_now)
    applied_at: str | None = None
    applied_deck_plan_sha256: str | None = None
    rebuilt_at: str | None = None
    superseded_at: str | None = None
    superseded_reason: str | None = None


class JobRecord(BaseModel):
    id: str
    name: str
    client: str = ""
    state: JobState = JobState.INTAKE
    route: ProductionRoute = ProductionRoute.NATIVE
    route_source: Literal["automatic", "user_override", "legacy"] = "legacy"
    route_reason: str = ""
    authoring_policy_id: str | None = None
    # A Work request is instruction provenance, not a normal source.  Keeping
    # it separate stops assistant/user instructions leaking into slide copy.
    work_request: WorkRequestBinding | None = None
    input_roles: dict[str, InputRole] = Field(default_factory=dict)
    source_ids: list[str] = Field(default_factory=list)
    approvals: dict[str, bool] = Field(default_factory=lambda: {"brief": False, "prototype": False, "final": False})
    approval_hashes: dict[str, dict[str, str]] = Field(default_factory=dict)
    dirty_slides: list[str] = Field(default_factory=list)
    blocked_reason: str | None = None
    resume_state: JobState | None = None
    failure: str | None = None
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)


class BuildManifest(BaseModel):
    job_id: str
    created_at: str = Field(default_factory=utc_now)
    status: Literal["candidate", "alternate", "final"]
    route: ProductionRoute
    source_hashes: dict[str, str]
    providers: list[dict[str, str]] = Field(default_factory=list)
    engines: dict[str, str]
    outputs: dict[str, dict[str, Any]]
    qa: dict[str, Any]
