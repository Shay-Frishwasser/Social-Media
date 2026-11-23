from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import blake2s

import numpy as np

from brandai.onboarding.embeddings import EmbeddingProvider

from .models import DraftCopy, Violation
from .validator import caption_text


@dataclass(frozen=True, slots=True)
class DiversitySettings:
    history_limit: int = 20
    lexical_threshold: float = 0.76
    semantic_threshold: float = 0.92
    hook_threshold: float = 0.86

    def __post_init__(self) -> None:
        if not 1 <= self.history_limit <= 100:
            raise ValueError("content history_limit must be between 1 and 100")
        if any(not math.isfinite(value) or not 0 < value <= 1 for value in (
            self.lexical_threshold, self.semantic_threshold, self.hook_threshold,
        )):
            raise ValueError("diversity thresholds must be finite and within (0, 1]")


@dataclass(frozen=True, slots=True)
class PriorCopy:
    content_id: str
    hook: str
    body: str
    cta: str
    embedding: bytes | None
    embedding_model: str | None


def tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", text).casefold()
    unaccented = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return re.findall(r"[^\W_]+", unaccented)


def _shingles(text: str) -> set[tuple[str, ...]]:
    words = tokens(text)
    width = min(3, len(words))
    return {tuple(words[index:index + width]) for index in range(len(words) - width + 1)} if width else set()


def _opening(hook: str) -> list[str]:
    return ["number" if word.isdecimal() else word for word in tokens(hook)[:12]]


def _same_pattern(left: list[str], right: list[str]) -> bool:
    if not left or not right:
        return False
    return left == right or (len(left) >= 3 and len(right) >= 3 and left[:3] == right[:3])


def _jaccard(left: set, right: set) -> float:
    return len(left & right) / len(left | right) if left or right else 0.0


class HashEmbeddingProvider:
    """Stable, CPU-light embedding double with the same passage/query interface as E5."""

    model_name = "brandai-test-hash-v1"

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode(texts, "passage: ")

    def embed_queries(self, texts: Sequence[str]) -> np.ndarray:
        return self._encode(texts, "query: ")

    @staticmethod
    def _encode(texts: Sequence[str], prefix: str) -> np.ndarray:
        rows = np.zeros((len(texts), 512), dtype=np.float32)
        for row, text in zip(rows, texts, strict=True):
            words = tokens(text.removeprefix(prefix))
            features = [*words, *(f"{left} {right}" for left, right in zip(words, words[1:]))]
            for feature in features:
                digest = blake2s(feature.encode(), digest_size=4).digest()
                row[int.from_bytes(digest[:2], "big") % row.size] += 1 if digest[2] % 2 else -1
        return rows


class DiversityScorer:
    def __init__(self, embeddings: EmbeddingProvider, settings: DiversitySettings):
        self.embeddings, self.settings = embeddings, settings

    def encode(self, copy: DraftCopy) -> bytes:
        vectors = np.asarray(self.embeddings.embed_passages([caption_text(copy)]), dtype="<f4")
        if vectors.ndim != 2 or vectors.shape[0] != 1 or not vectors.shape[1] or not np.isfinite(vectors).all():
            raise ValueError("embedding provider returned an invalid content vector")
        return vectors[0].tobytes()

    def violations(self, copy: DraftCopy, embedding: bytes, history: Sequence[PriorCopy],
                   *, embedding_model: str) -> list[Violation]:
        opening = _opening(copy.hook)
        body = _shingles(copy.body)
        structure = _shingles("\n".join((copy.hook, copy.body, copy.cta)))
        vector = np.frombuffer(embedding, dtype="<f4")
        norm = float(np.linalg.norm(vector))
        violations = []
        for index, previous in enumerate(history):
            earlier_opening = _opening(previous.hook)
            hook_score = SequenceMatcher(None, opening, earlier_opening, autojunk=False).ratio()
            if index == 0 and (_same_pattern(opening, earlier_opening) or hook_score >= self.settings.hook_threshold):
                violations.append(Violation(
                    code="repeated_opening", field="hook", message="Choose a different opening from the most recent content.",
                    details={"compared_content_id": previous.content_id, "score": round(hook_score, 4),
                             "previous_hook": previous.hook[:200]},
                ))
            prior_structure = _shingles("\n".join((previous.hook, previous.body, previous.cta)))
            lexical = max(_jaccard(body, _shingles(previous.body)), _jaccard(structure, prior_structure))
            cosine = self._cosine(vector, norm, previous, embedding_model)
            if lexical >= self.settings.lexical_threshold or (
                cosine is not None and cosine >= self.settings.semantic_threshold
            ):
                violations.append(Violation(
                    code="repeated_content", field="body",
                    message="The caption is too similar to recent brand content.",
                    details={"compared_content_id": previous.content_id, "lexical_score": round(lexical, 4),
                             "semantic_score": round(cosine, 4) if cosine is not None else None},
                ))
                break
        return violations

    @staticmethod
    def _cosine(vector: np.ndarray, norm: float, previous: PriorCopy, model: str) -> float | None:
        if previous.embedding_model != model or not previous.embedding or len(previous.embedding) != vector.nbytes:
            return None
        other = np.frombuffer(previous.embedding, dtype="<f4")
        denominator = norm * float(np.linalg.norm(other))
        return float(np.clip(np.dot(vector, other) / denominator, -1, 1)) if denominator else None
