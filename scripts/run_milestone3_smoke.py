from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import func, select

from brandai import BrandAIStore, RuntimeSettings
from brandai.db.brand_models import BrandClaim, BrandProfile, Source, SourceChunk
from brandai.db.engine import session_factory
from brandai.onboarding.crawler import PlaywrightRenderer, WebsiteCrawler
from brandai.onboarding.embeddings import E5EmbeddingProvider
from brandai.onboarding.gpu import SingleGPUController, gpu_snapshot
from brandai.onboarding.llm import LlamaCppProvider
from brandai.onboarding.pipeline import BrandResearchPipeline
from brandai.onboarding.search import DDGSSearchProvider, FallbackSearchProvider


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://www.python.org/")
    parser.add_argument("--name", default="Python Software Foundation")
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parents[1]
    settings = RuntimeSettings.from_env(workspace / ".env")
    smoke_parent = settings.root / "smoke"
    smoke_parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="milestone3-", dir=smoke_parent))
    started = time.monotonic()
    report = {"url": args.url, "company_name": args.name, "temporary_runtime": str(temporary)}
    store = None
    brand_engine = None
    try:
        store = BrandAIStore(RuntimeSettings(temporary))
        brand = store.create_brand(args.name)
        brand_engine = store.brand_engine(brand.id)
        Session = session_factory(brand_engine)
        embedding = E5EmbeddingProvider(settings.root / "models" / "embeddings" / "multilingual-e5-base")
        crawl_started = time.monotonic()
        with Session.begin() as session:
            pipeline = BrandResearchPipeline(session,
                crawler=WebsiteCrawler(renderer=PlaywrightRenderer(), per_domain_interval=.35, max_pages=3),
                search=FallbackSearchProvider([DDGSSearchProvider()]), embeddings=embedding)
            research = asyncio.run(pipeline.research(args.url, args.name))
            report["crawl_embedding_seconds"] = time.monotonic() - crawl_started
            report["pages"] = [{"url": page.url, "characters": len(page.text), "rendered": page.rendered}
                               for page in research.pages]
            report["discovery_count"] = research.discovery_count
            report["visual_signals"] = asdict(research.visual_signals)

        controller = SingleGPUController(
            server_exe=Path(settings.root / "tools" / "llama.cpp-b12633" / "llama-server.exe"),
            model=settings.root / "models" / "llm" / "qwen3.5-9b-q8" / "Qwen_Qwen3.5-9B-Q8_0.gguf",
            log_dir=settings.root / "logs")
        extraction_started = time.monotonic()
        with controller.llama_server() as (base_url, metrics):
            with Session.begin() as session:
                outcome = pipeline.__class__(session, crawler=pipeline.crawler, search=pipeline.search,
                                             embeddings=embedding).extract(research, LlamaCppProvider(base_url))
                report["outcome"] = asdict(outcome)
                report["profile_states"] = list(session.scalars(select(BrandProfile.status).order_by(BrandProfile.version)))
                report["counts"] = {
                    "sources": session.scalar(select(func.count()).select_from(Source)),
                    "chunks": session.scalar(select(func.count()).select_from(SourceChunk)),
                    "claims": session.scalar(select(func.count()).select_from(BrandClaim)),
                }
        report["extraction_seconds"] = time.monotonic() - extraction_started
        report["gpu"] = asdict(metrics)
        report["total_seconds"] = time.monotonic() - started
        report["success"] = True
    except Exception as exc:
        report["success"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["gpu_at_failure"] = gpu_snapshot()
        raise
    finally:
        if brand_engine: brand_engine.dispose()
        if store: store.global_engine.dispose()
        report_path = settings.root / "logs" / "milestone3-real-site-smoke.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report["production_artifacts_removed"] = False
        try:
            shutil.rmtree(temporary)
            report["production_artifacts_removed"] = not temporary.exists()
        finally:
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(report_path)


if __name__ == "__main__":
    main()
