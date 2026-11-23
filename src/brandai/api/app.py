from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select

from brandai import BrandAIStore, RuntimeSettings
from brandai.content.diversity import DiversitySettings, HashEmbeddingProvider
from brandai.content.generator import ContentLLMProvider, MockDeterministicContentProvider, QwenContentProvider
from brandai.content.models import ContentGenerationUnavailable
from brandai.content.service import ContentService
from brandai.db.brand_models import Asset, BrandClaim, BrandDesignSystem, BrandProfile, Source
from brandai.db.global_models import Brand, Job
from brandai.enums import PhotoTreatmentMode
from brandai.errors import BrandAIError, NotFoundError
from brandai.onboarding.assets import MaterialIngestor
from brandai.onboarding.embeddings import E5EmbeddingProvider, EmbeddingProvider
from brandai.onboarding.search import DDGSSearchProvider, SearchProvider
from brandai.repositories import ClaimRepository, GlobalRepository, OnDemandPhotoRepository, VersionRepository
from brandai.research.network import PublicTopicCrawler
from brandai.research.service import TopicCrawler, TopicResearchService
from brandai.strategy.generator import DeterministicStrategyPlanner, StrategyPlanner
from brandai.visual_studio.images import VisualPipeline
from brandai.visual_studio.providers import EllipseSegmentationProvider, GradientBackgroundProvider
from brandai.visual_studio.renderer import ChromiumRenderer
from brandai.visual_studio.service import VisualService
from .content import content_router
from .planning import planning_router
from .schemas import (BrandCreate, ClaimDecision, ClaimOverride, DesignSystemUpdate,
                      KnowledgeGapAnswer, PhotoBriefCreate, ResearchRequest)
from .services import local_llm_worker, run_onboarding_job
from .topic_research import topic_router
from .visual_assets import visual_router


def _design_dict(row: BrandDesignSystem) -> dict:
    return {"id": row.id, "version": row.version, "status": row.status, "colors": row.colors,
            "font_pairings": row.font_pairings, "logo_safe_zones": row.logo_safe_zones,
            "layout_templates": row.layout_templates, "flux_style_prefix": row.flux_style_prefix,
            "flux_constraints": row.flux_constraints, "created_at": row.created_at}


