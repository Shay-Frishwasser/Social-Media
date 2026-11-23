from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
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


@dataclass(slots=True)
class PreparedEvidence:
    kind: str
    url: str | None
    title: str | None
    content: str
    media_type: str
    metadata: dict | None
    chunks: list[str]
    vectors: np.ndarray | list


class EvidenceIngestor:
    def __init__(self, session: Session, embeddings: EmbeddingProvider):
        self.repo = SourceRepository(session)
        self.embeddings = embeddings

    def prepare_page(self, page: CrawledPage) -> PreparedEvidence:
        return self.prepare_text(kind="website", text=page.text, url=page.url, title=page.title,
                                 raw_content=page.raw_html, media_type="text/html",
                                 metadata={"rendered": page.rendered, "status_code": page.status_code})

    def ingest_page(self, page: CrawledPage) -> IngestionResult:
        return self.save(self.prepare_page(page))

    def prepare_text(self, *, kind: str, text: str, url: str | None = None, title: str | None = None,
                     raw_content: str | None = None, media_type: str = "text/plain",
                     metadata: dict | None = None) -> PreparedEvidence:
        chunks = chunk_text(text)
        vectors = self.embeddings.embed_passages(chunks) if chunks else []
        return PreparedEvidence(kind, url, title, raw_content or text, media_type, metadata, chunks, vectors)

    def ingest_text(self, *, kind: str, text: str, url: str | None = None, title: str | None = None,
                    raw_content: str | None = None, media_type: str = "text/plain",
                    metadata: dict | None = None) -> IngestionResult:
        return self.save(self.prepare_text(kind=kind, text=text, url=url, title=title, raw_content=raw_content,
                                           media_type=media_type, metadata=metadata))

    def save(self, value: PreparedEvidence) -> IngestionResult:
        source = self.repo.add_source(kind=value.kind, url=value.url, title=value.title, content=value.content,
                                      media_type=value.media_type, metadata=value.metadata)
        rows = [self.repo.add_chunk(source.id, i, chunk, value.vectors[i], self.embeddings.model_name)
                for i, chunk in enumerate(value.chunks)]
        return IngestionResult(source.id, [row.id for row in rows])
