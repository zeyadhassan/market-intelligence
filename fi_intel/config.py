"""Runtime configuration.

All configuration flows through Pydantic settings objects. Module-level
globals are forbidden by project coding standards because they make
configuration invisible to tests and impossible to scope per-run.
"""

from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-wide settings, overridable via FI_INTEL_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="FI_INTEL_", frozen=True)

    # Defaults are local-dev only, matching deploy/docker-compose.yml.
    # Real credentials arrive via FI_INTEL_* environment variables.
    postgres_dsn: str = "postgresql://fi_intel:fi_intel@localhost:5432/fi_intel"  # noqa: S105
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "fi_intel"  # noqa: S105

    # Authenticated identity injected by the deployment boundary. Commands
    # never accept entitlement group or barrier side as caller-provided flags.
    access_principal_id: str = "cli.user"
    access_entitlement_group: str = "fi_gcc_public"
    access_side: Literal["public", "private"] = "public"

    # Open-web RSS/Atom sources (fi_intel/sources/adapters/rss.py). SEC.gov
    # rejects unidentified traffic with 403; the User-Agent must name an
    # organization and contact per SEC's fair-access policy. The default is
    # a deliberately unusable placeholder, not a real contact, so demo runs
    # fail loudly instead of impersonating whoever happens to run this repo.
    rss_user_agent: str = "market-intelligence-demo set-FI_INTEL_RSS_USER_AGENT@example.invalid"
    sec_edgar_feed_url: str = (
        "https://www.sec.gov/cgi-bin/browse-edgar"
        "?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=100&output=atom"
    )
    fed_press_feed_url: str = "https://www.federalreserve.gov/feeds/press_all.xml"
    # These feeds are not GCC FIG coverage. They remain registered for
    # controlled research/testing but cannot enter production ingestion unless
    # an operator explicitly enables them and grants the appropriate audience.
    enable_sec_edgar_source: bool = False
    enable_fed_press_source: bool = False

    # Registered-source acquisition limits. Network adapters apply these to
    # every initial request and redirect; callers cannot override origins or
    # byte limits at runtime.
    source_http_timeout_seconds: float = 15.0
    source_http_max_attempts: int = 3
    source_http_max_redirects: int = 3
    source_max_feed_bytes: int = 2 * 1024 * 1024
    source_max_detail_bytes: int = 16 * 1024 * 1024
    source_cursor_history_limit: int = 1_000
    source_raw_retention_days: int = 2_555

    # Detector coverage is explicit and fail-closed. These are comma-separated
    # stable IDs so deployment config can name the authorized source universe
    # and the desk's covered legal entities without asking an extractor.
    coverage_required_source_ids: str = ""
    covered_entity_leis: str = ""
    triage_priority_threshold: int = 60
    historical_precision_min_feedback: int = 30

    # GLEIF publishes both a JSON:API surface and Golden Copy bulk files.
    # The paginated API is the default operational path; bulk file URLs are
    # accepted only when returned from the registered GLEIF origins.
    gleif_api_url: str = "https://api.gleif.org/api/v1/lei-records?page[size]=100"
    gleif_page_size: int = 100
    gleif_max_pages: int = 100

    # On-prem LLM (fi_intel/ingest/extractors/, fi_intel/agents/reasoning/).
    # Speaks the OpenAI-compatible chat completions API — the shape nearly
    # every self-hosted serving stack (vLLM, TGI, Ollama, LocalAI, ...)
    # exposes, so which stack is actually running behind llm_base_url is a
    # deployment choice, not a code choice. No cloud LLM provider is called
    # anywhere in this codebase. No default base_url: unset means the
    # corresponding build_*() factory raises a clear configuration error
    # rather than silently falling back to a stub. No default
    # api_key either, but a harmless placeholder rather than None — most
    # on-prem servers don't check it, and requiring one anyway would block
    # construction over an auth scheme nobody asked for.
    llm_base_url: str | None = None
    llm_api_key: str = "not-needed"  # noqa: S105
    extraction_model: str = "gpt-oss-120b"
    research_model: str = "gpt-oss-120b"
    # Chat Completions' reasoning_effort, sent only when set (server support
    # for this varies by stack; omitting it rather than guessing a default
    # avoids a request some servers may reject). gpt-oss itself supports
    # low/medium/high; left as a plain string, not a Literal, since the
    # accepted set is a property of the server, not this codebase.
    extraction_reasoning_effort: str | None = None
    research_reasoning_effort: str | None = None
    # temperature is part of the original OpenAI completions shape and
    # universally supported, unlike reasoning_effort, so these are always
    # sent. Extraction wants maximally consistent structured output;
    # research is a judgement call and gets a little room.
    extraction_temperature: float = 0.0
    research_temperature: float = 0.2
    # LLM self-reported confidence is not calibrated on a governed labelled
    # set. The admission gate is therefore disabled by default. A non-zero
    # deployment value must cite evals/confidence_calibration.py output.
    min_extraction_confidence: float = 0.0

    # Embedding provider (fi_intel/retrieval/embedders/). Model/server not
    # yet decided — falls back to the deterministic HashingEmbedder until
    # embedding_base_url is set (see build_embedder). Also targets the
    # OpenAI-compatible /v1/embeddings shape most local embedding servers
    # (TEI, vLLM, LocalAI, Ollama's OpenAI-compat mode, ...) expose, so
    # picking a specific local model later is a config change.
    embedding_base_url: str | None = None
    embedding_api_key: str = "not-needed"  # noqa: S105
    embedding_model: str | None = None
    # Must match document_chunk.embedding's vector(N) column — update both
    # together (and run `fi-intel index reembed`) once a model is chosen,
    # since local embedding models vary widely in output dimension
    # (384/768/1024/1536/4096 are all common). 1024 matches today's schema.
    embedding_dim: int = 1024
    # Some embedding model families (e5, bge, gte, ...) are trained
    # asymmetrically and expect a literal prefix on the input text to tell
    # a search query from a passage/document apart (e.g. "query: " /
    # "passage: "). Left blank by default (symmetric treatment) since the
    # model isn't chosen yet; set both once it is, only if it needs them.
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""
