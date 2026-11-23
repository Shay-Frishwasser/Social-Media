from uuid import UUID

from fastapi import APIRouter

from brandai.errors import GenerationBlockedError
from brandai.jobs.models import JobEnqueue, JobResponse, JobStatus
from brandai.jobs.queue import JobQueue, JobRunner


def jobs_router(queue: JobQueue, runner: JobRunner, *, queue_only: bool = False) -> APIRouter:
    router = APIRouter(prefix="/api/brands/{brand_id}/jobs", tags=["Background jobs"])

    @router.post("", status_code=202)
    def enqueue(brand_id: UUID, job: JobEnqueue) -> JobResponse:
        payload = job.payload.model_dump(mode="json")
        if job.kind == "onboarding_research" and not payload["social_handles"]:
            payload.pop("social_handles")
        return queue.enqueue(str(brand_id), job.kind, job.idempotency_key, payload, max_attempts=job.max_attempts)

    @router.get("")
    def list_jobs(brand_id: UUID, status: JobStatus | None = None) -> list[JobResponse]:
        return queue.list(str(brand_id), status)

    @router.post("/run-next")
    def run_next(brand_id: UUID) -> JobResponse | None:
        if queue_only:
            raise GenerationBlockedError("the dedicated AWS queue worker owns job ticks")
        return runner.run_next(str(brand_id))

    @router.get("/{job_id}")
    def get_job(brand_id: UUID, job_id: UUID) -> JobResponse:
        return queue.get(str(brand_id), str(job_id))

    return router
