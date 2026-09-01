"""Operational workbench, lifecycle, telemetry, and factory contracts."""

from datetime import UTC, datetime
from typing import cast

from fastapi.testclient import TestClient

from fi_intel.api.app import create_app
from fi_intel.api.auth import (
    LOCAL_BEARER_TOKEN,
    Authenticator,
    LocalIdentityDirectory,
    LocalTokenVerifier,
    RequestPrincipal,
    VerifiedToken,
)
from fi_intel.api.models import EntityView, EvidenceSpanView, SignalView
from fi_intel.api.service import InMemoryAnalystService
from fi_intel.application.preflight import canonical_configuration_errors
from fi_intel.config import Settings
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.telemetry import Telemetry

NOW = datetime(2025, 1, 2, 10, tzinfo=UTC)


async def test_local_product_authenticates_without_external_identity_configuration() -> None:
    principal = await Authenticator(LocalTokenVerifier(), LocalIdentityDirectory()).authenticate(
        LOCAL_BEARER_TOKEN
    )

    assert principal.subject == "local-analyst"
    assert principal.principal.entitlement_group == "fi_gcc_public"
    assert {"analyst", "reviewer", "publisher"} <= principal.roles


class _Verifier:
    async def verify(self, credential: str) -> VerifiedToken:
        return VerifiedToken(subject=credential, issuer="https://issuer.test")


class _Directory:
    async def resolve(self, subject: str) -> RequestPrincipal | None:
        if subject != "alice":
            return None
        return RequestPrincipal(
            subject="alice",
            principal=Principal(
                principal_id="directory-alice",
                entitlement_group="fi-public",
                side=Side.PUBLIC,
            ),
            desks=frozenset({"fi_gcc"}),
            roles=frozenset({"analyst", "reviewer", "publisher", "operator"}),
            purposes=frozenset({"market_intelligence"}),
        )

    async def close(self) -> None:
        return None


class _Telemetry:
    def __init__(self) -> None:
        self.http: list[tuple[str, str, int, float]] = []
        self.shutdown_called = False

    def record_http(self, method: str, route: str, status_code: int, duration: float) -> None:
        self.http.append((method, route, status_code, duration))

    def shutdown(self) -> None:
        self.shutdown_called = True


class _Resource:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer alice"}


def _service() -> InMemoryAnalystService:
    return InMemoryAnalystService(
        signals=(
            SignalView(
                signal_id="signal-1",
                pattern_id="capital_programme",
                pattern_version="2",
                entity_id="entity-1",
                entity_name="Example Bank",
                desk="fi_gcc",
                status="confirmed",
                score=0.91,
                as_of=NOW,
                changed_at=NOW,
                evidence_span_ids=("evidence-1",),
            ),
        ),
        entities=(
            EntityView(
                entity_id="entity-1",
                entity_type="organization",
                canonical_name="Example Bank",
                identifiers={"lei": "LEI-1"},
            ),
        ),
        evidence=(
            EvidenceSpanView(
                evidence_span_id="evidence-1",
                document_version_id="version-1",
                title="Capital plan",
                quote="The board approved the issuance programme.",
                char_start=10,
                char_end=52,
                source_id="wire",
                published_at=NOW,
            ),
        ),
    )


def _app(service: InMemoryAnalystService | None = None):
    return create_app(Authenticator(_Verifier(), _Directory()), service or _service())


def test_workbench_uses_local_assets_and_no_authorization_overrides() -> None:
    client = TestClient(_app())
    shell = client.get("/workbench")
    css = client.get("/workbench/assets/workbench.css")
    javascript = client.get("/workbench/assets/workbench.js")

    assert shell.status_code == 200
    assert "/workbench/assets/workbench.css" in shell.text
    assert "/workbench/assets/workbench.js" in shell.text
    assert "https://" not in shell.text and "http://" not in shell.text
    assert "@media" in css.text and "grid-template-columns" in css.text
    assert "/v1/signals" in javascript.text
    assert "sessionStorage" in javascript.text
    assert "entitlement_group" not in javascript.text
    assert "barrier_side" not in javascript.text
    assert shell.headers["content-security-policy"].startswith("default-src 'self'")


def test_session_exposes_directory_desks_but_not_policy_overrides() -> None:
    client = TestClient(_app())
    assert client.get("/v1/session").status_code == 401

    response = client.get("/v1/session", headers=_headers())

    assert response.status_code == 200
    assert response.json()["desks"] == ["fi_gcc"]
    assert response.json()["principal_id"] == "directory-alice"
    assert "entitlement_group" not in response.json()
    assert "side" not in response.json()


def test_workbench_decisions_and_lifecycle_close_are_operational() -> None:
    service = _service()
    client = TestClient(_app(service))

    feedback = client.post(
        "/v1/signals/signal-1/feedback",
        headers=_headers(),
        json={"verdict": "needs_review", "reason": "Confirm the programme size."},
    )
    closed = client.post(
        "/v1/signals/signal-1/close",
        headers=_headers(),
        json={"reason": "actioned", "note": "Coverage accepted the lead."},
    )
    detail = client.get("/v1/signals/signal-1", headers=_headers())

    assert feedback.status_code == 201
    assert closed.status_code == 201
    assert closed.json()["status"] == "suppressed"
    assert detail.json()["latest_feedback"] == "needs_review"
    assert detail.json()["closed_at"] is not None


def test_brief_request_and_append_only_publication_contract() -> None:
    service = _service()
    client = TestClient(_app(service))
    requested = client.post(
        "/v1/briefs",
        headers=_headers(),
        json={"desk": "fi_gcc", "as_of": NOW.isoformat()},
    )
    brief_id = requested.json()["brief_id"]
    service.briefs[brief_id] = service.briefs[brief_id].model_copy(
        update={"coverage_complete": True}
    )
    published = client.post(
        f"/v1/briefs/{brief_id}/publication",
        headers=_headers(),
        json={"html": "<article>Grounded brief</article>"},
    )

    assert requested.status_code == 202
    assert published.status_code == 201
    assert published.json()["status"] == "published"
    assert published.json()["coverage_complete"] is True


def test_http_telemetry_uses_normalized_route_and_owned_resources_close() -> None:
    telemetry = _Telemetry()
    resource = _Resource()
    app = create_app(
        Authenticator(_Verifier(), _Directory()),
        _service(),
        cast(Telemetry, telemetry),
        owns_telemetry=True,
        owned_resources=(resource,),
    )

    with TestClient(app) as client:
        response = client.get("/v1/signals/signal-1", headers=_headers())
        assert response.status_code == 200

    assert telemetry.http
    method, route, status_code, duration = telemetry.http[-1]
    assert (method, route, status_code) == ("GET", "/v1/signals/{signal_id}", 200)
    assert "signal-1" not in route
    assert duration >= 0.0
    assert telemetry.shutdown_called
    assert resource.closed


def test_canonical_settings_need_only_model_endpoints() -> None:
    errors = canonical_configuration_errors(
        Settings(
            llm_base_url="http://model.test/v1",
            embedding_base_url="http://embedding.test/v1",
            embedding_model="nvidia/llama-3.2-nv-embedqa-1b-v2",
            embedding_dim=2048,
        )
    )

    assert errors == ()
