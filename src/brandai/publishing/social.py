from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from io import BytesIO
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx
from PIL import Image

from brandai.adapters.publishing import DeliveryMedia, DeliveryReceipt, PublishRequest
from brandai.research.network import public_url

from .models import ConnectionResponse, ProviderFailure, TokenInput


class PublicMediaURLProvider(Protocol):
    async def url(self, brand_id: str, delivery_id: str, media: DeliveryMedia) -> str: ...


def _delay(response: httpx.Response) -> float:
    value = response.headers.get("retry-after", "")
    if value.isdecimal():
        return float(min(86400, max(1, int(value[:9]))))
    try:
        due = parsedate_to_datetime(value).astimezone(UTC)
        return min(86400, max(1, (due - datetime.now(UTC)).total_seconds()))
    except (ValueError, TypeError, OverflowError):
        return 30


def _json(response: httpx.Response, *, final: bool = False) -> dict:
    try:
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError
        return data
    except ValueError:
        raise ProviderFailure("provider_response_unreadable", retry_after=None if final else 30, uncertain=final) from None


def _identifier(response: httpx.Response, *, final: bool = False) -> str:
    value = _json(response, final=final).get("id")
    if not isinstance(value, (str, int)) or isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise ProviderFailure("provider_receipt_missing" if final else "media_id_missing",
                              retry_after=None if final else 30, uncertain=final)
    return str(value)


class _HTTPPublisher:
    def __init__(self, connection: ConnectionResponse, tokens: TokenInput, client: httpx.AsyncClient | None):
        self.connection, self.tokens, self.client = connection, tokens, client

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens.access_token.get_secret_value()}"}

    async def publish(self, request: PublishRequest) -> DeliveryReceipt:
        if request.platform != self.connection.platform:
            raise ProviderFailure("connection_platform_mismatch")
        if self.client:
            return await self._publish(self.client, request)
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            return await self._publish(client, request)

    async def _request(self, client: httpx.AsyncClient, method: str, url: str, *, final: bool = False,
                       idempotency_key: str | None = None, **kwargs) -> httpx.Response:
        headers = kwargs.pop("headers", self.headers)
        if idempotency_key:
            headers = {**headers, "X-BrandAI-Request-ID": idempotency_key}
        try:
            response = await client.request(method, url, headers=headers, follow_redirects=False, **kwargs)
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout):
            raise ProviderFailure("provider_connection_failed", retry_after=15) from None
        except httpx.HTTPError:
            raise ProviderFailure("provider_transport_error", retry_after=None if final else 15, uncertain=final) from None
        if response.status_code == 429:
            raise ProviderFailure("provider_rate_limited", retry_after=_delay(response))
        if response.status_code >= 500 or response.status_code in {408, 409}:
            raise ProviderFailure("provider_response_uncertain", retry_after=None if final else 30, uncertain=final)
        if not response.is_success:
            if self.connection.provider == "meta" and response.status_code == 400:
                # Graph also returns some explicit throttle failures as HTTP 400.
                try:
                    error = response.json().get("error", {})
                    if error.get("code") in {4, 17, 32, 613, 80002, 80004}:
                        raise ProviderFailure("provider_rate_limited", retry_after=_delay(response))
                except (ValueError, AttributeError):
                    pass
            raise ProviderFailure("provider_request_rejected")
        return response

    async def _publish(self, client: httpx.AsyncClient, request: PublishRequest) -> DeliveryReceipt:
        raise NotImplementedError


def _instagram_jpeg(file: DeliveryMedia) -> DeliveryMedia:
    with Image.open(BytesIO(file.data)) as original:
        if not .8 <= original.width / original.height <= 1.91:
            raise ProviderFailure("instagram_feed_aspect_unsupported")
        image = original.convert("RGB")
        if image.width > 1440:
            image = image.resize((1440, round(1440 * image.height / image.width)), Image.Resampling.LANCZOS)
        encoded = BytesIO()
        image.save(encoded, "JPEG", quality=92, optimize=True)
    return DeliveryMedia(file.filename.rsplit(".", 1)[0] + ".jpg", "image/jpeg", encoded.getvalue())


