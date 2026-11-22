from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .db.brand_models import (
    Asset, BrandClaim, BrandDesignSystem, BrandProfile, CadencePlan, ContentPackage,
    ContentPackageRevision, EmailDelivery, OnDemandPhotoBrief, PerformanceFeedback, PostHistoryMemory,
    Reminder, Source, SourceChunk, Strategy,
)
from .db.global_models import Job, SystemReminder, User
from .db.base import utcnow
from .enums import ProfileStatus
from .errors import BrandIsolationError, GenerationBlockedError, InvalidStateTransitionError


PROFILE_TRANSITIONS = {
    None: {"DRAFT"},
    "DRAFT": {"RESEARCHING", "PENDING_REVIEW"},
    "RESEARCHING": {"PENDING_REVIEW"},
    "PENDING_REVIEW": {"APPROVED", "REVISION_PENDING"},
    "APPROVED": {"REVISION_PENDING"},
    "REVISION_PENDING": {"PENDING_REVIEW", "APPROVED"},
}


def _blob(vector: list[float] | np.ndarray) -> tuple[bytes, int]:
    array = np.asarray(vector, dtype=np.float32)
    if array.ndim != 1 or not array.size:
        raise ValueError("embedding must be a non-empty one-dimensional vector")
    return array.tobytes(), int(array.size)


def _cosine(query: np.ndarray, blob: bytes, dim: int) -> float:
    other = np.frombuffer(blob, dtype=np.float32, count=dim)
    denom = float(np.linalg.norm(query) * np.linalg.norm(other))
    return 0.0 if denom == 0 else float(np.dot(query, other) / denom)


class SourceRepository:
    def __init__(self, session: Session): self.session = session

    def add_source(self, *, kind: str, url: str | None = None, title: str | None = None,
                   content: str | None = None, media_type: str | None = None,
                   metadata: dict | None = None) -> Source:
        source = Source(kind=kind, url=url, title=title,
                        raw_content=content, media_type=media_type,
                        content_sha256=hashlib.sha256(content.encode()).hexdigest() if content else None,
                        metadata_json=metadata or {})
        self.session.add(source); self.session.flush(); return source

    def add_chunk(self, source_id: str, ordinal: int, chunk_text: str,
                  embedding: list[float] | np.ndarray | None = None,
                  model: str = "intfloat/multilingual-e5-base") -> SourceChunk:
        blob, dim = (None, None) if embedding is None else _blob(embedding)
        chunk = SourceChunk(source_id=source_id, ordinal=ordinal, text=chunk_text,
                            embedding=blob, embedding_dim=dim, embedding_model=model if blob else None)
        self.session.add(chunk); self.session.flush(); return chunk

    def fts_search(self, query: str, limit: int = 10) -> list[SourceChunk]:
        ids = self.session.execute(text(
            "SELECT s.id FROM source_chunks s JOIN source_chunks_fts f ON f.rowid=s.rowid "
            "WHERE source_chunks_fts MATCH :query ORDER BY bm25(source_chunks_fts) LIMIT :limit"
        ), {"query": query, "limit": limit}).scalars().all()
        return list(self.session.scalars(select(SourceChunk).where(SourceChunk.id.in_(ids)))) if ids else []

    def vector_search(self, vector: list[float] | np.ndarray, limit: int = 10) -> list[tuple[SourceChunk, float]]:
        query = np.asarray(vector, dtype=np.float32)
        rows = self.session.scalars(select(SourceChunk).where(SourceChunk.embedding.is_not(None))).all()
        scored = [(row, _cosine(query, row.embedding, row.embedding_dim)) for row in rows
                  if row.embedding_dim == query.size]
        return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]


class ClaimRepository:
    def __init__(self, session: Session): self.session = session

    def add(self, *, field_key: str, value: Any, origin: str, confidence: float,
            source_id: str | None = None, quoted_span: str | None = None,
            status: str = "UNREVIEWED", approved_by: str | None = None,
            user_override: bool = False, supersedes_claim_id: str | None = None) -> BrandClaim:
        if origin == "observed" and (not source_id or not quoted_span):
            raise ValueError("observed claims require source_id and quoted_span")
        if user_override:
            status = "APPROVED"
            approved_by = approved_by or "user"
        claim = BrandClaim(field_key=field_key, value={"value": value}, origin=origin,
                           source_id=source_id, quoted_span=quoted_span, confidence=confidence,
                           status=status, approved_by=approved_by,
                           approved_at=utcnow() if status == "APPROVED" else None,
                           is_user_override=user_override, supersedes_claim_id=supersedes_claim_id)
        self.session.add(claim); self.session.flush(); return claim

    def effective(self, field_key: str) -> BrandClaim | None:
        claims = list(self.session.scalars(select(BrandClaim).where(BrandClaim.field_key == field_key)))
        def rank(c: BrandClaim) -> tuple[int, float, str]:
            if c.is_user_override and c.status == "APPROVED": tier = 5
            elif c.origin == "observed" and c.status == "APPROVED": tier = 4
            elif c.origin == "observed": tier = 3
            elif c.origin == "inferred" and c.status == "APPROVED": tier = 2
            else: tier = 1
            return tier, c.confidence, c.created_at.isoformat()
        eligible = [c for c in claims if c.status not in {"REJECTED", "SUPERSEDED"}]
        return max(eligible, key=rank) if eligible else None


