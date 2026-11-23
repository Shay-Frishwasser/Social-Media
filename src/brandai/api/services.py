from __future__ import annotations

import asyncio
import os
from collections.abc import Callable

from sqlalchemy import select

from brandai import BrandAIStore, RuntimeSettings
from brandai.adapters.workers import GPUWorkerProvider
from brandai.db.brand_models import BrandProfile
from brandai.jobs.models import OnboardingPayload, PermanentJobError
from brandai.jobs.queue import ClaimedJob
from brandai.onboarding.embeddings import E5EmbeddingProvider
from brandai.onboarding.gpu import LocalLlamaGPUWorker, SingleGPUController
from brandai.onboarding.llm import LlamaCppProvider
from brandai.onboarding.pipeline import BrandResearchPipeline
from brandai.onboarding.search import DDGSSearchProvider
from brandai.research.network import PublicTopicCrawler, public_url


def local_llm_worker(settings: RuntimeSettings) -> LocalLlamaGPUWorker:
    controller = SingleGPUController(
        server_exe=settings.directory("BRANDAI_LLAMACPP_DIR", "tools/llama.cpp-b12633") / "llama-server.exe",
        model=settings.directory("BRANDAI_LLM_DIR", "models/llm") / "qwen3.5-9b-q8/Qwen_Qwen3.5-9B-Q8_0.gguf",
        log_dir=settings.directory("BRANDAI_LOG_DIR", "logs"),
    )
    return LocalLlamaGPUWorker(controller)


def execute_onboarding(
    store: BrandAIStore, job: ClaimedJob, progress: Callable[[int], None], worker: GPUWorkerProvider | None,
) -> dict:
    payload = OnboardingPayload.model_validate(job.payload)
    try:
        source_url = public_url(str(payload.website_url))
    except ValueError:
        raise PermanentJobError("onboarding_source_requires_public_https") from None
    if worker is None:
        if os.name != "nt":
            raise PermanentJobError("onboarding_worker_unconfigured")
        worker = local_llm_worker(store.settings)
    progress(20)
    with store.brand_session(job.brand_id) as session:
        # The extraction versions commit with their job ID. A replay returns the
        # committed version even if the original worker lost its queue lease.
        existing = session.scalar(select(BrandProfile.id).where(
            BrandProfile.created_by == job.id, BrandProfile.status == "PENDING_REVIEW",
        ))
        if existing:
            return {"profile_id": existing}
        latest = session.scalar(select(BrandProfile.status).order_by(BrandProfile.version.desc()).limit(1))
        if latest not in {None, "DRAFT"}:
            raise PermanentJobError("onboarding_requires_draft_profile")
        pipeline = BrandResearchPipeline(
            session, crawler=PublicTopicCrawler(), search=DDGSSearchProvider(),
            embeddings=E5EmbeddingProvider(store.settings.directory(
                "BRANDAI_EMBEDDINGS_DIR", "models/embeddings/multilingual-e5-base")),
        )
        research = asyncio.run(pipeline.research(source_url, payload.company_name))
        with worker.lease("llm") as lease:
            outcome = pipeline.extract(research, LlamaCppProvider(lease.base_url), created_by=job.id)
    progress(95)
    return {"profile_id": outcome.profile_id}
