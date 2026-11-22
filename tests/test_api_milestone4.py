from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select

from brandai import RuntimeSettings
from brandai.api import create_app
from brandai.db.brand_models import BrandProfile, OnDemandPhotoBrief
from brandai.repositories import ClaimRepository, SourceRepository, VersionRepository


def _png() -> bytes:
    stream = BytesIO(); Image.new("RGBA", (320, 240), (20, 80, 140, 255)).save(stream, format="PNG"); return stream.getvalue()


def _seed_review_state(app, brand_id):
    store = app.state.store
    with store.brand_session(brand_id) as session:
        source = SourceRepository(session).add_source(kind="website", url="https://brand.test/about", title="About", content="raw")
        claim = ClaimRepository(session).add(field_key="location", value="Tel Aviv", origin="observed", confidence=.95,
            source_id=source.id, quoted_span="Based in Tel Aviv")
        versions = VersionRepository(session)
        versions.append_profile(status="DRAFT", document={}, created_by="ai")
        versions.append_profile(status="RESEARCHING", document={}, created_by="ai")
        versions.append_profile(status="PENDING_REVIEW", document={"knowledge_gaps": []}, created_by="ai")
        versions.append_design_system(status="DRAFT", document={"colors": {"primary": "#2457D6"}}, created_by="ai")
        return claim.id


def test_claim_override_design_persistence_and_photo_brief(tmp_path):
    app = create_app(RuntimeSettings(tmp_path / "runtime"), run_background_jobs=False)
    client = TestClient(app)
    brand = client.post("/api/brands", json={"name": "API Brand"}).json()
    claim_id = _seed_review_state(app, brand["id"])

    overridden = client.post(f"/api/brands/{brand['id']}/claims/{claim_id}/override", json={"value": "Jerusalem"})
    assert overridden.status_code == 201 and overridden.json()["is_user_override"] is True
    claims = client.get(f"/api/brands/{brand['id']}/claims").json()
    assert next(row for row in claims if row["is_user_override"])["value"] == "Jerusalem"

    design = {"colors": {"primary": "#123456", "secondary": "#222222", "accent": "#FFD000"},
        "font_pairings": {"hebrew_family": "Heebo", "english_family": "Inter"},
        "logo_safe_zones": {"safe_zone_percent": 6}, "layout_templates": ["editorial_hero"],
        "flux_style_prefix": "text-free editorial product photography", "flux_constraints": {"text_free": True}}
    saved = client.put(f"/api/brands/{brand['id']}/design-system", json=design)
    assert saved.status_code == 200 and saved.json()["colors"]["primary"] == "#123456"
    assert client.post(f"/api/brands/{brand['id']}/profile/approve", json={}).json()["status"] == "APPROVED"
    assert client.post(f"/api/brands/{brand['id']}/design-system/approve", json={}).json()["status"] == "APPROVED"

    upload = client.post(f"/api/brands/{brand['id']}/assets?kind=product_photo",
                         files={"file": ("dish.png", _png(), "image/png")})
    assert upload.status_code == 201 and upload.json()["metadata"]["width"] == 320
    with app.state.store.brand_session(brand["id"]) as session:
        profile_versions_before = session.scalar(select(func.count()).select_from(BrandProfile))
    brief = client.post(f"/api/brands/{brand['id']}/on-demand-photo-briefs", json={
        "source_asset_id": upload.json()["id"], "prompt": "Announce tonight's new dish",
        "treatment_mode": "hero_cutout_scene", "platform": "instagram"})
    assert brief.status_code == 201 and brief.json()["treatment_mode"] == "hero_cutout_scene"
    with app.state.store.brand_session(brand["id"]) as session:
        assert session.scalar(select(func.count()).select_from(OnDemandPhotoBrief)) == 1
        assert session.scalar(select(func.count()).select_from(BrandProfile)) == profile_versions_before


def test_research_endpoint_queues_without_running_gpu(tmp_path):
    app = create_app(RuntimeSettings(tmp_path / "runtime"), run_background_jobs=False)
    client = TestClient(app)
    brand = client.post("/api/brands", json={"name": "Queue Brand"}).json()
    response = client.post(f"/api/brands/{brand['id']}/research",
                           json={"website_url": "https://example.com", "company_name": "Queue Brand"})
    assert response.status_code == 202 and response.json()["status"] == "QUEUED"
