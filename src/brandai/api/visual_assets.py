from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from brandai.errors import GenerationBlockedError
from brandai.visual_studio.models import RenderOptions, VisualRender, VisualResponse
from brandai.visual_studio.service import VisualService


def visual_router(service: VisualService, *, queue_only: bool = False) -> APIRouter:
    router = APIRouter(prefix="/api/brands/{brand_id}/visual-assets", tags=["Visual assets"])

    def allow_direct():
        if queue_only:
            raise GenerationBlockedError("enqueue AWS visual rendering through the brand jobs route")

    @router.post("/render", status_code=201, dependencies=[Depends(allow_direct)])
    def render(brand_id: UUID, payload: VisualRender) -> VisualResponse:
        return service.render(str(brand_id), payload)

    @router.get("")
    def list_assets(brand_id: UUID) -> list[VisualResponse]:
        return service.list(str(brand_id))

    @router.get("/{asset_id}")
    def get(brand_id: UUID, asset_id: UUID) -> VisualResponse:
        return service.get(str(brand_id), str(asset_id))

    @router.post("/{asset_id}/rerender", status_code=201, dependencies=[Depends(allow_direct)])
    def rerender(brand_id: UUID, asset_id: UUID, payload: RenderOptions = RenderOptions()) -> VisualResponse:
        return service.render(str(brand_id), payload, previous_id=str(asset_id))

    @router.get("/{asset_id}/download", response_class=Response)
    def download(brand_id: UUID, asset_id: UUID, format: Literal["png", "pdf", "zip"] = "png",
                 page: Annotated[int, Query(ge=1, le=10)] = 1) -> Response:
        data, artifact = service.download(str(brand_id), str(asset_id), format, page)
        types = {"png": "image/png", "pdf": "application/pdf", "zip": "application/zip"}
        return Response(data, media_type=types[format], headers={
            "Content-Disposition": f'attachment; filename="{artifact.name}"', "ETag": f'"{artifact.sha256}"',
            "Cache-Control": "no-store",
        })

    return router
