"""Fail-closed contracts for the unified live GCC Stage 1 path."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from fi_intel.api.stage_one_page import STAGE_ONE_HTML, STAGE_ONE_JS
from fi_intel.application.preflight import canonical_configuration_errors
from fi_intel.config import Settings
from fi_intel.ledger.models import AccessPolicy
from fi_intel.runtime import ExecutionPath
from fi_intel.sources.adapters.gcc_official import (
    GCC_OFFICIAL_SOURCES,
    GccOfficialCanonicalizer,
    GccOfficialSource,
    OfficialGccRawAdapter,
)
from fi_intel.sources.canonical import BarrierSide
from fi_intel.sources.transport import SourceTransportError
from tests.source_support import ScriptedSourceTransport, source_response

NOW = datetime(2026, 8, 27, 10, tzinfo=UTC)


def test_live_matrix_has_two_official_pages_in_each_gcc_country() -> None:
    by_country: dict[str, int] = {}
    for source in GCC_OFFICIAL_SOURCES:
        by_country[source.country] = by_country.get(source.country, 0) + 1

    assert set(by_country) == {
        "Saudi Arabia",
        "United Arab Emirates",
        "Qatar",
        "Kuwait",
        "Bahrain",
        "Oman",
    }
    assert set(by_country.values()) == {2}
    assert all(source.url.startswith("https://") for source in GCC_OFFICIAL_SOURCES)


def test_default_stage_one_page_describes_the_local_product_path() -> None:
    assert "Local GCC intelligence" in STAGE_ONE_HTML
    assert "Official sources + configured models" in STAGE_ONE_HTML
    assert "Live GCC POC" not in STAGE_ONE_HTML
    assert "deterministic synthetic fixture" not in STAGE_ONE_HTML
    assert "stage-one-demo" not in STAGE_ONE_JS
    assert 'return "fi-intel-local"' in STAGE_ONE_JS
    assert "not invoked (coverage incomplete)" in STAGE_ONE_JS
    assert "selectTopic(topicId, force)" in STAGE_ONE_JS
    assert 'model_name || "unavailable"' not in STAGE_ONE_JS
    assert "window.sessionStorage" not in STAGE_ONE_JS
    assert "OIDC access token" not in STAGE_ONE_JS
    assert "console.table" in STAGE_ONE_JS
    assert "[FI Intel] source" in STAGE_ONE_JS
    assert "backend logs: .venv" in STAGE_ONE_JS
    assert 'detail.className = "source-detail"' in STAGE_ONE_JS


def test_live_preflight_requires_only_model_endpoints() -> None:
    errors = canonical_configuration_errors(
        Settings(
            llm_base_url=None,
            embedding_base_url=None,
            embedding_model=None,
            rss_user_agent="market-intelligence-demo contact@example.invalid",
        )
    )

    assert "FI_INTEL_LLM_BASE_URL is required" in errors
    assert "FI_INTEL_EMBEDDING_BASE_URL is required" in errors
    assert "FI_INTEL_EMBEDDING_MODEL is required" in errors
    assert not any("FI_INTEL_RSS_USER_AGENT" in error for error in errors)


def test_no_direct_live_analysis_runtime_or_implementation_remains() -> None:
    assert set(ExecutionPath) == {
        ExecutionPath.FIXTURE_REGRESSION,
        ExecutionPath.UNIFIED_PIPELINE,
    }
    assert not (Path("fi_intel/demo/gcc_live.py")).exists()


async def test_official_source_adapter_archives_bounded_detail_pages_and_resumes() -> None:
    landing_url = "https://regulator.example/news"
    detail_url = "https://regulator.example/news/detail?id=42"
    source = GccOfficialSource(
        source_id="example_official",
        country="Example",
        display_name="Example regulator",
        source_type="regulator",
        url=landing_url,
        allowed_origins=("https://regulator.example",),
    )
    landing = (
        "<html><head><title>News</title></head><body>"
        f"<a href='{detail_url}'>Material announcement</a>"
        + ("Official market updates. " * 20)
        + "</body></html>"
    ).encode()
    detail = (
        "<html><head><title>Bank programme approved</title>"
        "<meta property='article:published_time' content='2026-08-27T08:00:00Z'>"
        "</head><body>"
        + ("Example Bank approved a new issuance programme. " * 10)
        + "</body></html>"
    ).encode()
    transport = ScriptedSourceTransport(
        [
            (
                landing_url,
                source_response(
                    200,
                    landing,
                    headers=(("content-type", "text/html"), ("etag", '"landing"')),
                ),
            ),
            (
                detail_url,
                source_response(
                    200,
                    detail,
                    headers=(("content-type", "text/html"), ("etag", '"detail"')),
                ),
            ),
            (landing_url, source_response(304)),
            (detail_url, source_response(304)),
        ]
    )
    policy = AccessPolicy(
        policy_id=uuid4(),
        barrier_side=BarrierSide.PUBLIC,
        allowed_entitlement_groups=frozenset({"fi_gcc_public"}),
        created_at=NOW - timedelta(days=1),
    )
    settings = Settings(
        rss_user_agent="fi-intel-test test@example.invalid",
        source_http_max_attempts=1,
        gcc_source_max_detail_pages=5,
    )
    adapter = OfficialGccRawAdapter(
        source,
        settings,
        policy,
        transport=transport,
        clock=lambda: NOW,
    )

    first = await adapter.poll()
    second = await adapter.poll(first.next_cursor)
    detail_document = await GccOfficialCanonicalizer(source, settings).canonicalize(
        first.items[1].envelope
    )

    assert first.discovered_count == 2
    assert [item.envelope.external_id for item in first.items] == [
        "landing-page",
        first.items[1].envelope.external_id,
    ]
    assert detail_document.url == detail_url
    assert detail_document.published_at == datetime(2026, 8, 27, 8, tzinfo=UTC)
    assert second.items == ()
    assert second.unchanged_count == 2
    assert all("If-None-Match" in request[1] for request in transport.requests[-2:])
    transport.assert_exhausted()


async def test_official_source_adapter_reports_failed_details_as_incomplete() -> None:
    landing_url = "https://regulator.example/news"
    detail_url = "https://regulator.example/news/detail?id=42"
    source = GccOfficialSource(
        source_id="example_official",
        country="Example",
        display_name="Example regulator",
        source_type="regulator",
        url=landing_url,
        allowed_origins=("https://regulator.example",),
    )
    landing = (
        "<html><head><title>News</title></head><body>"
        f"<a href='{detail_url}'>Material announcement</a>"
        + ("Official market updates. " * 20)
        + "</body></html>"
    ).encode()
    transport = ScriptedSourceTransport(
        [
            (
                landing_url,
                source_response(200, landing, headers=(("content-type", "text/html"),)),
            ),
            (detail_url, SourceTransportError("detail unavailable")),
        ]
    )
    policy = AccessPolicy(
        policy_id=uuid4(),
        barrier_side=BarrierSide.PUBLIC,
        allowed_entitlement_groups=frozenset({"fi_gcc_public"}),
        created_at=NOW - timedelta(days=1),
    )
    adapter = OfficialGccRawAdapter(
        source,
        Settings(source_http_max_attempts=1),
        policy,
        transport=transport,
        clock=lambda: NOW,
    )

    poll = await adapter.poll()

    assert poll.discovered_count == 2
    assert len(poll.items) == 1
    assert poll.failed_count == 1
    transport.assert_exhausted()
