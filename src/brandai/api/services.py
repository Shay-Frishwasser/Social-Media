from __future__ import annotations

import asyncio

from brandai import BrandAIStore, RuntimeSettings
from brandai.db.global_models import Job
from brandai.onboarding.crawler import PlaywrightRenderer, WebsiteCrawler
from brandai.onboarding.embeddings import E5EmbeddingProvider
from brandai.onboarding.gpu import LocalLlamaGPUWorker, SingleGPUController
from brandai.onboarding.llm import LlamaCppProvider
from brandai.onboarding.pipeline import BrandResearchPipeline
from brandai.onboarding.search import DDGSSearchProvider, FallbackSearchProvider


def local_llm_worker(settings: RuntimeSettings) -> LocalLlamaGPUWorker:
    controller = SingleGPUController(
        server_exe=settings.directory("BRANDAI_LLAMACPP_DIR", "tools/llama.cpp-b12633") / "llama-server.exe",
        model=settings.directory("BRANDAI_LLM_DIR", "models/llm") / "qwen3.5-9b-q8/Qwen_Qwen3.5-9B-Q8_0.gguf",
        log_dir=settings.directory("BRANDAI_LOG_DIR", "logs"),
    )
    return LocalLlamaGPUWorker(controller)


def run_onboarding_job(store: BrandAIStore, job_id: str, brand_id: str, website_url: str, company_name: str) -> None:
    with store.GlobalSession.begin() as session:
        job = session.get(Job, job_id); job.status = "RUNNING"
    settings = store.settings
    try:
        embeddings = E5EmbeddingProvider(
            settings.directory("BRANDAI_EMBEDDINGS_DIR", "models/embeddings/multilingual-e5-base"))
        worker = local_llm_worker(settings)
        with store.brand_session(brand_id) as session:
            pipeline = BrandResearchPipeline(session,
                crawler=WebsiteCrawler(renderer=PlaywrightRenderer()),
                search=FallbackSearchProvider([DDGSSearchProvider()]), embeddings=embeddings)
            research = asyncio.run(pipeline.research(website_url, company_name))
            with worker.lease("llm") as lease:
                outcome = pipeline.extract(research, LlamaCppProvider(lease.base_url))
        with store.GlobalSession.begin() as session:
            job = session.get(Job, job_id); job.status = "COMPLETED"; job.result = {"profile_id": outcome.profile_id}
    except Exception as exc:
        with store.GlobalSession.begin() as session:
            job = session.get(Job, job_id); job.status = "FAILED"; job.result = {"error": f"{type(exc).__name__}: {exc}"}
