from uuid import UUID

from fastapi import APIRouter
from fastapi.responses import Response

from brandai.publishing.connections import ConnectionService
from brandai.publishing.models import (
    ConnectionResponse, ConnectionWrite, DispatchRequest, ExportRequest, GateReport, OAuthComplete, OAuthState,
    PublishResponse,
)
from brandai.publishing.service import PublishingService


def publishing_router(service: PublishingService, connections: ConnectionService) -> APIRouter:
    router = APIRouter(prefix="/api/brands/{brand_id}/publishing", tags=["Publishing"])

    @router.get("/gate/{content_id}")
    def gate(brand_id: UUID, content_id: UUID, visual_asset_id: UUID | None = None) -> GateReport:
        return service.gate.check(str(brand_id), str(content_id), str(visual_asset_id) if visual_asset_id else None)

    @router.post("/export/{content_id}", status_code=201)
    def export(brand_id: UUID, content_id: UUID, payload: ExportRequest = ExportRequest()) -> PublishResponse:
        visual = str(payload.visual_asset_id) if payload.visual_asset_id else None
        return service.export(str(brand_id), str(content_id), visual)

    @router.get("/export/{content_id}", response_class=Response,
                responses={200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}})
    def download(brand_id: UUID, content_id: UUID, export_id: UUID | None = None) -> Response:
        bundle = service.download(str(brand_id), str(content_id), str(export_id) if export_id else None)
        return Response(
            bundle.data, media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="brandai-{content_id}.zip"', "Cache-Control": "no-store"},
        )

    @router.post("/dispatch/{content_id}", status_code=202)
    def dispatch(brand_id: UUID, content_id: UUID, payload: DispatchRequest) -> PublishResponse:
        return service.enqueue(str(brand_id), str(content_id), payload)

    @router.get("/records/{record_id}")
    def get_record(brand_id: UUID, record_id: UUID) -> PublishResponse:
        return service.get(str(brand_id), str(record_id))

    @router.get("/connections")
    def list_connections(brand_id: UUID) -> list[ConnectionResponse]:
        return connections.list(str(brand_id))

    @router.post("/connections")
    def save_connection(brand_id: UUID, payload: ConnectionWrite) -> ConnectionResponse:
        return connections.upsert(str(brand_id), payload)

    @router.post("/connections/{connection_id}/oauth-state")
    def issue_state(brand_id: UUID, connection_id: UUID, response: Response) -> OAuthState:
        response.headers["Cache-Control"] = "no-store"
        return connections.issue_state(str(brand_id), str(connection_id))

    @router.post("/connections/{connection_id}/complete")
    def complete_state(brand_id: UUID, connection_id: UUID, payload: OAuthComplete) -> ConnectionResponse:
        return connections.complete(str(brand_id), str(connection_id), payload)

    return router
