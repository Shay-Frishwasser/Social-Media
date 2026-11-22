from __future__ import annotations

from pathlib import Path
from typing import Protocol, Sequence

import numpy as np


class EmbeddingProvider(Protocol):
    model_name: str
    def embed_passages(self, texts: Sequence[str]) -> np.ndarray: ...
    def embed_queries(self, texts: Sequence[str]) -> np.ndarray: ...


class E5EmbeddingProvider:
    model_name = "intfloat/multilingual-e5-base"

    def __init__(self, model_path: Path):
        self.model_path = model_path
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(str(self.model_path), device="cpu", local_files_only=True)
        return self._model

    def _encode(self, texts: Sequence[str], prefix: str) -> np.ndarray:
        values = [text if text.startswith(prefix) else prefix + text for text in texts]
        return np.asarray(self._load().encode(values, normalize_embeddings=True, show_progress_bar=False), dtype=np.float32)

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode(texts, "passage: ")

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode(texts, "query: ")
