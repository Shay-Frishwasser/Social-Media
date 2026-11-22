from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from brandai.onboarding.assets import MaterialIngestor


class TinyEmbeddings:
    model_name = "fake"
    def embed_passages(self, texts): return np.ones((len(texts), 2), dtype=np.float32)
    def embed_queries(self, texts): return np.ones((len(texts), 2), dtype=np.float32)


def test_png_logo_and_sample_post_ingestion(store, two_brands, tmp_path):
    brand, _ = two_brands
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (128, 64), (1, 2, 3, 0)).save(logo)
    with store.brand_session(brand.id) as session:
        ingestor = MaterialIngestor(session, Path(brand.assets_path), TinyEmbeddings())
        asset = ingestor.ingest_asset(logo, kind="logo")
        assert Path(asset.path).is_relative_to(Path(brand.assets_path))
        assert asset.metadata_json["has_alpha"] is True
        assert ingestor.ingest_sample_post("פוסט לדוגמה", platform="instagram").chunk_ids


def test_active_svg_is_rejected(store, two_brands, tmp_path):
    brand, _ = two_brands
    svg = tmp_path / "bad.svg"
    svg.write_text('<svg width="20" height="20"><script>alert(1)</script></svg>', encoding="utf-8")
    with pytest.raises(ValueError, match="unsafe"):
        with store.brand_session(brand.id) as session:
            MaterialIngestor(session, Path(brand.assets_path), TinyEmbeddings()).ingest_asset(svg, kind="logo")
