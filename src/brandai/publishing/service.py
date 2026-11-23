from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.orm import Session

from brandai.adapters.publishing import DeliveryMedia, DeliveryReceipt, PublishRequest, SocialPublisher
from brandai.db.brand_models import BackgroundJob, PublishRecord, SocialConnection
from brandai.errors import BrandAIError, GenerationBlockedError, InvalidStateTransitionError, NotFoundError
from brandai.jobs.models import PermanentJobError, RetryJobError
from brandai.jobs.queue import ClaimedJob, JobQueue
from brandai.store import BrandAIStore

from .connections import ConnectionService
from .email import EmailDeliveryAdapter
from .gate import PublishingGate
from .models import ConnectionResponse, DispatchRequest, ProviderFailure, PublishResponse, PublishingBlocked, TokenInput
from .package import DeliveryBundle, build_bundle
from .state import release_failed_schedule


PublisherFactory = Callable[[ConnectionResponse, TokenInput], SocialPublisher]


def _hash(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _view(row: PublishRecord) -> PublishResponse:
    return PublishResponse(
        id=row.id, content_id=row.content_id, revision_id=row.revision_id, visual_asset_id=row.visual_id,
        channel=row.channel, status=row.status, created_at=row.created_at.replace(tzinfo=UTC),
        scheduled_at=row.scheduled_at.replace(tzinfo=UTC) if row.scheduled_at else None,
        detail=row.detail, job_id=row.detail.get("job_id"),
    )


class PublishingService:
    def __init__(self, store: BrandAIStore, queue: JobQueue, connections: ConnectionService,
                 email: EmailDeliveryAdapter | None, publishers: dict[str, PublisherFactory]):
        self.store, self.queue, self.connections = store, queue, connections
        self.email, self.publishers = email, publishers
        self.gate = PublishingGate(store)

    def export(self, brand_id: str, content_id: str, visual_id: str | None) -> PublishResponse:
        with self.store.brand_session(brand_id, immediate=True) as session:
            approved = self.gate.require(session, brand_id, content_id, visual_id)
            key = _hash(["export", approved.revision.id, approved.visual.id])
            row = session.scalar(select(PublishRecord).where(PublishRecord.dedup_key == key))
            if row is None:
                now = self.queue.clock()
                bundle = build_bundle(session, approved, now)
                row = PublishRecord(
                    content_id=content_id, revision_id=approved.revision.id, visual_id=approved.visual.id,
                    channel="export", status="DELIVERED", idempotency_key=f"export:{key}", dedup_key=key, request_hash=key,
                    created_at=now, detail={"manifest": bundle.manifest, "sha256": sha256(bundle.data).hexdigest()},
                )
                session.add(row)
                session.flush()
            return _view(row)

    def download(self, brand_id: str, content_id: str, export_id: str | None) -> DeliveryBundle:
        with self.store.brand_session(brand_id, immediate=True) as session:
            query = select(PublishRecord).where(PublishRecord.content_id == content_id, PublishRecord.channel == "export")
            if export_id:
                query = query.where(PublishRecord.id == export_id)
            row = session.scalar(query.order_by(PublishRecord.created_at.desc(), PublishRecord.id).limit(1))
            approved = self.gate.require(session, brand_id, content_id, row.visual_id if row else None)
            if row is None:
                raise NotFoundError("build an approved export before downloading")
            bundle = build_bundle(session, approved, row.created_at.replace(tzinfo=UTC))
            if sha256(bundle.data).hexdigest() != row.detail["sha256"]:
                raise InvalidStateTransitionError("export provenance changed; render a new asset and build a new export")
            return bundle

    def get(self, brand_id: str, record_id: str) -> PublishResponse:
        with self.store.brand_session(brand_id) as session:
            return _view(self._record(session, record_id))

    @staticmethod
    def _record(session: Session, record_id: str) -> PublishRecord:
        row = session.get(PublishRecord, record_id)
        if row is None:
            raise NotFoundError("publish record not found in this brand")
        return row

    def enqueue(self, brand_id: str, content_id: str, request: DispatchRequest) -> PublishResponse:
        key = f"api:{request.idempotency_key}"
        due = request.scheduled_at.astimezone(UTC) if request.scheduled_at else None
        request_hash = _hash([content_id, request.model_dump(mode="json", exclude={"idempotency_key", "scheduled_at"}),
                              due.isoformat() if due else None])
        with self.store.brand_session(brand_id, immediate=True) as session:
            existing = session.scalar(select(PublishRecord).where(PublishRecord.idempotency_key == key))
            if existing:
                if existing.request_hash != request_hash:
                    raise InvalidStateTransitionError("dispatch idempotency key already belongs to a different request")
                return _view(existing)
            approved = self.gate.require(session, brand_id, content_id,
                                         str(request.visual_asset_id) if request.visual_asset_id else None)
            identifier = str(request.connection_id) if request.connection_id else None
            if identifier:
                connection = session.get(SocialConnection, identifier)
                if connection is None:
                    raise NotFoundError("social connection not found in this brand")
                if connection.provider != request.channel or connection.platform != approved.item.platform:
                    raise GenerationBlockedError("choose a connection matching the approved content platform")
                if connection.ciphertext is None or request.channel not in self.publishers:
                    raise GenerationBlockedError("configure the official publisher and connection credentials")
                target = connection.account_id
                dedup_key = _hash(["social", content_id, connection.platform, target])
            else:
                if self.email is None:
                    raise GenerationBlockedError("configure an email delivery adapter")
                dedup_key = _hash(["email", approved.revision.id, request.recipient.casefold()])
            if duplicate := session.scalar(select(PublishRecord).where(PublishRecord.dedup_key == dedup_key)):
                if duplicate.visual_id != approved.visual.id or duplicate.scheduled_at != (due.replace(tzinfo=None) if due else None):
                    raise InvalidStateTransitionError("this destination already has a dispatch for the approved content")
                return _view(duplicate)
            if due and due <= self.queue.clock():
                raise GenerationBlockedError("schedule delivery for a future Asia/Jerusalem time")
            row = PublishRecord(
                content_id=content_id, revision_id=approved.revision.id, visual_id=approved.visual.id,
                connection_id=identifier, channel=request.channel, idempotency_key=key, dedup_key=dedup_key,
                request_hash=request_hash, recipient=request.recipient, status="QUEUED", scheduled_at=due, detail={},
            )
            session.add(row)
            session.flush()
            job = self.queue.insert(session, "publish", f"publish:{row.id}", {"publish_id": row.id}, available_at=due)
            row.detail = {"job_id": job.id}
            if due and identifier and approved.item.status == "APPROVED":
                approved.item.status, approved.item.scheduled_at = "SCHEDULED", due
            return _view(row)

    def run_dispatch(self, job: ClaimedJob, progress: Callable[[int], None]) -> dict:
        record_id, brand_id = job.payload["publish_id"], job.brand_id
        with self.store.brand_session(brand_id, immediate=True) as session:
            row = self._record(session, record_id)
            if row.status == "DELIVERED":
                return {"publish_id": row.id, **row.detail}
            if row.status != "QUEUED":
                if row.status == "DISPATCHING":
                    self._set(row, "NEEDS_RECONCILIATION", code="dispatch_outcome_unknown")
                problem = row.detail.get("code", "dispatch_is_terminal")
            else:
                problem = None
            connection_id, channel, recipient = row.connection_id, row.channel, row.recipient
        if problem:
            raise PermanentJobError(problem)

        progress(10)
        try:
            publisher = None
            if connection_id:
                connection, credentials = self.connections.credentials(brand_id, connection_id)
                factory = self.publishers.get(channel)
                if factory is None:
                    raise ProviderFailure("publisher_adapter_unavailable")
                publisher = factory(connection, credentials)
            elif self.email is None:
                raise ProviderFailure("email_adapter_unavailable")
        except BrandAIError:
            self._reject(brand_id, record_id, "BLOCKED", "connection_unavailable")
            raise PermanentJobError("connection_unavailable") from None
        except ProviderFailure as exc:
            self._handle_failure(brand_id, record_id, exc)

        progress(30)
        bundle = None
        with self.store.brand_session(brand_id, immediate=True) as session:
            row = self._record(session, record_id)
            lease = session.get(BackgroundJob, job.id)
            if row.status != "QUEUED" or lease is None or lease.lease_token != job.token:
                raise PermanentJobError("dispatch_lease_lost")
            try:
                approved = self.gate.require(session, brand_id, row.content_id, row.visual_id)
            except PublishingBlocked as exc:
                self._set(row, "BLOCKED", code="publishing_gate_failed", gate=exc.report.model_dump(mode="json"))
            else:
                if approved.revision.id != row.revision_id:
                    self._set(row, "BLOCKED", code="scheduled_revision_changed")
                else:
                    due = row.scheduled_at.replace(tzinfo=UTC) if row.scheduled_at else None
                    bundle = build_bundle(session, approved, self.queue.clock(), due)
                    files = tuple(DeliveryMedia(
                        file.manifest.name, "image/png" if file.manifest.format == "png" else "application/pdf", file.data,
                    ) for file in approved.files if file.manifest.format in {"png", "pdf"})
                    request = PublishRequest(brand_id, approved.item.platform, bundle.manifest["caption"], (), row.id, files)
                    self._set(row, "DISPATCHING")
            failure_code = row.detail.get("code")
            release_failed_schedule(session, row)
        if bundle is None:
            raise PermanentJobError(failure_code or "dispatch_input_unavailable")
        try:
            receipt = (
                asyncio.run(publisher.publish(request)) if publisher
                else self.email.deliver(recipient, bundle, idempotency_key=record_id)
            )
            required_status = "DELIVERED" if channel == "email" else "PUBLISHED"
            if not isinstance(receipt, DeliveryReceipt) or receipt.status != required_status or not receipt.external_id:
                raise ProviderFailure("provider_receipt_missing", uncertain=True)
        except ProviderFailure as exc:
            self._handle_failure(brand_id, record_id, exc)
        except Exception:
            self._reject(brand_id, record_id, "NEEDS_RECONCILIATION", "unexpected_dispatch_failure")
            raise PermanentJobError("dispatch_outcome_unknown") from None

        with self.store.brand_session(brand_id, immediate=True) as session:
            row = self._record(session, record_id)
            if row.status not in {"DISPATCHING", "NEEDS_RECONCILIATION"}:
                raise PermanentJobError("dispatch_lease_lost")
            safe_receipt = {"provider": receipt.provider, "external_id": receipt.external_id, "status": receipt.status}
            self._set(row, "DELIVERED", receipt=safe_receipt)
            if publisher:
                item = session.get(type(approved.item), row.content_id)
                item.status, item.published_at = "PUBLISHED", self.queue.clock()
                item.delivery_receipt = safe_receipt
            return {"publish_id": row.id, **row.detail}

    @staticmethod
    def _set(row: PublishRecord, status: str, **details: object) -> None:
        row.status, row.detail = status, {"job_id": row.detail["job_id"], **details}

    def _reject(self, brand_id: str, record_id: str, status: str, code: str) -> None:
        with self.store.brand_session(brand_id, immediate=True) as session:
            row = self._record(session, record_id)
            self._set(row, status, code=code)
            release_failed_schedule(session, row)

    def _handle_failure(self, brand_id: str, record_id: str, error: ProviderFailure) -> None:
        if error.uncertain:
            self._reject(brand_id, record_id, "NEEDS_RECONCILIATION", error.code)
            raise PermanentJobError("dispatch_outcome_unknown")
        if error.retry_after is not None:
            self._reject(brand_id, record_id, "QUEUED", error.code)
            raise RetryJobError(error.code, error.retry_after)
        self._reject(brand_id, record_id, "FAILED", error.code)
        raise PermanentJobError(error.code)
