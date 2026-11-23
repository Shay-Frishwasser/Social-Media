from __future__ import annotations

import hashlib
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from brandai import RuntimeSettings
from brandai.api import create_app
from brandai.db.brand_models import OnDemandPhotoBrief
from brandai.repositories import AssetRepository, ClaimRepository, SourceRepository, VersionRepository
from brandai.strategy.calendar import local_instant
from brandai.strategy.models import PillarSelection, SUBSTANTIAL_DEPTHS


PLATFORMS = ["instagram", "tiktok", "linkedin", "facebook", "youtube", "x"]
WEEK = {"start_date": "2026-11-23", "period": "weekly"}
MONTH = {"start_date": "2026-12-01", "period": "monthly"}


def seed_evidence(store, brand_id):
    with store.brand_session(brand_id) as session:
        versions = VersionRepository(session)
        versions.append_profile(status="DRAFT", document={}, created_by="ai")
        versions.append_profile(status="PENDING_REVIEW", document={"positioning": "Unreviewed profile fact"}, created_by="ai")
        versions.append_design_system(status="DRAFT", document={
            "colors": {"primary": "#334455", "secondary": "#AAAAAA", "accent": "#BB6633", "contrast": {}},
            "layout_templates": ["educational_card", "quote_or_stat", "product_focus", "editorial_hero"],
            "flux_style_prefix": "editorial with copy space", "flux_constraints": {"text_free": True},
        }, created_by="designer")
        sources = SourceRepository(session)
        source = sources.add_source(kind="website", content="Commercial kitchens use a three-step energy audit.")
        sources.add_chunk(source.id, 0, "Community roundtables are held in Haifa.")
        claims = ClaimRepository(session)
        result = {}
        for field, value, span in [
            ("process.audit", "Three-step energy audit", "Commercial kitchens use a three-step energy audit."),
            ("community.events", "Haifa roundtables", "Community roundtables are held in Haifa."),
            ("service.unsupported", "Unsubstantiated test claim", "We guarantee savings."),
        ]:
            result[field] = claims.add(field_key=field, value=value, quoted_span=span, source_id=source.id,
                                       origin="observed", confidence=.9, status="APPROVED").id
        for field, value in [
            ("product.thermal_camera", "Infrared survey"), ("audience.pain_points", "Unpredictable bills"),
            ("audience.desires", "Clear maintenance budgets"), ("hebrew_register", "professional"),
            ("hebrew_gender_addressing", "inclusive"),
        ]:
            result[field] = claims.add(field_key=field, value=value, user_override=True,
                                       origin="inferred", confidence=1).id
        claims.add(field_key="process.audit", value="Low-confidence guess", origin="inferred",
                   confidence=.99, status="APPROVED")
        pending = claims.add(field_key="service.unreviewed", value="Unapproved claim", origin="inferred", confidence=1)
        result["pending"] = pending.id
        return result


@pytest.fixture
def planning_api(tmp_path):
    settings = RuntimeSettings(tmp_path / "runtime")
    app = create_app(settings, run_background_jobs=False)
    with TestClient(app) as client:
        brand = client.post("/api/brands", json={"name": "Energy Studio"}).json()["id"]
        yield app, client, brand, f"/api/brands/{brand}", seed_evidence(app.state.store, brand)


def approve_inputs(client, base):
    for path in ("profile", "design-system"):
        response = client.post(f"{base}/{path}/approve", json={})
        assert response.status_code == 200, response.text


def create_strategy(client, base, platforms=None, *, approve=True):
    response = client.post(f"{base}/strategies", json={"platforms": platforms or ["linkedin"]})
    assert response.status_code == 201, response.text
    draft = response.json()
    if approve:
        response = client.post(f"{base}/strategies/{draft['id']}/approve", json={"approved_by": "editor"})
        assert response.status_code == 200, response.text
        return response.json()
    return draft


