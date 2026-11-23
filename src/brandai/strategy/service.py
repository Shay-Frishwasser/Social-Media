from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import TypeVar

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from brandai.db.base import utcnow
from brandai.db.brand_models import CadencePlan, OnDemandPhotoBrief, PlannedContentSlot, Strategy
from brandai.errors import GenerationBlockedError, InvalidStateTransitionError, NotFoundError
from brandai.repositories import PlanningDeliveryRepository, VersionRepository
from brandai.store import BrandAIStore

from .calendar import JERUSALEM, Slot, end_date, generate_slots, utc_naive
from .generator import StrategyPlanner, approved_evidence, build_strategy, effective_weights
from .models import (
    Approval, CadenceDocument, CadenceResponse, CalendarSlot, LegacyPlanningResponse, PhotoSlotRequest, PlanRequest,
    StrategyDocument, StrategyResponse, StrategyUpdate, VersionReference, VersionSummary,
)
from .rules import BrandPlaybooks


PlanningRow = TypeVar("PlanningRow", Strategy, CadencePlan)


def _summary(row: Strategy | CadencePlan) -> VersionSummary:
    return VersionSummary(id=row.id, version=row.version, status=row.status,
                          created_at=row.created_at.replace(tzinfo=UTC))


class BrandPlanning:
    """Operate on a single brand session. Write callers hold a SQLite immediate transaction."""

    def __init__(self, store: BrandAIStore, session: Session, planner: StrategyPlanner):
        self.session, self.planner = session, planner
        self.repository = PlanningDeliveryRepository(session)
        self.playbooks = BrandPlaybooks(store, session)

    def row(self, table: type[PlanningRow], row_id: str) -> PlanningRow:
        row = self.session.get(table, row_id)
        if row is None:
            raise NotFoundError(f"{table.__tablename__} version not found")
        return row

    def versions(self, table: type[Strategy] | type[CadencePlan]) -> list[VersionSummary]:
        return [_summary(row) for row in self.session.scalars(select(table).order_by(table.version.desc()))]

    def view(self, row: Strategy | CadencePlan) -> StrategyResponse | CadenceResponse | LegacyPlanningResponse:
        if isinstance(row, Strategy) and row.document.get("schema_version") == 1:
            return self.strategy_response(row)
        if isinstance(row, CadencePlan) and row.document.get("schema_version") == 1:
            return self.cadence_response(row)
        return LegacyPlanningResponse(**_summary(row).model_dump(), document=row.document)

    @staticmethod
    def strategy_response(row: Strategy) -> StrategyResponse:
        try:
            document = StrategyDocument.model_validate(row.document)
        except ValidationError as exc:
            raise InvalidStateTransitionError("this record predates structured strategies; generate a new version") from exc
        return StrategyResponse(**_summary(row).model_dump(), document=document)

    def _check_strategy_evidence(self, document: StrategyDocument) -> None:
        profile, design = VersionRepository(self.session).assert_generation_ready()
        if document.profile.id != profile.id or document.design_system.id != design.id:
            raise GenerationBlockedError("the profile or design system changed; regenerate the strategy")
        evidence = {claim.id: claim for claim in approved_evidence(self.session)}
        if any(evidence.get(claim.id) != claim for claim in document.claims):
            raise GenerationBlockedError("approved evidence changed; regenerate the strategy")

    def generate_strategy(self, payload: StrategyUpdate, previous_id: str | None = None) -> StrategyResponse:
        VersionRepository(self.session).assert_generation_ready()
        if previous_id:
            self.row(Strategy, previous_id)
        playbooks = {platform: self.playbooks.resolve(platform) for platform in sorted(payload.platforms)}
        document = build_strategy(self.session, payload, playbooks, self.planner, previous_id=previous_id)
        row = self.repository.add_strategy(document.model_dump(mode="json"))
        return self.strategy_response(row)

    def approve_strategy(self, row_id: str, decision: Approval) -> StrategyResponse:
        row = self.row(Strategy, row_id)
        response = self.strategy_response(row)
        if row.status == "APPROVED":
            return response
        if row.status != "DRAFT":
            raise InvalidStateTransitionError("only draft strategies can be approved")
        self._check_strategy_evidence(response.document)
        if any(self.playbooks.resolve(platform) != snapshot for platform, snapshot in response.document.playbooks.items()):
            raise GenerationBlockedError("platform rules changed; regenerate the strategy")
        if self.session.scalar(select(Strategy.id).where(Strategy.status == "APPROVED", Strategy.version > row.version)):
            raise InvalidStateTransitionError("a newer strategy has already been approved")
        response.document.approved_by = decision.approved_by
        response.document.approved_at = utcnow()
        row.status, row.document = "APPROVED", response.document.model_dump(mode="json")
        return self.strategy_response(row)

    def _approved_strategy(self) -> StrategyResponse:
        row = self.session.scalar(select(Strategy).where(Strategy.status == "APPROVED").order_by(Strategy.version.desc()).limit(1))
        if row is None:
            raise GenerationBlockedError("approve a strategy before planning cadence")
        response = self.strategy_response(row)
        self._check_strategy_evidence(response.document)
        return response

    @staticmethod
    def _plan_document(row: CadencePlan) -> CadenceDocument:
        try:
            return CadenceDocument.model_validate(row.document)
        except ValidationError as exc:
            raise InvalidStateTransitionError("this record predates structured cadence plans; generate a new version") from exc

    def _slots(self, row: CadencePlan) -> list[PlannedContentSlot]:
        return list(self.session.scalars(select(PlannedContentSlot).where(
            PlannedContentSlot.cadence_plan_id == row.id,
        ).order_by(PlannedContentSlot.scheduled_at_utc)))

    def cadence_response(self, row: CadencePlan) -> CadenceResponse:
        return CadenceResponse(**_summary(row).model_dump(), document=self._plan_document(row), slots=[
            CalendarSlot(
                id=slot.id, platform=slot.platform, pillar_id=slot.pillar_id, claim_id=slot.claim_id,
                depth=slot.depth, photo_brief_id=slot.photo_brief_id,
                scheduled_at=slot.scheduled_at_utc.replace(tzinfo=UTC).astimezone(JERUSALEM),
                kind="on_demand" if slot.photo_brief_id else "cadence",
            ) for slot in self._slots(row)
        ])

    def _active_reservations(self, document: CadenceDocument) -> tuple[set[datetime], set[str]]:
        start, end = document.request.start_date, document.end_date_exclusive
        platforms = set(document.platforms)
        if document.replaces_id and self.row(CadencePlan, document.replaces_id).status != "APPROVED":
            raise InvalidStateTransitionError("this plan's approved predecessor has already been replaced")
        active = select(CadencePlan).where(CadencePlan.status == "APPROVED", CadencePlan.id != (document.replaces_id or ""))
        for row in self.session.scalars(active):
            if row.document.get("schema_version") != 1:
                continue
            other = self._plan_document(row)
            if platforms.intersection(other.platforms) and start < other.end_date_exclusive and other.request.start_date < end:
                raise GenerationBlockedError(f"calendar period overlaps approved plan {row.id} for a selected platform")
        joined = select(PlannedContentSlot).join(CadencePlan).where(
            CadencePlan.status == "APPROVED", CadencePlan.id != (document.replaces_id or ""),
        )
        # Widen the query by one day on each side to cover local offsets and midnight transitions.
        in_window = joined.where(
            PlannedContentSlot.scheduled_at_utc >= datetime.combine(start, time.min) - timedelta(days=1),
            PlannedContentSlot.scheduled_at_utc < datetime.combine(end, time.min) + timedelta(days=1),
        )
        photos = self.session.scalars(joined.with_only_columns(PlannedContentSlot.photo_brief_id).where(
            PlannedContentSlot.photo_brief_id.is_not(None),
        ))
        return {slot.scheduled_at_utc for slot in self.session.scalars(in_window)}, set(photos)

    def _persist_cadence(self, document: CadenceDocument, slots: list[Slot]) -> CadenceResponse:
        row = self.repository.add_cadence_plan(document.model_dump(mode="json", exclude_unset=True))
        self.session.add_all([
            PlannedContentSlot(
                cadence_plan_id=row.id, platform=slot.platform, scheduled_at_utc=slot.at_utc,
                pillar_id=slot.pillar_id, claim_id=slot.claim_id, depth=slot.depth,
                photo_brief_id=slot.photo_brief_id,
            ) for slot in slots
        ])
        self.session.flush()
        return self.cadence_response(row)

    @staticmethod
    def _slot(row: PlannedContentSlot) -> Slot:
        return Slot(row.platform, row.scheduled_at_utc, row.pillar_id, row.claim_id, row.depth, row.photo_brief_id)

    @staticmethod
    def _valid_photo_slots(document: CadenceDocument, photos: list[Slot],
                           busy: set[datetime], reserved_photos: set[str]) -> None:
        for photo in photos:
            day = photo.at_utc.replace(tzinfo=UTC).astimezone(JERUSALEM).date()
            if photo.platform not in document.platforms or not document.request.start_date <= day < document.end_date_exclusive:
                raise GenerationBlockedError("on-demand slot must fall within the plan period and active platforms")
            if photo.at_utc in busy or photo.photo_brief_id in reserved_photos:
                raise GenerationBlockedError("on-demand time or photo brief is already reserved")

    def generate_cadence(self, payload: PlanRequest, previous_id: str | None = None) -> CadenceResponse:
        strategy = self._approved_strategy()
        platforms = sorted(payload.platforms or strategy.document.platforms)
        if set(platforms) - set(strategy.document.platforms) or set(payload.overrides) - set(platforms):
            raise GenerationBlockedError("cadence platforms and overrides must come from the approved strategy")
        playbooks = {platform: self.playbooks.resolve(platform, payload.overrides.get(platform)) for platform in platforms}
        pillar_ids = [pillar.id for pillar in strategy.document.pillars]
        weights = {}
        for platform, playbook in playbooks.items():
            per_plan = payload.overrides.get(platform)
            explicit = per_plan.pillar_weights if per_plan else None
            weights[platform] = effective_weights(
                pillar_ids, playbook, explicit or strategy.document.pillar_weight_overrides.get(platform),
            )
        previous = self.row(CadencePlan, previous_id) if previous_id else None
        replaces_id = self._replacement(previous) if previous else None
        document = CadenceDocument(
            schema_version=1,
            strategy=VersionReference(id=strategy.id, version=strategy.version), request=payload,
            end_date_exclusive=end_date(payload.start_date, payload.period), platforms=platforms,
            playbooks=playbooks, pillar_weights=weights, previous_id=previous_id, replaces_id=replaces_id,
        )
        photos = [self._slot(slot) for slot in self._slots(previous) if slot.photo_brief_id] if previous else []
        busy, reserved = self._active_reservations(document)
        self._valid_photo_slots(document, photos, busy, reserved)
        slots = generate_slots(document, strategy.document, busy | {slot.at_utc for slot in photos})
        if not slots:
            raise GenerationBlockedError("the selected period has no cadence slots")
        return self._persist_cadence(document, slots + photos)

    def _replacement(self, row: CadencePlan) -> str | None:
        if row.status == "APPROVED":
            return row.id
        if row.status != "DRAFT":
            raise InvalidStateTransitionError("only draft or active approved cadence plans can be revised")
        if row.document.get("schema_version") != 1:
            return None
        return self._plan_document(row).replaces_id

    def _cadence_is_current(self, document: CadenceDocument) -> None:
        strategy = self._approved_strategy()
        if strategy.id != document.strategy.id:
            raise GenerationBlockedError("the active strategy changed; regenerate the cadence plan")
        for platform, snapshot in document.playbooks.items():
            if self.playbooks.resolve(platform, document.request.overrides.get(platform)) != snapshot:
                raise GenerationBlockedError("platform rules changed; regenerate the cadence plan")

    def inject_photo(self, plan_id: str, payload: PhotoSlotRequest) -> CadenceResponse:
        row = self.row(CadencePlan, plan_id)
        replaces_id = self._replacement(row)
        document = self._plan_document(row)
        photo = self.session.get(OnDemandPhotoBrief, str(payload.photo_brief_id))
        if photo is None:
            raise NotFoundError("photo brief not found in this brand")
        document = document.model_copy(update={
            "previous_id": row.id, "replaces_id": replaces_id, "approved_by": None, "approved_at": None,
            "request": document.request.model_copy(update={"created_by": payload.created_by}),
        })
        self._cadence_is_current(document)
        slots = [self._slot(slot) for slot in self._slots(row)]
        inserted = Slot(photo.platform, utc_naive(payload.scheduled_at), photo_brief_id=photo.id)
        busy, reserved = self._active_reservations(document)
        self._valid_photo_slots(document, [inserted], busy | {slot.at_utc for slot in slots},
                                reserved | {slot.photo_brief_id for slot in slots if slot.photo_brief_id})
        return self._persist_cadence(document, slots + [inserted])

    def approve_cadence(self, row_id: str, decision: Approval) -> CadenceResponse:
        row = self.row(CadencePlan, row_id)
        document = self._plan_document(row)
        if row.status == "APPROVED":
            return self.cadence_response(row)
        if row.status != "DRAFT":
            raise InvalidStateTransitionError("only draft cadence plans can be approved")
        self._cadence_is_current(document)
        busy, reserved = self._active_reservations(document)
        slots = [self._slot(slot) for slot in self._slots(row)]
        if any(slot.at_utc in busy for slot in slots):
            raise GenerationBlockedError("a publishing time has already been reserved")
        self._valid_photo_slots(document, [slot for slot in slots if slot.photo_brief_id], busy, reserved)
        if document.replaces_id:
            self.row(CadencePlan, document.replaces_id).status = "SUPERSEDED"
        document.approved_by, document.approved_at = decision.approved_by, utcnow()
        row.status, row.document = "APPROVED", document.model_dump(mode="json", exclude_unset=True)
        self.session.flush()
        return self.cadence_response(row)
