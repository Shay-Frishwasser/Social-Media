from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from .crawler import CrawledPage, WebsiteCrawler
from .embeddings import EmbeddingProvider
from .extraction import ExtractionOutcome, GroundedBrandExtractor
from .ingestion import EvidenceIngestor
from .llm import StructuredLLMProvider
from .search import SearchProvider
from .visual import VisualSignals, extract_visual_signals


@dataclass(slots=True)
class ResearchResult:
    pages: list[CrawledPage]
    discovery_count: int
    source_ids: list[str]
    visual_signals: VisualSignals


class BrandResearchPipeline:
    def __init__(self, session: Session, *, crawler: WebsiteCrawler, search: SearchProvider,
                 embeddings: EmbeddingProvider):
        self.session, self.crawler, self.search, self.embeddings = session, crawler, search, embeddings

    async def research(
        self, website_url: str, company_name: str, *,
        social_handles: Sequence[str] = (), on_pages: Callable[[], None] | None = None,
        on_embeddings: Callable[[], None] | None = None,
    ) -> ResearchResult:
        pages = await self.crawler.crawl(website_url)
        if not pages: raise ValueError("no crawlable website pages were found")
        if on_pages:
            on_pages()
        domain = urlparse(website_url).netloc
        discoveries = await self.search.search(f'"{company_name}" {domain} reviews OR YouTube OR profile', 8)
        for handle in social_handles:
            discoveries.extend(await self.search.search(f'"{company_name}" "{handle}"', 2))
        ingestor = EvidenceIngestor(self.session, self.embeddings)
        prepared = [ingestor.prepare_page(page) for page in pages]
        prepared.extend(ingestor.prepare_text(kind="public_discovery", text=result.snippet or result.title,
                                              url=result.url, title=result.title, media_type="text/plain",
                                              metadata={"connector": "search", "unverified_mention": True})
                        for result in discoveries)
        # Publish progress before opening the brand database's single write transaction.
        if on_embeddings:
            on_embeddings()
        source_ids = [ingestor.save(item).source_id for item in prepared]
        combined_html = "\n".join(page.raw_html for page in pages)
        visual = extract_visual_signals(combined_html, website_url)
        return ResearchResult(pages, len(discoveries), source_ids, visual)

    def extract(self, research: ResearchResult, llm: StructuredLLMProvider,
                *, created_by: str = "local-ai") -> ExtractionOutcome:
        return GroundedBrandExtractor(self.session, llm).extract(research.visual_signals, created_by=created_by)
