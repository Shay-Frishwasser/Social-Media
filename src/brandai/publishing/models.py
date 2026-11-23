from typing import Annotated, Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, Field, SecretStr, StringConstraints, model_validator

from brandai.errors import BrandAIError
from brandai.strategy.models import Model

JERUSALEM = ZoneInfo("Asia/Jerusalem")
Channel = Literal["email", "meta", "linkedin"]
Provider = Literal["meta", "linkedin"]
SocialPlatform = Literal["instagram", "facebook", "linkedin"]
Key = Annotated[str, StringConstraints(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")]
EmailAddress = Annotated[str, StringConstraints(max_length=254, pattern=r"^[A-Za-z0-9.!#$%&'*+/=?^_\x60{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")]


class GateIssue(Model):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class GateReport(Model):
    allowed: bool
    content_id: UUID
    revision_id: UUID | None = None
    visual_asset_id: UUID | None = None
    issues: list[GateIssue] = Field(default_factory=list)


class PublishingBlocked(BrandAIError):
    def __init__(self, report: GateReport):
        super().__init__("Publishing preflight failed")
        self.report = report


class ProviderFailure(Exception):
    def __init__(self, code: str, *, retry_after: float | None = None, uncertain: bool = False):
        super().__init__(code)
        self.code, self.retry_after, self.uncertain = code, retry_after, uncertain


class DispatchRequest(Model):
    channel: Channel
    visual_asset_id: UUID | None = None
    connection_id: UUID | None = None
    recipient: EmailAddress | None = None
    scheduled_at: AwareDatetime | None = None
    idempotency_key: Key

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        if self.channel == "email":
            if self.recipient is None or self.connection_id is not None:
                raise ValueError("email requires a recipient")
        elif self.connection_id is None or self.recipient is not None:
            raise ValueError("social dispatch requires a connection")
        if self.scheduled_at and self.scheduled_at.utcoffset() != self.scheduled_at.astimezone(JERUSALEM).utcoffset():
            raise ValueError("scheduled_at must use the offset for Asia/Jerusalem at that instant")
        return self


class ExportRequest(Model):
    visual_asset_id: UUID | None = None


class PublishResponse(Model):
    id: UUID
    content_id: UUID
    revision_id: UUID
    visual_asset_id: UUID
    channel: Literal["export", "email", "meta", "linkedin"]
    status: Literal["QUEUED", "DISPATCHING", "DELIVERED", "BLOCKED", "FAILED", "NEEDS_RECONCILIATION"]
    job_id: UUID | None = None
    scheduled_at: AwareDatetime | None = None
    detail: dict[str, Any]
    created_at: AwareDatetime


class ConnectionRequest(Model):
    provider: Provider
    platform: SocialPlatform
    account_id: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]

    @model_validator(mode="after")
    def validate_account(self) -> Self:
        import re

        if self.provider == "linkedin":
            if self.platform != "linkedin" or not re.fullmatch(r"urn:li:(?:person|organization):[A-Za-z0-9_-]+", self.account_id):
                raise ValueError("LinkedIn requires a person or organization URN")
        elif self.platform not in {"instagram", "facebook"} or not re.fullmatch(r"[0-9]{1,32}", self.account_id):
            raise ValueError("Meta requires a numeric Instagram Business or Facebook Page ID")
        return self


class TokenInput(Model):
    access_token: SecretStr = Field(min_length=1, max_length=8000)
    refresh_token: SecretStr | None = Field(default=None, min_length=1, max_length=8000)
    expires_at: AwareDatetime | None = None


class ConnectionWrite(ConnectionRequest):
    tokens: TokenInput | None = None


class OAuthComplete(TokenInput):
    state: SecretStr = Field(min_length=10, max_length=128)


class OAuthState(Model):
    state: str
    expires_at: AwareDatetime


class ConnectionResponse(ConnectionRequest):
    id: UUID
    has_token: bool
    expires_at: AwareDatetime | None
