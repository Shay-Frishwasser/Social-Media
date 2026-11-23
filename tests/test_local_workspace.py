import io
from uuid import uuid4

from fastapi.testclient import TestClient
from PIL import Image

from brandai import RuntimeSettings
from brandai.api import create_app


def test_manual_review_and_isolated_upload_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("BRANDAI_CONTENT_PROVIDER", "mock")
    monkeypatch.setenv("BRANDAI_TOPIC_RESEARCH", "offline")
    monkeypatch.setenv("BRANDAI_AUTOMATED_ONBOARDING", "0")
    with TestClient(create_app(RuntimeSettings(tmp_path))) as client:
        brand = client.post("/api/brands", json={"name": "Local workspace"}).json()["id"]
        other = client.post("/api/brands", json={"name": "Other workspace"}).json()["id"]
        base = f"/api/brands/{brand}"
        assert client.get(base + "/profile").status_code == 404
        assert client.get(base + "/profile?allow_empty=true").json() is None
        assert client.get(base + "/design-system?allow_empty=true").json() is None
        assert client.get("/api/workspace").json()["onboarding_research"] is False
        payload = {"offering": "Small seasonal menus", "audience": "Local diners",
                   "differentiator": "The menu changes with the seasons."}
        created = client.post(base + "/profile/manual", json=payload)
        assert created.status_code == 201
        assert created.json()["status"] == "PENDING_REVIEW"
        claims = client.get(base + "/claims").json()
        assert {claim["value"] for claim in claims} == set(payload.values())
        assert all(claim["is_user_override"] and claim["source"] is None for claim in claims)
        assert client.post(base + "/profile/manual", json=payload).status_code == 409
        approved = client.post(base + "/profile/approve", json={}).json()
        assert approved["status"] == "APPROVED" and approved["version"] == created.json()["version"] + 1

        buffer = io.BytesIO()
        Image.new("RGB", (32, 32), "green").save(buffer, format="PNG")
        png = buffer.getvalue()
        def upload():
            return client.post(base + "/assets?kind=logo", files={"file": ("logo.png", png, "image/png")})
        asset = upload().json()
        assert upload().json()["id"] == asset["id"]
        route = f"/assets/{asset['id']}/preview"
        preview = client.get(base + route)
        assert preview.content == png
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert client.get(f"/api/brands/{other}" + route).status_code == 404
        assert client.get(base + f"/assets/{uuid4()}/preview").status_code == 404
        assert client.get(base + "/on-demand-photo-briefs").json() == []
