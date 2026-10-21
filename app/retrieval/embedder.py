import math
import re
import zlib
from collections.abc import Sequence
from typing import Any, Protocol

BGE_SMALL_MODEL = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    # Config text is embedded as documents. A question is embedded as a query, which some models treat differently.
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class BgeSmallEmbedder:
    # The local model chosen for retrieval (SPEC Q10). It runs on the laptop, so questions never leave the machine.
    # The model is loaded on first use, so importing the app does not download anything.
    def __init__(self, model_name: str = BGE_SMALL_MODEL) -> None:
        self._model_name = model_name
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [[float(x) for x in vector] for vector in self._load().embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        return [float(x) for x in next(iter(self._load().query_embed([text])))]


class FakeEmbedder:
    # Bag of words hashed into fixed buckets. Deterministic and needs no model, so tests can rely on it.
    # Wide enough that unrelated words rarely share a bucket, which would give a false similarity.
    def __init__(self, dims: int = 4096) -> None:
        self._dims = dims

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

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
