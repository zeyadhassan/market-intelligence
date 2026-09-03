"""FastAPI factories for authenticated analyst workflows."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import monotonic
from typing import Annotated, Protocol
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.responses import Response

from fi_intel.api.auth import (
    AuthenticationError,
    Authenticator,
    AuthorizationError,
    LocalIdentityDirectory,
    LocalTokenVerifier,
    RequestPrincipal,
)
from fi_intel.api.models import (
    BriefPublicationRequest,
    BriefRequest,
    BriefView,
    EntityView,
    EvidenceSpanView,
    FeedbackReceipt,
    FeedbackRequest,
    ResultEvaluationReceipt,
    ResultEvaluationRequest,
    ReviewDecisionRequest,
    ReviewReceipt,
    RunView,
    SearchCreateRequest,
    SearchView,
    SessionView,
    SignalCloseReceipt,
    SignalCloseRequest,
    SignalView,
    TopicResultsView,
    TopicSubscriptionUpdate,
    TopicSubscriptionView,
    TopicTagView,
)
from fi_intel.api.service import (
    AnalystService,
    PublicationNotReadyError,
    ResourceNotFoundError,
    StageOneService,
)
from fi_intel.api.stage_one_page import STAGE_ONE_CSS, STAGE_ONE_HTML, STAGE_ONE_JS
from fi_intel.api.workbench import WORKBENCH_CSS, WORKBENCH_HTML, WORKBENCH_JS
from fi_intel.application.operations import RuntimeDashboardView
from fi_intel.logging import get_logger
from fi_intel.telemetry import Telemetry

_LOG = get_logger(component="api.http")


class _AsyncCloseable(Protocol):
    async def close(self) -> None: ...


def create_app(  # noqa: C901 - route registration is intentionally centralized
    authenticator: Authenticator,
    service: AnalystService,
    telemetry: Telemetry | None = None,
    *,
    stage_one_service: StageOneService | None = None,
    stage_one_html: str = STAGE_ONE_HTML,
    stage_one_javascript: str = STAGE_ONE_JS,
    canonical_stage_one_only: bool = False,
    owns_telemetry: bool = False,
    owned_resources: tuple[_AsyncCloseable, ...] = (),
) -> FastAPI:
    """Build the API with explicit authentication and application ports."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        try:
            yield
        finally:
            errors: list[Exception] = []
            for resource in reversed(owned_resources):
                try:
                    await resource.close()
                except Exception as exc:  # pragma: no cover - shutdown best effort
                    errors.append(exc)
            if telemetry is not None and owns_telemetry:
                try:
                    telemetry.shutdown()
                except Exception as exc:  # pragma: no cover - exporter-specific
                    errors.append(exc)
            if errors:
                raise ExceptionGroup("analyst API shutdown failed", errors)

    app = FastAPI(
        title="FI Intelligence Analyst API",
        version="2.0.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    bearer = HTTPBearer(auto_error=False)

    @app.exception_handler(PublicationNotReadyError)
    async def publication_not_ready(
        request: Request, exc: PublicationNotReadyError
    ) -> JSONResponse:
        del request
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})

    async def current_principal(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
    ) -> RequestPrincipal:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="bearer authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            principal = await authenticator.authenticate(credentials.credentials)
            principal.require_purpose("market_intelligence")
            return principal
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    principal_dependency = Annotated[RequestPrincipal, Depends(current_principal)]

    @app.middleware("http")
    async def request_id_header(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = monotonic()
        request_id = request.headers.get("x-request-id") or uuid4().hex
        response_status = 500
        try:
            response = await call_next(request)
            response_status = response.status_code
            response.headers["x-request-id"] = request_id
            response.headers["cache-control"] = "no-store"
            response.headers["x-content-type-options"] = "nosniff"
            response.headers["x-frame-options"] = "DENY"
            response.headers["referrer-policy"] = "no-referrer"
            response.headers["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
            response.headers["content-security-policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'self'"
            )
            return response
        finally:
            if telemetry is not None:
                route = request.scope.get("route")
                route_path = getattr(route, "path", "<unmatched>")
                try:
                    telemetry.record_http(
                        request.method,
                        route_path,
                        response_status,
                        monotonic() - started,
                    )
                except Exception as exc:
                    _LOG.warning(
                        "telemetry.http_failed",
                        error_type=type(exc).__name__,
                    )

    @app.exception_handler(AuthorizationError)
    async def authorization_handler(request: Request, exc: AuthorizationError) -> JSONResponse:
        del request
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(ResourceNotFoundError)
    async def not_found_handler(request: Request, exc: ResourceNotFoundError) -> JSONResponse:
        del request
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @app.get("/health/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        is_ready = await service.ready()
        return JSONResponse(
            status_code=200 if is_ready else 503,
            content={"status": "ready" if is_ready else "not_ready"},
        )

    @app.get("/workbench", response_class=HTMLResponse, include_in_schema=False)
    async def workbench() -> str:
        return WORKBENCH_HTML

    @app.get("/workbench/assets/workbench.css", include_in_schema=False)
    async def workbench_css() -> Response:
        return Response(WORKBENCH_CSS, media_type="text/css")

    @app.get("/workbench/assets/workbench.js", include_in_schema=False)
    async def workbench_js() -> Response:
        return Response(WORKBENCH_JS, media_type="text/javascript")

    if stage_one_service is not None:
        stage_one = stage_one_service

        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        @app.get("/stage-one", response_class=HTMLResponse, include_in_schema=False)
        async def stage_one_page() -> str:
            return stage_one_html

        @app.get("/stage-one/assets/stage-one.css", include_in_schema=False)
        async def stage_one_css() -> Response:
            return Response(STAGE_ONE_CSS, media_type="text/css")

        @app.get("/stage-one/assets/stage-one.js", include_in_schema=False)
        async def stage_one_javascript_asset() -> Response:
            return Response(stage_one_javascript, media_type="text/javascript")

    @app.get("/v1/session", response_model=SessionView)
    async def session(principal: principal_dependency) -> SessionView:
        return SessionView(
            principal_id=principal.principal.principal_id,
            desks=tuple(sorted(principal.desks)),
            roles=tuple(sorted(principal.roles)),
        )

    if stage_one_service is not None:

        @app.get("/v1/topics", response_model=list[TopicTagView])
        async def list_topics(principal: principal_dependency) -> list[TopicTagView]:
            return await stage_one.list_topics(principal)

        @app.get("/v1/subscriptions", response_model=list[TopicSubscriptionView])
        async def list_subscriptions(
            principal: principal_dependency,
        ) -> list[TopicSubscriptionView]:
            return await stage_one.list_subscriptions(principal)

        @app.put(
            "/v1/topics/{topic_id}/subscription",
            response_model=TopicSubscriptionView,
        )
        async def update_subscription(
            topic_id: str,
            subscription: TopicSubscriptionUpdate,
            principal: principal_dependency,
        ) -> TopicSubscriptionView:
            return await stage_one.update_subscription(principal, topic_id, subscription)

        @app.get("/v1/topics/{topic_id}/results", response_model=TopicResultsView)
        async def get_topic_results(
            topic_id: str,
            principal: principal_dependency,
            response: Response,
            refresh: Annotated[bool, Query()] = False,
        ) -> TopicResultsView:
            result = await stage_one.get_topic_results(principal, topic_id, refresh=refresh)
            if result.analysis_status in {
                "queued",
                "running",
                "deferred",
                "retryable_failed",
            }:
                response.status_code = status.HTTP_202_ACCEPTED
            return result

        @app.post(
            "/v1/results/{result_id}/evaluation",
            response_model=ResultEvaluationReceipt,
            status_code=status.HTTP_201_CREATED,
        )
        async def evaluate_result(
            result_id: str,
            evaluation: ResultEvaluationRequest,
            principal: principal_dependency,
        ) -> ResultEvaluationReceipt:
            return await stage_one.evaluate_result(principal, result_id, evaluation)

        @app.post(
            "/v1/searches",
            response_model=SearchView,
            status_code=status.HTTP_202_ACCEPTED,
        )
        async def create_search(
            search_request: SearchCreateRequest,
            principal: principal_dependency,
        ) -> SearchView:
            return await stage_one.create_search(principal, search_request)

        @app.get("/v1/searches/{search_id}", response_model=SearchView)
        async def get_search(
            search_id: str,
            principal: principal_dependency,
            response: Response,
        ) -> SearchView:
            result = await stage_one.get_search(principal, search_id)
            if result.state in {"queued", "running", "retryable_failed"}:
                response.status_code = status.HTTP_202_ACCEPTED
            return result

        @app.get("/v1/operations/dashboard", response_model=RuntimeDashboardView)
        async def operations_dashboard(
            principal: principal_dependency,
            event_limit: Annotated[int, Query(ge=1, le=500)] = 200,
        ) -> RuntimeDashboardView:
            return await stage_one.operations_dashboard(
                principal,
                event_limit=event_limit,
            )

    @app.get("/v1/signals", response_model=list[SignalView])
    async def list_signals(
        principal: principal_dependency,
        desk: Annotated[str, Query(min_length=1)],
        signal_status: Annotated[str | None, Query(alias="status")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[SignalView]:
        return await service.list_signals(principal, desk=desk, status=signal_status, limit=limit)

    @app.get("/v1/signals/{signal_id}", response_model=SignalView)
    async def get_signal(signal_id: str, principal: principal_dependency) -> SignalView:
        return await service.get_signal(principal, signal_id)

    @app.post(
        "/v1/signals/{signal_id}/feedback",
        response_model=FeedbackReceipt,
        status_code=status.HTTP_201_CREATED,
    )
    async def submit_feedback(
        signal_id: str,
        feedback: FeedbackRequest,
        principal: principal_dependency,
    ) -> FeedbackReceipt:
        return await service.submit_feedback(principal, signal_id, feedback)

    @app.post(
        "/v1/signals/{signal_id}/close",
        response_model=SignalCloseReceipt,
        status_code=status.HTTP_201_CREATED,
    )
    async def close_signal(
        signal_id: str,
        close_request: SignalCloseRequest,
        principal: principal_dependency,
    ) -> SignalCloseReceipt:
        return await service.close_signal(principal, signal_id, close_request)

    @app.get("/v1/entities/{entity_id}", response_model=EntityView)
    async def get_entity(entity_id: str, principal: principal_dependency) -> EntityView:
        return await service.get_entity(principal, entity_id)

    @app.get("/v1/evidence/{evidence_span_id}", response_model=EvidenceSpanView)
    async def get_evidence(
        evidence_span_id: str, principal: principal_dependency
    ) -> EvidenceSpanView:
        return await service.get_evidence(principal, evidence_span_id)

    @app.post(
        "/v1/reviews/{subject_type}/{subject_id}",
        response_model=ReviewReceipt,
        status_code=status.HTTP_201_CREATED,
    )
    async def decide_review(
        subject_type: str,
        subject_id: str,
        decision: ReviewDecisionRequest,
        principal: principal_dependency,
    ) -> ReviewReceipt:
        return await service.decide_review(principal, subject_type, subject_id, decision)

    @app.post(
        "/v1/briefs",
        response_model=BriefView,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def request_brief(
        brief_request: BriefRequest, principal: principal_dependency
    ) -> BriefView:
        return await service.request_brief(principal, brief_request)

    @app.get("/v1/briefs/{brief_id}", response_model=BriefView)
    async def get_brief(brief_id: str, principal: principal_dependency) -> BriefView:
        return await service.get_brief(principal, brief_id)

    @app.post(
        "/v1/briefs/{brief_id}/publication",
        response_model=BriefView,
        status_code=status.HTTP_201_CREATED,
    )
    async def publish_brief(
        brief_id: str,
        publication: BriefPublicationRequest,
        principal: principal_dependency,
    ) -> BriefView:
        return await service.publish_brief(principal, brief_id, publication)

    @app.get("/v1/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str, principal: principal_dependency) -> RunView:
        return await service.get_run(principal, run_id)

    if canonical_stage_one_only:
        if stage_one_service is None:
            raise ValueError("canonical Stage One routing requires a Stage One service")
        supported_paths = {
            "/",
            "/health/live",
            "/health/ready",
            "/stage-one",
            "/stage-one/assets/stage-one.css",
            "/stage-one/assets/stage-one.js",
            "/v1/session",
            "/v1/topics",
            "/v1/subscriptions",
            "/v1/topics/{topic_id}/subscription",
            "/v1/topics/{topic_id}/results",
            "/v1/results/{result_id}/evaluation",
            "/v1/searches",
            "/v1/searches/{search_id}",
            "/v1/operations/dashboard",
        }
        app.router.routes[:] = [
            route for route in app.router.routes if getattr(route, "path", None) in supported_paths
        ]
    return app


def create_production_app() -> FastAPI:
    """Strict ``uvicorn --factory`` entry point for the deployed API."""
    from fi_intel.api.postgres import PostgresAnalystService
    from fi_intel.api.stage_one_postgres import PostgresStageOneService
    from fi_intel.application.preflight import canonical_configuration_errors
    from fi_intel.application.runtime_resources import SharedPostgresPool
    from fi_intel.config import Settings
    from fi_intel.runtime import ExecutionPath, RuntimeCapabilities, validate_settings_runtime
    from fi_intel.telemetry import TelemetryConfig

    settings = Settings()
    errors = canonical_configuration_errors(settings)
    if errors:
        raise RuntimeError(f"Canonical API is not configured: {'; '.join(errors)}")
    validate_settings_runtime(
        settings,
        RuntimeCapabilities(
            execution_path=ExecutionPath.UNIFIED_PIPELINE,
            uses_fixture_data=False,
            uses_hashing_embeddings=False,
            all_models_configured=True,
            coverage_computed_server_side=True,
            durable_step_store=True,
        ),
    )
    postgres = SharedPostgresPool(settings)
    directory = LocalIdentityDirectory()
    service = PostgresAnalystService(settings.postgres_dsn, pool_provider=postgres)
    telemetry = Telemetry(
        TelemetryConfig(
            service_name="fi-intel-analyst-api",
            service_version="0.1.0",
            environment=settings.analysis_mode,
            trace_endpoint=settings.telemetry_trace_endpoint,
            metric_endpoint=settings.telemetry_metric_endpoint,
        )
    )
    stage_one = PostgresStageOneService(
        settings.postgres_dsn,
        settings=settings,
        telemetry=telemetry,
        mode=settings.analysis_mode,
        pool_provider=postgres,
    )
    return create_app(
        Authenticator(
            LocalTokenVerifier(),
            directory,
        ),
        service,
        telemetry,
        stage_one_service=stage_one,
        canonical_stage_one_only=True,
        owns_telemetry=True,
        owned_resources=(postgres, service, stage_one),
    )
