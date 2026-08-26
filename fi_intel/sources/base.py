"""The source adapter protocol.

Every licensed source — wires, filings, reference data like GLEIF — is a
registered adapter. Reference data goes through the same boundary as news:
nothing reaches the open internet outside an adapter, and adapters are the
only place vendor semantics may live.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from fi_intel.sources.canonical import CanonicalDocument

# Note: implementations declare fetch() as "async def ... -> Any" because it
# is an async generator. mypy types such a function as returning a coroutine;
# the Any return annotation is the pragmatic escape hatch, and the contract
# test (tests/test_adapter_contract.py) verifies the real runtime behaviour.


class FetchCursor(BaseModel):
    """Opaque resumption token for a source.

    Opaque to the pipeline, meaningful to the adapter. Persisted after every
    successfully committed batch so a killed run resumes without gap or
    duplicate.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str
    position: str
    updated_at: datetime


@runtime_checkable
class SourceAdapter(Protocol):
    """Contract every source adapter must satisfy.

    Enforced by tests/test_adapter_contract.py, which every new adapter
    must pass unmodified.
    """

    @property
    def source_id(self) -> str:
        """Stable identifier joining documents to the source registry."""
        ...

    async def fetch(
        self, cursor: FetchCursor | None = None
    ) -> AsyncIterator[CanonicalDocument]:
        """Yield canonical documents after the cursor position.

        Must be resumable: passing back the cursor emitted alongside the
        last yielded document continues without re-yielding it. Must raise
        — never skip — on a record that cannot be mapped cleanly.
        """
        ...

    def cursor_for(self, doc: CanonicalDocument) -> FetchCursor:
        """Return the cursor that resumes immediately after ``doc``."""
        ...
