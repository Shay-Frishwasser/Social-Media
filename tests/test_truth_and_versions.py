import pytest
from sqlalchemy import update
from sqlalchemy.exc import DatabaseError

from brandai.db.brand_models import BrandProfile
from brandai.errors import GenerationBlockedError
from brandai.repositories import ClaimRepository, ContentRepository, SourceRepository, VersionRepository


def _approve_profile(versions):
    versions.append_profile(status="DRAFT", document={}, created_by="u1")
    versions.append_profile(status="RESEARCHING", document={}, created_by="u1")
    versions.append_profile(status="PENDING_REVIEW", document={}, created_by="u1")
    return versions.append_profile(status="APPROVED", document={"name": "Approved"}, created_by="u1")


def test_truth_hierarchy_user_override_wins(store, two_brands):
    brand, _ = two_brands
    with store.brand_session(brand.id) as session:
        source = SourceRepository(session).add_source(kind="website", url="https://brand.test")
        claims = ClaimRepository(session)
        claims.add(field_key="tone", value="corporate", origin="observed", confidence=.99,
                   source_id=source.id, quoted_span="We are corporate", status="APPROVED", approved_by="u1")
        claims.add(field_key="tone", value="warm", origin="inferred", confidence=.95)
        override = claims.add(field_key="tone", value="playful Israeli", origin="inferred", confidence=1,
                              user_override=True, approved_by="owner")
        assert claims.effective("tone").id == override.id


def test_generation_blocked_until_latest_profile_and_design_are_approved(store, two_brands):
    brand, _ = two_brands
    with store.brand_session(brand.id) as session:
        versions = VersionRepository(session)
        versions.append_profile(status="DRAFT", document={}, created_by="u1")
        with pytest.raises(GenerationBlockedError):
            ContentRepository(session).create(platform="linkedin", caption_document={}, visual_recipe={}, created_by="u1")
        versions.append_profile(status="RESEARCHING", document={}, created_by="u1")
        versions.append_profile(status="PENDING_REVIEW", document={}, created_by="u1")
        versions.append_profile(status="APPROVED", document={}, created_by="u1")
        versions.append_design_system(status="DRAFT", document={}, created_by="u1")
        with pytest.raises(GenerationBlockedError):
            ContentRepository(session).create(platform="linkedin", caption_document={}, visual_recipe={}, created_by="u1")
        versions.append_design_system(status="APPROVED", document={"colors": {"primary": "#123456"}}, created_by="u1")
        package = ContentRepository(session).create(platform="linkedin", caption_document={"text": "ok"},
                                                    visual_recipe={"template": "hero"}, created_by="u1")
        assert package.id


def test_profile_and_design_versions_are_database_immutable(store, two_brands):
    brand, _ = two_brands
    with pytest.raises(DatabaseError):
        with store.brand_session(brand.id) as session:
            profile = _approve_profile(VersionRepository(session))
            session.execute(update(BrandProfile).where(BrandProfile.id == profile.id).values(status="DRAFT"))
