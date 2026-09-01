"""Document chunking and embedding interfaces.

Chunk offsets use the source text coordinate space so evidence citations
resolve to document character spans. HashingEmbedder provides a deterministic
fallback for tests and unconfigured environments.
"""

import hashlib
import math
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from fi_intel.sources.canonical import CanonicalDocument, document_text

EMBEDDING_DIM = 2048  # migration 0024 / nvidia/llama-3.2-nv-embedqa-1b-v2
CHUNK_SIZE = 512
CHUNK_OVERLAP = 64
CHUNKER_VERSION = f"structure-aware-v2:{CHUNK_SIZE}:{CHUNK_OVERLAP}"


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    doc_id: str
    chunk_index: int
    char_start: int
    char_end: int
    text: str
    section_type: str = "text"
    structure_path: tuple[str, ...] = ()
    chunk_id: str | None = None
    document_version_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    assertion_ids: tuple[str, ...] = ()
    evidence_span_ids: tuple[str, ...] = ()
    policy_id: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    content_hash: str | None = None
    chunker_version: str | None = None
    embedding_release: str | None = None


def chunk_document(
    doc: CanonicalDocument, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[Chunk]:
    """Split on document structure first, with bounded windows for long blocks.

    Coordinates always refer to the untouched canonical title+body text.  Blank
    lines, table-like rows, headings, and sentence ends are preferred over an
    arbitrary character boundary.
    """
    text = document_text(doc)
    chunks: list[Chunk] = []
    start = 0
    index = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            candidates = [
                text.rfind("\n\n", start, end),
                text.rfind("\n", start, end),
                text.rfind(". ", start, end),
                text.rfind(" ", start, end),
            ]
            boundary = next(
                (candidate for candidate in candidates if candidate >= start + size // 3),
                -1,
            )
            if boundary > start:
                end = boundary + (1 if text[boundary] == "." else 0)
        excerpt = text[start:end]
        section_type = (
            "table"
            if _looks_tabular(excerpt)
            else ("heading" if "\n" not in excerpt and len(excerpt) <= 120 else "text")
        )
        chunks.append(
            Chunk(
                source_id=doc.source_id,
                doc_id=doc.doc_id,
                chunk_index=index,
                char_start=start,
                char_end=end,
                text=excerpt,
                section_type=section_type,
                structure_path=(doc.document_class.value,),
            )
        )
        index += 1
        start = end if end - overlap <= start else end - overlap
        if end == len(text):
            break
    return chunks


def _looks_tabular(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    return bool(lines) and sum("|" in line or "\t" in line for line in lines) >= max(
        1, len(lines) // 2
    )


@runtime_checkable
class Embedder(Protocol):
    @property
    def dim(self) -> int: ...

    @property
    def model_version(self) -> str:
        """Identify the model version that produced these vectors."""
        ...

    async def embed_batch(
        self, texts: list[str], *, kind: Literal["document", "query"] = "document"
    ) -> list[list[float]]:
        """Embed a batch of documents or queries."""
        ...


class HashingEmbedder:
    """Deterministic local embedder based on hashed token n-grams."""

    def __init__(self, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        return "hashing-v1"

    def _embed_one(self, text: str) -> list[float]:
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

    async def embed_batch(
        self, texts: list[str], *, kind: Literal["document", "query"] = "document"
    ) -> list[list[float]]:
        del kind  # symmetric by construction; no query/document distinction to make
        return [self._embed_one(t) for t in texts]


def cosine(a: list[float], b: list[float]) -> float:
    return float(sum(x * y for x, y in zip(a, b, strict=True)))
