from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider(Protocol):
    async def search(self, query: str, limit: int = 8) -> list[SearchResult]: ...


class DDGSSearchProvider:
    """Best-effort public discovery. Any upstream failure degrades to no results."""
    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        def run():
            from ddgs import DDGS
            return list(DDGS().text(query, max_results=limit))
        try:
            rows = await asyncio.to_thread(run)
            return [SearchResult(title=r.get("title", ""), url=r.get("href", ""), snippet=r.get("body", ""))
                    for r in rows if r.get("href")]
        except Exception:
            return []


class FallbackSearchProvider:
    def __init__(self, providers: list[SearchProvider]): self.providers = providers

    async def search(self, query: str, limit: int = 8) -> list[SearchResult]:
        for provider in self.providers:
            try:
                results = await provider.search(query, limit)
                if results: return results[:limit]
            except Exception:
                continue
        return []
