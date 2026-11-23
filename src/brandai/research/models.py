from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator

from brandai.strategy.models import Model, Topic

from .network import domain_name


class TopicRequest(Model):
    topic: Topic
    domains: list[str] = Field(default_factory=list, max_length=8)
    content_id: UUID | None = None
    refresh: bool = False

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, domains: list[str]) -> list[str]:
        return sorted({domain_name(domain) for domain in domains})


class ExternalCitation(Model):
    id: UUID
    label: int
    url: str
    title: str
    retrieved_at: AwareDatetime
    sha256: str
    quoted_span: str

    @property
    def paragraph(self) -> str:
        return f"“{' '.join(self.quoted_span.split())}” [{self.label}]"


class TopicResponse(Model):
    id: UUID
    topic: str
    content_id: UUID | None
    domains: list[str]
    created_at: AwareDatetime
    evidence: list[ExternalCitation]
