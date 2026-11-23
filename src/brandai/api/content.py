from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from brandai.content.models import ContentApproval, ContentGenerate, ContentPatch, ContentResponse, ContentStatus, ContentSummary, ValidationReport
from brandai.content.service import ContentService
from brandai.errors import GenerationBlockedError
from brandai.strategy.models import Platform


def content_router(service: ContentService, *, queue_only: bool = False) -> APIRouter:
    router = APIRouter(prefix="/api/brands/{brand_id}/content", tags=["Content"])

    def allow_direct():
        if queue_only:
            raise GenerationBlockedError("enqueue AWS content generation through the brand jobs route")

    @router.post("/generate/from-slot/{slot_id}", status_code=201, responses={200: {"model": ContentResponse}},
                 dependencies=[Depends(allow_direct)])
    def from_slot(brand_id: UUID, slot_id: UUID, response: Response,
                  payload: ContentGenerate = ContentGenerate()) -> ContentResponse:
        result, created = service.generate(str(brand_id), payload, slot_id=str(slot_id))
        response.status_code = 201 if created else 200
        return result

    @router.post("/generate/from-on-demand/{brief_id}", status_code=201, responses={200: {"model": ContentResponse}},
                 dependencies=[Depends(allow_direct)])
    def from_photo(brand_id: UUID, brief_id: UUID, response: Response,
                   payload: ContentGenerate = ContentGenerate()) -> ContentResponse:
        result, created = service.generate(str(brand_id), payload, brief_id=str(brief_id))
        response.status_code = 201 if created else 200
        return result

    @router.get("", include_in_schema=False)
    @router.get("/")
    def list_items(
        brand_id: UUID, platform: Platform | None = None, status: ContentStatus | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50, offset: Annotated[int, Query(ge=0)] = 0,
    ) -> list[ContentSummary]:
        return service.list_content(str(brand_id), platform=platform, status=status, limit=limit, offset=offset)

    @router.get("/{content_id}")
    def get_item(brand_id: UUID, content_id: UUID) -> ContentResponse:
        return service.get_content(str(brand_id), str(content_id))

    @router.post("/{content_id}/validate")
    def validate_item_draft(brand_id: UUID, content_id: UUID, payload: ContentPatch) -> ValidationReport:
        return service.validate_draft(str(brand_id), str(content_id), payload)

    @router.patch("/{content_id}")
    def edit_item(brand_id: UUID, content_id: UUID, payload: ContentPatch) -> ContentResponse:
        return service.patch(str(brand_id), str(content_id), payload)

    @router.post("/{content_id}/approve")
    def approve_item(brand_id: UUID, content_id: UUID, payload: ContentApproval) -> ContentResponse:
        return service.approve(str(brand_id), str(content_id), payload)

    return router
