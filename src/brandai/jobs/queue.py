from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, Thread
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from brandai.db.base import utcnow
from brandai.db.brand_models import BackgroundJob, PublishRecord
from brandai.errors import BrandAIError, InvalidStateTransitionError, NotFoundError
from brandai.publishing.state import release_failed_schedule
from brandai.store import BrandAIStore

from .models import JobKind, JobResponse, JobStatus, PermanentJobError, RetryJobError


def view(row: BackgroundJob) -> JobResponse:
    return JobResponse(
        id=row.id, kind=row.kind, status=row.status, attempts=row.attempts, max_attempts=row.max_attempts,
        progress=row.progress, available_at=row.available_at.replace(tzinfo=UTC), result=row.result,
        error_code=row.error_code, created_at=row.created_at.replace(tzinfo=UTC), updated_at=row.updated_at.replace(tzinfo=UTC),
    )


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    brand_id: str
    kind: JobKind
    payload: dict
    token: str
    attempt: int
    max_attempts: int


class JobQueue:
    def __init__(self, store: BrandAIStore, *, clock: Callable[[], datetime] = utcnow, lease_seconds: int = 120):
        if lease_seconds < 6:
            raise ValueError("job leases must be at least six seconds")
        self.store, self.clock, self.lease_seconds = store, clock, lease_seconds

    def enqueue(self, brand_id: str, kind: JobKind, key: str, payload: dict, *, max_attempts: int = 4) -> JobResponse:
        # Reserve a separate namespace for internal publishing jobs.
        with self.store.brand_session(brand_id, immediate=True) as session:
            return view(self.insert(session, kind, f"api:{key}", payload, max_attempts=max_attempts))

    def insert(self, session: Session, kind: JobKind, key: str, payload: dict, *,
               max_attempts: int = 4, available_at: datetime | None = None) -> BackgroundJob:
        existing = session.scalar(select(BackgroundJob).where(BackgroundJob.idempotency_key == key))
        if existing:
            if (existing.kind, existing.payload, existing.max_attempts) != (kind, payload, max_attempts):
                raise InvalidStateTransitionError("job idempotency key already belongs to a different request")
            return existing
        row = BackgroundJob(kind=kind, idempotency_key=key, payload=payload, progress=0,
                            available_at=available_at or self.clock(), max_attempts=max_attempts)
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _get(session: Session, job_id: str) -> BackgroundJob:
        row = session.get(BackgroundJob, job_id)
        if row is None:
            raise NotFoundError("job not found in this brand")
        return row

    def get(self, brand_id: str, job_id: str) -> JobResponse:
        with self.store.brand_session(brand_id) as session:
            return view(self._get(session, job_id))

    def list(self, brand_id: str, status: JobStatus | None = None) -> list[JobResponse]:
        with self.store.brand_session(brand_id) as session:
            query = select(BackgroundJob)
            if status:
                query = query.where(BackgroundJob.status == status)
            return [view(row) for row in session.scalars(query.order_by(BackgroundJob.created_at.desc(), BackgroundJob.id).limit(100))]

    def _recover_expired(self, session: Session, now: datetime) -> None:
        expired = session.scalars(select(BackgroundJob).where(
            BackgroundJob.status == "RUNNING", BackgroundJob.lease_expires_at <= now,
        ))
        for row in expired:
            record = self._publish_record(session, row)
            uncertain = bool(record and record.status == "DISPATCHING")
            if uncertain:
                record.status = "NEEDS_RECONCILIATION"
                record.detail = {**record.detail, "code": "dispatch_lease_expired"}
            if record and record.status == "DELIVERED":
                row.status, row.progress, row.error_code = "SUCCEEDED", 100, None
                row.result = {"publish_id": record.id, **record.detail}
            elif uncertain or row.attempts >= row.max_attempts:
                row.status, row.error_code = "DEAD_LETTER", "dispatch_outcome_unknown" if uncertain else "job_lease_expired"
                if record and record.status == "QUEUED":
                    record.status, record.detail = "FAILED", {**record.detail, "code": row.error_code}
                    release_failed_schedule(session, record)
            else:
                row.status, row.available_at, row.error_code = "RETRY_WAIT", now, "job_lease_expired"
            row.lease_token = row.lease_expires_at = None
        session.flush()

    @staticmethod
    def _publish_record(session: Session, row: BackgroundJob) -> PublishRecord | None:
        if row.kind == "publish":
            return session.get(PublishRecord, row.payload["publish_id"])
        return None

    def claim(self, brand_id: str) -> ClaimedJob | None:
        with self.store.brand_session(brand_id, immediate=True) as session:
            now = self.clock()
            self._recover_expired(session, now)
            row = session.scalar(select(BackgroundJob).where(
                BackgroundJob.status.in_(("QUEUED", "RETRY_WAIT")), BackgroundJob.available_at <= now,
            ).order_by(BackgroundJob.available_at, BackgroundJob.created_at, BackgroundJob.id).limit(1))
            if row is None:
                return None
            row.status, row.attempts, row.progress = "RUNNING", row.attempts + 1, 0
            row.lease_token = str(uuid4())
            row.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            return ClaimedJob(row.id, brand_id, row.kind, row.payload, row.lease_token, row.attempts, row.max_attempts)

    def heartbeat(self, job: ClaimedJob, progress: int | None = None) -> bool:
        with self.store.brand_session(job.brand_id, immediate=True) as session:
            row = self._get(session, job.id)
            if row.status != "RUNNING" or row.lease_token != job.token:
                return False
            if progress is not None:
                row.progress = max(row.progress, min(99, max(0, progress)))
            row.lease_expires_at = self.clock() + timedelta(seconds=self.lease_seconds)
            return True

    def finish(self, job: ClaimedJob, *, result: dict | None = None,
               error: str | None = None, retry_delay: float | None = None) -> JobResponse:
        with self.store.brand_session(job.brand_id, immediate=True) as session:
            row = self._get(session, job.id)
            if row.status != "RUNNING" or row.lease_token != job.token:
                return view(row)
            row.lease_token = row.lease_expires_at = None
            row.error_code = error
            if error is None:
                row.status, row.progress, row.result = "SUCCEEDED", 100, result
            elif retry_delay is not None and row.attempts < row.max_attempts:
                backoff = min(900, 15 * 2 ** (row.attempts - 1))
                row.status = "RETRY_WAIT"
                row.available_at = self.clock() + timedelta(seconds=max(backoff, min(86400, retry_delay)))
            else:
                row.status = "DEAD_LETTER"
                record = self._publish_record(session, row)
                if record and record.status in {"QUEUED", "DISPATCHING"}:
                    record.status = "NEEDS_RECONCILIATION" if record.status == "DISPATCHING" else "FAILED"
                    record.detail = {**record.detail, "code": error}
                    release_failed_schedule(session, record)
            session.flush()
            return view(row)

    def gpu_pending(self, brand_id: str) -> bool:
        with self.store.brand_session(brand_id) as session:
            return session.scalar(select(BackgroundJob.id).where(
                BackgroundJob.kind.in_(("onboarding_research", "content_generation", "visual_render")),
                (BackgroundJob.status == "RUNNING")
                | (BackgroundJob.status.in_(("QUEUED", "RETRY_WAIT")) & (BackgroundJob.available_at <= self.clock())),
            ).limit(1)) is not None


