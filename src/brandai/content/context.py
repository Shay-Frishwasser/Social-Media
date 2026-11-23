from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from brandai.db.brand_models import CadencePlan, OnDemandPhotoBrief, PlannedContentSlot, Strategy
from brandai.errors import GenerationBlockedError, NotFoundError
from brandai.on_demand import PhotoTreatmentPlanner
from brandai.repositories import VersionRepository
from brandai.store import BrandAIStore
from brandai.strategy.calendar import allowed_claims
from brandai.strategy.generator import approved_evidence, evidence_text
from brandai.strategy.models import CadenceDocument, Depth, HebrewAddress, Platform, Register, StrategyDocument
from brandai.strategy.rules import BrandPlaybooks

from .editorial import copy_choices, format_rules
from .models import ContentContext, FluxDirective, Language, PhotoDirective, VisualDirective, VisualSelection


@dataclass(frozen=True)
class PreparedContent:
    strategy_id: str
    slot_id: str | None
    brief_id: str | None
    platform: Platform
    depth: Depth
    language: Language
    provenance: ContentContext
    facts: dict[str, str]
    event_note: str | None
    hooks: tuple[str, ...]
    ctas: tuple[str, ...]
    hashtags: tuple[str, ...]
    register: Register
    address: HebrewAddress
    template_ids: tuple[str, ...]
    default_visual: VisualDirective

    def directive(self, selection: VisualSelection) -> VisualDirective:
        return self.default_visual.model_copy(update={"selection": selection})


def _latest_strategy(session: Session) -> Strategy | None:
    return session.scalar(select(Strategy).where(Strategy.status == "APPROVED").order_by(
        Strategy.version.desc(),
    ).limit(1))


def _approved_plan(session: Session, slot: PlannedContentSlot) -> CadenceDocument:
    row = session.get(CadencePlan, slot.cadence_plan_id)
    if row is None or row.status != "APPROVED":
        raise GenerationBlockedError("the cadence slot requires an active approved plan")
    try:
        return CadenceDocument.model_validate(row.document)
    except ValidationError as exc:
        raise GenerationBlockedError("the cadence plan requires structured playbook provenance") from exc


def _slot_for_brief(session: Session, brief_id: str) -> PlannedContentSlot | None:
    return session.scalar(select(PlannedContentSlot).join(CadencePlan).where(
        PlannedContentSlot.photo_brief_id == brief_id, CadencePlan.status == "APPROVED",
    ))


def _template_ids(templates: list) -> tuple[str, ...]:
    identifiers = [item.get("id") if isinstance(item, dict) else item for item in templates]
    return tuple(dict.fromkeys(item for item in identifiers if isinstance(item, str) and 0 < len(item) <= 120))


