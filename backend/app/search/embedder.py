"""Text -> 384-number vectors ("embeddings"). Similar meaning gives nearby vectors."""

import hashlib
import math
import re
import threading
from functools import lru_cache
from typing import Protocol

from app.config import Settings

DIM = 384  # all-MiniLM-L6-v2 output size; must match the vector(384) columns


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        """One unit-length vector of DIM floats per text."""
        ...


class LocalEmbedder:
    """sentence-transformers on CPU. The model is loaded on first use (not at import) and
    then kept for the life of the process; the files are cached in the /models volume."""

    def __init__(self, model_name: str, max_seq_length: int = 512) -> None:
        self.model_name = model_name
        self._max_seq_length = max_seq_length
        self._model = None
        self._lock = threading.Lock()  # one encode at a time; also guards the first load

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # heavy: import lazily

            # Eager attention, not torch's fused SDPA kernel: on CPU (torch 2.15, aarch64)
            # SDPA with more than one thread never returns for inputs over ~128 tokens,
            # e.g. a crowded invoice page. Eager gives the same vectors (0.25 s for such a
            # page); a hung encode cannot be cancelled and blocked every later job.
            model = SentenceTransformer(
                self.model_name, device="cpu", model_kwargs={"attn_implementation": "eager"}
            )
            # The model stops at 256 word-pieces by default; its BERT backbone accepts 512,
            # so raise the limit or the second half of a 500-token chunk would be ignored.
            model.max_seq_length = self._max_seq_length
            self._model = model
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        with self._lock:
            vectors = self._load().encode(
                texts,
                batch_size=32,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        return vectors.tolist()


class FakeEmbedder:
    """For tests: deterministic bag-of-words vectors (each word hashed into one of 384
    slots). Texts sharing words are close; no model download needed."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("embedding model unavailable")
        return [self._vector(text) for text in texts]

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = [0.0] * DIM
        for word in re.findall(r"\w+", text.lower()):
            slot = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % DIM
            vector[slot] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:  # empty text: any fixed unit vector (a zero vector has no direction)
            vector[0], norm = 1.0, 1.0
        return [v / norm for v in vector]


@lru_cache
def _local(model_name: str) -> LocalEmbedder:
    return LocalEmbedder(model_name)  # one per process


def get_embedder(settings: Settings) -> Embedder | None:
    """The configured embedder, or None when EMBEDDINGS_ENABLED is false."""
    return _local(settings.embedding_model) if settings.embeddings_enabled else None
