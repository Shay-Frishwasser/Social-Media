import numpy as np

from brandai.onboarding.embeddings import E5EmbeddingProvider
from brandai.onboarding.ingestion import EvidenceIngestor, chunk_text
from brandai.repositories import SourceRepository


def test_e5_prefix_embedding_chunk_and_fts(store, two_brands, tmp_path, monkeypatch):
    brand, _ = two_brands
    calls = []

    class StubEncoder:
        def encode(self, values, *, normalize_embeddings, show_progress_bar):
            assert normalize_embeddings and not show_progress_bar
            calls.extend(values)
            return np.broadcast_to(np.eye(1, 768, dtype=np.float32), (len(values), 768))

    provider = E5EmbeddingProvider(tmp_path / "model")
    monkeypatch.setattr(provider, "_load", lambda: StubEncoder())
    with store.brand_session(brand.id) as session:
        result = EvidenceIngestor(session, provider).ingest_text(
            kind="user_sample_post", title="Hebrew sample",
            text="אנחנו מספקים פתרונות אבטחת מידע לעסקים קטנים.\n\nהשירות ניתן מחיפה.")
        assert result.chunk_ids
        hits = SourceRepository(session).fts_search("אבט")
        assert hits and hits[0].embedding_dim == 768
        query = provider.embed_queries(["אבטחת מידע"])[0]
        assert SourceRepository(session).vector_search(query)[0][0].id == hits[0].id
    assert all(value.startswith("passage: ") for value in calls[:-1])
    assert calls[-1] == "query: אבטחת מידע"


def test_chunking_is_bounded_and_overlapping():
    chunks = chunk_text("א" * 1500, max_chars=500, overlap_chars=50)
    assert len(chunks) >= 3 and all(len(chunk) <= 500 for chunk in chunks)
