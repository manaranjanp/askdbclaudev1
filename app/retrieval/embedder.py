import math
import re
import zlib
from collections.abc import Sequence
from typing import Protocol

# The real local model (SPEC Q10) is not chosen yet. Retrieval works with any Embedder, and the tests use FakeEmbedder.


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FakeEmbedder:
    # Bag of words hashed into fixed buckets. Deterministic and needs no model, so tests can rely on it.
    # Wide enough that unrelated words rarely share a bucket, which would give a false similarity.
    def __init__(self, dims: int = 4096) -> None:
        self._dims = dims

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dims
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            # crc32, not hash(): Python's hash() changes between runs.
            vector[zlib.crc32(token.encode()) % self._dims] += 1.0
        return vector


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
