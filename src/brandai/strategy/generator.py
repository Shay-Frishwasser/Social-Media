from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, get_args

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from brandai.db.brand_models import BrandClaim, BrandDesignSystem, Source, SourceChunk
from brandai.errors import GenerationBlockedError
from brandai.repositories import ClaimRepository, VersionRepository

from .models import (
    ArtDirection, ClaimEvidence, ContentPillar, EffectivePlaybook, HebrewAddress, MessagingAngle,
    PillarSelection, Platform, Register, StrategyDocument, StrategyUpdate, VersionReference,
)


@dataclass(frozen=True, slots=True)
class PlanningContext:
    claims: tuple[ClaimEvidence, ...]
    platforms: tuple[Platform, ...]


class StrategyPlanner(Protocol):
    """Select approved claim IDs for editorial pillars. The generator renders and validates copy."""
    def plan(self, context: PlanningContext) -> list[PillarSelection]: ...


PILLAR_RECIPES = {
    "value": ("Professional insight", "Teach a useful principle the audience can apply.", "educational_card"),
    "education": ("Practical education", "Teach a repeatable process or explain a decision.", "educational_card"),
    "authority": ("Expert perspective", "Show how the brand approaches a topic through approved evidence.", "editorial_hero"),
    "community": ("Community conversation", "Invite the audience to share a relevant experience.", "quote_or_stat"),
    "product": ("Product and service", "Help the audience evaluate an approved offer or capability.", "product_focus"),
}

TOPIC_SIGNALS = {
    "product": ("product", "service", "offer", "feature", "capability", "pricing"),
    "community": ("community", "customer", "audience", "people", "story", "team"),
    "authority": ("expertise", "credential", "mission", "approach", "process", "positioning", "proof"),
    "education": ("guide", "method", "process", "problem", "benefit", "use_case"),
    "value": ("expertise", "industry", "method", "process", "problem", "benefit"),
}

# v1 records use platform-specific editorial names. Map those names to shared pillar IDs.
PRIOR_TERMS = {
    "education": "education", "entertainment": "community", "authority": "authority",
    "community": "community", "story": "community", "conversion": "product",
    "insight": "education", "conversation": "community", "curation": "authority",
}

HOOKS = {
    "value": ("What can teams take away from this?", "מה אפשר ללמוד מהדוגמה הזו?"),
    "education": ("How does this work in practice?", "איך זה עובד בפועל?"),
    "authority": ("What does this approach look like in practice?", "איך הגישה הזו נראית בפועל?"),
    "community": ("How does this compare with your experience?", "איך זה משתווה לניסיון מהשטח?"),
    "product": ("What should you check when evaluating this offer?", "מה כדאי לבדוק כשבוחנים את ההצעה?"),
}
REGISTER_FIELDS = {"register", "hebrew_register", "voice.register"}
ADDRESS_FIELDS = {"hebrew_address", "gender_addressing", "hebrew_gender_addressing"}

def _tokens(field_key: str) -> set[str]:
    return set(re.split(r"[^a-z0-9]+", field_key.casefold()))


def _audience(field_key: str) -> bool:
    return bool(_tokens(field_key) & {"audience", "customer", "persona", "pain", "desire", "need", "needs"})


class DeterministicStrategyPlanner:
    def plan(self, context: PlanningContext) -> list[PillarSelection]:
        pillars = ("value", "community", "product") if context.platforms == ("linkedin",) else (
            "education", "authority", "community", "product",
        )
        audience = [claim for claim in context.claims if _audience(claim.field_key)]
        subjects = [
            claim for claim in context.claims
            if not _audience(claim.field_key) and claim.field_key.casefold() not in REGISTER_FIELDS | ADDRESS_FIELDS
        ] or audience
        if not subjects:
            raise GenerationBlockedError("approve a brand topic claim in addition to messaging preferences")
        used = set()
        result = []
        for pillar in pillars:
            ranked = sorted(subjects, key=lambda claim: (
                -len(_tokens(claim.field_key).intersection(TOPIC_SIGNALS[pillar])),
                claim.id in used, claim.field_key, claim.id,
            ))
            selected = ranked[0]
            used.add(selected.id)
            result.append(PillarSelection(id=pillar, claim_ids=[selected.id],
                                          audience_claim_ids=[claim.id for claim in audience if claim.id != selected.id][:12]))
        return result


