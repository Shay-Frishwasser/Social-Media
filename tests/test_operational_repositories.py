from datetime import UTC, datetime

from brandai.repositories import (
    ContentRepository, GlobalRepository, PlanningDeliveryRepository, VersionRepository,
)


def test_global_and_brand_local_operational_records(store, two_brands):
    brand, _ = two_brands
    with store.GlobalSession.begin() as session:
        repo = GlobalRepository(session)
        user = repo.add_user(email="owner@example.test", display_name="Owner")
        assert repo.add_job(brand_id=brand.id, kind="RESEARCH").brand_id == brand.id
        assert repo.add_reminder(brand_id=brand.id, user_id=user.id,
                                 due_at=datetime.now(UTC)).brand_id == brand.id

    with store.brand_session(brand.id) as session:
        versions = VersionRepository(session)
        for state in ("DRAFT", "RESEARCHING", "PENDING_REVIEW", "APPROVED"):
            versions.append_profile(status=state, document={}, created_by=user.id)
        versions.append_design_system(status="APPROVED", document={}, created_by=user.id)
        package = ContentRepository(session).create(platform="x", caption_document={"text": "hello"},
                                                    visual_recipe={}, created_by=user.id)
        repo = PlanningDeliveryRepository(session)
        assert repo.add_strategy({"pillars": []}).version == 1
        assert repo.add_cadence_plan({"x": 3}).version == 1
        assert repo.add_reminder(due_at=datetime.now(UTC), package_id=package.id).package_id == package.id
        assert repo.add_email_delivery(package_id=package.id, recipient="owner@example.test").status == "QUEUED"
        assert repo.add_feedback(package_id=package.id, platform="x", metrics={"likes": 4}).metrics["likes"] == 4
