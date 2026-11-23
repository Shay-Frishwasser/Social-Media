from __future__ import annotations

import asyncio
import json
import smtplib
from io import BytesIO
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from brandai.adapters.publishing import DeliveryMedia, PublishRequest
from brandai.publishing.models import ConnectionResponse, ProviderFailure, TokenInput
from brandai.publishing.email import SMTPConfig, SMTPEmailDelivery
from brandai.publishing.package import DeliveryBundle
from brandai.publishing.social import LinkedInPublisher, MetaGraphPublisher


def identity(provider, platform, account):
    return ConnectionResponse(id=uuid4(), provider=provider, platform=platform, account_id=account, label="Test",
                              has_token=True, expires_at=None)


def png():
    stream = BytesIO()
    Image.new("RGB", (108, 135), "#334411").save(stream, "PNG")
    return DeliveryMedia("slide-01.png", "image/png", stream.getvalue())


def test_instagram_container_and_publish_use_mock_http_contract():
    requests = []

    class MediaHost:
        async def url(self, brand_id, delivery_id, media):
            assert media.media_type == "image/jpeg" and media.data[:2] == b"\xff\xd8"
            assert brand_id == "brand" and delivery_id == "release-1"
            return "https://media.example.org/signed.jpg"

    def handle(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer graph-token"
        if request.url.path.endswith("/media_publish"):
            assert parse_qs(request.content.decode()) == {"creation_id": ["456"]}
            assert request.headers["x-brandai-request-id"] == "release-1"
            return httpx.Response(200, json={"id": "678"})
        if request.method == "GET":
            assert request.url.params["fields"] == "status_code"
            return httpx.Response(200, json={"status_code": "FINISHED"})
        assert request.url.path == "/v27.0/123/media"
        assert parse_qs(request.content.decode()) == {
            "image_url": ["https://media.example.org/signed.jpg"], "caption": ["The approved copy."],
        }
        return httpx.Response(200, json={"id": "456"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = MetaGraphPublisher(identity("meta", "instagram", "123"), TokenInput(access_token="graph-token"),
                                         "v27.0", media_urls=MediaHost(), client=client)
            return await adapter.publish(PublishRequest("brand", "instagram", "The approved copy.", (), "release-1", (png(),)))

    receipt = asyncio.run(run())
    assert receipt.external_id == "678" and receipt.status == "PUBLISHED" and len(requests) == 3


def test_facebook_photo_multipart_and_rate_limit_are_explicit():
    def handle(request):
        assert request.url.path == "/v27.0/123/photos"
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b'The approved copy.' in request.content and b'filename="slide-01.png"' in request.content
        return httpx.Response(429, headers={"Retry-After": "73"})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = MetaGraphPublisher(identity("meta", "facebook", "123"), TokenInput(access_token="meta-token"),
                                         "v27.0", client=client)
            return await adapter.publish(PublishRequest("brand", "facebook", "The approved copy.", (), "release-1", (png(),)))

    with pytest.raises(ProviderFailure) as failure:
        asyncio.run(run())
    assert failure.value.retry_after == 73 and not failure.value.uncertain


@pytest.mark.parametrize("document", [True, False])
def test_linkedin_document_and_image_upload_then_post(document):
    kind = "document" if document else "image"
    urn = f"urn:li:{kind}:asset123"
    parts = (png(), DeliveryMedia("carousel.pdf", "application/pdf", b"%PDF-1.4 fixture")) if document else (png(),)

    def handle(request):
        if request.url.host == "www.linkedin.com":
            assert request.method == "PUT" and request.content == parts[-1].data
            return httpx.Response(201)
        assert request.headers["linkedin-version"] == "202609"
        if request.url.path == "/rest/posts":
            payload = json.loads(request.content)
            assert payload["author"] == "urn:li:person:123"
            assert payload["content"]["media"]["id"] == urn and payload["lifecycleState"] == "PUBLISHED"
            assert payload["commentary"] == r"Approved copy \[1\]"
            assert request.headers["x-brandai-request-id"] == "li-release"
            return httpx.Response(201, headers={"x-restli-id": "urn:li:share:456"})
        if request.method == "GET":
            assert request.url.path.endswith(urn)
            return httpx.Response(200, json={"status": "AVAILABLE"})
        assert request.url.path == f"/rest/{kind}s" and request.url.params["action"] == "initializeUpload"
        return httpx.Response(200, json={"value": {kind: urn, "uploadUrl": "https://www.linkedin.com/dms-uploads/media"}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = LinkedInPublisher(identity("linkedin", "linkedin", "urn:li:person:123"),
                                        TokenInput(access_token="li-token"), "202609", client=client)
            return await adapter.publish(PublishRequest("brand", "linkedin", "Approved copy [1]", (), "li-release", parts))

    receipt = asyncio.run(run())
    assert receipt.external_id == "urn:li:share:456" and receipt.status == "PUBLISHED"


def test_smtp_delivers_exact_package_with_tls_and_stable_message_id(monkeypatch):
    events = []

    class LocalSMTP:
        def __init__(self, host, port, *, context, timeout):
            events.append((host, port, timeout))
            assert context.check_hostname

        def login(self, username, password):
            assert (username, password) == ("delivery", "local-test")

        def send_message(self, message):
            assert message["To"] == "owner@example.org"
            attachment, = list(message.iter_attachments())
            assert attachment.get_content_type() == "application/zip" and attachment.get_payload(decode=True) == b"zip"
            events.append(str(message["Message-ID"]))

        def close(self):
            events.append("closed")

    monkeypatch.setattr(smtplib, "SMTP_SSL", LocalSMTP)
    adapter = SMTPEmailDelivery(SMTPConfig("smtp.example.org", "deliver@example.org",
                                          username="delivery", password="local-test"))
    receipt = adapter.deliver("owner@example.org", DeliveryBundle(b"zip", {"platform": "linkedin"}),
                              idempotency_key="delivery-record")
    assert receipt.status == "DELIVERED" and events[1] == receipt.external_id == "<brandai-delivery-record@example.org>"
    assert events[0][:2] == ("smtp.example.org", 465) and events[-1] == "closed"
