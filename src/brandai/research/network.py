from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit

import httpx

from brandai.onboarding.crawler import CrawledPage, WebsiteCrawler


DOMAIN = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", re.I)
MAX_DOCUMENT = 1_500_000


def domain_name(value: str) -> str:
    name = value.strip().rstrip(".").encode("idna").decode("ascii").lower()
    if len(name) > 253 or not DOMAIN.fullmatch(name) or name.endswith((".localhost", ".local", ".internal", ".onion")):
        raise ValueError("use a public DNS domain name")
    return name


def public_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.port not in (None, 443):
        raise ValueError("research sources require public HTTPS on port 443")
    host = domain_name(parsed.hostname)
    return parsed._replace(netloc=host, fragment="").geturl()


def _public_address(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    return ip.is_global and not (ip.is_multicast or ip.is_reserved or getattr(ip, "is_site_local", False))


class PublicResearchTransport(httpx.AsyncBaseTransport):
    """Resolve once per request, then connect to a validated address with the original TLS identity."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            host = urlsplit(public_url(str(request.url))).hostname
            async with asyncio.timeout(5):
                records = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addresses = list(dict.fromkeys(record[4][0] for record in records))
            if not addresses or not all(_public_address(address) for address in addresses):
                raise ValueError("address is outside the public web")
        except (ValueError, OSError, TimeoutError) as exc:
            raise httpx.ConnectError("source did not resolve exclusively to public addresses", request=request) from exc

        # Do not pool connections across hostnames that resolve to the same address.
        headers = httpx.Headers(request.headers)
        headers["Accept-Encoding"] = "identity"
        pinned = httpx.Request(request.method, request.url.copy_with(host=addresses[0]), headers=headers,
                               extensions={**request.extensions, "sni_hostname": host})
        async with httpx.AsyncHTTPTransport(trust_env=False) as transport:
            response = await transport.handle_async_request(pinned)
            try:
                body = bytearray()
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise httpx.ReadError("unsupported research response encoding", request=request)
                async for chunk in response.aiter_raw():
                    if len(body) + len(chunk) > MAX_DOCUMENT:
                        raise httpx.ReadError("research response exceeds the size limit", request=request)
                    body.extend(chunk)
                headers = response.headers.copy()
                headers.pop("content-encoding", None)
                headers.pop("content-length", None)
                return httpx.Response(response.status_code, headers=headers, content=bytes(body))
            finally:
                await response.aclose()


class PublicTopicCrawler:
    async def crawl(self, url: str) -> list[CrawledPage]:
        async with httpx.AsyncClient(
            transport=PublicResearchTransport(), trust_env=False, follow_redirects=False, timeout=10,
            headers={"User-Agent": "BrandAIResearchBot/0.7", "Accept": "text/html,text/plain"},
        ) as client:
            crawler = WebsiteCrawler(client=client, user_agent="BrandAIResearchBot", max_pages=1,
                                     strict_robots=True, collect_stylesheets=False, per_domain_interval=1)
            return await crawler.crawl(public_url(url))
