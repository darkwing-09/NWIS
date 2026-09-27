import hashlib
import math
from typing import List, Protocol, runtime_checkable

from config.errors import EmbeddingProviderError


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for generating text embeddings (e.g. text-embedding-004 / 1536-dim)."""

    def embed(self, texts: List[str]) -> List[List[float]]:
        ...

    def embed_one(self, text: str) -> List[float]:
        ...


class MockEmbeddingProvider:
    """
    Deterministic mock embedding provider generating normalized 1536-dimensional vectors.
    Uses sha256 hash seeds to ensure identical text yields identical vectors.
    """

    def __init__(self, dimension: int = 1536, should_raise: bool = False) -> None:
        self.dimension = dimension
        self.should_raise = should_raise

    def embed_one(self, text: str) -> List[float]:
        if self.should_raise:
            raise EmbeddingProviderError("Mock embedding provider error")

        # Deterministic generation from text hash
        raw_hash = hashlib.sha256(text.encode("utf-8")).digest()
        vec = []
        for i in range(self.dimension):
            byte_val = raw_hash[i % len(raw_hash)]
            vec.append(float((byte_val + i) % 100) / 100.0)

        # L2 normalize
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [round(x / norm, 6) for x in vec]

    def embed(self, texts: List[str]) -> List[List[float]]:
        if self.should_raise:
            raise EmbeddingProviderError("Mock embedding provider error")
        return [self.embed_one(t) for t in texts]


_default_embedding_provider = MockEmbeddingProvider()


def get_embedding_provider() -> EmbeddingProvider:
    return _default_embedding_provider
