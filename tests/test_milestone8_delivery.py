from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from brandai import RuntimeSettings
from brandai.adapters.publishing import DeliveryReceipt
from brandai.api import create_app
from brandai.db.brand_models import PublishRecord, SocialConnection
from brandai.publishing.connections import TokenVault
from brandai.publishing.email import MockEmailDelivery
from brandai.publishing.models import ProviderFailure
from test_milestone5_planning import WEEK, approve_inputs, approve_plan, create_plan, create_strategy, seed_evidence


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 23, 8, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def _publish_paths(base, item):
    identifier = item["id"]
    return {name: f"{base}/publishing/{name}/{identifier}" for name in ("gate", "export", "dispatch")}


@pytest.fixture
def publication(tmp_path):
    browser = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if not browser or not list(Path(browser).glob("chromium_headless_shell-*")):
        pytest.skip("Chromium headless shell is missing from PLAYWRIGHT_BROWSERS_PATH")

    @contextmanager
    def setup(**kwargs):
        clock, email = Clock(), MockEmailDelivery()
        app = create_app(RuntimeSettings(tmp_path / "runtime"), run_background_jobs=False, email_delivery=email,
                         token_vault=TokenVault(Fernet.generate_key().decode()), job_clock=clock, **kwargs)
        with TestClient(app) as client:
            brand = client.post("/api/brands", json={"name": "Delivery Co"}).json()["id"]
            base = f"/api/brands/{brand}"
            claims = seed_evidence(app.state.store, brand)
            approve_inputs(client, base)
            create_strategy(client, base)
            plan = approve_plan(client, base, create_plan(client, base, WEEK))
            slot = plan["slots"][0]
            result = client.post(f"{base}/content/generate/from-slot/{slot['id']}")
            assert result.status_code == 201, result.text
            item = result.json()
            render = client.post(f"{base}/visual-assets/render", json={"content_id": item["id"], "carousel": True})
            assert render.status_code == 201, render.text
            yield app, client, brand, base, clock, email, item, render.json(), claims

    return setup


def _approve(client, base, item):
    result = client.post(f"{base}/content/{item['id']}/approve", json={"revision_id": item["revision"]["id"]})
    assert result.status_code == 200, result.text


def _connect(client, base):
    response = client.post(f"{base}/publishing/connections", json={
        "provider": "linkedin", "platform": "linkedin", "account_id": "urn:li:organization:123",
        "label": "Company page", "tokens": {"access_token": "owner-secret-token"},
    })
    assert response.status_code == 200 and "owner-secret-token" not in response.text, response.text
    return response.json()["id"]


def test_export_and_email_are_gated_verified_and_idempotent(publication):
    with publication() as (app, client, brand, base, _, email, item, asset, _):
        routes = _publish_paths(base, item)
        gate = client.get(routes["gate"]).json()
        assert not gate["allowed"] and gate["issues"][0]["code"] == "revision_not_approved"
        assert client.post(routes["export"]).status_code == 409
        assert client.get(routes["export"]).status_code == 409
        assert client.get(f"{base}/visual-assets/{asset['id']}/download").status_code == 409
        payload = {"channel": "email", "recipient": "owner@example.org", "idempotency_key": "email-release"}
        assert client.post(routes["dispatch"], json=payload).status_code == 409
        assert email.deliveries == [] and client.get(f"{base}/jobs").json() == []

        _approve(client, base, item)
        assert client.get(routes["gate"]).json()["allowed"]
        created = client.post(routes["export"])
        assert created.status_code == 201, created.text
        record = created.json()
        exported = client.get(routes["export"], params={"export_id": record["id"]})
        assert exported.status_code == 200 and sha256(exported.content).hexdigest() == record["detail"]["sha256"]
        with ZipFile(BytesIO(exported.content)) as zipped:
            assert set(zipped.namelist()) == {"copy.txt", "manifest.json", "media/slide-01.png",
                                              "media/slide-02.png", "media/carousel.pdf"}
            manifest = json.loads(zipped.read("manifest.json"))
            assert manifest["brand_claims"] and manifest["copy"]["hook"] == item["revision"]["hook"]
            assert manifest["publish_window"]["timezone"] == "Asia/Jerusalem"
            assert manifest["design_system_id"] == asset["design_system_id"]
        assert client.post(routes["export"]).json()["id"] == record["id"]

        queued = client.post(routes["dispatch"], json=payload)
        assert queued.status_code == 202, queued.text
        dispatch = queued.json()
        assert dispatch["status"] == "QUEUED" and not email.deliveries
        assert client.post(routes["dispatch"], json=payload).json()["job_id"] == dispatch["job_id"]
        assert client.post(routes["dispatch"], json={**payload, "idempotency_key": "another-key"}).json()["id"] == dispatch["id"]
        done = client.post(f"{base}/jobs/run-next").json()
        assert done["status"] == "SUCCEEDED" and done["id"] == dispatch["job_id"] and done["progress"] == 100
        assert client.post(f"{base}/jobs/run-next").json() is None
        assert len(email.deliveries) == 1 and email.deliveries[0][0] == "owner@example.org"
        assert client.get(f"{base}/publishing/records/{dispatch['id']}").json()["status"] == "DELIVERED"
        assert client.get(f"{base}/jobs/{done['id']}").json() == done

        # Every file is checked during dispatch and export, including the second slide.
        path = app.state.store.settings.brand_assets_dir(brand) / "visuals" / asset["id"] / "slide-02.png"
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        assert client.get(routes["gate"]).json()["issues"][0]["code"] == "visual_integrity_failed"
        assert client.get(routes["export"]).status_code == 409