def approved_evidence(session: Session) -> list[ClaimEvidence]:
    # Newer unreviewed material cannot mask an approved claim. Observations must still have evidence.
    matching_span = or_(
        func.instr(Source.raw_content, BrandClaim.quoted_span) > 0,
        exists(select(SourceChunk.id).where(SourceChunk.source_id == Source.id,
                                              func.instr(SourceChunk.text, BrandClaim.quoted_span) > 0)),
    )
    rows = session.scalars(select(BrandClaim).outerjoin(Source, Source.id == BrandClaim.source_id).where(
        BrandClaim.status == "APPROVED",
        or_(BrandClaim.origin == "inferred", and_(BrandClaim.quoted_span != "", matching_span)),
    ))
    by_field: dict[str, list[BrandClaim]] = defaultdict(list)
    for row in rows:
        by_field[row.field_key].append(row)
    return [
        ClaimEvidence(id=row.id, field_key=key, value=row.value["value"], origin=row.origin,
                      is_user_override=row.is_user_override, source_id=row.source_id, quoted_span=row.quoted_span)
        for key, candidates in sorted(by_field.items())
        for row in [max(candidates, key=ClaimRepository.rank)]
    ]


def effective_weights(
    pillar_ids: list[str], playbook: EffectivePlaybook, explicit: Mapping[str, int] | None = None,
) -> dict[str, int]:
    supplied = explicit if explicit is not None else playbook.overrides.pillar_weights
    if supplied is not None:
        if set(supplied) != set(pillar_ids) or sum(supplied.values()) != 100:
            raise GenerationBlockedError(f"{playbook.platform} pillar weights must cover every strategy pillar and total 100")
        return {key: supplied[key] for key in pillar_ids}

    if playbook.platform == "linkedin":
        # Editorial interpretation of 3:2:1, with value split when the strategy has two value pillars.
        prior = {"value": 50, "education": 30, "authority": 20, "community": 33, "product": 17}
    else:
        prior = defaultdict(int)
        for key, weight in playbook.rules.get("pillar_mix_prior", {}).items():
            if key in PRIOR_TERMS and isinstance(weight, int) and weight >= 0:
                prior[PRIOR_TERMS[key]] += weight
    scores = {key: prior.get(key) or 10 for key in pillar_ids}
    total = sum(scores.values())
    weights = {key: 100 * scores[key] // total for key in scores}
    by_remainder = sorted(scores, key=lambda key: -(100 * scores[key] % total))
    for key in by_remainder[:100 - sum(weights.values())]:
        weights[key] += 1
    return weights


def evidence_text(claim: ClaimEvidence) -> str:
    if claim.origin == "observed" and claim.quoted_span:
        return claim.quoted_span
    if isinstance(claim.value, str):
        return claim.value
    return json.dumps(claim.value, ensure_ascii=False, sort_keys=True)


def _art_direction(design: BrandDesignSystem, preferred: str) -> ArtDirection:
    approved = design.layout_templates
    preferred_templates = [
        template for template in approved
        if template == preferred or (isinstance(template, dict) and template.get("id") == preferred)
    ]
    roles = {key: value for key, value in design.colors.items() if key != "contrast"}
    return ArtDirection(allowed_templates=preferred_templates or approved, color_roles=roles,
                        flux_style_prefix=design.flux_style_prefix, flux_constraints=design.flux_constraints)


def _tag(claims: list[ClaimEvidence], keys: set[str], allowed: tuple, fallback: str) -> str:
    for claim in claims:
        if claim.field_key.casefold() in keys and claim.value in allowed:
            return claim.value
    return fallback


def build_strategy(
    session: Session, request: StrategyUpdate, playbooks: dict[Platform, EffectivePlaybook],
    planner: StrategyPlanner, *, previous_id: str | None = None,
) -> StrategyDocument:
    profile, design = VersionRepository(session).assert_generation_ready()
    claims = approved_evidence(session)
    if not claims:
        raise GenerationBlockedError("approve at least one grounded brand claim before generating a strategy")
    evidence = {claim.id: claim for claim in claims}
    platform_order = tuple(sorted(request.platforms))
    try:
        selections = request.pillars
        if selections is None:
            context = PlanningContext(tuple(claim.model_copy(deep=True) for claim in claims), platform_order)
            selections = planner.plan(context)
        selections = TypeAdapter(list[PillarSelection]).validate_python(selections)
    except ValidationError as exc:
        raise GenerationBlockedError("planner did not return valid pillar selections") from exc
    pillar_ids = [pillar.id for pillar in selections]
    if len(pillar_ids) != len(set(pillar_ids)) or not 3 <= len(pillar_ids) <= 5:
        raise GenerationBlockedError("strategy requires three to five unique content pillars")
    if set(request.pillar_weights) - set(platform_order):
        raise GenerationBlockedError("pillar weights require an active strategy platform")

    register = request.messaging_register or _tag(claims, REGISTER_FIELDS, get_args(Register), "neutral")
    address = request.hebrew_address or _tag(claims, ADDRESS_FIELDS, get_args(HebrewAddress), "neutral")
    pillars, angles = [], []
    for selection in selections:
        ids = selection.claim_ids + selection.audience_claim_ids
        if set(ids) - evidence.keys() or len(ids) != len(set(ids)):
            raise GenerationBlockedError(f"pillar {selection.id} requires distinct effective approved claim IDs")
        audience = [evidence[claim_id] for claim_id in selection.audience_claim_ids]
        if any(not _audience(claim.field_key) for claim in audience):
            raise GenerationBlockedError(f"pillar {selection.id} contains a claim outside the audience fields")
        label, goal, template = PILLAR_RECIPES.get(
            selection.id, ("Brand topic", "Explain the selected topic using approved evidence.", "editorial_hero"),
        )
        primary = evidence[selection.claim_ids[0]]
        subject = primary.field_key.replace(".", " ").replace("_", " ").capitalize()
        pillars.append(ContentPillar(
            **selection.model_dump(), title=f"{label}: {subject}", goal=goal,
            audience_pain_points=[claim for claim in audience if _tokens(claim.field_key) & {"pain", "need", "needs"}],
            audience_desires=[claim for claim in audience if _tokens(claim.field_key) & {"desire", "desires", "goal", "goals"}],
            audience_context=audience, art_direction=_art_direction(design, template),
        ))
        for claim_id in selection.claim_ids:
            fact = evidence_text(evidence[claim_id])
            english_hook, hebrew_hook = HOOKS.get(selection.id, HOOKS["education"])
            hook = f"{hebrew_hook} ״{fact}״" if re.search(r"[\u0590-\u05ff]", fact) else f"{english_hook} “{fact}”"
            angles.append(MessagingAngle(
                pillar_id=selection.id, claim_id=claim_id, evidence_text=fact,
                angle=f"{label}: {fact}", hook=hook,
                messaging_register=register, hebrew_address=address,
            ))

    return StrategyDocument(
        schema_version=1,
        profile=VersionReference(id=profile.id, version=profile.version),
        design_system=VersionReference(id=design.id, version=design.version),
        platforms=list(platform_order), playbooks=playbooks, pillars=pillars, claims=claims,
        messaging_angles=angles, messaging_register=register, hebrew_address=address,
        pillar_weight_overrides=request.pillar_weights,
        pillar_weights={platform: effective_weights(pillar_ids, playbooks[platform], request.pillar_weights.get(platform))
                        for platform in platform_order}, created_by=request.created_by, previous_id=previous_id,
    )
