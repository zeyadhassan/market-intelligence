"""Document chunking and deterministic local embeddings.

The default embedder is a hashing trick over token unigrams+bigrams: no
network, no model download, fully reproducible — which is what tests and
backtests need. A licensed embedding service slots in behind the Embedder
protocol as a config change; nothing downstream changes.

Chunk offsets are character offsets into the source document body, because
evidence citations (invariant 7) resolve to document + character span.
"""

import hashlib
import math
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from fi_intel.sources.canonical import CanonicalDocument

EMBEDDING_DIM = 1024  # matches deploy/init.sql document_chunk.embedding
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    doc_id: str
    chunk_index: int
    char_start: int
    char_end: int
    text: str


def chunk_document(
    doc: CanonicalDocument, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[Chunk]:
    """Split title+body into overlapping character windows on word boundaries."""
    text = doc.title + "\n" + doc.body
    chunks: list[Chunk] = []
    start = 0
    index = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            # Back off to a word boundary so chunks don't split tokens.
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary
        chunks.append(
            Chunk(
                source_id=doc.source_id,
                doc_id=doc.doc_id,
                chunk_index=index,
                char_start=start,
                char_end=end,
                text=text[start:end],
            )
        )
        index += 1
        start = end if end - overlap <= start else end - overlap
        if end == len(text):
            break
    return chunks


@runtime_checkable
class Embedder(Protocol):
    @property
    def dim(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic local embedder. Semantic signal comes from token
    n-gram overlap in a hashed space — enough for hybrid retrieval to be
    meaningfully different from pure BM25 on morphological variants."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        tokens = text.lower().split()
        features = tokens + [f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)]
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "little") % self._dim
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec))
        if norm > 0:
            vec = [v / norm for v in vec]
        return vec


def cosine(a: list[float], b: list[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b, strict=True)))