JobHandler = Callable[[ClaimedJob, Callable[[int], None]], dict]


class JobRunner:
    def __init__(self, queue: JobQueue, handlers: dict[JobKind, JobHandler]):
        self.queue, self.handlers = queue, handlers

    def run_next(self, brand_id: str) -> JobResponse | None:
        job = self.queue.claim(brand_id)
        if job is None:
            return None
        stop = Event()

        def keep_alive() -> None:
            while not stop.wait(self.queue.lease_seconds / 3):
                try:
                    if not self.queue.heartbeat(job):
                        return
                except Exception:
                    # A transient local DB lock should not kill the heartbeat thread.
                    continue

        def progress(value: int) -> None:
            if not self.queue.heartbeat(job, value):
                raise PermanentJobError("job_lease_lost")

        thread = Thread(target=keep_alive, name="brandai-job-heartbeat", daemon=True)
        thread.start()
        try:
            handler = self.handlers.get(job.kind)
            if handler is None:
                raise PermanentJobError("worker_handler_unavailable")
            result = handler(job, progress)
        except PermanentJobError as exc:
            return self.queue.finish(job, error=exc.code)
        except RetryJobError as exc:
            return self.queue.finish(job, error=exc.code, retry_delay=exc.delay)
        except (BrandAIError, ValidationError):
            return self.queue.finish(job, error="job_input_blocked")
        except Exception:
            return self.queue.finish(job, error="worker_error", retry_delay=0)
        else:
            return self.queue.finish(job, result=result)
        finally:
            stop.set()
            thread.join()