def prepare_content(
    store: BrandAIStore, session: Session, *, slot_id: str | None = None, brief_id: str | None = None,
    language: Language | None = None, pinned_strategy_id: str | None = None,
    pinned_context: ContentContext | None = None,
) -> PreparedContent:
    slot = session.get(PlannedContentSlot, slot_id) if slot_id else None
    if slot_id and slot is None:
        raise NotFoundError("cadence slot not found in this brand")
    if slot:
        brief_id = slot.photo_brief_id
    brief = session.get(OnDemandPhotoBrief, brief_id) if brief_id else None
    if brief_id and brief is None:
        raise NotFoundError("photo brief not found in this brand")
    if slot is None and brief and pinned_context is None:
        slot = _slot_for_brief(session, brief.id)
    if slot is None and brief is None:
        raise GenerationBlockedError("content requires a cadence slot or photo brief")
    plan = _approved_plan(session, slot) if slot else None
    if plan:
        strategy = session.get(Strategy, plan.strategy.id)
    elif pinned_strategy_id:
        strategy = session.get(Strategy, pinned_strategy_id)
    else:
        strategy = _latest_strategy(session)
    if strategy is None or strategy.status != "APPROVED":
        raise GenerationBlockedError("content generation requires an approved strategy")
    try:
        document = StrategyDocument.model_validate(strategy.document)
    except ValidationError as exc:
        raise GenerationBlockedError("generate a structured strategy before creating content") from exc

    profile, design = VersionRepository(session).assert_generation_ready()
    if document.profile.id != profile.id or document.design_system.id != design.id:
        raise GenerationBlockedError("the profile or design system changed; refresh strategy and cadence")
    if (plan and plan.strategy.version != strategy.version) or (
        pinned_strategy_id and pinned_strategy_id != strategy.id
    ):
        raise GenerationBlockedError("the source strategy no longer matches the content item")

    platform = slot.platform if slot else brief.platform
    if platform not in document.platforms or (brief and brief.platform != platform):
        raise GenerationBlockedError("the platform must belong to the approved strategy and match the photo brief")
    if plan:
        playbook = plan.playbooks.get(platform)
    elif pinned_context:
        playbook = pinned_context.playbook
    else:
        playbook = BrandPlaybooks(store, session).resolve(platform)
    if playbook is None:
        raise GenerationBlockedError("the approved calendar lacks a playbook for this platform")
    rules = pinned_context.format_rules if pinned_context else format_rules(playbook)

    eligible = allowed_claims(document, playbook.overrides.excluded_topics)
    pillar = None
    if slot and not brief:
        pillar = next((item for item in document.pillars if item.id == slot.pillar_id), None)
        if pillar is None or slot.claim_id not in eligible.get(pillar.id, ()):
            raise GenerationBlockedError("the planned claim is outside the approved strategy or platform rules")
        claim_ids = [slot.claim_id, *(claim_id for claim_id in eligible[pillar.id] if claim_id != slot.claim_id)]
    else:
        claim_ids = list(dict.fromkeys(claim_id for ids in eligible.values() for claim_id in ids))
    current = {claim.id: claim for claim in approved_evidence(session)}
    approved_snapshot = {claim.id: claim for claim in document.claims}
    facts = {
        claim_id: " ".join(evidence_text(current[claim_id]).split()) for claim_id in claim_ids
        if claim_id in current and current[claim_id] == approved_snapshot.get(claim_id) and evidence_text(current[claim_id]).strip()
    }
    required_claim = slot.claim_id if slot and not brief else None
    if required_claim and required_claim not in facts:
        raise GenerationBlockedError("the planned claim is no longer effective and approved")

    depth = (slot.depth or "standard") if slot else "standard"
    event_note = " ".join(brief.prompt.split()) if brief else None
    if language is None:
        primary_text = event_note or facts.get(required_claim, "") or next(iter(facts.values()), "")
        language = "he" if re.search(r"[\u05d0-\u05ea]", primary_text) else "en"
    hooks, ctas, hashtags = copy_choices(playbook, depth, language, document.messaging_register, document.hebrew_address)
    templates = _template_ids(pillar.art_direction.allowed_templates if pillar else design.layout_templates)
    selection = VisualSelection(template_id=templates[0] if templates else None)
    if brief:
        recipe = PhotoTreatmentPlanner().plan(brief.treatment_mode, platform=platform, design_system_version=design.version)
        visual = PhotoDirective(
            selection=selection, source_asset_id=brief.source_asset_id, mode=recipe.mode,
            preserve_source_pixels=recipe.preserve_source_pixels, operations=list(recipe.operations),
            requires_gpu_capabilities=sorted(recipe.requires_gpu_capabilities), output_contract=recipe.output_contract,
        )
    else:
        direction = pillar.art_direction
        visual = FluxDirective(
            selection=selection.model_copy(update={"subject": "abstract_brand_scene"}),
            style_prefix=direction.flux_style_prefix, color_roles=direction.color_roles,
            constraints={**direction.flux_constraints, "text_free": True, "no_letters": True, "no_logos": True},
        )
    return PreparedContent(
        strategy_id=strategy.id, slot_id=slot.id if slot else None, brief_id=brief.id if brief else None,
        platform=platform, depth=depth, language=language,
        provenance=ContentContext(
            profile=document.profile, design_system=document.design_system, playbook=playbook, format_rules=rules,
            required_claim_id=required_claim, cadence_plan_id=slot.cadence_plan_id if slot else None,
            pillar_id=pillar.id if pillar else None,
        ),
        facts=facts, event_note=event_note, hooks=hooks, ctas=ctas, hashtags=hashtags,
        register=document.messaging_register, address=document.hebrew_address, template_ids=templates,
        default_visual=visual,
    )