class VersionRepository:
    def __init__(self, session: Session): self.session = session

    def append_profile(self, *, status: str, document: dict, created_by: str) -> BrandProfile:
        previous = self.session.scalar(select(BrandProfile).order_by(BrandProfile.version.desc()).limit(1))
        prior_status = previous.status if previous else None
        if status not in PROFILE_TRANSITIONS.get(prior_status, set()):
            raise InvalidStateTransitionError(f"profile transition {prior_status!r} -> {status!r} is not allowed")
        row = BrandProfile(version=(previous.version + 1 if previous else 1), status=status,
                           document=document, previous_id=previous.id if previous else None, created_by=created_by)
        self.session.add(row); self.session.flush(); return row

    def append_design_system(self, *, status: str, document: dict, created_by: str) -> BrandDesignSystem:
        previous = self.session.scalar(select(BrandDesignSystem).order_by(BrandDesignSystem.version.desc()).limit(1))
        row = BrandDesignSystem(version=(previous.version + 1 if previous else 1), status=status,
            colors=document.get("colors", {}), font_pairings=document.get("font_pairings", {}),
            logo_safe_zones=document.get("logo_safe_zones", {}), layout_templates=document.get("layout_templates", []),
            flux_style_prefix=document.get("flux_style_prefix", ""), flux_constraints=document.get("flux_constraints", {}),
            previous_id=previous.id if previous else None, created_by=created_by)
        self.session.add(row); self.session.flush(); return row

    def assert_generation_ready(self) -> None:
        profile = self.session.scalar(select(BrandProfile).order_by(BrandProfile.version.desc()).limit(1))
        design = self.session.scalar(select(BrandDesignSystem).order_by(BrandDesignSystem.version.desc()).limit(1))
        if not profile or profile.status != ProfileStatus.APPROVED:
            raise GenerationBlockedError("latest brand profile is not approved")
        if not design or design.status != "APPROVED":
            raise GenerationBlockedError("latest brand design system is not approved")


class AssetRepository:
    def __init__(self, session: Session, brand_assets_dir: Path):
        self.session, self.root = session, brand_assets_dir.resolve()

    def add(self, path: Path, *, kind: str, metadata: dict | None = None) -> Asset:
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise BrandIsolationError(f"asset path must be inside {self.root}")
        row = Asset(path=str(resolved), kind=kind, metadata_json=metadata or {},
                    sha256=hashlib.sha256(resolved.read_bytes()).hexdigest() if resolved.is_file() else None)
        self.session.add(row); self.session.flush(); return row


class ContentRepository:
    def __init__(self, session: Session): self.session = session

    def create(self, *, platform: str, caption_document: dict, visual_recipe: dict,
               created_by: str, grounded_brief: dict | None = None) -> ContentPackage:
        VersionRepository(self.session).assert_generation_ready()
        package = ContentPackage(platform=platform)
        self.session.add(package); self.session.flush()
        self.revise(package.id, caption_document=caption_document, visual_recipe=visual_recipe,
                    created_by=created_by, grounded_brief=grounded_brief)
        return package

    def revise(self, package_id: str, *, caption_document: dict, visual_recipe: dict,
               created_by: str, grounded_brief: dict | None = None,
               validation_report: dict | None = None) -> ContentPackageRevision:
        latest = self.session.scalar(select(func.max(ContentPackageRevision.version)).where(
            ContentPackageRevision.package_id == package_id)) or 0
        row = ContentPackageRevision(package_id=package_id, version=latest + 1,
            caption_document=caption_document, visual_recipe=visual_recipe,
            grounded_brief=grounded_brief or {}, validation_report=validation_report or {}, created_by=created_by)
        self.session.add(row); self.session.flush(); return row

    def revisions(self, package_id: str) -> list[ContentPackageRevision]:
        return list(self.session.scalars(select(ContentPackageRevision).where(
            ContentPackageRevision.package_id == package_id).order_by(ContentPackageRevision.version)))