class MetaGraphPublisher(_HTTPPublisher):
    name = "meta-graph"

    def __init__(self, connection: ConnectionResponse, tokens: TokenInput, version: str, *,
                 media_urls: PublicMediaURLProvider | None = None, client: httpx.AsyncClient | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        super().__init__(connection, tokens, client)
        if not re.fullmatch(r"v[0-9]+\.[0-9]+", version):
            raise ValueError("configure a versioned Meta Graph API")
        if not re.fullmatch(r"[0-9]{1,32}", connection.account_id):
            raise ValueError("invalid Meta account")
        self.base = f"https://graph.facebook.com/{version}"
        self.media_urls, self.sleep = media_urls, sleep

    async def _publish(self, client: httpx.AsyncClient, request: PublishRequest) -> DeliveryReceipt:
        images = [file for file in request.media if file.media_type == "image/png"]
        if not 1 <= len(images) <= 10:
            raise ProviderFailure("meta_images_required")
        proven_id = await (self._instagram(client, request, images) if request.platform == "instagram"
                           else self._facebook(client, request, images))
        return DeliveryReceipt(self.name, proven_id, "PUBLISHED")

    async def _ready(self, client: httpx.AsyncClient, identifier: str) -> None:
        for attempt in range(6):
            response = await self._request(client, "GET", f"{self.base}/{identifier}", params={"fields": "status_code"})
            state = _json(response).get("status_code")
            if state == "FINISHED":
                return
            if state in {"ERROR", "EXPIRED", "PUBLISHED"}:
                raise ProviderFailure("instagram_container_unusable")
            if attempt < 5:
                await self.sleep(5)
        raise ProviderFailure("instagram_container_pending", retry_after=30)

    async def _instagram(self, client: httpx.AsyncClient, request: PublishRequest,
                         images: list[DeliveryMedia]) -> str:
        if self.media_urls is None:
            raise ProviderFailure("instagram_public_media_adapter_required")
        base = f"{self.base}/{self.connection.account_id}"
        children = []
        carousel = len(images) > 1
        for image in images:
            jpeg = _instagram_jpeg(image)
            url = await self.media_urls.url(request.brand_id, request.idempotency_key, jpeg)
            try:
                url = public_url(url)
            except (ValueError, TypeError):
                raise ProviderFailure("instagram_media_url_not_public_https") from None
            data = {"image_url": url, "is_carousel_item": "true"} if carousel else {
                "image_url": url, "caption": request.caption,
            }
            result = await self._request(client, "POST", f"{base}/media", data=data)
            child = _identifier(result)
            await self._ready(client, child)
            children.append(child)
        if carousel:
            container = _identifier(await self._request(client, "POST", f"{base}/media", data={
                "media_type": "CAROUSEL", "children": ",".join(children), "caption": request.caption,
            }))
            await self._ready(client, container)
        else:
            container = children[0]
        result = await self._request(client, "POST", f"{base}/media_publish", final=True,
                                     idempotency_key=request.idempotency_key, data={"creation_id": container})
        return _identifier(result, final=True)

    async def _facebook(self, client: httpx.AsyncClient, request: PublishRequest,
                        images: list[DeliveryMedia]) -> str:
        base = f"{self.base}/{self.connection.account_id}"
        carousel = len(images) > 1
        attachments = []
        for image in images:
            if len(image.data) > 10 * 1024**2:
                raise ProviderFailure("facebook_image_too_large")
            data = {"published": "false"} if carousel else {"published": "true", "message": request.caption}
            result = await self._request(
                client, "POST", f"{base}/photos", final=not carousel, data=data,
                idempotency_key=request.idempotency_key if not carousel else None,
                files={"source": (image.filename, image.data, image.media_type)},
            )
            if not carousel:
                post_id = _json(result, final=True).get("post_id")
                if isinstance(post_id, str) and re.fullmatch(r"[0-9]+_[0-9]+", post_id):
                    return post_id
                return _identifier(result, final=True)
            attachments.append({"media_fbid": _identifier(result)})
        result = await self._request(client, "POST", f"{base}/feed", final=True, idempotency_key=request.idempotency_key,
                                     data={"message": request.caption, "attached_media": json.dumps(attachments)})
        post = _json(result, final=True).get("id")
        if not isinstance(post, str) or not re.fullmatch(r"[0-9]+(?:_[0-9]+)?", post):
            raise ProviderFailure("provider_receipt_missing", uncertain=True)
        return post


class LinkedInPublisher(_HTTPPublisher):
    name = "linkedin-posts"

    def __init__(self, connection: ConnectionResponse, tokens: TokenInput, version: str, *,
                 client: httpx.AsyncClient | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        super().__init__(connection, tokens, client)
        if not re.fullmatch(r"[0-9]{4}(?:0[1-9]|1[0-2])", version):
            raise ValueError("configure a LinkedIn YYYYMM API version")
        self.version, self.sleep = version, sleep
        self.base = "https://api.linkedin.com/rest"

    @property
    def headers(self) -> dict[str, str]:
        return {**super().headers, "LinkedIn-Version": self.version, "X-Restli-Protocol-Version": "2.0.0"}

    async def _publish(self, client: httpx.AsyncClient, request: PublishRequest) -> DeliveryReceipt:
        file = next((part for part in request.media if part.media_type == "application/pdf"), None)
        if file is None:
            file = next((part for part in request.media if part.media_type == "image/png"), None)
        if file is None:
            raise ProviderFailure("linkedin_media_required")
        document = file.media_type == "application/pdf"
        kind = "document" if document else "image"
        route = f"{self.base}/{kind}s"
        initialized = _json(await self._request(client, "POST", route, params={"action": "initializeUpload"},
                                                json={"initializeUploadRequest": {"owner": self.connection.account_id}}))
        value = initialized.get("value", {})
        if not isinstance(value, dict):
            raise ProviderFailure("linkedin_upload_response_invalid", retry_after=30)
        urn, upload = value.get(kind), value.get("uploadUrl")
        if not isinstance(urn, str) or not re.fullmatch(rf"urn:li:{kind}:[A-Za-z0-9_-]+", urn) or not isinstance(upload, str):
            raise ProviderFailure("linkedin_upload_response_invalid", retry_after=30)
        try:
            upload = public_url(upload)
            hostname = urlsplit(upload).hostname
        except ValueError:
            raise ProviderFailure("linkedin_upload_origin_invalid") from None
        if not any(hostname == domain or hostname.endswith("." + domain) for domain in ("linkedin.com", "licdn.com")):
            raise ProviderFailure("linkedin_upload_origin_invalid")
        await self._request(client, "PUT", upload, content=file.data, headers={
            "Authorization": self.headers["Authorization"], "Content-Type": file.media_type,
        })
        await self._available(client, route, urn)
        media = {"id": urn, "title": "Approved carousel"} if document else {"id": urn}
        response = await self._request(client, "POST", f"{self.base}/posts", final=True,
                                       idempotency_key=request.idempotency_key, json={
            "author": self.connection.account_id,
            "commentary": re.sub(r"([\\{}\[\]()<>@|~_*])", r"\\\1", request.caption),
            "visibility": "PUBLIC", "distribution": {
                "feedDistribution": "MAIN_FEED", "targetEntities": [], "thirdPartyDistributionChannels": [],
            },
            "content": {"media": media}, "lifecycleState": "PUBLISHED", "isReshareDisabledByAuthor": False,
        })
        identifier = response.headers.get("x-restli-id")
        if not identifier or not identifier.startswith(("urn:li:share:", "urn:li:ugcPost:")):
            raise ProviderFailure("provider_receipt_missing", uncertain=True)
        return DeliveryReceipt(self.name, identifier, "PUBLISHED")

    async def _available(self, client: httpx.AsyncClient, route: str, urn: str) -> None:
        for attempt in range(6):
            response = await self._request(client, "GET", f"{route}/{quote(urn, safe='')}")
            status = _json(response).get("status")
            if status == "AVAILABLE":
                return
            if status in {"PROCESSING_FAILED", "DELETED"}:
                raise ProviderFailure("linkedin_media_unusable")
            if attempt < 5:
                await self.sleep(5)
        raise ProviderFailure("linkedin_media_pending", retry_after=30)


class LinkedInTokenRefresher:
    """Refresh existing OAuth grants when the provisioned application supports refresh tokens."""

    def __init__(self, client_id: str, client_secret: str, client: httpx.AsyncClient | None = None):
        self.client_id, self._client_secret, self.client = client_id, client_secret, client

    async def __call__(self, previous: TokenInput) -> TokenInput:
        async def refresh(client: httpx.AsyncClient) -> TokenInput:
            try:
                result = await client.post("https://www.linkedin.com/oauth/v2/accessToken", follow_redirects=False, data={
                    "grant_type": "refresh_token", "refresh_token": previous.refresh_token.get_secret_value(),
                    "client_id": self.client_id, "client_secret": self._client_secret,
                })
            except httpx.HTTPError:
                raise ProviderFailure("oauth_refresh_unavailable", retry_after=30) from None
            if result.status_code == 429 or result.status_code >= 500:
                raise ProviderFailure("oauth_refresh_unavailable", retry_after=_delay(result))
            if not result.is_success:
                raise ProviderFailure("oauth_refresh_rejected")
            data = _json(result)
            try:
                expires = datetime.now(UTC) + timedelta(seconds=int(data["expires_in"]))
                return TokenInput(access_token=data["access_token"], refresh_token=data.get("refresh_token"), expires_at=expires)
            except (ValueError, KeyError, TypeError, OverflowError):
                raise ProviderFailure("oauth_refresh_response_invalid") from None

        if self.client:
            return await refresh(self.client)
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            return await refresh(client)
