from uuid import UUID

from fastapi import APIRouter, Response

from brandai.research.models import TopicRequest, TopicResponse
from brandai.research.service import TopicResearchService


def topic_router(service: TopicResearchService) -> APIRouter:
    router = APIRouter(prefix="/api/brands/{brand_id}/research/topics", tags=["Topic research"])

    @router.post("", status_code=201, responses={200: {"model": TopicResponse}})
    def run(brand_id: UUID, payload: TopicRequest, response: Response) -> TopicResponse:
        result, created = service.run(str(brand_id), payload)
        response.status_code = 201 if created else 200
        return result

    @router.get("")
    def list_topics(brand_id: UUID, content_id: UUID | None = None) -> list[TopicResponse]:
        return service.list(str(brand_id), str(content_id) if content_id else None)

    @router.get("/{topic_id}")
    def get(brand_id: UUID, topic_id: UUID) -> TopicResponse:
        return service.get(str(brand_id), str(topic_id))

    return router
