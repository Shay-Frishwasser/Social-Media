from datetime import date, datetime, time
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator


Platform = Literal["instagram", "tiktok", "linkedin", "facebook", "youtube", "x"]
Depth = Literal["micro", "standard", "deep_dive", "educational_framework", "story", "carousel"]
DEPTHS = ("micro", "standard", "deep_dive", "educational_framework", "story", "carousel")
SUBSTANTIAL_DEPTHS = frozenset({"deep_dive", "educational_framework", "carousel"})
Register = Literal["neutral", "professional", "conversational", "formal"]
HebrewAddress = Literal["neutral", "plural", "feminine", "masculine", "inclusive"]
Actor = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=36)]
Topic = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
PillarId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,49}$")]
Weight = Annotated[int, Field(ge=0, le=100)]
Weekday = Annotated[int, Field(ge=0, le=6)]
PositiveVersion = Annotated[int, Field(ge=1)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlatformOverrides(Model):
    posts_per_week: int | None = Field(default=None, ge=1, le=42)
    weekdays: list[Weekday] | None = Field(default=None, min_length=1, max_length=7)
    local_times: list[time] | None = Field(default=None, min_length=1, max_length=12)
    excluded_topics: list[Topic] = Field(default_factory=list, max_length=50)
    pillar_weights: dict[PillarId, Weight] | None = Field(default=None, min_length=3, max_length=5)
    depth_mix: dict[Depth, Weight] | None = None
    account_type: Literal["company_page", "personal_profile"] | None = None
    link_policy: Topic | None = None

    @model_validator(mode="after")
    def validate_rules(self) -> Self:
        if self.weekdays and len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("weekdays must be unique")
        if self.local_times:
            if any(t.tzinfo or t.second or t.microsecond for t in self.local_times):
                raise ValueError("local_times must be local hours and minutes")
            if len(set(self.local_times)) != len(self.local_times):
                raise ValueError("local_times must be unique")
        if self.pillar_weights and sum(self.pillar_weights.values()) != 100:
            raise ValueError("pillar_weights must sum to 100")
        if self.depth_mix is not None:
            active = {depth for depth, weight in self.depth_mix.items() if weight}
            if (sum(self.depth_mix.values()) != 100 or len(active) < 3
                    or not active & SUBSTANTIAL_DEPTHS or not active - SUBSTANTIAL_DEPTHS):
                raise ValueError("depth_mix must total 100 and span at least three depths with substantial and lighter formats")
        return self


class PlatformOverrideUpdate(PlatformOverrides):
    created_by: Actor = "local-owner"


class EffectivePlaybook(Model):
    platform: Platform
    playbook_version: PositiveVersion
    sha256: str
    override_version: PositiveVersion | None
    rules: dict[str, Any]
    overrides: PlatformOverrides
    posts_per_week: int
    depth_mix: dict[Depth, Weight]
    link_policy: str


class VersionReference(Model):
    id: str
    version: PositiveVersion


class ClaimEvidence(Model):
    id: str
    field_key: str
    value: Any
    origin: Literal["observed", "inferred"]
    is_user_override: bool
    source_id: str | None
    quoted_span: str | None


class PillarSelection(Model):
    id: PillarId
    claim_ids: list[str] = Field(min_length=1, max_length=12)
    audience_claim_ids: list[str] = Field(default_factory=list, max_length=12)


class StrategyRequest(Model):
    platforms: list[Platform] = Field(min_length=1, max_length=6)
    messaging_register: Register | None = None
    hebrew_address: HebrewAddress | None = None
    created_by: Actor = "local-owner"

    @model_validator(mode="after")
    def unique_platforms(self) -> Self:
        if len(set(self.platforms)) != len(self.platforms):
            raise ValueError("platforms must be unique")
        return self


class StrategyUpdate(StrategyRequest):
    pillars: list[PillarSelection] | None = Field(default=None, min_length=3, max_length=5)
    pillar_weights: dict[Platform, dict[PillarId, Weight]] = Field(default_factory=dict, max_length=6)


class ArtDirection(Model):
    allowed_templates: list[Any]
    color_roles: dict[str, Any]
    image_model: Literal["FLUX.2 Klein 4B"] = "FLUX.2 Klein 4B"
    flux_style_prefix: str
    flux_constraints: dict[str, Any]


class ContentPillar(PillarSelection):
    title: str
    goal: str
    audience_pain_points: list[ClaimEvidence]
    audience_desires: list[ClaimEvidence]
    audience_context: list[ClaimEvidence]
    art_direction: ArtDirection


class MessagingAngle(Model):
    pillar_id: PillarId
    claim_id: str
    evidence_text: str
    angle: str
    hook: str
    messaging_register: Register
    hebrew_address: HebrewAddress


class StrategyDocument(Model):
    schema_version: Literal[1]
    profile: VersionReference
    design_system: VersionReference
    platforms: list[Platform]
    playbooks: dict[Platform, EffectivePlaybook]
    pillars: list[ContentPillar]
    claims: list[ClaimEvidence]
    messaging_angles: list[MessagingAngle]
    pillar_weights: dict[Platform, dict[PillarId, Weight]]
    pillar_weight_overrides: dict[Platform, dict[PillarId, Weight]]
    messaging_register: Register
    hebrew_address: HebrewAddress
    created_by: Actor
    previous_id: str | None = None
    approved_by: Actor | None = None
    approved_at: AwareDatetime | None = None


class PlanRequest(Model):
    start_date: date
    period: Literal["weekly", "monthly"]
    platforms: list[Platform] | None = Field(default=None, min_length=1, max_length=6)
    overrides: dict[Platform, PlatformOverrides] = Field(default_factory=dict, max_length=6)
    created_by: Actor = "local-owner"

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if not 1900 <= self.start_date.year <= 9998:
            raise ValueError("start_date year must be between 1900 and 9998")
        if self.period == "weekly" and self.start_date.weekday() != 0:
            raise ValueError("weekly start_date must be a Monday")
        if self.period == "monthly" and self.start_date.day != 1:
            raise ValueError("monthly start_date must be the first day of the month")
        if self.platforms and len(set(self.platforms)) != len(self.platforms):
            raise ValueError("platforms must be unique")
        return self


class PhotoSlotRequest(Model):
    photo_brief_id: UUID
    scheduled_at: AwareDatetime
    created_by: Actor = "local-owner"


class CadenceDocument(Model):
    schema_version: Literal[1]
    strategy: VersionReference
    request: PlanRequest
    end_date_exclusive: date
    timezone: Literal["Asia/Jerusalem"] = "Asia/Jerusalem"
    platforms: list[Platform]
    playbooks: dict[Platform, EffectivePlaybook]
    pillar_weights: dict[Platform, dict[PillarId, Weight]]
    previous_id: str | None = None
    replaces_id: str | None = None
    approved_by: Actor | None = None
    approved_at: AwareDatetime | None = None


class VersionSummary(VersionReference):
    status: str
    created_at: datetime


class LegacyPlanningResponse(VersionSummary):
    legacy: Literal[True] = True
    document: dict[str, Any]


class StrategyResponse(VersionSummary):
    document: StrategyDocument


class CalendarSlot(Model):
    id: str
    platform: Platform
    kind: Literal["cadence", "on_demand"]
    scheduled_at: AwareDatetime
    pillar_id: PillarId | None
    claim_id: str | None
    depth: Depth | None
    photo_brief_id: str | None


class CadenceResponse(VersionSummary):
    document: CadenceDocument
    slots: list[CalendarSlot]


class Approval(Model):
    approved_by: Actor = "local-owner"
