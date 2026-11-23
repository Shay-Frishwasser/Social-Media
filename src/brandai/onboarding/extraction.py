from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from brandai.db.brand_models import BrandProfile, Source, SourceChunk
from brandai.errors import InvalidStateTransitionError
from brandai.repositories import ClaimRepository, VersionRepository
from .llm import StructuredLLMProvider
from .visual import VisualSignals, build_agency_visual_system


EXTRACTION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["claims", "knowledge_gaps", "conflicts"],
    "properties": {
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["field_key", "value", "origin", "source_id", "quoted_span", "confidence"],
            "properties": {
                "field_key": {"type": "string"}, "value": {},
                "origin": {"enum": ["observed", "inferred"]},
                "source_id": {"type": ["string", "null"]},
                "quoted_span": {"type": ["string", "null"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1}
            }}},
        "knowledge_gaps": {"type": "array", "items": {"type": "object", "required": ["question", "reason"],
            "properties": {"question": {"type": "string"}, "reason": {"type": "string"}}, "additionalProperties": False}},
        "conflicts": {"type": "array", "items": {"type": "object", "required": ["field_key", "description", "source_ids"],
            "properties": {"field_key": {"type": "string"}, "description": {"type": "string"},
                           "source_ids": {"type": "array", "items": {"type": "string"}}}, "additionalProperties": False}}
    }
}


SYSTEM_PROMPT = """You extract a draft brand identity from supplied evidence only.
Observed claims MUST copy a short verbatim quoted_span from the exact named source and may not paraphrase it.
Inferred claims are hypotheses: set source_id and quoted_span to null and tag origin=inferred.
Never turn an inference into a fact. Surface missing decisions as knowledge_gaps and contradictory evidence as conflicts.
Useful inferred fields include positioning, audience hypotheses, tone traits, Hebrew register, and gender-addressing preference.
Return only schema-valid JSON. The result remains draft and will be reviewed by a human."""


@dataclass(slots=True)
class ExtractionOutcome:
    profile_id: str
    design_system_id: str
    claim_ids: list[str]
    knowledge_gaps: list[dict]
    conflicts: list[dict]


class GroundedBrandExtractor:
    def __init__(self, session: Session, llm: StructuredLLMProvider, max_evidence_chunks: int = 12):
        self.session, self.llm = session, llm
        self.max_evidence_chunks = max_evidence_chunks

    def extract(self, visual: VisualSignals, *, created_by: str = "local-ai") -> ExtractionOutcome:
        versions = VersionRepository(self.session)
        latest = self.session.scalar(select(BrandProfile).order_by(BrandProfile.version.desc()).limit(1))
        if latest is None:
            versions.append_profile(status="DRAFT", document={}, created_by=created_by)
            latest_status = "DRAFT"
        else:
            latest_status = latest.status
        if latest_status != "DRAFT":
            raise InvalidStateTransitionError(f"onboarding extraction requires DRAFT, got {latest_status}")
        versions.append_profile(status="RESEARCHING", document={}, created_by=created_by)

        rows = self.session.execute(select(SourceChunk, Source).join(Source, Source.id == SourceChunk.source_id)
                                    .order_by(SourceChunk.source_id, SourceChunk.ordinal)).all()
        if not rows: raise ValueError("cannot extract a brand without evidence chunks")
        evidence = [{"source_id": source.id, "chunk_id": chunk.id, "title": source.title,
                     "url": source.url, "kind": source.kind, "text": chunk.text} for chunk, source in rows]
        evidence = evidence[:self.max_evidence_chunks]
        payload = self.llm.complete_json(system=SYSTEM_PROMPT,
            user="EVIDENCE:\n" + json.dumps(evidence, ensure_ascii=False), schema=EXTRACTION_SCHEMA)
        validated = self._validate(payload, evidence)
        claims_repo = ClaimRepository(self.session)
        claim_rows = [claims_repo.add(**claim) for claim in validated["claims"]]
        profile_document = {
            "claims": [{"claim_id": row.id, "field_key": row.field_key} for row in claim_rows],
            "knowledge_gaps": validated["knowledge_gaps"], "conflicts": validated["conflicts"],
            "requires_human_approval": True,
        }
        profile = versions.append_profile(status="PENDING_REVIEW", document=profile_document, created_by=created_by)
        system = build_agency_visual_system(visual).to_document()
        design_document = {
            "colors": {**system["color_roles"], "contrast": system["contrast"]},
            "font_pairings": system["typography"], "logo_safe_zones": system["logo_geometry"],
            "layout_templates": system["feed_cohesion"]["template_families"],
            "flux_style_prefix": system["flux_style_prefix"],
            "flux_constraints": {**system["flux_constraints"], "photography": system["photography"],
                                 "feed_cohesion": system["feed_cohesion"]},
        }
        design = versions.append_design_system(status="DRAFT", document=design_document, created_by=created_by)
        return ExtractionOutcome(profile.id, design.id, [row.id for row in claim_rows],
                                 validated["knowledge_gaps"], validated["conflicts"])

    @staticmethod
    def _validate(payload: dict, evidence: list[dict]) -> dict:
        if not isinstance(payload, dict): raise ValueError("LLM result must be an object")
        allowed_sources = {item["source_id"] for item in evidence}
        source_text = {source_id: "\n".join(item["text"] for item in evidence if item["source_id"] == source_id)
                       for source_id in allowed_sources}
        claims = payload.get("claims")
        if not isinstance(claims, list): raise ValueError("claims must be an array")
        for claim in claims:
            required = {"field_key", "value", "origin", "source_id", "quoted_span", "confidence"}
            if not isinstance(claim, dict) or not required.issubset(claim): raise ValueError("claim is missing required fields")
            if claim["origin"] == "observed":
                source_id, quote = claim["source_id"], claim["quoted_span"]
                if source_id not in allowed_sources or not isinstance(quote, str) or not quote.strip():
                    raise ValueError("observed claim lacks valid source and quote")
                if quote not in source_text[source_id]: raise ValueError("observed quoted_span is not verbatim source evidence")
            elif claim["origin"] == "inferred":
                if claim["source_id"] is not None or claim["quoted_span"] is not None:
                    raise ValueError("inferred claims may not masquerade as sourced facts")
            else: raise ValueError("unknown claim origin")
            claim["confidence"] = float(claim["confidence"])
            if not 0 <= claim["confidence"] <= 1: raise ValueError("claim confidence outside 0..1")
        for key in ("knowledge_gaps", "conflicts"):
            if not isinstance(payload.get(key), list): raise ValueError(f"{key} must be an array")
        for conflict in payload["conflicts"]:
            if any(source_id not in allowed_sources for source_id in conflict.get("source_ids", [])):
                raise ValueError("conflict references an unknown source")
        return payload