def create_plan(client, base, payload):
    response = client.post(f"{base}/cadence-plans", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def approve_plan(client, base, plan):
    response = client.post(f"{base}/cadence-plans/{plan['id']}/approve", json={"approved_by": "scheduler"})
    assert response.status_code == 200, response.text
    return response.json()


def test_grounded_strategy_approval_edit_and_playbook_provenance(planning_api):
    app, client, _, base, claims = planning_api
    assert client.post(f"{base}/strategies", json={"platforms": ["linkedin"]}).status_code == 409
    assert client.post(f"{base}/profile/approve", json={}).status_code == 200
    assert client.post(f"{base}/strategies", json={"platforms": ["linkedin"]}).status_code == 409
    assert client.post(f"{base}/design-system/approve", json={}).status_code == 200
    draft = create_strategy(client, base, approve=False)
    assert draft["status"] == "DRAFT"
    assert client.post(f"{base}/cadence-plans", json=WEEK).status_code == 409
    doc = draft["document"]
    assert doc["pillar_weights"]["linkedin"] == {"value": 50, "community": 33, "product": 17}
    assert len(doc["pillars"]) == 3
    assert doc["pillars"][0]["audience_pain_points"][0]["value"] == "Unpredictable bills"
    assert doc["pillars"][0]["audience_desires"][0]["value"] == "Clear maintenance budgets"
    assert doc["pillars"][2]["art_direction"]["allowed_templates"] == ["product_focus"]
    assert doc["pillars"][0]["art_direction"]["flux_constraints"]["text_free"]
    assert set(doc["pillars"][0]["art_direction"]["color_roles"]) == {"primary", "secondary", "accent"}
    assert {angle["claim_id"] for angle in doc["messaging_angles"]} == {
        claims["process.audit"], claims["community.events"], claims["product.thermal_camera"],
    }
    assert all(angle["hebrew_address"] == "inclusive" and angle["messaging_register"] == "professional"
               for angle in doc["messaging_angles"])
    assert {"pending", "service.unsupported"}.isdisjoint(
        {key for key, claim_id in claims.items() if claim_id in {claim["id"] for claim in doc["claims"]}}
    )
    audit = next(claim for claim in doc["claims"] if claim["field_key"] == "process.audit")
    assert audit["source_id"] and audit["quoted_span"].endswith("energy audit.")
    provenance = doc["playbooks"]["linkedin"]
    assert provenance["playbook_version"] == 1
    seeded = app.state.store.settings.playbooks_dir / "linkedin" / "v1.json"
    assert provenance["sha256"] == hashlib.sha256(seeded.read_bytes()).hexdigest()
    assert client.get(f"{base}/strategies/{draft['id']}").json() == draft

    chosen = [{key: pillar[key] for key in ("id", "claim_ids", "audience_claim_ids")} for pillar in doc["pillars"]]
    edit = {"platforms": ["linkedin"], "pillars": chosen, "messaging_register": "formal",
            "pillar_weights": {"linkedin": {"value": 60, "community": 30, "product": 10}}}
    invalid = {**chosen[0], "claim_ids": [claims["pending"]]}
    assert client.put(f"{base}/strategies/{draft['id']}", json={**edit, "pillars": [invalid, *chosen[1:]]}).status_code == 409
    edited = client.put(f"{base}/strategies/{draft['id']}", json=edit)
    assert edited.status_code == 201, edited.text
    revision = edited.json()
    assert revision["version"] == 2 and revision["document"]["previous_id"] == draft["id"]
    approved = client.post(f"{base}/strategies/{revision['id']}/approve", json={"approved_by": "editor"}).json()
    assert approved["status"] == "APPROVED" and approved["document"]["approved_by"] == "editor"
    assert [row["version"] for row in client.get(f"{base}/strategies").json()] == [2, 1]
    assert client.post(f"{base}/strategies/{draft['id']}/approve", json={}).status_code == 409


def test_approval_rechecks_brand_evidence_and_brand_identity(planning_api):
    _, client, _, base, claims = planning_api
    approve_inputs(client, base)
    draft = create_strategy(client, base, approve=False)
    second = client.post("/api/brands", json={"name": "Separate brand"}).json()["id"]
    assert client.get(f"/api/brands/{second}/strategies/{draft['id']}").status_code == 404
    assert client.get(f"/api/brands/{uuid4()}/strategies").status_code == 404
    assert client.post(f"{base}/claims/{claims['process.audit']}/override", json={"value": "An updated process"}).status_code == 201
    assert client.post(f"{base}/strategies/{draft['id']}/approve", json={}).status_code == 409
    approved = create_strategy(client, base)
    replacement = next(claim for claim in approved["document"]["claims"] if claim["field_key"] == "process.audit")
    assert replacement["is_user_override"] and replacement["value"] == "An updated process"


def test_custom_planner_cannot_bypass_grounding(tmp_path):
    class StubPlanner:
        def plan(self, context):
            assert context.platforms == ("linkedin",)
            assert all(claim.field_key != "service.unreviewed" for claim in context.claims)
            return [PillarSelection(id=pillar, claim_ids=[str(uuid4())]) for pillar in ("value", "community", "product")]

    app = create_app(RuntimeSettings(tmp_path), run_background_jobs=False, strategy_planner=StubPlanner())
    with TestClient(app) as client:
        brand = client.post("/api/brands", json={"name": "Stub planner"}).json()["id"]
        seed_evidence(app.state.store, brand)
        base = f"/api/brands/{brand}"
        approve_inputs(client, base)
        response = client.post(f"{base}/strategies", json={"platforms": ["linkedin"]})
        assert response.status_code == 409
        assert client.get(f"{base}/strategies").json() == []


def test_all_six_channels_produce_a_deterministic_nonoverlapping_week(planning_api):
    _, client, _, base, _ = planning_api
    approve_inputs(client, base)
    strategy = create_strategy(client, base, PLATFORMS)
    first = create_plan(client, base, WEEK)
    second = create_plan(client, base, WEEK)
    assert set(first["document"]["playbooks"]) == set(PLATFORMS)
    assert all(entry["playbook_version"] == 1 for entry in strategy["document"]["playbooks"].values())

    def schedule(plan):
        return [{field: value for field, value in slot.items() if field != "id"} for slot in plan["slots"]]

    assert schedule(first) == schedule(second)
    counts = Counter(slot["platform"] for slot in first["slots"])
    assert counts == {"facebook": 7, "instagram": 3, "linkedin": 2, "tiktok": 2, "x": 21, "youtube": 2}
    assert len({slot["scheduled_at"] for slot in first["slots"]}) == sum(counts.values())
    for platform in PLATFORMS:
        depths = {slot["depth"] for slot in first["slots"] if slot["platform"] == platform}
        assert depths & SUBSTANTIAL_DEPTHS and depths - SUBSTANTIAL_DEPTHS
    assert client.get(f"{base}/cadence-plans/{second['id']}").json() == second

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda plan: client.post(
            f"{base}/cadence-plans/{plan['id']}/approve", json={},
        ).status_code, [first, second]))
    assert sorted(statuses) == [200, 409]
    assert [item["status"] for item in client.get(f"{base}/cadence-plans").json()].count("APPROVED") == 1