def test_stale_approval_and_asset_are_reported_after_manual_edit(publication):
    with publication() as (_, client, _, base, _, _, item, asset, _):
        _approve(client, base, item)
        edited = client.patch(f"{base}/content/{item['id']}", json={
            "revision_id": item["revision"]["id"], "hashtags": [],
        }).json()
        assert edited["status"] == "READY_FOR_REVIEW"
        issues = client.get(_publish_paths(base, item)["gate"], params={"visual_asset_id": asset["id"]}).json()["issues"]
        assert {"revision_not_approved", "visual_revision_mismatch"} <= {issue["code"] for issue in issues}
        _approve(client, base, edited)
        result = client.post(_publish_paths(base, item)["export"], json={"visual_asset_id": asset["id"]})
        assert result.status_code == 409 and result.json()["issues"][0]["code"] == "visual_revision_mismatch"


def test_scheduled_publish_respects_time_and_rechecks_stale_claims(publication):
    calls = []

    class Publisher:
        async def publish(self, request):
            calls.append(request)
            return DeliveryReceipt("linkedin-stub", "post-id", "PUBLISHED")

    with publication(publishers={"linkedin": lambda connection, tokens: Publisher()}) as (
        _, client, _, base, clock, _, item, _, _,
    ):
        _approve(client, base, item)
        connection = _connect(client, base)
        routes = _publish_paths(base, item)
        payload = {"channel": "linkedin", "connection_id": connection, "idempotency_key": "scheduled",
                   "scheduled_at": "2026-11-23T11:00:00+02:00"}
        queued = client.post(routes["dispatch"], json=payload)
        assert queued.status_code == 202, queued.text
        record = queued.json()
        assert client.get(f"{base}/content/{item['id']}").json()["status"] == "SCHEDULED"
        assert client.post(f"{base}/jobs/run-next").json() is None
        claim_id = item["revision"]["cited_claim_ids"][0]
        assert client.post(f"{base}/claims/{claim_id}/override", json={"value": "Changed claim"}).status_code == 201
        clock.advance(3600)
        done = client.post(f"{base}/jobs/run-next").json()
        assert done["status"] == "DEAD_LETTER"
        persisted = client.get(f"{base}/publishing/records/{record['id']}").json()
        assert persisted["status"] == "BLOCKED" and persisted["detail"]["gate"]["issues"][0]["code"] == "validation_context_stale"
        assert client.get(f"{base}/content/{item['id']}").json()["status"] == "APPROVED"
        assert not calls


@pytest.mark.parametrize("ambiguous", [False, True])
def test_social_retry_and_ambiguous_outcome_cannot_duplicate_post(publication, ambiguous):
    keys = []

    class Publisher:
        async def publish(self, request):
            keys.append(request.idempotency_key)
            if len(keys) == 1:
                if ambiguous:
                    raise ProviderFailure("response_lost", uncertain=True)
                raise ProviderFailure("rate_limit", retry_after=60)
            return DeliveryReceipt("test-linkedin", "post-456", "PUBLISHED")

    with publication(publishers={"linkedin": lambda connection, tokens: Publisher()}) as (
        _, client, _, base, clock, _, item, _, _,
    ):
        _approve(client, base, item)
        routes = _publish_paths(base, item)
        payload = {"channel": "linkedin", "connection_id": _connect(client, base), "idempotency_key": "li-release"}
        initial = client.post(routes["dispatch"], json=payload)
        assert initial.status_code == 202, initial.text
        row = initial.json()
        attempt = client.post(f"{base}/jobs/run-next").json()
        assert attempt["status"] == ("DEAD_LETTER" if ambiguous else "RETRY_WAIT")
        assert client.post(f"{base}/jobs/run-next").json() is None
        clock.advance(60)
        if ambiguous:
            assert client.post(f"{base}/jobs/run-next").json() is None
            assert client.get(f"{base}/publishing/records/{row['id']}").json()["status"] == "NEEDS_RECONCILIATION"
            assert len(keys) == 1
        else:
            result = client.post(f"{base}/jobs/run-next").json()
            assert result["status"] == "SUCCEEDED" and result["attempts"] == 2
            assert client.get(f"{base}/content/{item['id']}").json()["status"] == "PUBLISHED"
            assert keys == [row["id"], row["id"]]
        duplicate = client.post(routes["dispatch"], json={**payload, "idempotency_key": "different-client-key"}).json()
        assert duplicate["id"] == row["id"] and client.post(f"{base}/jobs/run-next").json() is None


