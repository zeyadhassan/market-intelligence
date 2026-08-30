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

    # This is an explicit operating claim, not a cosmetic environment label.
    # Production-capable entry points validate it against the actual data,
    # model, coverage, and execution-path capabilities before doing work.
    analysis_mode: Literal["fixture", "shadow", "pilot", "production"] = "shadow"

    # Defaults are local-dev only, matching deploy/compose.yml (Podman Compose).
    # Real credentials arrive via FI_INTEL_* environment variables.
    postgres_dsn: str = "postgresql://fi_intel:fi_intel@localhost:5432/fi_intel"  # noqa: S105
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "fi_intel"  # noqa: S105
    postgres_pool_min_size: int = 2
    postgres_pool_max_size: int = 16
    postgres_command_timeout_seconds: float = 60.0

    # Authenticated identity injected by the deployment boundary. Commands
    # never accept entitlement group or barrier side as caller-provided flags.
    access_subject: str = "cli.user"
    access_principal_id: str = "cli.user"
    access_entitlement_group: str = "fi_gcc_public"
    access_side: Literal["public", "private"] = "public"
    access_desks: str = "fi_gcc"
    access_roles: str = "analyst"
    access_purposes: str = "market_intelligence"

    # The canonical browser/API path uses the same OIDC and PostgreSQL
    # configuration namespace as every other process. No second API-specific
    # settings path or fixed development bearer token is supported.
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_jwks_url: str | None = None

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
    raw_archive_path: str = ".fi-intel/archive"

    # Canonical GCC source acquisition. The checked-in source matrix is intentionally
    # bounded to official, public regulator/market pages in all six GCC
    # countries. `complete` means every required configured page was fetched
    # and analysed in the current run; it is not a claim that every
    # issuer IR site, rating action, or licensed news wire is covered.
    gcc_source_char_limit: int = 60_000
    gcc_source_max_parallel_sources: int = 4
    gcc_source_max_detail_pages: int = 25

    # Hard limits for one canonical daily run. They are enforced by the
    # shared coordinator used by both the API and the Stage 1 page.
    daily_max_model_calls: int = 250
    daily_max_model_tokens: int = 1_000_000
    daily_max_model_latency_seconds: float = 1_800.0
    daily_analysis_timezone: str = "Europe/Berlin"
    daily_analysis_cutoff_hour: int = 6
    daily_analysis_window_version: str = "daily-window-v1"
    daily_signal_concurrency: int = 4
    daily_signal_timeout_seconds: float = 180.0
    daily_signal_max_attempts: int = 2
    outbox_dispatch_batch_size: int = 500
    outbox_dispatch_max_batches: int = 100
    outbox_handler_timeout_seconds: float = 180.0
    outbox_handler_max_attempts: int = 3
    outbox_lease_seconds: float = 300.0
    worker_poll_interval_seconds: float = 15.0
    worker_lease_seconds: float = 300.0
    worker_max_attempts: int = 5
    telemetry_trace_endpoint: str | None = None
    telemetry_metric_endpoint: str | None = None

    # Development email is disabled by default. Enabling it requires a
    # sandbox SMTP endpoint, an explicit recipient allowlist, and a Fernet
    # key used to protect destination addresses at rest.
    email_enabled: bool = False
    email_smtp_host: str = "localhost"
    email_smtp_port: int = 1025
    email_smtp_starttls: bool = False
    email_sender: str = "fi-intel@localhost.invalid"
    email_recipient_allowlist: str = ""
    email_destination_key: str | None = None
    email_template_version: str = "daily-digest-v1"
    email_max_attempts: int = 3

    # Detector coverage is explicit and fail-closed. These are comma-separated
    # stable IDs so deployment config can name the authorized source universe
    # and the desk's covered legal entities without asking an extractor.
    coverage_required_source_ids: str = ""
    covered_entity_leis: str = ""
    # Measured on the 120-scenario calibration grid (2026-08-26): 60 admits
    # 72%, versus 43% at 65 and 92% at 55.
    # Every brief renders its realized score range against this threshold so a
    # deployment-specific retune is observable rather than a silent cliff.
    triage_priority_threshold: int = 60
    historical_precision_full_weight_samples: int = 30

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
    entailment_model: str = "gpt-oss-120b"
    reranker_model: str = "gpt-oss-120b"
    # A single operator-owned deploy/app.env also identifies the evaluated
    # artifacts that may serve those model roles.  The release synchronizer
    # derives prompt/schema identities from code, while operators supply the
    # immutable deployment and evaluation identities below.
    model_quality_gate_passed: bool = False
    model_evaluation_dataset_digest: str = ""
    model_evaluation_report_digest: str = ""
    model_evaluated_at: str = ""
    model_registered_at: str = ""
    model_release_created_by: str = ""
    extraction_release_id: str = ""
    extraction_artifact_digest: str = ""
    reasoning_release_id: str = ""
    reasoning_artifact_digest: str = ""
    embedding_release_id: str = ""
    embedding_artifact_digest: str = ""
    reranker_release_id: str = ""
    reranker_artifact_digest: str = ""
    entailment_release_id: str = ""
    entailment_artifact_digest: str = ""
    # Chat Completions' reasoning_effort, sent only when set (server support
    # for this varies by stack; omitting it rather than guessing a default
    # avoids a request some servers may reject). gpt-oss itself supports
    # low/medium/high; left as a plain string, not a Literal, since the
    # accepted set is a property of the server, not this codebase.
    extraction_reasoning_effort: str | None = None
    research_reasoning_effort: str | None = None
    entailment_reasoning_effort: str | None = None
    reranker_reasoning_effort: str | None = None
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

    # Embedding provider (fi_intel/retrieval/embedders/). Fixture-only
    # builders may use the deterministic HashingEmbedder. The canonical
    # service fails closed unless a registry-routed endpoint and artifact
    # are configured. This also targets the
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
