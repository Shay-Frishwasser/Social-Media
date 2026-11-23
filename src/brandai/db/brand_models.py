from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import BrandBase, json_type, new_uuid, utcnow


class Source(BrandBase):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    raw_content: Mapped[str | None] = mapped_column(Text)
    media_type: Mapped[str | None] = mapped_column(String(120))
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(json_type(), default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceChunk(BrandBase):
    __tablename__ = "source_chunks"
    __table_args__ = (UniqueConstraint("source_id", "ordinal"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    embedding_model: Mapped[str | None] = mapped_column(String(160))


class BrandClaim(BrandBase):
    __tablename__ = "brand_claims"
    __table_args__ = (
        CheckConstraint("origin IN ('observed','inferred')", name="origin_allowed"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    field_key: Mapped[str] = mapped_column(String(160), index=True)
    value: Mapped[dict] = mapped_column(json_type(), nullable=False)
    origin: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    quoted_span: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING", index=True)
    approved_by: Mapped[str | None] = mapped_column(String(36))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_user_override: Mapped[bool] = mapped_column(Boolean, default=False)
    supersedes_claim_id: Mapped[str | None] = mapped_column(ForeignKey("brand_claims.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BrandProfile(BrandBase):
    __tablename__ = "brand_profiles"
    __table_args__ = (UniqueConstraint("version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    document: Mapped[dict] = mapped_column(json_type(), nullable=False)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("brand_profiles.id"))
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BrandDesignSystem(BrandBase):
    __tablename__ = "brand_design_systems"
    __table_args__ = (UniqueConstraint("version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    colors: Mapped[dict] = mapped_column(json_type(), default=dict)
    font_pairings: Mapped[dict] = mapped_column(json_type(), default=dict)
    logo_safe_zones: Mapped[dict] = mapped_column(json_type(), default=dict)
    layout_templates: Mapped[list] = mapped_column(json_type(), default=list)
    flux_style_prefix: Mapped[str] = mapped_column(Text, default="")
    flux_constraints: Mapped[dict] = mapped_column(json_type(), default=dict)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("brand_design_systems.id"))
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Asset(BrandBase):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
    path: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(json_type(), default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Strategy(BrandBase):
    __tablename__ = "strategies"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    document: Mapped[dict] = mapped_column(json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CadencePlan(BrandBase):
    __tablename__ = "cadence_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    document: Mapped[dict] = mapped_column(json_type(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContentPackage(BrandBase):
    __tablename__ = "content_packages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContentPackageRevision(BrandBase):
    __tablename__ = "content_package_revisions"
    __table_args__ = (UniqueConstraint("package_id", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    package_id: Mapped[str] = mapped_column(ForeignKey("content_packages.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    caption_document: Mapped[dict] = mapped_column(json_type(), nullable=False)
    visual_recipe: Mapped[dict] = mapped_column(json_type(), nullable=False)
    grounded_brief: Mapped[dict] = mapped_column(json_type(), default=dict)
    validation_report: Mapped[dict] = mapped_column(json_type(), default=dict)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PostHistoryMemory(BrandBase):
    __tablename__ = "post_history_memory"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    package_id: Mapped[str | None] = mapped_column(ForeignKey("content_packages.id"))
    angle: Mapped[str] = mapped_column(Text, default="")
    hook: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(Text, default="")
    pillar: Mapped[str] = mapped_column(String(100), index=True)
    visual_concept: Mapped[str] = mapped_column(Text, default="")
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_dim: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Reminder(BrandBase):
    __tablename__ = "reminders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    package_id: Mapped[str | None] = mapped_column(ForeignKey("content_packages.id"))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    payload: Mapped[dict] = mapped_column(json_type(), default=dict)


class EmailDelivery(BrandBase):
    __tablename__ = "email_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    package_id: Mapped[str] = mapped_column(ForeignKey("content_packages.id"), index=True)
    recipient: Mapped[str] = mapped_column(String(320), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(30), default="QUEUED")
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column(json_type(), default=dict)


class PerformanceFeedback(BrandBase):
    __tablename__ = "performance_feedback"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    package_id: Mapped[str] = mapped_column(ForeignKey("content_packages.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    metrics: Mapped[dict] = mapped_column(json_type(), default=dict)
    notes: Mapped[str | None] = mapped_column(Text)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OnDemandPhotoBrief(BrandBase):
    __tablename__ = "on_demand_photo_briefs"
    __table_args__ = (CheckConstraint(
        "treatment_mode IN ('original_branded','smart_enhance','hero_cutout_scene')",
        name="photo_treatment_mode_allowed"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    treatment_mode: Mapped[str] = mapped_column(String(40), nullable=False)
    platform: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT", nullable=False)
    transient_overrides: Mapped[dict] = mapped_column(json_type(), default=dict)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlatformRuleOverride(BrandBase):
    __tablename__ = "platform_rule_overrides"
    __table_args__ = (UniqueConstraint("platform", "version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    platform: Mapped[str] = mapped_column(String(30), index=True)
    version: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(json_type(), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlannedContentSlot(BrandBase):
    __tablename__ = "planned_content_slots"
    __table_args__ = (
        UniqueConstraint("cadence_plan_id", "scheduled_at_utc"),
        UniqueConstraint("cadence_plan_id", "photo_brief_id"),
        CheckConstraint(
            "(photo_brief_id IS NULL AND pillar_id IS NOT NULL AND depth IS NOT NULL AND claim_id IS NOT NULL) OR "
            "(photo_brief_id IS NOT NULL AND pillar_id IS NULL AND depth IS NULL AND claim_id IS NULL)",
            name="cadence_or_photo",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    cadence_plan_id: Mapped[str] = mapped_column(ForeignKey("cadence_plans.id"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    scheduled_at_utc: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    pillar_id: Mapped[str | None] = mapped_column(String(50))
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("brand_claims.id"))
    depth: Mapped[str | None] = mapped_column(String(30))
    photo_brief_id: Mapped[str | None] = mapped_column(ForeignKey("on_demand_photo_briefs.id"))