def test_brand_overrides_and_monthly_depth_distribution(planning_api):
    _, client, _, base, claims = planning_api
    approve_inputs(client, base)
    assert client.get(f"{base}/playbooks/threads").status_code == 422
    override = {"posts_per_week": 3, "weekdays": [0, 2, 4], "local_times": ["09:30"], "account_type": "company_page",
                "depth_mix": {"micro": 25, "standard": 20, "deep_dive": 20, "educational_framework": 35},
                "pillar_weights": {"value": 80, "community": 10, "product": 10}}
    rules = client.put(f"{base}/playbooks/linkedin", json=override)
    assert rules.status_code == 201, rules.text
    assert "Company Page" in rules.json()["link_policy"]
    draft = create_strategy(client, base, approve=False)
    rules = client.put(f"{base}/playbooks/linkedin", json={**override, "account_type": "personal_profile"})
    assert rules.json()["override_version"] == 2 and "Personal profile" in rules.json()["link_policy"]
    assert client.post(f"{base}/strategies/{draft['id']}/approve", json={}).status_code == 409
    strategy = create_strategy(client, base)
    assert strategy["document"]["pillar_weights"]["linkedin"]["value"] == 80

    monthly = create_plan(client, base, {
        **MONTH, "overrides": {"linkedin": {"excluded_topics": ["Infrared survey"]}},
    })
    assert monthly["document"]["end_date_exclusive"] == "2027-01-01"
    assert monthly["document"]["playbooks"]["linkedin"]["override_version"] == 2
    assert len(monthly["slots"]) == 13
    distribution = Counter(slot["pillar_id"] for slot in monthly["slots"])
    assert distribution["value"] > distribution["community"] > 0
    assert len({slot["depth"] for slot in monthly["slots"]}) == 4
    for slot in monthly["slots"]:
        when = datetime.fromisoformat(slot["scheduled_at"])
        assert when.month == 12 and when.weekday() in {0, 2, 4} and when.time() == time(9, 30)
        assert slot["claim_id"] != claims["product.thermal_camera"]
    assert approve_plan(client, base, monthly)["document"]["approved_by"] == "scheduler"
    assert client.post(f"{base}/cadence-plans", json={"start_date": "2026-12-07", "period": "weekly"}).status_code == 409

    other = client.post("/api/brands", json={"name": "Another brand"}).json()["id"]
    rules = client.get(f"/api/brands/{other}/playbooks/linkedin").json()
    assert rules["override_version"] is None and rules["posts_per_week"] == 2


