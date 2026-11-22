from sqlalchemy import select

from brandai.db.brand_models import BrandClaim, BrandDesignSystem, BrandProfile
from brandai.onboarding.extraction import GroundedBrandExtractor
from brandai.onboarding.visual import VisualSignals
from brandai.repositories import SourceRepository


class FixedLLM:
    def __init__(self, source_id): self.source_id = source_id
    def complete_json(self, **_):
        return {
            "claims": [
                {"field_key": "location", "value": "Haifa", "origin": "observed", "source_id": self.source_id,
                 "quoted_span": "Our support is based in Haifa.", "confidence": .99},
                {"field_key": "audience", "value": "small B2B teams", "origin": "inferred", "source_id": None,
                 "quoted_span": None, "confidence": .7}],
            "knowledge_gaps": [{"question": "Which gender address is preferred in Hebrew?", "reason": "Not stated"}],
            "conflicts": []}


def test_grounded_extraction_and_state_progression(store, two_brands):
    brand, _ = two_brands
    with store.brand_session(brand.id) as session:
        repo = SourceRepository(session)
        source = repo.add_source(kind="website", url="https://acme.test", content="<html/>")
        repo.add_chunk(source.id, 0, "Acme makes tools. Our support is based in Haifa.", [1.0, 0.0])
        visual = VisualSignals(["#123456"], ["Inter"], {"hebrew": "Heebo", "english": "Inter"}, [],
                               "editorial photography, text-free, no letters, no logos")
        outcome = GroundedBrandExtractor(session, FixedLLM(source.id)).extract(visual)
        states = list(session.scalars(select(BrandProfile.status).order_by(BrandProfile.version)))
        assert states == ["DRAFT", "RESEARCHING", "PENDING_REVIEW"]
        claims = list(session.scalars(select(BrandClaim).order_by(BrandClaim.created_at)))
        assert claims[0].quoted_span == "Our support is based in Haifa."
        assert claims[1].origin == "inferred" and claims[1].source_id is None
        design = session.get(BrandDesignSystem, outcome.design_system_id)
        assert design.status == "DRAFT" and design.flux_constraints["text_free"] is True


class HallucinatingLLM(FixedLLM):
    def complete_json(self, **kwargs):
        payload = super().complete_json(**kwargs)
        payload["claims"][0]["quoted_span"] = "Invented fact not in source"
        return payload


def test_non_verbatim_observed_claim_is_rejected(store, two_brands):
    import pytest
    brand, _ = two_brands
    with pytest.raises(ValueError, match="not verbatim"):
        with store.brand_session(brand.id) as session:
            repo = SourceRepository(session)
            source = repo.add_source(kind="website", content="raw")
            repo.add_chunk(source.id, 0, "Our support is based in Haifa.", [1.0, 0.0])
            visual = VisualSignals([], [], {"hebrew": "Heebo", "english": "Inter"}, [], "text-free")
            GroundedBrandExtractor(session, HallucinatingLLM(source.id)).extract(visual)
