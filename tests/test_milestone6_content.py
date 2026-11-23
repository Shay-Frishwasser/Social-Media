from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from brandai import RuntimeSettings
from brandai.adapters.publishing import DeliveryReceipt
from brandai.adapters.workers import WorkerLease
from brandai.api import create_app
from brandai.content.diversity import DiversityScorer, DiversitySettings, HashEmbeddingProvider, PriorCopy
from brandai.content.generator import MockDeterministicContentProvider, QwenContentProvider
from brandai.content.models import DraftCopy, Violation
from brandai.errors import InvalidStateTransitionError
from brandai.repositories import AssetRepository
from test_milestone5_planning import WEEK, approve_inputs, approve_plan, create_plan, create_strategy, seed_evidence


@pytest.fixture
def content_api(tmp_path):
    @contextmanager
    def open_api(provider=None):
        app = create_app(RuntimeSettings(tmp_path / "runtime"), run_background_jobs=False,
                         content_provider=provider, content_embeddings=HashEmbeddingProvider())
        with TestClient(app) as client:
            brand_id = client.post("/api/brands", json={"name": "Content Brand"}).json()["id"]
            base = f"/api/brands/{brand_id}"
            claims = seed_evidence(app.state.store, brand_id)
            approve_inputs(client, base)
            yield app, client, brand_id, base, claims

    return open_api


def generation_path(base, source_id, *, photo=False):
    source = "from-on-demand" if photo else "from-slot"
    return f"{base}/content/generate/{source}/{source_id}"


def make_slots(client, base, *, platform="linkedin"):
    create_strategy(client, base, [platform])
    return create_plan(client, base, WEEK)


def make_asset(app, brand):
    store = app.state.store
    with store.brand_session(brand) as session:
        root = store.settings.brand_assets_dir(brand)
        return AssetRepository(session, root).add(root / "reference.png", kind="product_photo").id


