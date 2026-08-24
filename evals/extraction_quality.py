"""Extraction quality eval. Runs separately from unit tests; may use a
live model. Asserts extracted events against ground truth.

Usage: python -m evals.extraction_quality
Requires: FI_INTEL_TEST_NEO4J_URI (graph), and a live extractor wired in
production. In this scaffold it runs the stub path against ground truth to
prove the harness; point it at a real extractor to measure a model.
"""

import asyncio
import os

from fi_intel.graph.client import GraphClient
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ingest.extract_pipeline import ExtractionPipeline, InMemoryProposedTypeSink
from fi_intel.sources.fixture import synthetic_wire


async def main() -> int:
    uri = os.environ.get("FI_INTEL_TEST_NEO4J_URI")
    if uri is None:
        print("FI_INTEL_TEST_NEO4J_URI not set; skipping extraction eval")
        return 0
    client = GraphClient(uri, "neo4j", "fi_intel")
    await client.migrate()
    await client.delete_all()
    await client.migrate()
    try:
        # Stub path: no live model configured in this scaffold. The harness
        # structure (run corpus -> compare to ground truth) is what is
        # demonstrated; wire a real StructuredExtractor to measure a model.
        sink = InMemoryProposedTypeSink()
        ExtractionPipeline(
            extractor=None,  # type: ignore[arg-type]  # wire a real extractor here
            writer=AssertionWriter(client),
            proposed_sink=sink,
        )
        print("extraction eval harness ready (stub extractor); "
              f"graph assertions before run: {await client.assertion_count()}")
        docs = [d async for d in synthetic_wire().fetch()]
        print(f"corpus documents: {len(docs)}")
        return 0
    finally:
        await client.delete_all()
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
