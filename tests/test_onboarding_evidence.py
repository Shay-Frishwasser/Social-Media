from pathlib import Path

import numpy as np

from brandai.onboarding.embeddings import E5EmbeddingProvider
from brandai.onboarding.ingestion import EvidenceIngestor, chunk_text
from brandai.repositories import SourceRepository


def test_real_cpu_e5_prefix_embedding_chunk_and_fts(store, two_brands):
    brand, _ = two_brands
    model = Path(r"C:\brandai-runtime\models\embeddings\multilingual-e5-base")
    provider = E5EmbeddingProvider(model)
    with store.brand_session(brand.id) as session:
        result = EvidenceIngestor(session, provider).ingest_text(
            kind="user_sample_post", title="Hebrew sample",
            text="אנחנו מספקים פתרונות אבטחת מידע לעסקים קטנים.\n\nהשירות ניתן מחיפה.")
        assert result.chunk_ids
        hits = SourceRepository(session).fts_search("אבט")
        assert hits and hits[0].embedding_dim == 768
        query = provider.embed_queries(["אבטחת מידע"])[0]
        assert SourceRepository(session).vector_search(query)[0][0].id == hits[0].id


def test_chunking_is_bounded_and_overlapping():
    chunks = chunk_text("א" * 1500, max_chars=500, overlap_chars=50)
    assert len(chunks) >= 3 and all(len(chunk) <= 500 for chunk in chunks)