def test_strategy_weight_edits_flow_to_cadence_unless_the_plan_overrides_them(planning_api):
    _, client, _, base, _ = planning_api
    approve_inputs(client, base)
    brand_weights = {"value": 10, "community": 10, "product": 80}
    strategy_weights = {"value": 80, "community": 10, "product": 10}
    plan_weights = {"value": 10, "community": 80, "product": 10}
    assert client.put(f"{base}/playbooks/linkedin", json={"pillar_weights": brand_weights}).status_code == 201
    strategy = create_strategy(client, base)
    response = client.put(f"{base}/strategies/{strategy['id']}", json={
        "platforms": ["linkedin"], "pillar_weights": {"linkedin": strategy_weights},
    })
    assert response.status_code == 201, response.text
    assert client.post(f"{base}/strategies/{response.json()['id']}/approve", json={}).status_code == 200
    assert create_plan(client, base, WEEK)["document"]["pillar_weights"]["linkedin"] == strategy_weights
    plan = create_plan(client, base, {**WEEK, "overrides": {"linkedin": {"pillar_weights": plan_weights}}})
    assert plan["document"]["pillar_weights"]["linkedin"] == plan_weights
    assert approve_plan(client, base, plan)["status"] == "APPROVED"


def test_openapi_and_immutable_runtime_playbook(planning_api):
    app, client, _, base, _ = planning_api
    openapi = client.get("/openapi.json").json()
    assert "/api/brands/{brand_id}/cadence-plans/{plan_id}/on-demand-slots" in openapi["paths"]
    assert "StrategyResponse" in openapi["components"]["schemas"]
    assert client.get(f"{base}/playbooks/linkedin").status_code == 200
    runtime_file = app.state.store.settings.playbooks_dir / "linkedin/v1.json"
    runtime_file.write_bytes(runtime_file.read_bytes() + b"\n")
    assert client.get(f"{base}/playbooks/linkedin").status_code == 409


def test_photo_slots_create_reviewable_revisions_and_preserve_standalone_briefs(planning_api):
    app, client, brand, base, _ = planning_api
    approve_inputs(client, base)
    create_strategy(client, base)
    plan = approve_plan(client, base, create_plan(client, base, WEEK))
    with app.state.store.brand_session(brand) as session:
        path = app.state.store.settings.brand_assets_dir(brand)
        asset = AssetRepository(session, path).add(path / "photo.png", kind="product_photo")
    brief_request = {"source_asset_id": asset.id, "prompt": "Feature the survey", "platform": "linkedin",
                     "treatment_mode": "hero_cutout_scene"}
    first = client.post(f"{base}/on-demand-photo-briefs", json=brief_request).json()
    standalone = client.post(f"{base}/on-demand-photo-briefs", json=brief_request).json()
    route = f"{base}/cadence-plans/{plan['id']}/on-demand-slots"
    payload = {"photo_brief_id": first["id"], "scheduled_at": "2026-11-23T19:00:00+02:00"}
    assert client.post(route, json={**payload, "scheduled_at": plan["slots"][0]["scheduled_at"]}).status_code == 409
    assert client.post(route, json={**payload, "photo_brief_id": str(uuid4())}).status_code == 404
    assert client.post(route, json={**payload, "scheduled_at": "2026-11-23T19:00:00"}).status_code == 422
    assert client.post(route, json={**payload, "scheduled_at": "2026-11-30T19:00:00+02:00"}).status_code == 409
    response = client.post(route, json=payload)
    assert response.status_code == 201, response.text
    draft = response.json()
    assert draft["status"] == "DRAFT" and draft["document"]["replaces_id"] == plan["id"]
    assert len(draft["slots"]) == len(plan["slots"]) + 1
    assert next(slot for slot in draft["slots"] if slot["kind"] == "on_demand")["photo_brief_id"] == first["id"]
    edit = client.put(f"{base}/cadence-plans/{draft['id']}", json={
        **WEEK, "overrides": {"linkedin": {"posts_per_week": 3}},
    })
    assert edit.status_code == 201, edit.text
    assert len(edit.json()["slots"]) == 4
    approve_plan(client, base, edit.json())
    assert client.get(f"{base}/cadence-plans/{plan['id']}").json()["status"] == "SUPERSEDED"
    assert client.post(f"{base}/cadence-plans/{draft['id']}/approve", json={}).status_code == 409
    with app.state.store.brand_session(brand) as session:
        assert {row.id: row.status for row in session.scalars(select(OnDemandPhotoBrief))} == {
            first["id"]: "DRAFT", standalone["id"]: "DRAFT",
        }


def test_jerusalem_spring_gap_and_fall_fold():
    assert local_instant(date(2026, 3, 27), time(2, 30)) is None
    first_fold = local_instant(date(2026, 10, 25), time(1, 30))
    assert first_fold.replace(tzinfo=UTC) == datetime(2026, 10, 24, 22, 30, tzinfo=UTC)