class MemoryRepository:
    def __init__(self, session: Session): self.session = session

    def add(self, *, angle: str, hook: str, topic: str, pillar: str, visual_concept: str,
            embedding: list[float], package_id: str | None = None) -> PostHistoryMemory:
        blob, dim = _blob(embedding)
        row = PostHistoryMemory(package_id=package_id, angle=angle, hook=hook, topic=topic,
                                pillar=pillar, visual_concept=visual_concept, embedding=blob, embedding_dim=dim)
        self.session.add(row); self.session.flush(); return row

    def similar(self, embedding: list[float], limit: int = 10) -> list[tuple[PostHistoryMemory, float]]:
        query = np.asarray(embedding, dtype=np.float32)
        rows = self.session.scalars(select(PostHistoryMemory).where(PostHistoryMemory.embedding.is_not(None))).all()
        scored = [(r, _cosine(query, r.embedding, r.embedding_dim)) for r in rows if r.embedding_dim == query.size]
        return sorted(scored, key=lambda x: x[1], reverse=True)[:limit]


class GlobalRepository:
    """Global-only coordination data; never stores brand knowledge or generated content."""

    def __init__(self, session: Session): self.session = session

    def add_user(self, *, email: str, display_name: str | None = None) -> User:
        row = User(email=email, display_name=display_name)
        self.session.add(row); self.session.flush(); return row

    def add_job(self, *, brand_id: str, kind: str, payload: dict | None = None) -> Job:
        row = Job(brand_id=brand_id, kind=kind, payload=payload or {})
        self.session.add(row); self.session.flush(); return row

    def add_reminder(self, *, brand_id: str, due_at, payload: dict | None = None,
                     user_id: str | None = None) -> SystemReminder:
        row = SystemReminder(brand_id=brand_id, user_id=user_id, due_at=due_at, payload=payload or {})
        self.session.add(row); self.session.flush(); return row


class PlanningDeliveryRepository:
    """Brand-local strategy, cadence, reminder, delivery, and feedback records."""

    def __init__(self, session: Session): self.session = session

    def add_strategy(self, document: dict, *, status: str = "DRAFT") -> Strategy:
        version = (self.session.scalar(select(func.max(Strategy.version))) or 0) + 1
        row = Strategy(version=version, status=status, document=document)
        self.session.add(row); self.session.flush(); return row

    def add_cadence_plan(self, document: dict, *, status: str = "DRAFT") -> CadencePlan:
        version = (self.session.scalar(select(func.max(CadencePlan.version))) or 0) + 1
        row = CadencePlan(version=version, status=status, document=document)
        self.session.add(row); self.session.flush(); return row

    def add_reminder(self, *, due_at, package_id: str | None = None,
                     payload: dict | None = None) -> Reminder:
        row = Reminder(due_at=due_at, package_id=package_id, payload=payload or {})
        self.session.add(row); self.session.flush(); return row

    def add_email_delivery(self, *, package_id: str, recipient: str,
                           metadata: dict | None = None) -> EmailDelivery:
        row = EmailDelivery(package_id=package_id, recipient=recipient, metadata_json=metadata or {})
        self.session.add(row); self.session.flush(); return row

    def add_feedback(self, *, package_id: str, platform: str, metrics: dict,
                     notes: str | None = None) -> PerformanceFeedback:
        row = PerformanceFeedback(package_id=package_id, platform=platform, metrics=metrics, notes=notes)
        self.session.add(row); self.session.flush(); return row


class OnDemandPhotoRepository:
    def __init__(self, session: Session): self.session = session

    def create(self, *, source_asset_id: str, prompt: str, treatment_mode: str,
               platform: str, created_by: str, transient_overrides: dict | None = None) -> OnDemandPhotoBrief:
        VersionRepository(self.session).assert_generation_ready()
        if not prompt.strip(): raise ValueError("on-demand prompt cannot be empty")
        if self.session.get(Asset, source_asset_id) is None: raise NotFoundError("source asset not found in this brand")
        row = OnDemandPhotoBrief(source_asset_id=source_asset_id, prompt=prompt.strip(),
            treatment_mode=treatment_mode, platform=platform, created_by=created_by,
            transient_overrides=transient_overrides or {})
        self.session.add(row); self.session.flush(); return row

