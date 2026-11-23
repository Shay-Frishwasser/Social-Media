from __future__ import annotations

import os

from brandai import BrandAIStore
from brandai.content.models import ContentGenerate
from brandai.content.service import ContentService
from brandai.jobs.aws import AWSEC2OnDemandGPUWorker
from brandai.jobs.models import ContentPayload, VisualRerenderPayload
from brandai.jobs.queue import JobHandler, JobQueue, JobRunner
from brandai.publishing.connections import ConnectionService, TokenRefresher, TokenVault
from brandai.publishing.email import EmailDeliveryAdapter, SMTPConfig, SMTPEmailDelivery
from brandai.publishing.service import PublisherFactory, PublishingService
from brandai.publishing.social import LinkedInPublisher, LinkedInTokenRefresher, MetaGraphPublisher, PublicMediaURLProvider
from brandai.visual_studio.models import RenderOptions, VisualRender
from brandai.visual_studio.service import VisualService

from .services import execute_onboarding


def configure_delivery(
    store: BrandAIStore, queue: JobQueue, *,
    email: EmailDeliveryAdapter | None, vault: TokenVault | None,
    publishers: dict[str, PublisherFactory] | None, refreshers: dict[str, TokenRefresher] | None,
    media_urls: PublicMediaURLProvider | None,
) -> PublishingService:
    if email is None and (host := os.getenv("BRANDAI_SMTP_HOST")):
        tls = os.getenv("BRANDAI_SMTP_TLS", "implicit")
        if tls not in {"implicit", "starttls"}:
            raise ValueError("BRANDAI_SMTP_TLS must be implicit or starttls")
        email = SMTPEmailDelivery(SMTPConfig(
            host=host, sender=os.environ["BRANDAI_SMTP_SENDER"], port=int(os.getenv("BRANDAI_SMTP_PORT", "465")),
            username=os.getenv("BRANDAI_SMTP_USERNAME"), password=os.getenv("BRANDAI_SMTP_PASSWORD"),
            implicit_tls=tls == "implicit",
        ))
    if vault is None and (key := os.getenv("BRANDAI_OAUTH_ENCRYPTION_KEY")):
        vault = TokenVault(key)
    if publishers is None:
        publishers = {}
        if meta_version := os.getenv("BRANDAI_META_GRAPH_VERSION"):
            publishers["meta"] = lambda connection, tokens: MetaGraphPublisher(
                connection, tokens, meta_version, media_urls=media_urls)
        if linkedin_version := os.getenv("BRANDAI_LINKEDIN_VERSION"):
            publishers["linkedin"] = lambda connection, tokens: LinkedInPublisher(connection, tokens, linkedin_version)
    if refreshers is None:
        refreshers = {}
        if (client_id := os.getenv("BRANDAI_LINKEDIN_CLIENT_ID")) and (secret := os.getenv("BRANDAI_LINKEDIN_CLIENT_SECRET")):
            refreshers["linkedin"] = LinkedInTokenRefresher(client_id, secret)
    return PublishingService(store, queue, ConnectionService(store, vault, refreshers), email, publishers)


def configure_runner(
    queue: JobQueue, content: ContentService, visual: VisualService, delivery: PublishingService,
    aws: AWSEC2OnDemandGPUWorker | None, overrides: dict[str, JobHandler] | None,
) -> JobRunner:
    def generate(job, progress):
        payload = ContentPayload.model_validate(job.payload)
        progress(20)
        result, _ = content.generate(
            job.brand_id, ContentGenerate.model_validate(payload.model_dump(exclude={"slot_id", "brief_id"})),
            slot_id=str(payload.slot_id) if payload.slot_id else None, brief_id=str(payload.brief_id) if payload.brief_id else None,
        )
        return {"content_id": str(result.id), "status": result.status}

    def render(job, progress):
        progress(20)
        previous = None
        if "previous_id" in job.payload:
            request = VisualRerenderPayload.model_validate(job.payload)
            previous = str(request.previous_id)
            options = RenderOptions.model_validate(request.model_dump(exclude={"previous_id"}, exclude_none=True))
        else:
            options = VisualRender.model_validate(job.payload)
        result = visual.render(job.brand_id, options, previous_id=previous, operation_id=job.id)
        return {"visual_asset_id": str(result.id), "revision_id": str(result.content_revision_id) if result.content_revision_id else None}

    def onboarding(job, progress):
        return execute_onboarding(queue.store, job, progress, aws)

    return JobRunner(queue, {
        "onboarding_research": onboarding, "content_generation": generate, "visual_render": render,
        **(overrides or {}), "publish": delivery.run_dispatch,
    })