def test_connection_state_ciphertext_redaction_and_refresh(tmp_path):
    vault = TokenVault(Fernet.generate_key().decode())
    refreshes = []

    async def refresh(tokens):
        from brandai.publishing.models import TokenInput
        refreshes.append(tokens.refresh_token.get_secret_value())
        return TokenInput(access_token="refreshed-token", expires_at=datetime.now(UTC) + timedelta(hours=1))

    app = create_app(RuntimeSettings(tmp_path), run_background_jobs=False, token_vault=vault,
                     token_refreshers={"linkedin": refresh})
    with TestClient(app) as client:
        brand = client.post("/api/brands", json={"name": "Connection brand"}).json()["id"]
        route = f"/api/brands/{brand}/publishing/connections"
        response = client.post(route, json={
            "provider": "linkedin", "platform": "linkedin", "account_id": "urn:li:person:user-123", "label": "Member",
        })
        assert response.status_code == 200 and not response.json()["has_token"]
        identifier = response.json()["id"]
        state = client.post(f"{route}/{identifier}/oauth-state").json()["state"]
        payload = {"state": state, "access_token": "plaintext-oauth", "refresh_token": "plaintext-refresh",
                   "expires_at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}
        wrong = client.post(f"{route}/{identifier}/complete", json={**payload, "state": "wrong-oauth-state"})
        assert wrong.status_code == 409 and "plaintext" not in wrong.text
        accepted = client.post(f"{route}/{identifier}/complete", json=payload)
        assert accepted.status_code == 200 and "plaintext" not in accepted.text
        assert client.post(f"{route}/{identifier}/complete", json=payload).status_code == 409
        invalid = client.post(route, json={"provider": "meta", "platform": "linkedin", "account_id": "bad",
                                           "label": "Invalid", "tokens": {"access_token": "reject-secret"}})
        assert invalid.status_code == 422 and "reject-secret" not in invalid.text
        with app.state.store.brand_session(brand) as session:
            row = session.get(SocialConnection, identifier)
            assert b"plaintext" not in row.ciphertext and row.oauth_state_hash is None
        from brandai.publishing.connections import ConnectionService
        service = ConnectionService(app.state.store, vault, {"linkedin": refresh})
        _, new_tokens = service.credentials(brand, identifier)
        assert new_tokens.access_token.get_secret_value() == "refreshed-token" and refreshes == ["plaintext-refresh"]
        assert "refreshed-token" not in client.get(route).text


def test_persistent_queue_runs_content_and_visual_handlers(publication):
    with publication() as (_, client, _, base, _, _, item, asset, _):
        root = f"{base}/jobs"
        payloads = [
            {"kind": "content_generation", "idempotency_key": "generate",
             "payload": {"slot_id": item["cadence_slot_id"]}},
            {"kind": "visual_render", "idempotency_key": "visual",
             "payload": {"previous_id": asset["id"], "seed": 42}},
        ]
        for payload in payloads:
            queued = client.post(root, json=payload)
            assert queued.status_code == 202, queued.text
            done = client.post(f"{root}/run-next").json()
            assert done["status"] == "SUCCEEDED" and done["id"] == queued.json()["id"]
            if payload["kind"] == "content_generation":
                assert done["result"]["content_id"] == item["id"]
            else:
                rerendered = client.get(f"{base}/visual-assets/{done['result']['visual_asset_id']}").json()
                assert rerendered["previous_id"] == asset["id"] and rerendered["seed"] == 42


def test_expired_publish_lease_records_uncertainty_without_retry(publication):
    with publication() as (app, client, brand, base, clock, email, item, _, _):
        _approve(client, base, item)
        dispatch = client.post(_publish_paths(base, item)["dispatch"], json={
            "channel": "email", "recipient": "owner@example.org", "idempotency_key": "crashed-worker",
        }).json()
        queue = app.state.queue
        claimed = queue.claim(brand)
        assert claimed.id == dispatch["job_id"]
        with app.state.store.brand_session(brand, immediate=True) as session:
            session.get(PublishRecord, dispatch["id"]).status = "DISPATCHING"
        clock.advance(queue.lease_seconds + 1)
        assert client.post(f"{base}/jobs/run-next").json() is None
        stored = client.get(f"{base}/publishing/records/{dispatch['id']}").json()
        assert stored["status"] == "NEEDS_RECONCILIATION" and stored["job_id"] == dispatch["job_id"]
        assert queue.get(brand, claimed.id).status == "DEAD_LETTER" and email.deliveries == []
