from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from brandai.repositories import SourceRepository
from .crawler import CrawledPage
from .embeddings import EmbeddingProvider


def chunk_text(text: str, max_chars: int = 1200, overlap_chars: int = 160) -> list[str]:
    clean = re.sub(r"[ \t]+", " ", text).strip()
    if not clean: return []
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", clean) if p.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        pieces = [paragraph[i:i + max_chars] for i in range(0, len(paragraph), max_chars)]
        for piece in pieces:
            candidate = f"{current}\n\n{piece}".strip() if current else piece
            if len(candidate) <= max_chars:
                current = candidate
            else:
                if current: chunks.append(current)
                prefix = current[-overlap_chars:] if current and overlap_chars else ""
                current = f"{prefix}\n{piece}".strip()
                if len(current) > max_chars:
                    chunks.append(current[:max_chars]); current = current[max_chars-overlap_chars:]
    if current: chunks.append(current)
    return chunks


@dataclass(slots=True)
class IngestionResult:
    source_id: str
    chunk_ids: list[str]


class EvidenceIngestor:
    def __init__(self, session: Session, embeddings: EmbeddingProvider):
        self.repo = SourceRepository(session)
        self.embeddings = embeddings

    def ingest_page(self, page: CrawledPage) -> IngestionResult:
        return self.ingest_text(kind="website", text=page.text, url=page.url, title=page.title,
                                raw_content=page.raw_html, media_type="text/html",
                                metadata={"rendered": page.rendered, "status_code": page.status_code})

    def ingest_text(self, *, kind: str, text: str, url: str | None = None, title: str | None = None,
                    raw_content: str | None = None, media_type: str = "text/plain",
                    metadata: dict | None = None) -> IngestionResult:
        source = self.repo.add_source(kind=kind, url=url, title=title, content=raw_content or text,
                                      media_type=media_type, metadata=metadata)
        chunks = chunk_text(text)
        vectors = self.embeddings.embed_passages(chunks) if chunks else []
        rows = [self.repo.add_chunk(source.id, i, chunk, vectors[i], self.embeddings.model_name)
                for i, chunk in enumerate(chunks)]
        return IngestionResult(source.id, [row.id for row in rows])
