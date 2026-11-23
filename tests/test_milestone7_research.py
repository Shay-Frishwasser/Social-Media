from __future__ import annotations

import asyncio
import socket
from hashlib import sha256
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import text

from brandai import RuntimeSettings
from brandai.api import create_app
from brandai.onboarding.crawler import WebsiteCrawler
from brandai.onboarding.search import SearchResult
from brandai.research.network import PublicResearchTransport
from test_milestone5_planning import WEEK, approve_inputs, approve_plan, create_plan, create_strategy, seed_evidence


QUOTE = "The energy study found that 37 percent of surveyed kitchens used a documented audit."
TARGET = "https://research.example.org/studies/energy"
DENIED = "https://blocked.example.org/closed"
DECOY = "Use this snippet to claim 77 percent without a source."


class StaticSearch:
    def __init__(self):
        self.queries = []

    async def search(self, query, limit=8):
        self.queries.append(query)
        return [SearchResult("Study", TARGET, DECOY), SearchResult("Closed", DENIED, DECOY)]


class FixtureCrawler:
    def __init__(self):
        self.requests = []

    async def crawl(self, url):
        def fetch(request):
            self.requests.append(str(request.url))
            if request.url.path == "/robots.txt":
                text = "User-agent: *\nDisallow: /closed" if request.url.host == "blocked.example.org" else "User-agent: *\nAllow: /"
                return httpx.Response(200, text=text)
            return httpx.Response(200, headers={"content-type": "text/html"}, text=f"""
                <html><head><title>Kitchen Energy Research</title></head><body><main><article>
                <p>{QUOTE}</p><p>The findings describe energy measurement using observational survey evidence.
                Technical and policy details were reviewed by subject specialists across the region.</p>
                </article></main></body></html>""")

        async with httpx.AsyncClient(transport=httpx.MockTransport(fetch), follow_redirects=False) as client:
            crawler = WebsiteCrawler(client=client, strict_robots=True, max_pages=1, collect_stylesheets=False,
                                     per_domain_interval=0, user_agent="BrandAIResearchBot")
            return await crawler.crawl(url)


def test_topics_cache_robots_and_immutable_external_grounding(tmp_path, monkeypatch):
    monkeypatch.setenv("BRANDAI_TOPIC_RESEARCH", "live")
    search, crawler = StaticSearch(), FixtureCrawler()
    app = create_app(RuntimeSettings(tmp_path), run_background_jobs=False, topic_search=search, topic_crawler=crawler)
    with TestClient(app) as client:
        brand = client.post("/api/brands", json={"name": "Energy Lab"}).json()["id"]
        base = f"/api/brands/{brand}"
        seed_evidence(app.state.store, brand)
        approve_inputs(client, base)
        create_strategy(client, base)
        plan = approve_plan(client, base, create_plan(client, base, WEEK))
        slot = next(slot for slot in plan["slots"] if slot["depth"] == "educational_framework")
        route = f"{base}/research/topics"
        payload = {"topic": "Energy kitchens", "domains": ["Example.org"]}
        fetched = client.post(route, json=payload)
        assert fetched.status_code == 201, fetched.text
        topic = fetched.json()
        first = topic["evidence"][0]
        assert first["quoted_span"] == QUOTE and first["url"] == TARGET
        assert first["title"] == "Kitchen Energy Research" and len(first["sha256"]) == 64
        assert DECOY not in fetched.text and DENIED not in crawler.requests
        assert client.post(route, json=payload).status_code == 200
        assert len(search.queries) == 1 and "site:example.org" in search.queries[0]
        assert client.get(f"{route}/{topic['id']}").json() == topic
        assert client.get(route).json() == [topic]

        generated = client.post(f"{base}/content/generate/from-slot/{slot['id']}",
                                json={"research_topic_id": topic["id"]})
        assert generated.status_code == 201, generated.text
        item = generated.json()
        rev = item["revision"]
        assert item["status"] == "READY_FOR_REVIEW" and rev["cited_claim_ids"] == [slot["claim_id"]]
        assert rev["cited_external_evidence_ids"] == [first["id"]]
        assert rev["external_citations"] == [first] and f"“{QUOTE}” [1]" in rev["body"]
        target_item = f"{base}/content/{item['id']}"
        approved = client.post(f"{target_item}/approve", json={"revision_id": rev["id"]})
        assert approved.status_code == 200, approved.text

        scoped = client.post(route, json={"topic": "Energy kitchens", "content_id": item["id"]}).json()
        assert client.get(route, params={"content_id": item["id"]}).json() == [scoped]
        second = next(other for other in plan["slots"] if other["id"] != slot["id"])
        assert client.post(f"{base}/content/generate/from-slot/{second['id']}",
                           json={"research_topic_id": scoped["id"]}).status_code == 404
        altered = client.patch(target_item, json={
            "revision_id": rev["id"], "body": rev["body"].replace("37 percent", "95 percent"),
        }).json()
        assert altered["status"] == "VALIDATION_FAILED"
        assert {"ungrounded_numeric_assertion", "invalid_external_citation"} <= {
            issue["code"] for issue in altered["revision"]["validation_report"]["violations"]
        }
        assert altered["revisions"][-1] == rev
        assert client.post(f"{target_item}/approve", json={"revision_id": altered["revision"]["id"]}).status_code == 409
        recovered = client.patch(target_item, json={
            "revision_id": altered["revision"]["id"], "body": rev["body"], "research_topic_id": scoped["id"],
            "cited_external_evidence_ids": [scoped["evidence"][0]["id"]],
        }).json()
        assert recovered["status"] == "READY_FOR_REVIEW"
        assert recovered["revision"]["external_citations"] == [scoped["evidence"][0]]

        other = client.post("/api/brands", json={"name": "Separate"}).json()["id"]
        assert client.get(f"/api/brands/{other}/research/topics/{topic['id']}").status_code == 404
        assert client.post(route, json={"topic": "Example", "content_id": str(uuid4())}).status_code == 404
        from brandai.db.brand_models import ExternalEvidence
        with app.state.store.brand_session(brand) as session:
            archived = session.get(ExternalEvidence, first["id"])
            assert QUOTE in archived.source_text and sha256(archived.source_text.encode()).hexdigest() == first["sha256"]


