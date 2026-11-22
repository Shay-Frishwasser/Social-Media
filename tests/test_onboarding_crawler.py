from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from brandai.onboarding.crawler import WebsiteCrawler
from brandai.onboarding.search import FallbackSearchProvider, SearchResult


class SiteHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        routes = {
            "/robots.txt": ("text/plain", "User-agent: *\nDisallow: /private\n"),
            "/": ("text/html", '<html><head><title>Home</title><link rel="stylesheet" href="/style.css"></head><body><main>Acme builds secure widgets for teams.</main><a href="/about">About</a><a href="/app">App</a><a href="/private">Private</a></body></html>'),
            "/style.css": ("text/css", "body{color:#123456;font-family:Inter,sans-serif}"),
            "/about": ("text/html", "<html><title>About</title><body><article>Acme was founded to make reliable tools for small teams. Our support is based in Haifa.</article></body></html>"),
            "/app": ("text/html", '<html><title>App</title><body><div id="root"></div><script>document.getElementById("root").innerHTML="<main>Rendered pricing starts at 20 dollars per month for teams.</main>"</script></body></html>'),
            "/private": ("text/html", "<html><body>SECRET SHOULD NEVER BE FETCHED</body></html>"),
        }
        media, body = routes.get(self.path, ("text/plain", "missing"))
        self.send_response(200 if self.path in routes else 404)
        self.send_header("Content-Type", media + "; charset=utf-8")
        self.end_headers(); self.wfile.write(body.encode())
    def log_message(self, *_): pass


class FakeRenderer:
    calls = []
    async def render(self, url):
        self.calls.append(url)
        return "<html><title>Rendered App</title><body><main>Rendered pricing starts at 20 dollars per month for teams.</main></body></html>"


def test_static_js_and_robots_crawling():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        renderer = FakeRenderer()
        pages = asyncio.run(WebsiteCrawler(renderer=renderer, per_domain_interval=0, max_pages=5,
                                           min_static_chars=40).crawl(f"http://127.0.0.1:{server.server_port}/"))
        urls = {page.url: page for page in pages}
        assert not any("private" in url for url in urls)
        assert any(url.endswith("/about") for url in urls)
        app = next(page for url, page in urls.items() if url.endswith("/app"))
        assert app.rendered and "pricing" in app.text.lower()
        assert len(renderer.calls) == 1
        assert "#123456" in urls[f"http://127.0.0.1:{server.server_port}/"].raw_html
    finally:
        server.shutdown(); server.server_close(); thread.join()


class BrokenSearch:
    async def search(self, query, limit=8): raise RuntimeError("rate limited")
class WorkingSearch:
    async def search(self, query, limit=8): return [SearchResult("Official", "https://example.test", "mention")]


def test_search_degrades_and_falls_back():
    result = asyncio.run(FallbackSearchProvider([BrokenSearch(), WorkingSearch()]).search("Acme"))
    assert result[0].title == "Official"
    assert asyncio.run(FallbackSearchProvider([BrokenSearch()]).search("Acme")) == []


def test_real_playwright_renders_local_js(monkeypatch):
    from brandai.onboarding.crawler import PlaywrightRenderer
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", r"C:\brandai-runtime\cache\playwright")
    server = ThreadingHTTPServer(("127.0.0.1", 0), SiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        pages = asyncio.run(WebsiteCrawler(renderer=PlaywrightRenderer(), per_domain_interval=0, max_pages=1,
                                           min_static_chars=40).crawl(f"http://127.0.0.1:{server.server_port}/app"))
        assert pages[0].rendered and "Rendered pricing" in pages[0].text
    finally:
        server.shutdown(); server.server_close(); thread.join()
