"""GLEIF golden-copy adapter.

GLEIF bulk data is reference data, but it enters the platform through the
same adapter boundary as news (invariant 2): nothing fetches from the open
internet outside a registered adapter. This implementation is
fixture-backed; swapping in the real bulk file is a config change (point
``fixture_name`` at a downloaded golden copy), not a refactor.

Each golden-copy record becomes a REFERENCE-class CanonicalDocument whose
metadata carries the vendor-neutral reference fields (legal_name,
jurisdiction, sector, parent_lei) that resolution blocks on.
"""

from fi_intel.sources.fixture import FixtureAdapter


def gleif_fixture() -> FixtureAdapter:
    """The GLEIF golden-copy fixture used across the test suite."""
    return FixtureAdapter(source_id="gleif_fixture", fixture_name="gleif_golden_copy.json")