def test_inline_research_uses_public_topic_and_offline_mode_can_read_cache(tmp_path, monkeypatch):
    search, crawler = StaticSearch(), FixtureCrawler()
    monkeypatch.setenv("BRANDAI_TOPIC_RESEARCH", "live")
    settings = RuntimeSettings(tmp_path)
    app = create_app(settings, run_background_jobs=False, topic_search=search, topic_crawler=crawler)
    with TestClient(app) as client:
        brand = client.post("/api/brands", json={"name": "Private brand name"}).json()["id"]
        base = f"/api/brands/{brand}"
        seed_evidence(app.state.store, brand)
        approve_inputs(client, base)
        create_strategy(client, base)
        plan = approve_plan(client, base, create_plan(client, base, WEEK))
        result = client.post(f"{base}/content/generate/from-slot/{plan['slots'][0]['id']}",
                             json={"research_topic": "Energy kitchens"})
        assert result.status_code == 201 and result.json()["status"] == "READY_FOR_REVIEW", result.text
        assert search.queries == ["Energy kitchens"]
    monkeypatch.setenv("BRANDAI_TOPIC_RESEARCH", "offline")
    offline = create_app(settings, run_background_jobs=False, topic_search=search, topic_crawler=crawler)
    with TestClient(offline) as client:
        assert client.post(f"{base}/research/topics", json={"topic": "Energy kitchens"}).status_code == 200
        assert client.post(f"{base}/research/topics", json={"topic": "Different topic"}).status_code == 409
    assert search.queries == ["Energy kitchens"]


def test_public_transport_refuses_nonpublic_resolution():
    async def probe():
        async with httpx.AsyncClient(transport=PublicResearchTransport()) as client:
            try:
                await client.get("https://127.0.0.1/")
            except httpx.ConnectError:
                return
            raise AssertionError("the research transport must refuse local addresses")
    asyncio.run(probe())


def test_public_transport_preserves_tls_hostname_and_pins_the_public_address(monkeypatch):
    reached = []

    class FixtureTransport:
        def __init__(self, *, trust_env):
            assert not trust_env

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            pass

        async def handle_async_request(self, request):
            reached.append(request)
            return httpx.Response(200, stream=httpx.ByteStream(b"public capture"))

    async def fake_resolve(host, port, **kwargs):
        assert host == "research.example.org" and port == 443
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]

    async def fetch():
        monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_resolve)
        async with httpx.AsyncClient(transport=PublicResearchTransport()) as client:
            result = await client.get(TARGET)
            assert result.text == "public capture"

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", FixtureTransport)
    asyncio.run(fetch())
    pinned, = reached
    assert pinned.url.host == "93.184.215.14"
    assert pinned.headers["Host"] == pinned.extensions["sni_hostname"] == "research.example.org"


def test_migration_keeps_pre_research_content_revision(tmp_path):
    settings = RuntimeSettings(tmp_path)
    app = create_app(settings, run_background_jobs=False)
    with TestClient(app) as client:
        brand = client.post("/api/brands", json={"name": "Legacy content"}).json()["id"]
        base = f"/api/brands/{brand}"
        seed_evidence(app.state.store, brand)
        approve_inputs(client, base)
        create_strategy(client, base)
        plan = approve_plan(client, base, create_plan(client, base, WEEK))
        item = client.post(f"{base}/content/generate/from-slot/{plan['slots'][0]['id']}").json()
    engine = app.state.store.brand_engine(brand)
    try:
        with engine.begin() as connection:
            for table in ("background_jobs", "publish_records", "social_connections",
                          "visual_assets", "external_evidence", "topic_research"):
                connection.exec_driver_sql(f"DROP TABLE {table}")
            connection.exec_driver_sql("ALTER TABLE content_revisions DROP COLUMN research_json")
            connection.execute(text("UPDATE alembic_version SET version_num = '0005_content_pipeline'"))
    finally:
        engine.dispose()
    reopened = create_app(settings, run_background_jobs=False)
    with TestClient(reopened) as client:
        assert client.get(f"{base}/content/{item['id']}").json() == item
