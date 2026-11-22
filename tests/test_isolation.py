from pathlib import Path

import pytest
from sqlalchemy import select

from brandai.db.brand_models import Source
from brandai.errors import BrandIsolationError
from brandai.repositories import AssetRepository, MemoryRepository, SourceRepository


def test_physical_vector_fts_and_asset_isolation(store, two_brands):
    a, b = two_brands
    assert Path(a.db_path) != Path(b.db_path)
    assert Path(a.db_path).is_file() and Path(b.db_path).is_file()

    asset_a = Path(a.assets_path) / "logo-a.svg"
    asset_a.write_text("<svg/>", encoding="utf-8")
    with store.brand_session(a.id) as session:
        sources = SourceRepository(session)
        source = sources.add_source(kind="website", url="https://a.test")
        sources.add_chunk(source.id, 0, "פתרונות אבטחת מידע לעסקים", [1.0, 0.0])
        AssetRepository(session, Path(a.assets_path)).add(asset_a, kind="logo")
        MemoryRepository(session).add(angle="risk", hook="hook A", topic="security", pillar="education",
                                      visual_concept="shield", embedding=[1.0, 0.0])

    with store.brand_session(a.id) as session:
        assert "אבטחת" in SourceRepository(session).fts_search("אבט")[0].text
        assert SourceRepository(session).vector_search([1.0, 0.0])[0][1] == pytest.approx(1.0)
        assert MemoryRepository(session).similar([1.0, 0.0])[0][0].hook == "hook A"

    with store.brand_session(b.id) as session:
        assert session.scalar(select(Source)) is None
        assert SourceRepository(session).fts_search("אבט") == []
        assert SourceRepository(session).vector_search([1.0, 0.0]) == []
        assert MemoryRepository(session).similar([1.0, 0.0]) == []
        with pytest.raises(BrandIsolationError):
            AssetRepository(session, Path(b.assets_path)).add(asset_a, kind="logo")
