from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, HttpUrl, StringConstraints, model_validator

from brandai.content.models import ContentGenerate
from brandai.publishing.models import Key
from brandai.strategy.models import Model
from brandai.visual_studio.models import RenderOptions, VisualRender


JobKind = Literal["onboarding_research", "content_generation", "visual_render", "publish"]
JobStatus = Literal["QUEUED", "RUNNING", "RETRY_WAIT", "SUCCEEDED", "DEAD_LETTER"]


SocialHandles = Annotated[list[Annotated[str, StringConstraints(pattern=r"^@[A-Za-z0-9_.-]{1,64}$")]], Field(max_length=6)]


class OnboardingPayload(Model):
    website_url: HttpUrl
    company_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=250)]


    social_handles: SocialHandles = Field(default_factory=list)


class ContentPayload(ContentGenerate):
    slot_id: UUID | None = None
    brief_id: UUID | None = None

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if (self.slot_id is None) == (self.brief_id is None):
            raise ValueError("choose one cadence slot or photo brief")
        return self


class JobOptions(Model):
    idempotency_key: Key
    max_attempts: int = Field(default=4, ge=1, le=8)


class OnboardingJob(JobOptions):
    kind: Literal["onboarding_research"]
    payload: OnboardingPayload


class ContentJob(JobOptions):
    kind: Literal["content_generation"]
    payload: ContentPayload


class VisualRerenderPayload(RenderOptions):
    previous_id: UUID


class VisualJob(JobOptions):
    kind: Literal["visual_render"]
    payload: VisualRender | VisualRerenderPayload


JobEnqueue = Annotated[OnboardingJob | ContentJob | VisualJob, Field(discriminator="kind")]


class JobResponse(Model):
    id: UUID
    kind: JobKind
    status: JobStatus
    attempts: int
    max_attempts: int
    progress: int
    available_at: AwareDatetime
    result: dict[str, Any] | None
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class RetryJobError(Exception):
    def __init__(self, code: str, delay: float = 0):
        super().__init__(code)
        self.code, self.delay = code, delay


class PermanentJobError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
