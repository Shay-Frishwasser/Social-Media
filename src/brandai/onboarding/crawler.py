from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import httpx
import trafilatura
from bs4 import BeautifulSoup


KEY_TERMS = ("about", "services", "products", "pricing", "faq", "case", "contact",
             "אודות", "שירות", "מוצר", "מחיר", "שאלות", "צור-קשר")


@dataclass(slots=True)
class CrawledPage:
    url: str
    title: str
    raw_html: str
    text: str
    status_code: int
    rendered: bool = False
    links: list[str] = field(default_factory=list)


class PageRenderer(Protocol):
    async def render(self, url: str) -> str: ...


class PlaywrightRenderer:
    def __init__(self, timeout_ms: int = 20_000): self.timeout_ms = timeout_ms

    async def render(self, url: str) -> str:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=self.timeout_ms)
                return await page.content()
            finally:
                await browser.close()


class DomainRateLimiter:
    def __init__(self, interval_seconds: float):
        self.interval = interval_seconds
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, url: str) -> None:
        domain = urlparse(url).netloc.lower()
        lock = self._locks.setdefault(domain, asyncio.Lock())
        async with lock:
            delay = self.interval - (time.monotonic() - self._last.get(domain, 0.0))
            if delay > 0: await asyncio.sleep(delay)
            self._last[domain] = time.monotonic()


class WebsiteCrawler:
    def __init__(self, *, client: httpx.AsyncClient | None = None, renderer: PageRenderer | None = None,
                 user_agent: str = "BrandAIResearchBot/0.3 (+local human-reviewed onboarding)",
                 per_domain_interval: float = 1.0, max_pages: int = 12, min_static_chars: int = 120):
        self.client = client
        self.renderer = renderer
        self.user_agent = user_agent
        self.max_pages = max_pages
        self.min_static_chars = min_static_chars
        self.limiter = DomainRateLimiter(per_domain_interval)
        self._robots: dict[str, RobotFileParser] = {}

    async def _robots_for(self, url: str, client: httpx.AsyncClient) -> RobotFileParser:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots: return self._robots[origin]
        parser = RobotFileParser()
        robots_url = origin + "/robots.txt"
        try:
            await self.limiter.wait(robots_url)
            response = await client.get(robots_url)
            parser.parse(response.text.splitlines() if response.status_code < 400 else [])
        except httpx.HTTPError:
            parser.parse([])
        self._robots[origin] = parser
        return parser

    async def crawl(self, start_url: str) -> list[CrawledPage]:
        parsed_start = urlparse(start_url)
        if parsed_start.scheme not in {"http", "https"} or not parsed_start.netloc:
            raise ValueError("start_url must be an absolute HTTP(S) URL")
        headers = {"User-Agent": self.user_agent, "Accept": "text/html,application/xhtml+xml"}
        owned = self.client is None
        client = self.client or httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20)
        queue = [urldefrag(start_url)[0]]
        seen: set[str] = set()
        pages: list[CrawledPage] = []
        try:
            while queue and len(pages) < self.max_pages:
                queue.sort(key=self._priority, reverse=True)
                url = queue.pop(0)
                if url in seen: continue
                seen.add(url)
                if urlparse(url).netloc.lower() != parsed_start.netloc.lower(): continue
                robots = await self._robots_for(url, client)
                if not robots.can_fetch(self.user_agent, url): continue
                await self.limiter.wait(url)
                try:
                    response = await client.get(url)
                    if response.status_code >= 400 or "html" not in response.headers.get("content-type", ""):
                        continue
                except httpx.HTTPError:
                    continue
                html = response.text
                extracted = self._extract(html, url)
                rendered = False
                if self.renderer and self._needs_render(html, extracted):
                    html = await self.renderer.render(url)
                    extracted = self._extract(html, url)
                    rendered = True
                html = await self._with_stylesheets(html, url, parsed_start.netloc, robots, client)
                soup = BeautifulSoup(html, "html.parser")
                links = self._links(soup, url, parsed_start.netloc)
                page = CrawledPage(url=url, title=self._title(soup), raw_html=html,
                                   text=extracted, status_code=response.status_code,
                                   rendered=rendered, links=links)
                pages.append(page)
                queue.extend(link for link in links if link not in seen)
        finally:
            if owned: await client.aclose()
        return pages

    async def _with_stylesheets(self, html: str, page_url: str, domain: str,
                                robots: RobotFileParser, client: httpx.AsyncClient) -> str:
        soup = BeautifulSoup(html, "html.parser")
        styles = []
        for node in soup.select("link[rel~='stylesheet'][href]")[:5]:
            css_url = urljoin(page_url, node.get("href"))
            if urlparse(css_url).netloc.lower() != domain.lower() or not robots.can_fetch(self.user_agent, css_url):
                continue
            try:
                await self.limiter.wait(css_url)
                response = await client.get(css_url)
                if response.status_code < 400 and len(response.content) <= 1_000_000:
                    styles.append(f'<style data-source-url="{css_url}">{response.text}</style>')
            except httpx.HTTPError:
                continue
        return html + "\n" + "\n".join(styles)

    @staticmethod
    def _extract(html: str, url: str) -> str:
        return (trafilatura.extract(html, url=url, include_links=False, include_images=False,
                                    favor_precision=True) or "").strip()

    def _needs_render(self, html: str, text: str) -> bool:
        indicators = ("__NEXT_DATA__", "id=\"root\"", "id=\"app\"", "enable javascript")
        return len(text) < self.min_static_chars and ("<script" in html.lower() or any(x in html.lower() for x in indicators))

    @staticmethod
    def _title(soup: BeautifulSoup) -> str:
        return soup.title.get_text(" ", strip=True) if soup.title else ""

    @staticmethod
    def _links(soup: BeautifulSoup, base: str, domain: str) -> list[str]:
        result = []
        for anchor in soup.select("a[href]"):
            url = urldefrag(urljoin(base, anchor.get("href")))[0]
            parsed = urlparse(url)
            if parsed.scheme in {"http", "https"} and parsed.netloc.lower() == domain.lower():
                if not re.search(r"\.(?:pdf|jpe?g|png|gif|svg|zip)(?:$|\?)", url, re.I): result.append(url)
        return list(dict.fromkeys(result))

    @staticmethod
    def _priority(url: str) -> tuple[int, int]:
        lowered = url.lower()
        return (sum(term in lowered for term in KEY_TERMS), -len(urlparse(url).path))
