from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, StringConstraints, model_validator

from brandai.research.models import ExternalCitation
from brandai.strategy.models import (
    Actor, Approval, Depth, EffectivePlaybook, HebrewAddress, Model, Platform, Register, Topic, VersionReference,
)


Language = Literal["he", "en"]
ContentStatus = Literal["DRAFT", "VALIDATION_FAILED", "READY_FOR_REVIEW", "APPROVED", "SCHEDULED", "PUBLISHED"]
CopyText = Annotated[str, StringConstraints(max_length=20000)]
Hashtag = Annotated[str, StringConstraints(max_length=100)]
TemplateId = Annotated[str, StringConstraints(min_length=1, max_length=120)]
Framing = Literal["centered", "copy_space_start", "copy_space_end", "wide_editorial"]
Subject = Literal["abstract_brand_scene", "product_detail", "editorial_workplace", "human_activity"]


class FormatRules(Model):
    caption_max: int
    hook_max: int
    cta_max: int
    hashtag_max: int
    link_placement: Literal["forbidden", "body"]


class DraftCopy(Model):
    hook: CopyText = ""
    body: CopyText = ""
    cta: CopyText = ""
    hashtags: list[Hashtag] = Field(default_factory=list, max_length=30)
    cited_claim_ids: list[UUID] = Field(default_factory=list, max_length=32)
    cited_external_evidence_ids: list[UUID] = Field(default_factory=list, max_length=8)


class VisualSelection(Model):
    template_id: TemplateId | None = None
    framing: Framing = "centered"
    subject: Subject | None = None


class ContentProposal(DraftCopy):
    # Explicit in the response schema sent to the structured LLM.
    hook: CopyText
    body: CopyText
    cta: CopyText
    hashtags: list[Hashtag] = Field(max_length=30)
    cited_claim_ids: list[UUID] = Field(max_length=32)


class FluxDirective(Model):
    kind: Literal["flux"] = "flux"
    selection: VisualSelection
    model: Literal["FLUX.2 Klein 4B"] = "FLUX.2 Klein 4B"
    style_prefix: str
    color_roles: dict[str, Any]
    constraints: dict[str, Any]


class PhotoDirective(Model):
    kind: Literal["on_demand"] = "on_demand"
    selection: VisualSelection
    source_asset_id: str
    mode: Literal["original_branded", "smart_enhance", "hero_cutout_scene"]
    preserve_source_pixels: bool
    operations: list[str]
    requires_gpu_capabilities: list[str]
    output_contract: dict[str, Any]


VisualDirective = Annotated[FluxDirective | PhotoDirective, Field(discriminator="kind")]


class Violation(Model):
    code: str
    field: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationAttempt(Model):
    number: int
    violations: list[Violation]


class ValidationReport(Model):
    rule_version: Literal[1] = 1
    valid: bool
    violations: list[Violation]
    attempts: list[ValidationAttempt]
    event_note_used: bool


class HebrewRegisterCheck(Model):
    applies: bool
    messaging_register: Register
    hebrew_address: HebrewAddress
    passed: bool


class ContentContext(Model):
    profile: VersionReference
    design_system: VersionReference
    playbook: EffectivePlaybook
    format_rules: FormatRules
    required_claim_id: str | None
    cadence_plan_id: str | None
    pillar_id: str | None


class ContentGenerate(Model):
    language: Literal["auto", "he", "en"] = "auto"
    created_by: Actor = "local-owner"
    research_topic: Topic | None = None
    research_topic_id: UUID | None = None

    @model_validator(mode="after")
    def validate_research(self) -> Self:
        if self.research_topic and self.research_topic_id:
            raise ValueError("choose a topic to research or an existing topic brief")
        return self


class ContentPatch(Model):
    revision_id: UUID
    edited_by: Actor = "local-owner"
    hook: CopyText | None = None
    body: CopyText | None = None
    cta: CopyText | None = None
    hashtags: list[Hashtag] | None = Field(default=None, max_length=30)
    cited_claim_ids: list[UUID] | None = Field(default=None, max_length=32)
    cited_external_evidence_ids: list[UUID] | None = Field(default=None, max_length=8)
    research_topic_id: UUID | None = None
    visual_directive: VisualSelection | None = None

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        changes = self.model_dump(exclude_unset=True, exclude={"revision_id", "edited_by"})
        if not changes or any(value is None for key, value in changes.items() if key != "research_topic_id"):
            raise ValueError("provide at least one non-null copy or visual field to edit")
        return self


class ContentApproval(Approval):
    revision_id: UUID


class ContentRevisionResponse(DraftCopy):
    id: str
    version: int
    visual_directive: VisualDirective
    hebrew_register_check: HebrewRegisterCheck
    validation_report: ValidationReport
    created_by: Actor
    created_at: AwareDatetime
    research_topic_id: UUID | None = None
    external_citations: list[ExternalCitation] = Field(default_factory=list)


class ContentSummary(Model):
    id: str
    brand_id: str
    cadence_slot_id: str | None
    on_demand_brief_id: str | None
    strategy_id: str
    playbook_version: int
    platform: Platform
    content_depth: Depth
    language: Language
    status: ContentStatus
    approved_revision_version: int | None
    approved_by: str | None
    approved_at: AwareDatetime | None
    scheduled_at: AwareDatetime | None
    published_at: AwareDatetime | None
    created_at: AwareDatetime


class ContentResponse(ContentSummary):
    context: ContentContext
    revision: ContentRevisionResponse
    revisions: list[ContentRevisionResponse]


class ContentGenerationUnavailable(Exception):
    """A required provider was unavailable before the generation could be evaluated."""
