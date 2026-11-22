import pytest
from sqlalchemy import update
from sqlalchemy.exc import DatabaseError

from brandai.db.brand_models import ContentPackageRevision
from brandai.repositories import ContentRepository, VersionRepository


def test_content_edits_append_and_preserve_prior_versions(store, two_brands):
    brand, _ = two_brands
    with store.brand_session(brand.id) as session:
        versions = VersionRepository(session)
        for state in ("DRAFT", "RESEARCHING", "PENDING_REVIEW", "APPROVED"):
            versions.append_profile(status=state, document={"state": state}, created_by="owner")
        versions.append_design_system(status="APPROVED", document={"colors": {"primary": "#000000"}}, created_by="owner")
        repo = ContentRepository(session)
        package = repo.create(platform="instagram", caption_document={"text": "first"},
                              visual_recipe={"headline": "One"}, created_by="ai")
        repo.revise(package.id, caption_document={"text": "user edit"},
                    visual_recipe={"headline": "Two"}, created_by="owner")
        rows = repo.revisions(package.id)
        assert [r.version for r in rows] == [1, 2]
        assert rows[0].caption_document == {"text": "first"}
        assert rows[1].caption_document == {"text": "user edit"}

    with pytest.raises(DatabaseError):
        with store.brand_session(brand.id) as session:
            session.execute(update(ContentPackageRevision).values(caption_document={"text": "overwrite"}))
