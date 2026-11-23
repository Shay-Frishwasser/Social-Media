from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from brandai.repositories import AssetRepository, SourceRepository
from .embeddings import EmbeddingProvider
from .ingestion import EvidenceIngestor
from .safe_svg import static_svg


MAX_ASSET_BYTES = 25 * 1024 * 1024
SAFE_EXTENSIONS = {".png", ".svg", ".jpg", ".jpeg", ".webp"}


class MaterialIngestor:
    def __init__(self, session: Session, assets_dir: Path, embeddings: EmbeddingProvider):
        self.session = session
        self.embeddings = embeddings
        self.assets_dir = assets_dir.resolve()
        self.assets_dir.mkdir(parents=True, exist_ok=True)

    def ingest_sample_post(self, text: str, *, platform: str, label: str = "User sample"):
        if not text.strip(): raise ValueError("sample post cannot be empty")
        return EvidenceIngestor(self.session, self.embeddings).ingest_text(
            kind="user_sample_post", title=label, text=text, media_type="text/plain",
            metadata={"platform": platform, "user_supplied": True})

    def ingest_asset(self, source_path: Path, *, kind: str):
        source_path = source_path.resolve()
        if source_path.suffix.lower() not in SAFE_EXTENSIONS: raise ValueError("unsupported asset type")
        if source_path.stat().st_size > MAX_ASSET_BYTES: raise ValueError("asset exceeds 25 MiB")
        metadata = self._validate(source_path)
        digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
        destination = self.assets_dir / f"{digest[:16]}{source_path.suffix.lower()}"
        if not destination.exists(): shutil.copy2(source_path, destination)
        asset = AssetRepository(self.session, self.assets_dir).add(destination, kind=kind, metadata=metadata)
        SourceRepository(self.session).add_source(
            kind="user_asset", title=source_path.name, content=f"sha256:{digest}",
            media_type=metadata["media_type"], metadata={**metadata, "asset_id": asset.id, "user_supplied": True})
        return asset

    @staticmethod
    def _validate(path: Path) -> dict:
        if path.suffix.lower() == ".svg":
            _, width, height, view_box = static_svg(path.read_bytes())
            return {"media_type": "image/svg+xml", "width": width, "height": height,
                    "view_box": view_box, "has_alpha": True}
        with Image.open(path) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"}:
                raise ValueError("unsupported raster format")
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
            if width < 16 or height < 16: raise ValueError("image dimensions are too small")
            if width * height > 100_000_000: raise ValueError("image dimensions are unsafe")
            has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
            return {"media_type": Image.MIME.get(image.format, "application/octet-stream"),
                    "width": width, "height": height, "has_alpha": has_alpha, "format": image.format}
