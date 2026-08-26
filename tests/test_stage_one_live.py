"""Fail-closed contracts for the live GCC Stage 1 path."""

from datetime import UTC, datetime

import pytest

from fi_intel.api.stage_one_page import STAGE_ONE_HTML
from fi_intel.config import Settings
from fi_intel.demo.gcc_live import (
    GCC_LIVE_SOURCES,
    GccLiveSource,
    LiveGccAnalysisRunner,
    LiveOpportunityCandidate,
    LiveSourceDocument,
    live_demo_configuration_errors,
)

_AS_OF = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
_SOURCE = GccLiveSource(
    source_id="test_official",
    country="Qatar",
    display_name="Test official source",
    source_type="central_bank",
    url="https://official.example/news",
    allowed_origins=("https://official.example",),
)


class _Reader:
    async def read(self, source: GccLiveSource) -> LiveSourceDocument:
        text = (
            "August 20, 2026\n"
            "Gulf Example Bank approved a USD 500 million sukuk programme for future issuance."
        )
        return LiveSourceDocument(
            source=source,
            title="Official announcements",
            text=text,
            fetched_at=_AS_OF,
            content_hash="a" * 64,
        )


class _Model:
    model_name = "real-model-under-test"

    async def analyse(
        self,
        document: LiveSourceDocument,
        *,
        as_of: datetime,
        lookback_days: int,
        run_id: str,
    ) -> tuple[LiveOpportunityCandidate, ...]:
        del document, as_of, lookback_days, run_id
        supported = LiveOpportunityCandidate(
            topic_id="issuance-programmes",
            title="New sukuk programme may create an issuance window",
            entity_name="Gulf Example Bank",
            summary="The bank explicitly approved a new sukuk programme for future issuance.",
            freshness_reason=(
                "The official page dates the approval inside the live lookback window."
            ),
            published_at=datetime(2026, 8, 20, 0, 0, tzinfo=UTC),
            date_quote="August 20, 2026",
            evidence_quote=(
                "Gulf Example Bank approved a USD 500 million sukuk programme for future issuance."
            ),
            falsifier="A later official notice cancels or fully exhausts the programme.",
            relevance_score=0.82,
        )
        unsupported = supported.model_copy(
            update={
                "entity_name": "Invented Bank",
                "evidence_quote": (
                    "Invented Bank approved a programme that is absent from the page."
                ),
            }
        )
        wrong_date = supported.model_copy(
            update={"published_at": datetime(2026, 8, 21, 0, 0, tzinfo=UTC)}
        )
        return supported, unsupported, wrong_date

    async def close(self) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        llm_base_url="http://model.example/v1",
        research_model="real-model-under-test",
        rss_user_agent="Example FI Watch ops@example.com",
        gcc_live_lookback_days=45,
        gcc_live_max_parallel_sources=1,
    )


def test_live_matrix_has_two_official_pages_in_each_gcc_country() -> None:
    by_country: dict[str, int] = {}
    for source in GCC_LIVE_SOURCES:
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
    assert all(source.url.startswith("https://") for source in GCC_LIVE_SOURCES)


def test_default_stage_one_page_is_live_and_never_labels_itself_synthetic() -> None:
    assert "Live GCC POC" in STAGE_ONE_HTML
    assert "Official sources + configured LLM" in STAGE_ONE_HTML
    assert "deterministic synthetic fixture" not in STAGE_ONE_HTML


def test_live_demo_preflight_refuses_unconfigured_model_and_placeholder_identity() -> None:
    errors = live_demo_configuration_errors(
        Settings(
            llm_base_url=None,
            rss_user_agent="market-intelligence-demo contact@example.invalid",
        )
    )

    assert "FI_INTEL_LLM_BASE_URL is not set" in errors
    assert any("FI_INTEL_RSS_USER_AGENT" in error for error in errors)


@pytest.mark.asyncio
async def test_live_runner_keeps_supported_quote_and_rejects_hallucinated_candidate() -> None:
    runner = LiveGccAnalysisRunner(
        _settings(),
        _Reader(),
        _Model(),
        sources=(_SOURCE,),
        clock=lambda: _AS_OF,
    )

    run = await runner.run()

    assert run.coverage_complete is True
    assert run.rejected_candidate_count == 2
    assert run.source_statuses[0].candidate_count == 1
    results = run.results_by_topic["issuance-programmes"]
    assert len(results) == 1
    assert results[0].entity_name == "Gulf Example Bank"
    assert results[0].evidence[0].quote.startswith("Gulf Example Bank approved")
    assert results[0].evidence[0].content_hash == "a" * 64
    assert results[0].coverage_state == "complete"