def create_app(
    settings: RuntimeSettings | None = None, *, run_background_jobs: bool = True,
    strategy_planner: StrategyPlanner | None = None,
    content_provider: ContentLLMProvider | None = None, content_embeddings: EmbeddingProvider | None = None,
    content_diversity: DiversitySettings | None = None,
    topic_search: SearchProvider | None = None, topic_crawler: TopicCrawler | None = None,
    visual_pipeline: VisualPipeline | None = None,
) -> FastAPI:
    settings = settings or RuntimeSettings.from_env()
    store = BrandAIStore(settings)
    app = FastAPI(title="BrandAI Local", version="0.7.0")
    app.state.store = store
    app.state.run_background_jobs = run_background_jobs
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(BrandAIError)
    async def domain_error(_request, exc):
        return JSONResponse(status_code=404 if isinstance(exc, NotFoundError) else 409, content={"detail": str(exc)})

    @app.exception_handler(ContentGenerationUnavailable)
    async def provider_error(_request, exc):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.get("/api/health")
    def health(): return {"status": "ok", "runtime": str(settings.root)}

    @app.get("/api/brands")
    def list_brands():
        with store.GlobalSession() as session:
            return [{"id": row.id, "name": row.name, "status": row.status, "created_at": row.created_at}
                    for row in session.scalars(select(Brand).order_by(Brand.created_at.desc()))]

    @app.post("/api/brands", status_code=201)
    def create_brand(payload: BrandCreate):
        row = store.create_brand(payload.name)
        return {"id": row.id, "name": row.name, "status": row.status}

    @app.post("/api/brands/{brand_id}/research", status_code=202)
    def trigger_research(brand_id: str, payload: ResearchRequest, background: BackgroundTasks):
        store.brand_engine(brand_id).dispose()
        with store.GlobalSession.begin() as session:
            job = GlobalRepository(session).add_job(brand_id=brand_id, kind="BRAND_RESEARCH",
                payload={"website_url": str(payload.website_url), "company_name": payload.company_name})
        if app.state.run_background_jobs:
            background.add_task(run_onboarding_job, store, job.id, brand_id, str(payload.website_url), payload.company_name)
        return {"job_id": job.id, "status": job.status}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        with store.GlobalSession() as session:
            row = session.get(Job, job_id)
            if not row: raise HTTPException(404, "job not found")
            return {"id": row.id, "brand_id": row.brand_id, "kind": row.kind, "status": row.status, "result": row.result}

    @app.get("/api/brands/{brand_id}/claims")
    def list_claims(brand_id: str):
        with store.brand_session(brand_id) as session:
            claims = list(session.scalars(select(BrandClaim).order_by(BrandClaim.field_key, BrandClaim.created_at)))
            sources = {row.id: row for row in session.scalars(select(Source))}
            return [{"id": c.id, "field_key": c.field_key, "value": c.value["value"], "origin": c.origin,
                     "confidence": c.confidence, "status": c.status, "quoted_span": c.quoted_span,
                     "source": ({"id": sources[c.source_id].id, "title": sources[c.source_id].title,
                                 "url": sources[c.source_id].url} if c.source_id in sources else None),
                     "is_user_override": c.is_user_override} for c in claims]

    @app.post("/api/brands/{brand_id}/claims/{claim_id}/approve")
    def approve_claim(brand_id: str, claim_id: str, payload: ClaimDecision):
        with store.brand_session(brand_id) as session:
            claim = session.get(BrandClaim, claim_id)
            if not claim: raise HTTPException(404, "claim not found")
            claim.status, claim.approved_by, claim.approved_at = "APPROVED", payload.approved_by, datetime.now(UTC)
            return {"id": claim.id, "status": claim.status}

    @app.post("/api/brands/{brand_id}/claims/{claim_id}/override", status_code=201)
    def override_claim(brand_id: str, claim_id: str, payload: ClaimOverride):
        with store.brand_session(brand_id) as session:
            original = session.get(BrandClaim, claim_id)
            if not original: raise HTTPException(404, "claim not found")
            original.status = "SUPERSEDED"
            row = ClaimRepository(session).add(field_key=original.field_key, value=payload.value, origin="inferred",
                confidence=1, user_override=True, approved_by=payload.approved_by, supersedes_claim_id=original.id)
            return {"id": row.id, "value": row.value["value"], "status": row.status, "is_user_override": True}

    @app.post("/api/brands/{brand_id}/knowledge-gaps", status_code=201)
    def answer_gap(brand_id: str, payload: KnowledgeGapAnswer):
        with store.brand_session(brand_id) as session:
            row = ClaimRepository(session).add(field_key=f"user_answer:{payload.question}", value=payload.answer,
                origin="inferred", confidence=1, user_override=True, approved_by=payload.approved_by)
            return {"claim_id": row.id, "status": row.status}

    @app.post("/api/brands/{brand_id}/profile/approve")
    def approve_profile(brand_id: str, payload: ClaimDecision):
        with store.brand_session(brand_id) as session:
            latest = session.scalar(select(BrandProfile).order_by(BrandProfile.version.desc()).limit(1))
            if not latest: raise HTTPException(409, "no profile to approve")
            row = VersionRepository(session).append_profile(status="APPROVED", document=latest.document, created_by=payload.approved_by)
            return {"id": row.id, "version": row.version, "status": row.status}

    @app.get("/api/brands/{brand_id}/profile")
    def get_profile(brand_id: str):
        with store.brand_session(brand_id) as session:
            row = session.scalar(select(BrandProfile).order_by(BrandProfile.version.desc()).limit(1))
            if not row: raise HTTPException(404, "profile not found")
            return {"id": row.id, "version": row.version, "status": row.status, "document": row.document}

    @app.get("/api/brands/{brand_id}/design-system")
    def get_design_system(brand_id: str):
        with store.brand_session(brand_id) as session:
            row = session.scalar(select(BrandDesignSystem).order_by(BrandDesignSystem.version.desc()).limit(1))
            if not row: raise HTTPException(404, "design system not found")
            return _design_dict(row)

    @app.put("/api/brands/{brand_id}/design-system")
    def update_design_system(brand_id: str, payload: DesignSystemUpdate):
        with store.brand_session(brand_id) as session:
            row = VersionRepository(session).append_design_system(status="DRAFT", document=payload.model_dump(exclude={"created_by"}),
                                                                  created_by=payload.created_by)
            return _design_dict(row)

    @app.post("/api/brands/{brand_id}/design-system/approve")
    def approve_design_system(brand_id: str, payload: ClaimDecision):
        with store.brand_session(brand_id) as session:
            latest = session.scalar(select(BrandDesignSystem).order_by(BrandDesignSystem.version.desc()).limit(1))
            if not latest: raise HTTPException(409, "no design system to approve")
            document = {"colors": latest.colors, "font_pairings": latest.font_pairings,
                        "logo_safe_zones": latest.logo_safe_zones, "layout_templates": latest.layout_templates,
                        "flux_style_prefix": latest.flux_style_prefix, "flux_constraints": latest.flux_constraints}
            row = VersionRepository(session).append_design_system(status="APPROVED", document=document, created_by=payload.approved_by)
            return _design_dict(row)

    @app.post("/api/brands/{brand_id}/assets", status_code=201)
    async def upload_asset(brand_id: UUID, file: UploadFile = File(...), kind: str = "product_photo"):
        identifier = str(brand_id)
        store.brand_engine(identifier).dispose()
        suffix = Path(file.filename or "upload.bin").suffix.lower()
        incoming = settings.brand_dir(identifier).resolve() / "incoming"
        if incoming.resolve() != incoming:
            raise HTTPException(409, "the brand upload directory cannot be redirected")
        incoming.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(suffix=suffix, dir=incoming)
        os.close(descriptor)
        temp_path = Path(temporary)
        try:
            size = 0
            with temp_path.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 25 * 1024 * 1024: raise HTTPException(413, "asset exceeds 25 MiB")
                    output.write(chunk)
            with store.brand_session(identifier) as session:
                embeddings = E5EmbeddingProvider(
                    settings.directory("BRANDAI_EMBEDDINGS_DIR", "models/embeddings/multilingual-e5-base"))
                root = settings.brand_assets_dir(identifier).resolve()
                if root != settings.brand_dir(identifier).resolve() / "assets":
                    raise HTTPException(409, "the brand asset directory cannot be redirected")
                try:
                    row = MaterialIngestor(session, root, embeddings).ingest_asset(temp_path, kind=kind)
                except (ValueError, OSError) as exc:
                    raise HTTPException(422, "unsupported or invalid image asset") from exc
                return {"id": row.id, "kind": row.kind, "path": row.path, "metadata": row.metadata_json}
        finally:
            temp_path.unlink(missing_ok=True)

    @app.get("/api/brands/{brand_id}/assets")
    def list_assets(brand_id: str):
        with store.brand_session(brand_id) as session:
            return [{"id": row.id, "kind": row.kind, "path": row.path, "metadata": row.metadata_json}
                    for row in session.scalars(select(Asset).order_by(Asset.created_at.desc()))]

    font_files = {"heebo": "heebo/Heebo.ttf", "assistant": "assistant/Assistant.ttf",
                  "rubik": "rubik/Rubik.ttf", "noto-sans-hebrew": "notosanshebrew/NotoSansHebrew.ttf",
                  "inter": "inter/Inter.ttf"}

    @app.get("/api/fonts/{font_key}")
    def font_file(font_key: str):
        relative = font_files.get(font_key)
        if not relative: raise HTTPException(404, "font not found")
        return FileResponse(settings.directory("BRANDAI_FONT_DIR", "fonts") / relative, media_type="font/ttf")

    @app.post("/api/brands/{brand_id}/on-demand-photo-briefs", status_code=201)
    def create_photo_brief(brand_id: str, payload: PhotoBriefCreate):
        with store.brand_session(brand_id) as session:
            row = OnDemandPhotoRepository(session).create(**payload.model_dump())
            return {"id": row.id, "status": row.status, "treatment_mode": row.treatment_mode,
                    "platform": row.platform, "source_asset_id": row.source_asset_id}

    app.include_router(planning_router(store, strategy_planner or DeterministicStrategyPlanner()))
    if content_provider is None:
        provider_mode = os.getenv("BRANDAI_CONTENT_PROVIDER", "mock")
        if provider_mode == "local" and os.name == "nt":
            content_provider = QwenContentProvider(local_llm_worker(settings))
        elif provider_mode == "mock":
            content_provider = MockDeterministicContentProvider()
        else:
            raise ValueError("BRANDAI_CONTENT_PROVIDER must be mock or local (Windows only)")
    if content_embeddings is None:
        content_embeddings = (
            E5EmbeddingProvider(settings.directory("BRANDAI_EMBEDDINGS_DIR", "models/embeddings/multilingual-e5-base"))
            if isinstance(content_provider, QwenContentProvider) else HashEmbeddingProvider()
        )
    topic_service = TopicResearchService(
        store, topic_search or DDGSSearchProvider(), topic_crawler or PublicTopicCrawler(),
        enabled=os.getenv("BRANDAI_TOPIC_RESEARCH", "live") != "offline",
    )
    content_service = ContentService(store, content_provider, content_embeddings,
                                     content_diversity or DiversitySettings(), research=topic_service)
    app.state.content_service = content_service
    app.include_router(content_router(content_service))
    app.include_router(topic_router(topic_service))
    visual_service = VisualService(
        store, visual_pipeline or VisualPipeline(GradientBackgroundProvider(), EllipseSegmentationProvider()),
        ChromiumRenderer(settings.directory("BRANDAI_FONT_DIR", "fonts")),
    )
    app.include_router(visual_router(visual_service))
    web_dist = Path(__file__).resolve().parents[3] / "web" / "dist"
    if web_dist.exists(): app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