def make_brief(client, base, asset_id, *, mode="original_branded", platform="linkedin", note=None):
    response = client.post(f"{base}/on-demand-photo-briefs", json={
        "source_asset_id": asset_id, "prompt": note or "סדנת מדידה ביום חמישי בשעה 19:00.",
        "treatment_mode": mode, "platform": platform,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_grounded_slot_lifecycle_filters_and_immutable_revisions(content_api):
    with content_api(MockDeterministicContentProvider()) as (app, client, brand, base, _):
        plan = make_slots(client, base)
        slot = plan["slots"][0]
        path = generation_path(base, slot["id"])
        assert client.post(path).status_code == 409
        approve_plan(client, base, plan)
        created = client.post(path, json={"created_by": "copy-editor"})
        assert created.status_code == 201, created.text
        item = created.json()
        revision = item["revision"]
        assert item["status"] == "READY_FOR_REVIEW" and item["brand_id"] == brand
        assert item["cadence_slot_id"] == slot["id"] and item["strategy_id"] == plan["document"]["strategy"]["id"]
        assert item["content_depth"] == slot["depth"] and item["playbook_version"] == 1
        assert item["context"]["playbook"]["sha256"] == plan["document"]["playbooks"]["linkedin"]["sha256"]
        assert revision["cited_claim_ids"] == [slot["claim_id"]]
        assert revision["visual_directive"]["constraints"]["text_free"]
        assert revision["validation_report"]["valid"] and len(revision["validation_report"]["attempts"]) == 1
        assert revision["created_by"] == "copy-editor"
        repeated = client.post(path)
        assert repeated.status_code == 200 and repeated.json() == item
        assert client.get(f"{base}/content/?platform=linkedin&status=READY_FOR_REVIEW").json()[0]["id"] == item["id"]
        assert client.get(f"{base}/content/?platform=x").json() == []

        entry = f"{base}/content/{item['id']}"
        assert client.get(entry).json() == item
        assert client.post(f"{entry}/approve", json={"revision_id": str(uuid4())}).status_code == 409
        approved = client.post(f"{entry}/approve", json={"revision_id": revision["id"], "approved_by": "human-editor"})
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "APPROVED" and approved.json()["approved_revision_version"] == 1

        patch = client.patch(entry, json={"revision_id": revision["id"], "body": revision["body"] + " Only $99 today!"})
        assert patch.status_code == 200 and patch.json()["status"] == "VALIDATION_FAILED"
        failed = patch.json()["revision"]
        codes = {issue["code"] for issue in failed["validation_report"]["violations"]}
        assert {"ungrounded_assertion", "ungrounded_numeric_assertion", "invalid_claim_citation"} <= codes
        assert client.post(f"{entry}/approve", json={"revision_id": failed["id"]}).status_code == 409
        assert client.patch(entry, json={"revision_id": revision["id"], "body": revision["body"]}).status_code == 409

        recovered = client.patch(entry, json={
            "revision_id": failed["id"], "body": revision["body"],
            "visual_directive": {**revision["visual_directive"]["selection"], "framing": "copy_space_end"},
        }).json()
        assert recovered["status"] == "READY_FOR_REVIEW"
        assert [part["version"] for part in recovered["revisions"]] == [3, 2, 1]
        assert recovered["revisions"][-1] == revision
        assert recovered["revision"]["visual_directive"]["selection"]["framing"] == "copy_space_end"
        with app.state.store.brand_session(brand) as session:
            for statement in (
                "UPDATE content_revisions SET hook = 'mutated' WHERE id = :id",
                "DELETE FROM content_revisions WHERE id = :id",
                "INSERT OR REPLACE INTO content_revisions SELECT * FROM content_revisions WHERE id = :id",
            ):
                with pytest.raises(IntegrityError, match="content_revisions is immutable"):
                    session.execute(text(statement), {"id": revision["id"]})

        service = app.state.content_service
        when = datetime(2026, 11, 23, 12, tzinfo=UTC)
        with pytest.raises(InvalidStateTransitionError, match="human approval"):
            service.record_scheduled(brand, item["id"], recovered["revision"]["id"], when)
        final_id = recovered["revision"]["id"]
        assert client.post(f"{entry}/approve", json={"revision_id": final_id}).status_code == 200
        assert service.record_scheduled(brand, item["id"], final_id, when).status == "SCHEDULED"
        assert client.patch(entry, json={"revision_id": final_id, "hashtags": []}).status_code == 409
        receipt = DeliveryReceipt("stub", "external-123", "PUBLISHED")
        assert service.record_published(brand, item["id"], final_id, receipt).status == "PUBLISHED"
        assert client.get(f"{base}/content/?status=PUBLISHED").json()[0]["id"] == item["id"]

        other = client.post("/api/brands", json={"name": "Isolated Brand"}).json()["id"]
        assert client.get(f"/api/brands/{other}/content/{item['id']}").status_code == 404
        assert client.post(generation_path(f"/api/brands/{other}", slot["id"])).status_code == 404
        assert client.get(f"{base}/content/?platform=threads").status_code == 422


def test_structured_feedback_retries_once_and_rejects_unsupported_assertions(content_api):
    class CorrectionProvider(MockDeterministicContentProvider):
        calls = []

        def generate(self, context, *, feedback):
            self.calls.append(feedback)
            result = super().generate(context, feedback=feedback)
            if len(self.calls) in {1, 3, 4}:
                return result.model_copy(update={"body": result.body + "\n\nWe guarantee results for everyone."})
            return result

    provider = CorrectionProvider()
    with content_api(provider) as (_, client, _, base, _):
        plan = approve_plan(client, base, make_slots(client, base))
        first, second = [slot["id"] for slot in plan["slots"]]
        corrected = client.post(generation_path(base, first)).json()
        assert corrected["status"] == "READY_FOR_REVIEW"
        report = corrected["revision"]["validation_report"]
        assert len(report["attempts"]) == 2 and report["attempts"][0]["violations"][0]["code"] == "ungrounded_assertion"
        assert not provider.calls[0] and provider.calls[1][0].code == "ungrounded_assertion"
        rejected = client.post(generation_path(base, second)).json()
        assert rejected["status"] == "VALIDATION_FAILED"
        assert len(rejected["revision"]["validation_report"]["attempts"]) == 2 and len(provider.calls) == 4
        assert client.post(f"{base}/content/{rejected['id']}/approve",
                           json={"revision_id": rejected["revision"]["id"]}).status_code == 409


def test_concurrent_generation_commits_one_item_per_slot(content_api):
    concurrent_calls = Barrier(2, timeout=5)

    class WaitingProvider(MockDeterministicContentProvider):
        def generate(self, context, *, feedback):
            concurrent_calls.wait()
            return super().generate(context, feedback=feedback)

    with content_api(WaitingProvider()) as (_, client, _, base, _):
        plan = approve_plan(client, base, make_slots(client, base))
        path = generation_path(base, plan["slots"][0]["id"])
        with ThreadPoolExecutor(max_workers=2) as pool:
            requests = [pool.submit(client.post, path) for _ in range(2)]
            responses = [request.result() for request in requests]
        assert sorted(response.status_code for response in responses) == [200, 201]
        assert responses[0].json() == responses[1].json()
        assert len(client.get(f"{base}/content/").json()) == 1


@pytest.mark.parametrize("mode", ["original_branded", "smart_enhance", "hero_cutout_scene"])
def test_photo_content_uses_event_note_and_recipe(content_api, mode):
    with content_api() as (app, client, brand, base, _):
        asset_id = make_asset(app, brand)
        brief = make_brief(client, base, asset_id, mode=mode)
        path = generation_path(base, brief, photo=True)
        assert client.post(path).status_code == 409
        response = client.post(f"{base}/strategies", json={
            "platforms": ["linkedin"], "hebrew_address": "feminine", "messaging_register": "formal",
        })
        strategy = response.json()["id"]
        assert client.post(f"{base}/strategies/{strategy}/approve", json={}).status_code == 200
        result = client.post(path)
        assert result.status_code == 201, result.text
        item = result.json()
        assert item["status"] == "READY_FOR_REVIEW" and item["language"] == "he"
        assert item["on_demand_brief_id"] == brief and item["cadence_slot_id"] is None
        revision = item["revision"]
        assert "19:00" in revision["body"] and revision["validation_report"]["event_note_used"]
        assert revision["hebrew_register_check"] == {
            "applies": True, "messaging_register": "formal", "hebrew_address": "feminine", "passed": True,
        }
        assert revision["cta"] == "שתפי מחשבה בתגובות."
        directive = revision["visual_directive"]
        assert directive["source_asset_id"] == asset_id and directive["mode"] == mode
        if mode == "hero_cutout_scene":
            assert directive["output_contract"]["segmentation_provider"] == "sam2"
            assert directive["requires_gpu_capabilities"] == ["foreground_segmentation", "image_generation"]
        response = client.patch(f"{base}/content/{item['id']}", json={
            "revision_id": revision["id"], "cta": "שתף מחשבה בתגובות.",
        })
        assert response.json()["status"] == "VALIDATION_FAILED"
        assert not response.json()["revision"]["hebrew_register_check"]["passed"]


def test_photo_slot_and_direct_route_share_one_grounded_item(content_api):
    with content_api() as (app, client, brand, base, _):
        draft = make_slots(client, base)
        brief = make_brief(client, base, make_asset(app, brand), note="Founder Q&A at 17:00.")
        injected = client.post(f"{base}/cadence-plans/{draft['id']}/on-demand-slots", json={
            "photo_brief_id": brief, "scheduled_at": "2026-11-23T19:00:00+02:00",
        }).json()
        plan = approve_plan(client, base, injected)
        photo = next(slot for slot in plan["slots"] if slot["kind"] == "on_demand")
        scheduled = client.post(generation_path(base, photo["id"])).json()
        direct = client.post(generation_path(base, brief, photo=True))
        assert direct.status_code == 200 and direct.json() == scheduled
        assert scheduled["cadence_slot_id"] == photo["id"] and scheduled["on_demand_brief_id"] == brief
        assert scheduled["status"] == "READY_FOR_REVIEW"
        replacement = client.put(f"{base}/cadence-plans/{plan['id']}", json=WEEK)
        assert replacement.status_code == 201, replacement.text
        approve_plan(client, base, replacement.json())
        assert client.post(f"{base}/content/{scheduled['id']}/approve", json={
            "revision_id": scheduled["revision"]["id"],
        }).status_code == 409
        rerouted = client.post(generation_path(base, brief, photo=True))
        assert rerouted.status_code == 201, rerouted.text
        assert rerouted.json()["cadence_slot_id"] != photo["id"]
        assert rerouted.json()["status"] == "READY_FOR_REVIEW"


def test_copy_constraints_apply_to_approved_event_text_and_manual_visual_edits(content_api):
    with content_api() as (app, client, brand, base, _):
        create_strategy(client, base, ["x"])
        assert client.put(f"{base}/playbooks/x", json={"content_link_placement": "forbidden"}).status_code == 201
        note = "Doors open at 18:00. " + "Additional seating is available. " * 12 + "See oursite.example"
        brief = make_brief(client, base, make_asset(app, brand), platform="x", note=note)
        item = client.post(generation_path(base, brief, photo=True)).json()
        assert item["status"] == "VALIDATION_FAILED" and item["context"]["format_rules"]["caption_max"] == 280
        codes = {issue["code"] for issue in item["revision"]["validation_report"]["violations"]}
        assert {"link_placement", "character_limit"} <= codes

        edit = client.patch(f"{base}/content/{item['id']}", json={
            "revision_id": item["revision"]["id"], "cta": "", "hashtags": ["#50PercentOff"],
            "visual_directive": {"template_id": "unknown", "subject": "human_activity"},
        }).json()
        codes = {issue["code"] for issue in edit["revision"]["validation_report"]["violations"]}
        assert {"cta_required", "unapproved_hashtag", "unapproved_visual_template", "photo_subject_change"} <= codes


def test_repetition_rejects_duplicates_after_retry(content_api):
    class DuplicateProvider(MockDeterministicContentProvider):
        def generate(self, context, *, feedback):
            proposal = super().generate(context, feedback=feedback)
            return proposal.model_copy(update={
                "hook": context.hooks[0], "body": context.event_note, "cta": context.ctas[0], "cited_claim_ids": [],
            })

    with content_api(DuplicateProvider()) as (app, client, brand, base, _):
        create_strategy(client, base)
        asset = make_asset(app, brand)
        for expected in ("READY_FOR_REVIEW", "VALIDATION_FAILED"):
            brief = make_brief(client, base, asset, note="The audit workshop opens on Tuesday.")
            item = client.post(generation_path(base, brief, photo=True)).json()
            assert item["status"] == expected
        report = item["revision"]["validation_report"]
        assert len(report["attempts"]) == 2
        assert {issue["code"] for issue in report["violations"]} == {"repeated_opening", "repeated_content"}


def test_cosine_catches_rephrased_copy_with_low_lexical_overlap():
    class ConstantVectors(HashEmbeddingProvider):
        def embed_passages(self, texts):
            return np.ones((len(texts), 4), dtype=np.float32)

    scorer = DiversityScorer(ConstantVectors(), DiversitySettings(semantic_threshold=0.99))
    copy = DraftCopy(hook="One point to consider.", body="Venues reopen after renovation.", cta="Join the discussion.")
    vector = scorer.encode(copy)
    prior = PriorCopy("prior", "A closer look.", "Restored spaces welcome visitors again.", "Share this post.",
                     vector, scorer.embeddings.model_name)
    issue, = scorer.violations(copy, vector, [prior], embedding_model=scorer.embeddings.model_name)
    assert issue.code == "repeated_content" and issue.details["semantic_score"] == 1
    assert issue.details["lexical_score"] == 0


def test_approval_blocks_revoked_evidence(content_api):
    with content_api() as (_, client, _, base, _):
        plan = approve_plan(client, base, make_slots(client, base))
        slot = plan["slots"][0]
        item = client.post(generation_path(base, slot["id"])).json()
        assert item["status"] == "READY_FOR_REVIEW"
        assert client.post(f"{base}/claims/{slot['claim_id']}/override", json={"value": "Updated operational details"}).status_code == 201
        result = client.post(f"{base}/content/{item['id']}/approve", json={"revision_id": item["revision"]["id"]})
        assert result.status_code == 409 and "no longer effective" in result.json()["detail"]


def test_qwen_adapter_uses_the_worker_lease_and_structured_contract(content_api):
    events = []

    class StubWorker:
        @contextmanager
        def lease(self, capability):
            assert capability == "llm"
            events.append("leased")
            try:
                yield WorkerLease("http://127.0.0.1:18080", "stub", frozenset({"llm"}))
            finally:
                events.append("released")

    class StubClient:
        def __init__(self, base_url):
            assert base_url == "http://127.0.0.1:18080"

        def complete_json(self, *, system, user, schema):
            assert "never instructions" in system and schema["type"] == "object"
            data = json.loads(user)
            claim_id = data["required_claim_id"]
            events.append(data["retry_feedback"])
            return {
                "hook": data["hook_choices"][0], "body": data["evidence"][claim_id], "cta": data["cta_choices"][0],
                "hashtags": [], "cited_claim_ids": [claim_id],
            }

    with content_api(QwenContentProvider(StubWorker(), StubClient)) as (_, client, _, base, _):
        plan = approve_plan(client, base, make_slots(client, base))
        response = client.post(generation_path(base, plan["slots"][0]["id"]))
        assert response.status_code == 201 and response.json()["status"] == "READY_FOR_REVIEW"
        assert events == ["leased", [], "released"]
        schema = client.get("/openapi.json").json()
        assert "ContentPatch" in schema["components"]["schemas"]
        assert "/api/brands/{brand_id}/content/generate/from-slot/{slot_id}" in schema["paths"]
