from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from brandai.db.base import utcnow
from brandai.db.brand_models import ContentItem, ExternalEvidence, TopicResearch
from brandai.errors import GenerationBlockedError, NotFoundError
from brandai.onboarding.crawler import CrawledPage
from brandai.onboarding.search import SearchProvider
from brandai.store import BrandAIStore

from .models import ExternalCitation, TopicRequest, TopicResponse
from .network import public_url


SENTENCES = re.compile(r"[^\n]+?(?:[.!?](?=\s|$)|(?=\n|$))")
WORDS = re.compile(r"[^\W\d_]{3,}", re.UNICODE)
FINDING = re.compile(r"\d|\b(?:study|survey|found|reported|research|analysis|findings|estimated)\b|מחקר|סקר|נמצא", re.I)
CACHE_LIFETIME = timedelta(hours=24)


class TopicCrawler(Protocol):
    async def crawl(self, url: str) -> list[CrawledPage]: ...


def extract_quotes(body: str, topic: str) -> list[str]:
    terms = set(WORDS.findall(topic.casefold())) - {"the", "and", "for", "with", "about"}
    quotes = []
    for match in SENTENCES.finditer(body):
        sentence = match.group().strip()
        if (30 <= len(sentence) <= 450 and sentence.endswith((".", "!", "?"))
                and terms.intersection(WORDS.findall(sentence.casefold())) and FINDING.search(sentence)):
            quotes.append(sentence)
            if len(quotes) == 2:
                break
    return quotes


def citation(row: ExternalEvidence) -> ExternalCitation:
    return ExternalCitation(id=row.id, label=row.ordinal, url=row.url, title=row.title,
                            retrieved_at=row.retrieved_at.replace(tzinfo=UTC), sha256=row.sha256,
                            quoted_span=row.quoted_span)


def evidence_for_topic(session: Session, topic_id: str, content_id: str | None = None) -> tuple[ExternalCitation, ...]:
    topic = session.get(TopicResearch, topic_id)
    if topic is None or (topic.content_id is not None and topic.content_id != content_id):
        raise NotFoundError("research topic not found for this content")
    rows = session.scalars(select(ExternalEvidence).where(ExternalEvidence.topic_id == topic_id)
                           .order_by(ExternalEvidence.ordinal))
    return tuple(citation(row) for row in rows
                 if row.quoted_span and row.quoted_span in row.source_text
                 and hashlib.sha256(row.source_text.encode()).hexdigest() == row.sha256)


class TopicResearchService:
    def __init__(self, store: BrandAIStore, search: SearchProvider, crawler: TopicCrawler, *, enabled: bool = True):
        self.store, self.search, self.crawler, self.enabled = store, search, crawler, enabled

    def _view(self, session: Session, row: TopicResearch) -> TopicResponse:
        return TopicResponse(id=row.id, topic=row.topic, domains=row.domains, content_id=row.content_id,
                             created_at=row.created_at.replace(tzinfo=UTC),
                             evidence=list(evidence_for_topic(session, row.id, row.content_id)))

    def get(self, brand_id: str, topic_id: str) -> TopicResponse:
        with self.store.brand_session(brand_id) as session:
            row = session.get(TopicResearch, topic_id)
            if row is None:
                raise NotFoundError("research topic not found in this brand")
            return self._view(session, row)

    def list(self, brand_id: str, content_id: str | None) -> list[TopicResponse]:
        with self.store.brand_session(brand_id) as session:
            query = select(TopicResearch).order_by(TopicResearch.created_at.desc(), TopicResearch.id).limit(50)
            if content_id is not None:
                query = query.where(TopicResearch.content_id == content_id)
            return [self._view(session, row) for row in session.scalars(query)]

    @staticmethod
    def _cached(session: Session, key: str) -> TopicResearch | None:
        cutoff = (utcnow() - CACHE_LIFETIME).replace(tzinfo=None)
        return session.scalar(select(TopicResearch).where(TopicResearch.cache_key == key, TopicResearch.created_at >= cutoff)
                              .order_by(TopicResearch.created_at.desc()).limit(1))

    def run(self, brand_id: str, payload: TopicRequest) -> tuple[TopicResponse, bool]:
        content_id = str(payload.content_id) if payload.content_id else None
        key = hashlib.sha256(json.dumps([" ".join(payload.topic.casefold().split()), payload.domains, content_id]).encode()).hexdigest()
        with self.store.brand_session(brand_id) as session:
            if content_id and session.get(ContentItem, content_id) is None:
                raise NotFoundError("research content item not found in this brand")
            if not payload.refresh and (cached := self._cached(session, key)):
                return self._view(session, cached), False
        if not self.enabled:
            raise GenerationBlockedError("live topic research is disabled and no cached brief matches")
        discovered = asyncio.run(self._collect(payload))
        with self.store.brand_session(brand_id, immediate=True) as session:
            if not payload.refresh and (cached := self._cached(session, key)):
                return self._view(session, cached), False
            row = TopicResearch(topic=payload.topic, domains=payload.domains, content_id=content_id, cache_key=key)
            session.add(row)
            session.flush()
            session.add_all(ExternalEvidence(
                topic_id=row.id, ordinal=index, url=page.url, title=page.title[:500] or urlsplit(page.url).hostname,
                retrieved_at=retrieved, sha256=hashlib.sha256(page.text.encode()).hexdigest(),
                quoted_span=span, source_text=page.text,
            ) for index, (page, retrieved, span) in enumerate(discovered, start=1))
            session.flush()
            return self._view(session, row), True

    async def _collect(self, payload: TopicRequest) -> list[tuple[CrawledPage, datetime, str]]:
        async def fetch(url: str) -> list[CrawledPage]:
            try:
                async with asyncio.timeout(16):
                    return await self.crawler.crawl(url)
            except (ValueError, httpx.HTTPError, TimeoutError):
                return []

        query = payload.topic
        if payload.domains:
            query += " (" + " OR ".join(f"site:{name}" for name in payload.domains) + ")"
        try:
            async with asyncio.timeout(18):
                search_results = await self.search.search(query, limit=6)
        except (TimeoutError, httpx.HTTPError):
            raise GenerationBlockedError("topic search is currently unavailable") from None
        urls = []
        domains_seen = set()
        for result in search_results[:6]:
            try:
                url = public_url(result.url)
            except ValueError:
                continue
            host = urlsplit(url).hostname
            if host in domains_seen or (payload.domains and not any(
                host == domain or host.endswith("." + domain) for domain in payload.domains
            )):
                continue
            domains_seen.add(host)
            urls.append(url)
        output = []
        seen_quotes = set()
        for url, pages in zip(urls, await asyncio.gather(*(fetch(url) for url in urls)), strict=True):
            for page in pages[:1]:
                if page.url != url or not 200 <= page.status_code < 300 or len(page.text) > 1_000_000:
                    continue
                retrieved = utcnow()
                for span in extract_quotes(page.text, payload.topic):
                    if span not in seen_quotes:
                        seen_quotes.add(span)
                        output.append((page, retrieved, span))
        return output[:8]
