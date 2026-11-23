from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from brandai.db.brand_models import CadencePlan, ContentItem, ContentRevision, PlannedContentSlot
from brandai.errors import NotFoundError
from brandai.strategy.models import Platform

from .context import PreparedContent
from .diversity import PriorCopy
from .models import (
    ContentContext, ContentResponse, ContentRevisionResponse, ContentStatus, ContentSummary, DraftCopy,
    HebrewRegisterCheck, ValidationReport, VisualDirective,
)


ACTIVE_COPY = ("READY_FOR_REVIEW", "APPROVED", "SCHEDULED", "PUBLISHED")


def _aware(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _summary(row: ContentItem) -> ContentSummary:
    return ContentSummary(
        id=row.id, brand_id=row.brand_id, cadence_slot_id=row.cadence_slot_id, on_demand_brief_id=row.on_demand_brief_id,
        strategy_id=row.strategy_id, playbook_version=row.playbook_version, platform=row.platform,
        content_depth=row.content_depth, language=row.language, status=row.status,
        approved_revision_version=row.approved_revision_version, approved_by=row.approved_by,
        approved_at=_aware(row.approved_at), scheduled_at=_aware(row.scheduled_at),
        published_at=_aware(row.published_at), created_at=_aware(row.created_at),
    )


def revision_copy(row: ContentRevision) -> DraftCopy:
    return DraftCopy(hook=row.hook, body=row.body, cta=row.cta, hashtags=row.hashtags, cited_claim_ids=row.cited_claim_ids,
                     cited_external_evidence_ids=row.research_json.get("cited_external_evidence_ids", []))


def _revision(row: ContentRevision) -> ContentRevisionResponse:
    return ContentRevisionResponse(
        **revision_copy(row).model_dump(), id=row.id, version=row.version, created_by=row.created_by,
        created_at=_aware(row.created_at), visual_directive=row.visual_directive,
        hebrew_register_check=row.hebrew_register_check, validation_report=row.validation_report,
        research_topic_id=row.research_json.get("research_topic_id"),
        external_citations=row.research_json.get("external_citations", []),
    )


class ContentRepository:
    """The caller supplies only the current brand session."""

    def __init__(self, session: Session, brand_id: str):
        self.session, self.brand_id = session, brand_id

    def get(self, content_id: str) -> ContentItem:
        row = self.session.get(ContentItem, content_id)
        if row is None or row.brand_id != self.brand_id:
            raise NotFoundError("content item not found in this brand")
        return row

    def existing(self, context: PreparedContent) -> ContentItem | None:
        if context.slot_id:
            source = (ContentItem.cadence_slot_id == context.slot_id,)
        else:
            source = (ContentItem.cadence_slot_id.is_(None), ContentItem.on_demand_brief_id == context.brief_id)
        return self.session.scalar(select(ContentItem).where(ContentItem.brand_id == self.brand_id, *source))

    def list(self, *, platform: Platform | None, status: ContentStatus | None, limit: int, offset: int) -> list[ContentSummary]:
        query = select(ContentItem).where(ContentItem.brand_id == self.brand_id)
        if platform is not None:
            query = query.where(ContentItem.platform == platform)
        if status is not None:
            query = query.where(ContentItem.status == status)
        rows = self.session.scalars(query.order_by(ContentItem.created_at.desc(), ContentItem.id).limit(limit).offset(offset))
        return [_summary(row) for row in rows]

    def latest(self, row: ContentItem) -> ContentRevision:
        return self.session.scalars(select(ContentRevision).where(ContentRevision.content_id == row.id).order_by(
            ContentRevision.version.desc(),
        ).limit(1)).one()

    def response(self, row: ContentItem) -> ContentResponse:
        revisions = [
            _revision(revision) for revision in self.session.scalars(select(ContentRevision).where(
                ContentRevision.content_id == row.id,
            ).order_by(ContentRevision.version.desc()))
        ]
        return ContentResponse(
            **_summary(row).model_dump(), context=ContentContext.model_validate(row.context_json),
            revision=revisions[0], revisions=revisions,
        )

    def create(self, prepared: PreparedContent) -> ContentItem:
        row = ContentItem(
            brand_id=self.brand_id, cadence_slot_id=prepared.slot_id, on_demand_brief_id=prepared.brief_id,
            strategy_id=prepared.strategy_id, playbook_version=prepared.provenance.playbook.playbook_version,
            platform=prepared.platform, content_depth=prepared.depth, language=prepared.language,
            context_json=prepared.provenance.model_dump(mode="json"), status="DRAFT",
        )
        self.session.add(row)
        self.session.flush()
        return row

    def append(
        self, row: ContentItem, copy: DraftCopy, visual: VisualDirective, voice: HebrewRegisterCheck,
        report: ValidationReport, actor: str, *, version: int, embedding: bytes | None, embedding_model: str | None,
        prepared: PreparedContent,
    ) -> ContentResponse:
        declared = set(copy.cited_external_evidence_ids)
        self.session.add(ContentRevision(
            content_id=row.id, version=version, **copy.model_dump(mode="json", exclude={"cited_external_evidence_ids"}),
            research_json={
                "research_topic_id": prepared.research_topic_id,
                "cited_external_evidence_ids": [str(identifier) for identifier in copy.cited_external_evidence_ids],
                "external_citations": [item.model_dump(mode="json") for item in prepared.external if item.id in declared],
            },
            visual_directive=visual.model_dump(mode="json"), hebrew_register_check=voice.model_dump(mode="json"),
            validation_report=report.model_dump(mode="json"), created_by=actor,
            embedding=embedding, embedding_model=embedding_model,
        ))
        row.status = "READY_FOR_REVIEW" if report.valid else "VALIDATION_FAILED"
        self.session.flush()
        return self.response(row)

    def recent(self, limit: int, *, omit_content_id: str | None = None) -> list[PriorCopy]:
        latest = select(
            ContentRevision.content_id, func.max(ContentRevision.version).label("version"),
        ).group_by(ContentRevision.content_id).subquery()
        query = select(ContentRevision).join(
            latest, (latest.c.content_id == ContentRevision.content_id) & (latest.c.version == ContentRevision.version),
        ).join(ContentItem).outerjoin(PlannedContentSlot).outerjoin(CadencePlan).where(
            ContentItem.brand_id == self.brand_id, ContentItem.status.in_(ACTIVE_COPY),
            or_(ContentItem.cadence_slot_id.is_(None), CadencePlan.status == "APPROVED",
                ContentItem.status.in_(("SCHEDULED", "PUBLISHED"))),
        )
        if omit_content_id:
            query = query.where(ContentItem.id != omit_content_id)
        return [
            PriorCopy(row.content_id, row.hook, row.body, row.cta, row.embedding, row.embedding_model)
            for row in self.session.scalars(query.order_by(ContentRevision.created_at.desc(), ContentRevision.id).limit(limit))
        ]
