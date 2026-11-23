from collections.abc import Iterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from brandai.db.brand_models import CadencePlan, Strategy
from brandai.errors import NotFoundError
from brandai.store import BrandAIStore
from brandai.strategy.generator import StrategyPlanner
from brandai.strategy.models import (
    Approval, CadenceResponse, EffectivePlaybook, LegacyPlanningResponse, PhotoSlotRequest, PlanRequest, Platform,
    PlatformOverrideUpdate, StrategyRequest, StrategyResponse, StrategyUpdate, VersionSummary,
)
from brandai.strategy.service import BrandPlanning


def planning_router(store: BrandAIStore, planner: StrategyPlanner) -> APIRouter:
    router = APIRouter(prefix="/api/brands/{brand_id}", tags=["Brand planning"])

    def read(brand_id: UUID) -> Iterator[BrandPlanning]:
        try:
            with store.brand_session(str(brand_id)) as session:
                yield BrandPlanning(store, session, planner)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def write(brand_id: UUID) -> Iterator[BrandPlanning]:
        try:
            with store.brand_session(str(brand_id), immediate=True) as session:
                yield BrandPlanning(store, session, planner)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    Read = Annotated[BrandPlanning, Depends(read)]
    Write = Annotated[BrandPlanning, Depends(write, scope="function")]

    @router.get("/strategies")
    def list_strategies(service: Read) -> list[VersionSummary]:
        return service.versions(Strategy)

    @router.post("/strategies", status_code=201)
    def generate_strategy(payload: StrategyRequest, service: Write) -> StrategyResponse:
        return service.generate_strategy(StrategyUpdate.model_validate(payload.model_dump()))

    @router.get("/strategies/{strategy_id}")
    def get_strategy(strategy_id: UUID, service: Read) -> StrategyResponse | LegacyPlanningResponse:
        return service.view(service.row(Strategy, str(strategy_id)))

    @router.put("/strategies/{strategy_id}", status_code=201)
    def revise_strategy(strategy_id: UUID, payload: StrategyUpdate, service: Write) -> StrategyResponse:
        return service.generate_strategy(payload, previous_id=str(strategy_id))

    @router.post("/strategies/{strategy_id}/approve")
    def approve_strategy(strategy_id: UUID, payload: Approval, service: Write) -> StrategyResponse:
        return service.approve_strategy(str(strategy_id), payload)

    @router.get("/cadence-plans")
    def list_cadence(service: Read) -> list[VersionSummary]:
        return service.versions(CadencePlan)

    @router.post("/cadence-plans", status_code=201)
    def generate_cadence(payload: PlanRequest, service: Write) -> CadenceResponse:
        return service.generate_cadence(payload)

    @router.get("/cadence-plans/{plan_id}")
    def get_cadence(plan_id: UUID, service: Read) -> CadenceResponse | LegacyPlanningResponse:
        return service.view(service.row(CadencePlan, str(plan_id)))

    @router.put("/cadence-plans/{plan_id}", status_code=201)
    def revise_cadence(plan_id: UUID, payload: PlanRequest, service: Write) -> CadenceResponse:
        return service.generate_cadence(payload, previous_id=str(plan_id))

    @router.post("/cadence-plans/{plan_id}/approve")
    def approve_cadence(plan_id: UUID, payload: Approval, service: Write) -> CadenceResponse:
        return service.approve_cadence(str(plan_id), payload)

    @router.post("/cadence-plans/{plan_id}/on-demand-slots", status_code=201)
    def inject_photo(plan_id: UUID, payload: PhotoSlotRequest, service: Write) -> CadenceResponse:
        return service.inject_photo(str(plan_id), payload)

    @router.get("/playbooks/{platform}")
    def get_playbook(platform: Platform, service: Read) -> EffectivePlaybook:
        return service.playbooks.resolve(platform)

    @router.put("/playbooks/{platform}", status_code=201)
    def override_playbook(platform: Platform, payload: PlatformOverrideUpdate, service: Write) -> EffectivePlaybook:
        return service.playbooks.override(platform, payload)

    return router
