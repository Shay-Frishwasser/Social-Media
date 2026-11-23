from sqlalchemy import select
from sqlalchemy.orm import Session

from brandai.db.brand_models import ContentItem, PublishRecord


def release_failed_schedule(session: Session, record: PublishRecord) -> None:
    if record.status not in {"BLOCKED", "FAILED"} or not record.connection_id or not record.scheduled_at:
        return
    item = session.get(ContentItem, record.content_id)
    if item is None or item.status != "SCHEDULED":
        return
    other = session.scalar(select(PublishRecord.id).where(
        PublishRecord.content_id == record.content_id, PublishRecord.id != record.id,
        PublishRecord.connection_id.is_not(None), PublishRecord.scheduled_at.is_not(None),
        PublishRecord.status.in_(("QUEUED", "DISPATCHING", "NEEDS_RECONCILIATION")),
    ).limit(1))
    if other is None:
        item.status, item.scheduled_at = "APPROVED", None
